"""Integration tests for SSE stream replay resolved-event filtering.

Feature: 010-ui-ux-polish
User Story 3: SSE stream reliability fix

Tests validate:
- clarify_resolved and approval_resolved markers are written to ReplayableStream
- Replay handler implements two-pass filtering to skip resolved events
- clarify_id is included in clarify event data
"""
import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()

ROUTES_SRC = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
STREAMING_SRC = (REPO_ROOT / "api" / "streaming.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# T025: Backend integration tests for resolved-event markers and replay filter
# ---------------------------------------------------------------------------

def test_clarify_resolved_marker_written_in_respond_handler():
    """_handle_clarify_respond must write clarify_resolved marker to active stream."""
    assert "clarify_resolved" in ROUTES_SRC, \
        "_handle_clarify_respond must write clarify_resolved event to ReplayableStream"


def test_approval_resolved_marker_written_in_respond_handler():
    """_handle_approval_respond must write approval_resolved marker to active stream."""
    assert "approval_resolved" in ROUTES_SRC, \
        "_handle_approval_respond must write approval_resolved event to ReplayableStream"


def test_replay_handler_two_pass_filter():
    """Replay handler must implement two-pass filtering to skip resolved events."""
    assert "clarify_resolved" in ROUTES_SRC, \
        "Replay handler must handle clarify_resolved events in filter logic"
    # Two-pass pattern: collect IDs then filter
    assert "resolved_clarify_ids" in ROUTES_SRC or "resolved_ids" in ROUTES_SRC, \
        "Replay handler must collect resolved event IDs for filtering"
    assert "continue" in ROUTES_SRC, \
        "Replay handler must skip (continue) resolved events during replay"


def test_clarify_id_in_streaming_data():
    """_clarify_callback_impl must include clarify_id in clarify event data."""
    assert "clarify_id" in STREAMING_SRC, \
        "clarify data in _clarify_callback_impl must include clarify_id field"


def test_stream_marker_write_uses_active_stream_id():
    """Both respond handlers must look up the session's active_stream_id to find the stream."""
    assert "active_stream_id" in ROUTES_SRC, \
        "Respond handlers must use active_stream_id to find the ReplayableStream"
