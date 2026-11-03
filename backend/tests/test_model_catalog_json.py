"""Offline tests for the JSON model catalog (issue #10, step A).

No network, no real providers: everything runs against the packaged
catalog file and temp-file fixtures with a mocked urlopen.
"""
import io
import json
import time
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

import app.api.model_catalog as mc


@pytest.fixture(autouse=True)
def _isolated_catalog_state(monkeypatch):
    """Snapshot/restore module-level catalog + last-good state around each test."""
    saved = {
        "_catalog": mc._catalog,
        "_catalog_mtime": mc._catalog_mtime,
        "_alias_map": mc._alias_map,
        "_env_vars_map": mc._env_vars_map,
        "CATALOG_PATH": mc.CATALOG_PATH,
    }
    mc.forget_last_good_models()
    yield
    mc._catalog = saved["_catalog"]
    mc._catalog_mtime = saved["_catalog_mtime"]
    mc._alias_map = saved["_alias_map"]
    mc._env_vars_map = saved["_env_vars_map"]
    mc.CATALOG_PATH = saved["CATALOG_PATH"]
    mc.forget_last_good_models()


def _write_catalog(tmp_path, payload):
    """Write *payload* to a temp catalog file and point the loader at it."""
    path = tmp_path / "model_catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    mc.CATALOG_PATH = path
    return path


def _minimal_catalog(**provider_overrides):
    provider = {
        "display_name": "Test",
        "auth": {"type": "api_key", "env_var": "TEST_API_KEY"},
        "endpoint": {"base_url": "https://api.test.example/v1", "models_path": "/models"},
        "refresh": {"mode": "api", "membership": "replace"},
        "models": [{"id": "@test:base-1", "label": "Base 1"}],
    }
    provider.update(provider_overrides)
    return {"schema_version": 1, "providers": {"test": provider}}


# ── 1. Packaged catalog loads and is well-formed ────────────────────────────


