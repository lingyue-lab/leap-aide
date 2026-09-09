"""
Tests for Phase 1: Real-time Gateway Session Sync.

Tests are ordered TDD-style:
  1. Gateway sessions appear in /api/sessions when setting enabled
  2. Gateway sessions excluded when setting disabled
  3. Gateway sessions have correct metadata (source_tag, is_cli_session)
  4. Watcher detects new sessions inserted into state.db
  5. Settings UI has renamed label

The tests in this file do NOT write to state.db directly. They use the
test-only HTTP seed endpoint /api/_test/seed_session (gated by
HERMES_WEBUI_TEST_SEED=1 in conftest.py) which writes through the SDK's
SessionDB connection. The earlier raw-SQL helper approach was abandoned
because test-side sqlite3 connections race with the server's read paths
(WAL snapshot visibility) and can drop columns the projection requires.
See api/_test_helpers.py for the write contract.
"""
import json
import time
import urllib.error
import urllib.request

import pytest

from tests._pytest_port import BASE


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read()), r.status


def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read()), e.code
        except Exception:
            return {}, e.code


# ── Test seed helpers ──────────────────────────────────────────────────────
# Each helper composes an HTTP request to /api/_test/seed_session and asserts
# 200. The server-side handler calls SessionDB.create_session + append_message
# under api/_test_helpers.py. We intentionally avoid raw sqlite3 here because
# the server's read paths run through their own SDK connections and a test-side
# write can fail to surface due to WAL snapshot visibility.

def _seed(session_id, source, *, title=None, model=None, started_at=None,
          parent_session_id=None, ended_at=None, end_reason=None,
          message_count=None, messages=None):
    """Seed a session row + optional messages through the test-only endpoint."""
    body = {"session_id": session_id, "source": source}
    if title is not None: body["title"] = title
    if model is not None: body["model"] = model
    if started_at is not None: body["started_at"] = started_at
    if parent_session_id is not None: body["parent_session_id"] = parent_session_id
    if ended_at is not None: body["ended_at"] = ended_at
    if end_reason is not None: body["end_reason"] = end_reason
    if message_count is not None: body["message_count"] = message_count
    if messages is not None: body["messages"] = messages
    data, status = post("/api/_test/seed_session", body)
    assert status == 200, f"_seed failed for {session_id}: {status} {data}"
    return data


def _seed_gateway(session_id, source, title, *,
                  model="anthropic/claude-sonnet-4-5",
                  started_at=None, message_count=2):
    """Seed a gateway-style session with a user/assistant message pair (default 2 msgs).

    Mirrors the old _insert_gateway_session helper. By default ``message_count``
    inserts 2 messages AND sets sessions.message_count=2 so the projection's
    actual_message_count matches s.message_count.

    Pass ``message_count=0`` to exercise the watcher's fallback path
    (sessions.message_count stale at 0 but actual_message_count > 0).
    """
    started_at = started_at if started_at is not None else time.time()
    msgs = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"{title} #{i+1}",
             "timestamp": started_at + i} for i in range(message_count)]
    return _seed(session_id, source,
                 title=title, model=model, started_at=started_at,
                 message_count=message_count, messages=msgs)


def _seed_agent_session(session_id, source, title, *,
                        model="openai/gpt-5", started_at=None,
                        parent_session_id=None, ended_at=None,
                        end_reason=None, messages=1):
    """Seed a generic agent session row (compression/branch tests use this).

    Mirrors the old _insert_agent_session_row helper. ``messages`` is the number
    of message rows to insert AND the value stored in sessions.message_count.
    Pass messages=0 to seed a row with no messages (projection filters it out).
    """
    started_at = started_at if started_at is not None else time.time()
    msgs = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"{title} #{i+1}",
             "timestamp": started_at + i} for i in range(messages)]
    return _seed(session_id, source,
                 title=title, model=model, started_at=started_at,
                 parent_session_id=parent_session_id,
                 ended_at=ended_at, end_reason=end_reason,
                 message_count=messages, messages=msgs)


def _remove_sessions(*session_ids):
    """Delete test sessions via the test-only cleanup endpoint."""
    if not session_ids:
        return
    data, status = post("/api/_test/cleanup_sessions",
                        {"session_ids": list(session_ids)})
    assert status == 200, f"_remove_sessions failed: {status} {data}"


