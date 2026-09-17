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

# Minimal stub hermes config so AIAgent init completes inside the background
# chat/start thread. Without it, /api/chat/start spawns the agent thread,
# init_agent() in hermes-agent reaches agent_init.py:1417 with no provider
# resolved and raises RuntimeError("No LLM provider configured..."). That
# RuntimeError is harmless to the HTTP response (chat/start already returned
# stream_id), but it produces a noisy traceback in server.log on every
# smoke run.
#
# The 'custom' provider + base_url combo enters the explicit-creds branch
# at agent/agent_init.py:~1263 (`if api_key and base_url`), which builds
# client_kwargs directly and skips resolve_provider_client() / the
# RuntimeError at :1417 entirely. The base_url points at Ollama's default
# loopback (127.0.0.1:11434) — never contacted by tests that only assert
# the chat/start HTTP contract (no consumer reads the SSE stream).
# 'no-key-required' is hermes-agent's sentinel set at runtime_provider.py:
# 1368-1369 when a custom provider has no key configured.
_STUB_HERMES_CONFIG_YAML = (
    "model:\n"
    "  provider: custom\n"
    "  base_url: http://127.0.0.1:11434/v1\n"
    "  api_key: no-key-required\n"
    "  default: stub-test-model\n"
)

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

# ── Hermes agent discovery ─────────────────────────────────────────────────
def _discover_agent_dir() -> pathlib.Path:
    """Locate the hermes-agent checkout.

    Delegates to api.config._discover_agent_dir so the test process sees the
    same candidate list as the server it spawns. Historically this function
    duplicated the candidate list inline (the comment above used to read
    "mirrors api/config._discover_agent_dir"), but the two implementations
    drifted: conftest missed the XDG_DATA_HOME and parent-of-parent entries
    that api/config.py added later, so on testbox conftest frequently
    returned None even when api/config found /usr/local/lib/hermes-agent.

    Delegating here is safe at pytest collection time: api/config has no
    top-level side effects beyond computing module-level Path constants,
    which is exactly what the test process wants (HERMES_WEBUI_STATE_DIR is
    already overridden at line 68 above before any api import can happen).

    The fallback list covers environments where api.config can't be imported
    (missing hermes-agent deps on the host running pytest).
    """
    try:
        from api.config import _discover_agent_dir as _impl
        return _impl()
    except Exception:
        # Fallback: minimal candidate list so the test process at least boots
        # in environments where api.config can't be imported.
        for c in [
            os.getenv('HERMES_WEBUI_AGENT_DIR', ''),
            '/usr/local/lib/hermes-agent',
            '/opt/hermes-agent',
            '/usr/local/hermes-agent',
        ]:
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
    import atexit as _atexit
    _trace_path = pathlib.Path("/tmp/leap-aide-test-server-trace.log")
    _trace_path.parent.mkdir(parents=True, exist_ok=True)
    def _trace(msg):
        import datetime as _dt
        try:
            with _trace_path.open("a") as _f:
                _f.write(f"{_dt.datetime.now().isoformat()} pid={os.getpid()} {msg}\n")
        except Exception:
            pass
    _trace(f"test_server fixture ENTRY port={TEST_PORT}")
    # Capture pytest session state + caller frame to diagnose double-entry
    try:
        import traceback as _tb
        import atexit as _atexit
        _caller_frames = _tb.extract_stack(limit=6)
        _trace(f"  cwd={os.getcwd()} conftest globals id={id(globals())}")
        for _fr in _caller_frames[-4:]:
            _trace(f"    at {_fr.filename}:{_fr.lineno} in {_fr.name}")
        # Register an atexit handler to confirm when (or whether) pytest exits
        def _atexit_handler():
            import datetime as _dt2
            try:
                with _trace_path.open("a") as _f:
                    _f.write(f"{_dt2.datetime.now().isoformat()} pid={os.getpid()} ATEXIT — pytest is exiting\n")
            except Exception:
                pass
        _atexit.register(_atexit_handler)
    except Exception as _e:
        _trace(f"  trace capture failed: {_e}")

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

    # Write a minimal stub hermes config so the background chat/start
    # agent thread completes init_agent() without raising
    # RuntimeError("No LLM provider configured"). See _STUB_HERMES_CONFIG_YAML
    # for the full rationale. Written before server subprocess starts so
    # api/config._get_config_path() sees it on first read.
    (TEST_STATE_DIR / 'config.yaml').write_text(_STUB_HERMES_CONFIG_YAML)

    # Symlink real skills into test home so skill-related tests work,
    # but all write-heavy state stays isolated.
    real_skills  = HERMES_HOME / 'skills'
    test_skills  = TEST_STATE_DIR / 'skills'
    if real_skills.exists() and not test_skills.exists():
        test_skills.symlink_to(real_skills)

    # Isolated cron state
    (TEST_STATE_DIR / 'cron').mkdir(parents=True, exist_ok=True)

    # api.session_display creates SESSIONS_DISPLAY_DIR at module-load time
    # (api/session_display.py:47), but test_server wipes TEST_STATE_DIR
    # after import. Recreate it here so SessionDisplay tests can write
    # their <sid>.json config without FileNotFoundError. Mirrors the
    # cron / test-workspace pattern above.
    from api.config import SESSIONS_DISPLAY_DIR  # noqa: E402
    SESSIONS_DISPLAY_DIR.mkdir(parents=True, exist_ok=True)

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
    # Inject a synthetic OPENROUTER_API_KEY so the catalog builds an openrouter
    # group. Our _STUB_HERMES_CONFIG_YAML sets `provider: custom` which makes
    # _resolve_compatible_session_model (api/routes.py:345) enter the
    # custom/openrouter normalization branch; without an openrouter group in
    # the catalog, that branch replaces the persisted session model with
    # default_model (api/routes.py:361), breaking test_session_update and
    # similar tests that round-trip arbitrary model strings through /api/session.
    # The fake key is never used to call OpenRouter — the auxiliary client
    # marks openrouter unhealthy on 401 and moves on, same outcome as before.
    env["OPENROUTER_API_KEY"] = "stub-openrouter-key-for-test-only"
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
    # Use a managed file object: subprocess.Popen inherits the fd, but the
    # Python-level file wrapper must be closed before pytest exits or the
    # gc'd flush can hold up interpreter shutdown (manifests as the
    # pytest process showing "100%" then hanging on exit).
    _server_log = open(TEST_STATE_DIR / "server.log", "wb")
    try:
        proc = subprocess.Popen(
            [VENV_PYTHON, str(SERVER_SCRIPT)],
            cwd=WORKDIR,
            env=env,
            stdout=_server_log,
            stderr=_server_log,
        )
        _trace(f"test_server Popen pid={proc.pid} port={TEST_PORT}")

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
        _trace(f"test_server YIELD DONE — entering teardown proc.pid={proc.pid}")
        _trace(f"  post-yield: before proc.kill() — generator state: {proc}")

        # ── Teardown: SIGKILL without wait ─────────────────────────────
        # CRITICAL — do NOT block in this fixture's post-yield.
        # Background: pytest 9.1.1's session finalizer calls next() on
        # the generator to advance past yield. If the post-yield code
        # blocks (e.g. proc.wait() on a server whose SIGTERM handler
        # is stuck in server.py's 6-step shutdown cascade), pytest
        # observes the long wait as fixture failure, clears
        # FixtureDef.cached_result, and re-runs setup for the next
        # test — spawning a duplicate server.py. Evidence from
        # /tmp/leap-aide-test-server-trace.log on testbox (batch-size=50,
        # pytest 9.1.1, session-scoped autouse test_server):
        #
        #   20:26:13.229 pid=172268 post-yield: proc.terminate() returned
        #   20:26:16.753 pid=172268 test_server fixture ENTRY port=26597
        #   20:26:16.755 caller stack: pytest_fixture_setup → call_fixture_func
        #   20:26:17.327 pid=172268 test_server Popen pid=174095 port=26597
        #
        # First server (PID 172272) was left as <defunct> because
        # proc.wait() never completed; pytest re-entered setup and
        # spawned PID 174095 instead. Batch 2 ended up serving tests
        # against two overlapping server.py processes, with the orphan
        # PID 172272 persisting as a zombie until pytest exits.
        #
        # Fix: SIGKILL and return immediately. The kernel reaps the
        # zombie asynchronously; the next pytest run's `fuser -k` at
        # fixture ENTRY (line ~338 below) guarantees port cleanup.
        # No `proc.wait()` is called here — it is the wait that
        # triggers pytest's re-entry, not the signal itself.
        try:
            proc.kill()
            _trace(f"  post-yield: proc.kill() returned (SIGKILL sent, no wait)")
        except BaseException as _e:
            _trace(f"  post-yield: proc.kill() raised {_e!r}")
            # Don't re-raise — pytest must see this fixture as cleanly
            # finalized even if kill() fails (e.g. process already dead).
            # The next batch's port-cleanup guarantees no leftovers.
    finally:
        _trace(f"test_server fixture EXIT (finally block) proc.pid={proc.pid}")
        # Close the server-log file wrapper BEFORE pytest tears down its
        # session. subprocess.Popen already inherited the underlying fd,
        # so closing the Python-level wrapper only releases the Python
        # object — it does not affect the server's ability to write to
        # the log until the server itself exits.
        try:
            _server_log.close()
        except Exception:
            pass

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


