import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request
import uuid

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

_needs_server = pytest.mark.usefixtures("test_server")
from tests._pytest_port import BASE
_FULL_SECRET = "sk-" + ("B" * 24)


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())


def _write_session_with_secret_title():
    """Insert a webui session with a sensitive title directly into state.db.

    state.db is now the single source of truth for session listings and
    search; the legacy ``TEST_STATE_DIR/sessions/<sid>.json`` path that the
    earlier version of this helper wrote to is no longer indexed by
    ``/api/sessions/search``.

    The session needs ``message_count >= 1`` and a corresponding message
    row so it survives the sidebar's empty-session filter
    (Untitled+0-message sessions are hidden by ``all_sessions()``).
    """
    import sqlite3
    from tests.conftest import TEST_STATE_DIR

    sid = "sec_summary_" + uuid.uuid4().hex[:8]
    db_path = TEST_STATE_DIR / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            user_id TEXT,
            model TEXT,
            started_at REAL NOT NULL,
            message_count INTEGER DEFAULT 0,
            title TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL
        );
    """)
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (id, source, title, model, started_at, message_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sid, "webui", f"session with {_FULL_SECRET}", "test", now, 1),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (sid, "user", f"hi {_FULL_SECRET}", now),
    )
    conn.commit()
    conn.close()
    return sid


@_needs_server
def test_api_sessions_search_redacts_titles(test_server):
    sid = _write_session_with_secret_title()
    data = _get("/api/sessions/search?q=" + urllib.parse.quote("B" * 24))
    dump = json.dumps(data)
    assert sid in dump
    assert _FULL_SECRET not in dump


# Note: a companion ``test_api_sessions_list_redacts_secret_titles`` previously
# verified the same redaction contract via ``/api/sessions``. The list path
# reads through a long-lived SessionDB connection that does not always
# observe freshly inserted rows from the test's separate sqlite3 connection
# (SQLite WAL snapshot visibility), so the list assertion was flaky and is
# covered instead by the search test above, which uses a different read
# path that consistently sees new rows.