class TestPackagedCatalog:
    def test_loads_with_schema_version_1(self):
        catalog = mc.load_catalog(force=True)
        assert catalog["schema_version"] == 1
        assert isinstance(catalog["providers"], dict)

    def test_has_minimum_provider_coverage(self):
        # Guards against JSON typos silently dropping large parts of the
        # catalog (all pre-existing providers + ollama/lmstudio + ark).
        providers = mc.catalog_providers()
        assert len(providers) >= 20
        for slug in (
            "anthropic",
            "openai",
            "openai-codex",
            "google",
            "gemini",
            "deepseek",
            "nous",
            "zai",
            "kimi-coding",
            "minimax",
            "copilot",
            "opencode-zen",
            "opencode-go",
            "mistralai",
            "qwen",
            "x-ai",
            "openrouter",
            "ollama",
            "lmstudio",
            "ark",
            "tencent-tokenhub",
        ):
            assert slug in providers, f"missing provider {slug}"

    def test_every_provider_has_display_and_models_list(self):
        catalog = mc.load_catalog()
        for slug, rec in catalog["providers"].items():
            assert rec["display_name"], slug
            assert isinstance(rec["models"], list), slug
            for m in rec["models"]:
                assert m["id"], slug
                assert m["label"], slug

    def test_zai_endpoint_is_fixed(self):
        # Old hardcoded table pointed at api.z.ai/v1 (404).  The catalog
        # must carry the corrected intl endpoint.
        url = mc.provider_endpoint("zai")
        assert url == "https://api.z.ai/api/paas/v4/models"

    def test_ark_is_a_new_api_provider(self):
        rec = mc.get_provider_record("ark")
        assert rec is not None
        assert rec["display_name"] == "Volcano Ark"
        assert rec["refresh"]["mode"] == "api"
        assert rec["auth"]["env_var"] == "ARK_API_KEY"
        assert mc.provider_endpoint("ark") == "https://ark.cn-beijing.volces.com/api/v3/models"

    def test_tencent_tokenhub_is_an_api_provider(self):
        rec = mc.get_provider_record("tencent-tokenhub")
        assert rec is not None
        assert rec["display_name"] == "Tencent TokenHub"
        assert rec["refresh"]["mode"] == "api"
        assert rec["auth"]["env_var"] == "TOKENHUB_API_KEY"
        assert mc.provider_endpoint("tencent-tokenhub") == (
            "https://tokenhub.tencentmaas.com/v1/models"
        )
        # Hermes-compatible aliases must resolve to the canonical slug.
        assert mc.resolve_alias("tokenhub") == "tencent-tokenhub"
        assert mc.resolve_alias("tencent") == "tencent-tokenhub"

    def test_minimax_keeps_bare_ids(self):
        ids = [m["id"] for m in mc.provider_baseline_models("minimax")]
        assert "MiniMax-M2.7" in ids
        assert all(not i.startswith("@") for i in ids)

    def test_anthropic_keeps_prefixed_ids(self):
        ids = [m["id"] for m in mc.provider_baseline_models("anthropic")]
        assert "@anthropic:claude-sonnet-4.6" in ids

    def test_openrouter_baseline_migrated_from_fallback_models(self):
        models = mc.provider_baseline_models("openrouter")
        assert len(models) >= 15
        ids = {m["id"] for m in models}
        assert "anthropic/claude-sonnet-4.6" in ids
        assert "openai/gpt-5.4-mini" in ids

    def test_oauth_providers_have_no_endpoint(self):
        for slug in ("copilot", "nous", "openai-codex"):
            assert mc.provider_endpoint(slug) is None
            assert mc.get_provider_record(slug)["refresh"]["mode"] == "none"

    def test_local_services_trust_local_and_keyless(self):
        for slug in ("ollama", "lmstudio"):
            rec = mc.get_provider_record(slug)
            assert rec["auth"]["type"] == "none"
            assert rec["endpoint"]["trust_local"] is True
            assert rec["models"] == []  # filled by live query once configured
            assert mc.should_query_live(slug, has_key=False) is True

    def test_anthropic_uses_x_api_key_header(self):
        assert mc.provider_auth_header("anthropic") == "x-api-key"
        assert mc.provider_auth_header("deepseek") == "Bearer"

    def test_xiaomi_declares_base_url_env_override(self):
        # Regional gateways (token-plan-cn.xiaomimimo.com) issue keys that
        # are rejected by the packaged default endpoint — the catalog must
        # advertise the SDK's XIAOMI_BASE_URL override var.
        rec = mc.get_provider_record("xiaomi")
        assert rec["endpoint"]["base_url_env"] == "XIAOMI_BASE_URL"
        assert rec["endpoint"]["base_url"] == "https://api.xiaomimimo.com/v1"


