"""Unit tests for the process-loop completion drain (issue #75).

No server fixture: the registry, the queue, the delegation claim helpers,
the stream manager and threading are all faked so a single drain tick can be
driven synchronously and its call order asserted.
"""
import queue
import sqlite3
import time
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
        foreign = []
        while not self.completion_queue.empty():
            evt = self.completion_queue.get_nowait()
            if owns_event is None or owns_event(evt):
                drained.append((evt, f"msg:{evt.get('delegation_id')}"))
            else:
                foreign.append(evt)
        for evt in foreign:
            # Unrecorded put: leaving another session's event queued is not a
            # requeue, and must not show up in the call order.
            queue.Queue.put(self.completion_queue, evt)
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
    def __init__(self, calls, target=None, args=(), daemon=None, name=None):
        self.calls = calls
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name
        self.start_error = None
        self.start_calls = 0

    def start(self):
        self.calls.append("thread_start")
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error


class FakeThreading:
    def __init__(self, calls):
        self.calls = calls
        self.created = []

    def Thread(self, **kwargs):
        thread = FakeThread(self.calls, **kwargs)
        self.created.append(thread)
        return thread


@pytest.fixture
def harness(monkeypatch):
    calls = []
    delivery_calls = []
    session = FakeSession()
    registry = FakeRegistry(calls)
    sm = FakeStreamManager(calls)
    th = FakeThreading(calls)
    knobs = {"claim": "claim-1", "claim_error": None, "complete_error": None, "release_error": None, "drop_error": None}
    fake_delivery_state = {}

    def claim_event_delivery(event, consumer):
        calls.append("claim")
        if knobs["claim_error"] is not None:
            raise knobs["claim_error"]
        return knobs["claim"]

    def complete_event_delivery(event, claim):
        calls.append("complete")
        delivery_calls.append(("complete", claim))
        delegation_id = str(event.get("delegation_id") or "")
        fake_delivery_state.setdefault(delegation_id, "pending")
        if knobs["complete_error"] is not None:
            raise knobs["complete_error"]
        fake_delivery_state[delegation_id] = "delivered"

    def release_event_delivery(event, claim):
        calls.append("release")
        delivery_calls.append(("release", claim))
        if knobs["release_error"] is not None:
            raise knobs["release_error"]

    def drop_completion_delivery(delegation_id, claim_id):
        calls.append("drop")
        delivery_calls.append(("drop", delegation_id, claim_id))
        if knobs["drop_error"] is not None:
            raise knobs["drop_error"]
        fake_delivery_state[delegation_id] = "dropped"

    monkeypatch.setattr(process_registry_module, "process_registry", registry)
    monkeypatch.setattr(async_delegation, "claim_event_delivery", claim_event_delivery)
    monkeypatch.setattr(async_delegation, "complete_event_delivery", complete_event_delivery)
    monkeypatch.setattr(async_delegation, "release_event_delivery", release_event_delivery)
    monkeypatch.setattr(async_delegation, "drop_completion_delivery", drop_completion_delivery)
    monkeypatch.setattr(streaming, "stream_manager", sm)
    monkeypatch.setattr(streaming, "threading", SimpleNamespace(Thread=th.Thread))
    return SimpleNamespace(
        calls=calls, session=session, registry=registry, sm=sm, th=th,
        delivery_calls=delivery_calls,
        fake_delivery_state=fake_delivery_state,
        knobs=knobs, monkeypatch=monkeypatch,
    )