# ── Tests ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_state_db():
    """Nuke state.db before each test in this module.

    The seed endpoint below writes through `SessionDB.create_session`,
    which fires messages_fts's AFTER INSERT trigger. After several seed
    round-trips the FTS shadow table becomes unreadable on the testbox's
    overlay filesystem ("database disk image is malformed"), and every
    subsequent seed opens with a one-shot FTS-rebuild that locks up the
    writer. Wiping state.db at the START of each test gives us a virgin
    FTS5 shadow table that never has to repair itself, while leaving
    intra-test parent->child FK chains intact (the nuke runs BEFORE the
    test's first seed, not between them).

    Scoped to this module only — tests in OTHER modules that don't use
    the seed endpoint (e.g. test_session_summary_redaction's
    test_api_sessions_search_redacts_titles) build their own state.db
    schema via raw SQL, which a global nuke would wipe out and break.
    """
    try:
        post('/api/_test/nuke_state_db', {})
    except Exception:
        pass
    yield


def test_gateway_sessions_appear_when_enabled():
    """Gateway sessions from state.db appear in /api/sessions when show_messaging_sessions is on."""
    try:
        _seed_gateway('gw_test_tg_001', 'telegram', 'TG Test Chat')

        # Enable the setting
        post('/api/settings', {'show_messaging_sessions': True})

        data, status = get('/api/sessions')
        assert status == 200
        sessions = data.get('sessions', [])
        gw_ids = [s['session_id'] for s in sessions if s.get('session_id') == 'gw_test_tg_001']
        assert len(gw_ids) == 1, f"Expected gateway session gw_test_tg_001, got {[s['session_id'] for s in sessions]}"
    finally:
        try:
            _remove_sessions('gw_test_tg_001')
        except Exception:
            pass
        post('/api/settings', {'show_messaging_sessions': False})


def test_gateway_sessions_without_messages_are_hidden_from_sidebar():
    """Regression: empty agent session rows must not appear as broken sidebar entries."""
    empty_sid = 'gw_empty_no_messages_001'
    try:
        # Seed an empty session row (no messages) via SDK; the projection's
        # actual_message_count<=0 guard will filter it from the sidebar.
        _seed(empty_sid, 'cron',
              title='Cron Session', model='openai/gpt-5',
              started_at=time.time(), message_count=0)

        post('/api/settings', {'show_cron_sessions': True})

        data, status = get('/api/sessions')
        assert status == 200
        sessions = data.get('sessions', [])
        assert empty_sid not in {s.get('session_id') for s in sessions}, (
            "Agent sessions with no readable message rows should be filtered before "
            "they reach the sidebar; otherwise clicking them fails during import."
        )
    finally:
        try:
            _remove_sessions(empty_sid)
        except Exception:
            pass
        post('/api/settings', {'show_cron_sessions': False})


def test_session_watcher_hides_sessions_without_messages(monkeypatch):
    """Regression: SSE watcher must use the same importable-agent filter."""
    empty_sid = 'gw_empty_watcher_001'
    live_sid = 'gw_live_watcher_001'
    try:
        # Empty row: no messages, no override of message_count -> filtered.
        _seed(empty_sid, 'telegram',
              title='Empty Telegram Session', model='openai/gpt-5',
              started_at=time.time(), message_count=0)
        # Live row: 2 messages present, but force sessions.message_count=0
        # to exercise the watcher's actual_message_count fallback path.
        # We bypass _seed_gateway (whose `message_count=N` arg both creates
        # N message rows AND sets sessions.message_count=N — no way to
        # decouple them). Pass messages=[...] explicitly so the messages
        # table holds 2 rows while sessions.message_count stays 0.
        t0 = time.time()
        _seed(live_sid, 'telegram',
              title='Live Telegram Session', model='openai/gpt-5',
              started_at=t0, message_count=0,
              messages=[
                  {'role': 'user', 'content': 'live #1', 'timestamp': t0},
                  {'role': 'assistant', 'content': 'live #2', 'timestamp': t0 + 1},
              ])

        # The watcher resolves state.db via api.profiles.get_active_hermes_home()
        # which already points at the test state dir under HERMES_HOME=TEST_STATE_DIR,
        # so no monkeypatch is needed any more. The legacy _get_state_db_path
        # import is left intact in case other tests still use it.
        import api.session_watcher as session_watcher

        sessions = session_watcher._get_agent_sessions_from_db()
        ids = {s.get('session_id') for s in sessions}
        live = next((s for s in sessions if s.get('session_id') == live_sid), None)

        assert empty_sid not in ids
        assert live is not None
        assert live.get('message_count') == 2, (
            "Watcher should fall back to actual message rows when stored "
            "message_count is zero, matching the sidebar route."
        )
    finally:
        try:
            _remove_sessions(empty_sid, live_sid)
        except Exception:
            pass


