"""Regression tests for credential_pool provider detection in /api/models."""

import json
import sys
import types

import app.api.config as config
import app.api.profiles as profiles

_AMBIENT_SOURCES = {"gh_cli", "gh auth token"}


def _install_fake_hermes_cli(monkeypatch, *, with_load_pool: bool = False, pool_data: dict | None = None):
    """Stub hermes_cli modules so tests are deterministic and offline.

    When *with_load_pool* is True, also stubs hermes_cli.credential_pool with a
    suppression-aware load_pool() implementation that mirrors upstream behaviour:
    entries whose source/label/key_source signals ambient gh-cli auth are filtered out.
    """
    fake_pkg = types.ModuleType("hermes_cli")
    fake_pkg.__path__ = []

    fake_models = types.ModuleType("hermes_cli.models")
    fake_models.list_available_providers = lambda: []

    fake_auth = types.ModuleType("hermes_cli.auth")
    fake_auth.get_auth_status = lambda _pid: {}

    monkeypatch.setitem(sys.modules, "hermes_cli", fake_pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.models", fake_models)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", fake_auth)

    # Always remove the real agent.credential_pool so get_available_models() takes
    # the ImportError fallback path and reads from the monkeypatched auth store,
    # not the live ~/.hermes/auth.json via the real venv module.
    monkeypatch.delitem(sys.modules, "agent.credential_pool", raising=False)
    monkeypatch.delitem(sys.modules, "agent", raising=False)

    if with_load_pool:
        _pool_data = pool_data or {}

        class _FakeEntry:
            """Minimal PooledCredential stand-in with attribute access (matching the real class)."""
            def __init__(self, d):
                self.source = d.get("source", "manual")
                self.label = d.get("label", "")
                self.key_source = d.get("key_source", "")
                self.id = d.get("id", "")

        class _FakePool:
            def __init__(self, entries_list):
                self._entries = entries_list

            def entries(self):
                return self._entries

        def _fake_load_pool(pid):
            # Return ALL entries without filtering — mirrors the real load_pool()
            # which does NOT suppress ambient gh-cli tokens on its own.
            # Ambient-source filtering is the webui's responsibility.
            raw = _pool_data.get(pid, [])
            return _FakePool([_FakeEntry(e) for e in raw])

        fake_cp = types.ModuleType("agent.credential_pool")
        fake_cp.load_pool = _fake_load_pool
        monkeypatch.setitem(sys.modules, "agent.credential_pool", fake_cp)


def _call_get_available_models(monkeypatch, tmp_path, auth_payload, *, with_load_pool: bool = False):
    """Call get_available_models() with auth.json pinned to a temp Hermes home."""
    _install_fake_hermes_cli(
        monkeypatch,
        with_load_pool=with_load_pool,
        pool_data=auth_payload.get("credential_pool", {}),
    )

    (tmp_path / "auth.json").write_text(json.dumps(auth_payload), encoding="utf-8")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg["model"] = {}
    try:
        # Pin mtime to avoid reload_config() clobbering our in-memory cfg patch.
        config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
    except Exception:
        config._cfg_mtime = 0.0

    config.invalidate_models_cache()
    try:
        return config.get_available_models()
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()


def _group_by_provider(result):
    return {g["provider"]: g["models"] for g in result.get("groups", [])}


