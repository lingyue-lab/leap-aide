"""Unit tests for the process-loop completion drain (issue #75).

No server fixture: the registry, the queue, the delegation claim helpers,
the stream manager and threading are all faked so a single drain tick can be
driven synchronously and its call order asserted.
"""
import queue
from types import SimpleNamespace

import pytest

# app.api first: importing app.api.config appends the hermes-agent dir to
# sys.path, which is what makes the `tools.*` imports below resolve.
from app.api import streaming
from app.api.streaming import _drain_process_notifications_for_session

import tools.async_delegation as async_delegation
import tools.process_registry as process_registry_module

SESSION_ID = "sess-1"
DELEGATION_ID = "deleg-aaaa"


class FakeSession:
    def __init__(self, session_id=SESSION_ID):
        self.session_id = session_id
        self.model = "@test:model"  # keeps the model-normalisation branch a no-op
        self.workspace = "/tmp/ws"
        self._process_loop_last_active = 0.0


class RecordingQueue(queue.Queue):
    """queue.Queue that records every put() as a "requeue" call."""

    def __init__(self, calls):
        super().__init__()
        self.calls = calls

    def put(self, item, block=True, timeout=None):
        self.calls.append("requeue")
        return super().put(item, block, timeout)


class FakeRegistry:
    def __init__(self, calls, events=()):
        self.calls = calls
        self.completion_queue = RecordingQueue(calls)
        for evt in events:
            queue.Queue.put(self.completion_queue, evt)  # seed without recording
        self.drain_calls = []
        self.raise_on_drain = None

    def drain_notifications(self, session_key="", owns_event=None, **kwargs):
        self.calls.append("drain")
        self.drain_calls.append(session_key)
        if self.raise_on_drain is not None:
            raise self.raise_on_drain
        drained = []
        while not self.completion_queue.empty():
            evt = self.completion_queue.get_nowait()
            drained.append((evt, f"msg:{evt.get('delegation_id')}"))
        return drained


class FakeStreamManager:
    def __init__(self, calls, busy_stream_id=None, begin_result="sid-1234"):
        self.calls = calls
        self.busy_stream_id = busy_stream_id
        self.begin_result = begin_result
        self.begin_error = None

    def get_stream_id(self, session_id):
        self.calls.append("get_stream_id")
        return self.busy_stream_id

    def begin_stream(self, session):
        self.calls.append("begin_stream")
        if self.begin_error is not None:
            raise self.begin_error
        return self.begin_result

    def cleanup_by_stream_id(self, stream_id):
        self.calls.append("cleanup")

    def end_stream(self, session, stream_id=None):
        self.calls.append("end_stream")