def test_compression_chain_collapses_to_latest_tip_in_sidebar():
    """Show one logical agent conversation for a compression continuation chain."""
    from tests._pytest_port import TEST_STATE_DIR
    ids_to_remove = ('chain_root_001', 'chain_empty_mid_001', 'chain_tip_001')
    t0 = time.time() - 600
    try:
        _seed_agent_session(
            'chain_root_001',
            source='telegram',  # use telegram (not weixin) so it passes default exclude filter
            title='Magazine Style PPT Skill',
            started_at=t0,
            ended_at=t0 + 100,
            end_reason='compression',
            messages=3,
        )
        _seed_agent_session(
            'chain_empty_mid_001',
            source='telegram',
            title='Magazine Style PPT Skill #2',
            started_at=t0 + 101,
            parent_session_id='chain_root_001',
            ended_at=t0 + 200,
            end_reason='compression',
            messages=0,
        )
        _seed_agent_session(
            'chain_tip_001',
            source='telegram',
            title='Magazine Style PPT Skill #3',
            started_at=t0 + 201,
            parent_session_id='chain_empty_mid_001',
            messages=2,
        )

        post('/api/settings', {'show_messaging_sessions': True})
        data, status = get('/api/sessions')
        assert status == 200
        ids = {s.get('session_id') for s in data.get('sessions', [])}
        tip = next((s for s in data.get('sessions', []) if s.get('session_id') == 'chain_tip_001'), None)

        assert 'chain_tip_001' in ids
        assert 'chain_root_001' not in ids
        assert 'chain_empty_mid_001' not in ids
        assert tip is not None
        assert tip.get('title') == 'Magazine Style PPT Skill'
        assert tip.get('message_count') == 2
        # created_at = the chain head's started_at (preserves original conversation date)
        assert abs(tip.get('created_at') - t0) < 0.01
        # updated_at = the tip's last message timestamp so the sidebar entry
        # bubbles to the top by true recency, not by the root's stale activity.
        # tip messages are at t0+201 and t0+202, so last_activity = t0 + 202.
        assert abs(tip.get('updated_at') - (t0 + 202)) < 0.01

        from api.agent_sessions import read_importable_agent_session_rows

        rows = read_importable_agent_session_rows(TEST_STATE_DIR / 'state.db', limit=None)
        projected_tip = next((row for row in rows if row.get('id') == 'chain_tip_001'), None)
        assert projected_tip is not None
        assert projected_tip.get('title') == 'Magazine Style PPT Skill'
        assert projected_tip.get('_lineage_root_id') == 'chain_root_001'
        assert projected_tip.get('_lineage_tip_id') == 'chain_tip_001'
        assert projected_tip.get('_compression_segment_count') == 3
    finally:
        try:
            _remove_sessions(*ids_to_remove)
        except Exception:
            pass
        post('/api/settings', {'show_messaging_sessions': False})


def test_compression_chain_with_empty_latest_tip_falls_back_to_latest_importable_segment():
    """Empty latest tips should not make the whole conversation disappear."""
    ids_to_remove = ('empty_tip_root_001', 'empty_tip_001')
    t0 = time.time() - 500
    try:
        _seed_agent_session(
            'empty_tip_root_001',
            source='telegram',
            title='Long Conversation',
            started_at=t0,
            ended_at=t0 + 100,
            end_reason='compression',
            messages=2,
        )
        _seed_agent_session(
            'empty_tip_001',
            source='telegram',
            title='Long Conversation #2',
            started_at=t0 + 101,
            parent_session_id='empty_tip_root_001',
            messages=0,
        )

        post('/api/settings', {'show_messaging_sessions': True})
        data, status = get('/api/sessions')
        assert status == 200
        ids = {s.get('session_id') for s in data.get('sessions', [])}

        assert 'empty_tip_root_001' in ids
        assert 'empty_tip_001' not in ids
        root = next((s for s in data.get('sessions', []) if s.get('session_id') == 'empty_tip_root_001'), None)
        assert root and root.get('title') == 'Long Conversation'
    finally:
        try:
            _remove_sessions(*ids_to_remove)
        except Exception:
            pass
        post('/api/settings', {'show_messaging_sessions': False})


