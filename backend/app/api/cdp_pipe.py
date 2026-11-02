"""Chrome launch via ``--remote-debugging-pipe`` (parent-child fd channel).

Chrome's ``--remote-debugging-pipe`` flag makes the browser read CDP
frames on fd 3 and write them on fd 4 — no TCP port is opened, so no
connectable CDP endpoint appears in ``ps``/``ss`` output.  The only
holder of the pipe is the parent that spawned Chrome (the receptionist,
see ``app.api.browser_receptionist``).

Frame format over the pipe is identical to CDP-over-WebSocket except
that messages are delimited by a NUL byte instead of a WS frame:
``<json>\\0<json>\\0...``.

All spawn/IO helpers here run on the receptionist's asyncio loop thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# CDP messages over the pipe are small (JSON), but a runaway target can
# produce large payloads — cap frames at 16 MiB to bound memory.
MAX_FRAME_BYTES = 16 * 1024 * 1024


class PipeWriter:
    """NUL-delimited JSON writer over a raw writable fd.

    Writes go through an executor so a full pipe buffer blocks a worker
    thread instead of the receptionist event loop.
    """

    def __init__(self, fd: int, loop: asyncio.AbstractEventLoop) -> None:
        self._fd = fd
        self._loop = loop

    async def write_frame(self, obj: dict) -> None:
        data = json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\0"
        await self._loop.run_in_executor(None, os.write, self._fd, data)


@dataclass
class ChromePipeHandle:
    """Parent-side ends of the CDP pipe plus the Chrome subprocess."""

    proc: asyncio.subprocess.Process
    reader: asyncio.StreamReader  # evt_r: Chrome writes CDP frames here
    writer: PipeWriter            # cmd_w: parent writes CDP frames here
    raw_fds: tuple[int, int] = field(default_factory=tuple)  # parent ends

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None


async def read_frame(reader: asyncio.StreamReader) -> dict | None:
    """Read one NUL-terminated CDP frame. Returns None on EOF."""
    try:
        buf = await reader.readuntil(b"\0")
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None  # clean EOF
        raise RuntimeError("CDP pipe closed mid-frame") from exc
    except asyncio.LimitOverrunError as exc:
        raise RuntimeError(
            f"CDP pipe frame exceeds {MAX_FRAME_BYTES} bytes"
        ) from exc
    payload = buf[:-1]  # strip trailing NUL
    if not payload:
        return await read_frame(reader)  # tolerate stray NULs
    try:
        return json.loads(payload)
    except ValueError as exc:
        raise RuntimeError(f"invalid CDP pipe frame: {payload[:200]!r}") from exc


# Tiny bootstrap executed as the child instead of using preexec_fn: it
# re-exposes the chrome-side pipe ends as fds 3/4 and execve's Chrome.
# A preexec_fn dup2 is not reliable here — on some Python builds fds
# created there do not survive to the exec'd image — while a bootstrap
# that dup2's immediately before execve always does. It also avoids the
# preexec_fn/thread-safety warnings entirely.
_BOOTSTRAP = (
    "import fcntl,os,sys\n"
    "cmd_r=int(sys.argv[1]);evt_w=int(sys.argv[2]);chrome=sys.argv[3:]\n"
    "if cmd_r==4:cmd_r=fcntl.fcntl(cmd_r,fcntl.F_DUPFD,10)\n"
    "if evt_w==3:evt_w=fcntl.fcntl(evt_w,fcntl.F_DUPFD,10)\n"
    "os.dup2(cmd_r,3)\n"
    "os.dup2(evt_w,4)\n"
    "for fd in (cmd_r,evt_w):\n"
    "    if fd>4:\n"
    "        try:os.close(fd)\n"
    "        except OSError:pass\n"
    "os.execv(chrome[0],chrome)\n"
)


async def spawn_chrome_with_pipe(
    chrome_bin: str,
    *,
    display: str,
    profile_dir: Path,
    extra_args: list[str],
    env: dict[str, str],
) -> ChromePipeHandle:
    """Spawn Chrome with the CDP pipe attached on fds 3/4.

    Must be called on the asyncio loop that will own the handle.
    """
    import sys as _sys

    profile_dir.mkdir(parents=True, exist_ok=True)
    # Chrome's ProcessSingleton leaves stale lock files after a container
    # recreation (hostname changes); this profile is owned exclusively by
    # the global desktop, so leftovers are always stale.
    for sub in ("", "Default"):
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (profile_dir / sub / name).unlink(missing_ok=True)
            except OSError:
                pass

    cmd_r, cmd_w = os.pipe()
    evt_r, evt_w = os.pipe()
    chrome_args = [
        chrome_bin,
        "--remote-debugging-pipe",
        f"--user-data-dir={profile_dir}",
        *extra_args,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            _sys.executable,
            "-c",
            _BOOTSTRAP,
            str(cmd_r),
            str(evt_w),
            *chrome_args,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(cmd_r, evt_w),
        )
    except BaseException:
        for fd in (cmd_r, cmd_w, evt_r, evt_w):
            try:
                os.close(fd)
            except OSError:
                pass
        raise

    # Parent must drop its copies of the chrome-side ends (the child has
    # its own via dup2/pass_fds), or the pipe never sees EOF.
    try:
        os.close(cmd_r)
    except OSError:
        pass
    try:
        os.close(evt_w)
    except OSError:
        pass

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_FRAME_BYTES)
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(
        lambda: protocol, os.fdopen(evt_r, "rb", buffering=0)
    )
    writer = PipeWriter(cmd_w, loop)
    handle = ChromePipeHandle(
        proc=proc,
        reader=reader,
        writer=writer,
        raw_fds=(evt_r, cmd_w),
    )
    logger.info(
        "cdp_pipe: chrome spawned pid=%s display=%s profile=%s",
        proc.pid, display, profile_dir,
    )
    return handle


async def terminate(handle: ChromePipeHandle | None) -> None:
    """SIGTERM → wait 3s → SIGKILL, then close parent pipe ends."""
    if handle is None:
        return
    proc = handle.proc
    if proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            try:
                proc.send_signal(signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("cdp_pipe: chrome pid=%s refused to die", proc.pid)
    for fd in handle.raw_fds:
        try:
            os.close(fd)
        except OSError:
            pass
