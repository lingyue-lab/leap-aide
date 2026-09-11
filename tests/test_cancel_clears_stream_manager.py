"""
Tests for the cancel_stream → stream_manager._streams cleanup chain.

cancel_stream() itself doesn't pop stream_manager._streams — it relies on
the agent thread's `finally` block to call stream_manager.end_stream(),
which clears _streams and _session_streams. cancel_stream() waits up to
10 s (Step 4) for AGENT_INSTANCES to be popped as a proxy for that
thread-exit signal. These tests assert the end-to-end contract: after
cancel_stream() returns True, the stream must be gone from
stream_manager._streams.
"""
import queue
import threading
from unittest.mock import Mock

from api.streaming import cancel_stream
from api.config import AGENT_INSTANCES, CANCEL_FLAGS
from api.stream_manager import stream_manager


class TestCancelClearsStreamManager:
    """Verify cancel_stream transitively cleans stream_manager._streams."""

    def setup_method(self):
        AGENT_INSTANCES.clear()
        stream_manager._streams.clear()
        CANCEL_FLAGS.clear()

    def teardown_method(self):
        AGENT_INSTANCES.clear()
        stream_manager._streams.clear()
        CANCEL_FLAGS.clear()

    def test_cancel_clears_stream_manager_after_agent_thread_exits(self):
        """cancel_stream with an agent registered cleans stream_manager._streams."""
        stream_id = "test_clear_full"
        mock_agent = Mock()
        mock_agent.interrupt = Mock()

        stream_manager._streams[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()
        AGENT_INSTANCES[stream_id] = mock_agent

        result = cancel_stream(stream_id)

        assert result is True
        mock_agent.interrupt.assert_called_once_with("Cancelled by user")
        assert stream_manager.get_stream_by_id(stream_id) is None, \
            "cancel_stream should transitively clean stream_manager._streams"

    def test_cancel_with_no_agent_still_clears_stream_manager(self):
        """Race condition: no agent yet but stream is in stream_manager — still cleared."""
        stream_id = "test_clear_no_agent"
        # Only register in stream_manager; no AGENT_INSTANCES entry.
        stream_manager._streams[stream_id] = queue.Queue()
        CANCEL_FLAGS[stream_id] = threading.Event()

        result = cancel_stream(stream_id)

        assert result is True
        assert stream_manager.get_stream_by_id(stream_id) is None, \
            "cancel_stream should clean stream_manager._streams even without agent"

    def test_active_count_decrements_after_cancel(self):
        """active_count() reflects the cleanup: 2 → 1 after one cancel."""
        sid_a = "test_active_a"
        sid_b = "test_active_b"
        # Register two streams; cancel one; expect count to drop to 1.
        stream_manager._streams[sid_a] = queue.Queue()
        stream_manager._streams[sid_b] = queue.Queue()
        CANCEL_FLAGS[sid_a] = threading.Event()
        CANCEL_FLAGS[sid_b] = threading.Event()

        assert stream_manager.active_count() == 2

        result = cancel_stream(sid_a)
        assert result is True

        assert stream_manager.active_count() == 1, \
            "active_count should reflect the cancelled stream being removed"
        # The other stream should remain.
        assert stream_manager.get_stream_by_id(sid_b) is not None