"""
Regression tests for session streaming indicator payloads used by the session list.

This ensures backend payloads report per-session streaming status from active stream
tracking, not only for the foreground conversation.
"""

import pytest

import api.models as models
from api.models import Session, all_sessions
from api.stream_manager import stream_manager


@pytest.fixture(autouse=True)
def _isolate_session_stream_state(tmp_path, monkeypatch):
    """Keep session/index/stream state isolated from the host environment."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    index_file = session_dir / "_index.json"

    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", index_file)
    models.SESSIONS.clear()
    stream_manager._streams.clear()
    stream_manager._session_streams.clear()

    yield

    models.SESSIONS.clear()
    stream_manager._streams.clear()
    stream_manager._session_streams.clear()


def _make_session(session_id, stream_id=None, message_count=1):
    s = Session(
        session_id=session_id,
        title=session_id,
        messages=[{"role": "user", "content": f"seed-{session_id}"}] * message_count,
    )
    s.active_stream_id = stream_id
    return s


def test_all_sessions_marks_indexed_and_in_memory_streaming_sessions():
    """Session records from both index and in-memory cache should expose is_streaming."""
    s_disk = _make_session("disk_session", stream_id="stream-1")
    models.SESSIONS[s_disk.session_id] = s_disk
    s_disk.sync_webui_metadata()

    s_memory = _make_session("memory_session", stream_id="stream-2")
    with models.LOCK:
        models.SESSIONS[s_memory.session_id] = s_memory

    # Register streams in stream_manager._session_streams (the source of truth
    # for active_stream_ids used by all_sessions() to compute is_streaming).
    stream_manager._session_streams["disk_session"] = "stream-1"
    stream_manager._session_streams["memory_session"] = "stream-2"

    listed = all_sessions()
    by_sid = {s["session_id"]: s for s in listed}

    assert by_sid["disk_session"]["is_streaming"] is True
    assert by_sid["memory_session"]["is_streaming"] is True
    assert by_sid["memory_session"]["active_stream_id"] == "stream-2"


def test_all_sessions_marks_streaming_false_when_stream_is_not_active():
    """Stale active_stream_id should not imply streaming without active registry entry."""
    s = _make_session("stalesession", stream_id="stale-stream")
    models.SESSIONS[s.session_id] = s
    s.sync_webui_metadata()

    assert all_sessions()[0]["is_streaming"] is False

    stream_manager._session_streams["stalesession"] = "stale-stream"
    assert all_sessions()[0]["is_streaming"] is True

    stream_manager._session_streams.pop("stalesession", None)
    assert all_sessions()[0]["is_streaming"] is False


def test_all_sessions_does_not_report_streaming_after_restart_without_active_registry():
    """Server restarts should not resurrect sidebar streaming state from disk alone.

    ``active_stream_id`` is a runtime-only field (lives in
    ``stream_manager._session_streams``); it is never persisted to state.db
    nor to ``_index.json``. The contract being guarded:

      1. ``Session.load`` must find the session after a restart (state.db
         is the source of truth).
      2. ``is_streaming`` must be derived from the active stream registry,
         not from a stale ``active_stream_id`` on disk.
    """
    s = _make_session("restart_session", stream_id="old-stream")
    models.SESSIONS[s.session_id] = s
    s.sync_webui_metadata()

    # Sidebar reports streaming while the registry has the entry.
    stream_manager._session_streams["restart_session"] = "old-stream"
    pre = {x["session_id"]: x for x in all_sessions()}
    assert pre["restart_session"]["is_streaming"] is True

    # Simulate server restart: drop in-memory cache and stream registry.
    # Disk (state.db) still holds the session row.
    models.SESSIONS.clear()
    stream_manager._session_streams.clear()

    # Session.load must find the row (state.db path).
    reloaded = Session.load("restart_session")
    assert reloaded is not None, (
        "Session.load should resolve 'restart_session' from state.db "
        "after sync_webui_metadata wrote it"
    )

    # Sidebar must NOT report streaming — the registry is empty.
    post = {x["session_id"]: x for x in all_sessions()}
    assert "restart_session" in post
    assert post["restart_session"]["is_streaming"] is False, (
        "is_streaming must be derived from stream_manager (active registry), "
        "not from a stale active_stream_id on disk"
    )