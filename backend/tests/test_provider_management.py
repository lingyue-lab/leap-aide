"""Tests for /api/providers CRUD endpoints (provider key management).

Closes #586 — allow provider key update from the WebUI.
Part of #604 — multi-provider model picker support.
"""

import contextlib
import json
import os
import sys
import types
import urllib.error
import urllib.request

import pytest

import app.api.config as config
import app.api.profiles as profiles
from tests._pytest_port import BASE


@pytest.fixture(autouse=True)
def _restore_env_snapshot():
    """Snapshot/restore the full process env around every test here.

    ``_write_env_file`` publishes saved keys into ``os.environ`` as a side
    effect, but ``monkeypatch.delenv(..., raising=False)`` is a no-op when
    the var is absent — so it never records a var the save path creates
    afterwards, and the key leaks into every later test in the session
    (e.g. a leaked XIAOMI_TOKEN_PLAN_API_KEY hijacks another test's
    captured Authorization header).  This fixture is the last to tear
    down (autouse setups run before explicitly requested fixtures, so
    monkeypatch undoes first), guaranteeing the start state returns.
    """
    saved = dict(os.environ)
    yield
    for key in set(os.environ) - set(saved):
        del os.environ[key]
    for key, value in saved.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


# ── HTTP helpers ──────────────────────────────────────────────────────────


def _get(path):
    """GET helper — returns parsed JSON."""
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())


def _post(path, body=None):
    """POST helper — returns (parsed_json, status_code)."""
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body_text), e.code
        except Exception:
            return {"error": body_text}, e.code


def _install_fake_hermes_cli(monkeypatch):
    """Stub hermes_cli modules so tests are deterministic and offline."""
    fake_pkg = types.ModuleType("hermes_cli")
    fake_pkg.__path__ = []

    fake_models = types.ModuleType("hermes_cli.models")
    fake_models.list_available_providers = lambda: []

    fake_auth = types.ModuleType("hermes_cli.auth")
    fake_auth.get_auth_status = lambda _pid: {}

    monkeypatch.setitem(sys.modules, "hermes_cli", fake_pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.models", fake_models)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", fake_auth)
    monkeypatch.delitem(sys.modules, "agent.credential_pool", raising=False)
    monkeypatch.delitem(sys.modules, "agent", raising=False)

    # Flush the 60-second TTL model cache so no prior test's result bleeds in.
    try:
        from app.api.config import invalidate_models_cache
        invalidate_models_cache()
    except Exception:
        pass


# ── Unit tests (api/providers.py functions directly) ──────────────────────


