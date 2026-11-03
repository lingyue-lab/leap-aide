"""Tests for app.api.browser_receptionist — per-connection CDP isolation.

Chrome is faked: a bash wrapper execs a Python responder on the real
pipe fds 3/4 (see test_cdp_pipe), answering Target.* commands and
logging closeTarget calls to a file. No browser or X server needed.
"""

import asyncio
import json
import os
import socket
import stat
import time
import urllib.request
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")

from app.api.browser_receptionist import Receptionist

FAKE_CHROME_PY = """\
import json, os, sys

log_path = os.environ.get("FAKE_CHROME_LOG", "")

def log(entry):
    if log_path:
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\\n")

def read_frame():
    buf = b""
    while True:
        ch = os.read(3, 1)
        if not ch:
            return None
        if ch == b"\\0":
            if buf:
                return json.loads(buf)
            continue
        buf += ch

def write(obj):
    os.write(4, json.dumps(obj).encode() + b"\\0")

targets = {}
n = 0

while True:
    msg = read_frame()
    if msg is None:
        break
    log({"recv": msg})
    method = msg.get("method", "")
    mid = msg.get("id")
    # Real Chrome rejects non-int32 ids ("Message must have integer 'id'
    # property", response carries NO id field) — mirror that so oversized
    # internal ids fail tests too.
    if mid is not None and not (-(2**31) <= mid <= 2**31 - 1):
        write({"error": {"code": -32600,
               "message": "Message must have integer 'id' property"}})
        continue
    if method == "Target.createTarget":
        n += 1
        tid = f"target-{n}"
        targets[tid] = True
        write({"id": mid, "result": {"targetId": tid}})
    elif method == "Target.getTargets":
        infos = [
            {"targetId": t, "type": "page", "url": "about:blank", "title": "",
             "attached": False}
            for t in targets
        ]
        write({"id": mid, "result": {"targetInfos": infos}})
    elif method == "Target.closeTarget":
        tid = (msg.get("params") or {}).get("targetId")
        if tid in targets:
            targets.pop(tid)
            # Real Chrome emits targetDestroyed when a tab dies.
            write({"method": "Target.targetDestroyed",
                   "params": {"targetId": tid}})
        write({"id": mid, "result": {}})
    elif method == "Fake.emitGhostEvent":
        # Unsolicited event with a sessionId no connection owns — the
        # receptionist must drop it, not broadcast it.
        write({"method": "Runtime.consoleAPICalled",
               "sessionId": "ghost-session", "params": {}})
        write({"id": mid, "result": {}})
    else:
        write({"id": mid, "result": {}})
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def receptionist(tmp_path, monkeypatch):
    """A Receptionist on a free port with a faked Chrome on the pipe."""
    responder = tmp_path / "fake_chrome.py"
    responder.write_text(FAKE_CHROME_PY)
    wrapper = tmp_path / "fake-chrome-bin"
    # Chrome's CLI flags are intentionally not forwarded: python would
    # reject --remote-debugging-pipe as an interpreter option.
    wrapper.write_text(
        f'#!/usr/bin/env bash\nexec "{os.environ.get("PYTHON", "python3")}"'
        f' -u "{responder}"\n'
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)

    log_path = tmp_path / "chrome.log"
    port = _free_port()
    re = Receptionist(port=port)
    re.start_in_thread(
        chrome_bin=str(wrapper),
        profile_dir=tmp_path / "profile",
        display=":99",
        extra_args=[],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "FAKE_CHROME_LOG": str(log_path),
        },
    )
    # Wait for the HTTP surface to answer.
    deadline = time.time() + 10
    while time.time() < deadline:
        if re.is_healthy():
            break
        time.sleep(0.05)
    else:
        re.stop()
        raise AssertionError("receptionist never became healthy")
    re._test_log_path = log_path
    yield re
    re.stop()


def _get_json(re: Receptionist, path: str):
    with urllib.request.urlopen(
        f"http://{re.host}:{re.port}{path}", timeout=3
    ) as resp:
        return json.loads(resp.read().decode())


# ── HTTP micro-endpoints ────────────────────────────────────────────────────


def test_json_version_embeds_requesting_sid(receptionist):
    payload = _get_json(receptionist, "/json/version?sid=sessA")
    ws_url = payload["webSocketDebuggerUrl"]
    assert ws_url.startswith("ws://")
    assert "/devtools/browser/" in ws_url
    assert ws_url.endswith("?sid=sessA")


def test_json_version_requires_sid(receptionist):
    # Sid-less discovery is rejected with actionable guidance so a
    # hand-written CDP script learns the required URL shape in one shot.
    try:
        urllib.request.urlopen(
            f"http://{receptionist.host}:{receptionist.port}/json/version",
            timeout=3,
        )
        raise AssertionError("expected HTTPError 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        body = json.loads(exc.read().decode())
        assert "sid is required" in body["error"]
        assert "sid=<session_id>" in body["example"]


def test_json_version_normalizes_discovery_polluted_sid(receptionist):
    # SDK/browser-harness discovery appends /json/version to our
    # "?sid=" URL (endswith check fails on the query), so the query
    # value arrives as "<sid>/json/version". The embedded WS sid must
    # be the bare session id — otherwise /json/list filtering and the
    # turn-end orphan sweep never match.
    payload = _get_json(
        receptionist, "/json/version?sid=sessA/json/version"
    )
    assert payload["webSocketDebuggerUrl"].endswith("?sid=sessA")


def test_json_list_anonymous_is_empty(receptionist):
    assert _get_json(receptionist, "/json/list") == []


def test_browser_ws_requires_sid(receptionist):
    # A sid-less request to the WS endpoint is refused with the same
    # guidance (the handler answers before the upgrade), so anonymous
    # connections can no longer exist — every connection is attributable
    # to a session for /json/list and turn-end sweeps.
    try:
        urllib.request.urlopen(
            f"http://{receptionist.host}:{receptionist.port}"
            "/devtools/browser/global",
            timeout=3,
        )
        raise AssertionError("expected HTTPError 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        body = json.loads(exc.read().decode())
        assert "sid is required" in body["error"]


def test_json_new_without_connection_is_409(receptionist):
    try:
        urllib.request.urlopen(
            f"http://{receptionist.host}:{receptionist.port}/json/new?sid=sessA",
            timeout=3,
        )
        raise AssertionError("expected HTTPError 409")
    except urllib.error.HTTPError as exc:
        assert exc.code == 409


def test_is_healthy_and_chrome_alive(receptionist):
    assert receptionist.is_healthy() is True
    assert receptionist.is_chrome_alive() is True


# ── WebSocket isolation ─────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


async def _connect(re: Receptionist, sid: str):
    session = aiohttp.ClientSession()
    ws = await session.ws_connect(
        f"http://{re.host}:{re.port}/devtools/browser/global?sid={sid}"
    )
    return session, ws


async def _recv_until(ws, pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        data = json.loads(msg.data)
        if pred(data):
            return data
    raise AssertionError("expected message not received")


def test_connect_creates_no_preseeded_tab(receptionist):
    """A bare connection owns no tab until its client creates one."""
    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        try:
            # Any connect-time create would land by now; there is none —
            # the client (harness/preamble/script) makes its own tab.
            await asyncio.sleep(0.2)
            assert _get_json(receptionist, "/json/list?sid=sessA") == []
            await wa.send_str(json.dumps({"id": 1, "method": "Target.getTargets"}))
            ra = await _recv_until(wa, lambda m: m.get("id") == 1)
            assert ra["result"]["targetInfos"] == []
        finally:
            await sa.close()

    _run(scenario())


def test_two_clients_get_isolated_tabs(receptionist):
    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        sb, wb = await _connect(receptionist, "sessB")
        try:
            # No pre-seeded tabs; each client creates its own.
            assert _get_json(receptionist, "/json/list?sid=sessA") == []
            assert _get_json(receptionist, "/json/list?sid=sessB") == []
            for ws in (wa, wb):
                await ws.send_str(json.dumps({
                    "id": 20, "method": "Target.createTarget",
                    "params": {"url": "about:blank"},
                }))
                await _recv_until(ws, lambda m: m.get("id") == 20)
            la = _get_json(receptionist, "/json/list?sid=sessA")
            lb = _get_json(receptionist, "/json/list?sid=sessB")
            assert len(la) == 1, la
            assert len(lb) == 1, lb
            assert la[0]["id"] != lb[0]["id"]

            # getTargets is rewritten to owned targets only.
            await wa.send_str(json.dumps({"id": 1, "method": "Target.getTargets"}))
            ra = await _recv_until(wa, lambda m: m.get("id") == 1)
            await wb.send_str(json.dumps({"id": 1, "method": "Target.getTargets"}))
            rb = await _recv_until(wb, lambda m: m.get("id") == 1)

            ids_a = {t["targetId"] for t in ra["result"]["targetInfos"]}
            ids_b = {t["targetId"] for t in rb["result"]["targetInfos"]}
            assert ids_a == {la[0]["id"]}
            assert ids_b == {lb[0]["id"]}
            assert not (ids_a & ids_b)
        finally:
            await sa.close()
            await sb.close()

    _run(scenario())


def test_client_create_target_registers_ownership(receptionist):
    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        try:
            await wa.send_str(json.dumps({
                "id": 10, "method": "Target.createTarget",
                "params": {"url": "https://example.com"},
            }))
            resp = await _recv_until(wa, lambda m: m.get("id") == 10)
            tid = resp["result"]["targetId"]
            # id remapped back to the client's original.
            assert resp["id"] == 10

            la = _get_json(receptionist, "/json/list?sid=sessA")
            assert tid in {t["id"] for t in la}

            # Another sid must not see or close it.
            try:
                urllib.request.urlopen(
                    f"http://{receptionist.host}:{receptionist.port}"
                    f"/json/close/{tid}?sid=sessB",
                    timeout=3,
                )
                raise AssertionError("expected 404 for foreign close")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404

            # Owner can close it.
            closed = _get_json(
                receptionist, f"/json/close/{tid}?sid=sessA"
            )
            assert closed.get("closed") is True
        finally:
            await sa.close()

    _run(scenario())


def test_foreign_session_id_message_is_rejected(receptionist):
    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        try:
            await wa.send_str(json.dumps({
                "id": 5, "method": "Runtime.evaluate",
                "params": {}, "sessionId": "ghost-session",
            }))
            resp = await _recv_until(wa, lambda m: m.get("id") == 5)
            assert "error" in resp
            assert "not owned" in resp["error"]["message"]
        finally:
            await sa.close()

    _run(scenario())


def test_cross_session_close_target_is_rejected(receptionist):
    """A connection cannot close a tab owned by another sid."""
    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        sb, wb = await _connect(receptionist, "sessB")
        try:
            # B creates a tab and records its id.
            await wb.send_str(json.dumps({
                "id": 1, "method": "Target.createTarget",
                "params": {"url": "about:blank"},
            }))
            tid_b = (await _recv_until(wb, lambda m: m.get("id") == 1)
                     )["result"]["targetId"]
            # A tries to close B's tab over the CDP WS.
            await wa.send_str(json.dumps({
                "id": 2, "method": "Target.closeTarget",
                "params": {"targetId": tid_b},
            }))
            resp = await _recv_until(wa, lambda m: m.get("id") == 2)
            assert "error" in resp
            assert "not owned" in resp["error"]["message"]
            # A also cannot attach to B's tab.
            await wa.send_str(json.dumps({
                "id": 3, "method": "Target.attachToTarget",
                "params": {"targetId": tid_b, "flatten": True},
            }))
            resp = await _recv_until(wa, lambda m: m.get("id") == 3)
            assert "error" in resp
            # B's tab is still alive in Chrome.
            await wb.send_str(json.dumps({"id": 4, "method": "Target.getTargets"}))
            rb = await _recv_until(wb, lambda m: m.get("id") == 4)
            assert tid_b in {t["targetId"] for t in rb["result"]["targetInfos"]}
        finally:
            await sa.close()
            await sb.close()

    _run(scenario())


def test_client_closed_tab_is_forgotten(receptionist):
    """A client-closed tab leaves no stale ownership entries."""
    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        try:
            # One tab that stays (stands in for the pre-seed the old
            # connect-time create used to provide), one that gets closed.
            await wa.send_str(json.dumps({
                "id": 9, "method": "Target.createTarget",
                "params": {"url": "about:blank"},
            }))
            init_tid = (await _recv_until(wa, lambda m: m.get("id") == 9)
                        )["result"]["targetId"]

            await wa.send_str(json.dumps({
                "id": 10, "method": "Target.createTarget",
                "params": {"url": "about:blank"},
            }))
            tid = (await _recv_until(wa, lambda m: m.get("id") == 10)
                   )["result"]["targetId"]
            await wa.send_str(json.dumps({
                "id": 11, "method": "Target.closeTarget",
                "params": {"targetId": tid},
            }))
            # The destroy event must reach the owner; drain until both the
            # response and the event have arrived (order is Chrome-defined).
            got_resp = got_evt = False
            deadline = time.time() + 5
            while not (got_resp and got_evt) and time.time() < deadline:
                msg = await asyncio.wait_for(wa.receive(), timeout=5)
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("id") == 11:
                    got_resp = True
                if data.get("method") == "Target.targetDestroyed":
                    got_evt = True
            assert got_resp and got_evt, (got_resp, got_evt)

            # ...and ownership is forgotten everywhere.
            for _ in range(50):
                la = _get_json(receptionist, "/json/list?sid=sessA")
                if len(la) == 1:
                    break
                await asyncio.sleep(0.05)
            assert [t["id"] for t in la] == [init_tid], la
            conn = next(c for c in receptionist._conns.values()
                        if c.sid == "sessA" and not c.closed)
            assert tid not in conn.owned_targets
            assert tid not in receptionist._tabs
        finally:
            await sa.close()

    _run(scenario())


def test_ghost_session_event_is_dropped(receptionist):
    """A Chrome event with an unowned sessionId reaches no client."""
    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        try:
            await wa.send_str(json.dumps({
                "id": 9, "method": "Fake.emitGhostEvent",
            }))
            resp = await _recv_until(wa, lambda m: m.get("id") == 9)
            assert "result" in resp
            # The ghost event (emitted before the response) must not
            # have been forwarded: nothing else is on the socket.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(wa.receive(), timeout=0.5)
        finally:
            await sa.close()

    _run(scenario())


def test_disconnect_closes_owned_targets(receptionist):
    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        # Create a tab so there is owned state to clean on disconnect.
        await wa.send_str(json.dumps({
            "id": 10, "method": "Target.createTarget",
            "params": {"url": "about:blank"},
        }))
        await _recv_until(wa, lambda m: m.get("id") == 10)
        await sa.close()
        # Wait for the closeTarget to land on the fake chrome log.
        deadline = time.time() + 5
        while time.time() < deadline:
            if receptionist._test_log_path.exists():
                lines = receptionist._test_log_path.read_text().splitlines()
                if any(
                    "closeTarget" in (json.loads(ln).get("recv") or {}).get("method", "")
                    for ln in lines
                    if "recv" in ln
                ):
                    break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("closeTarget never reached fake chrome")
        # Tables are cleaned.
        assert _get_json(receptionist, "/json/list?sid=sessA") == []

    _run(scenario())


def test_target_cap_enforced(receptionist):
    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        try:
            from app.api.browser_receptionist import MAX_TARGETS_PER_CONN

            for i in range(MAX_TARGETS_PER_CONN):
                await wa.send_str(json.dumps({
                    "id": 100 + i, "method": "Target.createTarget",
                    "params": {"url": "about:blank"},
                }))
                await _recv_until(wa, lambda m: m.get("id") == 100 + i)
            await wa.send_str(json.dumps({
                "id": 999, "method": "Target.createTarget",
                "params": {"url": "about:blank"},
            }))
            resp = await _recv_until(wa, lambda m: m.get("id") == 999)
            assert "error" in resp
            assert "target cap" in resp["error"]["message"]
        finally:
            await sa.close()

    _run(scenario())


# ── Chrome death and respawn ────────────────────────────────────────────────


def test_chrome_death_and_respawn(receptionist):
    """A dead Chrome is detected; ensure_chrome rebuilds it and resets tables."""
    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        # Owned state that must vanish with the respawn.
        await wa.send_str(json.dumps({
            "id": 10, "method": "Target.createTarget",
            "params": {"url": "about:blank"},
        }))
        await _recv_until(wa, lambda m: m.get("id") == 10)
        await sa.close()

    _run(scenario())

    old_pid = receptionist._chrome.proc.pid
    os.kill(old_pid, 9)
    # The internal supervisor respawns after ~1s; also nudge directly so
    # the test doesn't depend on the backoff timer.
    deadline = time.time() + 15
    while time.time() < deadline:
        handle = receptionist._chrome
        if handle is not None and handle.alive and handle.proc.pid != old_pid:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("chrome was never respawned")

    # Old connection state was reset.
    assert _get_json(receptionist, "/json/list?sid=sessA") == []
    assert receptionist.is_chrome_alive() is True


def test_close_idle_connections(receptionist):
    """Orphan sweep closes only old conns of the given sid."""

    async def scenario():
        sa, wa = await _connect(receptionist, "sessA")
        sb, wb = await _connect(receptionist, "sessB")
        try:
            # B gets a tab so "untouched" is observable (no pre-seed).
            await wb.send_str(json.dumps({
                "id": 5, "method": "Target.createTarget",
                "params": {"url": "about:blank"},
            }))
            await _recv_until(wb, lambda m: m.get("id") == 5)
            # Backdate sessA's connection past the idle window, on the
            # receptionist's loop thread (tables are loop-owned).
            loop = receptionist._loop

            async def backdate():
                for c in receptionist._conns.values():
                    if c.sid == "sessA":
                        c.opened_at = time.time() - 3600

            await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(backdate(), loop)
            )
            closed = await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(
                    receptionist.close_idle_connections("sessA", 30.0), loop
                )
            )
            assert closed == 1
            assert _get_json(receptionist, "/json/list?sid=sessA") == []
            # sessB untouched.
            assert _get_json(receptionist, "/json/list?sid=sessB") != []
        finally:
            await sa.close()
            await sb.close()

    _run(scenario())
