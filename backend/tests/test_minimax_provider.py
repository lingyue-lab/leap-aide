"""
Tests for MiniMax provider support in the model/provider discovery layer.

Covers:
  - MiniMax models appear in the fallback model list
  - MINIMAX_API_KEY env var is scanned and detected from os.environ
  - @minimax: provider hint routing works correctly
  - minimax/MiniMax-M2.7 (slash format) is routed via openrouter when active provider differs
"""
import os
import pytest
import app.api.config as config
import app.api.model_catalog as mc
import app.api.profiles as profiles
from tests.test_provider_management import _install_fake_hermes_cli


@pytest.fixture(autouse=True)
def _isolate_models_cache():
    """Invalidate the models TTL cache before and after every test in this file."""
    try:
        config.invalidate_models_cache()
    except Exception:
        pass
    yield
    try:
        config.invalidate_models_cache()
    except Exception:
        pass


# ── Helper ────────────────────────────────────────────────────────────────────

def _resolve_with_config(model_id, provider=None, base_url=None):
    old_cfg = dict(config.cfg)
    model_cfg = {}
    if provider:
        model_cfg['provider'] = provider
    if base_url:
        model_cfg['base_url'] = base_url
    config.cfg['model'] = model_cfg if model_cfg else {}
    try:
        return config.resolve_model_provider(model_id)
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)


# ── JSON catalog baseline (issue #10) ─────────────────────────────────────────

def test_minimax_baseline_has_bare_ids():
    """The catalog's minimax baseline must keep bare model ids (no @minimax:
    prefix) — minimax is the one provider whose dropdown ids are unprefixed."""
    from app.api import model_catalog
    ids = [m['id'] for m in model_catalog.provider_baseline_models('minimax')]
    assert 'MiniMax-M2.7' in ids, (
        f"MiniMax-M2.7 missing from catalog minimax baseline. Found: {ids}"
    )
    assert 'MiniMax-M2.7-highspeed' in ids, (
        f"MiniMax-M2.7-highspeed missing from catalog minimax baseline. Found: {ids}"
    )
    for mid in ids:
        assert not mid.startswith('@'), (
            f"minimax baseline ids must be bare (no @provider: prefix): {mid}"
        )


def test_minimax_display_name():
    """Catalog must render MiniMax as the group display name."""
    from app.api import model_catalog
    assert model_catalog.provider_display('minimax') == 'MiniMax'


def test_minimax_cn_card_shape():
    """The China card declares its gateway variant, split endpoints, and model ids."""
    rec = mc.get_provider_record("minimax-cn")
    assert rec is not None, "minimax-cn record missing from catalog"
    assert rec["display_name"] == "MiniMax (China)"
    assert rec["sdk_provider"] == "minimax-cn"
    # Declared as the singular env_var; the normalizing map side is covered
    # by test_env_vars_map in test_model_catalog_json.py.
    assert rec["auth"]["env_var"] == "MINIMAX_CN_API_KEY"
    assert rec["endpoint"]["base_url"] == "https://api.minimax.chat/v1"
    assert rec["endpoint"]["inference_base_url"] == "https://api.minimaxi.com/anthropic"
    ids = [m["id"] for m in rec["models"]]
    assert ids[0] == "@minimax-cn:MiniMax-M3.1-Flash"
    assert "@minimax-cn:MiniMax-M3" in ids
    assert all(i.startswith("@minimax-cn:") for i in ids)


def test_minimax_baseline_gains_m3_family_bare_ids():
    """Offline fallback keeps the reporter's model selectable on both cards."""
    old_ids = [m["id"] for m in mc.get_provider_record("minimax")["models"]]
    assert "MiniMax-M3" in old_ids and "MiniMax-M3.1-Flash" in old_ids
    assert all(not i.startswith("@") for i in old_ids)  # existing bare-id rule holds


def test_only_cn_key_lights_cn_card_not_global_card(monkeypatch, tmp_path):
    """env split: a CN-only user must not see the global MiniMax card as configured."""
    # Same fake-hermes-home pattern as the gateway-variant key tests in
    # test_provider_management.py (_install_fake_hermes_cli +
    # get_active_hermes_home -> tmp_path) so no residual config.yaml token
    # can light either card.
    _install_fake_hermes_cli(monkeypatch)
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for var in ("MINIMAX_API_KEY", "MINIMAX_CN_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MINIMAX_CN_API_KEY", "sk-cn-test")

    old_cfg = dict(config.cfg)
    config.cfg.clear()
    config.cfg["model"] = {}
    from app.api import providers
    try:
        assert providers._provider_has_key("minimax-cn") is True
        assert providers._provider_has_key("minimax") is False
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)


