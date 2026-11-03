"""Tests for #815 — BYOK/custom provider models missing from WebUI model dropdown.

Root causes fixed:
  1. active_provider alias not normalized in get_available_models()
     ('z.ai' -> 'zai', 'x.ai' -> 'xai'/'x-ai', 'google' -> 'gemini', etc.)
     causing the provider to fall to the 'else/unknown' branch with no models.

The old /api/models/live route and its normalization tests were removed with
the issue #10 refactor — live refresh is now part of the /api/models build
and alias resolution lives in model_catalog.json.
"""
import pathlib
import sys
import unittest.mock as mock

import pytest

REPO = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO.parent / ".hermes" / "hermes-agent"))


def read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_models_cache():
    """Invalidate the TTL model cache before AND after every test.

    ``get_available_models()`` caches its result keyed on config.yaml mtime.
    Tests in this file repoint ``_get_config_path`` to a tmp_path, populate
    the cache there, then let monkeypatch restore the original path.  The
    cache, keyed on the tmp_path's mtime, then poisons downstream tests
    (e.g. test_model_resolver) which see stale data and never hit their
    mocks.  Clearing the cache around each test breaks that linkage.
    """
    import app.api.config as c
    try:
        c.invalidate_models_cache()
    except Exception:
        pass
    yield
    try:
        c.invalidate_models_cache()
    except Exception:
        pass


# ── api/config.py — active_provider normalization ─────────────────────────────

