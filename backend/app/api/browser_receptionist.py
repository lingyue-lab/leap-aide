"""The browser "receptionist" — sole network gateway to the shared Chrome.

Chrome is spawned with ``--remote-debugging-pipe`` (see
``app.api.cdp_pipe``) so it opens NO TCP CDP port: nothing connectable
shows up in ``ps``/``ss``.  This module holds the pipe and exposes the
only legitimate CDP surface on ``127.0.0.1:<receptionist_port>``
(default 6900), enforcing per-connection tab isolation:

- tabs belong to the connection that created them: a connection can
  only see and operate its own tab(s) — foreign tabs are filtered out
  of ``Target.getTargets``/``/json/list`` and foreign ``sessionId``
  messages are dropped;
- no tab is pre-created at connect time — the client (harness daemon,
  model code, hand-written script) creates its own on demand, which
  keeps a permanently-blank extra tab out of the shared desktop;
- owned tabs close automatically when the connection drops.

Clients MUST identify themselves with ``?sid=<session_id>`` (query) or
the ``X-LeapAide-Session`` header: discovery (``/json/version``) and the
WebSocket endpoints reject sid-less requests with a 400 that spells out
the required URL shape. The sid tags the connection for logging,
``/json/list`` filtering and turn-end orphan sweeps — isolation itself
is per-connection, not per-sid.

Threading model: a dedicated daemon thread runs its own asyncio loop;
the aiohttp AppRunner, the pipe, and all connection tables live on that
loop thread.  Cross-thread entry points (``is_healthy``,
``close_idle_connections``, ``ensure_chrome_async``) marshal via
``asyncio.run_coroutine_threadsafe``.  No table lock is needed because
mutators only run on the loop thread.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
import time
import urllib.request
import urllib.error
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_PORT = 6900
MAX_TARGETS_PER_CONN = 10
# Receptionist-internal requests use a disjoint id range so client-id
# remapping can never collide with them. Chrome's pipe JSON parser only
# accepts int32 ids ("-32600 Message must have integer 'id' property"
# beyond ±2^31-1), so the base sits at INT32_MIN and counts up — it
# reaches the client range (-1, 0, 1, ...) only after 2^31 internal
# commands, which no single receptionist lifetime will see.
_INTERNAL_ID_BASE = -(2**31)

# Rejection text for sid-less requests — an LLM writing a CDP script by
# hand reads this body and learns the required URL shape in one round trip.
_SID_REQUIRED = (
    "receptionist: sid is required — request "
    "/json/version?sid=<session_id> (your session id is the last segment "
    "of HERMES_SESSION_KEY) and use the returned webSocketDebuggerUrl "
    "as-is; it already embeds the sid"
)


def _sid_present(request) -> bool:
    """True when the request carries a non-empty session identity."""
    return bool(
        (request.query.get("sid") or "").strip()
        or (request.headers.get("X-LeapAide-Session") or "").strip()
    )


@dataclass
class ClientConnection:
    """One accepted client WebSocket and the CDP resources it owns."""

    conn_id: str
    sid: str
    peer: str
    ws: object  # aiohttp WebSocketResponse
    opened_at: float = field(default_factory=time.time)
    owned_targets: set[str] = field(default_factory=set)
    closed: bool = False


class Receptionist:
    """In-process CDP proxy with per-connection tab isolation."""

    def __init__(self, port: int | None = None) -> None:
        import os

        self.port = int(
            port
            if port is not None
            else os.environ.get("HERMES_RECEPTIONIST_PORT", str(DEFAULT_PORT))
        )
        self.host = "127.0.0.1"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runner = None
        self._chrome = None  # ChromePipeHandle
        self._chrome_env: dict = {}
        self._chrome_profile = None  # Path
        self._chrome_extra_args: list[str] = []
        self._stopping = False
        # ── loop-thread-only tables ──
        self._conns: dict[str, ClientConnection] = {}       # conn_id -> conn
        self._sessions: dict[str, set[str]] = {}            # sid -> {conn_id}
        self._tabs: dict[str, str] = {}                     # target_id -> conn_id
        self._cdp_sessions: dict[str, str] = {}             # sessionId -> conn_id
        self._pending: dict[int, tuple] = {}                # global id -> spec
        self._id_counter = itertools.count(-1)
        self._internal_id_counter = itertools.count(_INTERNAL_ID_BASE)
        self._pipe_reader_task: asyncio.Task | None = None
        self._chrome_watch_task: asyncio.Task | None = None

    # ── lifecycle (cross-thread safe) ────────────────────────────────────

    def start_in_thread(
        self,
        *,
        chrome_bin: str,
        profile_dir,
        display: str,
        extra_args: list[str],
        env: dict[str, str],
    ) -> None:
        """Start the loop thread + aiohttp app + Chrome. Idempotent-ish."""
        if self._thread and self._thread.is_alive():
            logger.info("receptionist: already running")
            return
        self._stopping = False
        self._chrome_env = dict(env)
        self._chrome_profile = profile_dir
        self._chrome_extra_args = list(extra_args)
        self._chrome_bin = chrome_bin
        self._display = display
        self._thread = threading.Thread(
            target=self._run_thread, name="cdp-receptionist", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        loop, self._loop = self._loop, None
        if loop and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
                fut.result(timeout=10)
            except Exception:
                logger.debug("receptionist: shutdown wait failed", exc_info=True)
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def is_healthy(self) -> bool:
        """Cheap HTTP self-check; callable from any thread."""
        if not (self._thread and self._thread.is_alive()):
            return False
        try:
            # /json/version requires a sid; the probe uses a reserved one
            # (never a real session id — nothing dials the returned WS).
            req = urllib.request.Request(
                f"http://{self.host}:{self.port}/json/version?sid=__probe__",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                payload = json.loads(resp.read().decode())
            return bool(payload.get("webSocketDebuggerUrl"))
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def is_chrome_alive(self) -> bool:
        """True when the receptionist is up AND Chrome's pipe is live."""
        if not self.is_healthy():
            return False
        loop = self._loop
        if not loop:
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._chrome_alive(), loop
            )
            return bool(fut.result(timeout=3))
        except Exception:
            return False

    def ensure_chrome_async(self) -> None:
        """Ask the loop to (re)spawn Chrome if needed. Any thread."""
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.ensure_chrome(), loop)

    async def close_idle_connections(self, sid: str, idle_seconds: float = 30.0) -> int:
        """Close connections for ``sid`` idle longer than ``idle_seconds``.

        Used by turn-end cleanup (``observer_tab_cleanup == 'session'``):
        live tool sockets are gone the moment their subprocess exits, so
        anything still attached and idle is a leak.  Returns count closed.
        """
        # ws.receive() timestamps are loop-internal; approximate "idle"
        # by connection open time — a leaked socket from a previous turn
        # is by definition older than the grace window.
        cutoff = time.time() - idle_seconds
        victims = [
            c for c in self._conns.values()
            if c.sid == sid and not c.closed and c.opened_at < cutoff
        ]
        for conn in victims:
            logger.info(
                "receptionist: closing orphan conn=%s sid=%s opened=%.0f",
                conn.conn_id, conn.sid, conn.opened_at,
            )
            await self._drop_connection(conn, reason="orphan")
        return len(victims)

    # ── loop thread body ─────────────────────────────────────────────────

    def _run_thread(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._start_app())
            loop.run_forever()
        except Exception:
            logger.exception("receptionist: loop thread crashed")
        finally:
            try:
                loop.run_until_complete(self._shutdown_cleanup())
            except Exception:
                pass
            loop.close()

    async def _start_app(self) -> None:
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/json/version", self.handle_json_version)
        app.router.add_get("/json/list", self.handle_json_list)
        app.router.add_get("/json", self.handle_json_list)
        app.router.add_get("/json/new", self.handle_json_new)
        app.router.add_get("/json/close/{tid}", self.handle_json_close)
        app.router.add_get("/json/activate/{tid}", self.handle_json_activate)
        app.router.add_get("/json/protocol", self.handle_json_protocol)
        app.router.add_get("/devtools/browser/{token}", self.handle_browser_ws)
        app.router.add_get("/devtools/page/{tid}", self.handle_page_ws)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info(
            "receptionist: listening on http://%s:%s (chrome via pipe)",
            self.host, self.port,
        )
        await self.ensure_chrome()

    async def _shutdown(self) -> None:
        if self._chrome_watch_task:
            self._chrome_watch_task.cancel()
        if self._pipe_reader_task:
            self._pipe_reader_task.cancel()
        for conn in list(self._conns.values()):
            await self._drop_connection(conn, reason="shutdown")
        from app.api import cdp_pipe

        await cdp_pipe.terminate(self._chrome)
        self._chrome = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    async def _shutdown_cleanup(self) -> None:
        pass

    async def _chrome_alive(self) -> bool:
        return self._chrome is not None and self._chrome.alive

    # ── chrome management (loop thread) ──────────────────────────────────

    async def ensure_chrome(self) -> bool:
        """Spawn Chrome over the pipe if not running. Returns success."""
        assert self._loop is asyncio.get_running_loop()
        if await self._chrome_alive():
            return True
        from app.api import cdp_pipe

        await cdp_pipe.terminate(self._chrome)
        self._chrome = None
        self._reset_tables()
        handle = await cdp_pipe.spawn_chrome_with_pipe(
            self._chrome_bin,
            display=self._display,
            profile_dir=self._chrome_profile,
            extra_args=self._chrome_extra_args,
            env=self._chrome_env,
        )
        self._chrome = handle
        self._pipe_reader_task = asyncio.create_task(self._pipe_read_loop())
        self._chrome_watch_task = asyncio.create_task(self._supervise_chrome())
        logger.info("receptionist: chrome up pid=%s", handle.proc.pid)
        return True

    async def _supervise_chrome(self) -> None:
        """Wait for Chrome to exit, then respawn with backoff."""
        handle = self._chrome
        if handle is None:
            return
        await handle.proc.wait()
        if self._stopping or self._chrome is not handle:
            return
        logger.warning("receptionist: chrome pid=%s exited; respawning", handle.proc.pid)
        self._chrome = None
        self._reset_tables()
        delay = 1.0
        while not self._stopping:
            await asyncio.sleep(delay)
            try:
                if await self.ensure_chrome():
                    logger.info("receptionist: chrome respawned")
                    return
            except Exception:
                logger.exception("receptionist: chrome respawn failed")
            delay = min(delay * 2, 30.0)

    def _reset_tables(self) -> None:
        self._conns.clear()
        self._sessions.clear()
        self._tabs.clear()
        self._cdp_sessions.clear()
        self._pending.clear()

    # ── pipe demux (loop thread) ─────────────────────────────────────────

    async def _pipe_read_loop(self) -> None:
        from app.api import cdp_pipe

        handle = self._chrome
        if handle is None:
            return
        try:
            while True:
                msg = await cdp_pipe.read_frame(handle.reader)
                if msg is None:
                    break
                await self._dispatch_chrome_message(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("receptionist: pipe reader died")

    async def _dispatch_chrome_message(self, msg: dict) -> None:
        if "id" in msg:
            spec = self._pending.pop(int(msg["id"]), None)
            if spec is None:
                logger.debug("receptionist: unmatched response id=%s", msg.get("id"))
                return
            kind = spec[0]
            if kind == "internal":
                spec[1].set_result(msg)
                return
            # client response: rewrite id back to the client's original
            # (spec may carry a 4th field: the targetId for attach/close)
            _, conn, orig_id = spec[:3]
            if conn.closed:
                return
            session_id = msg.get("sessionId")
            if session_id and self._cdp_sessions.get(session_id) not in (
                conn.conn_id, None
            ):
                return
            result = msg.get("result") or {}
            if kind == "client-create":
                target_id = result.get("targetId")
                if target_id and not msg.get("error"):
                    self._tabs[target_id] = conn.conn_id
                    conn.owned_targets.add(target_id)
            elif kind == "client-attach":
                new_session = result.get("sessionId")
                target_id = spec[3] if len(spec) > 3 else None
                if new_session and target_id in conn.owned_targets:
                    self._cdp_sessions[new_session] = conn.conn_id
            elif kind == "client-close":
                # Real Chrome sends no targetDestroyed for closeTarget in
                # this mode, so ownership must be forgotten here.
                target_id = spec[3] if len(spec) > 3 else None
                if target_id and not msg.get("error"):
                    self._tabs.pop(target_id, None)
                    conn.owned_targets.discard(target_id)
            msg = dict(msg)
            msg["id"] = orig_id
            await self._send_ws(conn, msg)
            return

        session_id = msg.get("sessionId")
        if session_id:
            conn_id = self._cdp_sessions.get(session_id)
            if conn_id is None:
                return  # foreign / stale session — drop
            conn = self._conns.get(conn_id)
            if conn and not conn.closed:
                await self._send_ws(conn, msg)
            return

        method = msg.get("method", "")
        params = msg.get("params") or {}
        if method == "Target.attachedToTarget":
            await self._on_attached_to_target(params)
            return
        if method == "Target.detachedFromTarget":
            sid = params.get("sessionId")
            conn_id = self._cdp_sessions.pop(sid, None)
            if conn_id:
                conn = self._conns.get(conn_id)
                if conn and not conn.closed:
                    await self._send_ws(conn, msg)
            return
        if method == "Target.targetDestroyed":
            # A tab died (client-closed its own tab, or Chrome exited it).
            # Forget ownership so /json/list and the per-conn target cap
            # don't count stale ids — but still deliver the event to the
            # former owner first.
            dead = params.get("targetId") or msg.get("targetId")
            owner_id = self._tabs.pop(dead, None) if dead else None
            if owner_id:
                owner = self._conns.get(owner_id)
                if owner:
                    owner.owned_targets.discard(dead)
                    if not owner.closed:
                        await self._send_ws(owner, msg)
            return
        target_id = params.get("targetId") or msg.get("targetId")
        if not target_id and "targetInfo" in params:
            target_id = (params.get("targetInfo") or {}).get("targetId")
        if target_id:
            conn_id = self._tabs.get(target_id)
            if conn_id is None:
                return  # foreign target event — drop
            conn = self._conns.get(conn_id)
            if conn and not conn.closed:
                await self._send_ws(conn, msg)
            return
        # Browser-level, target-neutral event: broadcast to everyone.
        for conn in list(self._conns.values()):
            if not conn.closed:
                await self._send_ws(conn, msg)

    async def _on_attached_to_target(self, params: dict) -> None:
        """Route a flatten-mode attach to the owning connection."""
        session_id = params.get("sessionId")
        target_id = (params.get("targetInfo") or {}).get("targetId")
        conn_id = self._tabs.get(target_id) if target_id else None
        if not session_id or conn_id is None:
            return  # foreign target attached — drop entirely
        self._cdp_sessions[session_id] = conn_id
        conn = self._conns.get(conn_id)
        if conn and not conn.closed:
            await self._send_ws(
                conn, {"method": "Target.attachedToTarget", "params": params}
            )

    # ── client → chrome (loop thread) ────────────────────────────────────

    async def _forward_client_message(
        self, conn: ClientConnection, msg: dict
    ) -> None:
        chrome = self._chrome
        if chrome is None or not chrome.alive:
            await self._send_error(conn, msg.get("id"), "browser unavailable")
            return
        session_id = msg.get("sessionId")
        if session_id and self._cdp_sessions.get(session_id) != conn.conn_id:
            await self._send_error(
                conn, msg.get("id"), "sessionId not owned by this connection"
            )
            return
        method = msg.get("method", "")
        if method == "Target.createTarget":
            if len(conn.owned_targets) >= MAX_TARGETS_PER_CONN:
                await self._send_error(
                    conn, msg.get("id"),
                    f"target cap reached ({MAX_TARGETS_PER_CONN})",
                )
                return
        elif method in ("Target.closeTarget", "Target.activateTarget",
                        "Target.attachToTarget"):
            # Ownership gate: a connection may only act on targets it owns.
            # Without this, any sid could close or attach to another sid's
            # tabs on the shared desktop.
            tid = (msg.get("params") or {}).get("targetId")
            if tid is None or tid not in conn.owned_targets:
                await self._send_error(
                    conn, msg.get("id"), "target not owned by this connection"
                )
                return
        out = dict(msg)
        if "id" in msg:
            gid = next(self._id_counter)
            # Tag the pending entry with its tracking kind NOW, before the
            # write yields the loop: Chrome's response can be dispatched
            # while write_frame is still awaiting the executor, so a
            # retag-after-forward would race and lose ownership.
            method_name = msg.get("method", "")
            if method_name == "Target.createTarget":
                self._pending[gid] = ("client-create", conn, msg["id"])
            elif method_name == "Target.attachToTarget":
                target_id = (msg.get("params") or {}).get("targetId")
                self._pending[gid] = (
                    "client-attach", conn, msg["id"], target_id
                )
            elif method_name == "Target.closeTarget":
                target_id = (msg.get("params") or {}).get("targetId")
                self._pending[gid] = (
                    "client-close", conn, msg["id"], target_id
                )
            else:
                self._pending[gid] = ("client", conn, msg["id"])
            out["id"] = gid
        await chrome.writer.write_frame(out)

    async def _send_error(self, conn: ClientConnection, orig_id, message: str) -> None:
        if orig_id is None:
            return
        await self._send_ws(
            conn,
            {
                "id": orig_id,
                "error": {"code": -32000, "message": f"receptionist: {message}"},
            },
        )

    async def _send_ws(self, conn: ClientConnection, obj: dict) -> None:
        try:
            await conn.ws.send_str(json.dumps(obj, separators=(",", ":")))
        except Exception:
            conn.closed = True

    async def _internal_command(self, method: str, params: dict | None = None) -> dict:
        """Send a receptionist-owned CDP command and await its response."""
        chrome = self._chrome
        if chrome is None or not chrome.alive:
            raise RuntimeError("chrome unavailable")
        gid = next(self._internal_id_counter)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[gid] = ("internal", fut)
        await chrome.writer.write_frame(
            {"id": gid, "method": method, "params": params or {}}
        )
        # Bounded wait: an unmatched/malformed Chrome response (e.g. an
        # id-less error frame) would otherwise hang connection setup.
        try:
            return await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending.pop(gid, None)
            raise RuntimeError(f"internal CDP command timed out: {method}") from None

    async def _track_created_target(self, conn_id: str, target_id: str) -> None:
        self._tabs[target_id] = conn_id
        conn = self._conns.get(conn_id)
        if conn:
            conn.owned_targets.add(target_id)

    # ── HTTP micro-endpoints ─────────────────────────────────────────────

    @property
    def _ws_base(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def _extract_sid(self, request) -> str:
        sid = request.query.get("sid") or request.headers.get("X-LeapAide-Session") or ""
        sid = sid.strip()
        # Discovery clients (SDK browser_tool._resolve_cdp_override,
        # browser_harness) decide "this URL already IS /json/version"
        # with endswith("/json/version") — which fails once our ?sid=
        # query is present — and append the path a second time, so the
        # query value arrives as "<sid>/json/version". Session ids never
        # contain "/", so strip the duplicated suffix; otherwise the
        # polluted sid breaks /json/list filtering and turn-end
        # close_idle_connections matching (orphan sweep never fires).
        while sid.endswith("/json/version"):
            sid = sid[: -len("/json/version")]
        return sid.strip() or "anonymous"

    async def handle_json_version(self, request):
        from aiohttp import web

        if not _sid_present(request):
            return web.json_response(
                {
                    "error": _SID_REQUIRED,
                    "example": "/json/version?sid=<session_id>",
                },
                status=400,
            )
        sid = self._extract_sid(request)
        # Embed the caller's sid in the browser WS URL so downstream tools
        # (SDK _resolve_cdp_override fetches this endpoint) reconnect with
        # their identity intact.
        ws_url = f"{self._ws_base}/devtools/browser/global?sid={sid}"
        return web.json_response(
            {
                "Browser": "LeapAide/1.0",
                "Protocol-Version": "1.3",
                "User-Agent": "LeapAide Receptionist",
                "V8-Version": "",
                "WebKit-Version": "",
                "webSocketDebuggerUrl": ws_url,
            }
        )

    async def handle_json_list(self, request):
        from aiohttp import web

        sid = self._extract_sid(request)
        out = []
        for target_id, conn_id in self._tabs.items():
            conn = self._conns.get(conn_id)
            if conn is None or conn.closed or conn.sid != sid or sid == "anonymous":
                continue
            out.append(
                {
                    "description": "",
                    "devtoolsFrontendUrl": "",
                    "id": target_id,
                    "title": "",
                    "type": "page",
                    "url": "",
                    "webSocketDebuggerUrl": (
                        f"{self._ws_base}/devtools/page/{target_id}?sid={sid}"
                    ),
                }
            )
        return web.json_response(out)

    async def handle_json_new(self, request):
        from aiohttp import web

        # Prefer Target.createTarget over the browser WS; this endpoint
        # requires an existing connection for the sid so the new tab has
        # an owner to clean it up.
        sid = self._extract_sid(request)
        conn = next(
            (c for c in self._conns.values() if c.sid == sid and not c.closed),
            None,
        )
        if conn is None:
            return web.json_response(
                {"error": "no active connection for this sid; "
                          "use Target.createTarget over the browser WS"},
                status=409,
            )
        url = request.query.get("url", "about:blank")
        try:
            resp = await self._internal_command(
                "Target.createTarget", {"url": url, "background": True}
            )
        except Exception:
            return web.json_response({"error": "browser unavailable"}, status=503)
        target_id = (resp.get("result") or {}).get("targetId")
        if not target_id:
            return web.json_response({"error": "createTarget failed"}, status=500)
        await self._track_created_target(conn.conn_id, target_id)
        return web.json_response(
            {
                "description": "",
                "id": target_id,
                "title": "",
                "type": "page",
                "url": url,
                "webSocketDebuggerUrl": (
                    f"{self._ws_base}/devtools/page/{target_id}?sid={sid}"
                ),
            }
        )

    async def _owned_or_forbidden(self, request, tid: str):
        sid = self._extract_sid(request)
        conn_id = self._tabs.get(tid)
        if conn_id is None:
            return None, sid
        conn = self._conns.get(conn_id)
        if conn is None or conn.closed or (sid != "anonymous" and conn.sid != sid):
            return None, sid
        return conn, sid

    async def handle_json_close(self, request):
        from aiohttp import web

        tid = request.match_info["tid"]
        conn, _ = await self._owned_or_forbidden(request, tid)
        if conn is None:
            return web.json_response({"error": "unknown or foreign target"}, status=404)
        try:
            await self._internal_command("Target.closeTarget", {"targetId": tid})
        except Exception:
            return web.json_response({"error": "browser unavailable"}, status=503)
        self._tabs.pop(tid, None)
        conn.owned_targets.discard(tid)
        return web.json_response({"closed": True})

    async def handle_json_activate(self, request):
        from aiohttp import web

        tid = request.match_info["tid"]
        conn, _ = await self._owned_or_forbidden(request, tid)
        if conn is None:
            return web.json_response({"error": "unknown or foreign target"}, status=404)
        try:
            await self._internal_command("Target.activateTarget", {"targetId": tid})
        except Exception:
            return web.json_response({"error": "browser unavailable"}, status=503)
        return web.json_response({"activated": True})

    async def handle_json_protocol(self, request):
        from aiohttp import web

        # Minimal static schema pointer; full schema is large and rarely
        # fetched — clients that need it talk CDP directly.
        return web.json_response({"version": {"major": "1", "minor": "3"}})

    # ── WebSocket endpoints ──────────────────────────────────────────────

    async def handle_browser_ws(self, request):
        from aiohttp import web

        if not _sid_present(request):
            return web.json_response({"error": _SID_REQUIRED}, status=400)
        if self._chrome is None or not self._chrome.alive:
            return web.Response(status=503, text="browser unavailable")
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        conn = ClientConnection(
            conn_id=uuid.uuid4().hex[:12],
            sid=self._extract_sid(request),
            peer=request.remote or "?",
            ws=ws,
        )
        self._conns[conn.conn_id] = conn
        self._sessions.setdefault(conn.sid, set()).add(conn.conn_id)
        logger.info(
            "receptionist: conn open conn=%s sid=%s peer=%s",
            conn.conn_id, conn.sid, conn.peer,
        )
        try:
            # No connect-time tab is pre-created. The ownership gates
            # (filtered getTargets//json/list, gated attach/close) already
            # keep a fresh connection away from foreign tabs, and the
            # client creates its own tab on demand — a pre-seeded blank
            # only added a second, never-navigated tab to the panel.
            from aiohttp import WSMsgType

            async for raw in ws:
                if raw.type in (WSMsgType.CLOSE, WSMsgType.CLOSING,
                                WSMsgType.CLOSED, WSMsgType.ERROR):
                    break
                if raw.type not in (WSMsgType.TEXT, WSMsgType.BINARY):
                    continue
                try:
                    msg = json.loads(raw.data)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    await self._handle_client_frame(conn, msg)
        finally:
            await self._drop_connection(conn, reason="disconnect")
        return ws

    async def handle_page_ws(self, request):
        """Page-scoped WS: only the owning connection may attach."""
        from aiohttp import web

        if not _sid_present(request):
            return web.json_response({"error": _SID_REQUIRED}, status=400)
        tid = request.match_info["tid"]
        conn_meta, _ = await self._owned_or_forbidden(request, tid)
        if conn_meta is None:
            return web.Response(status=403, text="target not owned by this connection")
        return web.Response(status=501, text="use the browser WS endpoint")

    async def _handle_client_frame(
        self, conn: ClientConnection, msg: dict
    ) -> None:
        method = msg.get("method", "")
        # Rewrite responses the receptionist must own or filter.
        if method == "Target.getTargets":
            orig_id = msg.get("id")
            if orig_id is not None:
                try:
                    resp = await self._internal_command("Target.getTargets", {})
                except Exception:
                    await self._send_error(conn, orig_id, "browser unavailable")
                    return
                infos = (resp.get("result") or {}).get("targetInfos", [])
                owned = [
                    t for t in infos
                    if t.get("targetId") in conn.owned_targets
                ]
                await self._send_ws(
                    conn,
                    {
                        "id": orig_id,
                        "result": {"targetInfos": owned},
                        **(
                            {"sessionId": resp["sessionId"]}
                            if "sessionId" in resp
                            else {}
                        ),
                    },
                )
                return
        await self._forward_client_message(conn, msg)
        # Ownership for client-created targets is registered when the
        # response arrives; the pending entry carries its tracking kind
        # (set in _forward_client_message before the write).

    def _retag_pending(
        self, conn: ClientConnection, orig_id, kind: str, extra=None
    ) -> None:
        """Re-tag a just-forwarded client request for ownership tracking."""
        for gid, spec in list(self._pending.items()):
            if spec[0] == "client" and spec[1] is conn and spec[2] == orig_id:
                if extra is None:
                    self._pending[gid] = (kind, conn, orig_id)
                else:
                    self._pending[gid] = (kind, conn, orig_id, extra)
                return

    async def _drop_connection(self, conn: ClientConnection, reason: str) -> None:
        if conn.closed:
            return
        conn.closed = True
        logger.info(
            "receptionist: conn close conn=%s sid=%s reason=%s targets=%d",
            conn.conn_id, conn.sid, reason, len(conn.owned_targets),
        )
        # Close every tab this connection owns.
        for target_id in list(conn.owned_targets):
            try:
                await self._internal_command(
                    "Target.closeTarget", {"targetId": target_id}
                )
            except Exception:
                pass
            self._tabs.pop(target_id, None)
        conn.owned_targets.clear()
        # Drop its cdp sessions.
        for sid_key in [
            k for k, v in self._cdp_sessions.items() if v == conn.conn_id
        ]:
            self._cdp_sessions.pop(sid_key, None)
        # Drop pending requests.
        for gid in [
            g
            for g, spec in self._pending.items()
            if spec[0] in ("client", "client-create", "client-attach")
            and spec[1] is conn
        ]:
            self._pending.pop(gid, None)
        self._conns.pop(conn.conn_id, None)
        conns = self._sessions.get(conn.sid)
        if conns:
            conns.discard(conn.conn_id)
            if not conns:
                self._sessions.pop(conn.sid, None)
        try:
            await conn.ws.close()
        except Exception:
            pass


# ── module-level singleton ──────────────────────────────────────────────────

_RECEPTIONIST: "Receptionist | None" = None


def get_receptionist() -> "Receptionist | None":
    return _RECEPTIONIST


def set_receptionist(r: "Receptionist | None") -> None:
    global _RECEPTIONIST
    _RECEPTIONIST = r
