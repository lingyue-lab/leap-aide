"""
Shared pytest fixtures for webui-mvp tests.

TEST ISOLATION:
  Tests run against a SEPARATE server instance on port 8788 with a
  completely separate state directory. Production data is never touched.
  The test state dir is wiped before each full test run and again on teardown.

PATH DISCOVERY:
  No hardcoded paths. Discovery order:
    1. Environment variables (HERMES_WEBUI_AGENT_DIR, HERMES_WEBUI_PYTHON, etc.)
    2. Sibling checkout heuristics relative to this repo
    3. Common install paths (~/.hermes/hermes-agent)
    4. System python3 as a last resort
"""
import json
import os
import pathlib
import shutil
import subprocess
import time
import urllib.request
import urllib.error
import pytest

# ── Repo root discovery ────────────────────────────────────────────────────
# conftest.py lives at <repo>/tests/conftest.py
TESTS_DIR  = pathlib.Path(__file__).parent.resolve()
REPO_ROOT  = TESTS_DIR.parent.resolve()
HOME       = pathlib.Path.home()
HERMES_HOME = pathlib.Path(os.getenv('HERMES_HOME', str(HOME / '.hermes')))

# ── Test server config ────────────────────────────────────────────────────
# Port and state dir auto-derive from the repo path when no env var is set,
# giving every worktree its own isolated port (8800-8899) and state directory.
# Override with HERMES_WEBUI_TEST_PORT / HERMES_WEBUI_TEST_STATE_DIR to pin.

def _auto_test_port(repo_root) -> int:
    """Map repo path to a unique port in 20000-29999 (10k range = near-zero collisions).
    Far from system port ranges and Linux ephemeral ports (32768+).
    Override with HERMES_WEBUI_TEST_PORT to use a specific port."""
    import hashlib
    h = int(hashlib.md5(str(repo_root).encode()).hexdigest(), 16)
    return 20000 + (h % 10000)

def _auto_state_dir_name(repo_root) -> str:
    import hashlib
    h = hashlib.md5(str(repo_root).encode()).hexdigest()[:8]
    return f"webui-test-{h}"

TEST_PORT      = int(os.getenv('HERMES_WEBUI_TEST_PORT',
                               str(_auto_test_port(REPO_ROOT))))
TEST_BASE      = f"http://127.0.0.1:{TEST_PORT}"
TEST_STATE_DIR = pathlib.Path(os.getenv(
    'HERMES_WEBUI_TEST_STATE_DIR',
    str(HERMES_HOME / _auto_state_dir_name(REPO_ROOT))
))
TEST_WORKSPACE = TEST_STATE_DIR / 'test-workspace'

# Publish at module level so api.config, _pytest_port.py, and any test module
# importing stateful API code during collection see the isolated test paths.
#
# Direct assignment is intentional for production-risk paths: tests that import
# api.config/api.models in the pytest process must never inherit the real
# ~/.hermes state tree before the server subprocess fixture starts.
os.environ['HERMES_WEBUI_TEST_PORT'] = str(TEST_PORT)
os.environ['HERMES_WEBUI_TEST_STATE_DIR'] = str(TEST_STATE_DIR)
os.environ['HERMES_WEBUI_STATE_DIR'] = str(TEST_STATE_DIR)
os.environ['HERMES_WEBUI_DEFAULT_WORKSPACE'] = str(TEST_WORKSPACE)
os.environ['HERMES_HOME'] = str(TEST_STATE_DIR)
os.environ['HERMES_BASE_HOME'] = str(TEST_STATE_DIR)
# Default model for the test process (read by _pytest_port.TEST_DEFAULT_MODEL
# at import time). Tests that want a different model pass it explicitly.
# When LEAP_AIDE_TEST_DEEPSEEK_KEY is set we use the @provider:model form so
# tests that don't pass an explicit one still route to the direct DeepSeek
# endpoint (see the server-subprocess note in test_server()).
if os.environ.get('LEAP_AIDE_TEST_DEEPSEEK_KEY'):
    os.environ.setdefault('HERMES_WEBUI_DEFAULT_MODEL', '@deepseek:deepseek-v4-flash')