# ── MINIMAX_API_KEY env var detection ─────────────────────────────────────────

def test_minimax_env_vars_declared_in_catalog():
    """The catalog declares MINIMAX_API_KEY on the minimax card; the China
    gateway key MINIMAX_CN_API_KEY belongs to the separate minimax-cn card,
    so the env scan (driven by catalog_env_vars()) lights each card with its
    own key only, without editing ~/.hermes/.env."""
    from app.api import model_catalog
    env_vars = model_catalog.catalog_env_vars().get('minimax', [])
    assert 'MINIMAX_API_KEY' in env_vars, (
        f"MINIMAX_API_KEY not declared in catalog minimax record. Found: {env_vars}"
    )
    assert 'MINIMAX_CN_API_KEY' not in env_vars, (
        f"MINIMAX_CN_API_KEY must be declared by the minimax-cn card, not by "
        f"minimax. Found: {env_vars}"
    )
    cn_auth = mc.get_provider_record("minimax-cn")["auth"]
    # The loader keeps singular env_var and list env_vars as separate fields
    # (model_catalog.py:224-225); this record declares the singular env_var.
    assert cn_auth["env_var"] == "MINIMAX_CN_API_KEY"
    assert cn_auth["env_vars"] in (None, [])


def test_minimax_detected_from_os_environ(monkeypatch):
    """Setting MINIMAX_API_KEY in os.environ triggers minimax provider detection."""
    import urllib.error

    def _offline(slug, *a, **kw):
        raise urllib.error.URLError("offline")

    monkeypatch.setenv('MINIMAX_API_KEY', 'test-key-from-env')
    # Keep the test offline: raise a network error so the catalog falls
    # back to the JSON baseline (a live-success would replace it, and a
    # 401 would hide the group entirely).
    monkeypatch.setattr(config.model_catalog, 'query_live_models', _offline)
    old_cfg = dict(config.cfg)
    # Clear model config so the env-var fallback path is exercised
    config.cfg['model'] = {}
    try:
        result = config.get_available_models()
        provider_names = [g['provider'] for g in result['groups']]
        assert 'MiniMax' in provider_names, (
            f"MiniMax not detected when MINIMAX_API_KEY is set in os.environ. "
            f"Active provider groups: {provider_names}"
        )
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)


# ── Model routing ─────────────────────────────────────────────────────────────

def test_provider_hint_minimax_m2_7():
    """@minimax:MiniMax-M2.7 routes to minimax provider with bare model name."""
    model, provider, base_url = _resolve_with_config(
        '@minimax:MiniMax-M2.7', provider='anthropic',
    )
    assert model == 'MiniMax-M2.7'
    assert provider == 'minimax'
    assert base_url is None


def test_provider_hint_minimax_highspeed():
    """@minimax:MiniMax-M2.7-highspeed routes to minimax provider."""
    model, provider, base_url = _resolve_with_config(
        '@minimax:MiniMax-M2.7-highspeed', provider='openai',
    )
    assert model == 'MiniMax-M2.7-highspeed'
    assert provider == 'minimax'


def test_minimax_cn_prefix_routes_to_minimax_cn():
    """@minimax-cn: bare-model routing mirrors the @minimax: shape."""
    model, provider, base_url = _resolve_with_config(
        "@minimax-cn:MiniMax-M3.1-Flash", provider="anthropic",
    )
    assert model == "MiniMax-M3.1-Flash"
    assert provider == "minimax-cn"
    assert base_url is None


def test_minimax_slash_format_routes_openrouter_when_not_active():
    """minimax/MiniMax-M2.7 (slash format) routes via openrouter when active
    provider is anthropic (cross-provider routing)."""
    model, provider, base_url = _resolve_with_config(
        'minimax/MiniMax-M2.7', provider='anthropic',
    )
    assert model == 'minimax/MiniMax-M2.7'
    assert provider == 'openrouter'
