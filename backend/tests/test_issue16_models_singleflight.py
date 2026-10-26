"""
Tests for issue #16 — model catalog single-flight build coordination and the
bounded /api/models degrade path.

Issue #16: the first Model picker open spun for 48-160s and switching
panels during the spin froze the page.  The live catalog build used to run
INSIDE _available_models_cache_lock, so every concurrent /api/models and
every peek parked behind the whole SDK provider loop.  These tests pin the
LeapAide-side fixes:

  1. The live build always runs on a background worker; concurrent cold
     callers share exactly one build.
  2. A bounded waiter (wait_seconds=N) degrades to a building:True answer
     instead of blocking for the build.
  3. A stale memory cache is served (flagged building:True) while a
     rebuild is in flight.
  4. An invalidation that races a build discards the stale publish and
     retries once (generation counter).
  5. A failing build clears the single-flight flag, raises to the call
     that spawned the worker, and a later call retries.
  6. A bounded waiter degrades (does not raise) when someone else's build
     fails.
  7. normalize_model_prefix accepts a pre-fetched catalog (batch callers
     never re-peek per row).
  8. apply_aws_metadata_preference: unreachable IMDS sets the botocore
     opt-out env vars; reachable IMDS / operator opt-out / disabled knob
     leave the environment untouched.
"""
import os
import pathlib
import socket
import threading
import time

import pytest


# ── Shared fixtures & helpers ────────────────────────────────────────────────

_FULL_CATALOG = {
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


@pytest.fixture
def isolated_catalog(monkeypatch):
    """Isolate the catalog cache globals and all disk/config side effects."""
    import app.api.config as config

    saved = {
        "_available_models_cache": config._available_models_cache,
        "_available_models_cache_ts": config._available_models_cache_ts,
        "_cache_build_in_progress": config._cache_build_in_progress,
        "_models_cache_generation": config._models_cache_generation,
        "_models_build_error": config._models_build_error,
        "_cfg_mtime": config._cfg_mtime,
    }

    monkeypatch.setattr(
        config, "_get_config_path", lambda: pathlib.Path("/nonexistent-config.yaml")
    )
    monkeypatch.setattr(config, "reload_config", lambda: None)
    monkeypatch.setattr(config, "_load_models_cache_from_disk", lambda: None)
    monkeypatch.setattr(config, "_save_models_cache_to_disk", lambda cache: None)

    # _get_config_path is stubbed to a missing file (mtime 0.0), so keep
    # _cfg_mtime at 0.0 too — otherwise every call would take the
    # config-changed branch and invalidate the cache under test.
    config._cfg_mtime = 0.0
    config._available_models_cache = None
    config._available_models_cache_ts = 0.0
    config._cache_build_in_progress = False
    config._models_build_error = None

    yield config

    # Let any stray worker finish before restoring module state.
    _wait_until(lambda: not config._cache_build_in_progress, timeout=5.0)

    config._available_models_cache = saved["_available_models_cache"]
    config._available_models_cache_ts = saved["_available_models_cache_ts"]
    config._cache_build_in_progress = saved["_cache_build_in_progress"]
    config._models_cache_generation = saved["_models_cache_generation"]
    config._models_build_error = saved["_models_build_error"]
    config._cfg_mtime = saved["_cfg_mtime"]


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ── 1. Concurrent cold calls share exactly one build ────────────────────────

def test_concurrent_cold_build_runs_once(isolated_catalog, monkeypatch):
    """Six concurrent cold callers must trigger ONE build and all get the
    full catalog (the issue #16 log: six GET /api/models finished together
    after the prewarm — they must not have serialized six builds)."""
    config = isolated_catalog
    release = threading.Event()  # main thread releases the build
    build_calls = []

    def fake_build():
        build_calls.append(1)
        assert release.wait(10.0), "harness must release the build"
        return dict(_FULL_CATALOG)

    monkeypatch.setattr(config, "_build_available_models_uncached", fake_build)

    results, errors = [], []

    def call():
        try:
            results.append(config.get_available_models(wait_seconds=5.0))
        except Exception as exc:  # pragma: no cover - failure path asserted below
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(6)]
    for t in threads:
        t.start()
    time.sleep(0.3)  # let every thread reach the gate / CV
    release.set()
    for t in threads:
        t.join(timeout=10.0)

    assert errors == [], f"waiters must not raise: {errors}"
    assert len(build_calls) == 1, "six concurrent cold calls must share one build"
    assert len(results) == 6
    for r in results:
        assert r["groups"] == _FULL_CATALOG["groups"]
        assert "building" not in r, "a completed build must not be flagged building"


# ── 2. Bounded waiter degrades with building:True, then recovers ─────────────

