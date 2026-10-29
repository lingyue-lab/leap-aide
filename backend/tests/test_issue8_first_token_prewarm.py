"""
Tests for issue #8 — first-token latency: boot-time prewarm coordination,
the non-blocking model catalog peek, default IPv4 preference, and the
provider connectivity probe.

Issue #8: after a fresh boot the first chat stalled for dozens of seconds
(30s ConnectTimeout + tool-check gauntlet such as a runtime
``uv pip install edge-tts`` on the request path).  These tests pin the
LeapAide-side fixes:

  1-3. app.api.prewarm — single-flight tool-defs warm, done-always
       semantics, and wait helpers that no-op when no prewarm is active.
  4-5. peek_available_models — never triggers a live catalog build, joins
       an in-flight build with a bounded wait.
  6.   _resolve_compatible_session_model uses the peek, not the blocking
       get_available_models.
  7.   apply_network_preferences defaults to IPv4 preference (force=True)
       and honours the HERMES_WEBUI_FORCE_IPV4=0 opt-out.
  8.   probe_provider_connectivity classifies ok/auth/network/skipped and
       never raises.
  9.   set_provider_key fires a background probe.
"""
import pathlib
import sys
import threading
import time
import types
import urllib.error
import urllib.request

import pytest


# ── 1. warm_tool_definitions: single-flight + done-on-raise ─────────────────

@pytest.fixture
def prewarm_mod():
    """Import app.api.prewarm with module state snapshotted/restored."""
    import app.api.prewarm as prewarm

    saved_active = prewarm.PREWARM_ACTIVE
    saved_mcp = prewarm._MCP_DONE.is_set()
    saved_done = prewarm._TOOL_DEFS_DONE.is_set()
    saved_ran = prewarm._TOOL_DEFS_RAN
    yield prewarm
    prewarm.PREWARM_ACTIVE = saved_active
    if saved_mcp:
        prewarm._MCP_DONE.set()
    else:
        prewarm._MCP_DONE.clear()
    if saved_done:
        prewarm._TOOL_DEFS_DONE.set()
    else:
        prewarm._TOOL_DEFS_DONE.clear()
    prewarm._TOOL_DEFS_RAN = saved_ran


def test_warm_tool_definitions_is_single_flight(prewarm_mod, monkeypatch):
    """Only the first warm call computes; a duplicate returns immediately."""
    calls = []

    def fake_get_tool_definitions(**kwargs):
        calls.append(kwargs)
        return [{"name": "tool_a"}, {"name": "tool_b"}]

    monkeypatch.setattr(
        prewarm_mod, "_load_tool_definitions", lambda: fake_get_tool_definitions
    )
    prewarm_mod._TOOL_DEFS_RAN = False
    prewarm_mod._TOOL_DEFS_DONE.clear()

    prewarm_mod.warm_tool_definitions(["core"])
    prewarm_mod.warm_tool_definitions(["core"])  # duplicate must not recompute

    assert len(calls) == 1, "second warm must hit the single-flight guard"
    assert calls[0] == {"enabled_toolsets": ["core"], "quiet_mode": True}
    assert prewarm_mod._TOOL_DEFS_DONE.is_set()


def test_warm_tool_definitions_marks_done_on_failure(prewarm_mod, monkeypatch):
    """A failing SDK warm must not leave waiters hanging (done in finally)."""
    def boom(**kwargs):
        raise RuntimeError("sdk exploded")

    monkeypatch.setattr(prewarm_mod, "_load_tool_definitions", lambda: boom)
    prewarm_mod._TOOL_DEFS_RAN = False
    prewarm_mod._TOOL_DEFS_DONE.clear()

    prewarm_mod.warm_tool_definitions([])  # must swallow, never raise

    assert prewarm_mod._TOOL_DEFS_DONE.is_set(), (
        "done event must be set even when get_tool_definitions raises — "
        "otherwise first-chat waiters block for the full wait timeout"
    )
    prewarm_mod.PREWARM_ACTIVE = True
    assert prewarm_mod.wait_for_tool_definitions(timeout=0.5) is True


# ── 2. wait_for_tool_definitions: no-op without an active prewarm ───────────

def test_wait_for_tool_definitions_is_noop_when_inactive(prewarm_mod):
    """Tests/embedders that never call server.main() must never block."""
    prewarm_mod.PREWARM_ACTIVE = False
    prewarm_mod._TOOL_DEFS_DONE.clear()

    assert prewarm_mod.wait_for_tool_definitions(timeout=30.0) is True
    assert not prewarm_mod._TOOL_DEFS_DONE.is_set(), (
        "inactive wait must return True without setting the done event"
    )