class TestGetProviders:
    """Unit tests for get_providers() function."""

    def test_returns_list_of_known_providers(self, monkeypatch, tmp_path):
        """GET /api/providers should return a list of all known providers."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0

        from app.api.providers import get_providers
        try:
            result = get_providers()
            assert "providers" in result
            assert "active_provider" in result
            assert isinstance(result["providers"], list)
            # Should include at least the built-in providers
            provider_ids = {p["id"] for p in result["providers"]}
            assert "anthropic" in provider_ids
            assert "openai" in provider_ids
            assert "openrouter" in provider_ids
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime

    def test_provider_entries_have_required_fields(self, monkeypatch, tmp_path):
        """Each provider entry should have id, display_name, has_key, configurable."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0

        from app.api.providers import get_providers
        try:
            result = get_providers()
            for p in result["providers"]:
                assert "id" in p, f"Missing 'id' in provider entry"
                assert "display_name" in p, f"Missing 'display_name' for {p['id']}"
                assert "has_key" in p, f"Missing 'has_key' for {p['id']}"
                assert "configurable" in p, f"Missing 'configurable' for {p['id']}"
                assert "key_source" in p, f"Missing 'key_source' for {p['id']}"
                assert isinstance(p["has_key"], bool)
                assert isinstance(p["configurable"], bool)
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime

    def test_oauth_providers_not_configurable(self, monkeypatch, tmp_path):
        """OAuth providers (copilot, nous, openai-codex) should not be configurable."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0

        from app.api.providers import get_providers
        try:
            result = get_providers()
            for p in result["providers"]:
                if p["id"] in ("copilot", "nous", "openai-codex"):
                    assert p["configurable"] is False, f"{p['id']} should not be configurable"
                # ollama-cloud is now configurable (uses OLLAMA_API_KEY)
                if p["id"] == "ollama-cloud":
                    assert p["configurable"] is True, "ollama-cloud should be configurable"
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime


class TestSetProviderKey:
    """Unit tests for set_provider_key() function."""

    def test_set_key_writes_to_env_file(self, monkeypatch, tmp_path):
        """Setting a key should write the env var to ~/.hermes/.env."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
        # Also pin HERMES_HOME so code that reads it directly gets tmp_path,
        # not the conftest session TEST_STATE_DIR that bleeds into the main process.
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0

        from app.api.providers import set_provider_key
        try:
            result = set_provider_key("anthropic", "sk-ant-test-key-12345678")
            assert result["ok"] is True
            assert result["provider"] == "anthropic"
            assert result["action"] == "updated"

            # Verify .env file was written
            env_path = tmp_path / ".env"
            assert env_path.exists(), f".env not written to {env_path}; HERMES_HOME={__import__('os').environ.get('HERMES_HOME')!r}"
            content = env_path.read_text()
            assert "ANTHROPIC_API_KEY=sk-ant-test-key-12345678" in content
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime

    def test_remove_key_deletes_from_env_file(self, monkeypatch, tmp_path):
        """Removing a key should delete the env var from .env."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0

        from app.api.providers import set_provider_key
        try:
            # First set a key
            set_provider_key("anthropic", "sk-ant-test-key-12345678")
            # Then remove it
            result = set_provider_key("anthropic", None)
            assert result["ok"] is True
            assert result["action"] == "removed"

            # Verify .env file no longer has the key
            env_path = tmp_path / ".env"
            content = env_path.read_text() if env_path.exists() else ""
            assert "ANTHROPIC_API_KEY" not in content
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime

    def test_oauth_provider_rejected(self, monkeypatch, tmp_path):
        """Setting a key for an OAuth provider should fail."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0

        from app.api.providers import set_provider_key
        try:
            result = set_provider_key("copilot", "some-key")
            assert result["ok"] is False
            assert "OAuth" in result["error"]
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime

    def test_short_key_rejected(self, monkeypatch, tmp_path):
        """API keys shorter than 8 chars should be rejected."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0

        from app.api.providers import set_provider_key
        try:
            result = set_provider_key("anthropic", "short")
            assert result["ok"] is False
            assert "too short" in result["error"]
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime

    def test_empty_provider_id_rejected(self, monkeypatch, tmp_path):
        """Empty provider ID should be rejected."""
        from app.api.providers import set_provider_key
        result = set_provider_key("", "some-key")
        assert result["ok"] is False
        assert "required" in result["error"]

    def test_newline_in_key_rejected(self, monkeypatch, tmp_path):
        """API keys with newlines should be rejected."""
        from app.api.providers import set_provider_key
        result = set_provider_key("anthropic", "sk-ant-key\nINJECTED=evil")
        assert result["ok"] is False
        assert "newline" in result["error"]


class TestRemoveProviderKey:
    """Unit tests for remove_provider_key() wrapper."""

    def test_remove_provider_key_calls_set_with_none(self, monkeypatch, tmp_path):
        """remove_provider_key should delegate to set_provider_key(id, None)."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0

        from app.api.providers import remove_provider_key
        try:
            result = remove_provider_key("anthropic")
            assert result["ok"] is True
            assert result["action"] == "removed"
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime


