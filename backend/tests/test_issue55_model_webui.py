"""Regression tests for issue #55 — persist the WebUI model identity.

Background
----------
The WebUI presents model ids as ``@provider:model``, but that prefix is not
what the SDK stores: ``resolve_model_provider`` strips it before handing the
model to ``AIAgent``, and the SDK writes the bare ``agent.model`` into
``state.db.sessions.model``.  After a stream ends the in-memory session is
reloaded from the DB, so a session could lose its provider hint between turns
and the next prompt fell through to config.yaml's ``provider: auto`` +
OpenRouter base URL (HTTP 401 — the issue #55 symptom).

The fix records the user's selection in a WebUI-owned ``model__webui`` column.
This file pins the contract:

* ``_from_db_row`` prefers ``model__webui`` and marks the session invalid when
  the column is NULL (no heuristic reconstruction from SDK columns).
* ``_sync_to_db`` writes ``model__webui`` (NULL for invalid/unknown models).
* ``Session.compact()`` / ``copy_from()`` / ``clone()`` carry ``model_invalid``.
* The display resolver and chat start never strip or rewrite a stored
  ``@provider:model`` identity, and chat start rejects invalid sessions.
* ``ensure_webui_columns`` adds the column to existing databases.
"""
import sqlite3
import uuid

import pytest

import app.api.config as config
import app.api.models as models
import app.api.routes as routes
from app.api.models import Session
from app.api.webui_schema import WEBUI_EXPECTED_COLUMNS, ensure_webui_columns
from tests._test_helpers import get_body, post

STOCK_CATALOG = {
    "active_provider": "auto",
    "default_model": "deepseek-v4-pro",
    "groups": [],
}


@pytest.fixture
def empty_catalog(monkeypatch):
    """Incident-shaped catalog: no live groups to normalize against."""
    monkeypatch.setattr(config, "peek_available_models", lambda **kw: dict(STOCK_CATALOG))
    monkeypatch.setattr(routes, "peek_available_models", lambda **kw: dict(STOCK_CATALOG))
    return STOCK_CATALOG


def _row(**overrides):
    """Minimal sessions-table row as returned by SessionDB.get_session()."""
    row = {
        "id": "abc123def456",
        "title": "test",
        "cwd": "/tmp",
        "started_at": 1000.0,
        "last_activity_at": 1000.0,
        "source": "webui",
    }
    row.update(overrides)
    return row


# ── Schema ─────────────────────────────────────────────────────────────────


def test_webui_schema_declares_model_column():
    assert WEBUI_EXPECTED_COLUMNS["model__webui"] == "TEXT"


def test_ensure_webui_columns_adds_model_column():
    class _DB:
        def __init__(self, conn):
            self._conn = conn

        def _execute_write(self, fn):
            return fn(self._conn)

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        ensure_webui_columns(_DB(conn))
        columns = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        assert "model__webui" in columns
    finally:
        conn.close()


# ── _from_db_row: model__webui is authoritative ─────────────────────────────


def test_from_db_row_prefers_model_webui_over_sdk_model():
    session = Session._from_db_row(
        _row(model="deepseek-v4-flash", model__webui="@deepseek:deepseek-v4-flash")
    )
    assert session.model == "@deepseek:deepseek-v4-flash"
    assert session.model_invalid is False


def test_from_db_row_null_model_webui_marks_invalid():
    session = Session._from_db_row(_row(model="deepseek-v4-flash", model__webui=None))
    assert session.model_invalid is True
    # The SDK value may still be shown for reference, but it is not identity.
    assert session.model == "deepseek-v4-flash"


def test_from_db_row_missing_model_webui_marks_invalid():
    session = Session._from_db_row(_row(model=None))
    assert session.model_invalid is True


@pytest.mark.parametrize("stored", ["", "   ", "unknown", "UNKNOWN"])
def test_from_db_row_blank_or_unknown_model_webui_marks_invalid(stored):
    session = Session._from_db_row(_row(model="deepseek-v4-flash", model__webui=stored))
    assert session.model_invalid is True


def test_from_db_row_does_not_leak_model_webui_kwarg():
    # model__webui must map to the model fields, not survive as an extra attr.
    session = Session._from_db_row(
        _row(model="deepseek-v4-flash", model__webui="@deepseek:deepseek-v4-flash")
    )
    assert not hasattr(session, "model__webui")


# ── compact / copy_from / clone ─────────────────────────────────────────────


def test_compact_includes_model_invalid():
    valid = Session(session_id="abc123def456", model="@deepseek:deepseek-v4-flash")
    assert valid.compact()["model_invalid"] is False
    valid.model_invalid = True
    assert valid.compact()["model_invalid"] is True