# ── 3. wait_for_mcp_discovery before/after mark_mcp_done ────────────────────

def test_mcp_wait_blocks_until_marked(prewarm_mod):
    prewarm_mod.PREWARM_ACTIVE = True
    prewarm_mod._MCP_DONE.clear()

    assert prewarm_mod.wait_for_mcp_discovery(timeout=0.05) is False, (
        "with an active prewarm and discovery unfinished, wait must time out"
    )
    prewarm_mod.mark_mcp_done()
    assert prewarm_mod.wait_for_mcp_discovery(timeout=0.05) is True


def test_mcp_wait_is_noop_when_inactive(prewarm_mod):
    prewarm_mod.PREWARM_ACTIVE = False
    prewarm_mod._MCP_DONE.clear()

    assert prewarm_mod.wait_for_mcp_discovery(timeout=30.0) is True


# ── 4-5. peek_available_models: never builds, bounded wait for in-flight ────

def _isolate_catalog_state(monkeypatch):
    """Force peek's fast path: no memory cache, no disk cache, no reload."""
    import app.api.config as config

    monkeypatch.setattr(
        config, "_get_config_path", lambda: pathlib.Path("/nonexistent-config.yaml")
    )
    monkeypatch.setattr(config, "reload_config", lambda: None)
    monkeypatch.setattr(config, "_load_models_cache_from_disk", lambda: None)
    monkeypatch.setattr(config, "_available_models_cache", None)
    monkeypatch.setattr(config, "_cache_build_in_progress", False)


def test_peek_never_triggers_live_build(monkeypatch):
    """peek_available_models must answer without calling the blocking builder."""
    import app.api.config as config

    _isolate_catalog_state(monkeypatch)
    calls = []

    def _forbid(*args, **kwargs):
        calls.append(1)
        raise AssertionError("peek must not call get_available_models()")

    monkeypatch.setattr(config, "get_available_models", _forbid)

    result = config.peek_available_models()

    assert isinstance(result, dict)
    assert result["groups"] == [], "cold peek must fall back to config-only groups"
    assert "default_model" in result and "active_provider" in result
    assert result.get("config_only") is True, (
        "config-only fallbacks must be flagged so consumers skip "
        "group-dependent decisions instead of guessing on empty groups"
    )
    assert calls == [], "peek triggered a blocking catalog build"


def test_peek_waits_for_inflight_build(monkeypatch):
    """When a build is in flight, peek joins its result within the bound."""
    import app.api.config as config

    _isolate_catalog_state(monkeypatch)
    monkeypatch.setattr(config, "_cache_build_in_progress", True)
    filled = {
        "active_provider": "deepseek",
        "default_model": "deepseek-chat-v3-0324",
        "groups": [
            {
                "provider": "DeepSeek",
                "provider_id": "deepseek",
                "models": [{"id": "deepseek-chat-v3-0324", "label": "DeepSeek"}],
            }
        ],
    }

    def _fill():
        time.sleep(0.3)
        with config._available_models_cache_lock:
            config._available_models_cache = dict(filled)
            config._available_models_cache_ts = time.monotonic()
            config._cache_build_in_progress = False
            config._cache_build_cv.notify_all()

    filler = threading.Thread(target=_fill, daemon=True)
    filler.start()
    try:
        result = config.peek_available_models(wait_seconds=3.0)
    finally:
        filler.join(timeout=2.0)

    assert result["groups"] == filled["groups"], (
        "peek must wait for and return the in-flight build's result instead "
        "of degrading to an empty catalog while the build is running"
    )