class TestProviderEnvVars:
    """Unit tests for _provider_env_vars() — catalog-driven env var lookup.

    The env var names are read from model_catalog.json (auth.env_var /
    auth.env_vars) so catalog-only edits need no Python change.  These tests
    pin the lookup contract: card-id folding, multi-var providers, the copilot
    token-flow exception, and the key-detection paths that consume it.
    """

    def test_env_vars_come_from_catalog(self):
        """Multi-var catalog providers must expose every declared name."""
        from app.api.providers import _provider_env_vars
        assert _provider_env_vars("minimax") == [
            "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY",
        ]
        assert _provider_env_vars("xiaomi") == ["XIAOMI_API_KEY"]
        assert _provider_env_vars("deepseek") == ["DEEPSEEK_API_KEY"]

    def test_card_id_and_slug_alias_folding(self):
        """Settings card ids (xai / alibaba / gemini) fold to the catalog
        slug's env vars, same result as addressing the slug directly."""
        from app.api.providers import _provider_env_vars
        assert _provider_env_vars("xai") == ["XAI_API_KEY"]
        assert _provider_env_vars("x-ai") == ["XAI_API_KEY"]
        assert _provider_env_vars("alibaba") == ["DASHSCOPE_API_KEY"]
        assert _provider_env_vars("qwen") == ["DASHSCOPE_API_KEY"]
        assert _provider_env_vars("gemini") == ["GEMINI_API_KEY"]

    def test_copilot_token_flow_exception(self):
        """copilot is OAuth in the catalog but a manually set
        COPILOT_GITHUB_TOKEN remains a valid detection target."""
        from app.api.providers import _provider_env_vars
        assert _provider_env_vars("copilot") == ["COPILOT_GITHUB_TOKEN"]

    def test_unknown_and_empty_ids_return_empty(self):
        """Custom providers and empty ids resolve to no env vars."""
        from app.api.providers import _provider_env_vars
        assert _provider_env_vars("") == []
        assert _provider_env_vars("acme") == []
        assert _provider_env_vars("meta-llama") == []  # auth.type=none

    def test_has_key_detects_secondary_env_var(self, monkeypatch, tmp_path):
        """Regression: MINIMAX_CN_API_KEY alone in .env must count as a
        configured key.  The old hardcoded _PROVIDER_ENV_VAR map only knew
        MINIMAX_API_KEY, so CN-only setups showed as unconfigured here while
        config.py's catalog scan already detected them."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        for var in ("MINIMAX_API_KEY", "MINIMAX_CN_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        (tmp_path / ".env").write_text("MINIMAX_CN_API_KEY=cn-test-key-12345678\n")

        from app.api.providers import _provider_has_key
        assert _provider_has_key("minimax") is True

    def test_has_key_false_when_nothing_configured(self, monkeypatch, tmp_path):
        """No .env entry and no process env → no key, despite a known mapping."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        for var in ("MINIMAX_API_KEY", "MINIMAX_CN_API_KEY"):
            monkeypatch.delenv(var, raising=False)

        old_cfg = dict(config.cfg)
        config.cfg.clear()
        config.cfg["model"] = {}
        from app.api.providers import _provider_has_key
        try:
            assert _provider_has_key("minimax") is False
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)

    def test_set_key_writes_first_declared_var(self, monkeypatch, tmp_path):
        """set_provider_key writes to the first catalog-declared name; the
        remaining names are alternate spellings scanned for detection only."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0

        from app.api.providers import set_provider_key
        try:
            result = set_provider_key("minimax", "mm-test-key-12345678")
            assert result["ok"] is True
            content = (tmp_path / ".env").read_text()
            assert "MINIMAX_API_KEY=mm-test-key-12345678" in content
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime


class TestXiaomiGatewayVariantKeys:
    """Pattern B: xiaomi api and xiaomi token-plan keep independent keys.

    Each gateway is its own catalog entry with its own env var, so a key set
    on one card must never light up (or overwrite) the other.
    """

    def test_env_vars_are_distinct(self):
        from app.api.providers import _provider_env_vars
        assert _provider_env_vars("xiaomi") == ["XIAOMI_API_KEY"]
        assert _provider_env_vars("xiaomi-token-plan") == ["XIAOMI_TOKEN_PLAN_API_KEY"]

    def test_set_variant_key_writes_its_own_env_var(self, monkeypatch, tmp_path):
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        for var in ("XIAOMI_API_KEY", "XIAOMI_TOKEN_PLAN_API_KEY"):
            monkeypatch.delenv(var, raising=False)

        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0

        from app.api.providers import set_provider_key
        try:
            result = set_provider_key("xiaomi-token-plan", "tp-test-key-12345678")
            assert result["ok"] is True
            content = (tmp_path / ".env").read_text()
            assert "XIAOMI_TOKEN_PLAN_API_KEY=tp-test-key-12345678" in content
            # Must not touch the plain api gateway's var.
            assert "XIAOMI_API_KEY=" not in content
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime

    def test_key_isolation_between_gateways(self, monkeypatch, tmp_path):
        """One key set → only that gateway reports configured."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        for var in ("XIAOMI_API_KEY", "XIAOMI_TOKEN_PLAN_API_KEY"):
            monkeypatch.delenv(var, raising=False)

        old_cfg = dict(config.cfg)
        config.cfg.clear()
        config.cfg["model"] = {}
        from app.api.providers import _provider_has_key
        try:
            # Only the token-plan key present.
            (tmp_path / ".env").write_text("XIAOMI_TOKEN_PLAN_API_KEY=tp-only\n")
            assert _provider_has_key("xiaomi") is False
            assert _provider_has_key("xiaomi-token-plan") is True

            # Only the plain api key present.
            (tmp_path / ".env").write_text("XIAOMI_API_KEY=api-only\n")
            assert _provider_has_key("xiaomi") is True
            assert _provider_has_key("xiaomi-token-plan") is False

            # Both present → both lit.
            (tmp_path / ".env").write_text(
                "XIAOMI_API_KEY=api-only\nXIAOMI_TOKEN_PLAN_API_KEY=tp-only\n"
            )
            assert _provider_has_key("xiaomi") is True
            assert _provider_has_key("xiaomi-token-plan") is True
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)