def test_ollama_cloud_manual_credential_empty_baseline_omits_group(monkeypatch, tmp_path):
    """A manual ollama-cloud credential alone no longer invents a model group.

    Historically the SDK's provider_model_ids() enumerated Ollama Cloud
    models from the credential (issue #10 refactor removed that path).
    The catalog baseline for ollama-cloud is empty and refresh.mode=none,
    so — like the empty-discovery case below — the group is omitted rather
    than rendered with no models. Users who want the group can declare
    providers.ollama-cloud.models in config.yaml (covered by the test
    after this one).
    """
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "ollama-cloud": [
                {
                    "id": "abc123",
                    "label": "ollama-manual",
                    "source": "manual",
                    "auth_type": "api_key",
                    "base_url": "https://ollama.com/v1",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload)
    groups = _group_by_provider(result)
    assert "Ollama Cloud" not in groups, (
        f"Ollama Cloud group must be omitted with an empty catalog baseline; "
        f"got {list(groups)}"
    )


def test_ollama_cloud_cfg_models_override_shows_group(monkeypatch, tmp_path):
    """providers.ollama-cloud.models in config.yaml still surfaces the group."""
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "ollama-cloud": [
                {
                    "id": "abc123",
                    "label": "ollama-manual",
                    "source": "manual",
                    "auth_type": "api_key",
                    "base_url": "https://ollama.com/v1",
                }
            ]
        },
    }

    _install_fake_hermes_cli(monkeypatch)
    (tmp_path / "auth.json").write_text(json.dumps(auth_payload), encoding="utf-8")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg["model"] = {}
    config.cfg["providers"] = {"ollama-cloud": {"models": ["gpt-oss:20b", "qwen3:30b-a3b"]}}
    try:
        config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
    except Exception:
        config._cfg_mtime = 0.0

    config.invalidate_models_cache()
    try:
        result = config.get_available_models()
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()

    groups = _group_by_provider(result)
    assert "Ollama Cloud" in groups, f"Expected Ollama Cloud in {list(groups)}"
    model_ids = [m["id"] for m in groups["Ollama Cloud"]]
    assert model_ids == ["@ollama-cloud:gpt-oss:20b", "@ollama-cloud:qwen3:30b-a3b"], model_ids


