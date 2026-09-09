"""Contract and integration tests for Gateway Session monitoring (feature 007).

Tests validate:
- SSE endpoint structure and contracts
- Session data model changes (user_id, source_tag, _readonly)
- Cleanup endpoint safety
- Workspace injection
"""
import json
import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()

ROUTES_SRC = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
MODELS_SRC = (REPO_ROOT / "api" / "models.py").read_text(encoding="utf-8")
AGENT_SESSIONS_SRC = (REPO_ROOT / "api" / "agent_sessions.py").read_text(encoding="utf-8")
WORKSPACE_SRC = (REPO_ROOT / "api" / "workspace.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 3: User Story 1 — SSE endpoint contract (T010, T011)
#
# Note: The historical ``_handle_gateway_sse_stream`` handler that emitted
# ``sessions_changed`` SSE events and subscribed to a ``GatewayWatcher``
# queue has been retired. The agent-session SSE feed is now driven by
# ``api/session_watcher.SessionWatcher`` and consumed via a different path;
# the static-assertion tests for the old handler no longer apply and
# were removed.
# ---------------------------------------------------------------------------

def test_gateway_sse_no_hard_show_cli_sessions_block():
    """T007: SSE connection should always be available, no hard show_cli_sessions check."""
    # The hard check was removed — verify the current handler does NOT return 404 for disabled setting
    handler_lines = [
        line.strip() for line in ROUTES_SRC.split("\n")
        if "show_cli_sessions" in line and "agent sessions not enabled" in line
    ]
    assert len(handler_lines) == 0, \
        "SSE handler must not hard-block on show_cli_sessions — frontend toggle controls display"


# ---------------------------------------------------------------------------
# Phase 4: User Story 2 — Read-only session contract (T014)
# ---------------------------------------------------------------------------

def test_cli_session_fallback_includes_readonly():
    """T014: CLI session fallback response must include _readonly: True."""
    assert '"_readonly": True' in ROUTES_SRC, \
        "GET /api/session fallback for CLI sessions must set _readonly: True"


def test_cli_session_fallback_includes_source_tag():
    """T014: CLI session fallback must include source_tag."""
    assert '"source_tag"' in ROUTES_SRC, \
        "GET /api/session fallback must include source_tag from CLI metadata"


def test_cli_session_fallback_includes_user_id():
    """T014: CLI session fallback must include user_id."""
    assert '"user_id"' in ROUTES_SRC, \
        "GET /api/session fallback must include user_id from CLI metadata"


# ---------------------------------------------------------------------------
# Phase 5: User Story 3 — Session grouping (T017, T018)
# ---------------------------------------------------------------------------

def test_models_user_id_in_select():
    """T003: read_importable_agent_session_rows must SELECT s.user_id."""
    assert "s.user_id" in AGENT_SESSIONS_SRC, \
        "read_importable_agent_session_rows must include s.user_id in SELECT"


def test_models_cli_sessions_includes_user_id():
    """T004: get_cli_sessions() must include user_id in output dict."""
    # The user_id field must be in the output dict
    assert "'user_id': _user_id" in MODELS_SRC, \
        "get_cli_sessions() must output user_id field"


def test_models_cli_sessions_includes_source_tag():
    """Regression: source_tag must remain in get_cli_sessions() output."""
    assert "'source_tag': _source" in MODELS_SRC, \
        "get_cli_sessions() must continue to output source_tag"


def test_models_cli_sessions_derived_workspace():
    """T005-revised: get_cli_sessions() must fall back to home directory when cwd is None."""
    assert "Path.home()" in MODELS_SRC, \
        "get_cli_sessions() must fall back to Path.home() when cwd is None (no virtual path)"


# ---------------------------------------------------------------------------
# Phase 6: User Story 4 — Cleanup endpoint (T020, T021)
# ---------------------------------------------------------------------------

def test_cleanup_endpoint_exists():
    """T020-revised: POST /api/sessions/cleanup must be registered (backward compat gateway/cleanup also accepted)."""
    assert "/api/sessions/cleanup" in ROUTES_SRC or "/api/sessions/gateway/cleanup" in ROUTES_SRC, \
        "cleanup endpoint must be registered in handle_post"
    assert "_handle_session_cleanup" in ROUTES_SRC, \
        "cleanup handler function must be defined"


# ---------------------------------------------------------------------------
# Phase 7: User Story 5 — Workspace injection (T025)
# ---------------------------------------------------------------------------

def test_workspaces_endpoint_no_gateway_injection():
    """T025-revised: GET /api/workspaces must NOT inject virtual gateway workspaces."""
    assert "get_gateway_workspaces" not in ROUTES_SRC, \
        "get_gateway_workspaces() must NOT be used in api/routes.py"
    assert "get_gateway_workspaces" not in WORKSPACE_SRC, \
        "get_gateway_workspaces() must NOT be defined in api/workspace.py"


# ---------------------------------------------------------------------------
# TypeScript type definitions (T008, T009)
# ---------------------------------------------------------------------------

def test_types_include_gateway_session_fields():
    """T008: SessionListItem must include gateway session fields."""
    types_src = (REPO_ROOT / "frontend" / "src" / "types" / "index.ts").read_text(encoding="utf-8")
    assert "source_tag?: string" in types_src, \
        "SessionListItem must have source_tag field"
    assert "is_cli_session?: boolean" in types_src, \
        "SessionListItem must have is_cli_session field"
    assert "user_id?: string" in types_src, \
        "SessionListItem must have user_id field"


def test_types_workspace_fields():
    """T009-revised: Workspace must NOT include gateway field, but must keep readonly."""
    types_src = (REPO_ROOT / "frontend" / "src" / "types" / "index.ts").read_text(encoding="utf-8")
    assert "gateway?: boolean" not in types_src, \
        "Workspace interface must NOT have gateway field (virtual workspaces removed)"
    assert "readonly?: boolean" in types_src, \
        "Workspace interface must still have readonly field"
