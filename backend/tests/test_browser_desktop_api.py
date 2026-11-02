"""Tests for GET /api/browser-desktop (browser_desktop panel backend).

Uses a stub handler that captures the JSON body `j()` would send — no
server needed.
"""

import json

import pytest

from app.api import browser_desktop as bd
from app.api import global_desktop as gd_mod


class _FakeHandler:
    """Minimal BaseHTTPRequestHandler stand-in capturing one response."""

    def __init__(self):
        self.status = None
        self.headers = {}
        self.body = b""
        self.wfile = _CaptureStream(self)

    def send_response(self, status):
        self.status = status

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass

    @property
    def captured(self):
        return json.loads(self.body.decode("utf-8"))


class _CaptureStream:
    def __init__(self, handler):
        self._handler = handler

    def write(self, data):
        self._handler.body += data

    def flush(self):
        pass


@pytest.fixture
def handler():
    return _FakeHandler()


def test_disabled_desktop_reports_disabled(handler, monkeypatch):
    """HERMES_GLOBAL_DESKTOP=0 → enabled:false, status:disabled."""
    monkeypatch.setattr(gd_mod, "DESKTOP_ENABLED", False)
    bd.handle_browser_desktop_info(handler)
    payload = handler.captured
    assert payload["ok"] is False
    assert payload["enabled"] is False
    assert payload["running"] is False
    assert payload["status"] == "disabled"


def test_running_active_desktop(handler, monkeypatch):
    """Desktop up + receptionist healthy → ok/active with fixed ports."""
    monkeypatch.setattr(gd_mod, "DESKTOP_ENABLED", True)

    gd = gd_mod.GlobalDesktop()
    monkeypatch.setattr(gd_mod, "get_global_desktop", lambda: gd)
    monkeypatch.setattr(gd, "is_running", lambda: True)
    monkeypatch.setattr(
        gd, "info_dict",
        lambda: {
            "display": 99,
            "vnc_port": 5900,
            "receptionist_port": 6900,
            "receptionist_url": "http://127.0.0.1:6900",
            "status": "active",
        },
    )
    monkeypatch.setattr(gd, "_receptionist_healthy", lambda: True)

    bd.handle_browser_desktop_info(handler)
    payload = handler.captured
    assert payload["ok"] is True
    assert payload["enabled"] is True
    assert payload["running"] is True
    assert payload["status"] == "active"
    assert payload["vnc_port"] == 5900
    assert payload["display_num"] == 99
    assert payload["receptionist_port"] == 6900
    assert payload["receptionist_url"] == "http://127.0.0.1:6900"


def test_degraded_desktop_not_ok(handler, monkeypatch):
    """Receptionist down → running but status degraded, ok False."""
    monkeypatch.setattr(gd_mod, "DESKTOP_ENABLED", True)

    gd = gd_mod.GlobalDesktop()
    monkeypatch.setattr(gd_mod, "get_global_desktop", lambda: gd)
    monkeypatch.setattr(gd, "is_running", lambda: True)
    monkeypatch.setattr(
        gd, "info_dict",
        lambda: {
            "display": 99,
            "vnc_port": 5900,
            "receptionist_port": 6900,
            "receptionist_url": "http://127.0.0.1:6900",
            "status": "degraded",
        },
    )
    monkeypatch.setattr(gd, "_receptionist_healthy", lambda: False)

    bd.handle_browser_desktop_info(handler)
    payload = handler.captured
    assert payload["ok"] is False
    assert payload["running"] is True
    assert payload["status"] == "degraded"
    assert payload["chrome_alive"] is False


def test_stopped_desktop(handler, monkeypatch):
    """Desktop not running → ok False, chrome_alive False."""
    monkeypatch.setattr(gd_mod, "DESKTOP_ENABLED", True)

    gd = gd_mod.GlobalDesktop()
    monkeypatch.setattr(gd_mod, "get_global_desktop", lambda: gd)
    monkeypatch.setattr(gd, "is_running", lambda: False)
    monkeypatch.setattr(
        gd, "info_dict",
        lambda: {
            "display": 99,
            "vnc_port": 5900,
            "receptionist_port": 6900,
            "receptionist_url": "http://127.0.0.1:6900",
            "status": "error",
        },
    )

    bd.handle_browser_desktop_info(handler)
    payload = handler.captured
    assert payload["ok"] is False
    assert payload["running"] is False
    assert payload["chrome_alive"] is False