def test_bounded_waiter_degrades_then_recovers(isolated_catalog, monkeypatch):
    config = isolated_catalog
    release = threading.Event()

    def fake_build():
        assert release.wait(10.0)
        return dict(_FULL_CATALOG)

    monkeypatch.setattr(config, "_build_available_models_uncached", fake_build)

    t0 = time.monotonic()
    result = config.get_available_models(wait_seconds=0.2)
    elapsed = time.monotonic() - t0

    assert result.get("building") is True, (
        "a waiter that gives up on an in-flight build must flag the degrade "
        "so the frontend keeps polling"
    )
    assert result.get("config_only") is True
    assert result["groups"] == []
    assert elapsed < 2.0, f"bounded waiter blocked {elapsed:.1f}s (bound was 0.2s)"

    release.set()
    assert _wait_until(lambda: config._available_models_cache is not None), (
        "worker must publish after the build completes"
    )
    result2 = config.get_available_models(wait_seconds=5.0)
    assert result2["groups"] == _FULL_CATALOG["groups"]
    assert "building" not in result2


# ── 3. Stale memory is served (flagged) while a rebuild runs ────────────────

def test_stale_memory_served_with_building_flag(isolated_catalog, monkeypatch):
    config = isolated_catalog
    stale = {
        "active_provider": "deepseek",
        "default_model": "dm-old",
        "groups": [
            {"provider": "DeepSeek", "provider_id": "deepseek",
             "models": [{"id": "m-old", "label": "Old"}]}
        ],
    }
    config._available_models_cache = dict(stale)
    config._available_models_cache_ts = (
        time.monotonic() - (config._AVAILABLE_MODELS_CACHE_TTL + 5.0)
    )

    release = threading.Event()
    fresh = dict(_FULL_CATALOG)

    def fake_build():
        assert release.wait(10.0)
        return dict(fresh)

    monkeypatch.setattr(config, "_build_available_models_uncached", fake_build)

    result = config.get_available_models(wait_seconds=0.2)
    assert result.get("building") is True, (
        "a TTL-expired memory cache must be served (flagged) while the "
        "rebuild runs — better than an empty catalog"
    )
    assert result["groups"] == stale["groups"]

    release.set()
    assert _wait_until(lambda: config._available_models_cache == fresh)


# ── 4. Invalidation mid-build discards the stale publish and retries ────────

def test_invalidation_mid_build_discards_stale_publish(isolated_catalog, monkeypatch):
    config = isolated_catalog
    mid_build = threading.Event()
    release = threading.Event()
    attempts = []

    def fake_build():
        n = len(attempts) + 1
        attempts.append(n)
        if n == 1:
            mid_build.set()
            assert release.wait(10.0)
            return {"active_provider": "stale", "default_model": "stale",
                    "groups": [], "attempt": 1}
        return {"active_provider": "fresh", "default_model": "fresh",
                "groups": [{"provider": "Fresh", "provider_id": "fresh",
                            "models": [{"id": "m", "label": "M"}]}],
                "attempt": 2}

    monkeypatch.setattr(config, "_build_available_models_uncached", fake_build)

    results = []

    def call():
        results.append(config.get_available_models(wait_seconds=5.0))

    t = threading.Thread(target=call)
    t.start()
    assert mid_build.wait(5.0)

    config.invalidate_models_cache()  # bumps the generation mid-build
    release.set()
    t.join(timeout=10.0)

    assert attempts == [1, 2], "a discarded publish must trigger one retry"
    assert results and results[0].get("attempt") == 2, (
        "the stale mid-build result must not be served over the invalidation"
    )
    assert config._available_models_cache.get("attempt") == 2


# ── 5. Build failure: flag cleared, raised to the spawner, later retry ──────

def test_build_failure_propagates_to_spawner_and_clears_flag(isolated_catalog, monkeypatch):
    config = isolated_catalog
    calls = []

    def fake_build():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("catalog exploded")
        return dict(_FULL_CATALOG)

    monkeypatch.setattr(config, "_build_available_models_uncached", fake_build)

    with pytest.raises(RuntimeError, match="catalog exploded"):
        config.get_available_models(wait_seconds=None)

    assert config._cache_build_in_progress is False, (
        "a failed build must clear the single-flight flag"
    )

    # A subsequent call retries and succeeds.
    result = config.get_available_models(wait_seconds=5.0)
    assert result["groups"] == _FULL_CATALOG["groups"]
    assert "building" not in result
    assert len(calls) == 2


# ── 6. Bounded waiters degrade (not raise) when someone else's build fails ──

def test_bounded_waiter_degrades_when_build_fails(isolated_catalog, monkeypatch):
    config = isolated_catalog
    started = threading.Event()
    release = threading.Event()

    def fake_build():
        started.set()
        assert release.wait(10.0)
        raise RuntimeError("late failure")

    monkeypatch.setattr(config, "_build_available_models_uncached", fake_build)

    spawner_errors = []

    def spawner():
        try:
            config.get_available_models(wait_seconds=None)
        except Exception as exc:  # pragma: no cover - asserted below
            spawner_errors.append(exc)

    t = threading.Thread(target=spawner)
    t.start()
    assert started.wait(5.0)

    # Bounded waiter must degrade, not inherit the builder's exception.
    result = config.get_available_models(wait_seconds=0.2)
    assert result.get("building") is True
    assert result.get("config_only") is True

    release.set()
    t.join(timeout=10.0)
    assert len(spawner_errors) == 1
    assert isinstance(spawner_errors[0], RuntimeError)