def test_copilot_gh_cli_only_credential_hidden(monkeypatch, tmp_path):
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "copilot": [
                {
                    "id": "def456",
                    "label": "gh auth token",
                    "source": "gh_cli",
                    "auth_type": "api_key",
                    "base_url": "https://api.githubcopilot.com",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload)
    groups = _group_by_provider(result)
    assert "GitHub Copilot" not in groups, (
        "GitHub Copilot should be hidden when only ambient gh auth token is present; "
        f"got {list(groups)}"
    )


def test_copilot_mixed_credential_pool_remains_visible(monkeypatch, tmp_path):
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "copilot": [
                {
                    "id": "def456",
                    "label": "gh auth token",
                    "source": "gh_cli",
                    "auth_type": "api_key",
                    "base_url": "https://api.githubcopilot.com",
                },
                {
                    "id": "ghi789",
                    "label": "explicit-copilot",
                    "source": "manual",
                    "auth_type": "api_key",
                    "base_url": "https://api.githubcopilot.com",
                },
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload)
    groups = _group_by_provider(result)
    assert "GitHub Copilot" in groups, f"Expected GitHub Copilot in {list(groups)}"
    model_ids = [m["id"] for m in groups["GitHub Copilot"]]
    assert "@copilot:gpt-5.4" in model_ids, model_ids
    assert "@copilot:claude-opus-4.6" in model_ids, model_ids


def test_copilot_empty_field_entries_are_treated_as_explicit(monkeypatch, tmp_path):
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "copilot": [
                {
                    "id": "jkl012",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload)
    groups = _group_by_provider(result)
    assert "GitHub Copilot" in groups, f"Expected GitHub Copilot in {list(groups)}"


def test_copilot_oauth_credential_is_visible(monkeypatch, tmp_path):
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "copilot": [
                {
                    "id": "mno345",
                    "label": "github-oauth",
                    "source": "oauth",
                    "auth_type": "oauth",
                    "base_url": "https://api.githubcopilot.com",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload)
    groups = _group_by_provider(result)
    assert "GitHub Copilot" in groups, f"Expected GitHub Copilot in {list(groups)}"


# --- load_pool path (suppression-aware) ---


def test_load_pool_copilot_ambient_only_remains_hidden(monkeypatch, tmp_path):
    """load_pool path: copilot with only ambient gh-cli entries is suppressed."""
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "copilot": [
                {
                    "id": "lp001",
                    "label": "gh auth token",
                    "source": "gh_cli",
                    "auth_type": "api_key",
                    "base_url": "https://api.githubcopilot.com",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload, with_load_pool=True)
    groups = _group_by_provider(result)
    assert "GitHub Copilot" not in groups, (
        "GitHub Copilot must be hidden when load_pool returns no usable entries; "
        f"got {list(groups)}"
    )


def test_load_pool_copilot_ambient_key_source_only_remains_hidden(monkeypatch, tmp_path):
    """load_pool path: key_source-only ambient markers must also be suppressed."""
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "copilot": [
                {
                    "id": "lp001b",
                    "label": "copilot-token",
                    "source": "manual",
                    "key_source": "gh auth token",
                    "auth_type": "api_key",
                    "base_url": "https://api.githubcopilot.com",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload, with_load_pool=True)
    groups = _group_by_provider(result)
    assert "GitHub Copilot" not in groups, (
        "GitHub Copilot must stay hidden when load_pool entries only differ by key_source ambient markers; "
        f"got {list(groups)}"
    )


def test_load_pool_alias_provider_key_is_resolved(monkeypatch, tmp_path):
    """load_pool path: aliased pool keys should resolve to canonical provider ids."""
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "google": [
                {
                    "id": "gp001",
                    "label": "explicit-gemini",
                    "source": "manual",
                    "auth_type": "api_key",
                    "base_url": "https://generativelanguage.googleapis.com",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload, with_load_pool=True)
    groups = _group_by_provider(result)
    assert "Gemini" in groups, f"Expected Gemini in {list(groups)}"
    assert "Google" not in groups, f"Aliased provider key should not render under raw alias name: {list(groups)}"


def test_load_pool_explicit_credential_shows_provider(monkeypatch, tmp_path):
    """load_pool path: provider with at least one explicit entry is visible."""
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "copilot": [
                {
                    "id": "lp002",
                    "label": "gh auth token",
                    "source": "gh_cli",
                    "auth_type": "api_key",
                    "base_url": "https://api.githubcopilot.com",
                },
                {
                    "id": "lp003",
                    "label": "explicit-pat",
                    "source": "manual",
                    "auth_type": "api_key",
                    "base_url": "https://api.githubcopilot.com",
                },
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload, with_load_pool=True)
    groups = _group_by_provider(result)
    assert "GitHub Copilot" in groups, (
        f"GitHub Copilot must appear when load_pool has at least one usable entry; got {list(groups)}"
    )


# --- _apply_provider_prefix helper ---


def test_apply_provider_prefix_ollama_cloud_non_active():
    """Bare ollama-cloud model ids get @ollama-cloud: prefix."""
    from app.api.config import _apply_provider_prefix

    raw = [{"id": "gpt-oss:20b", "label": "gpt-oss:20b"}, {"id": "qwen3:30b-a3b", "label": "qwen3:30b-a3b"}]
    result = _apply_provider_prefix(raw, "ollama-cloud")
    ids = [m["id"] for m in result]
    assert ids == ["@ollama-cloud:gpt-oss:20b", "@ollama-cloud:qwen3:30b-a3b"], ids


def test_apply_provider_prefix_copilot_non_active():
    """Bare copilot model ids get @copilot: prefix."""
    from app.api.config import _apply_provider_prefix

    raw = [{"id": "gpt-5.4", "label": "GPT-5.4"}, {"id": "claude-opus-4.6", "label": "Claude Opus 4.6"}]
    result = _apply_provider_prefix(raw, "copilot")
    ids = [m["id"] for m in result]
    assert ids == ["@copilot:gpt-5.4", "@copilot:claude-opus-4.6"], ids


def test_apply_provider_prefix_no_double_prefix():
    """Already-prefixed ids pass through; slash ids are prefixed too."""
    from app.api.config import _apply_provider_prefix

    raw = [
        {"id": "@copilot:gpt-5.4", "label": "already prefixed"},
        {"id": "openai/gpt-5.4", "label": "slash form"},
        {"id": "bare-model", "label": "bare"},
    ]
    result = _apply_provider_prefix(raw, "copilot")
    ids = [m["id"] for m in result]
    assert ids == [
        "@copilot:gpt-5.4",
        "@copilot:openai/gpt-5.4",
        "@copilot:bare-model",
    ], ids


def test_apply_provider_prefix_active_provider_also_prefixed():
    """The active provider's models are prefixed like every other group."""
    from app.api.config import _apply_provider_prefix

    raw = [{"id": "gpt-5.4", "label": "GPT-5.4"}]
    result = _apply_provider_prefix(raw, "openai-codex")
    ids = [m["id"] for m in result]
    assert ids == ["@openai-codex:gpt-5.4"], ids


def test_copilot_mixed_pool_prefixed_models(monkeypatch, tmp_path):
    """Copilot with mixed pool and non-active provider has @copilot: prefixed model ids."""
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "copilot": [
                {
                    "id": "lp010",
                    "label": "explicit-copilot",
                    "source": "manual",
                    "auth_type": "api_key",
                    "base_url": "https://api.githubcopilot.com",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload)
    groups = _group_by_provider(result)
    assert "GitHub Copilot" in groups
    model_ids = [m["id"] for m in groups["GitHub Copilot"]]
    assert all(mid.startswith("@copilot:") for mid in model_ids), model_ids


def test_auth_store_active_provider_alias_is_resolved(monkeypatch, tmp_path):
    """active_provider read from auth.json must be alias-normalized.

    The alias table maps 'google' to 'gemini', so the group's provider id —
    and therefore its @provider:model prefixes — use the canonical slug.
    """
    auth_payload = {
        "version": 1,
        "providers": {},
        # Aliased name: 'google' → 'gemini' per the catalog alias table.
        "active_provider": "google",
        "credential_pool": {},
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload)
    groups = _group_by_provider(result)
    # Gemini should appear under its canonical display name and its model
    # ids should carry the canonical @gemini: prefix.
    assert "Gemini" in groups, f"Expected Gemini in {list(groups)}"
    model_ids = [m["id"] for m in groups["Gemini"]]
    assert model_ids, "Gemini group should have models"
    assert all(mid.startswith("@gemini:") for mid in model_ids), model_ids


def test_ollama_cloud_empty_catalog_skips_group(monkeypatch, tmp_path):
    """With no models discoverable for ollama-cloud, the group is omitted.

    The catalog baseline for ollama-cloud is empty (refresh.mode=none), so
    — matching the named-custom and unknown-provider branches — we don't
    invent a catalog we can't enumerate.
    """
    _install_fake_hermes_cli(monkeypatch)

    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "ollama-cloud": [
                {
                    "id": "oc-empty",
                    "label": "ollama-manual",
                    "source": "manual",
                    "auth_type": "api_key",
                }
            ]
        },
    }

    (tmp_path / "auth.json").write_text(json.dumps(auth_payload), encoding="utf-8")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg["model"] = {}
    try:
        config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
    except Exception:
        config._cfg_mtime = 0.0

    try:
        result = config.get_available_models()
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime

    groups = _group_by_provider(result)
    assert "Ollama Cloud" not in groups, (
        f"Ollama Cloud group should be skipped when catalog is empty; got {list(groups)}"
    )


# --- _format_ollama_label helper ---


def test_format_ollama_label_simple():
    from app.api.config import _format_ollama_label

    assert _format_ollama_label("kimi-k2.5") == "Kimi K2.5"


def test_format_ollama_label_with_variant():
    from app.api.config import _format_ollama_label

    assert _format_ollama_label("qwen3-vl:235b-instruct") == "Qwen3 VL (235B Instruct)"


def test_format_ollama_label_short_acronym():
    from app.api.config import _format_ollama_label

    assert _format_ollama_label("glm-5.1") == "GLM 5.1"


def test_format_ollama_label_gpt_oss_with_size():
    from app.api.config import _format_ollama_label

    assert _format_ollama_label("gpt-oss:20b") == "GPT OSS (20B)"


def test_format_ollama_label_empty_string():
    from app.api.config import _format_ollama_label

    assert _format_ollama_label("") == ""


def test_format_ollama_label_no_variant():
    from app.api.config import _format_ollama_label

    assert _format_ollama_label("nemotron-3-super") == "Nemotron 3 Super"


# --- Fallback-path (ImportError branch) alias resolution ---


def test_fallback_path_resolves_alias_when_load_pool_unavailable(monkeypatch, tmp_path):
    """When agent.credential_pool can't be imported, the manual-inspection
    branch must still canonicalize pool keys so aliased names (e.g. 'google')
    end up under their canonical provider id ('gemini')."""
    _install_fake_hermes_cli(monkeypatch)
    # Ensure agent.credential_pool is not importable so the fallback branch runs.
    monkeypatch.setitem(sys.modules, "agent.credential_pool", None)

    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "openai-codex",
        "credential_pool": {
            "google": [
                {
                    "id": "gp-fallback",
                    "label": "explicit-gemini",
                    "source": "manual",
                    "auth_type": "api_key",
                }
            ]
        },
    }

    (tmp_path / "auth.json").write_text(json.dumps(auth_payload), encoding="utf-8")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg["model"] = {}
    try:
        config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
    except Exception:
        config._cfg_mtime = 0.0

    try:
        result = config.get_available_models()
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime

    groups = _group_by_provider(result)
    assert "Gemini" in groups, (
        f"Fallback path must resolve 'google' -> 'gemini'; got {list(groups)}"
    )
    assert "Google" not in groups, (
        f"Raw alias name must not leak when fallback path runs; got {list(groups)}"
    )


# --- Regression: env-sourced pool entries must not bypass the key check ---
# A credential_pool entry with `source: env:VAR` is only a mirror of the env
# var. When the key is deleted the mirror lingers in auth.json but no longer
# represents a usable credential, so it must not keep the provider visible.


def test_stale_env_sourced_pool_entry_does_not_show_keyless_provider(monkeypatch, tmp_path):
    """load_pool path: a deleted key must hide the provider even if a stale
    env-sourced pool entry remains in auth.json."""
    monkeypatch.delenv("XIAOMI_API_KEY", raising=False)
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "",
        "credential_pool": {
            "xiaomi": [
                {
                    "id": "xp001",
                    "label": "env mirror",
                    # Mirror of a now-deleted XIAOMI_API_KEY.
                    "source": "env:XIAOMI_API_KEY",
                    "auth_type": "api_key",
                    "base_url": "https://api.xiaomimimo.com/v1",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload, with_load_pool=True)
    groups = _group_by_provider(result)
    xiaomi = config.model_catalog.provider_display("xiaomi")
    assert xiaomi not in groups, (
        f"A stale env-sourced pool entry must not keep a keyless provider visible; got {list(groups)}"
    )


def test_manual_pool_entry_still_shows_provider_without_env_key(monkeypatch, tmp_path):
    """load_pool path: a manual (non-env) pool entry is a real credential and
    must still bypass _provider_has_key — guards against over-filtering."""
    monkeypatch.delenv("XIAOMI_API_KEY", raising=False)
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "",
        "credential_pool": {
            "xiaomi": [
                {
                    "id": "xp002",
                    "label": "manual-cred",
                    "source": "manual",
                    "auth_type": "api_key",
                    "base_url": "https://api.xiaomimimo.com/v1",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload, with_load_pool=True)
    groups = _group_by_provider(result)
    xiaomi = config.model_catalog.provider_display("xiaomi")
    assert xiaomi in groups, (
        f"A manual pool entry is a real credential and must keep the provider visible; got {list(groups)}"
    )


def test_stale_env_sourced_entry_hidden_on_importerror_fallback_path(monkeypatch, tmp_path):
    """ImportError fallback path: same stale env-mirror rule as the load_pool path."""
    monkeypatch.delenv("XIAOMI_API_KEY", raising=False)
    auth_payload = {
        "version": 1,
        "providers": {},
        "active_provider": "",
        "credential_pool": {
            "xiaomi": [
                {
                    "id": "xp003",
                    "label": "env mirror",
                    "source": "env:XIAOMI_API_KEY",
                    "auth_type": "api_key",
                }
            ]
        },
    }

    result = _call_get_available_models(monkeypatch, tmp_path, auth_payload, with_load_pool=False)
    groups = _group_by_provider(result)
    xiaomi = config.model_catalog.provider_display("xiaomi")
    assert xiaomi not in groups, (
        f"Fallback path must also drop a stale env-sourced entry; got {list(groups)}"
    )


# --- Regression: catalog .env scan must run even when hermes_cli succeeds ---
# hermes_cli only knows providers it registered itself. Gateway variants
# (e.g. xiaomi-token-plan / XIAOMI_TOKEN_PLAN_API_KEY) are invisible to it, so
# the catalog env-var scan must run unconditionally — not only when hermes_cli
# fails — or a configured Token-Plan key never surfaces in the model picker.


def test_catalog_env_scan_detects_variant_even_when_hermes_cli_succeeds(monkeypatch, tmp_path):
    import types as _types

    # hermes_cli "succeeds" and reports one authenticated (OAuth) provider.
    fake_pkg = _types.ModuleType("hermes_cli")
    fake_pkg.__path__ = []
    fake_models = _types.ModuleType("hermes_cli.models")
    fake_models.list_available_providers = lambda: [{"id": "copilot", "authenticated": True}]
    fake_auth = _types.ModuleType("hermes_cli.auth")
    fake_auth.get_auth_status = lambda _pid: {}
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.models", fake_models)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", fake_auth)
    monkeypatch.delitem(sys.modules, "agent.credential_pool", raising=False)
    monkeypatch.delitem(sys.modules, "agent", raising=False)

    # Configure the token-plan gateway key that hermes_cli cannot see.
    monkeypatch.setenv("XIAOMI_TOKEN_PLAN_API_KEY", "unit-test-token-plan-key")

    # Keep the test offline: a fake key against the live gateway would 401,
    # and the catalog now hides groups whose key is rejected (auth failure).
    # A network-style failure makes resolve_provider_models fall back to
    # the JSON baseline, which is what this test asserts on.
    import urllib.error

    def _offline(slug, *a, **kw):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(config.model_catalog, "query_live_models", _offline)

    auth_payload = {"version": 1, "providers": {}, "active_provider": "", "credential_pool": {}}
    (tmp_path / "auth.json").write_text(json.dumps(auth_payload), encoding="utf-8")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg["model"] = {}
    try:
        config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
    except Exception:
        config._cfg_mtime = 0.0
    config.invalidate_models_cache()
    try:
        result = config.get_available_models()
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()

    groups = _group_by_provider(result)
    # The hermes_cli-reported provider is still detected (post-filter keeps OAuth).
    assert "GitHub Copilot" in groups, f"hermes_cli-detected provider missing; got {list(groups)}"
    # The catalog .env scan must ALSO add the gateway variant hermes_cli cannot see.
    variant = config.model_catalog.provider_display("xiaomi-token-plan")
    assert variant in groups, (
        f"Catalog .env scan must detect xiaomi-token-plan despite hermes_cli succeeding; got {list(groups)}"
    )