def test_peek_never_parks_behind_builder_lock(monkeypatch):
    """get_available_models() holds the cache lock for its WHOLE live build.

    peek must bound lock acquisition too (not just the cv wait), otherwise
    a request issued during the boot prewarm's cold build blocks for the
    full multi-second build — the issue #8 smoke regression where
    /api/session/new exceeded the 5s client timeout."""
    import app.api.config as config

    _isolate_catalog_state(monkeypatch)
    monkeypatch.setattr(config, "_cache_build_in_progress", True)

    def _hold_lock():
        config._available_models_cache_lock.acquire()
        time.sleep(0.8)  # longer than the 0.3s peek budget under test
        config._available_models_cache_lock.release()

    holder = threading.Thread(target=_hold_lock, daemon=True)
    holder.start()
    time.sleep(0.1)  # let the holder win the lock

    t0 = time.monotonic()
    try:
        result = config.peek_available_models(wait_seconds=0.3)
        elapsed = time.monotonic() - t0  # measure only the peek, not the join
    finally:
        holder.join(timeout=4.0)

    assert result["groups"] == [] and "default_model" in result
    assert elapsed < 1.0, (
        f"peek blocked {elapsed:.2f}s behind the builder lock — lock "
        "acquisition must be bounded by wait_seconds"
    )


# ── 6. session model resolution avoids the blocking builder ─────────────────

def test_session_model_resolver_uses_peek_not_builder(monkeypatch):
    import app.api.routes as routes

    def _forbid(*args, **kwargs):
        raise AssertionError(
            "_resolve_compatible_session_model must not call get_available_models()"
        )

    monkeypatch.setattr(routes, "get_available_models", _forbid)
    monkeypatch.setattr(
        routes,
        "peek_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
            "groups": [],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "gemini-3.1-pro-preview"
    )

    assert changed is True
    assert effective == "gpt-5.4-mini"


def test_resolver_preserves_model_on_config_only_catalog(monkeypatch):
    """A config-only peek (cold cache) must not drive cross-provider rewrites.

    With no groups to check routability against, the custom-provider branch
    would rewrite any slashed model to the default — issue #8 smoke regression:
    test_session_update saw anthropic/claude-sonnet-4.6 replaced by
    stub-test-model on GET /api/session while the boot prewarm still held the
    catalog lock."""
    import app.api.routes as routes

    monkeypatch.setattr(
        routes,
        "peek_available_models",
        lambda: {
            "active_provider": "custom",
            "default_model": "stub-test-model",
            "groups": [],
            "config_only": True,
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "anthropic/claude-sonnet-4.6"
    )

    assert changed is False, "config-only catalog must skip normalization"
    assert effective == "anthropic/claude-sonnet-4.6"


# ── 7. apply_network_preferences: IPv4 default + env opt-out ────────────────

def _install_fake_hermes_constants(monkeypatch) -> list:
    """Inject a recording fake hermes_constants module (never call the real
    apply_ipv4_preference — it monkey-patches getaddrinfo process-wide)."""
    calls = []
    fake = types.ModuleType("hermes_constants")
    fake.apply_ipv4_preference = lambda force=False: calls.append(force)
    monkeypatch.setitem(sys.modules, "hermes_constants", fake)
    return calls


def test_network_preferences_default_forces_ipv4(monkeypatch):
    import app.api.config as config

    calls = _install_fake_hermes_constants(monkeypatch)
    monkeypatch.delenv("HERMES_WEBUI_FORCE_IPV4", raising=False)
    monkeypatch.setattr(config, "cfg", {"network": {}})

    assert config.apply_network_preferences() is True
    assert calls == [True], "IPv4 preference must be applied with force=True"

    # Idempotent: calling again re-applies without error.
    assert config.apply_network_preferences() is True
    assert calls == [True, True]


def test_network_preferences_env_opt_out(monkeypatch):
    import app.api.config as config

    calls = _install_fake_hermes_constants(monkeypatch)
    monkeypatch.setenv("HERMES_WEBUI_FORCE_IPV4", "0")
    monkeypatch.setattr(config, "cfg", {"network": {}})

    assert config.apply_network_preferences() is False
    assert calls == [], "HERMES_WEBUI_FORCE_IPV4=0 must skip the patch entirely"


# ── 8. probe_provider_connectivity: classification + never raises ───────────

def _patch_probe_endpoint(monkeypatch, resolved):
    """Patch providers._resolve_probe_endpoint; resolved=None → no endpoint."""
    import app.api.providers as providers

    monkeypatch.setattr(providers, "_resolve_probe_endpoint", lambda pid=None: resolved)


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        return b"{}"


def test_probe_ok(monkeypatch):
    import app.api.providers as providers

    monkeypatch.delenv("HERMES_WEBUI_CONNECTIVITY_PROBE", raising=False)
    _patch_probe_endpoint(monkeypatch, ("https://api.deepseek.com/v1", "sk-test", "deepseek"))
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _FakeResp())

    assert providers.probe_provider_connectivity() == "ok"