def test_compression_chain_with_all_empty_segments_is_hidden():
    """A compression chain with no importable segment should not appear."""
    ids_to_remove = ('all_empty_root_001', 'all_empty_tip_001')
    t0 = time.time() - 450
    try:
        _seed_agent_session(
            'all_empty_root_001',
            source='telegram',
            title='Empty Long Conversation',
            started_at=t0,
            ended_at=t0 + 100,
            end_reason='compression',
            messages=0,
        )
        _seed_agent_session(
            'all_empty_tip_001',
            source='telegram',
            title='Empty Long Conversation #2',
            started_at=t0 + 101,
            parent_session_id='all_empty_root_001',
            messages=0,
        )

        post('/api/settings', {'show_messaging_sessions': True})
        data, status = get('/api/sessions')
        assert status == 200
        ids = {s.get('session_id') for s in data.get('sessions', [])}

        assert 'all_empty_root_001' not in ids
        assert 'all_empty_tip_001' not in ids
    finally:
        try:
            _remove_sessions(*ids_to_remove)
        except Exception:
            pass
        post('/api/settings', {'show_messaging_sessions': False})


def test_non_compression_child_is_not_collapsed_into_parent():
    """Parent/child relationships that are not compression continuations stay flat."""
    from tests._pytest_port import TEST_STATE_DIR
    ids_to_remove = ('branch_parent_001', 'branch_child_001')
    t0 = time.time() - 400
    try:
        _seed_agent_session(
            'branch_parent_001',
            source='telegram',
            title='Branch Parent',
            started_at=t0,
            ended_at=t0 + 100,
            end_reason='branched',
            messages=2,
        )
        _seed_agent_session(
            'branch_child_001',
            source='telegram',
            title='Branch Child',
            started_at=t0 + 101,
            parent_session_id='branch_parent_001',
            messages=2,
        )

        from api.agent_sessions import read_importable_agent_session_rows

        rows = read_importable_agent_session_rows(TEST_STATE_DIR / 'state.db', limit=None)
        ids = {row.get('id') for row in rows}

        assert 'branch_parent_001' in ids
        assert 'branch_child_001' in ids
    finally:
        try:
            _remove_sessions(*ids_to_remove)
        except Exception:
            pass


def test_agent_session_limit_applies_after_compression_projection():
    """A long raw chain should count as one logical sidebar row before limiting."""
    from tests._pytest_port import TEST_STATE_DIR
    chain_ids = [f'limit_chain_{i:03d}' for i in range(8)]
    standalone_id = 'limit_standalone_001'
    t0 = time.time() - 300
    try:
        for i, sid in enumerate(chain_ids):
            _seed_agent_session(
                sid,
                source='telegram',
                title=f'Limit Chain #{i + 1}',
                started_at=t0 + i,
                parent_session_id=chain_ids[i - 1] if i else None,
                ended_at=t0 + i + 0.5 if i < len(chain_ids) - 1 else None,
                end_reason='compression' if i < len(chain_ids) - 1 else None,
                messages=1,
            )
        _seed_agent_session(
            standalone_id,
            source='telegram',
            title='Limit Standalone',
            started_at=t0 + 20,
            messages=1,
        )

        from api.agent_sessions import read_importable_agent_session_rows

        rows = read_importable_agent_session_rows(TEST_STATE_DIR / 'state.db', limit=2)
        ids = [row.get('id') for row in rows]

        assert len(rows) == 2
        assert chain_ids[-1] in ids
        assert standalone_id in ids
        assert not any(sid in ids for sid in chain_ids[:-1])
        chain = next(row for row in rows if row.get('id') == chain_ids[-1])
        assert chain.get('title') == 'Limit Chain #1'
        assert chain.get('_lineage_root_id') == chain_ids[0]
        assert chain.get('_compression_segment_count') == len(chain_ids)
    finally:
        try:
            _remove_sessions(*(chain_ids + [standalone_id]))
        except Exception:
            pass


