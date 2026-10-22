"""Startup-prewarm coordination for first-chat latency (issue #8).

server._prewarm() first synchronously imports the chat-path modules, then
spawns daemon threads that warm expensive hermes-agent caches; request
threads (streaming._run_agent_streaming) wait on the events below so a chat
sent within seconds of boot joins the in-flight prewarm instead of racing
it.  The SDK's check_fn cache has no single-flight (tools/registry.py
_check_fn_cached runs the probe outside its lock), so a racing chat would
duplicate slow work such as a lazy ``uv pip install`` or vision-provider
network probes.

This module is a deliberate leaf: it must not import server, streaming, or
any hermes/SDK module at import time (the SDK is imported lazily inside
warm_tool_definitions), so both sides can depend on it without cycles.
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# True once server._prewarm() has taken over.  Waiters in request
# threads only block while a prewarm is actually running; tests and
# embedders that drive streaming without server.main() keep the immediate
# (today's) behaviour.
PREWARM_ACTIVE = False

_MCP_DONE = threading.Event()
_TOOL_DEFS_DONE = threading.Event()
_TOOL_DEFS_LOCK = threading.Lock()
_TOOL_DEFS_RAN = False  # single-flight flag for warm_tool_definitions


def begin_prewarm() -> None:
    """Mark prewarm as active.  Called first thing in server._prewarm()."""
    global PREWARM_ACTIVE
    PREWARM_ACTIVE = True


def mark_mcp_done() -> None:
    """Signal that the startup MCP discovery prewarm finished (success or failure)."""
    _MCP_DONE.set()


def wait_for_mcp_discovery(timeout: float) -> bool:
    """Block until the startup MCP prewarm finishes (issue #8).

    discover_mcp_tools() takes a cross-process flock; a chat that races the
    server's own prewarm-mcp thread spins the SDK's lock-retry loop
    (240 x 0.5s) even when both threads live in the same process.  Returns
    True when it is safe to call discover_mcp_tools() now — immediately
    True when no prewarm is active (tests, embedders).
    """
    if not PREWARM_ACTIVE or _MCP_DONE.is_set():
        return True
    return _MCP_DONE.wait(timeout)


def tool_definitions_disabled() -> bool:
    """True when HERMES_WEBUI_PREWARM_TOOL_DEFS opts out of the tool-defs prewarm."""
    return str(os.environ.get("HERMES_WEBUI_PREWARM_TOOL_DEFS", "")).strip().lower() in (
        "0",
        "false",
        "no",
    )


def mark_tool_defs_done() -> None:
    """Signal tool-definitions readiness without computing (opt-out path)."""
    _TOOL_DEFS_DONE.set()


def _load_tool_definitions():
    """Return the SDK's get_tool_definitions (indirection so tests can patch)."""
    from model_tools import get_tool_definitions

    return get_tool_definitions


def warm_tool_definitions(enabled_toolsets) -> None:
    """Build tool definitions once so the first chat hits the SDK memo.

    ``get_tool_definitions(quiet_mode=True)`` memoizes without time expiry
    and runs every tool's check_fn only on a cache miss, so doing it at
    boot moves the whole check_fn gauntlet (lazy ``uv pip install``,
    vision provider probes) off the request path (issue #8).  Single-flight:
    only the first call computes; every path always marks the done event so
    waiters can never hang on a failed prewarm.
    """
    global _TOOL_DEFS_RAN
    with _TOOL_DEFS_LOCK:
        if _TOOL_DEFS_RAN:
            return  # a concurrent/earlier call owns the computation
        _TOOL_DEFS_RAN = True
    t0 = time.monotonic()
    try:
        get_tool_definitions = _load_tool_definitions()
        tools = get_tool_definitions(enabled_toolsets=list(enabled_toolsets), quiet_mode=True)
        logger.info(
            "Pre-warm: tool definitions ready (%.1fs, %d tools)",
            time.monotonic() - t0,
            len(tools),
        )
    except Exception:
        logger.warning(
            "Pre-warm: tool definitions failed (%.1fs)",
            time.monotonic() - t0,
            exc_info=True,
        )
    finally:
        _TOOL_DEFS_DONE.set()


def wait_for_tool_definitions(timeout: float | None = None) -> bool:
    """Wait (bounded) for the startup tool-definitions prewarm (issue #8).

    Called right before constructing a fresh AIAgent so the SDK's
    get_tool_definitions memo hits instead of re-running every check_fn.
    No-op when no prewarm is active or the prewarm already finished.
    *timeout* defaults to ``HERMES_WEBUI_PREWARM_WAIT_S`` (10s); on expiry
    returns False and the caller proceeds with its own computation.
    """
    if not PREWARM_ACTIVE or _TOOL_DEFS_DONE.is_set():
        return True
    if timeout is None:
        try:
            timeout = float(os.environ.get("HERMES_WEBUI_PREWARM_WAIT_S", "10"))
        except ValueError:
            timeout = 10.0
    if timeout <= 0:
        return _TOOL_DEFS_DONE.is_set()
    return _TOOL_DEFS_DONE.wait(timeout)
