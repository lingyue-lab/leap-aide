"""Global browser-desktop status endpoint (noVNC panel backend).

The frontend sidebar panel polls this to render the status chip and to
obtain the fixed VNC WebSocket target. The desktop itself is always-on
(started at boot by ``app.api.global_desktop``) — there is no per-session
pin gating anymore (issue #37).
"""

from __future__ import annotations

import logging

from app.api.helpers import j

logger = logging.getLogger(__name__)


def handle_browser_desktop_info(handler) -> bool:
    """GET /api/browser-desktop — status of the shared desktop."""
    from app.api.global_desktop import get_global_desktop, DESKTOP_ENABLED

    if not DESKTOP_ENABLED:
        return j(handler, {
            "ok": False, "running": False, "enabled": False,
            "status": "disabled",
            "error": "Global browser desktop disabled (HERMES_GLOBAL_DESKTOP=0)",
        })

    gd = get_global_desktop()
    info = gd.info_dict()
    running = gd.is_running()
    chrome_alive = False
    if running:
        try:
            from app.api import browser_receptionist

            r = browser_receptionist.get_receptionist()
            chrome_alive = bool(r and r.is_chrome_alive())
        except Exception:
            chrome_alive = False
    return j(handler, {
        "ok": running and info.get("status") == "active",
        "running": running,
        "enabled": True,
        "chrome_alive": chrome_alive,
        "vnc_port": info.get("vnc_port"),
        "display_num": info.get("display"),
        "receptionist_port": info.get("receptionist_port"),
        "receptionist_url": info.get("receptionist_url"),
        "status": info.get("status"),
    })