def test_compression_chain_bubbles_to_top_by_tip_activity():
    """An actively-used compression chain must surface in the sidebar by its
    TIP's last activity, not by the (stale) root's last activity.

    Without overriding ``last_activity`` from the tip, a long-running chain
    whose tip is being actively edited NOW would sort by the root's old
    timestamp and fall below recently touched standalone sessions — the
    inverse of what users expect from "Show agent sessions" sorted by
    recency. This regression test pins the override.
    """
    from tests._pytest_port import TEST_STATE_DIR
    ids_to_remove = ('bubble_root_001', 'bubble_tip_001', 'bubble_standalone_001')
    now = time.time()
    # Root started long ago; tip is being edited "now" (very recent message)
    root_started = now - 30 * 86400
    root_ended = now - 28 * 86400
    tip_started = root_ended + 1
    tip_latest_msg = now - 5  # 5 seconds ago — most recent activity in the DB
    # A standalone session active 2 days ago — older than tip, much newer
    # than the root. Without the fix, the chain row sorts by ROOT's age and
    # standalone wins; with the fix, the chain wins.
    standalone_msg = now - 2 * 86400
    try:
        # Use explicit messages list to control per-row message timestamps
        # (the SDK helper honors the timestamp field on each message dict).
        _seed('bubble_root_001', 'telegram',
              title='Bubble Root',
              started_at=root_started,
              ended_at=root_ended,
              end_reason='compression',
              messages=[{'role': 'user', 'content': 'old root msg',
                         'timestamp': root_started + 60}])
        _seed('bubble_tip_001', 'telegram',
              title='Bubble Tip',
              started_at=tip_started,
              parent_session_id='bubble_root_001',
              messages=[{'role': 'user', 'content': 'fresh tip msg',
                         'timestamp': tip_latest_msg}])
        _seed('bubble_standalone_001', 'telegram',
              title='Bubble Standalone',
              started_at=now - 2 * 86400 - 60,
              messages=[{'role': 'user', 'content': 'standalone msg',
                         'timestamp': standalone_msg}])

        from api.agent_sessions import read_importable_agent_session_rows

        rows = read_importable_agent_session_rows(TEST_STATE_DIR / 'state.db', limit=200)
        ids = [row.get('id') for row in rows]
        # Filter out unrelated rows from the shared DB
        ids = [i for i in ids if i in ('bubble_root_001', 'bubble_tip_001', 'bubble_standalone_001')]

        assert 'bubble_tip_001' in ids, (
            f"Compression tip must appear in projected output. ids={ids}"
        )
        assert 'bubble_root_001' not in ids, (
            "Compression root row must be hidden once the tip is the active row."
        )

        tip_pos = ids.index('bubble_tip_001')
        standalone_pos = ids.index('bubble_standalone_001') if 'bubble_standalone_001' in ids else -1
        assert standalone_pos == -1 or tip_pos < standalone_pos, (
            f"Active compression tip (last msg 5s ago) must sort BEFORE standalone "
            f"session (last msg 2d ago). Got order: {ids}. "
            f"This indicates merged.last_activity is the root's stale value, "
            f"not the tip's recent value."
        )

        tip_row = next(r for r in rows if r['id'] == 'bubble_tip_001')
        assert abs(tip_row['last_activity'] - tip_latest_msg) < 0.01, (
            f"Projected tip's last_activity must equal the tip's most recent "
            f"message timestamp ({tip_latest_msg}), not the root's "
            f"({root_started + 60}). Got: {tip_row['last_activity']}"
        )
    finally:
        try:
            _remove_sessions(*ids_to_remove)
        except Exception:
            pass


def test_gateway_sessions_excluded_when_disabled():
    """Gateway sessions are NOT returned when show_messaging_sessions is off."""
    try:
        _seed_gateway('gw_test_dc_001', 'discord', 'DC Test Chat')

        # Ensure setting is off
        post('/api/settings', {'show_messaging_sessions': False})

        data, status = get('/api/sessions')
        assert status == 200
        sessions = data.get('sessions', [])
        gw_ids = [s['session_id'] for s in sessions if s.get('session_id') == 'gw_test_dc_001']
        assert len(gw_ids) == 0, "Gateway session should not appear when setting is off"
    finally:
        try:
            _remove_sessions('gw_test_dc_001')
        except Exception:
            pass