else:
    os.environ.setdefault('HERMES_WEBUI_DEFAULT_MODEL', 'deepseek/deepseek-v4-flash')

# ── Server script: always relative to repo root ───────────────────────────
SERVER_SCRIPT = REPO_ROOT / 'server.py'
if not SERVER_SCRIPT.exists():
    raise RuntimeError(
        f"server.py not found at {SERVER_SCRIPT}. "
        "Is conftest.py in the tests/ subdirectory of the repo?"
    )

# ── Hermes agent discovery (mirrors api/config._discover_agent_dir) ───────
def _discover_agent_dir() -> pathlib.Path:
    candidates = [
        os.getenv('HERMES_WEBUI_AGENT_DIR', ''),
        str(HERMES_HOME / 'hermes-agent'),
        str(REPO_ROOT.parent / 'hermes-agent'),
        str(HOME / '.hermes' / 'hermes-agent'),
        str(HOME / 'hermes-agent'),
        # System install path: hermes-agent shipped as a Python package under
        # /usr/local/lib/hermes-agent (e.g. testbox setup, Docker images,
        # `pip install hermes-agent` deployments). Mirrors the path the
        # production server expects per CLAUDE.md.
        '/usr/local/lib/hermes-agent',
        '/opt/hermes-agent',
        '/usr/local/hermes-agent',
    ]
    for c in candidates:
        if not c:
            continue
        p = pathlib.Path(c).expanduser()
        if p.exists() and (p / 'run_agent.py').exists():
            return p.resolve()
    return None

# ── Python discovery (mirrors api/config._discover_python) ────────────────
def _discover_python(agent_dir) -> str:
    if os.getenv('HERMES_WEBUI_PYTHON'):
        return os.getenv('HERMES_WEBUI_PYTHON')
    if agent_dir:
        venv_py = agent_dir / 'venv' / 'bin' / 'python'
        if venv_py.exists():
            return str(venv_py)
    local_venv = REPO_ROOT / '.venv' / 'bin' / 'python'
    if local_venv.exists():
        return str(local_venv)
    return shutil.which('python3') or shutil.which('python') or 'python3'

HERMES_AGENT = _discover_agent_dir()
VENV_PYTHON  = _discover_python(HERMES_AGENT)

# Work dir: agent dir if found, else repo root
WORKDIR = str(HERMES_AGENT) if HERMES_AGENT else str(REPO_ROOT)

# ── Test API key availability ────────────────────────────────────────────────
# Tests that exercise a real chat completion (test_chat_stream_*) need a live
# provider key. Conftest forwards LEAP_AIDE_TEST_DEEPSEEK_KEY from the test
# runner's environment into the server subprocess as DEEPSEEK_API_KEY; if the
# runner didn't supply one, those tests self-skip.
TEST_DEEPSEEK_KEY_AVAILABLE = bool(os.environ.get('LEAP_AIDE_TEST_DEEPSEEK_KEY'))

# ── Agent availability detection ─────────────────────────────────────────────
# Tests that require hermes-agent modules (cron, skills, approval, chat/stream)
# are skipped when the agent isn't installed, instead of failing with 500 errors.
AGENT_AVAILABLE = HERMES_AGENT is not None


def _check_agent_modules() -> bool:
    """Verify hermes-agent Python modules are actually importable.

    Runs the check in the **venv python** (where hermes-agent is installed)
    rather than the pytest process's interpreter — otherwise the import check
    always fails on machines that don't have hermes-agent on PYTHONPATH, even
    though the test server subprocess uses the venv and can import them fine.
    That mismatch causes the spurious "hermes-agent not found; N tests will
    be skipped" warning for cases that would actually pass.
    """
    if not HERMES_AGENT or not VENV_PYTHON or not pathlib.Path(VENV_PYTHON).exists():
        return False
    try:
        import subprocess as _sp
        proc = _sp.run(
            [VENV_PYTHON, "-c",
             "import cron.jobs, tools.skills_tool; print('ok')"],
            capture_output=True, text=True, timeout=10,
            cwd=str(HERMES_AGENT),
        )
        return proc.returncode == 0 and "ok" in proc.stdout
    except Exception:
        return False

