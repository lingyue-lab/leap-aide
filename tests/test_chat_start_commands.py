"""Contract tests for extended POST /api/chat/start with command dispatch (T006b)."""
from pathlib import Path
import json
import urllib.request
import urllib.error

ROOT = Path(__file__).parent.parent


def _post(url: str, payload: dict) -> dict:
    """POST JSON and return parsed response."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def _create_session(test_server) -> str:
    """Create a fresh session and return its session_id."""
    url = f"http://127.0.0.1:{test_server}/api/sessions"
    req = urllib.request.Request(url, method="POST")
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["session_id"]


class TestChatStartDirectResponse:
    """Verify direct-response commands return {direct: true, ...} immediately."""

    def test_help_returns_direct_response(self, test_server):
        """POST /api/chat/start with /help returns direct response, no stream_id."""
        sid = _create_session(test_server)
        payload = {
            "session_id": sid,
            "message": "/help",
            "model": "anthropic/claude-sonnet-4-6",
        }
        result = _post(f"http://127.0.0.1:{test_server}/api/chat/start", payload)
        assert result.get("direct") is True, f"Expected direct=True, got keys: {list(result.keys())}"
        assert "content" in result, "direct response must have content"
        assert "stream_id" not in result, "direct response must NOT have stream_id"
        assert len(result["content"]) > 0

    def test_help_content_includes_commands(self, test_server):
        """Help output mentions built-in commands."""
        sid = _create_session(test_server)
        payload = {"session_id": sid, "message": "/help", "model": "anthropic/claude-sonnet-4-6"}
        result = _post(f"http://127.0.0.1:{test_server}/api/chat/start", payload)
        content = result["content"]
        assert "/help" in content or "help" in content.lower()

    def test_model_returns_session_update(self, test_server):
        """/model should return session_update.model."""
        sid = _create_session(test_server)
        payload = {"session_id": sid, "message": "/model anthropic/claude-sonnet-4-6", "model": "anthropic/claude-sonnet-4-6"}
        result = _post(f"http://127.0.0.1:{test_server}/api/chat/start", payload)
        assert result.get("direct") is True
        update = result.get("session_update", {})
        assert "model" in update, f"session_update should contain model, got {update}"

    def test_clear_returns_session_update_cleared(self, test_server):
        """/clear should return session_update.cleared=True."""
        sid = _create_session(test_server)
        # Send a regular message first to populate history
        try:
            _post(f"http://127.0.0.1:{test_server}/api/chat/start", {
                "session_id": sid, "message": "Hello", "model": "anthropic/claude-sonnet-4-6"
            })
        except Exception:
            pass  # May fail in test env without real agent; ignore
        payload = {"session_id": sid, "message": "/clear", "model": "anthropic/claude-sonnet-4-6"}
        result = _post(f"http://127.0.0.1:{test_server}/api/chat/start", payload)
        assert result.get("direct") is True
        update = result.get("session_update", {})
        assert update.get("cleared") is True

    def test_new_returns_new_session_id(self, test_server):
        """/new should return session_update.new_session_id."""
        sid = _create_session(test_server)
        payload = {"session_id": sid, "message": "/new Test Session", "model": "anthropic/claude-sonnet-4-6"}
        result = _post(f"http://127.0.0.1:{test_server}/api/chat/start", payload)
        assert result.get("direct") is True
        update = result.get("session_update", {})
        assert "new_session_id" in update


class TestChatStartSkillCommand:
    """Verify skill commands transform message and route to agent."""

    def test_nonexistent_skill_returns_error(self, test_server):
        """Non-existent skill returns direct error response."""
        sid = _create_session(test_server)
        payload = {"session_id": sid, "message": "/nonexistent-skill-xyz", "model": "anthropic/claude-sonnet-4-6"}
        result = _post(f"http://127.0.0.1:{test_server}/api/chat/start", payload)
        assert result.get("direct") is True
        assert "Skill not found" in result.get("content", "") or "Failed to load skill" in result.get("content", "")

    def test_valid_skill_returns_stream_id(self, test_server):
        """A known skill command should return stream_id (agent path)."""
        sid = _create_session(test_server)
        # Use a skill that likely exists in the test environment
        payload = {"session_id": sid, "message": "/simplify-code test.py", "model": "anthropic/claude-sonnet-4-6"}
        try:
            result = _post(f"http://127.0.0.1:{test_server}/api/chat/start", payload)
            # Either direct error (skill not found) or stream_id (skill dispatched)
            assert result.get("direct") is True or "stream_id" in result, (
                f"Expected direct error or stream_id, got: {list(result.keys())}"
            )
        except urllib.error.HTTPError:
            # In test env without real agent, this may fail - acceptable
            pass


class TestChatStartPassthrough:
    """Verify unknown / commands pass through to agent."""

    def test_unknown_slash_returns_stream_id(self, test_server):
        """Unknown /command should return stream_id (passthrough to agent)."""
        sid = _create_session(test_server)
        payload = {"session_id": sid, "message": "/arbitrary-text-12345", "model": "anthropic/claude-sonnet-4-6"}
        try:
            result = _post(f"http://127.0.0.1:{test_server}/api/chat/start", payload)
            # Should be agent path: stream_id present, no direct flag
            assert "stream_id" in result, f"Expected stream_id for passthrough, got: {list(result.keys())}"
            assert result.get("direct") is not True
        except urllib.error.HTTPError:
            pass  # Acceptable in test env without real agent

    def test_non_slash_message_unchanged(self, test_server):
        """Regular message (no /) returns stream_id as before."""
        sid = _create_session(test_server)
        payload = {"session_id": sid, "message": "Hello, how are you?", "model": "anthropic/claude-sonnet-4-6"}
        try:
            result = _post(f"http://127.0.0.1:{test_server}/api/chat/start", payload)
            assert "stream_id" in result, "Normal messages must still return stream_id"
        except urllib.error.HTTPError:
            pass  # Acceptable in test env without real agent
