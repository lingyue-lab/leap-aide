"""Tests for the unified ``@provider:model`` identity scheme.

Every model in the picker is identified as ``@provider:model``:

* the provider half is split at the FIRST colon, so gateway namespaces
  (``@openrouter:anthropic/claude-opus-4.6``) and Ollama tags
  (``@ollama1:qwen3-vl:235b``) survive intact;
* named ``custom_providers`` entries use their name slug as the provider id
  (``@ollama1:...``); the retired ``@custom:<name>:model`` form is rejected,
  never canonicalized;
* ``provider/model`` is a model namespace, not a provider identity.
"""
import pytest

import app.api.config as config
import app.api.routes as routes


@pytest.fixture
def custom_providers():
    """Install two named custom providers in config.cfg and restore after."""
    old_cfg = dict(config.cfg)
    config.cfg["custom_providers"] = [
        {
            "name": "Ollama1",
            "base_url": "http://ollama1.local:11434/v1",
            "model": "qwen3-vl:235b",
        },
        {
            "name": "My Key",
            "base_url": "http://my-key.local:8000/v1",
            "model": "private-llm",
        },
    ]
    try:
        yield config.cfg["custom_providers"]
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)


def _catalog(groups, active_provider="anthropic"):
    return {
        "active_provider": active_provider,
        "default_model": "",
        "groups": groups,
    }


# ── Catalog ids ─────────────────────────────────────────────────────────────


def test_apply_provider_prefix_keeps_gateway_namespace():
    """Gateway ids carry their upstream namespace in the model half."""
    raw = [{"id": "anthropic/claude-opus-4.6", "label": "Claude Opus 4.6"}]
    result = config._apply_provider_prefix(raw, "openrouter")
    assert [m["id"] for m in result] == [
        "@openrouter:anthropic/claude-opus-4.6"
    ]


def test_apply_provider_prefix_keeps_ollama_tag():
    raw = [{"id": "qwen3-vl:235b", "label": "Qwen3 VL 235B"}]
    result = config._apply_provider_prefix(raw, "ollama1")
    assert [m["id"] for m in result] == ["@ollama1:qwen3-vl:235b"]


def test_apply_provider_prefix_leaves_existing_identity():
    raw = [{"id": "@deepseek:deepseek-v4-pro", "label": "DeepSeek V4 Pro"}]
    result = config._apply_provider_prefix(raw, "anthropic")
    assert [m["id"] for m in result] == ["@deepseek:deepseek-v4-pro"]


# ── Custom provider slug / lookup ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Ollama1", "ollama1"),
        ("My Key", "my-key"),
        ("custom:thing", "custom-thing"),
        ("a/b@c", "a-b-c"),
        ("  pad  ", "pad"),
        ("", ""),
    ],
)
def test_custom_provider_slug(name, slug):
    assert config.custom_provider_slug(name) == slug


def test_named_custom_provider_accepts_slug_and_name(custom_providers):
    assert config.named_custom_provider("ollama1")["name"] == "Ollama1"
    assert config.named_custom_provider("Ollama1")["name"] == "Ollama1"
    assert config.named_custom_provider("my key")["name"] == "My Key"
    # The retired custom:<slug> provider id is not accepted.
    assert config.named_custom_provider("custom:ollama1") is None
    assert config.named_custom_provider("missing") is None


# ── resolve_model_provider ─────────────────────────────────────────────────


def test_named_custom_slug_resolves_with_base_url(custom_providers):
    model, provider, base_url = config.resolve_model_provider(
        "@ollama1:qwen3-vl:235b"
    )
    assert (model, provider, base_url) == (
        "qwen3-vl:235b",
        "ollama1",
        "http://ollama1.local:11434/v1",
    )


def test_named_custom_provider_name_with_space_resolves(custom_providers):
    model, provider, base_url = config.resolve_model_provider(
        "@My Key:private-llm"
    )
    assert (model, provider, base_url) == (
        "private-llm",
        "my-key",
        "http://my-key.local:8000/v1",
    )