def test_copy_from_propagates_model_invalid():
    stale = Session(session_id="abc123def456", model="@old:x")
    stale.model_invalid = True
    fresh = Session(session_id="abc123def456", model="@new:y")
    stale.copy_from(fresh)
    assert stale.model_invalid is False
    assert stale.model == "@new:y"


def test_clone_propagates_model_invalid():
    session = Session(session_id="abc123def456", model="")
    session.model_invalid = True
    assert session.clone().model_invalid is True


# ── _sync_to_db: writes model__webui ────────────────────────────────────────


class _FakeConn:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return self

    def fetchall(self):
        return []


class _FakeDB:
    def __init__(self):
        self._conn = _FakeConn()

    def _execute_write(self, fn):
        return fn(self._conn)

    def update_params(self):
        for sql, params in self._conn.executed:
            if "UPDATE sessions" in sql:
                return sql, params
        raise AssertionError("no UPDATE sessions statement was executed")


def _sync(session):
    db = _FakeDB()
    session._sync_to_db(db)
    return db.update_params()


def test_sync_to_db_writes_model_webui():
    session = Session(session_id="abc123def456", model="@deepseek:deepseek-v4-flash")
    sql, params = _sync(session)
    assert "model__webui = ?" in sql
    assert params[0] == "@deepseek:deepseek-v4-flash"


def test_sync_to_db_writes_null_for_invalid_session():
    session = Session(session_id="abc123def456", model="deepseek-v4-flash")
    session.model_invalid = True
    _sql, params = _sync(session)
    assert params[0] is None


def test_sync_to_db_writes_null_for_unknown_model():
    session = Session(session_id="abc123def456", model="unknown")
    _sql, params = _sync(session)
    assert params[0] is None


def test_sync_to_db_writes_null_for_empty_model():
    session = Session(session_id="abc123def456", model="")
    _sql, params = _sync(session)
    assert params[0] is None


# ── Display resolver: stored identity is authoritative ─────────────────────


def test_display_resolver_preserves_prefixed_identity(empty_catalog, monkeypatch):
    def _boom(_model):  # pragma: no cover - must not run
        raise AssertionError("valid @provider:model must not be re-resolved")

    monkeypatch.setattr(routes, "_resolve_compatible_session_model", _boom)
    session = Session(session_id="abc123def456", model="@deepseek:deepseek-v4-flash")
    assert routes._resolve_effective_session_model_for_display(session) == (
        "@deepseek:deepseek-v4-flash"
    )


def test_display_resolver_returns_stored_model_for_invalid_session(empty_catalog):
    session = Session(session_id="abc123def456", model="deepseek-v4-flash")
    session.model_invalid = True
    assert routes._resolve_effective_session_model_for_display(session) == (
        "deepseek-v4-flash"
    )


def test_display_resolver_still_normalizes_legacy_bare_models(empty_catalog):
    # Backwards behaviour for a bare model without an @provider: hint: the
    # legacy cross-provider check still applies (the issue #55 guard is only
    # for stored identities and invalid sessions).
    session = Session(session_id="abc123def456", model="gemini-3.1-pro-preview")
    result = routes._resolve_effective_session_model_for_display(session)
    assert result  # a model is still exposed for display


# ── Chat start resolution ──────────────────────────────────────────────────


def test_chat_start_rejects_invalid_session(empty_catalog):
    session = Session(session_id="abc123def456", model="deepseek-v4-flash")
    session.model_invalid = True
    model, normalized, error = routes._resolve_chat_start_model(
        session, "@deepseek:deepseek-v4-flash"
    )
    assert model == ""
    assert normalized is False
    assert error == "Session model is invalid. Please select a model."


def test_chat_start_preserves_stored_prefixed_identity(empty_catalog, monkeypatch):
    def _boom(_model):  # pragma: no cover - must not run
        raise AssertionError("stored identity must not be re-resolved")

    monkeypatch.setattr(routes, "_resolve_compatible_session_model", _boom)
    session = Session(session_id="abc123def456", model="@deepseek:deepseek-v4-flash")
    model, normalized, error = routes._resolve_chat_start_model(
        session, "@deepseek:deepseek-v4-flash"
    )
    assert (model, normalized, error) == ("@deepseek:deepseek-v4-flash", False, None)


def test_chat_start_preserves_stored_identity_when_client_echoes_bare(
    empty_catalog, monkeypatch
):
    def _boom(_model):  # pragma: no cover - must not run
        raise AssertionError("stored identity must not be re-resolved")

    monkeypatch.setattr(routes, "_resolve_compatible_session_model", _boom)
    session = Session(session_id="abc123def456", model="@deepseek:deepseek-v4-flash")
    model, normalized, error = routes._resolve_chat_start_model(
        session, "deepseek-v4-flash"
    )
    assert (model, normalized, error) == ("@deepseek:deepseek-v4-flash", False, None)


