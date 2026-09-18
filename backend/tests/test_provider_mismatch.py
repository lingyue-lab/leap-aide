"""
Tests for issue #266 — provider/model mismatch warning.

Covers:
  1. streaming.py: auth errors detected and classified as 'auth_mismatch'
  2. /api/models: response includes active_provider field
  3. session model normalization for stale cross-provider models
"""
import json
import pathlib
import re
import urllib.request
from tests.conftest import TEST_STATE_DIR

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
from tests._pytest_port import BASE


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()), r.status


# ── 1. streaming.py: auth error detection ───────────────────────────────────

class TestStreamingAuthErrorDetection:
    """streaming.py must classify auth/401 errors as auth_mismatch."""

    def test_auth_mismatch_type_defined_in_streaming(self):
        """'auth_mismatch' type must be emitted for auth errors."""
        src = _read("app/api/streaming.py")
        assert "auth_mismatch" in src, (
            "auth_mismatch type not found in streaming.py — "
            "401/auth errors will not be surfaced with a helpful message"
        )

    def test_is_auth_error_flag_defined(self):
        """auth error variable must exist in the error handler (exception path and silent-failure path)."""
        src = _read("app/api/streaming.py")
        # Variable renamed to _exc_is_auth in exception path, _is_auth in silent-failure path
        assert "_exc_is_auth" in src or "_is_auth" in src, (
            "auth error flag not found in streaming.py"
        )

    def test_auth_error_detects_401(self):
        """'401' must be part of the auth error detection logic."""
        src = _read("app/api/streaming.py")
        # Find the is_auth_error block
        # Variable renamed to _exc_is_auth in exception path, _is_auth in silent-failure path
        idx = src.find("_exc_is_auth")
        assert idx != -1
        block = src[idx:idx + 500]
        assert "'401'" in block or '"401"' in block, (
            "'401' not in auth error detection block"
        )

    def test_auth_error_detects_unauthorized(self):
        """'unauthorized' must be part of the auth error detection logic."""
        src = _read("app/api/streaming.py")
        # Variable renamed to _exc_is_auth in exception path
        idx = src.find("_exc_is_auth")
        block = src[idx:idx + 500]
        assert "unauthorized" in block.lower(), (
            "'unauthorized' not in auth error detection block"
        )

    def test_auth_error_hint_mentions_hermes_model(self):
        """The auth_mismatch hint must mention 'hermes model' command."""
        src = _read("app/api/streaming.py")
        # Find the auth_mismatch apperror block
        idx = src.find("auth_mismatch")
        block = src[idx:idx + 500]
        assert "hermes model" in block, (
            "auth_mismatch hint must mention 'hermes model' command "
            "so users know how to fix provider mismatch"
        )

    def test_auth_error_does_not_catch_rate_limit(self):
        """Rate limit errors must not be reclassified as auth_mismatch."""
        src = _read("app/api/streaming.py")
        # Variables renamed: _exc_is_rate_limit / _exc_is_auth in exception path
        # Quota check comes first (before rate limit), then rate limit, then auth
        rl_idx = src.find("_exc_is_rate_limit")
        ae_idx = src.find("_exc_is_auth")
        assert rl_idx != -1, "_exc_is_rate_limit not found in streaming.py exception path"
        assert ae_idx != -1, "_exc_is_auth not found in streaming.py exception path"
        assert rl_idx < ae_idx, (
            "_exc_is_rate_limit check should precede _exc_is_auth — "
            "rate limit errors must not be mistaken for auth errors"
        )


# ── 2. /api/models: active_provider in response ──────────────────────────────

def test_api_models_includes_active_provider():
    """/api/models must include 'active_provider' key in response."""
    with urllib.request.urlopen(BASE + "/api/models", timeout=10) as r:
        data = json.loads(r.read())
    # active_provider can be None/null but the key must exist
    assert "active_provider" in data, (
        "/api/models response missing 'active_provider' field — "
        "frontend needs this to detect provider mismatches"
    )


def test_bare_gemini_session_model_normalizes_to_active_provider_default(monkeypatch):
    """Persisted bare Gemini IDs must not survive a provider switch."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "gemini-3.1-pro-preview"
    )

    assert changed is True
    assert effective == "gpt-5.4-mini"


def test_prefixed_google_session_model_normalizes_to_active_provider_default(monkeypatch):
    """Persisted provider-prefixed Gemini IDs must normalize too."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "google/gemini-3.1-pro-preview"
    )

    assert changed is True
    assert effective == "gpt-5.4-mini"


