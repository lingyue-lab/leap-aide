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
    """Belt-and-suspenders: HERMES_HOME in the subprocess must NOT point at
    the user's real ~/.hermes. test_pytest_state_isolation.py covers this
    in depth; here we just sanity-check the env var surface."""
    test_state_dir = os.environ.get("HERMES_WEBUI_STATE_DIR", "")
    assert test_state_dir, "HERMES_WEBUI_STATE_DIR not set (conftest misconfigured?)"
    home_hermes = pathlib.Path.home() / ".hermes"
    # test state dir should be under a 'webui-test-*' subpath, never == home/.hermes
    assert pathlib.Path(test_state_dir) != home_hermes, (
        f"test state dir collides with production: {test_state_dir}"
    )
    assert "webui-test" in test_state_dir, (
        f"test state dir should match webui-test-* pattern: {test_state_dir}"
    )


def test_repo_root_layout_sane():
    """Repo must have server.py + api/ + static/ + tests/ — these are
    invariants every CI run depends on."""
    for rel in ("server.py", "api", "static", "tests", "scripts/ctl.sh"):
        assert (REPO_ROOT / rel).exists(), f"missing {rel} in repo root"