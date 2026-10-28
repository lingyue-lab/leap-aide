"""Tests for the models-cache disk snapshot envelope (issue #10).

The old snapshot only persisted ``{"groups": [...]}``, so the first
``/api/models`` response after a restart was missing ``active_provider``
and ``default_model``.  The envelope now stores the full public cache
shape plus a per-provider model map used as the last-success fallback
when a live refresh fails after a restart.

Validates:
  - Save → load round-trip preserves active_provider / default_model /
    groups and stashes provider_models for the prior fallback
  - Legacy ``{groups-only}`` files still load (synthesized empty fields)
  - Invalid / non-dict / groups-less files are rejected (None)
"""
from __future__ import annotations

import json

import pytest

import app.api.config as config


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    """Point the disk cache at a temp file and reset related globals."""
    path = tmp_path / "models_cache.json"
    monkeypatch.setattr(config, "_models_cache_path", path)
    monkeypatch.setattr(config, "_disk_provider_models", {})
    yield path
    # Ensure the module global is restored to a dict (fixture teardown order
    # restores monkeypatch after yield, but be explicit for clarity).
    config._disk_provider_models = {}


_SAMPLE_CACHE = {
    "active_provider": "anthropic",
    "default_model": "claude-sonnet-4.6",
    "groups": [
        {
            "provider": "Anthropic",
            "provider_id": "anthropic",
            "models": [
                {"id": "@anthropic:claude-sonnet-4.6", "label": "Claude Sonnet 4.6"},
            ],
        },
        {
            "provider": "OpenAI",
            "provider_id": "openai",
            "models": [
                {"id": "@openai:gpt-5.4", "label": "GPT-5.4"},
            ],
        },
    ],
}


class TestEnvelopeRoundTrip:

    def test_save_load_round_trip(self, cache_path):
        """Save the full cache; load must return all three public fields
        and populate _disk_provider_models from the envelope."""
        config._save_models_cache_to_disk(_SAMPLE_CACHE)
        assert cache_path.exists()

        config._disk_provider_models = {}
        loaded = config._load_models_cache_from_disk()

        assert loaded is not None
        assert loaded["active_provider"] == "anthropic"
        assert loaded["default_model"] == "claude-sonnet-4.6"
        assert loaded["groups"] == _SAMPLE_CACHE["groups"]

        # Per-provider map is stashed for the last-success fallback.
        assert config._disk_provider_models["anthropic"] == [
            {"id": "@anthropic:claude-sonnet-4.6", "label": "Claude Sonnet 4.6"},
        ]
        assert config._disk_provider_models["openai"] == [
            {"id": "@openai:gpt-5.4", "label": "GPT-5.4"},
        ]

    def test_saved_payload_has_envelope_fields(self, cache_path):
        """The on-disk file itself must carry the envelope keys."""
        config._save_models_cache_to_disk(_SAMPLE_CACHE)
        with open(cache_path, encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["schema_version"] == 1
        assert payload["active_provider"] == "anthropic"
        assert payload["default_model"] == "claude-sonnet-4.6"
        assert payload["groups"] == _SAMPLE_CACHE["groups"]
        assert "provider_models" in payload
        assert "fetched_at" in payload

    def test_groups_without_provider_id_skipped_in_map(self, cache_path):
        """Groups lacking provider_id must not crash the envelope build."""
        cache = {
            "active_provider": "custom",
            "default_model": "",
            "groups": [
                {"provider": "Custom", "models": [{"id": "m1", "label": "M1"}]},
            ],
        }
        config._save_models_cache_to_disk(cache)
        config._disk_provider_models = {}
        loaded = config._load_models_cache_from_disk()
        assert loaded is not None
        assert loaded["groups"] == cache["groups"]
        assert config._disk_provider_models == {}


class TestLegacySnapshotCompat:

    def test_groups_only_file_still_loads(self, cache_path):
        """Old-format ``{groups-only}`` snapshots must keep loading with
        synthesized empty active_provider / default_model."""
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"groups": _SAMPLE_CACHE["groups"]}, f)

        config._disk_provider_models = {}
        loaded = config._load_models_cache_from_disk()

        assert loaded is not None
        assert loaded["active_provider"] is None
        assert loaded["default_model"] == ""
        assert loaded["groups"] == _SAMPLE_CACHE["groups"]
        # No envelope map in legacy files — stays empty.
        assert config._disk_provider_models == {}

    def test_missing_file_returns_none(self, cache_path, tmp_path):
        assert config._load_models_cache_from_disk() is None

    def test_non_dict_file_returns_none(self, cache_path):
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(["not", "a", "dict"], f)
        assert config._load_models_cache_from_disk() is None

    def test_dict_without_groups_returns_none(self, cache_path):
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"active_provider": "anthropic"}, f)
        assert config._load_models_cache_from_disk() is None
