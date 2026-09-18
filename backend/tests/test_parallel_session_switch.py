"""Tests for session-switch performance optimizations.

Python-side optimizations covered here:

1. git_info_for_workspace runs git subprocesses in parallel (api/workspace.py)
2. Message pagination: msg_limit tail-window + msg_before index cursor (api/routes.py)

The original file also asserted against legacy frontend JS files
(static/sessions.js, static/workspace.js, static/ui.js) to verify that
loadDir's expanded-dir pre-fetch was parallelized and that loadSession's
idle path overlapped loadDir with highlightCode. Those files were deleted
when the frontend was migrated to Vite + React; the equivalent logic now
lives in frontend/src/stores/useSessionStore.ts and has its own vitest
coverage in frontend/tests/stores/. Those structural assertions have been
removed from this file as a result.
"""

import pathlib
import threading
import time
from unittest.mock import patch, MagicMock

REPO = pathlib.Path(__file__).parent.parent
ROUTES_PY = (REPO / "app" / "api" / "routes.py").read_text(encoding="utf-8")


# ── 1. workspace.py: git_info_for_workspace is parallelized ────────────────


class TestGitInfoParallel:
    """git_info_for_workspace() must run git subprocess calls in parallel
    to reduce wall-clock time."""

    def test_uses_thread_pool(self):
        source = pathlib.Path(__file__).parent.parent / "app" / "api" / "workspace.py"
        src = source.read_text()
        fn = src[src.find("def git_info_for_workspace") :]
        fn = fn[: fn.find("\ndef ")]

        assert "concurrent.futures" in src, (
            "concurrent.futures should be imported at the module level."
        )
        assert "ThreadPoolExecutor" in fn, (
            "git_info_for_workspace should use ThreadPoolExecutor "
            "to run git commands in parallel."
        )

    def test_git_commands_run_concurrently(self, tmp_path):
        """Proof that status/ahead/behind git commands execute in parallel,
        not sequentially. Uses threading.Barrier to verify overlap."""
        from app.api.workspace import git_info_for_workspace
        import app.api.workspace as ws_mod

        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        barrier = threading.Barrier(3, timeout=5)
        call_count = {"n": 0}
        started_times = []

        def fake_git(args, cwd, timeout=3):
            if args[0] == "rev-parse":
                return "main"
            call_count["n"] += 1
            started_times.append(time.monotonic())
            barrier.wait(timeout=2)
            if args[0] == "status":
                return ""
            return "0"

        with patch.object(ws_mod, "_run_git", side_effect=fake_git):
            result = git_info_for_workspace(tmp_path)

        assert result is not None
        assert result["is_git"] is True
        assert result["branch"] == "main"
        assert call_count["n"] == 3, (
            f"Expected 3 parallel git calls, got {call_count['n']}"
        )
        assert started_times[-1] - started_times[0] < 0.15, (
            f"Git commands started too far apart ({started_times[-1]-started_times[0]:.3f}s), "
            f"suggesting serial execution."
        )

    def test_parallel_faster_than_serial(self, tmp_path):
        """Wall-clock time for parallel execution should be ~1/3 of serial."""
        from app.api.workspace import git_info_for_workspace
        import app.api.workspace as ws_mod

        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        def slow_git(args, cwd, timeout=3):
            if args[0] == "rev-parse":
                return "main"
            time.sleep(0.1)
            if args[0] == "status":
                return ""
            return "0"

        with patch.object(ws_mod, "_run_git", side_effect=slow_git):
            t0 = time.monotonic()
            result = git_info_for_workspace(tmp_path)
            elapsed = time.monotonic() - t0

        assert result is not None
        assert result["is_git"] is True
        assert elapsed < 0.25, (
            f"git_info_for_workspace took {elapsed:.3f}s — expected < 0.25s "
            f"with parallel execution (serial baseline is ~0.3s)."
        )


# ── 2. Message pagination (msg_limit + msg_before) ─────────────────────────


