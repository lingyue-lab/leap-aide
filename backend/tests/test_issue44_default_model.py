"""Tests for issue #44: default model in Settings / new-session resolution.

Covers:
  - Model-ID normalization used for cross-format availability matching
  - Availability computation and the ``default_model_available`` catalog field
  - Injection of an unavailable configured default (marked, never rewritten)
  - ``get_default_model_for_new_session()`` precedence
  - ``new_session()`` using the configured default
"""
from __future__ import annotations

import pytest

import app.api.config as config
import app.api.models as models
from app.api.models import SESSIONS, new_session


# ── Model-ID matching ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model_id, expected",
    [
        ("anthropic/claude-opus-4.6", "claude.opus.4.6"),
        ("@nous:anthropic/claude-opus-4.6", "claude.opus.4.6"),
        ("claude-opus-4-6", "claude.opus.4.6"),
        ("CLAUDE-OPUS-4.6", "claude.opus.4.6"),
        ("minimax/minimax-m2.5:free", "minimax.m2.5:free"),
        ("", ""),
    ],
)
def test_normalize_model_id_for_match(model_id, expected):
    assert config._normalize_model_id_for_match(model_id) == expected


def test_compute_default_model_available_unknown_when_unset():
    assert config._compute_default_model_available("", [{"models": [{"id": "x"}]}]) is None


def test_compute_default_model_available_matches_across_prefixes():
    groups = [{"models": [{"id": "@nous:anthropic/claude-opus-4.6"}]}]
    assert config._compute_default_model_available(
        "anthropic/claude-opus-4.6", groups
    ) is True


def test_compute_default_model_available_false_when_absent():
    groups = [{"models": [{"id": "openai/gpt-5.4"}]}]
    assert config._compute_default_model_available(
        "anthropic/claude-opus-4.6", groups
    ) is False
    assert config._compute_default_model_available("anything", []) is False


# ── Catalog build: availability + injected entry ─────────────────────────────


def _build_with_default(monkeypatch, default_model: str) -> dict:
    """Build the catalog with one detected provider and a forced default."""
    monkeypatch.setattr(
        config,
        "_config_only_catalog_fields",
        lambda: (default_model, "anthropic", {}),
    )
    monkeypatch.setattr(
        config, "_resolve_detected_provider_models", lambda *a, **k: ({}, set())
    )
    import app.api.providers as providers

    monkeypatch.setattr(providers, "_provider_has_key", lambda pid: True)
    return config._build_available_models_uncached()


def test_build_marks_removed_default_unavailable(monkeypatch):
    result = _build_with_default(monkeypatch, "ghost-model-9")
    assert result["default_model_available"] is False
    injected = [
        m
        for g in result["groups"]
        for m in g["models"]
        if m["id"] == "ghost-model-9"
    ]
    assert injected, "configured default must stay visible in the picker"
    assert injected[0]["unavailable"] is True


def test_build_available_default_is_not_flagged(monkeypatch):
    baseline = config.model_catalog.provider_baseline_models("anthropic")
    assert baseline, "test assumes a bundled anthropic baseline"
    known_model = baseline[0]["id"]
    result = _build_with_default(monkeypatch, known_model)
    assert result["default_model_available"] is True
    assert not any(
        m.get("unavailable")
        for g in result["groups"]
        for m in g["models"]
        if m["id"] == known_model
    )


def test_build_without_providers_reports_unknown(monkeypatch):
    monkeypatch.setattr(
        config, "_config_only_catalog_fields", lambda: ("", None, {})
    )
    monkeypatch.setattr(
        config, "_resolve_detected_provider_models", lambda *a, **k: ({}, set())
    )
    result = config._build_available_models_uncached()
    assert result["default_model_available"] is None


# ── New-session resolution ───────────────────────────────────────────────────


def test_new_session_model_prefers_configured_default(monkeypatch):
    monkeypatch.setattr(config, "peek_available_models", lambda: {"default_model": ""})
    monkeypatch.setattr(
        config,
        "get_effective_default_model",
        lambda: "anthropic/claude-opus-4.6",
    )
    monkeypatch.setattr(
        config,
        "get_first_available_model",
        lambda: pytest.fail("first-available must not be consulted"),
    )
    assert (
        config.get_default_model_for_new_session()
        == "anthropic/claude-opus-4.6"
    )


def test_new_session_model_falls_back_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "peek_available_models", lambda: {"default_model": ""})
    monkeypatch.setattr(config, "get_effective_default_model", lambda: "")
    monkeypatch.setattr(
        config, "get_first_available_model", lambda: "deepseek/deepseek-v4-pro"
    )
    assert (
        config.get_default_model_for_new_session()
        == "deepseek/deepseek-v4-pro"
    )


def test_new_session_model_survives_peek_failure(monkeypatch):
    def _boom():
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(config, "peek_available_models", _boom)
    monkeypatch.setattr(config, "get_effective_default_model", lambda: "zai/glm-5.2")
    assert config.get_default_model_for_new_session() == "zai/glm-5.2"


# ── new_session() integration ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Redirect SESSION_DIR and SESSION_INDEX_FILE to a fresh tmp dir."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    SESSIONS.clear()
    yield
    SESSIONS.clear()


def test_new_session_uses_configured_default(monkeypatch):
    monkeypatch.setattr(
        models,
        "get_default_model_for_new_session",
        lambda: "@deepseek:deepseek-v4-pro",
    )
    session = new_session()
    assert session.model == "@deepseek:deepseek-v4-pro"


def test_new_session_explicit_model_wins(monkeypatch):
    monkeypatch.setattr(
        models,
        "get_default_model_for_new_session",
        lambda: "@deepseek:deepseek-v4-pro",
    )
    session = new_session(model="openai/gpt-5.4")
    assert session.model == "openai/gpt-5.4"
