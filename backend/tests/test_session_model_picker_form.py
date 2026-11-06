"""The stored session model must use the same form as the picker entry.

The model picker compares ids literally, so a session model stored in a
different form than the catalog's entry is not marked as selected — the user
opens the picker and sees nothing checked.  Since issue #55 every catalog id
is a routable ``@provider:model`` identity, and the stored value follows suit:
bare and namespaced values from config.yaml or CLI sessions are mapped onto
the identity of the provider that serves them.
"""
from __future__ import annotations

import pytest

import app.api.config as config
import app.api.models as models


CATALOG = {
    "active_provider": "deepseek",
    "default_model": "deepseek-flash",
    "groups": [
        {
            "provider": "DeepSeek",
            "provider_id": "deepseek",
            "models": [
                {"id": "@deepseek:deepseek-flash", "label": "DeepSeek Flash"},
                {"id": "@deepseek:deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
            ],
        },
        {
            "provider": "Xiaomi",
            "provider_id": "xiaomi",
            "models": [
                {"id": "@xiaomi:mimo-v2.6-flash", "label": "MiMo V2.6 Flash"},
            ],
        },
    ],
}


# ── normalize_model_prefix ───────────────────────────────────────────────────


def test_bare_model_maps_to_its_provider_identity(monkeypatch):
    """A bare value is attributed to the group that lists it."""
    monkeypatch.setattr(config, "peek_available_models", lambda: CATALOG)
    assert (
        config.normalize_model_prefix("deepseek-flash", catalog=CATALOG)
        == "@deepseek:deepseek-flash"
    )
    assert (
        config.normalize_model_prefix("deepseek-v4-pro", catalog=CATALOG)
        == "@deepseek:deepseek-v4-pro"
    )


def test_other_provider_model_keeps_the_provider_hint(monkeypatch):
    """A model of another provider keeps its ``@provider:`` hint."""
    monkeypatch.setattr(config, "peek_available_models", lambda: CATALOG)
    assert (
        config.normalize_model_prefix("mimo-v2.6-flash", catalog=CATALOG)
        == "@xiaomi:mimo-v2.6-flash"
    )


def test_namespaced_model_maps_to_its_provider(monkeypatch):
    """``provider/model`` resolves through the namespace group."""
    monkeypatch.setattr(config, "peek_available_models", lambda: CATALOG)
    assert (
        config.normalize_model_prefix("xiaomi/mimo-v2.6-flash", catalog=CATALOG)
        == "@xiaomi:mimo-v2.6-flash"
    )


def test_unknown_model_is_left_alone(monkeypatch):
    monkeypatch.setattr(config, "peek_available_models", lambda: CATALOG)
    assert (
        config.normalize_model_prefix("not-in-catalog", catalog=CATALOG)
        == "not-in-catalog"
    )


def test_prefixed_model_short_circuits(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("peek must not be consulted")

    monkeypatch.setattr(config, "peek_available_models", _boom)
    assert config.normalize_model_prefix("@deepseek:x") == "@deepseek:x"


# ── new_session() ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    models.SESSIONS.clear()
    yield
    models.SESSIONS.clear()


def test_new_session_stores_the_picker_form(monkeypatch):
    """The persisted model must be a catalog id, so the picker can mark it."""
    monkeypatch.setattr(config, "peek_available_models", lambda: CATALOG)
    monkeypatch.setattr(
        config, "get_default_model_for_new_session", lambda: "deepseek-flash"
    )

    session = models.new_session()

    assert session.model == "@deepseek:deepseek-flash"
    assert session.model in {
        m["id"] for g in CATALOG["groups"] for m in g["models"]
    }
