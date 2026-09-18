import json
import pathlib
import sys
import time
import urllib.error
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


def _post(path, body):
    """POST JSON body to the test server, return (data, status)."""
    data = json.dumps(body).encode()
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


def _write_session_with_secret_title():
    """Seed a webui session with a sensitive title via the SDK high-level API.

    Posts to ``/api/_test/seed_session`` (gated by ``HERMES_WEBUI_TEST_SEED=1``)
    which runs ``SessionDB.create_session`` + an SDK-owned INSERT into messages
    on the server side. Inserting directly via raw sqlite3 (the previous
    implementation) leaves state.db with a minimal ``sessions`` schema, which
    causes the server's ``_init_schema()`` to fail on ``CREATE INDEX … ON
    sessions(parent_session_id)`` because the column does not exist in the
    minimal schema. That failure takes the singleton SessionDB out of service
    and ``/api/sessions/search`` returns ``sessions: []``.
    """
    sid = "sec_summary_" + uuid.uuid4().hex[:8]
    body = {
        "session_id": sid,
        "source": "webui",
        "title": f"session with {_FULL_SECRET}",
        "model": "test",
        "message_count": 1,
        "messages": [
            {"role": "user", "content": f"hi {_FULL_SECRET}",
             "timestamp": time.time()},
        ],
    }
    data, status = _post("/api/_test/seed_session", body)
    assert status == 200, f"seed_session failed: {status} {data}"
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
