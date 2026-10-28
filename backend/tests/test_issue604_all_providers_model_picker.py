"""Tests for #604 — model picker shows all configured providers.

Since issue #10 the provider/model catalog lives in
``backend/app/api/data/model_catalog.json`` (single source of truth);
these tests assert against the loaded catalog and the env-var scan it
drives, instead of regex-parsing config.py's retired hardcoded tables.
"""
from app.api import model_catalog


class TestProviderDetectionEnvVars:
    """Env-var detection must map every key to a valid catalog provider."""

    def test_xai_env_maps_to_xai_provider(self):
        """XAI_API_KEY must be declared by the x-ai catalog record."""
        env_vars = model_catalog.catalog_env_vars().get("x-ai", [])
        assert "XAI_API_KEY" in env_vars, (
            f"XAI_API_KEY must map to provider 'x-ai'. Found: {env_vars}"
        )

    def test_mistral_env_maps_to_mistralai_provider(self):
        """MISTRAL_API_KEY must be declared by the mistralai catalog record."""
        env_vars = model_catalog.catalog_env_vars().get("mistralai", [])
        assert "MISTRAL_API_KEY" in env_vars, (
            f"MISTRAL_API_KEY must map to provider 'mistralai'. Found: {env_vars}"
        )

    def test_all_catalog_env_vars_belong_to_known_providers(self):
        """Every slug in catalog_env_vars() must resolve to a catalog record."""
        for slug, env_vars in model_catalog.catalog_env_vars().items():
            assert model_catalog.get_provider_record(slug) is not None, (
                f"catalog_env_vars() slug {slug!r} has no provider record"
            )
            assert env_vars, f"catalog_env_vars() entry {slug!r} has no env vars"

    def test_env_scan_skips_empty_none_refresh_providers(self):
        """Providers with an empty baseline and refresh.mode=none (e.g.
        meta-llama) are skipped by the env scan — they have nothing to
        render.  api-mode providers with empty baselines (ark, ollama,
        huggingface, ollama-cloud) must NOT be skipped: their models
        come from the live query once a key is configured."""
        rec = model_catalog.get_provider_record("meta-llama")
        assert rec is not None, "meta-llama missing from catalog"
        assert not rec["models"], "meta-llama baseline unexpectedly non-empty"
        assert rec["refresh"]["mode"] != "api", (
            "meta-llama is refresh.mode=api and would not be skipped"
        )
        for slug in ("ark", "ollama", "lmstudio", "huggingface", "ollama-cloud"):
            rec = model_catalog.get_provider_record(slug)
            assert rec is not None, f"{slug} missing from catalog"
            assert rec["refresh"]["mode"] == "api", (
                f"{slug} must be refresh.mode=api so an empty baseline still renders"
            )


class TestConfigProvidersDetection:
    """Providers listed in config.yaml providers section should be detected."""

    def test_cfg_providers_detection_exists(self):
        """_build_available_models must scan cfg['providers']."""
        import inspect
        import app.api.config as config

        src = inspect.getsource(config._build_available_models_uncached)
        assert 'cfg.get("providers", {})' in src, (
            "Must read cfg['providers']"
        )
        assert "_cfg_providers" in src, "Must use _cfg_providers variable"


class TestCatalogCompleteness:
    """Verify the JSON catalog carries the expected providers."""

    def _providers(self) -> set:
        return set(model_catalog.catalog_providers())

    def test_has_anthropic(self):
        assert "anthropic" in self._providers()

    def test_has_openai(self):
        assert "openai" in self._providers()

    def test_has_google(self):
        assert "google" in self._providers()

    def test_has_deepseek(self):
        assert "deepseek" in self._providers()

    def test_has_xai(self):
        assert "x-ai" in self._providers()

    def test_has_mistralai(self):
        assert "mistralai" in self._providers()

    def test_has_openrouter(self):
        assert "openrouter" in self._providers()
