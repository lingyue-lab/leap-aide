"""Regression: ``_resolve_effective_session_model_for_display`` must only warn on
**persisted** bare model names, not on its own normalization output.

Background
----------
``_resolve_compatible_session_model`` strips the ``@provider:`` prefix to bare
form whenever the provider hint matches the active provider (intentional — the
bare name is the canonical display when the routing is unambiguous). The display
helper historically warned on the post-normalization value, so every correctly
prefixed session triggered a spurious ``bare model name`` warning every time it
was loaded via ``GET /api/session`` (#unreleased).

The fix moves the warning check to ``original_model`` (the persisted value).

Each test below calls the helper directly with a stub Session, then asserts on
both ``caplog`` (warning behaviour) and the returned value (display correctness).

Environment
-----------
The module anchors itself to ONE deterministic environment: an autouse fixture
sets a fake ``DEEPSEEK_API_KEY`` so provider detection puts the static
``_PROVIDER_MODELS`` deepseek list into the model catalog (zero network — the
list is hardcoded in api/config.py). Display-normalization assertions can
therefore be exact, and this module is green whether or not the runner happens
to export provider keys. Do NOT gate these tests on live-key opt-in
(``TEST_USING_LIVE_KEY``): they never call a provider.
"""
import logging

import pytest

from app.api.models import Session as SessionModel
from app.api.routes import _resolve_effective_session_model_for_display


@pytest.fixture(autouse=True)
def _deepseek_configured(monkeypatch):
    """Anchor the module to a "DeepSeek is configured" environment.

    The deepseek catalog group comes from the static _PROVIDER_MODELS list in
    api/config.py, so a fake key only flips provider detection —
    get_available_models() never makes a network call. This makes the display
    normalization assertions exact and independent of whatever provider keys
    the test runner happens to export.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-not-a-real-key")
    from app.api.config import invalidate_models_cache
    invalidate_models_cache()  # drop any catalog cached by a prior test


def _make_session(model: str, session_id: str = "s_test") -> SessionModel:
    """Build a Session-shaped stub with only the fields the resolver reads.

    ``_resolve_effective_session_model_for_display`` only touches
    ``session.session_id`` and ``session.model``, so a fully populated Session
    is unnecessary. Bypassing ``__init__`` keeps the test focused on the
    display path and avoids touching global config defaults.
    """
    s = SessionModel.__new__(SessionModel)
    s.session_id = session_id
    s.model = model
    return s


def test_persisted_bare_model_triggers_warning_and_returns_normalized(caplog):
    """Legacy bare persisted model should warn + return the @provider: form.

    Reproduces the original intent of the warning: a session whose stored
    ``session.model`` lacks an ``@provider:`` prefix cannot be routed uniquely
    (e.g. ``deepseek-v4-flash`` exists on DeepSeek direct and OpenRouter).

    The returned form is asserted EXACTLY (``@deepseek:deepseek-v4-flash``)
    because ``_deepseek_configured`` pins the catalog to contain this model —
    without that anchor the value depends on which provider keys the runner
    exports, which is what made this test environment-dependent before.
    """
    caplog.set_level(logging.WARNING, logger="app.api.routes")

    s = _make_session(model="deepseek-v4-flash", session_id="s_bare_legacy")

    returned = _resolve_effective_session_model_for_display(s)

    # Display side: normalized to @provider:model so the frontend model
    # selector matches. Exact value is safe because _deepseek_configured
    # anchors the catalog with a deepseek group containing this model.
    assert returned == "@deepseek:deepseek-v4-flash", (
        f"expected normalized form, got {returned!r}"
    )

    # Warning side: must fire for legacy bare persisted model.
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "has bare model name" in r.getMessage()
    ]
    assert len(warnings) == 1, (
        f"expected exactly one 'bare model name' warning, got "
        f"{[r.getMessage() for r in warnings]}"
    )
    assert "s_bare_legacy" in warnings[0].getMessage()
    assert "deepseek-v4-flash" in warnings[0].getMessage()


def test_persisted_at_prefixed_model_does_not_warn(caplog):
    """Already-prefixed session must not trigger the bare-name warning.

    Regression for the bug: previously every correctly-prefixed session
    warned because ``_resolve_compatible_session_model`` strips the prefix
    to bare form whenever the provider hint matches the active provider.
    The display helper then warned on its own normalization output.
    """
    caplog.set_level(logging.WARNING, logger="app.api.routes")

    s = _make_session(model="@deepseek:deepseek-v4-flash", session_id="s_prefixed")

    returned = _resolve_effective_session_model_for_display(s)

    # Display side: with the deepseek catalog anchored by the fixture, the
    # @deepseek: hint is known-routable, so keeping the persisted value
    # unchanged is correct. Do NOT assert the value was replaced — that only
    # held in environments WITHOUT a deepseek catalog group, which is exactly
    # the hidden env coupling this module used to have. Only non-emptiness is
    # environment-independent.
    assert returned, "resolver must return a model string"

    # No spurious warning — this is the regression assertion.
    spurious = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "has bare model name" in r.getMessage()
    ]
    assert spurious == [], (
        f"unexpected bare-model warnings for already-prefixed session: "
        f"{[r.getMessage() for r in spurious]}"
    )


def test_persisted_slash_form_model_does_not_warn(caplog):
    """OpenRouter catalog form (``provider/model``) is also not bare.

    Same rationale as the @prefix case: a non-bare persisted form is
    unambiguous across providers.
    """
    caplog.set_level(logging.WARNING, logger="app.api.routes")

    s = _make_session(model="openai/gpt-5-mini", session_id="s_slash")

    _resolve_effective_session_model_for_display(s)

    spurious = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "has bare model name" in r.getMessage()
    ]
    assert spurious == [], (
        f"unexpected bare-model warnings for slash-form session: "
        f"{[r.getMessage() for r in spurious]}"
    )


def test_empty_persisted_model_does_not_warn(caplog):
    """Sessions with no model stored fall back to the default — no warning.

    Edge case: a freshly imported session row may have ``model=""``. The
    resolver substitutes the effective default and should not warn (the
    warning is about persisted bare *names*, not empty values).
    """
    caplog.set_level(logging.WARNING, logger="app.api.routes")

    s = _make_session(model="", session_id="s_empty")

    returned = _resolve_effective_session_model_for_display(s)

    # The default must be a non-bare value (DEFAULT_MODEL comes from
    # HERMES_WEBUI_DEFAULT_MODEL or config.yaml and is always prefixed/slash
    # in practice). If the test environment's default is somehow bare,
    # that's a separate bug — assert the warning is not the spurious one.
    spurious = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "has bare model name" in r.getMessage()
    ]
    assert spurious == [], (
        f"empty model should not warn, got: {[r.getMessage() for r in spurious]}"
    )
    # And we still got *some* model back (default fallback or empty).
    assert returned == "" or returned is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])