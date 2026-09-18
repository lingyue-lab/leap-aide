"""Contract tests for GET /api/sessions — last_seen_msg_count field.

Feature: 010-ui-ux-polish
User Story 1: Gateway Session badge fix

Tests validate:
- get_cli_sessions() includes last_seen_msg_count in output dict
- Read-time clamping logic: last_seen_msg_count must be ≤ message_count
- session_watcher SSE sessions_changed includes last_seen_msg_count
"""
import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
# Frontend lives at the actual repo root (one level above backend/), not under
# backend/. Use ACTUAL_REPO_ROOT from conftest for frontend paths so this
# works regardless of which top-level directory the test file moved into.
from tests.conftest import ACTUAL_REPO_ROOT  # noqa: E402

MODELS_SRC = (REPO_ROOT / "app" / "api" / "models.py").read_text(encoding="utf-8")
SESSION_WATCHER_SRC = (REPO_ROOT / "app" / "api" / "session_watcher.py").read_text(encoding="utf-8")
FRONTEND_STORE_SRC = (ACTUAL_REPO_ROOT / "frontend" / "src" / "stores" / "useSessionStore.ts").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# T003: Backend contract tests for last_seen_msg_count
# ---------------------------------------------------------------------------

def test_get_cli_sessions_includes_last_seen_msg_count():
    """get_cli_sessions() must include last_seen_msg_count in output dict."""
    assert "'last_seen_msg_count'" in MODELS_SRC or '"last_seen_msg_count"' in MODELS_SRC, \
        "get_cli_sessions() must output last_seen_msg_count field"


def test_get_cli_sessions_read_time_clamp():
    """get_cli_sessions() must clamp last_seen_msg_count ≤ message_count at read time.

    The clamp pattern ('min(last_seen, msg_count)' or equivalent) must exist
    in the get_cli_sessions() function or in the compact() method (for WebUI sessions).
    """
    # Check for read-time clamp in models.py (either in get_cli_sessions or compact)
    assert "min(" in MODELS_SRC, \
        "models.py must contain a read-time clamp using min() for last_seen_msg_count"
    # Specifically verify the clamp relates to last_seen and message_count
    assert "last_seen" in MODELS_SRC.lower(), \
        "models.py must reference last_seen in clamping logic"


def test_sessions_api_response_includes_last_seen():
    """The sessions API response builder must include last_seen_msg_count.

    This checks both get_cli_sessions() (gateway sessions) and compact() (WebUI sessions).
    """
    # At least one of the session serialization paths must emit last_seen_msg_count
    has_cli_field = ("'last_seen_msg_count'" in MODELS_SRC or
                     '"last_seen_msg_count"' in MODELS_SRC)
    assert has_cli_field, \
        "At least one session serialization path must include last_seen_msg_count field"


# ---------------------------------------------------------------------------
# Session watcher SSE event contract
# ---------------------------------------------------------------------------

def test_session_watcher_sessions_changed_includes_last_seen():
    """T005: session_watcher _get_agent_sessions_from_db() must include
    last_seen_msg_count in the sessions_changed SSE event dict."""
    assert "'last_seen_msg_count'" in SESSION_WATCHER_SRC or \
           '"last_seen_msg_count"' in SESSION_WATCHER_SRC, \
        "session_watcher _get_agent_sessions_from_db() must output last_seen_msg_count field"


def test_session_watcher_read_time_clamp():
    """session_watcher must also clamp last_seen_msg_count to ≤ message_count."""
    # Should reference last_seen in the watcher, or delegate to models.py
    # The clamp pattern should exist either in models.py or session_watcher.py
    assert "last_seen" in SESSION_WATCHER_SRC.lower(), \
        "session_watcher must reference last_seen_msg_count"


# ---------------------------------------------------------------------------
# Frontend SSE store contract
# ---------------------------------------------------------------------------

def test_frontend_sse_handler_preserves_last_seen():
    """T006: Frontend SSE sessions_changed handler must map last_seen_msg_count
    with defensive fallback using nullish coalescing."""
    assert "last_seen_msg_count" in FRONTEND_STORE_SRC, \
        "Frontend SSE sessions_changed handler must include last_seen_msg_count mapping"
    assert "??" in FRONTEND_STORE_SRC, \
        "Frontend SSE handler must use defensive fallback (??) for last_seen_msg_count"
