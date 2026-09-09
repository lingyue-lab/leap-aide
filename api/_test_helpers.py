"""Test-only seed helpers for writing agent/gateway/cron sessions into state.db.

These helpers exist so test_gateway_sync.py (and similar tests) can populate
state.db through the SDK's high-level entry points — `SessionDB.create_session`
and `SessionDB.append_message` — instead of opening a fresh `sqlite3.connect()`
and hand-writing INSERTs that may not match the SDK's evolving schema.

CLAUDE.md says webui must not modify the hermes-agent SDK and must use its
high-level API for any state.db writes. The SDK only ships `create_session` /
`append_message` / `end_session`; for fields they don't expose
(`started_at`, `title`, `ended_at`, `end_reason`, message timestamps) we drop
to `db._conn.execute(...)` rather than opening a separate sqlite3 connection —
writes still flow through the SDK's connection and don't race with WAL
snapshots. This is a deliberate, narrowly-scoped exception; production code
must not import this module.

Activation: this module is a no-op unless `HERMES_WEBUI_TEST_SEED=1` is set in
the server subprocess's environment. conftest.py sets the env var when spawning
the test server, so the helpers are wired up only during test runs. In
production, all helpers raise `RuntimeError`.

References:
  - hermes_state.SessionDB.create_session (hermes_state.py:1206)
  - hermes_state.SessionDB._insert_session_row (hermes_state.py:1175)
  - hermes_state.SessionDB.append_message (hermes_state.py:2266)
  - hermes_state.SessionDB.end_session (hermes_state.py:1210)
"""
import logging
import os
import time as _time
from pathlib import Path

logger = logging.getLogger(__name__)
TEST_SEED_ENABLED = os.environ.get("HERMES_WEBUI_TEST_SEED") == "1"


def _require_enabled() -> None:
    if not TEST_SEED_ENABLED:
        raise RuntimeError(
            "test seeding disabled: HERMES_WEBUI_TEST_SEED must be set to 1 "
            "in the server's environment to use api/_test_helpers.py"
        )


def _get_session_db():
    """Lazy-import + open SessionDB pointing at the runtime state.db."""
    from hermes_state import SessionDB
    from api.profiles import get_active_hermes_home
    db_path = Path(get_active_hermes_home()).expanduser().resolve() / "state.db"
    return SessionDB(db_path)


def nuke_test_state_db() -> None:
    """Delete state.db (+ WAL/SHM/journal sidecars) before a test starts.

    Tests in this module share one server process and one state.db across the
    whole pytest session. After the first few seeds, the messages_fts shadow
    table becomes unreadable on the testbox's overlay filesystem — every
    subsequent SessionDB open and INSERT raises "database disk image is
    malformed", and the SDK's runtime FTS-rebuild-and-retry path keeps firing
    but never escapes the corruption cascade. Wiping the file at the START of
    each test gives every test a virgin FTS5 shadow table that never has to
    repair itself, which keeps the seed round-trip deterministic.

    Within a single test, multiple `_seed` calls build parent→child chains
    via `parent_session_id`. Nuking is therefore per-TEST, not per-SEED —
    done by the `cleanup_test_sessions` autouse fixture in conftest.py
    AFTER each test finishes, so the next test starts on a clean DB while
    the current test's seed chain remains internally consistent.

    Also resets the server-side ``SessionDB`` singleton so subsequent
    requests reopen state.db on the freshly-created inode. Without this,
    the singleton's connection still points at the (now-unlinked) previous
    file and writes land on a phantom inode invisible to the test process's
    direct sqlite3 connection.

    No-op when HERMES_WEBUI_TEST_SEED is not set (production safety belt).
    """
    _require_enabled()
    db_path = Path(_active_hermes_home()).expanduser().resolve() / "state.db"
    for suffix in ("", "-wal", "-shm", "-journal"):
        sidecar = db_path.with_name(db_path.name + suffix)
        try:
            if sidecar.exists():
                sidecar.unlink()
        except OSError:
            # If we can't unlink (fd held by another process), truncate
            # instead so SQLite at least sees a zero-length file. The
            # holder's next read returns EOF and closes cleanly.
            try:
                with open(sidecar, "wb") as fh:
                    fh.truncate(0)
            except OSError:
                pass

    # ── Reset the server-side SessionDB singleton ────────────────────
    # The singleton's sqlite3 connection still references the now-unlinked
    # inode; close + discard it so the next _get_session_db() call opens a
    # fresh connection against the newly-created state.db file.
    try:
        from api.models import _session_db, _session_db_lock
        with _session_db_lock:
            if _session_db is not None:
                try:
                    _session_db._conn.close()
                except Exception:
                    pass
                # Mark the singleton as None so the next caller reopens.
                import api.models as _api_models
                _api_models._session_db = None
    except Exception:
        pass


def _active_hermes_home() -> str:
    """Local helper to resolve HERMES_HOME for nuke_test_state_db()."""
    from api.profiles import get_active_hermes_home
    return get_active_hermes_home()