def test_chat_start_uses_stored_identity_when_client_sends_no_model(empty_catalog, monkeypatch):
    def _boom(_model):  # pragma: no cover - must not run
        raise AssertionError("stored identity must not be re-resolved")

    monkeypatch.setattr(routes, "_resolve_compatible_session_model", _boom)
    session = Session(session_id="abc123def456", model="@nous:anthropic/claude-opus-4.6")
    model, normalized, error = routes._resolve_chat_start_model(session, "")
    assert (model, normalized, error) == ("@nous:anthropic/claude-opus-4.6", False, None)


def test_chat_start_resolves_explicit_new_model(empty_catalog, monkeypatch):
    """A model different from the stored identity goes through normal resolution."""
    seen = []

    def _resolve(model_id):
        seen.append(model_id)
        return "@anthropic:claude-opus-4.6", True

    monkeypatch.setattr(routes, "_resolve_compatible_session_model", _resolve)
    session = Session(session_id="abc123def456", model="@deepseek:deepseek-v4-flash")
    model, normalized, error = routes._resolve_chat_start_model(
        session, "@anthropic:claude-opus-4.6"
    )
    assert seen == ["@anthropic:claude-opus-4.6"]
    assert (model, normalized, error) == ("@anthropic:claude-opus-4.6", True, None)


# ── HTTP integration ───────────────────────────────────────────────────────


def test_session_new_persists_model_identity(cleanup_test_sessions):
    body, status = post(
        "/api/session/new", {"model": "@deepseek:deepseek-v4-flash"}
    )
    assert status == 200, body
    sid = body["session"]["session_id"]
    cleanup_test_sessions.append(sid)

    loaded = get_body(f"/api/session?session_id={sid}")
    assert loaded["session"]["model"] == "@deepseek:deepseek-v4-flash"
    assert loaded["session"]["model_invalid"] is False


def test_chat_start_rejects_session_without_model_identity(cleanup_test_sessions):
    """A state.db row with NULL model__webui must not be routed on guesswork."""
    sid = uuid.uuid4().hex[:12]
    db = models._get_session_db()
    assert db is not None, "test state.db unavailable"
    db.create_session(sid, "webui")
    cleanup_test_sessions.append(sid)

    loaded = get_body(f"/api/session?session_id={sid}")
    assert loaded["session"]["model_invalid"] is True

    body, status = post(
        "/api/chat/start",
        {
            "session_id": sid,
            "message": "hello",
            "model": "deepseek-v4-flash",
        },
    )
    assert status == 400, body
    assert "Session model is invalid" in body["error"]


def test_session_update_clears_invalid_and_preserves_identity(cleanup_test_sessions):
    sid = uuid.uuid4().hex[:12]
    db = models._get_session_db()
    assert db is not None, "test state.db unavailable"
    db.create_session(sid, "webui")
    cleanup_test_sessions.append(sid)

    loaded = get_body(f"/api/session?session_id={sid}")
    assert loaded["session"]["model_invalid"] is True

    body, status = post(
        "/api/session/update",
        {"session_id": sid, "model": "@deepseek:deepseek-v4-flash"},
    )
    assert status == 200, body
    assert body["session"]["model_invalid"] is False

    reloaded = get_body(f"/api/session?session_id={sid}")
    assert reloaded["session"]["model"] == "@deepseek:deepseek-v4-flash"
    assert reloaded["session"]["model_invalid"] is False

    # The SDK-owned column holds the bare id; the prefixed identity lives in
    # model__webui (read back through the API above).
    row = db.get_session(sid)
    assert row["model"] == "deepseek-v4-flash"
    assert row["model__webui"] == "@deepseek:deepseek-v4-flash"


def test_sessions_list_flag_matches_session_identity(cleanup_test_sessions):
    body, status = post(
        "/api/session/new", {"model": "@deepseek:deepseek-v4-flash"}
    )
    assert status == 200, body
    sid = body["session"]["session_id"]
    cleanup_test_sessions.append(sid)
    # Sessions with no messages are hidden from the sidebar; give it a title
    # and make it visible through a rename (same pattern as the CRUD tests).
    post("/api/session/rename", {"session_id": sid, "title": "issue55-list"})

    sessions = get_body("/api/sessions")["sessions"]
    row = next(s for s in sessions if s["session_id"] == sid)
    assert row["model"] == "@deepseek:deepseek-v4-flash"
    assert row.get("model_invalid") is False
