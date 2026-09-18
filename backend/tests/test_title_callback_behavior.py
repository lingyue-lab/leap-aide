"""
Behavior tests for api.streaming._on_session_title (SDK -> WebUI title push).

Replaces tests/test_sprint41_title_autogen.py (which used fragile regex/grep
assertions on streaming.py source) with a behavior test that imports the
callback and exercises its observable side effects:

- put() SSE events emitted / suppressed
- in-memory Session.title mutated / left alone
- empty / whitespace titles
- put() failures (queue closed) -> graceful log + return
- unknown exceptions -> outer try/except protects the SDK caller

These tests are immune to refactors of streaming.py: as long as the callback
behaves correctly, the test passes. They directly encode the a6e79a0f design
decision: 'derived' titles must NOT be pushed to the frontend.
"""
import sys
import pathlib
import unittest
from types import SimpleNamespace

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

from app.api.streaming import _on_session_title


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

class _FakeSession(SimpleNamespace):
    """Stand-in for api.models.Session — only .title is touched by the callback."""


def _make_put_capture():
    """Return (put_fn, captured_list). put_fn(kind, data) records to captured_list.

    Matches the SSE put signature put(kind, data). list.append takes one arg
    so we can't reuse it directly.
    """
    captured = []

    def _put(kind, data):
        captured.append((kind, data))

    return _put, captured


def _make_sessions_get(initial_titles=None):
    """Return (sessions_get, store) backed by a dict; initial_titles is {sid: title}."""
    store = {sid: _FakeSession(title=t) for sid, t in (initial_titles or {}).items()}

    def _get(sid):
        return store.get(sid)

    return _get, store


# ---------------------------------------------------------------------------
# Stage-1 'derived' must be suppressed (a6e79a0f)
# ---------------------------------------------------------------------------

