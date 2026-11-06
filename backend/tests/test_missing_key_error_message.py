"""Missing-API-key stream errors are translated for WebUI users.

The Hermes SDK rejects agent construction with a config.yaml / environment
variable level message.  LeapAide classifies it in both stream error paths
and replaces the raw text with a short, Settings-oriented message, keeping
the technical detail in the server log only.
"""
from __future__ import annotations

import inspect

import app.api.streaming as streaming

_SDK_TEXT = (
    "Provider 'tencent-tokenhub' is set in config.yaml but no API key "
    "was found. Set the TOKENHUB_API_KEY environment variable, or switch "
    "to a different provider with `hermes model`."
)


def test_classifies_sdk_missing_key_error():
    assert streaming._is_missing_key_error(_SDK_TEXT)


def test_classifies_without_provider_prefix():
    assert streaming._is_missing_key_error("No API key was found for the provider.")


def test_does_not_classify_other_auth_errors():
    assert not streaming._is_missing_key_error("401 invalid api key")
    assert not streaming._is_missing_key_error("rate limit reached")
    assert not streaming._is_missing_key_error("")


def test_user_message_does_not_leak_config_details():
    message = streaming._DEFAULT_MODEL_INVALID_MESSAGE
    assert message.startswith("Default model is not valid")
    assert "config.yaml" not in message
    assert "environment variable" not in message
    assert "hermes model" not in message


def test_wording_follows_the_conversation_not_the_model_value():
    """A new conversation talks about the default, an old one about its model.

    An existing session runs the model the user picked for it — calling that
    "the default model" is misleading even when it happens to be the same
    model the default points at (issue #44 follow-up).
    """
    new_label, new_message, new_hint = streaming._missing_key_wording(True)
    old_label, old_message, old_hint = streaming._missing_key_wording(False)

    assert new_label == "Default model not available"
    assert new_message.startswith("Default model is not valid")
    assert "default model's provider" in new_hint

    assert old_label == "Current model not available"
    assert old_message.startswith("Current model is not valid")
    assert "this model's provider" in old_hint
    # Neither wording may leak the SDK's technical detail.
    for text in (new_message, old_message, new_hint, old_hint):
        assert "config.yaml" not in text
        assert "environment variable" not in text
        assert "hermes model" not in text


def test_both_stream_error_paths_classify_missing_key():
    """Silent-failure and exception paths must both map the error."""
    src = inspect.getsource(streaming)
    assert src.count("_is_missing_key_error(") == 3, (
        "expected the helper definition plus calls in the silent-failure "
        "and exception handlers"
    )
    assert src.count("_missing_key_wording(") == 3, (
        "expected the helper definition plus calls in the silent-failure "
        "and exception handlers"
    )
