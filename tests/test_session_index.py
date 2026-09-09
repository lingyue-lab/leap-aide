"""
Tests for Session.compact() output.

The legacy ``_write_session_index`` / ``_index.json`` file-based session index
was retired when session state moved to state.db. The single source of truth
is now ``hermes_state`` in state.db, queried by ``all_sessions()``. As a
result, the historical "incremental patch / atomic write / corrupt index
fallback" suite of tests against ``_write_session_index`` no longer applies
and was removed. The one behavioural assertion that still matters — that
``Session.compact()`` exposes ``last_activity_at`` derived from
``updated_at`` so the sidebar can render activity timestamps without a
second query — is kept here.
"""
from api.models import Session


def test_compact_exposes_last_activity_at_from_updated_at():
    s = Session(
        session_id="sess_time",
        title="Time",
        updated_at=300.0,
        messages=[
            {"role": "user", "content": "old", "_ts": 100.0},
            {"role": "tool", "content": "ignore", "timestamp": 400.0},
            {"role": "assistant", "content": "latest", "timestamp": 200.0},
        ],
    )

    compact = s.compact()

    assert compact["updated_at"] == 300.0
    assert compact["last_activity_at"] == 300.0
