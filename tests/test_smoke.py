"""
Explicit smoke tests for leap-aide.

This file is the entry point for L1 in `scripts/run_test.sh smoke`.
It contains a small, fast set of checks that verify the test server is up
and core API surfaces respond. The full L1 set additionally pulls in:

    tests/test_api_sessions_crud.py
    tests/test_chat_start_commands.py
    tests/test_regressions.py
    tests/test_pytest_state_isolation.py

Run via:

    ./scripts/run_test.sh smoke         # on testbox
    ./scripts/run_test.sh smoke         # anywhere else — auto ssh-jumps

These tests must each complete in well under a second and must not depend
on hermes-agent SDK modules (so they are guaranteed to run on every machine).
"""
import json
import os
import pathlib
import urllib.error
import urllib.request

from tests._pytest_port import BASE
from tests.conftest import TEST_STATE_DIR

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()


# ── helpers (local; do not import conftest to avoid circular imports) ──

def _get(path: str, timeout: float = 5.0):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read()), r.status


def _post(path: str, body: dict | None = None, timeout: float = 5.0):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read()), e.code
        except Exception:
            return {}, e.code


# ── smoke checks ──

def test_health_endpoint():
    """Test server is up and reports healthy."""
    data, status = _get("/health")
    assert status == 200, f"/health returned {status}"
    assert data.get("status") == "ok", f"/health payload: {data}"


def test_root_serves_static_index():
    """GET / returns 200 with text/html (the static SPA shell)."""
    with urllib.request.urlopen(BASE + "/", timeout=5) as r:
        body = r.read()
        ctype = r.headers.get("Content-Type", "")
    assert r.status == 200
    assert "text/html" in ctype, f"unexpected content-type: {ctype}"
    assert b"<" in body, "root response does not look like HTML"


def test_sessions_list_endpoint():
    """GET /api/sessions returns 200 with a list payload."""
    data, status = _get("/api/sessions")
    assert status == 200, f"sessions returned {status}: {data}"
    assert "sessions" in data, f"missing 'sessions' key in {list(data)}"
    assert isinstance(data["sessions"], list), "sessions must be a list"


def test_session_create_then_delete(cleanup_test_sessions):
    """POST /api/session/new returns a session with id; delete cleans up."""
    data, status = _post("/api/session/new", {})
    assert status == 200, f"session/new returned {status}: {data}"
    sid = data["session"]["session_id"]
    assert sid, "session_id missing"
    cleanup_test_sessions.append(sid)
    # immediate GET /api/session should return the same session
    fetched, fstatus = _get(f"/api/session?session_id={sid}")
    assert fstatus == 200, f"session/get returned {fstatus}"
    assert fetched["session"]["session_id"] == sid


def test_models_endpoint_responds():
    """GET /api/models responds (shape may vary — just confirm 200)."""
    data, status = _get("/api/models")
    assert status == 200, f"/api/models returned {status}: {data}"
    # response is an object (provider -> list mapping)
    assert isinstance(data, dict), f"/api/models payload not a dict: {data}"


def test_chat_start_returns_stream_id(cleanup_test_sessions):
    """R1 regression: chat/start must always return a stream_id (or a known
    direct/skill response shape) — never 500 due to missing uuid import."""
    data, status = _post("/api/session/new", {})
    assert status == 200
    sid = data["session"]["session_id"]
    cleanup_test_sessions.append(sid)
    payload, pstatus = _post("/api/chat/start", {
        "session_id": sid,
        "message": "ping",
        "model": os.getenv("HERMES_WEBUI_DEFAULT_MODEL", "deepseek/deepseek-v4-flash"),
    })
    assert pstatus == 200, f"chat/start returned {pstatus}: {payload}"
    # Three valid shapes per test_chat_start_commands.py
    if "stream_id" in payload:
        assert len(payload["stream_id"]) > 8
    else:
        # direct or skill command — should still have a recognisable key
        assert "direct" in payload or "content" in payload, (
            f"unrecognised chat/start payload shape: {list(payload)}"
        )


def test_state_dir_is_isolated_from_production():
    """Belt-and-suspenders: the test state directory must NOT point at the
    user's real ~/.hermes — both the canonical conftest constant AND the env
    var that the server subprocess consumes must reflect the isolated path.

    `TEST_STATE_DIR` is published by conftest at module-import time and is
    never overridden by individual test files (conftest.py:298-300 reserves
    ``HERMES_WEBUI_TEST_STATE_DIR`` as the un-clobbered mapping for this
    purpose). Checking it directly makes the test robust to other test files
    mutating ``HERMES_WEBUI_STATE_DIR`` at import time, which is the exact
    failure mode that broke this assertion under --batch-size=128 (see
    test_auth_sessions.py / test_auth_session_persistence.py).

    `test_pytest_state_isolation.py` covers this in depth by importing
    api.config and asserting STATE_DIR/SESSION_DIR paths; here we just
    sanity-check the env-var surface that the server subprocess consumes.
    """
    # Canonical source: the constant conftest.py published at import time.
    # Importing it here is what makes the test robust to alphabetical
    # import-order clobbering of HERMES_WEBUI_STATE_DIR.
    canonical = pathlib.Path(TEST_STATE_DIR).resolve()
    home_hermes = pathlib.Path.home() / ".hermes"
    assert canonical != home_hermes, (
        f"conftest TEST_STATE_DIR collides with production: {canonical}"
    )
    assert "webui-test" in str(canonical), (
        f"conftest TEST_STATE_DIR should match webui-test-* pattern: {canonical}"
    )

    # Cross-check: the env var the server subprocess consumes must also
    # reflect the same isolated path. If another test file mutated it
    # between conftest import and now, we want to know about it.
    env_state_dir = os.environ.get("HERMES_WEBUI_STATE_DIR", "")
    assert env_state_dir, "HERMES_WEBUI_STATE_DIR not set (conftest misconfigured?)"
    assert pathlib.Path(env_state_dir).resolve() == canonical, (
        f"env HERMES_WEBUI_STATE_DIR={env_state_dir!r} does not match "
        f"conftest TEST_STATE_DIR={canonical!r} (clobbered by another test?)"
    )


def test_repo_root_layout_sane():
    """Repo must have server.py + api/ + static/ + tests/ — these are
    invariants every CI run depends on."""
    for rel in ("server.py", "api", "static", "tests", "scripts/ctl.sh"):
        assert (REPO_ROOT / rel).exists(), f"missing {rel} in repo root"