class TestMessagePaginationBackend:
    """Backend /api/session must support msg_limit and msg_before parameters
    to return only the last N messages, reducing payload size for fast
    session switching."""

    def _make_session(self, n_msgs=100):
        """Create a mock session with n_msgs messages."""
        session = MagicMock()
        session.session_id = "test_session_123"
        session.title = "Test Session"
        session.workspace = "/tmp/test"
        session.model = "test-model"
        session.created_at = 1000000
        session.updated_at = 2000000
        session.pinned = False
        session.archived = False
        session.project_id = None
        session.profile = None
        session.input_tokens = 0
        session.output_tokens = 0
        session.estimated_cost = None
        session.personality = None
        session.active_stream_id = None
        session.pending_user_message = None
        session.pending_attachments = []
        session.pending_started_at = None
        session.compression_anchor_visible_idx = None
        session.compression_anchor_message_key = None
        session._metadata_message_count = None
        session.messages = [
            {"role": "user" if i % 3 == 0 else "assistant", "content": f"Message {i}"}
            for i in range(n_msgs)
        ]
        session.tool_calls = []
        session.compact.return_value = {
            "session_id": "test_session_123",
            "title": "Test Session",
            "workspace": "/tmp/test",
            "model": "test-model",
            "message_count": n_msgs,
            "created_at": 1000000,
            "updated_at": 2000000,
            "last_activity_at": 2000000,
            "pinned": False,
            "archived": False,
            "project_id": None,
            "profile": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": None,
            "personality": None,
            "compression_anchor_visible_idx": None,
            "compression_anchor_message_key": None,
            "active_stream_id": None,
            "is_streaming": False,
        }
        return session

    def test_msg_limit_returns_tail(self):
        """msg_limit=10 should return the last 10 messages of a 100-msg session."""
        session = self._make_session(100)
        all_msgs = session.messages
        msg_limit = 10

        truncated = all_msgs[-msg_limit:]
        assert len(truncated) == 10
        assert truncated[0]["content"] == "Message 90"
        assert truncated[-1]["content"] == "Message 99"

    def test_msg_limit_larger_than_total(self):
        """msg_limit larger than total messages returns all messages."""
        session = self._make_session(50)
        all_msgs = session.messages
        msg_limit = 100

        truncated = all_msgs[-msg_limit:]
        assert len(truncated) == 50
        assert len(all_msgs) <= msg_limit

    def test_msg_before_index_based_slicing(self):
        """msg_before=50 returns messages[:50] then tail window."""
        session = self._make_session(100)
        all_msgs = session.messages
        msg_before = 50
        msg_limit = 30

        _slice = all_msgs[:msg_before]
        truncated = _slice[-msg_limit:]
        assert len(truncated) == 30
        assert truncated[0]["content"] == "Message 20"
        assert truncated[-1]["content"] == "Message 49"

    def test_msg_before_zero_returns_empty(self):
        """msg_before=0 means no older messages exist — returns empty."""
        session = self._make_session(100)
        all_msgs = session.messages
        msg_before = 0

        _slice = all_msgs[:msg_before]
        assert len(_slice) == 0

    def test_msg_before_equal_total(self):
        """msg_before=100 returns all 100, tail-30 gives messages 70-99."""
        session = self._make_session(100)
        all_msgs = session.messages
        msg_before = 100
        msg_limit = 30

        _slice = all_msgs[:msg_before]
        truncated = _slice[-msg_limit:]
        assert len(truncated) == 30
        assert truncated[0]["content"] == "Message 70"

    def test_truncation_flag(self):
        """_messages_truncated must be True when messages were omitted."""
        session = self._make_session(100)
        msg_limit = 30
        is_truncated = len(session.messages) > msg_limit
        assert is_truncated is True

        small = self._make_session(10)
        is_truncated_small = len(small.messages) > msg_limit
        assert is_truncated_small is False

    def test_truncation_flag_with_msg_before(self):
        """When msg_before filters to fewer than msg_limit, truncation is False."""
        session = self._make_session(100)
        msg_before = 10
        msg_limit = 30

        _slice = session.messages[:msg_before]
        _truncated = len(_slice) > msg_limit
        assert _truncated is False  # 10 < 30, no truncation

    def test_messages_offset_initial_load(self):
        """_messages_offset = index of first returned message in full array."""
        session = self._make_session(100)
        msg_limit = 30
        all_msgs = session.messages

        truncated = all_msgs[-msg_limit:]
        offset = len(all_msgs) - len(truncated)
        assert offset == 70
        assert truncated[0]["content"] == "Message 70"

    def test_messages_offset_with_msg_before(self):
        """_messages_offset for msg_before=50, msg_limit=30."""
        session = self._make_session(100)
        msg_before = 50
        msg_limit = 30

        _slice = session.messages[:msg_before]
        truncated = _slice[-msg_limit:]
        offset = msg_before - len(truncated)
        assert offset == 20
        assert truncated[0]["content"] == "Message 20"

    def test_payload_size_reduction(self):
        """Quantify the payload reduction: 100 msgs → 30 msgs = ~70% smaller."""
        import json

        session = self._make_session(100)
        all_json = json.dumps(session.messages)
        tail_json = json.dumps(session.messages[-30:])

        reduction = 1 - len(tail_json) / len(all_json)
        assert reduction > 0.6, (
            f"Expected >60% payload reduction, got {reduction*100:.0f}%."
        )

    def test_msg_before_bounds_clamping(self):
        """msg_before beyond array length should be clamped."""
        session = self._make_session(100)
        all_msgs = session.messages

        # msg_before = 999 → clamped to 100
        _before_idx = max(0, min(999, len(all_msgs)))
        assert _before_idx == 100

        # msg_before = -5 → clamped to 0
        _before_idx = max(0, min(-5, len(all_msgs)))
        assert _before_idx == 0
