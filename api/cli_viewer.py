"""
Hermes Web UI — SSE → xterm CLI tool visualization.

Listens to tool.started / tool.completed events from the SSE streaming
pipeline and spawns xterm windows on the session's Xvfb virtual display.

CRITICAL: Only visualizes when SessionDisplay is already running.
If the user hasn't clicked 👁, silently skips all visualization (FR-015).
Agent tools execute normally regardless — chat panel ToolCard always visible.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

_XTERM_AVAILABLE = shutil.which("xterm") is not None
_XTERM_WARNED = False

MAX_XTERMS = int(os.environ.get("HERMES_OBSERVE_MAX_XTERMS", "6"))
_XTERM_CLOSE_DELAY = 5.0
_xterm_outputs: dict[str, str] = {}


def _format_json_like(value) -> str:
    """Format a dict/list as indented JSON. Strings are kept as-is (no quotes)."""
    if isinstance(value, dict):
        try:
            return json.dumps(value, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, list):
        try:
            return json.dumps(value, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def on_tool_started(session_id: str, name: str, args: dict | None, tid: str) -> None:
    """Spawn an xterm window for a tool that just started."""
    global _XTERM_WARNED
    if not _XTERM_AVAILABLE:
        if not _XTERM_WARNED:
            logger.warning("xterm not found — tool visualization disabled. Install with: apt install xterm")
            _XTERM_WARNED = True
        return

    from api.session_display import SessionDisplay, _DISPLAYS

    sd = _DISPLAYS.get(session_id)
    if not sd or not sd.is_running():
        return

    display = sd.display_num
    if display is None:
        return

    # Enforce max xterms limit
    if len(sd.xterm_procs) >= MAX_XTERMS:
        oldest_tid = next(iter(sd.xterm_procs), None)
        if oldest_tid:
            try:
                sd.xterm_procs[oldest_tid].terminate()
            except OSError:
                pass
            del sd.xterm_procs[oldest_tid]
            _cleanup_tmpfile(oldest_tid)

    # Build display content
    params_str = _format_json_like(args) if args else "(none)"
    content = (
        f"--- Cmd ---\n"
        f"{name}\n\n"
        f"--- Params ---\n"
        f"{params_str}\n"
    )

    try:
        tmpf = tempfile.NamedTemporaryFile(
            mode="w", prefix=f"hermes-xterm-{tid[:8]}-", suffix=".out",
            delete=False,
        )
        tmpf.write(content)
        tmpf.flush()
        tmpf_path = tmpf.name
        _xterm_outputs[tid] = tmpf_path
    except OSError:
        tmpf_path = "/dev/null"

    try:
        proc = subprocess.Popen(
            [
                "xterm",
                "-T", f"[Agent] {name}"[:80],
                "-geometry", "96x58+960+0",
                "-fa", "Monospace",
                "-fs", "18",
                "-bg", "#0d1117",
                "-fg", "#58a6ff",
                "-cr", "#58a6ff",
                "-e", "sh", "-c",
                f"tail -n +1 -f {tmpf_path}",
            ],
            env={**os.environ, "DISPLAY": f":{display}"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        sd.xterm_procs[tid] = proc
    except Exception as exc:
        logger.warning("Failed to spawn xterm for %s/%s: %s", session_id, name, exc)


def on_tool_completed(
    session_id: str,
    name: str,
    tid: str,
    output: str | None,
    duration: float | None,
    is_error: bool,
) -> None:
    """Append result and status to the xterm window, then close it after a delay."""
    from api.session_display import SessionDisplay, _DISPLAYS

    sd = _DISPLAYS.get(session_id)
    if not sd:
        _cleanup_tmpfile(tid)
        return

    proc = sd.xterm_procs.pop(tid, None)
    tmpf_path = _xterm_outputs.pop(tid, None)
    status_text = "✗" if is_error else "✓"
    duration_str = f" ({duration:.1f}s)" if duration else ""
    output_text = (output or "(...)")[:10000]

    print(f"[cli_viewer] {session_id[:12]}/{name} output={len(output_text)} chars duration={duration}",
          flush=True)

    if tmpf_path:
        try:
            with open(tmpf_path, "a") as f:
                f.write(f"\n--- Result ---\n")
                f.write(output_text)
                f.write(f"\n\n--- Status ---\n")
                f.write(f"{status_text}  Completed{duration_str}\n")
                f.write("(window will close shortly)\n")
        except OSError:
            pass

    # Close xterm after a delay for the user to read output
    if proc and proc.poll() is None:
        _close_xterm_later(proc)

    # Clean up temp file after matching delay
    if tmpf_path:
        def _delayed_cleanup(path, delay=_XTERM_CLOSE_DELAY):
            time.sleep(delay)
            try:
                os.unlink(path)
            except OSError:
                pass
        threading.Thread(target=_delayed_cleanup, args=(tmpf_path,), daemon=True).start()


def cleanup_session_xterms(session_id: str) -> None:
    """Close all xterms for a session. Called when the streaming turn ends."""
    from api.session_display import _DISPLAYS

    sd = _DISPLAYS.get(session_id)
    if not sd:
        return
    for tid, proc in list(sd.xterm_procs.items()):
        try:
            proc.terminate()
        except OSError:
            pass
        _cleanup_tmpfile(tid)
    sd.xterm_procs.clear()


def _close_xterm_later(proc: subprocess.Popen, delay: float = _XTERM_CLOSE_DELAY) -> None:
    def _do_close():
        time.sleep(delay)
        try:
            proc.terminate()
        except OSError:
            pass
    threading.Thread(target=_do_close, daemon=True).start()


def _cleanup_tmpfile(tid: str) -> None:
    tmpf_path = _xterm_outputs.pop(tid, None)
    if tmpf_path:
        try:
            os.unlink(tmpf_path)
        except OSError:
            pass
