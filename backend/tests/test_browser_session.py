"""Tests for app.compat.browser_session — browser_exec per-session default.

Uses a stub ``orig`` (same pattern as the browser_cdp patch tests) so no
SDK import is needed.
"""

from types import SimpleNamespace

from app.compat.browser_session import (
    _default_session_id,
    make_patched_browser_exec,
    sync_preamble,
)
from app.compat.env_overrides import env_override_ctx


def test_default_session_id_from_context():
    token = env_override_ctx.set(
        {"HERMES_SESSION_KEY": "agent:main:webui:dm:abc123"}
    )
    try:
        assert _default_session_id() == "abc123"
    finally:
        env_override_ctx.reset(token)


def test_default_session_id_bare_key():
    token = env_override_ctx.set({"HERMES_SESSION_KEY": "deadbeef99"})
    try:
        assert _default_session_id() == "deadbeef99"
    finally:
        env_override_ctx.reset(token)


def test_default_session_id_sanitizes_for_bu_name():
    # BU_NAME / _SESSION_RE only accept [A-Za-z0-9_-]; junk from an odd
    # key format must never reach the harness as an invalid name.
    token = env_override_ctx.set(
        {"HERMES_SESSION_KEY": "agent:main:webui:dm:weird.name/1"}
    )
    try:
        assert _default_session_id() == "weirdname1"
    finally:
        env_override_ctx.reset(token)


def test_default_session_id_without_context_is_empty():
    token = env_override_ctx.set(None)
    try:
        assert _default_session_id() == ""
    finally:
        env_override_ctx.reset(token)


def test_patch_fills_omitted_session():
    seen = {}

    def orig(*args, **kwargs):
        seen.update(kwargs)
        return "ok"

    patched = make_patched_browser_exec(orig)
    token = env_override_ctx.set(
        {"HERMES_SESSION_KEY": "agent:main:webui:dm:abc123"}
    )
    try:
        assert patched(code="print(1)") == "ok"
    finally:
        env_override_ctx.reset(token)
    assert seen["session"] == "abc123"


def test_patch_preserves_explicit_session():
    seen = {}

    def orig(*args, **kwargs):
        seen.update(kwargs)
        return "ok"

    patched = make_patched_browser_exec(orig)
    token = env_override_ctx.set(
        {"HERMES_SESSION_KEY": "agent:main:webui:dm:abc123"}
    )
    try:
        assert patched(code="print(1)", session="custom-name") == "ok"
    finally:
        env_override_ctx.reset(token)
    assert seen["session"] == "custom-name"


def test_patch_without_context_leaves_default():
    seen = {}

    def orig(*args, **kwargs):
        seen.update(kwargs)
        return "ok"

    patched = make_patched_browser_exec(orig)
    token = env_override_ctx.set(None)
    try:
        assert patched(code="print(1)") == "ok"
    finally:
        env_override_ctx.reset(token)
    # No context → no session kwarg injected → orig's own default applies.
    assert "session" not in seen


# ── own-tab preamble toggle (receptionist active → suppress) ────────────────


def test_sync_preamble_suppresses_when_active():
    mod = SimpleNamespace(_OWN_TAB_PREAMBLE="PIN")
    sync_preamble(mod, True)
    assert mod._OWN_TAB_PREAMBLE == ""
    # Original captured for later restoration.
    assert mod._leap_aide_orig_preamble == "PIN"


def test_sync_preamble_restores_original_when_inactive():
    mod = SimpleNamespace(_OWN_TAB_PREAMBLE="PIN")
    sync_preamble(mod, True)
    sync_preamble(mod, False)
    assert mod._OWN_TAB_PREAMBLE == "PIN"


def test_sync_preamble_survives_repeated_toggles():
    # Restoration must always yield the SDK's original string, not the
    # suppressed "" captured by an earlier toggle.
    mod = SimpleNamespace(_OWN_TAB_PREAMBLE="PIN")
    for active in (True, False, True, False):
        sync_preamble(mod, active)
    assert mod._OWN_TAB_PREAMBLE == "PIN"


def test_patch_toggles_preamble_on_call():
    mod = SimpleNamespace(_OWN_TAB_PREAMBLE="PIN")
    seen = {}

    def orig(*args, **kwargs):
        seen["preamble"] = mod._OWN_TAB_PREAMBLE
        return "ok"

    patched = make_patched_browser_exec(
        orig, buc=mod, is_active=lambda: True,
    )
    token = env_override_ctx.set(
        {"HERMES_SESSION_KEY": "agent:main:webui:dm:abc123"}
    )
    try:
        assert patched(code="print(1)") == "ok"
    finally:
        env_override_ctx.reset(token)
    assert seen["preamble"] == ""


def test_patch_keeps_preamble_when_receptionist_inactive():
    mod = SimpleNamespace(_OWN_TAB_PREAMBLE="PIN")
    seen = {}

    def orig(*args, **kwargs):
        seen["preamble"] = mod._OWN_TAB_PREAMBLE
        return "ok"

    patched = make_patched_browser_exec(
        orig, buc=mod, is_active=lambda: False,
    )
    assert patched(code="print(1)") == "ok"
    assert seen["preamble"] == "PIN"


def test_patch_without_buc_skips_preamble_toggle():
    # Legacy signature (no module) must not touch any preamble state.
    seen = {}

    def orig(*args, **kwargs):
        seen.update(kwargs)
        return "ok"

    patched = make_patched_browser_exec(orig)
    assert patched(code="print(1)") == "ok"
    assert seen["code"] == "print(1)"
