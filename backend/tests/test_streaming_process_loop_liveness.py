"""Unit tests for the session process loop's exit decision (issue #97).

The loop that drains delegation completions used to stop as soon as the
session looked idle. The idle clock only advances when the session itself
does something, so a session waiting on a delegation that runs longer than
the idle timeout lost its poller -- and the delegation's completion then sat
on the shared queue until the service restarted.

No server fixture: the thread, the stop event and the drain are all faked so
one loop body can be driven synchronously and its call order asserted.
"""
from types import SimpleNamespace

import pytest

# app.api first: importing app.api.config appends the hermes-agent dir to
# sys.path, which is what makes the `tools.*` imports below resolve.
from app.api import config
from app.api import streaming

import tools.async_delegation as async_delegation

SESSION_ID = "sess-1"


class FakeStopEvent:
    """wait() hands control back to the loop body `ticks` times, then stops."""

    def __init__(self, ticks=1):
        self.ticks = ticks
        self.cleared = False

    def wait(self, timeout=None):
        if self.ticks > 0:
            self.ticks -= 1
            return False
        return True

    def clear(self):
        self.cleared = True


class FakeSession:
    def __init__(self, session_id=SESSION_ID):
        self.session_id = session_id
        self._process_loop_thread = None
        self._process_loop_stop = FakeStopEvent()
        self._process_loop_last_active = 0.0


class InlineThread:
    """Thread stub that runs the target synchronously inside start()."""

    def __init__(self, target=None, args=(), daemon=None, name=None):
        self._target = target
        self._args = args
        self.daemon = daemon
        self.name = name
        self.started = False

    def start(self):
        self.started = True
        if self._target is not None:
            self._target(*self._args)

    def is_alive(self):
        return False


@pytest.fixture
def loop_env(monkeypatch):
    """A process loop whose drain, exit check and idle timeout are all faked."""
    calls = []
    session = FakeSession()
    monkeypatch.setattr(streaming, "threading", SimpleNamespace(Thread=InlineThread))
    # The loop sets the idle clock when it starts, so the timeout is what
    # makes the clock look stale rather than the clock itself.
    monkeypatch.setattr(config, "get_idle_timeout", lambda: 0.0)
    monkeypatch.setattr(
        streaming, "_drain_process_notifications_for_session",
        lambda s, sid: calls.append("drain"),
    )
    return SimpleNamespace(calls=calls, session=session, monkeypatch=monkeypatch)


def test_live_delegation_lookup_asks_the_sdk_for_this_session(loop_env):
    """The SDK helper is asked about this WebUI session, not a session key."""
    seen = {}

    def fake_has_live_for_session(**kwargs):
        seen.update(kwargs)
        return True

    loop_env.monkeypatch.setattr(
        async_delegation, "has_live_for_session", fake_has_live_for_session,
    )

    assert streaming._session_has_live_delegations(SESSION_ID) is True
    assert seen == {"origin_ui_session_id": SESSION_ID}


def test_live_delegation_lookup_failure_keeps_the_session_alive(loop_env):
    """A broken lookup must not be the reason a live delegation loses its slot."""
    def boom(**kwargs):
        raise RuntimeError("registry unavailable")

    loop_env.monkeypatch.setattr(async_delegation, "has_live_for_session", boom)

    assert streaming._session_has_live_delegations(SESSION_ID) is True


def test_loop_drains_before_it_decides_to_stop(loop_env):
    """A completion already on the queue is picked up before the exit decision."""
    loop_env.monkeypatch.setattr(
        streaming, "_process_loop_should_exit",
        lambda s, sid: loop_env.calls.append("exit_check") or True,
    )

    streaming._ensure_process_loop(loop_env.session, SESSION_ID)

    assert loop_env.calls == ["drain", "exit_check"]


def test_loop_keeps_polling_while_a_delegation_is_live(loop_env):
    """A session waiting on a running delegation must not be reaped as idle."""
    loop_env.session._process_loop_stop = FakeStopEvent(ticks=3)
    loop_env.monkeypatch.setattr(
        streaming, "_session_has_live_delegations", lambda sid: True,
    )

    streaming._ensure_process_loop(loop_env.session, SESSION_ID)

    # Every tick drained; the loop never took the idle exit.
    assert loop_env.calls == ["drain", "drain", "drain"]


def test_loop_stops_when_idle_with_nothing_outstanding(loop_env):
    """With no live delegation the loop still retires once the idle timeout hits."""
    loop_env.session._process_loop_stop = FakeStopEvent(ticks=3)
    loop_env.monkeypatch.setattr(
        streaming, "_session_has_live_delegations", lambda sid: False,
    )

    streaming._ensure_process_loop(loop_env.session, SESSION_ID)

    # One tick: drain, then the exit decision ended the loop.
    assert loop_env.calls == ["drain"]


def test_fresh_session_is_never_reaped(loop_env):
    """A session that just acted is not idle, whatever the delegation state."""
    loop_env.monkeypatch.setattr(config, "get_idle_timeout", lambda: 3600.0)
    loop_env.session._process_loop_stop = FakeStopEvent(ticks=3)
    loop_env.monkeypatch.setattr(
        streaming, "_session_has_live_delegations", lambda sid: False,
    )

    streaming._ensure_process_loop(loop_env.session, SESSION_ID)

    assert loop_env.calls == ["drain", "drain", "drain"]
