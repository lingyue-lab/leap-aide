"""Session observer endpoints — noVNC over WebSocket.

SessionDisplay is now bound to session pin state:
    Pinned   → SessionDisplay runs → observer available
    Unpinned → no SessionDisplay   → observer blocked
"""

from __future__ import annotations

import logging
from pathlib import Path

from api.helpers import j, bad, t

logger = logging.getLogger(__name__)
_OBSERVER_HTML_PATH = Path(__file__).parent.parent / "static" / "observer.html"


def _is_session_pinned(session_id: str) -> bool:
    """Check whether a session is pinned."""
    try:
        from api.models import get_session
        s = get_session(session_id, metadata_only=True)
        return bool(getattr(s, 'pinned', False))
    except (KeyError, Exception):
        return False


# ── Endpoints ────────────────────────────────────────────────────────────────

def handle_observer_page(handler, session_id: str) -> bool:
    """Serve the observer HTML page. Requires pinned session."""
    if not _OBSERVER_HTML_PATH.exists():
        return bad(handler, "Observer page not found", 404)

    if not _is_session_pinned(session_id):
        return bad(handler, "请先 Pin 此会话以启用观察功能", 403)

    from api.session_display import SessionDisplay
    info = SessionDisplay.get_info(session_id)
    if info:
        vnc_port = info.get("vnc_port", 0)
    else:
        try:
            sd = SessionDisplay(session_id)
            r = sd.start()
            vnc_port = r.get("vnc_port", 0)
        except Exception as exc:
            logger.warning("Failed to start SessionDisplay for %s: %s", session_id, exc)
            vnc_port = 0

    try:
        html = _OBSERVER_HTML_PATH.read_text(encoding="utf-8")
        html = html.replace("__SESSION_ID__", session_id)
        html = html.replace("__VNC_PORT__", str(vnc_port))
        return t(handler, html, content_type="text/html; charset=utf-8")
    except Exception as exc:
        return bad(handler, "Internal error", 500)


def handle_observer_info(handler, session_id: str) -> bool:
    """Return SessionDisplay info. Requires pinned session."""
    if not _is_session_pinned(session_id):
        return j(handler, {"ok": False, "running": False,
                "error": "请先 Pin 此会话以启用观察功能"})

    from api.session_display import SessionDisplay
    info = SessionDisplay.get_info(session_id)
    if info:
        sd = SessionDisplay(session_id)
        if sd.is_running():
            return j(handler, {"ok": True, "running": True,
                    "vnc_port": info.get("vnc_port"),
                    "display_num": info.get("display"), "status": "active"})
    try:
        sd = SessionDisplay(session_id)
        r = sd.start()
        return j(handler, {"ok": True, "running": True,
                "vnc_port": r["vnc_port"], "display_num": r["display"],
                "cdp_port": r.get("cdp_port"), "status": "active"})
    except RuntimeError as exc:
        return j(handler, {"ok": False, "running": False, "error": str(exc)})
    except Exception as exc:
        return j(handler, {"ok": False, "running": False, "error": f"Cannot start: {exc}"})
