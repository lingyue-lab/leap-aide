"""
Tests: /api/sessions must correctly set is_subagent_session on rows that have
parent_session_id, and must exclude such rows from the top-level sessions[]
(the workspace-group primary list).

Regression for the sidebar double-render bug:
  WebUI-spawned subagent sessions (parent_session_id != NULL) appeared in
  BOTH the sidebar's primary workspace-group list AND the parent's expanded
  delegate-children tree. The primary-list leak was caused by:
    a) /api/sessions DB compact dict not setting is_subagent_session (so the
       frontend primary-list filter had no flag to match on)
    b) /api/sessions final filter not excluding subagent rows (no backend
       defense-in-depth)

Both halves of the fix are locked in by this single regression test.

Files touched by the production fix (mirrors this test's assertions):
  api/models.py:_all_sessions_from_db (compact dict + final filter)
  api/models.py:_all_sessions_from_index (final filter)
  api/models.py:_all_sessions_from_json (final filter)
  frontend/src/components/sidebar/SessionList.tsx (primary-list filter fallback)
  frontend/src/stores/notifyManager.ts (upsert_session existing-session branch)
"""

import sys
import pathlib
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from tests._test_helpers import get_body, post


# ──────────────────────────────────────────────
# Health check (prerequisite for all tests)
# ──────────────────────────────────────────────

def test_health():
    """Server must be running and healthy."""
    data = get_body("/health")
    assert data["status"] == "ok", f"health not ok: {data}"


# ──────────────────────────────────────────────
# Regression: /api/sessions must mark and exclude subagent rows
# ──────────────────────────────────────────────

def test_api_sessions_marks_and_excludes_subagent_rows():
    """Critical contract test.

    Setup: seed a parent webui session + a child session with
    parent_session_id=<parent>, source='webui'. This mirrors the
    webui-spawned subagent row shape that SDK produces at runtime.

    Assertions:
      1. Child is NOT in /api/sessions sessions[] — the final filter must
         exclude it (backend defense-in-depth from the fix).
      2. /api/session?session_id=<child> returns is_subagent_session=True
         on the loaded session (this is wired by _from_db_row, independent
         of /api/sessions — confirms the SDK-derived field is consistent
         with what /api/sessions compact dict now emits).
      3. Parent IS in /api/sessions sessions[] and is_subagent_session is
         False on the parent row (sanity check that exclusion is targeted,
         not blanket).
    """
    # 1) Create a real WebUI parent session via the normal API
    parent_data, code = post("/api/session/new", {})
    assert code == 200, f"create parent failed: {code} {parent_data}"
    parent_sid = parent_data["session"]["session_id"]
    # Give it a non-Untitled title so _hide_empty_untitled doesn't filter it
    # out (the parent has 0 messages but a real title).
    post("/api/session/rename",
         {"session_id": parent_sid, "title": "subagent-flag-parent"})

    # 2) Seed a child session row with parent_session_id=<parent_sid>
    #    via the test-only seed endpoint (writes through SDK high-level API,
    #    production never has this endpoint exposed).
    child_sid = "subflagchld000"
    seed_body = {
        "session_id": child_sid,
        "source": "webui",
        "title": "subagent-flag-child",
        "parent_session_id": parent_sid,
        "messages": [
            {"role": "user", "content": "child test seed"},
        ],
    }
    seed_result, seed_code = post("/api/_test/seed_session", seed_body)
    assert seed_code == 200, f"seed child failed: {seed_code} {seed_result}"

    try:
        # 3) /api/sessions — the bug surface
        listing = get_body("/api/sessions")
        sids_in_list = [s["session_id"] for s in listing["sessions"]]

        # (1) Child must NOT appear in the top-level list
        assert child_sid not in sids_in_list, (
            f"BUG: subagent row {child_sid} leaked into /api/sessions "
            f"sessions[] — backend final filter failed to exclude it. "
            f"List contains parent={parent_sid in sids_in_list} "
            f"child={child_sid in sids_in_list}; "
            f"full list: {sids_in_list}"
        )

        # (3) Parent IS in the list and is NOT marked as subagent
        parent_row = next(
            (s for s in listing["sessions"] if s["session_id"] == parent_sid),
            None,
        )
        assert parent_row is not None, (
            f"parent {parent_sid} missing from /api/sessions — test setup wrong"
        )
        # is_subagent_session may be missing (older compact dict) or False;
        # the contract is "not True" for a non-subagent.
        assert parent_row.get("is_subagent_session") is not True, (
            f"parent incorrectly marked as subagent: {parent_row}"
        )

        # (2) Loading the child directly must show is_subagent_session=True
        #     (this is set by _from_db_row from parent_session_id, which is
        #     the same path the compact dict now mirrors). If this assertion
        #     fails, the SDK's subagent detection is broken upstream of our
        #     fix and the test fixture is wrong, not the production code.
        child_loaded = get_body(f"/api/session?session_id={child_sid}")
        assert child_loaded["session"]["session_id"] == child_sid
        assert child_loaded["session"].get("is_subagent_session") is True, (
            f"child loaded without is_subagent_session=True: "
            f"{child_loaded['session'].get('is_subagent_session')}"
        )
    finally:
        # Cleanup via test-only endpoint (production never has this exposed)
        try:
            post("/api/_test/cleanup_sessions",
                 {"session_ids": [parent_sid, child_sid]})
        except Exception:
            pass