def test_gateway_session_has_correct_metadata():
    """Gateway sessions include source_tag and is_cli_session fields."""
    try:
        _seed_gateway('gw_meta_001', 'telegram', 'Meta Test')

        post('/api/settings', {'show_messaging_sessions': True})

        data, status = get('/api/sessions')
        assert status == 200
        sessions = data.get('sessions', [])
        gw = next((s for s in sessions if s['session_id'] == 'gw_meta_001'), None)
        assert gw is not None, "Gateway session not found"
        assert gw.get('source_tag') == 'telegram', f"Expected source_tag=telegram, got {gw.get('source_tag')}"
        assert gw.get('is_cli_session') is True, "is_cli_session should be True for agent sessions"
        assert gw.get('title') == 'Meta Test'
    finally:
        try:
            _remove_sessions('gw_meta_001')
        except Exception:
            pass
        post('/api/settings', {'show_messaging_sessions': False})


def test_imported_cron_sessions_hidden_from_sidebar_by_default(cleanup_test_sessions):
    """Cron sessions already imported into the WebUI store should stay hidden from the sidebar."""
    from api.models import Session

    sid = 'cron_imported_20260427'
    cleanup_test_sessions.append(sid)
    s = Session(
        session_id=sid,
        title='Hourly Cron Import',
        messages=[{'role': 'user', 'content': 'run hourly job', 'timestamp': time.time()}],
        model='openai/gpt-5',
    )
    s.is_cli_session = True
    # `save()` was renamed to `sync_webui_metadata` in the Session refactor;
    # this test predates that rename and previously crashed with
    # AttributeError: 'Session' object has no attribute 'save'.
    s.sync_webui_metadata(touch_updated_at=False)

    data, status = get('/api/sessions')
    assert status == 200
    assert sid not in {session.get('session_id') for session in data.get('sessions', [])}


def test_cron_sessions_hidden_from_sidebar_by_default():
    """Cron-run sessions are background/internal and should not appear in the default sidebar list."""
    try:
        _seed_gateway('cron_job123_20260427', 'cron', 'Nightly Cleanup')
        _seed_gateway('gw_noncron_001', 'telegram', 'Visible Chat')

        post('/api/settings', {'show_messaging_sessions': True})

        data, status = get('/api/sessions')
        assert status == 200
        sessions = data.get('sessions', [])
        ids = {s['session_id'] for s in sessions}
        assert 'gw_noncron_001' in ids, "Non-cron agent session should still appear"
        assert 'cron_job123_20260427' not in ids, "Cron session should be hidden by default"
    finally:
        try:
            _remove_sessions('cron_job123_20260427', 'gw_noncron_001')
        except Exception:
            pass
        post('/api/settings', {'show_messaging_sessions': False})


def test_importable_agent_rows_can_opt_into_cron_source():
    """Diagnostic callers can opt out of the default cron exclusion explicitly."""
    from tests._pytest_port import TEST_STATE_DIR
    try:
        _seed_agent_session('cron_diag_20260427', 'cron', 'Cron Diagnostic')

        from api.agent_sessions import read_importable_agent_session_rows

        default_rows = read_importable_agent_session_rows(TEST_STATE_DIR / 'state.db', limit=None)
        assert 'cron_diag_20260427' not in {row.get('id') for row in default_rows}

        diagnostic_rows = read_importable_agent_session_rows(
            TEST_STATE_DIR / 'state.db',
            limit=None,
            exclude_sources=None,
        )
        assert 'cron_diag_20260427' in {row.get('id') for row in diagnostic_rows}
    finally:
        try:
            _remove_sessions('cron_diag_20260427')
        except Exception:
            pass


def test_gateway_session_has_message_count():
    """Gateway sessions report correct message_count from state.db."""
    try:
        # Insert 5 messages AND set sessions.message_count=5 to pin the
        # contract: s.message_count is preferred over actual_message_count.
        _seed_gateway('gw_msg_001', 'discord', 'Msg Count Test', message_count=5)

        post('/api/settings', {'show_messaging_sessions': True})

        data, status = get('/api/sessions')
        assert status == 200
        sessions = data.get('sessions', [])
        gw = next((s for s in sessions if s['session_id'] == 'gw_msg_001'), None)
        assert gw is not None
        assert gw.get('message_count') == 5, f"Expected message_count=5, got {gw.get('message_count')}"
    finally:
        try:
            _remove_sessions('gw_msg_001')
        except Exception:
            pass
        post('/api/settings', {'show_messaging_sessions': False})