# ── 7. normalize_model_prefix accepts a pre-fetched catalog ─────────────────

def test_normalize_model_prefix_uses_prefilled_catalog(monkeypatch):
    import app.api.config as config

    def _boom(*args, **kwargs):
        raise AssertionError("peek must not be consulted when catalog= is given")

    monkeypatch.setattr(config, "peek_available_models", _boom)
    catalog = {
        "active_provider": "deepseek",
        "default_model": "deepseek-chat",
        "groups": [
            {
                "provider": "DeepSeek",
                "provider_id": "deepseek",
                "models": [{"id": "@deepseek:deepseek-v4-pro", "label": "DeepSeek V4 Pro"}],
            },
        ],
    }

    assert (
        config.normalize_model_prefix("deepseek-v4-pro", catalog=catalog)
        == "@deepseek:deepseek-v4-pro"
    )
    # Prefixed / slashed inputs short-circuit without touching any catalog.
    assert config.normalize_model_prefix("@deepseek:x") == "@deepseek:x"
    assert config.normalize_model_prefix("openrouter/x") == "openrouter/x"


def test_normalize_model_prefix_falls_back_to_peek(monkeypatch):
    import app.api.config as config

    monkeypatch.setattr(
        config, "peek_available_models",
        lambda *a, **k: {
            "active_provider": "deepseek",
            "default_model": "dm",
            "groups": [
                {"provider": "DeepSeek", "provider_id": "deepseek",
                 "models": [{"id": "@deepseek:m", "label": "M"}]},
            ],
        },
    )
    assert config.normalize_model_prefix("m") == "@deepseek:m"


# ── 8. apply_aws_metadata_preference (IMDS probe) ───────────────────────────

_IMDS_ENV_VARS = (
    "AWS_EC2_METADATA_DISABLED",
    "AWS_MAX_ATTEMPTS",
    "AWS_METADATA_SERVICE_NUM_ATTEMPTS",
)


def test_imds_probe_failure_disables_metadata(monkeypatch):
    import app.api.config as config

    monkeypatch.setattr(config, "_aws_metadata_probe_done", False)
    monkeypatch.delenv("HERMES_WEBUI_AWS_IMDS_PROBE", raising=False)
    for var in _IMDS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    def _refuse(*args, **kwargs):
        raise OSError("imds unreachable")

    monkeypatch.setattr(socket, "create_connection", _refuse)

    assert config.apply_aws_metadata_preference() is False
    assert os.environ["AWS_EC2_METADATA_DISABLED"] == "true"
    assert os.environ["AWS_MAX_ATTEMPTS"] == "1"
    assert os.environ["AWS_METADATA_SERVICE_NUM_ATTEMPTS"] == "1"


def test_imds_probe_success_leaves_env_untouched(monkeypatch):
    import app.api.config as config

    monkeypatch.setattr(config, "_aws_metadata_probe_done", False)
    monkeypatch.delenv("HERMES_WEBUI_AWS_IMDS_PROBE", raising=False)
    for var in _IMDS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    before = {var: os.environ.get(var) for var in _IMDS_ENV_VARS}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: _Conn())

    assert config.apply_aws_metadata_preference() is True
    after = {var: os.environ.get(var) for var in _IMDS_ENV_VARS}
    assert after == before, f"reachable IMDS must not change env: {before} -> {after}"


def test_imds_probe_disabled_via_env(monkeypatch):
    import app.api.config as config

    monkeypatch.setattr(config, "_aws_metadata_probe_done", False)
    monkeypatch.setenv("HERMES_WEBUI_AWS_IMDS_PROBE", "0")

    def _forbid(*args, **kwargs):
        raise AssertionError("probe must not run when disabled")

    monkeypatch.setattr(socket, "create_connection", _forbid)

    assert config.apply_aws_metadata_preference() is False


def test_imds_probe_respects_operator_optout(monkeypatch):
    import app.api.config as config

    monkeypatch.setattr(config, "_aws_metadata_probe_done", False)
    monkeypatch.delenv("HERMES_WEBUI_AWS_IMDS_PROBE", raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    def _forbid(*args, **kwargs):
        raise AssertionError("probe must not run when operator opted out")

    monkeypatch.setattr(socket, "create_connection", _forbid)

    assert config.apply_aws_metadata_preference() is False
    assert os.environ["AWS_EC2_METADATA_DISABLED"] == "true"
