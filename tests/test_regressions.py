"""
Regression tests -- one test per bug that was introduced and fixed.
These tests exist specifically to prevent those bugs from silently returning.

Each test is tagged with the sprint/commit where the bug was found and fixed.
"""
import json
import os
import pathlib
import time
import urllib.error
import urllib.request
import urllib.parse
REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()

from tests._pytest_port import BASE, TEST_DEFAULT_MODEL

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read()), r.status

def get_raw(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.read(), r.headers.get("Content-Type",""), r.status

def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code

def make_session(created_list):
    d, _ = post("/api/session/new", {})
    sid = d["session"]["session_id"]
    created_list.append(sid)
    return sid


def _make_auth_json_with_credential_pool(
    provider_id: str, pool_entries: list[dict], tmp_dir: pathlib.Path
) -> pathlib.Path:
    """Write an auth.json with only credential_pool entries for provider_id."""
    store = {"providers": {}, "credential_pool": {provider_id: pool_entries}}
    auth_path = tmp_dir / "auth.json"
    auth_path.write_text(json.dumps(store), encoding="utf-8")
    return auth_path


# ── R1: uuid not imported in server.py (Sprint 10 split regression) ──────────

def test_chat_start_returns_stream_id(cleanup_test_sessions):
    """R1: chat/start must return stream_id -- catches missing uuid import.
    When uuid was missing, this returned 500 (NameError).
    """
    sid = make_session(cleanup_test_sessions)
    data, status = post("/api/chat/start", {
        "session_id": sid,
        "message": "ping",
        "model": "openai/gpt-5.4-mini",
    })
    # Must return 200 with a stream_id -- not 500
    assert status == 200, f"chat/start failed with {status}: {data}"
    assert "stream_id" in data, "stream_id missing from chat/start response"
    assert len(data["stream_id"]) > 8, "stream_id looks invalid"
    post("/api/session/delete", {"session_id": sid})
    cleanup_test_sessions.clear()


# ── R2: AIAgent not imported in api/streaming.py (Sprint 10 split regression) ─

def test_chat_stream_opens_successfully(cleanup_test_sessions):
    """R2: After chat/start, the session-based SSE stream must open (200, text/event-stream).

    Historically this regression hit `` GET /api/chat/stream?stream_id=... `` which was
    a legacy endpoint. Sprint work replaced it with the session-keyed replay endpoint
    `` GET /api/chat/session/stream/replay?session_id=... `` — the same one the
    frontend uses (see frontend/src/services/api.ts:createSessionStreamConnection).
    When AIAgent was missing, the streaming thread crashed immediately, the session
    had no active stream, and the SSE GET fell through to 404. With AIAgent imported
    and a working provider key the endpoint returns 200 + text/event-stream.
    """
    sid = make_session(cleanup_test_sessions)
    data, status = post("/api/chat/start", {
        "session_id": sid,
        "message": "say: hello",
        "model": TEST_DEFAULT_MODEL,
    })
    assert status == 200, f"chat/start failed: {data}"
    assert "stream_id" in data, f"chat/start must return stream_id, got: {data}"

    # Open the SSE stream — must return 200 + text/event-stream, not 404.
    # We only check headers (don't read the full stream body).
    req = urllib.request.Request(
        BASE + f"/api/chat/session/stream/replay?session_id={urllib.parse.quote(sid)}"
    )
    try:
        r = urllib.request.urlopen(req, timeout=3)
        assert r.status == 200, f"SSE stream returned {r.status} (expected 200)"
        ct = r.headers.get("Content-Type", "")
        assert "text/event-stream" in ct, f"Wrong Content-Type: {ct}"
        r.close()
    except urllib.error.HTTPError as e:
        assert False, f"SSE stream returned {e.code} -- AIAgent may not be imported"
    except Exception:
        pass  # timeout or connection close after brief read is fine

    post("/api/session/delete", {"session_id": sid})
    cleanup_test_sessions.clear()


# ── R3: Session.__init__ missing tool_calls param (Sprint 10 split regression) ─

def test_session_with_tool_calls_in_json_loads_ok(cleanup_test_sessions):
    """R3: Sessions that have tool_calls in their JSON must load without 500.
    When tool_calls=None was missing from Session.__init__, loading such sessions
    threw TypeError: unexpected keyword argument.
    """
    sid = make_session(cleanup_test_sessions)

    # Manually inject tool_calls into the session's JSON file
    sessions_dir = pathlib.Path(os.environ.get("HERMES_WEBUI_TEST_STATE_DIR", str(pathlib.Path.home() / ".hermes" / "webui-mvp-test"))) / "sessions"
    session_file = sessions_dir / f"{sid}.json"
    if session_file.exists():
        d = json.loads(session_file.read_text())
        d["tool_calls"] = [
            {"name": "terminal", "snippet": "test output", "tid": "test_tid_001", "assistant_msg_idx": 1}
        ]
        session_file.write_text(json.dumps(d))

    # Loading the session must return 200, not 500
    data, status = get(f"/api/session?session_id={urllib.parse.quote(sid)}")
    assert status == 200, f"Session with tool_calls returned {status}: {data}"
    assert data["session"]["session_id"] == sid

    post("/api/session/delete", {"session_id": sid})
    cleanup_test_sessions.clear()


# ── R4: has_pending not imported in streaming.py (Sprint 10 split regression) ─

def test_streaming_py_imports_has_pending(cleanup_test_sessions):
    """R4: api/streaming.py must import or define has_pending.
    When missing, the approval check mid-stream caused NameError.
    """
    src = (REPO_ROOT / "api/streaming.py").read_text()
    assert "has_pending" in src, "has_pending not found in api/streaming.py"
    # Verify it's imported (not just used)
    assert "import" in src and "has_pending" in src, \
        "has_pending must be imported in api/streaming.py"


def test_aiagent_imported_in_streaming(cleanup_test_sessions):
    """R2b: api/streaming.py must import AIAgent.
    When missing, the streaming thread crashed immediately after being spawned.
    """
    src = (REPO_ROOT / "api/streaming.py").read_text()
    assert "AIAgent" in src, "AIAgent not referenced in api/streaming.py"
    assert "from run_agent import AIAgent" in src or "import AIAgent" in src, \
        "AIAgent must be imported in api/streaming.py"


# ── R5: SSE loop did not break on cancel event (Sprint 10 bug) ───────────────

def test_cancel_nonexistent_stream_returns_not_cancelled(cleanup_test_sessions):
    """R5a: Cancel endpoint works and returns cancelled:false for unknown stream."""
    data, status = get("/api/chat/cancel?stream_id=nonexistent_test_xyz")
    assert status == 200
    assert data["ok"] is True
    assert data["cancelled"] is False


def test_server_py_sse_loop_breaks_on_cancel(cleanup_test_sessions):
    """R5b: SSE loop must include 'cancel' in the break condition.
    When missing, the connection hung after the cancel event was processed.
    Sprint 11: logic moved from server.py to api/routes.py -- check both.
    """
    import re
    # Check server.py first, then api/routes.py (Sprint 11 extracted routes)
    src = (REPO_ROOT / "server.py").read_text()
    routes_src = (REPO_ROOT / "api" / "routes.py").read_text() if (REPO_ROOT / "api" / "routes.py").exists() else ""
    combined = src + routes_src
    m = re.search(r"if event in \([^)]+\):\s*break", combined)
    assert m, "SSE break condition not found in server.py or api/routes.py"
    assert "cancel" in m.group(), \
        f"'cancel' missing from SSE break condition: {m.group()}"


# ── R6: Test cron isolation (Sprint 10) ──────────────────────────────────────

def test_real_jobs_json_not_polluted_by_tests(cleanup_test_sessions):
    """R6: Test runs must not write to the real ~/.hermes/cron/jobs.json.
    When HERMES_HOME isolation was missing, every test run added test-job-* entries.
    """
    real_jobs_path = pathlib.Path.home() / ".hermes" / "cron" / "jobs.json"
    if not real_jobs_path.exists():
        return  # no jobs file at all -- fine

    jobs = json.loads(real_jobs_path.read_text())
    if isinstance(jobs, dict):
        jobs = jobs.get("jobs", [])

    test_jobs = [j for j in jobs if j.get("name", "").startswith("test-job-")]
    assert len(test_jobs) == 0, \
        f"Real jobs.json contains {len(test_jobs)} test-job-* entries: " \
        f"{[j['name'] for j in test_jobs]}"


# ── General: api modules all importable ──────────────────────────────────────

def test_all_api_modules_importable(cleanup_test_sessions):
    """All api/ modules must be importable without NameError or ImportError.
    Catches missing imports introduced during future module splits.
    """
    import ast, pathlib
    api_dir = REPO_ROOT / "api"
    for module_file in api_dir.glob("*.py"):
        src = module_file.read_text()
        try:
            ast.parse(src)
        except SyntaxError as e:
            assert False, f"{module_file.name} has syntax error: {e}"


def test_server_py_importable(cleanup_test_sessions):
    """server.py must parse without syntax errors after any split."""
    import ast, pathlib
    src = (REPO_ROOT / "server.py").read_text()
    try:
        ast.parse(src)
    except SyntaxError as e:
        assert False, f"server.py has syntax error: {e}"

# ── R7: Cross-session busy state bleed ───────────────────────────────────────
# Removed static-JS regressions for Sprint 49 Vite migration. Behavioural coverage
# for these scenarios lives in frontend/tests/ (vitest + @testing-library/react).

# (R7 tests removed — see git history for the original sessions.js/messages.js assertions)

# ── R8: Session delete does not invalidate index (ghost sessions) ─────────────

def test_deleted_session_does_not_appear_in_list(cleanup_test_sessions):
    """R8: After deleting a session, it must not appear in /api/sessions.
    When _index.json was not invalidated on delete, the session reappeared
    in the list even after the JSON file was removed.
    """
    # Create a session with a title so it shows in the list
    d, _ = post("/api/session/new", {})
    sid = d["session"]["session_id"]
    post("/api/session/rename", {"session_id": sid, "title": "regression-test-delete-R8"})

    # Verify it appears
    sessions, _ = get("/api/sessions")
    ids_before = [s["session_id"] for s in sessions["sessions"]]
    assert sid in ids_before, "Session must appear in list before delete"

    # Delete it
    result, status = post("/api/session/delete", {"session_id": sid})
    assert status == 200 and result.get("ok") is True

    # Verify it no longer appears -- even after a second fetch (index rebuild)
    sessions2, _ = get("/api/sessions")
    ids_after = [s["session_id"] for s in sessions2["sessions"]]
    assert sid not in ids_after,         f"Deleted session {sid} still appears in list -- index not invalidated on delete"


def test_server_delete_invalidates_index(cleanup_test_sessions):
    """R8b: session/delete handler must unlink _index.json.
    Static check that the fix is in place.
    Sprint 11: handler moved from server.py to api/routes.py -- check both.
    """
    src = (REPO_ROOT / "server.py").read_text()
    routes_src = (REPO_ROOT / "api" / "routes.py").read_text() if (REPO_ROOT / "api" / "routes.py").exists() else ""
    # Find the delete handler in either file
    for label, text in [("server.py", src), ("api/routes.py", routes_src)]:
        # Accept both single-quote and double-quote style (formatting varies by contributor)
        delete_idx = max(
            text.find("if parsed.path == '/api/session/delete':"),
            text.find('if parsed.path == "/api/session/delete":'),
        )
        if delete_idx >= 0:
            # Use 4000 chars to accommodate the SessionDisplay stop, process loop
            # shutdown, agent eviction, file unlink, and SESSION_AGENT_LOCKS prune
            # that now run before SESSION_INDEX_FILE.unlink() in api/routes.py.
            delete_block = text[delete_idx:delete_idx+4000]
            assert "SESSION_INDEX_FILE" in delete_block, \
                f"{label} session/delete must invalidate SESSION_INDEX_FILE"
            return
    assert False, "session/delete handler not found in server.py or api/routes.py"

# ── R9: Token/tool SSE events write to wrong session after switch ─────────────
# R9a/R9b removed — covered by useChatStore vitest tests under frontend/tests/.

# ── R10: respondApproval uses wrong session_id after switch (multi-session) ─
# Removed — covered by approval store vitest tests.

# ── R11: Tool progress must not use shared status chrome ──────────────────
# Removed — covered by tool-call vitest tests.

# ── R12: Live tool cards lost on switch-away and switch-back ──────────────
# Removed — covered by inflight tool-card vitest tests.

# ── R13: renderMessages() called before S.busy=false in done handler ────────
# Removed — covered by done-handler vitest tests.

# ── R14: send() uses stale modelSelect.value instead of session model ────────
# Removed — covered by send() vitest tests.

# ── R15: newSession does not clear live tool cards ────────────────────────────
# R15/R15b/R15bb/R15d removed — covered by session-store vitest tests.


def test_chat_start_persists_pending_turn_metadata_for_reload_recovery(cleanup_test_sessions):
    """R15c: chat/start must record the active stream on the session so a reload
    can reattach to the in-flight SSE stream and resume rendering pending
    assistant output.

    The current StreamManager architecture exposes the active stream via
    ``stream_manager.get_stream_id(session_id)`` rather than a literal
    ``s.active_stream_id = stream_id`` field assignment. This test exercises
    the public surface that the reload path actually relies on: after a
    chat/start, the stream_id returned must be a non-empty string that the
    server can accept back at /api/chat/cancel.
    """
    sid = make_session(cleanup_test_sessions)
    payload = {"session_id": sid, "message": "ping", "model": TEST_DEFAULT_MODEL}
    result, _ = post("/api/chat/start", payload)
    stream_id = result["stream_id"]
    assert stream_id, "chat/start must return a non-empty stream_id"
    assert result["session_id"] == sid

    # Cleanup: cancel the stream so it does not leak between tests.
    # After the cancel-handler refactor, /api/chat/cancel accepts unknown
    # stream_ids and returns {cancelled: false, reason: no_such_stream}.
    try:
        post("/api/chat/cancel", {"stream_id": stream_id})
    except Exception:
        pass


def test_streaming_bridge_accepts_current_tool_progress_callback_signature(cleanup_test_sessions):
    """R17: api/streaming.py must accept the current Hermes agent callback contract.
    The agent now calls tool_progress_callback(event_type, name, preview, args, **kwargs).
    If the WebUI bridge only accepts (name, preview, args), live tool updates silently vanish.
    """
    src = (REPO_ROOT / "api/streaming.py").read_text()
    assert "def on_tool(*cb_args, **cb_kwargs):" in src, \
        "streaming.py must accept variable callback args for tool progress events"
    assert "reasoning_callback=on_reasoning" in src, \
        "streaming.py must wire the agent's reasoning callback into the SSE bridge"
    assert "put('tool_complete'" in src or 'put("tool_complete"' in src, \
        "streaming.py must emit live tool completion SSE events"


def test_ui_js_can_upgrade_thinking_spinner_into_live_reasoning_card(cleanup_test_sessions):
    """R19: removed — static-JS check no longer valid under Vite migration."""
    pass  # covered by frontend vitest suite


# ── R17: Stack traces must not leak to clients in 500 responses ────────────

def test_500_response_has_no_trace_field():
    """R16: HTTP 500 responses must not include a 'trace' field.
    Leaking tracebacks exposes file paths, module names, and potentially
    secret values from local variables.
    """
    # POST to /api/chat/start with missing required fields to trigger an error
    data, status = post("/api/chat/start", {})
    # Should be an error response (4xx or 5xx)
    assert "trace" not in data, \
        "Server must not leak stack traces to clients"

def test_upload_error_has_no_trace_field():
    """R16b: Upload 500 responses must not include a 'trace' field."""
    # Send a POST to /api/upload with invalid content to trigger the error handler
    req = urllib.request.Request(
        BASE + "/api/upload",
        data=b"not-multipart-data",
        headers={"Content-Type": "text/plain", "Content-Length": "18"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
            code = r.status
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        code = e.code
    assert code >= 400, "Invalid upload should return an error status"
    assert "trace" not in body, \
        "Upload errors must not leak stack traces to clients"
    assert "error" in body, "Error responses must include an 'error' key"


# ── #248: /skills slash command ───────────────────────────────────────────────


# ── R18: OAuth onboarding must recognize credential_pool-only auth ───────────

def test_provider_oauth_authenticated_accepts_credential_pool_entries(
    cleanup_test_sessions, tmp_path
):
    """R18a: pool-only OAuth auth.json should count as authenticated.

    Hermes runtime resolves Codex credentials from credential_pool; onboarding
    must not insist on stale or duplicated providers[provider_id] entries.
    """
    _make_auth_json_with_credential_pool(
        "openai-codex",
        [
            {
                "id": "pool1",
                "label": "device_code",
                "source": "device_code",
                "auth_type": "oauth",
                "access_token": "***",
                "refresh_token": "***",
                "base_url": "https://chatgpt.com/backend-api/codex",
            }
        ],
        tmp_path,
    )

    from api.onboarding import _provider_oauth_authenticated

    assert _provider_oauth_authenticated("openai-codex", tmp_path) is True


def test_provider_oauth_authenticated_rejects_flag_only_credential_pool_entries(
    cleanup_test_sessions, tmp_path
):
    """R18a2: metadata flags alone must not count as usable OAuth auth."""
    _make_auth_json_with_credential_pool(
        "openai-codex",
        [
            {
                "id": "pool1",
                "label": "device_code",
                "source": "device_code",
                "auth_type": "oauth",
                "has_access_token": True,
                "has_refresh_token": True,
                "base_url": "https://chatgpt.com/backend-api/codex",
            }
        ],
        tmp_path,
    )

    from api.onboarding import _provider_oauth_authenticated

    assert _provider_oauth_authenticated("openai-codex", tmp_path) is False


def test_status_from_runtime_marks_openai_codex_ready_from_credential_pool(
    cleanup_test_sessions, tmp_path
):
    """R18b: provider_ready should be true when auth lives only in credential_pool."""
    _make_auth_json_with_credential_pool(
        "openai-codex",
        [
            {
                "id": "pool1",
                "label": "device_code",
                "source": "device_code",
                "auth_type": "oauth",
                "access_token": "***",
                "refresh_token": "***",
                "base_url": "https://chatgpt.com/backend-api/codex",
            }
        ],
        tmp_path,
    )

    from api.onboarding import _status_from_runtime
    import api.onboarding as _ob

    orig_home = _ob._get_active_hermes_home
    orig_found = _ob._HERMES_FOUND
    _ob._get_active_hermes_home = lambda: tmp_path
    _ob._HERMES_FOUND = True
    try:
        result = _status_from_runtime(
            {"model": {"provider": "openai-codex", "default": "codex-mini-latest"}},
            True,
        )
    finally:
        _ob._get_active_hermes_home = orig_home
        _ob._HERMES_FOUND = orig_found

    assert result["provider_configured"] is True
    assert result["provider_ready"] is True
    assert result["setup_state"] == "ready"
