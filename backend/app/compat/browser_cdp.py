"""Global-receptionist-based CDP resolution for the Hermes SDK (runtime patch).

The SDK resolves the browser CDP endpoint from the process-global
``os.environ['BROWSER_CDP_URL']`` (``tools/browser_tool.py``:
``_get_cdp_override_raw``). LeapAide runs concurrent sessions in one
process, so that global must never carry a session-specific value.

Single source of truth: the global desktop state file written by
``app.api.global_desktop`` —
``$HERMES_WEBUI_STATE_DIR/browser_desktop.json``.  Its ``receptionist_url``
points at the in-process receptionist (the ONLY network CDP surface;
Chrome itself is on a parent-child pipe with no TCP port).  This module
replaces the SDK's override getter with one that:

1. returns the receptionist discovery URL
   (``<receptionist_url>/json/version?sid=<session_id>``) when the
   desktop state says the receptionist is active — the SDK fetches
   ``/json/version`` and receives a ``webSocketDebuggerUrl`` that embeds
   the session id, so every downstream connection stays attributable;
2. returns ``""`` when the desktop is down — browser tools fall through
   to local headless (the SDK's own fallback);
3. with no session context in the thread (prewarm, CLI-style), falls
   back to the SDK's original lookup (global env / ``browser.cdp_url``
   config).

Isolation is enforced by the receptionist itself (per-connection tab
scoping), not by hiding an endpoint: even a hand-written client that
dials the receptionist only ever sees its own tab.

No SDK file is modified: this is a runtime monkey-patch installed from
LeapAide-owned code (AGENTS.md forbids editing the SDK checkout).
"""

import logging
import threading

logger = logging.getLogger(__name__)

# Legacy sentinel ("resolve me from the session JSON") that earlier LeapAide
# versions wrote to the global env. Nothing writes it anymore; still
# translated to "" on the fallback path so a stale value in a long-lived
# shell never reads as a live endpoint.
CDP_URL_SENTINEL = "http://127.0.0.1:19999"

_install_lock = threading.Lock()
_installed = False


def _normalize(value: str) -> str:
    value = (value or "").strip()
    return "" if value == CDP_URL_SENTINEL else value


def _receptionist_cdp_url():
    """Return this session's CDP discovery URL, or None.

    None means "no session context in this thread" — callers fall back to
    the SDK's original lookup. An empty string is a real answer: the
    global browser desktop is down, which must read as "no CDP
    configured" so the browser tools use local headless.
    """
    from app.compat.env_overrides import env_override_ctx

    overrides = env_override_ctx.get() or {}
    session_key = (overrides.get("HERMES_SESSION_KEY") or "").strip()
    if not session_key:
        return None
    # webui format is agent:main:webui:dm:<session_id>; a bare session id
    # (routes.py sync path) also works with rsplit.
    session_id = session_key.rsplit(":", 1)[-1]
    if not session_id:
        return None

    try:
        from app.api.global_desktop import GlobalDesktop

        info = GlobalDesktop.get_info()
        if not info or info.get("status") != "active":
            return ""
        base = str(info.get("receptionist_url") or "").rstrip("/")
        if not base:
            return ""
        # The URL must end with /json/version so the SDK's discovery
        # (browser_tool._resolve_cdp_override) fetches it as-is and keeps
        # the sid query; the returned webSocketDebuggerUrl re-embeds it.
        return f"{base}/json/version?sid={session_id}"
    except Exception:
        # Unreadable state → treat as no desktop rather than guessing.
        logger.debug(
            "compat.browser_cdp: failed reading desktop state", exc_info=True,
        )
        return ""


def make_patched_getter(orig):
    """Build the receptionist-aware replacement for ``_get_cdp_override_raw``.

    Split out from :func:`install` so tests can exercise the logic against
    a stub ``orig`` without importing the heavy SDK module.
    """

    def _patched():
        session_url = _receptionist_cdp_url()
        if session_url is not None:
            return session_url
        # No session context (background thread / CLI-style): SDK
        # precedence, with the legacy sentinel never treated as live.
        return _normalize(orig())

    return _patched


def make_patched_camofox(orig):
    """Camofox must yield to the global browser desktop.

    ``browser_camofox.is_camofox_mode`` reads the global env directly
    (deliberately avoiding a browser_tool import), so the desktop-state
    signal never reaches it on its own. ``browser_tool`` also binds the
    function to a private alias at import time — patch both names.
    """

    def _patched():
        if (_receptionist_cdp_url() or ""):
            return False  # a CDP desktop is available — no camofox
        return orig()

    return _patched


def install() -> None:
    """Patch the SDK's CDP/camofox lookups (idempotent)."""
    global _installed
    with _install_lock:
        if _installed:
            return
        try:
            from tools import browser_camofox as _cf
            from tools import browser_tool as _bt
        except Exception:
            # SDK not importable in this process (e.g. unit tests without
            # the hermes checkout on sys.path) — nothing to patch.
            logger.debug(
                "compat.browser_cdp: SDK browser modules not importable; skip",
                exc_info=True,
            )
            return

        if not getattr(_bt, "_leap_aide_cdp_patched", False):
            _bt._get_cdp_override_raw = make_patched_getter(
                _bt._get_cdp_override_raw
            )
            # browser_tool binds is_camofox_mode to a private alias at
            # import time (module top level); keep it in agreement.
            if hasattr(_bt, "_is_camofox_mode"):
                _bt._is_camofox_mode = make_patched_camofox(
                    _bt._is_camofox_mode
                )
            _bt._leap_aide_cdp_patched = True

        if not getattr(_cf, "_leap_aide_cdp_patched", False):
            _cf.is_camofox_mode = make_patched_camofox(_cf.is_camofox_mode)
            _cf._leap_aide_cdp_patched = True

        _installed = True
        logger.info(
            "compat.browser_cdp: global-receptionist CDP resolution patch installed"
        )