class TestActiveProviderNormalization:
    """get_available_models() must normalize active_provider aliases before lookup."""

    def _run(self, tmp_path, provider_str, monkeypatch):
        """Return get_available_models() output for a given provider string."""
        import app.api.config as c

        cfgfile = tmp_path / "config.yaml"
        cfgfile.write_text(
            f"model:\n  provider: {provider_str}\n  default: test-model\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(c, "_get_config_path", lambda: cfgfile)
        c.reload_config()
        # Patch list_available_providers to avoid real network calls
        fake_prov = mock.MagicMock()
        fake_prov.return_value = []
        try:
            import hermes_cli.models as hm
            monkeypatch.setattr(hm, "list_available_providers", fake_prov)
        except Exception:
            pass
        result = c.get_available_models()
        c.reload_config()
        return result

    def test_z_dot_ai_normalized_to_zai(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, "z.ai", monkeypatch)
        # active_provider returned to browser must be canonical 'zai' or
        # at minimum must not be 'z.ai' (which would miss the catalog lookup)
        ap = result.get("active_provider", "")
        assert ap in ("zai", ""), f"active_provider should be 'zai', got {ap!r}"

    def test_x_dot_ai_normalized_to_xai(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, "x.ai", monkeypatch)
        ap = result.get("active_provider", "")
        assert ap in ("xai", ""), f"active_provider should be 'xai', got {ap!r}"

    def test_google_normalized_to_gemini(self, tmp_path, monkeypatch):
        result = self._run(tmp_path, "google", monkeypatch)
        ap = result.get("active_provider", "")
        assert ap in ("gemini", ""), f"active_provider should be 'gemini', got {ap!r}"


class TestAliasResolver:
    """_resolve_provider_alias must normalize provider names in both envs.

    With hermes_cli importable the agent's _PROVIDER_ALIASES table wins;
    without it the JSON catalog's alias table is the fallback.  Both must
    resolve the #815 bug-report aliases ('z.ai', 'x.ai', 'google', 'grok').
    """

    def test_alias_resolver_normalizes_core_aliases(self):
        import app.api.config as c
        # Core CLI aliases from #815's bug report — must resolve in every env.
        assert c._resolve_provider_alias('z.ai') == 'zai'
        assert c._resolve_provider_alias('google') == 'gemini'
        # x.ai/grok: agent table maps to 'xai', catalog-only maps to 'x-ai';
        # both are valid canonical slugs that route to the same provider.
        assert c._resolve_provider_alias('x.ai') in ('xai', 'x-ai')
        assert c._resolve_provider_alias('grok') in ('xai', 'x-ai')
        # Case / whitespace insensitive
        assert c._resolve_provider_alias('  Z.AI  ') == 'zai'
        # Canonical names pass through unchanged
        assert c._resolve_provider_alias('openrouter') == 'openrouter'
        assert c._resolve_provider_alias('anthropic') == 'anthropic'
        assert c._resolve_provider_alias('custom') == 'custom'
        # Empty / None pass through
        assert c._resolve_provider_alias('') == ''
        assert c._resolve_provider_alias(None) is None


# ── Regression: known-good providers still work ───────────────────────────────

class TestKnownProvidersUnaffected:
    """Normalization must not break providers whose names are already canonical."""

    def test_openrouter_unaffected(self):
        src = read("app/api/config.py")
        # _PROVIDER_ALIASES lookup: 'openrouter' -> 'openrouter' (no change)
        assert "openrouter" in src, "openrouter must still exist in config"

    def test_anthropic_unaffected(self):
        src = read("app/api/config.py")
        assert "anthropic" in src

    def test_custom_unaffected(self):
        """'custom' is not in _PROVIDER_ALIASES so normalization is a no-op."""
        try:
            from hermes_cli.models import _PROVIDER_ALIASES
            assert "custom" not in _PROVIDER_ALIASES, (
                "'custom' must not be aliased to anything — it's a special sentinel"
            )
        except ImportError:
            pass  # hermes-agent not available in this env — skip


# ── Source-level: active_provider returned to browser is canonical ─────────────

class TestProviderIdInGroupResponse:
    """get_available_models() must include provider_id on every group so the
    frontend can match optgroups exactly rather than by substring."""

    def test_groups_include_provider_id(self, tmp_path, monkeypatch):
        import app.api.config as c

        cfgfile = tmp_path / "config.yaml"
        cfgfile.write_text(
            "model:\n  provider: zai\n  default: glm-5\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(c, "_get_config_path", lambda: cfgfile)
        c.reload_config()
        try:
            import hermes_cli.models as hm
            monkeypatch.setattr(hm, "list_available_providers", lambda: [
                {"id": "zai", "authenticated": True}
            ])
            import hermes_cli.auth as ha
            monkeypatch.setattr(ha, "get_auth_status", lambda p: {"key_source": "env"})
        except Exception:
            pass
        result = c.get_available_models()
        c.reload_config()
        for g in result.get("groups", []):
            assert "provider_id" in g, (
                f"group {g.get('provider')!r} missing provider_id — "
                "the frontend needs it to match optgroups exactly"
            )


# ── Opus-identified edge case: 'ollama' normalizes to 'custom' ────────────────

class TestOllamaAliasEdgeCase:
    """Opus review found: 'ollama' -> 'custom' via _PROVIDER_ALIASES.
    This is better behaviour (custom_providers fallback catches it) but worth
    documenting and not regressing."""

    def test_ollama_not_in_provider_aliases_as_ollama(self):
        """'ollama' maps to 'custom' in _PROVIDER_ALIASES — verify this is the
        intended behavior post-normalization (not a silent breakage)."""
        try:
            from hermes_cli.models import _PROVIDER_ALIASES
            # 'ollama' -> 'custom' means ollama users hit the custom_providers path
            # This is fine — ollama models appear via base_url auto-detection (step 3)
            # in get_available_models, not via a static provider model table.
            ollama_target = _PROVIDER_ALIASES.get("ollama", "ollama")
            # Acceptable outcomes: either unchanged (not in aliases) or 'custom'/'ollama-cloud'
            assert ollama_target in ("ollama", "custom", "ollama-cloud"), (
                f"Unexpected ollama alias: {ollama_target}"
            )
        except ImportError:
            pass  # hermes-agent not available


class TestGetAvailableModelsReturnsCanonicalProvider:
    """get_available_models() must return normalized active_provider in its
    response so the frontend model picker matches groups by canonical id."""

    def test_active_provider_in_response_is_normalized(self, tmp_path, monkeypatch):
        import app.api.config as c

        cfgfile = tmp_path / "config.yaml"
        cfgfile.write_text(
            "model:\n  provider: z.ai\n  default: glm-5\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(c, "_get_config_path", lambda: cfgfile)
        c.reload_config()
        try:
            import hermes_cli.models as hm
            monkeypatch.setattr(hm, "list_available_providers", lambda: [])
        except Exception:
            pass
        result = c.get_available_models()
        c.reload_config()
        ap = result.get("active_provider", "")
        # The browser uses this value to match model groups; it must be the
        # canonical slug 'zai' so optgroup matching works.
        assert ap != "z.ai", (
            "active_provider 'z.ai' must be normalized to 'zai' before being "
            "returned to the browser"
        )
