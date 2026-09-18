"""
Tests for the profile-switch guard that blocks while an agent is running.

Previously the guard was `len(STREAMS) > 0` against the legacy STREAMS
dict (dead code).  After STREAMS was deleted, the guard now reads
`stream_manager.active_count()` from the authoritative stream_manager.
These tests pin down the equivalence: the guard fires iff there is an
active stream in stream_manager, and stops firing as soon as cancel
cleans stream_manager._streams.
"""
import queue
import threading
from unittest.mock import Mock

import pytest

from app.api.profiles import switch_profile
from app.api.streaming import cancel_stream
from app.api.config import AGENT_INSTANCES, CANCEL_FLAGS
from app.api.stream_manager import stream_manager


class TestProfileSwitchBlocksActiveStream:
    """Guard fires when stream_manager has active streams."""

    def setup_method(self):
        AGENT_INSTANCES.clear()
        stream_manager._streams.clear()
        CANCEL_FLAGS.clear()

    def teardown_method(self):
        AGENT_INSTANCES.clear()
        stream_manager._streams.clear()
        CANCEL_FLAGS.clear()

    def test_switch_profile_raises_when_active_stream_exists(self):
        """switch_profile must raise RuntimeError when stream_manager has a stream."""
        sid = "test_guard_blocked"
        stream_manager._streams[sid] = queue.Queue()
        CANCEL_FLAGS[sid] = threading.Event()
        AGENT_INSTANCES[sid] = Mock(interrupt=Mock())

        with pytest.raises(RuntimeError, match="agent is running"):
            switch_profile("any-profile-name-doesnt-matter")

    def test_switch_profile_does_not_raise_guard_when_no_stream(self):
        """With stream_manager empty, switch_profile should NOT raise the
        'agent is running' guard. It may raise ValueError for a bogus
        profile name (because switch_profile proceeds past the guard and
        tries to resolve the profile directory); that's fine — the test
        only cares that the guard isn't what blocked it."""
        with pytest.raises(Exception) as exc_info:
            switch_profile("definitely-not-a-real-profile")
        # RuntimeError = guard fired. Anything else = guard passed.
        assert not isinstance(exc_info.value, RuntimeError), (
            "Guard should not fire when stream_manager has no active streams; "
            f"got RuntimeError instead: {exc_info.value}"
        )

    def test_switch_profile_succeeds_guard_after_cancel_clears_streams(self):
        """After cancel_stream cleans stream_manager._streams, the guard
        should let switch_profile through (still may raise ValueError for
        invalid profile name; the test asserts it is NOT RuntimeError)."""
        sid = "test_cancel_then_switch"
        stream_manager._streams[sid] = queue.Queue()
        CANCEL_FLAGS[sid] = threading.Event()
        AGENT_INSTANCES[sid] = Mock(interrupt=Mock())

        # Confirm guard fires before cancel.
        with pytest.raises(RuntimeError, match="agent is running"):
            switch_profile("any-profile")

        # Cancel the stream — stream_manager._streams should empty out.
        cancel_result = cancel_stream(sid)
        assert cancel_result is True
        assert stream_manager.active_count() == 0, \
            "cancel_stream should empty stream_manager._streams before guard is re-checked"

        # After cancel, switch_profile should NOT raise the guard.
        with pytest.raises(Exception) as exc_info:
            switch_profile("any-profile")
        assert not isinstance(exc_info.value, RuntimeError), (
            "Guard should not fire after cancel_stream cleaned stream_manager; "
            f"got RuntimeError instead: {exc_info.value}"
        )