# ── Per-test server liveness check ────────────────────────────────────────────
# When test_server crashes mid-batch (e.g. an unhandled exception in a request
# handler), every subsequent HTTP-based test in the same pytest process fails
# with `ConnectionRefusedError: [Errno 111]`. That cascades into hundreds of
# confusing failures that all point at "urllib URLError" instead of the real
# culprit. This fixture surfaces the crash immediately at the next test's
# setup boundary, dumping the tail of server.log so the failing test (the
# one whose teardown crashed the server) is obvious from the pytest output.
#
# Declared last among the autouse fixtures so its yield-teardown runs last;
# pytest runs teardown in reverse order of setup, so the position matters.

@pytest.fixture(autouse=True)
def _fail_fast_if_server_died():
    """After each test, ping the server. If it crashed, fail with server.log tail.

    Prevents 200+ cascading 'Connection refused' failures by surfacing the
    exact pytest process boundary at which test_server died.

    IMPORTANT: This fixture MUST NOT add latency to the happy path. The
    timeout=2s ping only triggers if /health is unreachable; a healthy
    server responds in <10ms. If the server is dead, pytest.fail() short-
    circuits subsequent tests, so the 2s ping only fires at most once.
    """
    import datetime as _dt
    _wd_path = pathlib.Path("/tmp/leap-aide-test-server-trace.log")
    def _wd(msg):
        try:
            with _wd_path.open("a") as _f:
                _f.write(f"{_dt.datetime.now().isoformat()} pid={os.getpid()} _fail_fast: {msg}\n")
        except Exception:
            pass
    _wd("yield — test finished, pinging server")
    # Check for a crash marker written by the previous test's teardown.
    # If present, fail the CURRENT test (not the previous one) so we don't
    # trigger pytest's "teardown failed → reset session fixture" machinery,
    # which caused a second server.py spawn in batch-size > 1 runs.
    _crash_marker = pathlib.Path("/tmp/leap-aide-test-server-crash.txt")
    if _crash_marker.exists():
        _wd("found crash marker from previous test — reading it")
        msg = _crash_marker.read_text(encoding="utf-8")
        _crash_marker.unlink()
        pytest.fail(msg)
    yield
    _wd("teardown — pinging server")
    if not _wait_for_server(TEST_BASE, timeout=2):
        _wd("SERVER DEAD — writing fixture-failed marker (no pytest.fail to avoid re-run)")
        log_path = TEST_STATE_DIR / "server.log"
        if log_path.exists():
            tail = log_path.read_text(errors='replace')[-4000:]
        else:
            tail = "<no server.log>"
        # Write a marker file instead of pytest.fail(). pytest.fail() in a
        # teardown fixture triggers pytest's "fixture failed → reset and retry"
        # machinery, which re-enters session-scoped fixtures. That cascade
        # produced a second server.py process mid-session (visible in
        # /tmp/leap-aide-test-server-trace.log). Raising a non-pytest
        # exception also retries. Writing the marker and returning cleanly
        # surfaces the failure to the JUnit XML on the NEXT test's setup
        # boundary without triggering pytest's fixture-reset path.
        crash_marker = pathlib.Path("/tmp/leap-aide-test-server-crash.txt")
        crash_marker.write_text(
            f"test_server died during the last test. tail of {log_path}:\n{tail}",
            encoding="utf-8",
        )
    _wd("server alive — ok")


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