AGENT_MODULES_AVAILABLE = _check_agent_modules()

# pytest marker: skip tests that need hermes-agent when it's not present
requires_agent = pytest.mark.skipif(
    not AGENT_AVAILABLE,
    reason="hermes-agent not found (skipping agent-dependent test)"
)
requires_agent_modules = pytest.mark.skipif(
    not AGENT_MODULES_AVAILABLE,
    reason="hermes-agent Python modules not importable (cron, skills_tool)"
)

def pytest_configure(config):
    config.addinivalue_line("markers", "requires_agent: skip when hermes-agent dir is not found")
    config.addinivalue_line("markers", "requires_agent_modules: skip when hermes-agent Python modules are not importable")
    config.addinivalue_line("markers", "integration: tests that require a live test server")
    # Per-test timeout marker (pytest-timeout). No global --timeout flag is
    # passed by run_test.sh because pytest-timeout's os._exit(1) on a slow
    # test tears down the session-scoped test_server fixture mid-run. Use
    # this marker only on individual tests that have a known hang risk.
    config.addinivalue_line(
        "markers",
        "timeout(N): per-test timeout in seconds (pytest-timeout). "
        "Use sparingly — only on tests with a known hang risk.",
    )

def pytest_collection_modifyitems(config, items):
    """Auto-skip tests that genuinely require a live provider key.

    The previous implementation skipped an over-broad list whenever
    ``_check_agent_modules()`` returned False. That check now runs in the venv
    python and is accurate, so the broad list is gone -- only tests that need
    a *real* chat completion (network call to DeepSeek V4 Flash) are gated
    on the test runner providing ``LEAP_AIDE_TEST_DEEPSEEK_KEY``.

    Without that key the agent thread crashes synchronously during AIAgent
    init and pops the stream before the SSE consumer can attach, so
    ``/api/chat/stream`` returns 404 -- which the test interprets as
    "AIAgent not imported" and fails on. Set ``LEAP_AIDE_TEST_DEEPSEEK_KEY``
    in the shell that invokes pytest to run these.
    """
    if TEST_DEEPSEEK_KEY_AVAILABLE:
        return

    _NEEDS_LIVE_KEY = {
        'test_chat_stream_opens_successfully',
    }
    skip_marker = pytest.mark.skip(
        reason="requires LEAP_AIDE_TEST_DEEPSEEK_KEY env var (live DeepSeek key)"
    )
    for item in items:
        if item.name in _NEEDS_LIVE_KEY:
            item.add_marker(skip_marker)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _post(base, path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {}


def _wait_for_server(base, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    return True
        except Exception:
            time.sleep(0.3)
    return False


# ── Session-scoped test server ────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def test_server():
    """
    Start an isolated test server on TEST_PORT with a clean state directory.
    Paths are discovered dynamically -- no hardcoded absolute path assumptions.
    """
    # Kill any leftover process on the test port before starting.
    # Stale servers from QA harness runs or prior test sessions cause
    # conftest to think the server is already up, producing false failures.
    try:
        subprocess.run(['fuser', '-k', f'{TEST_PORT}/tcp'],
                        capture_output=True, timeout=5)
    except Exception:
        pass
    time.sleep(0.5)  # brief pause to let the port release

    # Clean slate
    if TEST_STATE_DIR.exists():
        shutil.rmtree(TEST_STATE_DIR)
    TEST_STATE_DIR.mkdir(parents=True)
    TEST_WORKSPACE.mkdir(parents=True)

    # Symlink real skills into test home so skill-related tests work,
    # but all write-heavy state stays isolated.
    real_skills  = HERMES_HOME / 'skills'
    test_skills  = TEST_STATE_DIR / 'skills'
    if real_skills.exists() and not test_skills.exists():
        test_skills.symlink_to(real_skills)

    # Isolated cron state
    (TEST_STATE_DIR / 'cron').mkdir(parents=True, exist_ok=True)

    # Expose TEST_STATE_DIR to the test process itself so that tests which write
    # directly to state.db (e.g. test_gateway_sync.py) always use the same path
    # as the server.  Other test files (test_auth_sessions.py) may override
    # HERMES_WEBUI_STATE_DIR for their own purposes, but HERMES_WEBUI_TEST_STATE_DIR
    # is reserved for this mapping and is never overridden by individual test files.
    # Export both port and state-dir as env vars so individual test files
    # can read them without importing conftest (avoids circular imports).
    os.environ.setdefault('HERMES_WEBUI_TEST_PORT', str(TEST_PORT))
    # os.environ already set at module level above; no-op here.

    env = os.environ.copy()
    # Strip real provider keys so test subprocess never inherits production credentials.
    # We *intentionally* inject a separate test-only key (LEAP_AIDE_TEST_DEEPSEEK_KEY)
    # below if it's set in the test runner's environment — see that block.
    for _k in list(env):
        if any(_k.startswith(p) for p in (
            'OPENROUTER_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
            'GOOGLE_API_KEY', 'DEEPSEEK_API_KEY',
        )):
            del env[_k]
    # Optional test-only DeepSeek key. Set LEAP_AIDE_TEST_DEEPSEEK_KEY in the
    # shell that invokes pytest (CI secret, dev machine .env, etc.) and the
    # value is forwarded as DEEPSEEK_API_KEY into the server subprocess.
    # Tests that actually exercise chat completions (test_chat_stream_*,
    # test_approval_submit_and_respond) need a real key; SSE-open-only tests
    # and approval-flow tests still pass without one because they don't
    # consume tokens.
    _test_deepseek_key = os.environ.get('LEAP_AIDE_TEST_DEEPSEEK_KEY')
    env.update({
        "HERMES_WEBUI_PORT":              str(TEST_PORT),
        "HERMES_WEBUI_HOST":              "127.0.0.1",
        "HERMES_WEBUI_STATE_DIR":         str(TEST_STATE_DIR),
        "HERMES_WEBUI_DEFAULT_WORKSPACE": str(TEST_WORKSPACE),
        # Enable test-only seed endpoints at /api/_test/seed_session and
        # /api/_test/cleanup_sessions (api/_test_helpers.py). Production
        # servers never set this flag, so those endpoints 404 outside tests.
        "HERMES_WEBUI_TEST_SEED":         "1",
        # DeepSeek V4 Flash (cheap + fast). When a test key is injected below
        # we switch to the @provider:model form so resolve_model_provider
        # routes the call to DeepSeek's own endpoint (the slash form
        # 'deepseek/deepseek-v4-flash' is the OpenRouter catalog id and would
        # be routed to OpenRouter — which has no key here, so the agent thread
        # crashes on 401 and the stream pops before SSE can attach, producing
        # a 404 on /api/chat/stream). See api/config.py resolve_model_provider.
        "HERMES_WEBUI_DEFAULT_MODEL": (
            "@deepseek:deepseek-v4-flash" if _test_deepseek_key
            else "deepseek/deepseek-v4-flash"
        ),
        "HERMES_HOME":                    str(TEST_STATE_DIR),
        # Belt-and-suspenders: HERMES_BASE_HOME hard-locks _DEFAULT_HERMES_HOME
        # in api/profiles.py to the test state dir regardless of profile switching
        # or any os.environ mutation that happens inside the server process.
        # Without this, a profile switch or active_profile file in the real
        # ~/.hermes can redirect _get_active_hermes_home() out of the sandbox,
        # causing onboarding writes (config.yaml, .env) to land in the production
        # ~/.hermes/profiles/webui/ and overwrite real API keys.
        "HERMES_BASE_HOME":               str(TEST_STATE_DIR),
    })
    if _test_deepseek_key:
        env["DEEPSEEK_API_KEY"] = _test_deepseek_key

    # Pass agent dir if discovered so server.py doesn't have to re-discover
    if HERMES_AGENT:
        env["HERMES_WEBUI_AGENT_DIR"] = str(HERMES_AGENT)

    # DEBUG: capture server logs to file so seed failures are diagnosable.
    _server_log = open(TEST_STATE_DIR / "server.log", "wb")
    proc = subprocess.Popen(
        [VENV_PYTHON, str(SERVER_SCRIPT)],
        cwd=WORKDIR,
        env=env,
        stdout=_server_log,
        stderr=_server_log,
    )

    if not _wait_for_server(TEST_BASE, timeout=20):
        proc.kill()
        pytest.fail(
            f"Test server on port {TEST_PORT} did not start within 20s.\n"
            f"  server.py : {SERVER_SCRIPT}\n"
            f"  python    : {VENV_PYTHON}\n"
            f"  agent dir : {HERMES_AGENT}\n"
            f"  workdir   : {WORKDIR}\n"
        )

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)

    # DEBUG: preserve test state dir + server log for diagnosis
    if os.environ.get("LEAP_AIDE_KEEP_TEST_STATE") == "1":
        try:
            keep_path = pathlib.Path("/tmp/leap-aide-test-state-keep")
            keep_path.mkdir(exist_ok=True)
            server_log_src = TEST_STATE_DIR / "server.log"
            if server_log_src.exists():
                shutil.copy(server_log_src, keep_path / "server.log")
            state_db_src = TEST_STATE_DIR / "state.db"
            if state_db_src.exists():
                shutil.copy(state_db_src, keep_path / "state.db")
        except Exception:
            pass
    else:
        try:
            shutil.rmtree(TEST_STATE_DIR)
        except Exception:
            pass


# ── Test base URL ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def base_url():
    return TEST_BASE


# ── Per-test model cache invalidation ────────────────────────────────────────
# The TTL cache for get_available_models() persists across tests within the
# same process. Tests that modify cfg in-memory won't trigger the mtime path,
# so the cache must be explicitly invalidated after each test that exercises
# provider/model detection.

@pytest.fixture(autouse=True)
def _invalidate_models_cache_after_test():
    """Force the TTL cache to be cleared before and after every test.

    This prevents state bleed where a test that calls get_available_models()
    populates the cache with a particular config, and the next test sees stale
    results even though it has mutated _cfg_cache in-memory.
    """
    try:
        from api.config import invalidate_models_cache
        invalidate_models_cache()
    except Exception:
        pass
    yield
    try:
        from api.config import invalidate_models_cache
        invalidate_models_cache()
    except Exception:
        pass


# ── Per-test session cleanup ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup_test_sessions():
    """
    Yields a list for tests to register created session IDs.
    Deletes all registered sessions after each test.
    Resets last_workspace to the test workspace to prevent state bleed.
    """
    created: list[str] = []
    yield created

    for sid in created:
        try:
            _post(TEST_BASE, "/api/session/delete", {"session_id": sid})
        except Exception:
            pass

    try:
        # /api/sessions/cleanup requires `source` + `older_than_days`; without
        # `source` it returns 400 and silently no-ops. older_than_days=1 + a
        # known source wipes any zero-message sessions left behind by previous
        # tests, keeping the in-memory SESSIONS cache from leaking across tests.
        _post(TEST_BASE, "/api/sessions/cleanup", {"source": "webui", "older_than_days": 1})
    except Exception:
        pass

    try:
        last_ws_file = TEST_STATE_DIR / "last_workspace.txt"
        last_ws_file.write_text(str(TEST_WORKSPACE), encoding='utf-8')
    except Exception:
        pass


# ── Convenience helpers ────────────────────────────────────────────────────────

def make_session_tracked(created_list, ws=None):
    """
    Create a session on the test server and register it for cleanup.

    Usage:
        def test_something(cleanup_test_sessions):
            sid, ws = make_session_tracked(cleanup_test_sessions)
    """
    body = {}
    if ws:
        body["workspace"] = str(ws)
    d = _post(TEST_BASE, "/api/session/new", body)
    sid = d["session"]["session_id"]
    ws_path = pathlib.Path(d["session"]["workspace"])
    created_list.append(sid)
    return sid, ws_path
