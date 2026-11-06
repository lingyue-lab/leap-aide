"""Keyless providers must disappear from the catalog together with their models.

Reproduction: set the default model to Tencent TokenHub's live-only
``hy4-preview``, then delete the provider's API key in Settings.  The active
provider used to be kept unconditionally, so its JSON baseline
(``hy3-preview``) kept showing in the picker although no request could
succeed.  The catalog now drops catalog API-key providers without a key,
reports the default as definitively unavailable, and exposes whether the
default's provider has a key so the UI can explain the empty picker.
"""
from __future__ import annotations

import sys
import types

import pytest

import app.api.config as config
import app.api.model_catalog as model_catalog
import app.api.profiles as profiles


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Offline hermes_cli, empty profile dir, and no provider key env vars."""
    fake_pkg = types.ModuleType("hermes_cli")
    fake_pkg.__path__ = []
    fake_models = types.ModuleType("hermes_cli.models")
    fake_models.list_available_providers = lambda: []
    fake_auth = types.ModuleType("hermes_cli.auth")
    fake_auth.get_auth_status = lambda _pid: {}
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.models", fake_models)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", fake_auth)
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

    for env_vars in model_catalog.catalog_env_vars().values():
        for var in env_vars:
            monkeypatch.delenv(var, raising=False)

    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    try:
        yield tmp_path
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()


def _build_with_model(monkeypatch, model_cfg: dict) -> dict:
    config.cfg["model"] = dict(model_cfg)
    # Keep the live resolver out of the test: with no monkeypatch this would
    # perform a real HTTP query for providers that have a key.
    monkeypatch.setattr(
        config,
        "_resolve_detected_provider_models",
        lambda *args, **kwargs: ({}, set()),
    )
    return config._build_available_models_uncached()


def test_keyless_active_api_provider_is_hidden_with_its_models(
    monkeypatch, isolated_home
):
    result = _build_with_model(
        monkeypatch,
        {"default": "hy4-preview", "provider": "tencent-tokenhub"},
    )

    assert result["active_provider"] == "tencent-tokenhub"
    assert result["groups"] == [], (
        "A keyless API-key provider must not keep its baseline models; "
        f"got {result['groups']}"
    )
    assert result["default_model"] == "hy4-preview"
    assert result["default_model_available"] is False
    assert result["default_model_provider_has_key"] is False


def test_keyed_active_provider_keeps_baseline_and_reports_key(
    monkeypatch, isolated_home
):
    monkeypatch.setenv("TOKENHUB_API_KEY", "test-key")

    result = _build_with_model(
        monkeypatch,
        {"default": "hy4-preview", "provider": "tencent-tokenhub"},
    )

    assert [g["provider_id"] for g in result["groups"]] == ["tencent-tokenhub"]
    # The key exists but the live-only hy4-preview is not in the baseline:
    # this is the "provider no longer offers it" case, not a key problem.
    assert result["default_model_available"] is False
    assert result["default_model_provider_has_key"] is True


class TestDefaultModelProviderResolution:
    """config.yaml's model.provider outranks a slash prefix in model.default."""

    def test_active_provider_wins_for_openrouter_slash_model(self):
        assert config._resolve_default_model_provider(
            "deepseek/deepseek-v4-flash", "openrouter"
        ) == "openrouter"

    def test_webui_hint_is_resolved(self):
        assert config._resolve_default_model_provider(
            "@tencent-tokenhub:hy4-preview", "openrouter"
        ) == "tencent-tokenhub"

    def test_slash_prefix_inferred_without_configured_provider(self):
        assert config._resolve_default_model_provider(
            "deepseek/deepseek-v4-flash", None
        ) == "deepseek"

    def test_unknown_slash_prefix_stays_empty(self):
        assert config._resolve_default_model_provider("foo/bar", None) == ""

    def test_provider_key_state_is_none_for_local_providers(self):
        assert config._default_model_provider_has_key("llama3", "ollama") is None


class TestProviderRequiresApiKey:

    def test_catalog_api_key_provider(self):
        assert config._provider_requires_api_key("tencent-tokenhub") is True

    def test_catalog_keyless_provider(self):
        assert config._provider_requires_api_key("ollama") is False

    def test_unknown_provider_is_not_assumed_keyless(self):
        assert config._provider_requires_api_key("custom:gateway") is False