class FakeThread:
    def __init__(self, target=None, args=(), daemon=None, name=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name
        self.start_error = None
        self.start_calls = 0

    def start(self):
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error


class FakeThreading:
    def __init__(self):
        self.created = []

    def Thread(self, **kwargs):
        thread = FakeThread(**kwargs)
        self.created.append(thread)
        return thread


@pytest.fixture
def harness(monkeypatch):
    calls = []
    session = FakeSession()
    registry = FakeRegistry(calls)
    sm = FakeStreamManager(calls)
    th = FakeThreading()
    knobs = {"claim": "claim-1", "complete_error": None, "release_error": None}

    def claim_event_delivery(event, consumer):
        calls.append("claim")
        return knobs["claim"]

    def complete_event_delivery(event, claim):
        calls.append("complete")
        if knobs["complete_error"] is not None:
            raise knobs["complete_error"]

    def release_event_delivery(event, claim):
        calls.append("release")
        if knobs["release_error"] is not None:
            raise knobs["release_error"]

    monkeypatch.setattr(process_registry_module, "process_registry", registry)
    monkeypatch.setattr(async_delegation, "claim_event_delivery", claim_event_delivery)
    monkeypatch.setattr(async_delegation, "complete_event_delivery", complete_event_delivery)
    monkeypatch.setattr(async_delegation, "release_event_delivery", release_event_delivery)
    monkeypatch.setattr(streaming, "stream_manager", sm)
    monkeypatch.setattr(streaming, "threading", SimpleNamespace(Thread=th.Thread))
    return SimpleNamespace(
        calls=calls, session=session, registry=registry, sm=sm, th=th,
        knobs=knobs, monkeypatch=monkeypatch,
    )


def _event(delegation_id=DELEGATION_ID):
    return {
        "type": "async_delegation",
        "delegation_id": delegation_id,
        "session_key": f"agent:main:webui:dm:{SESSION_ID}",
        "origin_ui_session_id": SESSION_ID,
        "session_id": "child-1",
    }


def test_busy_session_leaves_queue_untouched(harness):
    """A turn is streaming: the drain must not pop, claim, or start a thread."""
    harness.registry.completion_queue.put(_event())
    harness.calls.clear()
    harness.sm.busy_stream_id = "busy-sid"

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.registry.drain_calls == []
    assert "claim" not in harness.calls
    assert harness.th.created == []
    assert harness.registry.completion_queue.qsize() == 1
    assert harness.session._process_loop_last_active > 0


def test_event_delivered_on_next_tick_after_busy(harness):
    """Busy tick keeps the event; the following idle tick delivers it exactly once."""
    harness.registry.completion_queue.put(_event())
    harness.sm.busy_stream_id = "busy-sid"
    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    harness.sm.busy_stream_id = None
    harness.calls.clear()
    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls.count("claim") == 1
    assert harness.calls.count("complete") == 1
    assert len(harness.th.created) == 1
    assert harness.registry.completion_queue.qsize() == 0


from app.api.streaming import _abandon_completion_delivery


def test_abandon_delivery_order_with_stream(harness):
    """Rollback order is cleanup -> release -> requeue; nothing else."""
    event = _event()
    _abandon_completion_delivery(event, "claim-1", harness.session, stream_id="sid-1234")

    assert harness.calls == ["cleanup", "release", "requeue"]
    assert harness.registry.completion_queue.get_nowait() is event


def test_abandon_delivery_order_without_stream(harness):
    """Without a stream there is nothing to clean; release still precedes requeue."""
    event = _event()
    _abandon_completion_delivery(event, "claim-1", harness.session)

    assert harness.calls == ["release", "requeue"]


def test_single_event_delivers_once(harness):
    """Happy path: claim once, begin once, start one thread, ack once."""
    event = _event()
    harness.registry.completion_queue.put(event)
    harness.calls.clear()

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls == [
        "get_stream_id", "drain", "claim", "begin_stream", "complete",
    ]
    assert len(harness.th.created) == 1
    thread = harness.th.created[0]
    assert thread.start_calls == 1
    assert thread.args[0] == SESSION_ID
    assert thread.args[1] == f"msg:{DELEGATION_ID}"
    assert thread.args[4] == "sid-1234"
    assert thread.args[6] is True
    assert harness.registry.completion_queue.qsize() == 0
    assert harness.session._process_loop_last_active > 0


def test_extra_events_requeued_before_claim(harness):
    """Two events in one tick: only the first is delivered; the second is back
    in the queue, in order, before the first claim happens."""
    first, second = _event("deleg-1"), _event("deleg-2")
    harness.registry.completion_queue.put(first)
    harness.registry.completion_queue.put(second)
    harness.calls.clear()

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls.index("requeue") < harness.calls.index("claim")
    assert harness.calls.count("claim") == 1
    assert harness.calls.count("complete") == 1
    assert harness.registry.completion_queue.qsize() == 1
    assert harness.registry.completion_queue.get_nowait() is second


def test_empty_drain_does_nothing(harness):
    """Queue holding only other sessions' events: no claim, no thread, no warning."""
    harness.calls.clear()

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls == ["get_stream_id", "drain"]
    assert harness.th.created == []


def test_claim_held_elsewhere_drops_local_copy(harness):
    """claim is None: our copy is a duplicate — drop it, no begin, no release."""
    harness.registry.completion_queue.put(_event())
    harness.knobs["claim"] = None
    harness.calls.clear()

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert "begin_stream" not in harness.calls
    assert "release" not in harness.calls
    assert "complete" not in harness.calls
    assert harness.th.created == []
    assert harness.registry.completion_queue.qsize() == 0


def test_non_persistent_event_delivers_with_empty_claim(harness):
    """claim == "" means "no durable row" — it is a success, not a duplicate."""
    harness.registry.completion_queue.put(_event())
    harness.knobs["claim"] = ""
    harness.calls.clear()

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert "begin_stream" in harness.calls
    assert "complete" in harness.calls
    assert "release" not in harness.calls
    assert len(harness.th.created) == 1


def test_begin_stream_race_rolls_back(harness):
    """begin_stream returns None: release strictly before requeue, no thread."""
    event = _event()
    harness.registry.completion_queue.put(event)
    harness.sm.begin_result = None
    harness.calls.clear()

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls.index("release") < harness.calls.index("requeue")
    assert "complete" not in harness.calls
    assert harness.th.created == []
    assert harness.registry.completion_queue.get_nowait() is event


def test_begin_stream_exception_rolls_back(harness):
    """begin_stream raising (not just returning None) must not lose the event."""
    event = _event()
    harness.registry.completion_queue.put(event)
    harness.sm.begin_error = RuntimeError("lock broken")
    harness.calls.clear()

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls.index("release") < harness.calls.index("requeue")
    assert harness.registry.completion_queue.get_nowait() is event


def test_thread_start_failure_rolls_back_with_warning(harness, caplog):
    """start() raising: cleanup -> release -> requeue, warning carries the delegation id."""
    event = _event()
    harness.registry.completion_queue.put(event)
    original_thread_factory = harness.th.Thread

    def failing_factory(**kwargs):
        thread = original_thread_factory(**kwargs)
        thread.start_error = RuntimeError("can't start new thread")
        return thread

    harness.monkeypatch.setattr(streaming, "threading", SimpleNamespace(Thread=failing_factory))
    harness.calls.clear()

    with caplog.at_level("WARNING"):
        _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls.index("cleanup") < harness.calls.index("release") < harness.calls.index("requeue")
    assert harness.registry.completion_queue.get_nowait() is event
    assert DELEGATION_ID in caplog.text
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_complete_failure_does_not_roll_back(harness, caplog):
    """The ack failing after the thread started must not requeue: delivery happened."""
    harness.registry.completion_queue.put(_event())
    harness.knobs["complete_error"] = RuntimeError("db locked")
    harness.calls.clear()

    with caplog.at_level("WARNING"):
        _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert "release" not in harness.calls
    assert "requeue" not in harness.calls
    assert harness.registry.completion_queue.qsize() == 0
    assert DELEGATION_ID in caplog.text


def test_outer_exception_logs_warning_with_session_id(harness, caplog):
    """A crash inside the drain is a warning carrying the session id, not debug."""
    harness.registry.raise_on_drain = RuntimeError("boom")

    with caplog.at_level("WARNING"):
        _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert any(
        r.levelname == "WARNING" and SESSION_ID in r.getMessage()
        for r in caplog.records
    )


from app.api.stream_manager import StreamManager


def test_begin_stream_notify_failure_keeps_stream_registered(monkeypatch, caplog):
    """A failing notify must not leave the session half-registered with no id."""
    import app.api.models as models

    def boom(*args, **kwargs):
        raise RuntimeError("notify exploded")

    monkeypatch.setattr(models, "_notify_session_event", boom)
    manager = StreamManager()

    with caplog.at_level("WARNING"):
        stream_id = manager.begin_stream(FakeSession())

    assert stream_id is not None
    assert manager.get_stream_id(SESSION_ID) == stream_id
    assert any(r.levelname == "WARNING" for r in caplog.records)


from app.api.streaming import _cleanup_stream_after_run


def test_cleanup_stream_after_run_with_no_session(harness):
    """session is None (get_session failed): fall back to cleanup_by_stream_id."""
    _cleanup_stream_after_run(None, "sid-1234")

    assert harness.calls == ["cleanup"]


def test_cleanup_stream_after_run_ends_stream_for_session(harness):
    """Loaded session keeps the normal end_stream path (callbacks, counts)."""
    _cleanup_stream_after_run(harness.session, "sid-1234")

    assert harness.calls == ["end_stream"]