class TestSetProviderKeyLiveValidation:
    """Save-time validation: fetch models with the candidate key before writing.

    conftest disables HERMES_WEBUI_VALIDATE_KEY_ON_SAVE for the whole suite
    so no test touches the network by default; each test here opts back in
    and stubs model_catalog.query_live_models.
    """

    @staticmethod
    @contextlib.contextmanager
    def _scope(monkeypatch, tmp_path):
        """Isolate hermes home + config.cfg and enable save-time validation."""
        _install_fake_hermes_cli(monkeypatch)
        monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_WEBUI_VALIDATE_KEY_ON_SAVE", "1")
        # Belt and braces: a kicked background probe must never hit the
        # network from inside the test process.
        monkeypatch.setenv("HERMES_WEBUI_CONNECTIVITY_PROBE", "0")
        old_cfg = dict(config.cfg)
        old_mtime = config._cfg_mtime
        config.cfg.clear()
        config.cfg["model"] = {}
        try:
            config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
        except Exception:
            config._cfg_mtime = 0.0
        try:
            yield
        finally:
            config.cfg.clear()
            config.cfg.update(old_cfg)
            config._cfg_mtime = old_mtime

    @pytest.fixture(autouse=True)
    def _clear_probe_status(self):
        """Isolate the module-level probe-status cache between tests."""
        import app.api.providers as providers

        providers._PROBE_STATUS.clear()
        yield
        providers._PROBE_STATUS.clear()

    @staticmethod
    def _env_content(tmp_path) -> str:
        env_path = tmp_path / ".env"
        return env_path.read_text() if env_path.exists() else ""

    def test_auth_rejected_key_is_not_saved(self, monkeypatch, tmp_path):
        """HTTP 401 → key must not reach .env; error mentions the rejection."""
        import app.api.model_catalog as mc
        from app.api.providers import _get_probe_status, set_provider_key

        def _reject(slug, **kwargs):
            raise urllib.error.HTTPError(
                "https://api.anthropic.com/v1/models", 401, "Unauthorized", None, None
            )

        with self._scope(monkeypatch, tmp_path):
            monkeypatch.setattr(mc, "query_live_models", _reject)
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
            result = set_provider_key("anthropic", "sk-ant-bogus-key-123456")

        assert result["ok"] is False
        assert "rejected" in result["error"]
        assert "ANTHROPIC_API_KEY" not in self._env_content(tmp_path)
        # The rejection is recorded so the Settings row can badge it.
        status, _checked_at = _get_probe_status("anthropic")
        assert status == "auth"

    def test_valid_key_saves_and_reports_models_count(self, monkeypatch, tmp_path):
        import app.api.model_catalog as mc
        from app.api.providers import _get_probe_status, set_provider_key

        captured = {}

        def _ok(slug, **kwargs):
            captured["slug"] = slug
            captured.update(kwargs)
            return [{"id": "m1", "label": "M1"}, {"id": "m2", "label": "M2"}]

        with self._scope(monkeypatch, tmp_path):
            monkeypatch.setattr(mc, "query_live_models", _ok)
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
            result = set_provider_key("anthropic", "sk-ant-good-key-123456")

        assert result["ok"] is True
        assert result["validated"] is True
        assert result["models_count"] == 2
        assert "warning" not in result
        assert "ANTHROPIC_API_KEY=sk-ant-good-key-123456" in self._env_content(tmp_path)
        # The CANDIDATE key is what gets validated, not a stored one.
        assert captured["slug"] == "anthropic"
        assert captured["api_key"] == "sk-ant-good-key-123456"
        status, _checked_at = _get_probe_status("anthropic")
        assert status == "ok"

    def test_network_failure_saves_with_warning(self, monkeypatch, tmp_path):
        """Inconclusive network failure must not block the save."""
        import app.api.model_catalog as mc
        from app.api.providers import _get_probe_status, set_provider_key

        def _down(slug, **kwargs):
            raise urllib.error.URLError("connection timed out")

        with self._scope(monkeypatch, tmp_path):
            monkeypatch.setattr(mc, "query_live_models", _down)
            monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
            result = set_provider_key("anthropic", "sk-ant-key-12345678")

        assert result["ok"] is True
        assert result.get("warning")
        assert "ANTHROPIC_API_KEY=sk-ant-key-12345678" in self._env_content(tmp_path)
        status, _checked_at = _get_probe_status("anthropic")
        assert status == "network"

    def test_kill_switch_skips_validation(self, monkeypatch, tmp_path):
        import app.api.model_catalog as mc
        from app.api.providers import set_provider_key

        def _forbid(*args, **kwargs):
            raise AssertionError("validation disabled — query_live_models must not run")

        with self._scope(monkeypatch, tmp_path):
            monkeypatch.setenv("HERMES_WEBUI_VALIDATE_KEY_ON_SAVE", "0")
            monkeypatch.setattr(mc, "query_live_models", _forbid)
            result = set_provider_key("anthropic", "sk-ant-key-12345678")

        assert result["ok"] is True
        assert result.get("validated") is False
        assert "models_count" not in result

    def test_non_qualifying_provider_skips_validation(self, monkeypatch, tmp_path):
        """refresh.mode=none providers have nothing to fetch, so save-time
        validation is skipped.

        Every api_key provider in the catalog now carries a live endpoint,
        so stub a mode=none record to exercise the non-qualifying branch.
        """
        import app.api.model_catalog as mc
        from app.api.providers import set_provider_key

        def _forbid(*args, **kwargs):
            raise AssertionError("non-qualifying provider must not be queried")

        real_rec = mc.get_provider_record

        def _rec(slug, *args, **kwargs):
            rec = real_rec(slug, *args, **kwargs)
            if slug == "huggingface" and rec is not None:
                rec = dict(rec)
                rec["refresh"] = {**rec["refresh"], "mode": "none"}
            return rec

        with self._scope(monkeypatch, tmp_path):
            monkeypatch.setattr(mc, "query_live_models", _forbid)
            monkeypatch.setattr(mc, "get_provider_record", _rec)
            monkeypatch.delenv("HF_TOKEN", raising=False)
            result = set_provider_key("huggingface", "hf_token-bogus-key-123")

        assert result["ok"] is True
        assert result.get("validated") is False
        assert "HF_TOKEN=" in self._env_content(tmp_path)

    def test_get_providers_exposes_key_status(self, monkeypatch, tmp_path):
        import app.api.providers as providers

        with self._scope(monkeypatch, tmp_path):
            providers._record_probe_status("anthropic", "auth")
            result = providers.get_providers()

        entry = next(p for p in result["providers"] if p["id"] == "anthropic")
        assert entry["key_status"] == "auth"
        assert entry["key_checked_at"] is not None

        # Providers with no recorded result report nulls (no badge in the UI).
        unrecorded = next(p for p in result["providers"] if p["id"] != "anthropic")
        assert unrecorded["key_status"] is None
        assert unrecorded["key_checked_at"] is None