def test_probe_network_errors(monkeypatch):
    import app.api.providers as providers

    monkeypatch.delenv("HERMES_WEBUI_CONNECTIVITY_PROBE", raising=False)
    _patch_probe_endpoint(monkeypatch, ("https://api.deepseek.com/v1", "sk-test", "deepseek"))

    def _raise_urlerror(req, timeout=None):
        raise urllib.error.URLError("connection timed out")

    monkeypatch.setattr(urllib.request, "urlopen", _raise_urlerror)
    assert providers.probe_provider_connectivity() == "network"

    def _raise_timeout(req, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", _raise_timeout)
    assert providers.probe_provider_connectivity() == "network"


def test_probe_auth_rejected(monkeypatch):
    import app.api.providers as providers

    monkeypatch.delenv("HERMES_WEBUI_CONNECTIVITY_PROBE", raising=False)
    _patch_probe_endpoint(monkeypatch, ("https://api.deepseek.com/v1", "sk-test", "deepseek"))

    def _raise_401(req, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.deepseek.com/v1/models", 401, "Unauthorized", None, None
        )

    monkeypatch.setattr(urllib.request, "urlopen", _raise_401)
    assert providers.probe_provider_connectivity() == "auth"


def test_probe_401_without_key_is_still_ok(monkeypatch):
    """No key configured → a 401 still proves the network path works."""
    import app.api.providers as providers

    monkeypatch.delenv("HERMES_WEBUI_CONNECTIVITY_PROBE", raising=False)
    _patch_probe_endpoint(monkeypatch, ("https://api.deepseek.com/v1", "", "deepseek"))

    def _raise_401(req, timeout=None):
        raise urllib.error.HTTPError(
            "https://api.deepseek.com/v1/models", 401, "Unauthorized", None, None
        )

    monkeypatch.setattr(urllib.request, "urlopen", _raise_401)
    assert providers.probe_provider_connectivity() == "ok"


def test_probe_skipped_without_endpoint(monkeypatch):
    import app.api.providers as providers

    monkeypatch.delenv("HERMES_WEBUI_CONNECTIVITY_PROBE", raising=False)
    _patch_probe_endpoint(monkeypatch, None)

    def _forbid(req, timeout=None):
        raise AssertionError("no endpoint resolved — probe must not issue a request")

    monkeypatch.setattr(urllib.request, "urlopen", _forbid)
    assert providers.probe_provider_connectivity() == "skipped"


def test_probe_disabled_by_env(monkeypatch):
    import app.api.providers as providers

    monkeypatch.setenv("HERMES_WEBUI_CONNECTIVITY_PROBE", "0")
    monkeypatch.setattr(
        providers,
        "_resolve_probe_endpoint",
        lambda pid=None: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )

    assert providers.probe_provider_connectivity() == "skipped"


# ── 9. key-save triggers fire-and-forget probe ──────────────────────────────

def test_set_provider_key_fires_background_probe(monkeypatch):
    import app.api.providers as providers

    monkeypatch.delenv("HERMES_WEBUI_CONNECTIVITY_PROBE", raising=False)
    seen = threading.Event()
    seen_ids = []

    def _probe(provider_id=None, **kwargs):
        seen_ids.append(provider_id)
        seen.set()
        return "ok"

    monkeypatch.setattr(providers, "probe_provider_connectivity", _probe)
    monkeypatch.setattr(providers, "_write_env_file", lambda *a, **k: None)
    monkeypatch.setattr(providers, "invalidate_models_cache", lambda: None)

    result = providers.set_provider_key("deepseek", "sk-valid-key-123")

    assert result["ok"] is True
    assert seen.wait(5.0), "set_provider_key must fire a background connectivity probe"
    assert seen_ids == ["deepseek"]


def test_remove_provider_key_skips_probe(monkeypatch):
    import app.api.providers as providers

    fired = threading.Event()
    monkeypatch.setattr(
        providers,
        "probe_provider_connectivity",
        lambda provider_id=None, **kwargs: fired.set() or "ok",
    )
    monkeypatch.setattr(providers, "_write_env_file", lambda *a, **k: None)
    monkeypatch.setattr(providers, "invalidate_models_cache", lambda: None)

    result = providers.set_provider_key("deepseek", None)

    assert result["ok"] is True and result["action"] == "removed"
    time.sleep(0.3)
    assert not fired.is_set(), "key removal has nothing to validate — no probe"
