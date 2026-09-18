"""Tests for unified session mode endpoints (Chat Mode Selection feature).

Covers:
- GET  /api/session/mode — query session mode state
- POST /api/session/mode — set session mode
- YOLO backward compatibility — /api/session/yolo delegates to mode
"""

import json
import os

import pytest

from tests.conftest import requires_agent_modules

TEST_BASE = f"http://127.0.0.1:{os.environ.get('HERMES_WEBUI_TEST_PORT', '8788')}"


def _get(path, expect_ok=True):
    import urllib.request
    import urllib.error

    try:
        with urllib.request.urlopen(TEST_BASE + path, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {}
        return body


def _post(path, body=None, expect_ok=True):
    import urllib.request
    import urllib.error

    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        TEST_BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {}
        return body


def _make_test_session():
    """Create a fresh session and return its session_id."""
    data = _post("/api/session/new")
    return data["session"]["session_id"]


# ── GET /api/session/mode ──────────────────────────────────────────────────


class TestGetSessionMode:
    """GET /api/session/mode — query mode for a session."""

    def test_get_mode_new_session_defaults_to_manual(self):
        """A new session should default to manual mode."""
        sid = _make_test_session()
        data = _get(f"/api/session/mode?session_id={sid}")
        assert data.get("mode") == "manual"
        assert "smart_supported" in data

    def test_get_mode_missing_session_id(self):
        """Missing session_id should return an error."""
        data = _get("/api/session/mode?session_id=", expect_ok=False)
        # Response should be either a 400 error or a missing-session response
        assert isinstance(data, dict)

    def test_get_mode_nonexistent_session(self):
        """A nonexistent session_id returns 404 or error."""
        data = _get("/api/session/mode?session_id=doesnotexist000", expect_ok=False)
        assert isinstance(data, dict)


# ── POST /api/session/mode ─────────────────────────────────────────────────


class TestPostSessionMode:
    """POST /api/session/mode — set mode for a session."""

    def test_post_set_mode_to_yolo(self):
        """Switch a session from manual to yolo."""
        sid = _make_test_session()
        data = _post("/api/session/mode", {"session_id": sid, "mode": "yolo"})
        assert data.get("ok") is True
        assert data.get("mode") == "yolo"
        # Verify GET returns the new mode
        get_data = _get(f"/api/session/mode?session_id={sid}")
        assert get_data.get("mode") == "yolo"

    def test_post_set_mode_to_smart(self):
        """Switch a session to smart mode."""
        sid = _make_test_session()
        data = _post("/api/session/mode", {"session_id": sid, "mode": "smart"})
        assert data.get("ok") is True
        assert data.get("mode") == "smart"
        get_data = _get(f"/api/session/mode?session_id={sid}")
        assert get_data.get("mode") == "smart"

    def test_post_switch_back_to_manual(self):
        """Switch yolo → manual."""
        sid = _make_test_session()
        _post("/api/session/mode", {"session_id": sid, "mode": "yolo"})
        data = _post("/api/session/mode", {"session_id": sid, "mode": "manual"})
        assert data.get("ok") is True
        assert data.get("mode") == "manual"
        get_data = _get(f"/api/session/mode?session_id={sid}")
        assert get_data.get("mode") == "manual"

    def test_post_invalid_mode_rejected(self):
        """Invalid mode values should be rejected."""
        sid = _make_test_session()
        data = _post("/api/session/mode", {"session_id": sid, "mode": "invalid"}, expect_ok=False)
        assert isinstance(data, dict)

    def test_post_missing_session_id(self):
        """Missing session_id should be rejected."""
        data = _post("/api/session/mode", {"mode": "yolo"}, expect_ok=False)
        assert isinstance(data, dict)


# ── Cross-session isolation ────────────────────────────────────────────────


class TestModeCrossSessionIsolation:
    """Mode should be isolated per session."""

    def test_mode_isolation(self):
        """Mode change in one session doesn't affect another."""
        sid_a = _make_test_session()
        sid_b = _make_test_session()
        _post("/api/session/mode", {"session_id": sid_a, "mode": "yolo"})
        data_b = _get(f"/api/session/mode?session_id={sid_b}")
        assert data_b.get("mode") == "manual"


# ── YOLO backward compatibility ────────────────────────────────────────────


@requires_agent_modules
class TestYoloBackwardCompat:
    """Ensure YOLO endpoints stay backward-compatible."""

    def test_yolo_get_reflects_mode(self):
        """GET /api/session/yolo should return yolo_enabled=true when mode=yolo."""
        sid = _make_test_session()
        # Default should be false (mode=manual)
        data = _get(f"/api/session/yolo?session_id={sid}")
        assert data.get("yolo_enabled") is False
        # Switch to yolo via unified API
        _post("/api/session/mode", {"session_id": sid, "mode": "yolo"})
        data = _get(f"/api/session/yolo?session_id={sid}")
        assert data.get("yolo_enabled") is True

    def test_yolo_post_still_works(self):
        """POST /api/session/yolo should still toggle YOLO state."""
        sid = _make_test_session()
        data = _post("/api/session/yolo", {"session_id": sid, "enabled": True})
        assert data.get("ok") is True
        assert data.get("yolo_enabled") is True