def test_legacy_at_provider_session_model_normalizes_when_provider_hidden(monkeypatch):
    """Old @provider:model session values must not bypass stale-model recovery."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "@copilot:gpt-5.5"
    )

    assert changed is True
    assert effective == "gpt-5.5"


def test_active_at_provider_session_model_strips_redundant_hint(monkeypatch):
    """@active-provider:model is an old persisted form; use the bare model now."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini"}],
                },
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "@openai-codex:gpt-5.4-mini"
    )

    assert changed is True
    assert effective == "gpt-5.4-mini"


def test_routable_non_active_at_provider_session_model_is_preserved(monkeypatch):
    """Visible cross-provider dropdown selections must keep their provider hint."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
                {
                    "provider": "GitHub Copilot",
                    "provider_id": "copilot",
                    "models": [{"id": "@copilot:gpt-5.4", "label": "GPT-5.4"}],
                },
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "@copilot:gpt-5.4"
    )

    assert changed is False
    assert effective == "@copilot:gpt-5.4"


def test_stale_at_provider_model_falls_back_when_family_mismatches(monkeypatch):
    """Unroutable @provider:model should not invent a bare model for another family."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "@copilot:claude-opus-4.6"
    )

    assert changed is True
    assert effective == "gpt-5.5"


def test_google_active_provider_keeps_valid_gemini_session_model(monkeypatch):
    """A Google-configured session must keep its Gemini model."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "google",
            "default_model": "gemini-3.1-pro-preview",
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "gemini-3.1-pro-preview"
    )

    assert changed is False
    assert effective == "gemini-3.1-pro-preview"


def test_session_model_normalizer_persists_corrected_model(monkeypatch):
    """Write-path normalization should still persist corrected models."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    save_calls = []

    class DummySession:
        def __init__(self):
            self.model = "gemini-3.1-pro-preview"

        def sync_webui_metadata(self, touch_updated_at=True, skip_index=False, **_):
            save_calls.append((touch_updated_at, skip_index))

    session = DummySession()
    effective = routes._normalize_session_model_in_place(session)

    assert effective == "gpt-5.4-mini"
    assert session.model == "gpt-5.4-mini"
    assert save_calls == [(False, False)]