# ── Integration tests (via HTTP endpoints) ───────────────────────────────


class TestProvidersEndpoints:
    """Integration tests for /api/providers HTTP endpoints."""

    def test_get_providers_returns_200(self):
        """GET /api/providers should return 200 with provider list."""
        result = _get("/api/providers")
        assert "providers" in result
        assert isinstance(result["providers"], list)

    def test_post_provider_set_key(self):
        """POST /api/providers with provider + api_key should set the key."""
        body, status = _post("/api/providers", {
            "provider": "anthropic",
            "api_key": "sk-ant-integration-test-key-12345678",
        })
        assert status == 200
        assert body.get("ok") is True
        assert body.get("provider") == "anthropic"

    def test_post_provider_remove_key(self):
        """POST /api/providers with provider but no api_key should remove the key."""
        body, status = _post("/api/providers", {
            "provider": "anthropic",
            "api_key": None,
        })
        assert status == 200
        assert body.get("ok") is True
        assert body.get("action") == "removed"

    def test_post_provider_delete(self):
        """POST /api/providers/delete should remove the key."""
        body, status = _post("/api/providers/delete", {
            "provider": "anthropic",
        })
        assert status == 200
        assert body.get("ok") is True

    def test_post_provider_missing_id(self):
        """POST /api/providers without provider should return 400."""
        body, status = _post("/api/providers", {"api_key": "some-key"})
        assert status == 400
        assert "required" in body.get("error", "").lower()

    def test_post_provider_delete_missing_id(self):
        """POST /api/providers/delete without provider should return 400."""
        body, status = _post("/api/providers/delete", {})
        assert status == 400
