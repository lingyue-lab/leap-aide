"""Tests: _get_all_delegate_children_map merges in-memory placeholder children.

Regression for Issue #77: the sidebar's delegate-children list read only
state.db, so subagent sessions that existed only as in-memory placeholders
(created by subagent.start, not yet persisted / not yet carrying the
_delegate_from marker) were invisible until a full page refresh, while the
delegate badge (which reads the SESSIONS cache) already showed them.

These tests call the function directly against a minimal in-memory sqlite
schema: the function only uses db._conn.execute(...) and sqlite3.Row-style
rows. Cache entries are injected as types.SimpleNamespace objects into THIS
test process's models.SESSIONS (the function reads the module-level
global) and cleaned up afterwards.
"""

import sqlite3
import types

from app.api import models


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, model TEXT, "
        "started_at TEXT, ended_at TEXT, model_config TEXT, parent_session_id TEXT)"
    )
    conn.execute("CREATE TABLE messages (session_id TEXT, role TEXT)")
    return types.SimpleNamespace(_conn=conn)


def _cache_child(sid, parent, **kw):
    # No started_at in the base dict: real placeholder Sessions only carry
    # created_at (Session.__init__), never started_at — so the merge's
    # started_at fallback must fire for cache-only children.
    base = dict(session_id=sid, parent_session_id=parent, is_subagent_session=True,
                title="cached-title", model="m",
                ended_at=None, messages=[])
    base.update(kw)
    return types.SimpleNamespace(**base)


def _seed_delegate_row(db, sid, parent, title="db-title"):
    db._conn.execute(
        "INSERT INTO sessions (id, title, model, started_at, ended_at, model_config, parent_session_id) "
        "VALUES (?, ?, 'db-model', '2026-11-24T00:00:01', NULL, ?, ?)",
        (sid, title, '{"_delegate_from": "main-1"}', parent),
    )


def test_cache_only_child_appears():
    db = _make_db()
    # NOTE: do NOT hold models.LOCK across the call — LOCK is a non-reentrant
    # threading.Lock and the implementation acquires it internally.
    models.SESSIONS["ph-1"] = _cache_child("ph-1", "p-1")
    try:
        got = models._get_all_delegate_children_map(db)["p-1"]
    finally:
        del models.SESSIONS["ph-1"]
    assert [c["id"] for c in got] == ["ph-1"]
    assert got[0]["title"] == "cached-title"
    assert got[0]["ended_at"] is None
    assert isinstance(got[0]["message_count"], int) and "parent_session_id" not in got[0]
    # Synthesized started_at fallback must be a unix float: state.db stores
    # REAL, frontend types started_at as number (types/index.ts), and
    # routes.py clear-delegates sorts with c.get('started_at', 0) — a str
    # fallback would TypeError there and silently no-op.
    assert isinstance(got[0]["started_at"], float)


def test_db_row_wins_on_id_collision():
    db = _make_db()
    _seed_delegate_row(db, "dup-1", "p-1", title="db-title")
    models.SESSIONS["dup-1"] = _cache_child("dup-1", "p-1", title="cache-title")
    try:
        got = models._get_all_delegate_children_map(db)["p-1"]
    finally:
        del models.SESSIONS["dup-1"]
    assert len(got) == 1
    assert got[0]["title"] == "db-title"          # DB row fields win
    assert got[0]["model"] == "db-model"


def test_persisted_row_without_delegate_marker_stays_excluded():
    db = _make_db()
    db._conn.execute(
        "INSERT INTO sessions (id, title, model, started_at, ended_at, model_config, parent_session_id) "
        "VALUES ('cont-1', 'x', 'm', '2026-11-24T00:00:02', NULL, '{}', 'p-1')"
    )  # compressed continuation: row exists, no _delegate_from marker
    models.SESSIONS["cont-1"] = _cache_child("cont-1", "p-1")
    try:
        got = models._get_all_delegate_children_map(db).get("p-1", [])
    finally:
        del models.SESSIONS["cont-1"]
    assert got == []   # filter semantics preserved: existing rows never re-enter via cache


def test_unrelated_cache_children_excluded():
    db = _make_db()
    models.SESSIONS["other"] = _cache_child("other", "p-OTHER")
    models.SESSIONS["notsub"] = _cache_child("notsub", "p-1", is_subagent_session=False)
    try:
        got = models._get_all_delegate_children_map(db).get("p-1", [])
    finally:
        del models.SESSIONS["other"]
        del models.SESSIONS["notsub"]
    assert got == []


def test_db_only_response_unchanged():
    db = _make_db()
    _seed_delegate_row(db, "keep-1", "p-1")
    db._conn.execute(
        "INSERT INTO sessions (id, title, model, started_at, ended_at, model_config, parent_session_id) "
        "VALUES ('skip-1', 'y', 'm', '2026-11-24T00:00:03', NULL, '{}', 'p-1')"
    )  # no _delegate_from marker
    got = models._get_all_delegate_children_map(db)["p-1"]
    assert [c["id"] for c in got] == ["keep-1"]   # same as pre-fix behavior


def test_message_count_counts_user_and_assistant_only():
    db = _make_db()
    models.SESSIONS["ph-2"] = _cache_child(
        "ph-2", "p-1",
        messages=[{"role": "user"}, {"role": "assistant"},
                  {"role": "tool"}, {"role": "system"}],
    )
    try:
        got = models._get_all_delegate_children_map(db)["p-1"]
    finally:
        del models.SESSIONS["ph-2"]
    assert got[0]["message_count"] == 2
