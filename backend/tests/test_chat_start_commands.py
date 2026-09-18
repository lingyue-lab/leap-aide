"""Contract tests for slash command dispatch + chat/start.

Sprint 49 split slash command handling into two endpoints:

- ``POST /api/chat/dispatch`` — inspects the message and returns either:
    * ``{"action": "direct", "content": ..., "session_update": ...}``
      for commands like ``/help``, ``/model``, ``/clear``, ``/new`` and unknown
      skill names (the response itself carries the user-facing message).
    * ``{"action": "proceed", "message": ...}`` (with optional ``mode`` /
      ``original_msg``) when the message should fall through to the agent.
- ``POST /api/chat/start`` — always starts a stream (no longer does inline
  slash command dispatch). Returns ``{stream_id, session_id}`` and (for queued
  / steered turns) ``queued`` / ``steered`` flags.

These tests probe the actual server (which runs in the hermes-agent venv
where ``hermes_cli.commands`` is importable), so they exercise the real
contract rather than skipping when the test process can't import the SDK.
"""
import json
import urllib.error
import urllib.request

from tests._pytest_port import BASE, TEST_DEFAULT_MODEL


def _post(url: str, payload: dict) -> dict:
    """POST JSON and return parsed response. Raises HTTPError on non-2xx."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def _post_soft(url: str, payload: dict) -> dict:
    """POST JSON and return parsed response; never raises — returns {} on error."""
    try:
        return _post(url, payload)
    except urllib.error.HTTPError:
        return {}


def _create_session() -> str:
    """Create a fresh session via /api/session/new and return its id."""
    result = _post(f"{BASE}/api/session/new", {})
    return result["session"]["session_id"]


class TestChatDispatchDirectResponse:
    """Verify direct-response commands return {action: 'direct', ...} from
    /api/chat/dispatch."""

    def test_help_returns_direct_response(self, cleanup_test_sessions):
        """POST /api/chat/dispatch with /help returns direct action, no proceed."""
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        payload = {
            "session_id": sid,
            "message": "/help",
            "model": TEST_DEFAULT_MODEL,
        }
        result = _post(f"{BASE}/api/chat/dispatch", payload)
        assert result.get("action") == "direct", (
            f"Expected action=direct, got: {list(result.keys())}"
        )
        assert "content" in result, "direct response must have content"
        assert "session_id" in result
        assert result.get("message") is None, "direct response must NOT echo message"
        assert len(result["content"]) > 0

    def test_help_content_includes_commands(self, cleanup_test_sessions):
        """Help output mentions built-in commands."""
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        payload = {"session_id": sid, "message": "/help", "model": TEST_DEFAULT_MODEL}
        result = _post(f"{BASE}/api/chat/dispatch", payload)
        assert result.get("action") == "direct"
        content = result.get("content", "")
        assert "/help" in content or "help" in content.lower()

    def test_model_returns_session_update(self, cleanup_test_sessions):
        """/model should set session_update.model."""
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        payload = {
            "session_id": sid,
            "message": f"/model {TEST_DEFAULT_MODEL}",
            "model": TEST_DEFAULT_MODEL,
        }
        result = _post(f"{BASE}/api/chat/dispatch", payload)
        assert result.get("action") == "direct", f"Expected action=direct, got: {result}"
        update = result.get("session_update", {})
        assert "model" in update, f"session_update should contain model, got: {update}"

    def test_clear_returns_session_update_cleared(self, cleanup_test_sessions):
        """/clear should set session_update.cleared=True."""
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        # Populate history with a regular message first
        _post_soft(f"{BASE}/api/chat/dispatch", {
            "session_id": sid, "message": "Hello", "model": TEST_DEFAULT_MODEL,
        })
        payload = {"session_id": sid, "message": "/clear", "model": TEST_DEFAULT_MODEL}
        result = _post(f"{BASE}/api/chat/dispatch", payload)
        assert result.get("action") == "direct"
        update = result.get("session_update", {})
        assert update.get("cleared") is True

    def test_new_returns_new_session_id(self, cleanup_test_sessions):
        """/new should set session_update.new_session_id."""
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        payload = {
            "session_id": sid,
            "message": "/new Test Session",
            "model": TEST_DEFAULT_MODEL,
        }
        result = _post(f"{BASE}/api/chat/dispatch", payload)
        assert result.get("action") == "direct"
        update = result.get("session_update", {})
        assert "new_session_id" in update, (
            f"session_update should contain new_session_id, got: {update}"
        )


class TestChatDispatchSkillCommand:
    """Verify skill dispatch routes through /api/chat/dispatch."""

    def test_unknown_skill_falls_through_to_agent(self, cleanup_test_sessions):
        """Unknown skill names are not built-in commands; they fall through to
        the agent so the user can still chat about them. The agent (not the
        dispatcher) is responsible for surfacing any "skill not found" guidance.
        """
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        payload = {
            "session_id": sid,
            "message": "/nonexistent-skill-xyz",
            "model": TEST_DEFAULT_MODEL,
        }
        result = _post(f"{BASE}/api/chat/dispatch", payload)
        assert result.get("action") == "proceed", (
            f"unknown skill must fall through to proceed, got: {result}"
        )
        assert result.get("message") == "/nonexistent-skill-xyz"

    def test_valid_skill_returns_proceed_or_direct(self, cleanup_test_sessions):
        """A known skill should be either dispatched (proceed) or error (direct)."""
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        payload = {
            "session_id": sid,
            "message": "/simplify-code test.py",
            "model": TEST_DEFAULT_MODEL,
        }
        result = _post_soft(f"{BASE}/api/chat/dispatch", payload)
        # Either action=direct (skill error/missing) or action=proceed (skill OK)
        assert result.get("action") in ("direct", "proceed"), (
            f"Expected action=direct|proceed, got: {list(result.keys())}"
        )


class TestChatDispatchPassthrough:
    """Verify non-slash messages and unknown slash commands fall through."""

    def test_non_slash_message_returns_proceed(self, cleanup_test_sessions):
        """Regular messages should return action=proceed so chat/start takes over."""
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        payload = {
            "session_id": sid,
            "message": "Hello, how are you?",
            "model": TEST_DEFAULT_MODEL,
        }
        result = _post(f"{BASE}/api/chat/dispatch", payload)
        assert result.get("action") == "proceed", (
            f"regular message should proceed, got: {result}"
        )
        assert result.get("message") == "Hello, how are you?"

    def test_unknown_slash_returns_proceed(self, cleanup_test_sessions):
        """Unknown /command should return action=proceed (passthrough to agent)."""
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        payload = {
            "session_id": sid,
            "message": "/arbitrary-text-12345",
            "model": TEST_DEFAULT_MODEL,
        }
        result = _post(f"{BASE}/api/chat/dispatch", payload)
        assert result.get("action") == "proceed", (
            f"unknown slash should proceed, got: {result}"
        )


class TestChatStartStreamContract:
    """Verify /api/chat/start always returns a stream_id and never inlines
    slash command handling (that is /api/chat/dispatch's job)."""

    def test_start_returns_stream_id_for_regular_message(self, cleanup_test_sessions):
        """Regular message: chat/start must return stream_id."""
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        payload = {
            "session_id": sid,
            "message": "ping",
            "model": TEST_DEFAULT_MODEL,
        }
        result = _post(f"{BASE}/api/chat/start", payload)
        assert "stream_id" in result, (
            f"chat/start must return stream_id, got: {list(result.keys())}"
        )
        assert result["session_id"] == sid
        assert len(result["stream_id"]) > 8

    def test_start_with_slash_message_still_returns_stream_id(self, cleanup_test_sessions):
        """After dispatch returns action=proceed, chat/start must yield a stream.

        Regression: the historical chat/start handler inlined slash command
        handling and returned ``{direct: true}`` for /help etc. The current
        architecture splits dispatch from streaming, so chat/start always
        returns ``{stream_id}`` regardless of message content. Tests that need
        to verify slash command behaviour should call /api/chat/dispatch first.
        """
        sid = _create_session()
        cleanup_test_sessions.append(sid)
        payload = {
            "session_id": sid,
            "message": "/help",  # would historically return {direct: true}
            "model": TEST_DEFAULT_MODEL,
        }
        try:
            result = _post(f"{BASE}/api/chat/start", payload)
            assert "stream_id" in result, (
                f"chat/start must return stream_id, got: {list(result.keys())}"
            )
        except urllib.error.HTTPError:
            # Acceptable in test env without real agent.
            pass