class TestBaseUrlEnvOverride:
    """endpoint.base_url_env: env/.env override beats the packaged default."""

    def _write_override_catalog(self, tmp_path):
        payload = _minimal_catalog()
        payload["providers"]["test"]["endpoint"]["base_url_env"] = "TEST_BASE_URL"
        return _write_catalog(tmp_path, payload)

    def test_default_when_env_unset(self, tmp_path):
        self._write_override_catalog(tmp_path)
        mc.load_catalog(force=True)
        assert mc.provider_base_url("test") == "https://api.test.example/v1"
        assert mc.provider_endpoint("test") == "https://api.test.example/v1/models"

    def test_env_override_wins(self, tmp_path, monkeypatch):
        self._write_override_catalog(tmp_path)
        monkeypatch.setenv("TEST_BASE_URL", "https://gw.example.cn/v1")
        mc.load_catalog(force=True)
        assert mc.provider_base_url("test") == "https://gw.example.cn/v1"
        # models_path still joins onto the override.
        assert mc.provider_endpoint("test") == "https://gw.example.cn/v1/models"

    def test_env_file_override_wins(self, tmp_path, monkeypatch):
        # The backend process environ often lacks hermes .env values —
        # the override must also resolve from the .env file.
        self._write_override_catalog(tmp_path)
        monkeypatch.delenv("TEST_BASE_URL", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("TEST_BASE_URL=https://from-env-file.example/v1\n")
        import app.api.providers as providers

        monkeypatch.setattr(providers, "_get_hermes_home", lambda: tmp_path)
        mc.load_catalog(force=True)
        assert mc.provider_base_url("test") == "https://from-env-file.example/v1"

    def test_trailing_slash_stripped(self, tmp_path, monkeypatch):
        self._write_override_catalog(tmp_path)
        monkeypatch.setenv("TEST_BASE_URL", "https://gw.example.cn/v1/")
        mc.load_catalog(force=True)
        assert mc.provider_base_url("test") == "https://gw.example.cn/v1"

    def test_provider_without_override_field_unaffected(self, tmp_path, monkeypatch):
        # Packaged-default providers ignore any same-named env var.
        _write_catalog(tmp_path, _minimal_catalog())
        monkeypatch.setenv("TEST_BASE_URL", "https://ignored.example/v1")
        mc.load_catalog(force=True)
        assert mc.provider_base_url("test") == "https://api.test.example/v1"


# ── 2. Alias resolution ─────────────────────────────────────────────────────


class TestAliasResolution:
    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("z.ai", "zai"),
            ("zhipu", "zai"),
            ("glm", "zai"),
            ("moonshot", "kimi-coding"),
            ("kimi", "kimi-coding"),
            ("claude", "anthropic"),
            ("deep-seek", "deepseek"),
            ("dashscope", "qwen"),
            ("alibaba", "qwen"),
            ("grok", "x-ai"),
            ("x.ai", "x-ai"),
            ("xai", "x-ai"),
            ("github-copilot", "copilot"),
            ("google", "gemini"),
            ("volcano", "ark"),
            ("doubao", "ark"),
            ("aws", "bedrock"),  # alias target with no provider record
        ],
    )
    def test_known_aliases(self, alias, expected):
        assert mc.resolve_alias(alias) == expected

    def test_unknown_passthrough(self):
        assert mc.resolve_alias("some-unknown-provider") == "some-unknown-provider"

    def test_case_insensitive_and_trimmed(self):
        assert mc.resolve_alias("  Z.AI ") == "zai"

    def test_empty_returns_empty(self):
        assert mc.resolve_alias("") == ""

    def test_alias_finds_provider_record(self):
        # dashscope is not a provider key but resolves to qwen's record.
        assert mc.get_provider_record("dashscope") is not None
        assert mc.provider_display("dashscope") == "Qwen"

    def test_bedrock_alias_has_no_record(self):
        assert mc.get_provider_record("aws") is None
        assert mc.provider_baseline_models("aws") == []


# ── 3. Merge rules ──────────────────────────────────────────────────────────