class TestDerivedSuppression(unittest.TestCase):
    """Stage-1 'derived' (synchronous heuristic) must NOT reach the frontend."""

    def test_derived_does_not_emit_title_sse_event(self):
        put, captured = _make_put_capture()
        sessions_get, _ = _make_sessions_get({'s1': 'Untitled'})

        _on_session_title(
            'Noisy derived', source='derived',
            session_id='s1', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(
            captured, [],
            "derived stage must not forward to SSE 'title' event "
            "(would cause sidebar flicker Untitled -> derived -> llm)",
        )

    def test_derived_does_not_mutate_in_memory_title(self):
        put, _ = _make_put_capture()
        sessions_get, store = _make_sessions_get({'s1': 'Untitled'})

        _on_session_title(
            'Noisy derived', source='derived',
            session_id='s1', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(
            store['s1'].title, 'Untitled',
            "derived stage must leave in-memory title at placeholder "
            "(otherwise done-event payload and GET /api/session reload flicker)",
        )

    def test_derived_with_workspace_prefix_does_not_emit(self):
        """The exact a6e79a0f regression scenario — the SDK's derive_title()
        picks up the [Workspace: ...] prefix webui prepends to user messages
        and would have rendered as the session title in the sidebar."""
        put, captured = _make_put_capture()
        sessions_get, _ = _make_sessions_get({'s1': 'Untitled'})

        _on_session_title(
            '[Workspace: /tmp/repo] implement feature X',
            source='derived',
            session_id='s1', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(
            captured, [],
            "workspace-prefixed derived title must not reach the frontend "
            "(would render as the session title)",
        )

    def test_derived_with_missing_session_row_is_safe(self):
        """SESSIONS.get returns None — callback must not raise."""
        put, captured = _make_put_capture()
        sessions_get, _ = _make_sessions_get({})  # empty store

        _on_session_title(
            'any', source='derived',
            session_id='unknown', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(captured, [])


# ---------------------------------------------------------------------------
# Stage-2 'llm' (and other non-derived sources) must reach the frontend
# ---------------------------------------------------------------------------

class TestLlmForward(unittest.TestCase):
    """Stage-2 'llm' (or any source != 'derived') must be forwarded."""

    def test_llm_emits_title_sse_event_with_correct_payload(self):
        put, captured = _make_put_capture()
        sessions_get, _ = _make_sessions_get({'s1': 'Untitled'})

        _on_session_title(
            'Refined LLM title', source='llm',
            session_id='s1', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(len(captured), 1)
        kind, payload = captured[0]
        self.assertEqual(kind, 'title')
        self.assertEqual(payload, {
            'session_id': 's1',
            'title': 'Refined LLM title',
            'source': 'llm',
        })

    def test_llm_mutates_in_memory_title(self):
        put, _ = _make_put_capture()
        sessions_get, store = _make_sessions_get({'s1': 'Untitled'})

        _on_session_title(
            'Refined', source='llm',
            session_id='s1', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(
            store['s1'].title, 'Refined',
            "llm stage must update in-memory title so the next done-event "
            "payload is consistent (CLAUDE.md rule 8: currentSession mirror)",
        )

    def test_non_derived_sources_are_forwarded(self):
        """Anything other than 'derived' (including empty string, 'manual',
        'compaction') is forwarded to the frontend as-is. Only 'derived' is
        special-cased for the flicker-prevention decision."""
        for source in ('llm', 'manual', 'compaction', ''):
            with self.subTest(source=source):
                put, captured = _make_put_capture()
                sessions_get, _ = _make_sessions_get({'s1': 'Untitled'})

                _on_session_title(
                    'A title', source=source,
                    session_id='s1', put=put, sessions_get=sessions_get,
                )

                self.assertEqual(
                    len(captured), 1,
                    f"source={source!r} must be forwarded (only 'derived' is suppressed)",
                )
                self.assertEqual(captured[0][1]['source'], source)

    def test_llm_with_missing_session_row_still_emits(self):
        """If SESSIONS.get returns None (cache eviction), still emit the
        'title' SSE — the title is authoritative in db and the frontend's
        re-fetch will pick it up."""
        put, captured = _make_put_capture()
        sessions_get, _ = _make_sessions_get({})  # empty

        _on_session_title(
            'A title', source='llm',
            session_id='unknown', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][1]['session_id'], 'unknown')


# ---------------------------------------------------------------------------
# Edge cases that must not crash the SDK caller
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    """Empty / whitespace / None titles; put / sessions_get failures."""

    def test_empty_string_title_returns_silently(self):
        put, captured = _make_put_capture()
        sessions_get, _ = _make_sessions_get({'s1': 'Untitled'})

        _on_session_title(
            '', source='llm',
            session_id='s1', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(captured, [])

    def test_whitespace_title_returns_silently(self):
        put, captured = _make_put_capture()
        sessions_get, _ = _make_sessions_get({'s1': 'Untitled'})

        _on_session_title(
            '   \t  \n  ', source='llm',
            session_id='s1', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(captured, [])

    def test_none_title_returns_silently(self):
        put, captured = _make_put_capture()
        sessions_get, _ = _make_sessions_get({'s1': 'Untitled'})

        _on_session_title(
            None, source='llm',
            session_id='s1', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(captured, [])

    def test_title_is_stripped_before_emit(self):
        put, captured = _make_put_capture()
        sessions_get, _ = _make_sessions_get({'s1': 'Untitled'})

        _on_session_title(
            '  Padded Title  ', source='llm',
            session_id='s1', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(captured[0][1]['title'], 'Padded Title')

    def test_title_in_memory_is_stripped_too(self):
        put, _ = _make_put_capture()
        sessions_get, store = _make_sessions_get({'s1': 'Untitled'})

        _on_session_title(
            '  Padded  ', source='llm',
            session_id='s1', put=put, sessions_get=sessions_get,
        )

        self.assertEqual(store['s1'].title, 'Padded')

    def test_put_failure_is_logged_not_raised(self):
        """The SDK invokes this from its auto-title daemon thread, which can
        outlive the agent thread that created the put() closure. If the SSE
        queue has already been GC'd, the put() call may raise — must be
        swallowed, not propagated (the SDK caller is on a daemon thread and
        must not die from a webui-side cleanup race)."""
        def _broken_put(kind, data):
            raise RuntimeError("queue closed")

        sessions_get, _ = _make_sessions_get({'s1': 'Untitled'})

        # Must not raise.
        _on_session_title(
            'A title', source='llm',
            session_id='s1', put=_broken_put, sessions_get=sessions_get,
        )

    def test_sessions_get_failure_is_logged_not_raised(self):
        """If SESSIONS.get throws (db gone, etc.), the outer try/except
        must catch it and not propagate to the SDK caller."""
        def _broken_get(sid):
            raise RuntimeError("db gone")

        put, captured = _make_put_capture()

        # Must not raise.
        _on_session_title(
            'A title', source='llm',
            session_id='s1', put=put, sessions_get=_broken_get,
        )

        # put() was not reached because sessions_get raised first.
        self.assertEqual(captured, [])


if __name__ == "__main__":
    unittest.main()