def test_legacy_custom_identity_is_not_resolved(custom_providers):
    """The retired @custom:<name>:model form is never canonicalized."""
    model, provider, base_url = config.resolve_model_provider(
        "@custom:Ollama1:qwen3-vl:235b"
    )
    assert (model, provider, base_url) == (
        "Ollama1:qwen3-vl:235b",
        "custom",
        None,
    )


def test_legacy_custom_identity_is_rejected(custom_providers):
    """Named custom identities must use the slug; the old form is invalid."""
    from app.api.models import Session

    assert config.is_legacy_custom_identity(
        "custom:Ollama1:qwen3-vl:235b"
    ) is True
    assert config.is_legacy_custom_identity("custom:qwen3-vl:235b") is False
    assert Session._is_valid_model_identity(
        "@custom:Ollama1:qwen3-vl:235b"
    ) is False


def test_unnamed_custom_identity_keeps_colon_model(custom_providers):
    """@custom:model:tag is the unnamed-custom pseudo-provider, not legacy."""
    model, provider, base_url = config.resolve_model_provider(
        "@custom:qwen3-vl:235b"
    )
    assert (model, provider, base_url) == ("qwen3-vl:235b", "custom", None)


def test_gateway_identity_keeps_namespaced_model(custom_providers):
    model, provider, base_url = config.resolve_model_provider(
        "@openrouter:anthropic/claude-opus-4.6"
    )
    assert (model, provider, base_url) == (
        "anthropic/claude-opus-4.6",
        "openrouter",
        None,
    )


def test_builtin_identity_resolves_to_bare_model(custom_providers):
    model, provider, base_url = config.resolve_model_provider(
        "@deepseek:deepseek-v4-pro"
    )
    assert (model, provider, base_url) == ("deepseek-v4-pro", "deepseek", None)


# ── normalize_model_prefix ─────────────────────────────────────────────────


def test_normalize_does_not_rewrite_legacy_custom_identity(custom_providers):
    """The retired form is rejected by validation, never canonicalized."""
    assert config.normalize_model_prefix(
        "@custom:My Key:private-llm"
    ) == "@custom:My Key:private-llm"
    assert config.is_legacy_custom_identity("custom:My Key:private-llm") is True


def test_normalize_keeps_unnamed_custom_identity_with_colon_model(custom_providers):
    from app.api.models import Session

    assert config.normalize_model_prefix(
        "@custom:qwen3-vl:235b"
    ) == "@custom:qwen3-vl:235b"
    assert Session._is_valid_model_identity("@custom:qwen3-vl:235b") is True


def test_normalize_namespaced_value_to_direct_provider_identity():
    catalog = _catalog(
        [
            {
                "provider": "Anthropic",
                "provider_id": "anthropic",
                "models": [
                    {"id": "@anthropic:claude-opus-4.6", "label": "Claude Opus 4.6"}
                ],
            },
        ]
    )
    assert config.normalize_model_prefix(
        "anthropic/claude-opus-4.6", catalog=catalog
    ) == "@anthropic:claude-opus-4.6"


def test_normalize_namespaced_value_prefers_active_gateway():
    catalog = _catalog(
        [
            {
                "provider": "OpenRouter",
                "provider_id": "openrouter",
                "models": [
                    {
                        "id": "@openrouter:anthropic/claude-opus-4.6",
                        "label": "Claude Opus 4.6",
                    }
                ],
            },
            {
                "provider": "Anthropic",
                "provider_id": "anthropic",
                "models": [
                    {"id": "@anthropic:claude-opus-4.6", "label": "Claude Opus 4.6"}
                ],
            },
        ],
        active_provider="openrouter",
    )
    assert config.normalize_model_prefix(
        "anthropic/claude-opus-4.6", catalog=catalog
    ) == "@openrouter:anthropic/claude-opus-4.6"


def test_normalize_namespaced_prefers_active_direct_provider():
    """provider: openai + model: openai/gpt-5.4 means OpenAI's own model."""
    catalog = _catalog(
        [
            {
                "provider": "OpenRouter",
                "provider_id": "openrouter",
                "models": [
                    {"id": "@openrouter:openai/gpt-5.4", "label": "GPT-5.4"}
                ],
            },
            {
                "provider": "OpenAI",
                "provider_id": "openai",
                "models": [{"id": "@openai:gpt-5.4", "label": "GPT-5.4"}],
            },
        ],
        active_provider="openai",
    )
    assert config.normalize_model_prefix(
        "openai/gpt-5.4", catalog=catalog
    ) == "@openai:gpt-5.4"