def test_gateway_sessions_multiple_sources():
    """Sessions from multiple gateway sources (telegram, discord, slack) all appear."""
    try:
        _seed_gateway('gw_multi_tg', 'telegram', 'TG Chat')
        _seed_gateway('gw_multi_dc', 'discord', 'DC Chat')
        _seed_gateway('gw_multi_sl', 'slack', 'SL Chat')

        post('/api/settings', {'show_messaging_sessions': True})

        data, status = get('/api/sessions')
        assert status == 200
        sessions = data.get('sessions', [])
        gw_ids = {s['session_id'] for s in sessions if s.get('session_id') in ('gw_multi_tg', 'gw_multi_dc', 'gw_multi_sl')}
        assert len(gw_ids) == 3, f"Expected 3 gateway sessions, got {len(gw_ids)}: {gw_ids}"
    finally:
        try:
            _remove_sessions('gw_multi_tg', 'gw_multi_dc', 'gw_multi_sl')
        except Exception:
            pass
        post('/api/settings', {'show_messaging_sessions': False})


def test_gateway_session_messages_readable():
    """Gateway session messages can be loaded via /api/session."""
    try:
        _seed_gateway('gw_read_001', 'telegram', 'Readable')

        post('/api/settings', {'show_messaging_sessions': True})

        data, status = get(f'/api/session?session_id=gw_read_001')
        assert status == 200
        msgs = data.get('session', {}).get('messages', [])
        assert len(msgs) >= 2, f"Expected at least 2 messages, got {len(msgs)}"
        assert msgs[0].get('role') == 'user'
    finally:
        try:
            _remove_sessions('gw_read_001')
        except Exception:
            pass
        post('/api/settings', {'show_messaging_sessions': False})


def test_importing_older_gateway_session_preserves_original_timestamps_and_order():
    """Importing an older gateway session should not bump it above newer WebUI sessions."""
    older_started_at = time.time() - 1800
    imported_sid = 'gw_import_old_001'
    newer_webui_sid = None
    try:
        newer_webui, status = post('/api/session/new', {'model': 'openai/gpt-5'})
        assert status == 200, newer_webui
        newer_webui_sid = newer_webui['session']['session_id']

        rename, rename_status = post(
            '/api/session/rename',
            {'session_id': newer_webui_sid, 'title': 'Newer WebUI Session'},
        )
        assert rename_status == 200, rename

        _seed_gateway(imported_sid, 'discord', 'Older imported gateway session',
                      started_at=older_started_at)
        post('/api/settings', {'show_messaging_sessions': True})

        imported, imported_status = post('/api/session/import_cli', {'session_id': imported_sid})
        assert imported_status == 200, imported
        imported_session = imported['session']
        assert abs(imported_session['created_at'] - older_started_at) < 2, imported_session
        assert abs(imported_session['updated_at'] - older_started_at) < 5, imported_session

        sessions_payload, sessions_status = get('/api/sessions')
        assert sessions_status == 200, sessions_payload
        ordered_ids = [item['session_id'] for item in sessions_payload.get('sessions', [])]
        assert newer_webui_sid in ordered_ids, ordered_ids
        assert imported_sid in ordered_ids, ordered_ids
        assert ordered_ids.index(newer_webui_sid) < ordered_ids.index(imported_sid), ordered_ids
    finally:
        try:
            _remove_sessions(imported_sid)
        except Exception:
            pass
        if imported_sid:
            try:
                post('/api/session/delete', {'session_id': imported_sid})
            except Exception:
                pass
        if newer_webui_sid:
            try:
                post('/api/session/delete', {'session_id': newer_webui_sid})
            except Exception:
                pass
        post('/api/settings', {'show_messaging_sessions': False})



# ── DELETED: test_gateway_sse_stream_endpoint_exists ──
# The /api/sessions/gateway/stream SSE endpoint was removed; real-time session
# notifications are now delivered through notify_stream + WebSocket at
# /api/ws_events. Tests asserting against the deleted endpoint were removed
# alongside it; this comment is left so the gap is visible in code review.
#
# ── DELETED: test_gateway_sse_stream_probe_reports_status ──
# Same reason — the endpoint no longer exists, so the probe mode does too.