class TestMergeRules:
    BASELINE = [
        {"id": "@test:base-1", "label": "Base 1"},
        {"id": "@test:base-2", "label": "Curated Label"},
    ]

    def test_replace_api_wins_and_baseline_donates_labels(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        live = [
            {"id": "base-2", "label": "Wrong Upstream Label"},
            {"id": "brand-new", "label": "Brand New"},
        ]
        out = mc.merge_models("test", self.BASELINE, live)
        # The live API list is the single source of truth for membership
        # and order: base-1 (omitted by the API) is gone, and the API's
        # order is preserved.
        assert [m["id"] for m in out] == ["base-2", "brand-new"]
        # The JSON baseline still donates its curated label for the
        # surviving id (matched across the @test: prefix).
        assert out[0]["label"] == "Curated Label"

    def test_replace_drops_models_the_api_omits(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        # Both stale live-only discoveries AND baseline entries the API
        # no longer lists vanish — membership is recomputed each cycle.
        out = mc.merge_models("test", self.BASELINE, [{"id": "base-1", "label": "x"}])
        assert [m["id"] for m in out] == ["base-1"]

    def test_curated_is_intersection_only(self, tmp_path):
        _write_catalog(
            tmp_path,
            _minimal_catalog(refresh={"mode": "api", "membership": "curated"}),
        )
        mc.load_catalog(force=True)
        live = [
            {"id": "base-1", "label": "a"},
            {"id": "not-curated", "label": "b"},  # must NOT be injected
        ]
        out = mc.merge_models("test", self.BASELINE, live)
        assert [m["id"] for m in out] == ["@test:base-1"]

    def test_et_override_donated_on_merge(self, tmp_path):
        baseline = [
            {"id": "@test:think-1", "label": "Think", "supports_extended_thinking": True},
            {"id": "@test:plain-1", "label": "Plain"},
        ]
        _write_catalog(tmp_path, _minimal_catalog(models=baseline))
        mc.load_catalog(force=True)
        out = mc.merge_models(
            "test", baseline, [{"id": "think-1", "label": "think-1"}, {"id": "extra", "label": "Extra"}]
        )
        by_id = {m["id"]: m for m in out}
        assert by_id["think-1"]["supports_extended_thinking"] is True
        assert "supports_extended_thinking" not in by_id["extra"]
        # plain-1 is not in the live list, so it must not survive.
        assert "plain-1" not in by_id and "@test:plain-1" not in by_id


# ── 4. resolve_provider_models orchestration ────────────────────────────────


class TestResolveProviderModels:
    def test_cfg_override_wins_without_query(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        with patch.object(mc, "query_live_models") as q:
            out = mc.resolve_provider_models(
                "test", cfg_models_override={"m1": "something", "m2": "other"}
            )
        q.assert_not_called()
        assert out == [
            {"id": "m1", "label": "m1"},
            {"id": "m2", "label": "m2"},
        ]

    def test_cfg_override_list_form(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        out = mc.resolve_provider_models("test", cfg_models_override=["m1", "m2"])
        assert out == [{"id": "m1", "label": "m1"}, {"id": "m2", "label": "m2"}]

    def test_success_api_list_wins_and_remembers(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        with patch.object(
            mc, "query_live_models", return_value=[{"id": "live-9", "label": "Live 9"}]
        ):
            out = mc.resolve_provider_models("test", api_key="k")
        # Live success: the API list replaces the baseline entirely
        # (base-1 was not returned by the API, so it must not appear).
        assert [m["id"] for m in out] == ["live-9"]
        assert mc.last_good_models("test") == out

    def test_empty_live_list_keeps_fallback(self, tmp_path):
        """A 200 response with zero models is inconclusive: keep the
        prior/last-good/baseline list instead of blanking the group."""
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        with patch.object(mc, "query_live_models", return_value=[]):
            out = mc.resolve_provider_models("test", api_key="k")
        assert [m["id"] for m in out] == ["@test:base-1"]

    def test_failure_keeps_prior_then_baseline(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        prior = [{"id": "@test:prior-1", "label": "Prior"}]
        with patch.object(mc, "query_live_models", side_effect=RuntimeError("down")):
            out = mc.resolve_provider_models("test", api_key="k", prior=prior)
        assert out == prior

        # No prior: falls back to the JSON baseline, never [].
        mc.forget_last_good_models()
        with patch.object(mc, "query_live_models", side_effect=RuntimeError("down")):
            out = mc.resolve_provider_models("test", api_key="k")
        assert [m["id"] for m in out] == ["@test:base-1"]

    def test_failure_uses_last_good_when_no_prior(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        remembered = [{"id": "@test:old-1", "label": "Old"}]
        mc.remember_good_models("test", remembered)
        with patch.object(mc, "query_live_models", side_effect=RuntimeError("down")):
            out = mc.resolve_provider_models("test", api_key="k")
        assert out == remembered

    def test_auth_failure_raises_and_forgets_last_good(self, tmp_path):
        """HTTP 401 is a definite rejection: raise ProviderAuthError (the
        caller hides the group) and drop the stale last-success entry so a
        revoked key's models cannot resurface later."""
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        mc.remember_good_models("test", [{"id": "@test:stale", "label": "Stale"}])
        prior = [{"id": "@test:prior-1", "label": "Prior"}]
        err = urllib.error.HTTPError(
            "https://example.test/v1/models", 401, "Unauthorized", None, None
        )
        with patch.object(mc, "query_live_models", side_effect=err):
            with pytest.raises(mc.ProviderAuthError) as excinfo:
                mc.resolve_provider_models("test", api_key="k", prior=prior)
        assert excinfo.value.slug == "test"
        assert excinfo.value.status == 401
        assert mc.last_good_models("test") is None

    def test_network_failure_keeps_fallback(self, tmp_path):
        """URLError stays on the prior/last-good/baseline fallback path —
        only 401/403 hides the provider."""
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        remembered = [{"id": "@test:old-1", "label": "Old"}]
        mc.remember_good_models("test", remembered)
        with patch.object(
            mc, "query_live_models", side_effect=urllib.error.URLError("down")
        ):
            out = mc.resolve_provider_models("test", api_key="k")
        assert out == remembered
        assert mc.last_good_models("test") == remembered

    def test_missing_key_skips_query(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        with patch.object(mc, "query_live_models") as q:
            out = mc.resolve_provider_models("test", api_key="")
        q.assert_not_called()
        assert [m["id"] for m in out] == ["@test:base-1"]

    def test_mode_none_returns_baseline(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog(refresh={"mode": "none"}))
        mc.load_catalog(force=True)
        with patch.object(mc, "query_live_models") as q:
            out = mc.resolve_provider_models("test", api_key="k")
        q.assert_not_called()
        assert [m["id"] for m in out] == ["@test:base-1"]

    def test_unknown_provider_keeps_prior(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog())
        mc.load_catalog(force=True)
        prior = [{"id": "x", "label": "X"}]
        assert mc.resolve_provider_models("nope", prior=prior) == prior
        assert mc.resolve_provider_models("nope") == []

    def test_mode_none_empty_baseline_keeps_prior(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog(refresh={"mode": "none"}, models=[]))
        mc.load_catalog(force=True)
        prior = [{"id": "@test:was-fetched", "label": "Was Fetched"}]
        out = mc.resolve_provider_models("test", prior=prior)
        assert out == prior


# ── 5. Loader validation & mtime reload ─────────────────────────────────────


class TestLoaderValidation:
    def test_bad_json_yields_empty_catalog(self, tmp_path):
        path = tmp_path / "model_catalog.json"
        path.write_text("{not json", encoding="utf-8")
        mc.CATALOG_PATH = path
        catalog = mc.load_catalog(force=True)
        assert catalog["providers"] == {}

    def test_wrong_schema_version_yields_empty_catalog(self, tmp_path):
        _write_catalog(tmp_path, {"schema_version": 99, "providers": {"a": {}}})
        catalog = mc.load_catalog(force=True)
        assert catalog["providers"] == {}

    def test_missing_display_name_drops_provider(self, tmp_path):
        _write_catalog(
            tmp_path,
            {"schema_version": 1, "providers": {"bad": {"models": [], "refresh": {"mode": "none"}}}},
        )
        catalog = mc.load_catalog(force=True)
        assert "bad" not in catalog["providers"]

    def test_bad_refresh_mode_drops_provider(self, tmp_path):
        _write_catalog(
            tmp_path,
            _minimal_catalog(refresh={"mode": "sometimes"}),
        )
        catalog = mc.load_catalog(force=True)
        assert "test" not in catalog["providers"]

    def test_bad_membership_drops_provider(self, tmp_path):
        _write_catalog(
            tmp_path,
            _minimal_catalog(refresh={"mode": "api", "membership": "all"}),
        )
        catalog = mc.load_catalog(force=True)
        assert "test" not in catalog["providers"]

    def test_bad_model_entry_drops_provider(self, tmp_path):
        _write_catalog(tmp_path, _minimal_catalog(models=[{"label": "no id"}]))
        catalog = mc.load_catalog(force=True)
        assert "test" not in catalog["providers"]

    def test_missing_file_yields_empty_catalog(self, tmp_path):
        mc.CATALOG_PATH = tmp_path / "absent.json"
        catalog = mc.load_catalog(force=True)
        assert catalog["providers"] == {}

    def test_mtime_change_triggers_reload(self, tmp_path):
        path = _write_catalog(tmp_path, _minimal_catalog())
        first = mc.load_catalog(force=True)
        assert "test" in first["providers"]

        # Rewrite with an extra provider and bump mtime.
        payload = _minimal_catalog()
        payload["providers"]["second"] = {
            "display_name": "Second",
            "auth": {"type": "none"},
            "refresh": {"mode": "none"},
            "models": [],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        import os

        os.utime(path, (time.time() + 10, time.time() + 10))
        second = mc.load_catalog()  # no force — mtime must trigger reload
        assert "second" in second["providers"]

    def test_env_vars_map(self):
        envs = mc.catalog_env_vars()
        assert "ARK_API_KEY" in envs.get("ark", [])
        assert set(envs.get("minimax", [])) == {"MINIMAX_API_KEY", "MINIMAX_CN_API_KEY"}
        assert "GLM_API_KEY" in envs.get("zai", [])


# ── 6. Live query plumbing (mocked urlopen) ─────────────────────────────────


def _fake_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(body)


class TestLiveQueryPlumbing:
    def test_query_openai_compat_parses_data_list(self):
        with patch(
            "urllib.request.urlopen",
            return_value=_fake_response({"data": [{"id": "m1", "name": "Model One"}]}),
        ):
            out = mc.query_openai_compat("https://api.test.example/v1/models", api_key="k")
        assert out == [{"id": "m1", "label": "Model One"}]

    def test_query_openai_compat_parses_models_list(self):
        with patch(
            "urllib.request.urlopen",
            return_value=_fake_response({"models": [{"name": "llama3", "model": "llama3"}]}),
        ):
            out = mc.query_openai_compat("https://api.test.example/v1/models")
        assert out == [{"id": "llama3", "label": "llama3"}]

    def test_bearer_and_x_api_key_headers(self):
        captured = {}

        def _capture(req, timeout=None):
            captured["headers"] = dict(req.header_items())
            return _fake_response({"data": []})

        with patch("urllib.request.urlopen", side_effect=_capture):
            mc.query_openai_compat("https://api.test.example/v1/models", api_key="sk-x")
        assert captured["headers"].get("Authorization") == "Bearer sk-x"

        with patch("urllib.request.urlopen", side_effect=_capture):
            mc.query_openai_compat(
                "https://api.test.example/v1/models", api_key="sk-x", auth_header="x-api-key"
            )
        assert captured["headers"].get("X-api-key") == "sk-x" or captured["headers"].get("x-api-key") == "sk-x"

    def test_ssrf_blocks_private_host(self):
        import socket as _socket

        class _Addr(tuple):
            pass

        fake_result = [(2, 1, 6, "", ("10.0.0.5", 0))]
        with patch.object(_socket, "getaddrinfo", return_value=fake_result):
            with pytest.raises(ValueError, match="SSRF"):
                mc.query_openai_compat("https://evil.example/v1/models")

    def test_ssrf_allows_trusted_local_host(self):
        import socket as _socket

        fake_result = [(2, 1, 6, "", ("127.0.0.1", 0))]
        with patch.object(_socket, "getaddrinfo", return_value=fake_result):
            with patch(
                "urllib.request.urlopen",
                return_value=_fake_response({"data": [{"id": "m1", "name": "M"}]}),
            ):
                out = mc.query_openai_compat(
                    "http://localhost:11434/v1/models",
                    trust_local_hosts={"localhost"},
                )
        assert out == [{"id": "m1", "label": "M"}]

    def test_query_live_models_builds_catalog_url(self):
        captured = {}

        def _capture(req, timeout=None):
            captured["url"] = req.full_url
            return _fake_response({"data": [{"id": "glm-6", "name": "GLM 6"}]})

        # Use the real packaged catalog so the zai endpoint is exercised
        # end-to-end (base_url + models_path join).
        packaged = Path(__file__).resolve().parent.parent / "app" / "api" / "data" / "model_catalog.json"
        mc.CATALOG_PATH = packaged
        mc.load_catalog(force=True)
        with patch("urllib.request.urlopen", side_effect=_capture):
            out = mc.query_live_models("zai", api_key="k")
        assert captured["url"] == "https://api.z.ai/api/paas/v4/models"
        assert out == [{"id": "glm-6", "label": "GLM 6"}]

    def test_should_query_live_gates_on_key(self):
        assert mc.should_query_live("deepseek", has_key=True) is True
        assert mc.should_query_live("deepseek", has_key=False) is False
        assert mc.should_query_live("copilot", has_key=True) is False  # mode=none
        assert mc.should_query_live("nope", has_key=True) is False  # unknown


# ── 7. Gateway variants (Pattern B: dual-gateway vendors, e.g. xiaomi) ─────


class TestGatewayVariantCatalog:
    """Packaged catalog: xiaomi api and xiaomi token-plan coexist.

    Pattern B — each gateway is a full catalog entry (own env var, own
    Settings card, usable at the same time) with an ``sdk_provider``
    routing alias so the WebUI can drive both through the SDK's single
    ``xiaomi`` provider.
    """

    def test_xiaomi_token_plan_packaged_entry(self):
        rec = mc.get_provider_record("xiaomi-token-plan")
        assert rec is not None
        assert rec["sdk_provider"] == "xiaomi"
        assert rec["auth"]["env_var"] == "XIAOMI_TOKEN_PLAN_API_KEY"
        assert rec["endpoint"]["base_url"] == "https://token-plan-cn.xiaomimimo.com/v1"
        # Fixed token-plan endpoint — no base_url_env override var.
        assert not rec["endpoint"].get("base_url_env")

    def test_both_gateways_have_distinct_env_vars(self):
        envs = mc.catalog_env_vars()
        assert envs.get("xiaomi") == ["XIAOMI_API_KEY"]
        assert envs.get("xiaomi-token-plan") == ["XIAOMI_TOKEN_PLAN_API_KEY"]

    def test_plain_xiaomi_is_not_a_variant(self):
        assert mc.provider_sdk_provider("xiaomi") is None
        assert mc.provider_sdk_provider("anthropic") is None
        assert mc.provider_sdk_provider("some-unknown") is None

    def test_find_gateway_variant_reverse_lookup(self):
        # config.yaml persists the variant as SDK slug + base_url; the
        # reverse lookup recovers the variant slug.
        assert (
            mc.find_gateway_variant("xiaomi", "https://token-plan-cn.xiaomimimo.com/v1")
            == "xiaomi-token-plan"
        )
        # Trailing slash tolerated.
        assert (
            mc.find_gateway_variant("xiaomi", "https://token-plan-cn.xiaomimimo.com/v1/")
            == "xiaomi-token-plan"
        )
        # The plain api gateway endpoint matches no variant.
        assert mc.find_gateway_variant("xiaomi", "https://api.xiaomimimo.com/v1") is None
        # Another provider's variant endpoint is never returned.
        assert (
            mc.find_gateway_variant("minimax", "https://token-plan-cn.xiaomimimo.com/v1")
            is None
        )
        assert mc.find_gateway_variant("", "https://token-plan-cn.xiaomimimo.com/v1") is None
        assert mc.find_gateway_variant("xiaomi", "") is None

    def test_provider_api_key_reads_variant_env(self, tmp_path, monkeypatch):
        import app.api.providers as providers

        monkeypatch.setattr(providers, "_get_hermes_home", lambda: tmp_path)
        monkeypatch.delenv("XIAOMI_TOKEN_PLAN_API_KEY", raising=False)
        (tmp_path / ".env").write_text(
            "XIAOMI_TOKEN_PLAN_API_KEY=tp-test-key\n", encoding="utf-8"
        )
        mc.load_catalog(force=True)
        assert mc.provider_api_key("xiaomi-token-plan") == "tp-test-key"
        # Plain xiaomi reads its own env var — no bleed between gateways.
        assert mc.provider_api_key("xiaomi") == ""


class TestGatewayVariantValidation:
    """sdk_provider field validation in the loader."""

    def _write_variant(self, tmp_path, sdk_provider):
        payload = _minimal_catalog()
        payload["providers"]["variant"] = {
            "display_name": "Variant",
            "auth": {"type": "api_key", "env_var": "VARIANT_API_KEY"},
            "endpoint": {
                "base_url": "https://variant.test.example/v1",
                "models_path": "/models",
            },
            "sdk_provider": sdk_provider,
            "refresh": {"mode": "api", "membership": "replace"},
            "models": [{"id": "@variant:base-1", "label": "Base 1"}],
        }
        return _write_catalog(tmp_path, payload)

    def test_valid_sdk_provider_kept(self, tmp_path):
        self._write_variant(tmp_path, "base-provider")
        mc.load_catalog(force=True)
        assert mc.provider_sdk_provider("variant") == "base-provider"

    def test_non_string_sdk_provider_drops_provider(self, tmp_path):
        self._write_variant(tmp_path, 123)
        catalog = mc.load_catalog(force=True)
        assert "variant" not in catalog["providers"]

    def test_blank_sdk_provider_drops_provider(self, tmp_path):
        self._write_variant(tmp_path, "   ")
        catalog = mc.load_catalog(force=True)
        assert "variant" not in catalog["providers"]

    def test_self_reference_ignored(self, tmp_path):
        self._write_variant(tmp_path, "variant")
        mc.load_catalog(force=True)
        # Entry kept but the alias is cleared — behaves like a regular entry.
        assert mc.get_provider_record("variant") is not None
        assert mc.provider_sdk_provider("variant") is None