def seed_test_session(
    session_id: str,
    source: str,
    *,
    title: str | None = None,
    model: str | None = None,
    started_at: float | None = None,
    parent_session_id: str | None = None,
    ended_at: float | None = None,
    end_reason: str | None = None,
    message_count: int | None = None,
    messages: list | None = None,
) -> dict:
    """Insert a session row + optional messages into state.db via SDK API.

    Mirrors what real gateway / cron / CLI integrations do when they create a
    session: go through `SessionDB.create_session` (which uses the SDK's
    connection and `_execute_write` BEGIN IMMEDIATE transaction wrapper).

    Fields the SDK doesn't expose in `create_session` (title, started_at,
    ended_at, end_reason, custom message timestamps) are written via
    `db._conn.execute(...)` so the writes still flow through the SDK's
    connection — avoids the WAL visibility issue where tests open a separate
    sqlite3 connection and the server's readers don't see the rows.

    Args:
        session_id:    primary key, e.g. 'gw_test_tg_001'
        source:        session source tag (telegram, discord, cli, cron, ...)
        title:         human-readable title (written via UPDATE; not in create_session)
        model:         model identifier
        started_at:    unix timestamp (defaults to time.time() via create_session
                       when None; we UPDATE after to override)
        parent_session_id: lineage link for compression chains / branches
        ended_at:      end timestamp (compression / branch / completion)
        end_reason:    one of 'compression', 'branched', 'completed', 'error', ...
        message_count: explicit value for sessions.message_count column.
                       - None  → auto-derive from len(messages) so the column
                                  matches the messages we just inserted.
                       - 0     → force a stale zero (used to exercise the
                                  watcher's fallback to actual_message_count).
                       - >0    → force a specific value (used to test that
                                  s.message_count is preferred over actual_message_count).
        messages:      list of dicts {'role', 'content', 'timestamp'}; timestamp
                       defaults to started_at or time.time() if missing

    Returns:
        Small diagnostic dict: session_id, source, db_path, message_count.
    """
    _require_enabled()
    db = _get_session_db()

    # Drop any pre-existing session + messages for idempotent re-seeding.
    db._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    db._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    db._conn.commit()

    # 1) SDK high-level: insert the session. create_session uses INSERT OR IGNORE
    #    and only accepts a small subset of kwargs (parent_session_id, model,
    #    user_id, model_config, system_prompt, cwd). Anything else we UPDATE below.
    db.create_session(
        session_id,
        source,
        parent_session_id=parent_session_id,
        model=model,
    )

    # 2) SDK high-level gap: started_at, title, ended_at, end_reason.
    #    UPDATE through the SDK's connection (not a fresh sqlite3.connect).
    update_fields = []
    update_values = []
    if started_at is not None:
        update_fields.append("started_at = ?")
        update_values.append(started_at)
    if title is not None:
        update_fields.append("title = ?")
        update_values.append(title)
    if ended_at is not None:
        update_fields.append("ended_at = ?")
        update_values.append(ended_at)
    if end_reason is not None:
        update_fields.append("end_reason = ?")
        update_values.append(end_reason)
    if update_fields:
        update_values.append(session_id)
        db._conn.execute(
            f"UPDATE sessions SET {', '.join(update_fields)} WHERE id = ?",
            update_values,
        )

    # 3) Messages: insert via the SDK's connection so `active` (NOT NULL
    #    DEFAULT 1) and the schema's other defaults are honored. We do not use
    #    append_message() because it hardcodes `timestamp = time.time()` and
    #    the tests need historical / future timestamps for compression
    #    lineage scenarios. Writing via the SDK's connection preserves schema
    #    correctness (triggers / FTS / indexes) without racing with WAL.
    inserted = 0
    if message_count is not None and not messages:
        # Caller wants sessions.message_count overridden but didn't pass any
        # messages — write the value directly so the projection sees it.
        # (E.g. a regression test verifying that a stale-zero session with
        # no real messages is filtered out by the projection.)
        db._conn.execute(
            "UPDATE sessions SET message_count = ? WHERE id = ?",
            (message_count, session_id),
        )
    if messages:
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            timestamp = (
                m.get("timestamp")
                if m.get("timestamp") is not None
                else (started_at if started_at is not None else _time.time())
            )
            db._conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, active, observed) "
                "VALUES (?, ?, ?, ?, 1, 0)",
                (session_id, role, content, timestamp),
            )
            inserted += 1
        # Keep sessions.message_count in sync. Three modes:
        #   - message_count is None: auto-derive = inserted (mirrors append_message)
        #   - message_count >= 0 (explicit): use the override verbatim, including 0
        #     (used by tests that need a stale-zero sessions.message_count while
        #      still having actual_message_count > 0 to exercise the fallback path)
        if message_count is None:
            db._conn.execute(
                "UPDATE sessions SET message_count = message_count + ? WHERE id = ?",
                (inserted, session_id),
            )
        else:
            db._conn.execute(
                "UPDATE sessions SET message_count = ? WHERE id = ?",
                (message_count, session_id),
            )

    db._conn.commit()

    # Read back the final sessions.message_count so the diagnostic accurately
    # reflects what the server's projection will see.
    final_row = db._conn.execute(
        "SELECT message_count FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    final_message_count = final_row[0] if final_row else inserted

    return {
        "session_id": session_id,
        "source": source,
        "db_path": str(db._conn.execute("PRAGMA database_list").fetchone()[2]),
        "message_count": final_message_count,
        "inserted_messages": inserted,
    }


def cleanup_test_sessions(session_ids: list[str]) -> dict:
    """Delete sessions and their messages via the SDK's connection.

    Parallel-safe: deletes only the listed ids, so concurrent tests are not
    affected. Mirrors the pattern in the original raw-SQL helpers but goes
    through the SDK connection instead of a fresh sqlite3.connect().
    """
    _require_enabled()
    if not session_ids:
        return {"deleted": 0}
    db = _get_session_db()
    deleted = 0
    for sid in session_ids:
        try:
            db._conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
            db._conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            deleted += 1
        except Exception as e:
            logger.warning("cleanup_test_sessions: failed to delete %s: %s", sid, e)
    db._conn.commit()
    return {"deleted": deleted}