def test_gateway_webui_sessions_not_duplicated():
    """If a session_id exists both in WebUI store and state.db, it's not duplicated."""
    # Create a WebUI session with a known ID
    body = {}
    d, _ = post('/api/session/new', body)
    webui_sid = d['session']['session_id']

    try:
        # Insert the same session_id into state.db as a gateway session
        _seed_gateway(webui_sid, 'telegram', 'Dup Test')

        post('/api/settings', {'show_messaging_sessions': True})

        data, status = get('/api/sessions')
        assert status == 200
        sessions = data.get('sessions', [])
        matching = [s for s in sessions if s['session_id'] == webui_sid]
        assert len(matching) == 1, f"Expected 1 entry for {webui_sid}, got {len(matching)}"
    finally:
        try:
            _remove_sessions(webui_sid)
        except Exception:
            pass
        post('/api/session/delete', {'session_id': webui_sid})
        post('/api/settings', {'show_messaging_sessions': False})


def test_gateway_sessions_no_state_db():
    """When state.db doesn't exist, /api/sessions works fine (no gateway sessions).

    Note: the original implementation deleted state.db on disk to test the
    missing-file path. With the SDK-based seeder we can't easily delete the
    server-side DB without restarting, so this test now relies on the empty
    baseline at session start (conftest wipes TEST_STATE_DIR). The contract
    being asserted (server survives a missing state.db) is implicitly
    covered by /health and the smoke suite; we keep this test as a smoke
    check on the toggle behavior with no rows present.
    """
    post('/api/settings', {'show_cli_sessions': True})
    try:
        data, status = get('/api/sessions')
        assert status == 200
        # Should succeed with just webui sessions (or empty)
        assert 'sessions' in data
    finally:
        post('/api/settings', {'show_cli_sessions': False})


def test_cli_sessions_still_work():
    """CLI sessions (source='cli') appear alongside messaging sessions when both toggles are on."""
    try:
        _seed_gateway('cli_legacy_001', 'cli', 'CLI Legacy')
        _seed_gateway('gw_new_001', 'telegram', 'GW New')

        post('/api/settings', {'show_cli_sessions': True, 'show_messaging_sessions': True})

        data, status = get('/api/sessions')
        assert status == 200
        sessions = data.get('sessions', [])
        agent_ids = {s['session_id'] for s in sessions if s.get('session_id') in ('cli_legacy_001', 'gw_new_001')}
        assert len(agent_ids) == 2, f"Expected 2 agent sessions (cli + messaging), got {len(agent_ids)}"
    finally:
        try:
            _remove_sessions('cli_legacy_001', 'gw_new_001')
        except Exception:
            pass
        post('/api/settings', {'show_cli_sessions': False, 'show_messaging_sessions': False})


def test_cli_sessions_hidden_when_cli_toggle_off():
    """CLI sessions (source='cli') are hidden when show_cli_sessions is off,
    while messaging sessions remain visible."""
    try:
        _seed_gateway('cli_hidden_001', 'cli', 'Hidden CLI')
        _seed_gateway('msg_visible_001', 'telegram', 'Visible MSG')

        post('/api/settings', {'show_cli_sessions': False, 'show_messaging_sessions': True})

        data, status = get('/api/sessions')
        assert status == 200
        ids = {s['session_id'] for s in data.get('sessions', [])}
        assert 'cli_hidden_001' not in ids, "CLI session should be hidden when show_cli_sessions is off"
        assert 'msg_visible_001' in ids, "Messaging session should still appear"
    finally:
        try:
            _remove_sessions('cli_hidden_001', 'msg_visible_001')
        except Exception:
            pass
        post('/api/settings', {'show_cli_sessions': True, 'show_messaging_sessions': True})


def test_cron_sessions_appear_when_enabled():
    """Cron sessions appear in the sidebar when show_cron_sessions is enabled."""
    try:
        _seed_gateway('cron_toggle_001', 'cron', 'Visible Cron')

        post('/api/settings', {'show_cron_sessions': True})

        data, status = get('/api/sessions')
        assert status == 200
        ids = {s['session_id'] for s in data.get('sessions', [])}
        assert 'cron_toggle_001' in ids, "Cron session should appear when show_cron_sessions is on"
    finally:
        try:
            _remove_sessions('cron_toggle_001')
        except Exception:
            pass
        post('/api/settings', {'show_cron_sessions': False})

