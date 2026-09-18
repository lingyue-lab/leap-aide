"""
Contract tests for the welcome-state first send.

When the frontend is in welcome state (placeholder session), the first send
posts ``/api/chat/start`` with a session_id the backend has never seen.
Before the refactor this raised 404. After the refactor it auto-creates a
real session (state.db row + SESSIONS entry) and returns the real id so
the frontend can swap its placeholder for the real Session.

These tests probe the actual server (which runs in the hermes-agent venv
where ``api.routes`` is importable), so they exercise the real contract
rather than skipping when the test process can't import the SDK.
"""

import json
import re
import urllib.error
import urllib.request

from tests._pytest_port import BASE


def _post(url: str, payload: dict) -> dict:
    """POST JSON and return parsed response. Raises HTTPError on non-2xx."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def test_chat_start_creates_session_when_missing():
    """Welcome-state first send: backend auto-creates instead of returning 404.

    The placeholder sid is arbitrary — the backend mints its own real id
    (see api/routes.py:_handle_chat_start auto-create branch) so state.db
    and SESSIONS stay consistent without an in-memory rename.
    """
    placeholder_sid = "new-local-not-a-real-id"
    payload = {
        "session_id": placeholder_sid,
        "message": "hello",
        "model": "anthropic/claude-sonnet-4-5",
    }
    result = _post(f"{BASE}/api/chat/start", payload)
    # Backend minted its own 12-hex id, not the placeholder.
    assert result["session_id"] != placeholder_sid
    assert re.fullmatch(r"[0-9a-fA-F]{12}", result["session_id"])
    assert "stream_id" in result
    assert len(result["stream_id"]) > 8


def test_chat_start_creates_session_when_id_format_invalid():
    """Any arbitrary session_id string falls into the auto-create branch."""
    payload = {
        "session_id": "literally-anything-not-a-uuid",
        "message": "hello",
        "model": "anthropic/claude-sonnet-4-5",
    }
    result = _post(f"{BASE}/api/chat/start", payload)
    assert result["session_id"] != "literally-anything-not-a-uuid"
    assert re.fullmatch(r"[0-9a-fA-F]{12}", result["session_id"])
    assert "stream_id" in result