def test_normalize_namespaced_prefers_gateway_exact_match():
    """A gateway id literally contains the persisted namespace; prefer it."""
    catalog = _catalog(
        [
            {
                "provider": "OpenRouter",
                "provider_id": "openrouter",
                "models": [
                    {
                        "id": "@openrouter:anthropic/claude-opus-4.6",
                        "label": "Claude Opus 4.6",
                    }
                ],
            },
            {
                "provider": "Anthropic",
                "provider_id": "anthropic",
                "models": [
                    {"id": "@anthropic:claude-opus-4.6", "label": "Claude Opus 4.6"}
                ],
            },
        ],
        active_provider="deepseek",
    )
    assert config.normalize_model_prefix(
        "anthropic/claude-opus-4.6", catalog=catalog
    ) == "@openrouter:anthropic/claude-opus-4.6"


def test_normalize_namespaced_alias_namespace():
    """The namespace may be an alias ('google' -> gemini)."""
    catalog = _catalog(
        [
            {
                "provider": "Gemini",
                "provider_id": "gemini",
                "models": [
                    {"id": "@gemini:gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro"}
                ],
            },
        ],
        active_provider="",
    )
    assert config.normalize_model_prefix(
        "google/gemini-3.1-pro-preview", catalog=catalog
    ) == "@gemini:gemini-3.1-pro-preview"


def test_normalize_bare_value_prefers_active_provider():
    catalog = _catalog(
        [
            {
                "provider": "DeepSeek",
                "provider_id": "deepseek",
                "models": [{"id": "@deepseek:gpt-5.4", "label": "GPT-5.4"}],
            },
            {
                "provider": "OpenAI",
                "provider_id": "openai",
                "models": [{"id": "@openai:gpt-5.4", "label": "GPT-5.4"}],
            },
        ],
        active_provider="openai",
    )
    assert config.normalize_model_prefix(
        "gpt-5.4", catalog=catalog
    ) == "@openai:gpt-5.4"


def test_normalize_ignores_picker_only_default_group():
    """The 'Default' group is not a routable provider (no @default: ids)."""
    catalog = _catalog(
        [
            {
                "provider": "Default",
                "provider_id": "default",
                "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
            },
        ],
        active_provider="",
    )
    assert config.normalize_model_prefix("gpt-5.5", catalog=catalog) == "gpt-5.5"


def test_normalize_ambiguous_value_is_unchanged():
    catalog = _catalog(
        [
            {
                "provider": "DeepSeek",
                "provider_id": "deepseek",
                "models": [{"id": "@deepseek:gpt-5.4", "label": "GPT-5.4"}],
            },
            {
                "provider": "OpenAI",
                "provider_id": "openai",
                "models": [{"id": "@openai:gpt-5.4", "label": "GPT-5.4"}],
            },
        ],
        active_provider="anthropic",
    )
    assert config.normalize_model_prefix("gpt-5.4", catalog=catalog) == "gpt-5.4"


# ── Cron model identity ────────────────────────────────────────────────────


def test_cron_split_routes_custom_identity(custom_providers):
    model, provider, base_url = routes._split_cron_model_identity(
        "@ollama1:qwen3-vl:235b"
    )
    assert (model, provider, base_url) == (
        "qwen3-vl:235b",
        "ollama1",
        "http://ollama1.local:11434/v1",
    )


def test_cron_split_rejects_legacy_custom_identity(custom_providers):
    with pytest.raises(ValueError, match="Legacy @custom"):
        routes._split_cron_model_identity("@custom:Ollama1:qwen3-vl:235b")


def test_cron_split_leaves_bare_model_unchanged(custom_providers):
    assert routes._split_cron_model_identity("plain-model") == (
        "plain-model",
        None,
        None,
    )