def _event(delegation_id=DELEGATION_ID, origin_session=SESSION_ID):
    return {
        "type": "async_delegation",
        "delegation_id": delegation_id,
        "session_key": f"agent:main:webui:dm:{origin_session}",
        "origin_ui_session_id": origin_session,
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
    assert harness.delivery_calls == [("release", "claim-1")]
    assert harness.registry.completion_queue.get_nowait() is event


def test_abandon_delivery_order_without_stream(harness):
    """Without a stream there is nothing to clean; release still precedes requeue."""
    event = _event()
    _abandon_completion_delivery(event, "claim-1", harness.session)

    assert harness.calls == ["release", "requeue"]
    assert harness.delivery_calls == [("release", "claim-1")]


def test_single_event_delivers_once(harness):
    """Happy path: claim, begin, start one thread, then ack its claim token."""
    event = _event()
    harness.registry.completion_queue.put(event)
    harness.calls.clear()

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls == [
        "get_stream_id", "drain", "claim", "begin_stream", "thread_start", "complete",
    ]
    # The ack carries the token claim_event_delivery handed back, and it must
    # come after the thread started (the delivery is only real once it runs).
    assert harness.delivery_calls == [("complete", "claim-1")]
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
    """Three events queued at once are delivered one per tick, in FIFO order,
    each exactly once; the extras go back before the claim, never lost."""
    first, second, third = _event("deleg-1"), _event("deleg-2"), _event("deleg-3")
    harness.registry.completion_queue.put(first)
    harness.registry.completion_queue.put(second)
    harness.registry.completion_queue.put(third)
    harness.calls.clear()

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    # Both extras are back in the queue, order preserved, before any claim.
    assert harness.calls.index("requeue") < harness.calls.index("claim")
    assert harness.calls.count("claim") == 1
    assert harness.calls.count("complete") == 1
    assert harness.registry.completion_queue.qsize() == 2
    assert harness.th.created[0].args[1] == "msg:deleg-1"

    # Second tick: the next event only, with the last one back in the queue.
    harness.calls.clear()
    harness.sm.begin_result = "sid-2"
    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls == [
        "get_stream_id", "drain", "requeue", "claim", "begin_stream",
        "thread_start", "complete",
    ]
    assert harness.th.created[-1].args[1] == "msg:deleg-2"
    assert harness.registry.completion_queue.qsize() == 1
    assert harness.registry.completion_queue.get_nowait() is third
    # Same object back in the queue; restore it for the last tick.
    queue.Queue.put(harness.registry.completion_queue, third)

    # Third tick: the last event, and the queue drains empty.
    harness.calls.clear()
    harness.sm.begin_result = "sid-3"
    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls == [
        "get_stream_id", "drain", "claim", "begin_stream", "thread_start", "complete",
    ]
    assert harness.th.created[-1].args[1] == "msg:deleg-3"
    assert [t.args[1] for t in harness.th.created] == [
        "msg:deleg-1", "msg:deleg-2", "msg:deleg-3",
    ]
    assert len(harness.th.created) == 3
    assert all(t.start_calls == 1 for t in harness.th.created)
    assert harness.registry.completion_queue.qsize() == 0
    assert harness.calls.count("complete") == 1


def test_empty_drain_does_nothing(harness):
    """Queue holding only other sessions' events: no claim, no thread, no warning."""
    foreign = _event("deleg-foreign", origin_session="sess-other")
    harness.registry.completion_queue.put(foreign)
    harness.calls.clear()

    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls == ["get_stream_id", "drain"]
    assert harness.th.created == []
    assert harness.delivery_calls == []
    # The foreign event is left where it was, not requeued and not dropped.
    assert harness.registry.completion_queue.qsize() == 1
    assert harness.registry.completion_queue.get_nowait() is foreign


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


def test_claim_failure_requeues_event_and_next_tick_redelivers(harness, caplog):
    """claim raising (e.g. db locked): requeue with an attributable warning,
    release nothing, open no stream; the next tick redelivers end to end."""
    event = _event()
    harness.registry.completion_queue.put(event)
    harness.knobs["claim_error"] = sqlite3.OperationalError("database is locked")
    harness.calls.clear()

    with caplog.at_level("WARNING"):
        _drain_process_notifications_for_session(harness.session, SESSION_ID)

    # The event is back in the shared queue, not lost in a local variable.
    assert harness.registry.completion_queue.qsize() == 1
    assert harness.registry.completion_queue.get_nowait() is event
    # Same object back in the queue, unrecorded (existing test convention).
    queue.Queue.put(harness.registry.completion_queue, event)

    # No claim token exists, so nothing may be released and no stream may open.
    assert "release" not in harness.calls
    assert "begin_stream" not in harness.calls
    assert "complete" not in harness.calls
    assert harness.th.created == []

    # The failure is attributable: WARNING level, delegation id + full session id.
    assert any(r.levelname == "WARNING" for r in caplog.records)
    assert DELEGATION_ID in caplog.text
    assert SESSION_ID in caplog.text

    # The failed drain refreshed the idle clock so the 3600s idle timeout
    # cannot kill the poller before the next tick redelivers the event.
    assert harness.session._process_loop_last_active > time.time() - 5

    # Next tick: knob cleared, the event goes through the whole happy path.
    harness.knobs["claim_error"] = None
    harness.calls.clear()
    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.calls == [
        "get_stream_id", "drain", "claim", "begin_stream", "thread_start", "complete",
    ]
    assert harness.delivery_calls == [("complete", "claim-1")]
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
    # A non-persistent event is acked with the empty token, never a fake claim.
    assert harness.delivery_calls == [("complete", "")]
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
    """The ack failing after the thread started must not requeue: delivery happened; the failed ack is retried on the next tick."""
    harness.registry.completion_queue.put(_event())
    harness.knobs["complete_error"] = RuntimeError("db locked")
    harness.calls.clear()

    with caplog.at_level("WARNING"):
        _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert "release" not in harness.calls
    assert "requeue" not in harness.calls
    assert harness.registry.completion_queue.qsize() == 0
    assert DELEGATION_ID in caplog.text

    # The failed ack is registered for cross-tick retry: the row is still
    # pending, the entry carries the real claim token, and the idle clock
    # is refreshed so the 3600s timeout cannot kill the poller mid-window.
    assert harness.fake_delivery_state[DELEGATION_ID] == "pending"
    assert len(harness.session._pending_acks) == 1
    ack = harness.session._pending_acks[0]
    assert ack.delegation_id == DELEGATION_ID
    assert ack.claim == "claim-1"
    assert ack.attempts == 1
    assert harness.session._process_loop_last_active > time.time() - 5

    # Next tick, lock released: the registered ack is retried and lands
    # delivered without any requeue or release.
    harness.knobs["complete_error"] = None
    harness.calls.clear()
    with caplog.at_level("WARNING"):
        _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.fake_delivery_state[DELEGATION_ID] == "delivered"
    assert harness.session._pending_acks == []
    assert "requeue" not in harness.calls
    assert "release" not in harness.calls
    assert any(
        r.levelname == "WARNING"
        and "ack retried ok" in r.getMessage()
        and DELEGATION_ID in r.getMessage()
        and "attempts=2" in r.getMessage()
        for r in caplog.records
    )


def test_pending_ack_retries_before_busy_early_return(harness):
    """A registered ack is retried at the top of the drain, before the busy
    early return (issue #82): a streaming turn must not defer the ack."""
    harness.registry.completion_queue.put(_event())
    harness.knobs["complete_error"] = RuntimeError("db locked")
    _drain_process_notifications_for_session(harness.session, SESSION_ID)
    assert len(harness.session._pending_acks) == 1

    harness.knobs["complete_error"] = None
    harness.sm.busy_stream_id = "busy-sid"
    harness.calls.clear()
    harness.registry.drain_calls.clear()
    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    # Retry first — before even the busy check — then the busy early return.
    assert harness.calls == ["complete", "get_stream_id"]
    assert harness.registry.drain_calls == []
    assert harness.fake_delivery_state[DELEGATION_ID] == "delivered"
    assert harness.session._pending_acks == []


def test_complete_failure_falls_back_to_drop_after_deadline(harness, caplog):
    """Past T1 with complete still failing: fall back to the terminal drop
    using the real claim token; the row leaves pending for good (issue #82)."""
    harness.registry.completion_queue.put(_event())
    harness.knobs["complete_error"] = RuntimeError("db locked")
    _drain_process_notifications_for_session(harness.session, SESSION_ID)
    assert harness.fake_delivery_state[DELEGATION_ID] == "pending"

    # The delivered window (T1) is over; the drop window (T2) is open.
    harness.monkeypatch.setattr(streaming, "_PENDING_ACK_T1_SECONDS", 0.0)
    harness.calls.clear()
    with caplog.at_level("WARNING"):
        _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert ("drop", DELEGATION_ID, "claim-1") in harness.delivery_calls
    assert harness.fake_delivery_state[DELEGATION_ID] == "dropped"
    assert harness.session._pending_acks == []
    assert "complete" not in harness.calls  # past T1: stop trying delivered
    assert "requeue" not in harness.calls
    assert "release" not in harness.calls
    assert any(
        r.levelname == "WARNING"
        and "ack fallback dropped" in r.getMessage()
        and DELEGATION_ID in r.getMessage()
        and "attempts=2" in r.getMessage()
        for r in caplog.records
    )


def test_pending_ack_unresolved_after_drop_deadline(harness, caplog):
    """Past T2 with drop still failing: remove the entry and log the final
    warning instead of accumulating retries forever (issue #82, spec 6.3)."""
    harness.registry.completion_queue.put(_event())
    harness.knobs["complete_error"] = RuntimeError("db locked")
    harness.knobs["drop_error"] = RuntimeError("db locked")
    _drain_process_notifications_for_session(harness.session, SESSION_ID)

    # T1 over, T2 open: the drop is attempted, fails, entry retained.
    harness.monkeypatch.setattr(streaming, "_PENDING_ACK_T1_SECONDS", 0.0)
    harness.calls.clear()
    # A completed-but-unacked delegation is not "live", so the idle clock is
    # the only thing keeping the poller alive during the retry window: age it
    # far past any idle timeout before this tick (final review Minor 2).
    harness.session._process_loop_last_active = time.time() - 9999
    _drain_process_notifications_for_session(harness.session, SESSION_ID)
    assert ("drop", DELEGATION_ID, "claim-1") in harness.delivery_calls
    assert len(harness.session._pending_acks) == 1
    # The retry pass must have refreshed the idle clock, or a non-default
    # idle_timeout under T2 (~290s) would exit the poller mid retry-window.
    assert harness.session._process_loop_last_active > time.time() - 5

    # T2 over: give up, remove, and leave the final attributable warning.
    harness.monkeypatch.setattr(streaming, "_PENDING_ACK_T2_SECONDS", 0.0)
    harness.calls.clear()
    with caplog.at_level("WARNING"):
        _drain_process_notifications_for_session(harness.session, SESSION_ID)

    assert harness.session._pending_acks == []
    assert harness.fake_delivery_state[DELEGATION_ID] == "pending"
    assert "complete" not in harness.calls
    assert "drop" not in harness.calls
    assert "requeue" not in harness.calls
    assert "release" not in harness.calls
    assert any(
        r.levelname == "WARNING"
        and "ack unresolved" in r.getMessage()
        and DELEGATION_ID in r.getMessage()
        and "attempts=2" in r.getMessage()
        for r in caplog.records
    )


def test_pending_ack_deadlines_stay_inside_claim_window():
    """T1 and T2 are monkeypatched to 0.0 in the deadline tests, so nothing
    else enforces the invariant their comment asserts: both deadlines must
    stay inside the SDK's 300s claim freshness window (final review Minor 3)."""
    assert (
        streaming._PENDING_ACK_T1_SECONDS
        < streaming._PENDING_ACK_T2_SECONDS
        < 300
    )


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
