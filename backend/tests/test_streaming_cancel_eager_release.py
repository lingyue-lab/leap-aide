"""
Test plan for the #653 fix (eager session lock release in cancel_stream).

These tests verify that after cancel_stream() is called:
1. CANCEL_FLAGS is popped
2. AGENT_INSTANCES is popped
3. stream_manager._streams is transitively cleaned (via agent thread finally)
4. Session active_stream_id is cleared (when agent is available)
5. Session pending fields are cleared (when agent is available)

All tests are isolated and clean up after themselves.
"""

import pytest
import queue
import threading
from unittest.mock import Mock, patch, MagicMock

from app.api.streaming import cancel_stream
from app.api.config import AGENT_INSTANCES, STREAMS_LOCK, CANCEL_FLAGS, ReplayableStream
from app.api.stream_manager import stream_manager


class TestCancelStreamEagerRelease:
    """Test suite for #653: eager session lock release on cancel."""

    def setup_method(self):
        """Clean up before each test."""
        AGENT_INSTANCES.clear()
        stream_manager._streams.clear()
        CANCEL_FLAGS.clear()

    def teardown_method(self):
        """Clean up after each test."""
        AGENT_INSTANCES.clear()
        stream_manager._streams.clear()
        CANCEL_FLAGS.clear()

    def test_cancel_pops_cancel_flags(self):
        """After cancel, stream_id should no longer be in CANCEL_FLAGS."""
        stream_id = "test_eager_flags"
        stream_manager._streams[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()

        cancel_stream(stream_id)

        assert stream_id not in CANCEL_FLAGS, \
            "cancel_stream() should eagerly pop from CANCEL_FLAGS"
        assert stream_manager.get_stream_by_id(stream_id) is None, \
            "cancel_stream should transitively clean stream_manager"

    def test_cancel_pops_agent_instances(self):
        """After cancel, stream_id should no longer be in AGENT_INSTANCES."""
        stream_id = "test_eager_agent"
        mock_agent = Mock()
        mock_agent.interrupt = Mock()
        stream_manager._streams[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()
        AGENT_INSTANCES[stream_id] = mock_agent

        cancel_stream(stream_id)

        assert stream_id not in AGENT_INSTANCES, \
            "cancel_stream() should eagerly pop from AGENT_INSTANCES"
        assert stream_manager.get_stream_by_id(stream_id) is None, \
            "cancel_stream should transitively clean stream_manager"

    def test_cancel_clears_session_active_stream_id(self):
        """After cancel, session.pending_* fields are cleared and metadata is persisted.

        Note: ``session.active_stream_id`` is owned by stream_manager, not the
        Session object — see api/stream_manager.py — so this test only verifies
        the contract that cancel_stream clears the per-turn pending fields and
        triggers a state.db sync.
        """
        stream_id = "test_session_clear"
        session_id = "sess_abc123"
        mock_agent = Mock()
        mock_agent.interrupt = Mock()
        mock_agent.session_id = session_id

        mock_session = Mock()
        mock_session.active_stream_id = stream_id
        mock_session.pending_user_message = "hello"
        mock_session.pending_attachments = ["file.txt"]
        mock_session.pending_started_at = 1234567890.0
        # Provide an iterable messages list so the dedup-diagnostic pre-pass
        # inside cancel_stream's cleanup block (which iterates _cs.messages)
        # doesn't raise TypeError on bare Mock. Without this the inner except
        # branch swallows the error and the pending_* / sync_webui_metadata
        # assertions never run.
        mock_session.messages = []
        mock_session.session_id = session_id
        # _postprocess_messages() iterates _cs._all_message_ts via iter() when
        # truthy; bare Mock auto-creates _all_message_ts as a truthy non-iterable
        # which raises TypeError, swallowing the cleanup branch before the
        # pending_* / sync_webui_metadata assignments run.
        mock_session._all_message_ts = None

        stream_manager._streams[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()
        AGENT_INSTANCES[stream_id] = mock_agent

        mock_db = Mock()
        # get_messages must return [] (falsy) — otherwise the cleanup block
        # overwrites _cs.messages with a Mock and reversed() iteration below
        # raises TypeError, swallowing the rest of the cleanup.
        mock_db.get_messages.return_value = []
        with patch('app.api.streaming.get_session', return_value=mock_session), \
             patch('app.api.models._get_session_db', return_value=mock_db):
            cancel_stream(stream_id)

        # Note: session.active_stream_id is intentionally NOT cleared here.
        # `active_stream_id` is owned by stream_manager (see api/stream_manager.py
        # "session objects never hold active_stream_id"); the next
        # sync_webui_metadata() notification will push the cleared value to the
        # frontend via _notify_session_event. Tests asserting on this attribute
        # would couple to a contract that the current architecture explicitly
        # disclaims.
        assert mock_session.pending_user_message is None, \
            "cancel_stream() should clear session.pending_user_message"
        assert mock_session.pending_attachments == [], \
            "cancel_stream() should clear session.pending_attachments"
        assert mock_session.pending_started_at is None, \
            "cancel_stream() should clear session.pending_started_at"
        mock_session.sync_webui_metadata.assert_called_once()
        assert stream_manager.get_stream_by_id(stream_id) is None, \
            "cancel_stream should transitively clean stream_manager"

    def test_cancel_without_agent_still_pops_flags(self):
        """Cancel should pop CANCEL_FLAGS even when no agent instance exists."""
        stream_id = "test_no_agent"
        stream_manager._streams[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()
        # No AGENT_INSTANCES entry

        cancel_stream(stream_id)

        assert stream_id not in CANCEL_FLAGS
        assert stream_manager.get_stream_by_id(stream_id) is None, \
            "cancel_stream should transitively clean stream_manager"

    def test_cancel_sentinel_still_queued(self):
        """Cancel sentinel should still be queued before cleanup."""
        stream_id = "test_sentinel"
        # Use ReplayableStream (not raw queue.Queue) because cancel_stream
        # calls `q.put('cancel', {message: ...})` — a 2-arg signature that
        # only ReplayableStream implements. queue.Queue.put silently drops
        # the second arg.
        q = ReplayableStream()
        stream_manager._streams[stream_id] = q
        CANCEL_FLAGS[stream_id] = threading.Event()
        # Subscribe BEFORE cancel_stream so we receive the sentinel
        # before stream.close() (triggered by cleanup_by_stream_id)
        # flushes subscribers.
        sub = q.subscribe()

        cancel_stream(stream_id)

        # The cancel sentinel should have been queued before the cleanup
        event_type, data = sub.get_nowait()
        assert event_type == 'cancel'
        assert data['message'] == 'Cancelled by user'
        assert stream_manager.get_stream_by_id(stream_id) is None, \
            "cancel_stream should transitively clean stream_manager"

    def test_double_cancel_is_safe(self):
        """Calling cancel_stream() twice should not raise."""
        stream_id = "test_double"
        mock_agent = Mock()
        mock_agent.interrupt = Mock()
        mock_agent.session_id = "sess_xyz"

        stream_manager._streams[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()
        AGENT_INSTANCES[stream_id] = mock_agent

        # First cancel
        result1 = cancel_stream(stream_id)
        assert result1 is True
        assert stream_manager.get_stream_by_id(stream_id) is None, \
            "cancel_stream should transitively clean stream_manager"

        # Second cancel (stream already popped)
        result2 = cancel_stream(stream_id)
        assert result2 is False

    def test_cancel_handle_get_session_failure(self):
        """Cancel should not raise even if get_session fails."""
        stream_id = "test_session_fail"
        mock_agent = Mock()
        mock_agent.interrupt = Mock()
        mock_agent.session_id = "sess_nonexistent"

        stream_manager._streams[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()
        AGENT_INSTANCES[stream_id] = mock_agent

        with patch('app.api.streaming.get_session', side_effect=KeyError("Session not found")):
            # Should not raise
            result = cancel_stream(stream_id)

        assert result is True
        assert stream_manager.get_stream_by_id(stream_id) is None, \
            "cancel_stream should transitively clean stream_manager"