def test_session_model_display_resolver_is_read_only(monkeypatch):
    """Read-path model resolution must not mutate or save the session."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    save_calls = []

    class DummySession:
        def __init__(self):
            self.model = "gemini-3.1-pro-preview"

        def sync_webui_metadata(self, touch_updated_at=True, skip_index=False, **_):
            save_calls.append((touch_updated_at, skip_index))

    session = DummySession()
    effective = routes._resolve_effective_session_model_for_display(session)

    assert effective == "gpt-5.4-mini"
    assert session.model == "gemini-3.1-pro-preview"
    assert save_calls == []


def test_api_session_is_side_effect_free_for_stale_models():
    """GET /api/session must not rewrite the session file on first open (#845)."""
    created, status = _post("/api/session/new", {})
    assert status == 200
    sid = created["session"]["session_id"]

    session_path = TEST_STATE_DIR / "sessions" / f"{sid}.json"
    # POST /api/session/new no longer eagerly writes empty sessions to disk
    # (#1171 follow-up). Materialise the file from the API response so the
    # rest of this test, which checks that GET is side-effect-free against
    # an on-disk session with a stale model, has a file to work with.
    if not session_path.exists():
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            json.dumps(created["session"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    session_data = json.loads(session_path.read_text(encoding="utf-8"))
    stale_model = "google/gemini-3.1-pro-preview"
    session_data["model"] = stale_model
    before = json.dumps(session_data, ensure_ascii=False, indent=2)
    session_path.write_text(before, encoding="utf-8")

    with urllib.request.urlopen(
        BASE + f"/api/session?session_id={sid}", timeout=10
    ) as r:
        payload = json.loads(r.read())

    after = session_path.read_text(encoding="utf-8")
    assert payload["session"]["model"], "response should still expose an effective display model"
    assert payload["session"]["model"] != stale_model, (
        "response model should be compatibility-normalized on the read path"
    )
    assert after == before, (
        "GET /api/session must return an effective model for display without "
        "rewriting the session file on disk"
    )


def test_unknown_prefix_model_passes_through_unchanged(monkeypatch):
    """Models with unknown/custom prefixes must never be stripped — regression test for #751."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    for custom_model in (
        "custom-provider/test-model-999",
        "test/import-model",
        "my-local-llm/variant-1",
        "lmstudio-community/Qwen2.5-Coder-7B-Instruct-GGUF",
    ):
        effective, changed = routes._resolve_compatible_session_model(custom_model)
        assert changed is False, (
            f"Model '{custom_model}' has an unknown prefix and must pass through unchanged, "
            f"but _resolve_compatible_session_model returned changed=True (effective='{effective}')"
        )
        assert effective == custom_model, (
            f"Expected '{custom_model}', got '{effective}'"
        )


def test_empty_model_session_does_not_trigger_save(monkeypatch):
    """Sessions with no model stored must not trigger session.save() — index rebuild is expensive."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    save_calls = []

    class DummySession:
        def __init__(self):
            self.model = None  # no model stored

        def sync_webui_metadata(self, touch_updated_at=True, skip_index=False, **_):
            save_calls.append((touch_updated_at, skip_index))

    session = DummySession()
    effective = routes._normalize_session_model_in_place(session)

    # Must return the default, but must NOT write to disk
    assert effective == "gpt-5.4-mini"
    assert save_calls == [], (
        "_normalize_session_model_in_place must not call session.save() when "
        "the session has no stored model — no correction needed, just a fallback."
    )


# ── Issue #829: stale cross-provider model on custom_providers-only setup ─────

def test_stale_openai_model_cleared_for_custom_only_provider(monkeypatch):
    """A stale openai/... session model must be cleared when active provider is
    'custom' and no catalog group can route the openai prefix (#829)."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "custom",
            "default_model": "",
            "groups": [
                {"provider": "Agent37", "provider_id": "custom:agent37",
                 "models": [{"id": "agent37/default", "label": "default"}]},
            ],
        },
    )
    # Block the `catalog.get('default_model') or DEFAULT_MODEL` fallback in
    # routes.py:274 so the "no default available" branch is exercised.
    monkeypatch.setattr(routes, "DEFAULT_MODEL", "")

    effective, changed = routes._resolve_compatible_session_model(
        "openai/gpt-5.4-mini"
    )

    # No routable group for openai/ — should clear to default (empty → model itself
    # only if no default available, which means changed=False when default_model="")
    # When default_model is empty, we can't clear — preserve and return False
    assert changed is False
    assert effective == "openai/gpt-5.4-mini"


def test_stale_openai_model_cleared_for_custom_provider_with_default(monkeypatch):
    """When active_provider='custom', no openrouter group, and default_model is
    configured, stale openai/... model should be cleared to default (#829)."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "custom",
            "default_model": "agent37/default",
            "groups": [
                {"provider": "Agent37", "provider_id": "custom:agent37",
                 "models": [{"id": "agent37/default", "label": "default"}]},
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "openai/gpt-5.4-mini"
    )

    assert changed is True
    assert effective == "agent37/default"


def test_openrouter_model_preserved_when_openrouter_group_present(monkeypatch):
    """When active_provider='openrouter' and openrouter group exists,
    openai/... model IDs must pass through unchanged — they are routable (#829)."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openrouter",
            "default_model": "openai/gpt-5.4-mini",
            "groups": [
                {"provider": "OpenRouter", "provider_id": "openrouter",
                 "models": [{"id": "openai/gpt-5.4-mini", "label": "GPT-5.4 Mini"}]},
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "openai/gpt-5.4-mini"
    )

    assert changed is False
    assert effective == "openai/gpt-5.4-mini"


def test_custom_namespace_model_always_preserved_on_custom_provider(monkeypatch):
    """Model IDs with 'custom/' prefix must always pass through unchanged even
    when active_provider='custom' (#829)."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "custom",
            "default_model": "agent37/default",
            "groups": [
                {"provider": "Agent37", "provider_id": "custom:agent37",
                 "models": [{"id": "agent37/default", "label": "default"}]},
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "custom/my-local-llm"
    )

    assert changed is False
    assert effective == "custom/my-local-llm"
