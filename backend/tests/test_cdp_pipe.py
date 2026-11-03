"""Tests for app.api.cdp_pipe — Chrome spawn via --remote-debugging-pipe.

The round-trip test speaks real NUL-delimited JSON over fds 3/4 with a
fake Chrome child (a bash wrapper that ignores Chrome's flags and runs
an echo responder), so the framing and the fd plumbing are exercised
end-to-end without a browser installed.
"""

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from app.api import cdp_pipe

REPO_ROOT = Path(__file__).resolve().parents[2]

FAKE_CHROME_PY = """\
import json, os, sys

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

while True:
    msg = read_frame()
    if msg is None:
        break
    write({"id": msg.get("id"), "result": {"echo": msg.get("method")}})
"""


@pytest.fixture
def fake_chrome(tmp_path):
    """Path to a bash wrapper that execs the echo responder on fds 3/4."""
    responder = tmp_path / "fake_chrome.py"
    responder.write_text(FAKE_CHROME_PY)
    wrapper = tmp_path / "fake-chrome-bin"
    # Chrome's CLI flags are intentionally not forwarded: python would
    # reject --remote-debugging-pipe as an interpreter option.
    wrapper.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" -u "{responder}"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return wrapper


def _env():
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }


# ── Frame codec (no process involved) ───────────────────────────────────────


def test_read_frame_parses_nul_delimited_json():
    async def main():
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"id":1,"result":{}}\0{"id":2,"result":{}}\0')
        first = await cdp_pipe.read_frame(reader)
        second = await cdp_pipe.read_frame(reader)
        assert first == {"id": 1, "result": {}}
        assert second == {"id": 2, "result": {}}

    asyncio.run(main())


def test_read_frame_eof_returns_none():
    async def main():
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"id":1}\0')
        reader.feed_eof()
        assert await cdp_pipe.read_frame(reader) == {"id": 1}
        assert await cdp_pipe.read_frame(reader) is None

    asyncio.run(main())


def test_read_frame_tolerates_stray_nuls():
    async def main():
        reader = asyncio.StreamReader()
        reader.feed_data(b'\0\0{"id":1}\0')
        assert await cdp_pipe.read_frame(reader) == {"id": 1}

    asyncio.run(main())


def test_read_frame_mid_frame_close_raises():
    async def main():
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"id":1,"resu')  # no NUL, then EOF
        reader.feed_eof()
        with pytest.raises(RuntimeError, match="mid-frame"):
            await cdp_pipe.read_frame(reader)

    asyncio.run(main())


def test_read_frame_invalid_json_raises():
    async def main():
        reader = asyncio.StreamReader()
        reader.feed_data(b"not-json\0")
        with pytest.raises(RuntimeError, match="invalid CDP pipe frame"):
            await cdp_pipe.read_frame(reader)

    asyncio.run(main())


# ── Spawn + round-trip + teardown ───────────────────────────────────────────


def test_spawn_round_trip_and_terminate(fake_chrome, tmp_path):
    """Frames written on the command pipe come back on the event pipe."""

    async def main():
        handle = await cdp_pipe.spawn_chrome_with_pipe(
            str(fake_chrome),
            display=":99",
            profile_dir=tmp_path / "profile",
            extra_args=["--no-sandbox"],
            env=_env(),
        )
        try:
            assert handle.alive
            await handle.writer.write_frame({"id": 7, "method": "Fake.echo"})
            resp = await asyncio.wait_for(cdp_pipe.read_frame(handle.reader), 5)
            assert resp == {"id": 7, "result": {"echo": "Fake.echo"}}
        finally:
            await cdp_pipe.terminate(handle)
        assert handle.proc.returncode is not None
        assert not handle.alive

    asyncio.run(main())


def test_spawn_cleans_singleton_locks(fake_chrome, tmp_path):
    """Stale Chrome ProcessSingleton locks are removed before spawn."""

    async def main():
        profile = tmp_path / "profile"
        (profile / "Default").mkdir(parents=True)
        (profile / "SingletonLock").write_text("stale")
        (profile / "Default" / "SingletonSocket").write_text("stale")
        handle = await cdp_pipe.spawn_chrome_with_pipe(
            str(fake_chrome),
            display=":99",
            profile_dir=profile,
            extra_args=[],
            env=_env(),
        )
        try:
            assert not (profile / "SingletonLock").exists()
            assert not (profile / "Default" / "SingletonSocket").exists()
        finally:
            await cdp_pipe.terminate(handle)

    asyncio.run(main())


def test_terminate_none_is_noop():
    asyncio.run(cdp_pipe.terminate(None))


def test_terminate_closes_parent_fds(fake_chrome, tmp_path):
    """After terminate the parent-side pipe ends are closed."""

    async def main():
        handle = await cdp_pipe.spawn_chrome_with_pipe(
            str(fake_chrome),
            display=":99",
            profile_dir=tmp_path / "profile",
            extra_args=[],
            env=_env(),
        )
        evt_r, cmd_w = handle.raw_fds
        await cdp_pipe.terminate(handle)
        for fd in (evt_r, cmd_w):
            with pytest.raises(OSError):
                os.fstat(fd)

    asyncio.run(main())
