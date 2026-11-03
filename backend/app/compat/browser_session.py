"""Per-session isolation for the browser-use CLI backend (runtime patch).

The SDK's ``browser_exec`` takes an optional ``session`` argument which
becomes the harness daemon's ``BU_NAME`` — one daemon per name, each with
its own CDP connection to the receptionist and its own dedicated tab.
When the model omits it, every LeapAide session shares the single
"default" daemon: its process-global tab state makes concurrent sessions
clobber each other's navigations, and its one shared receptionist
connection defeats the proxy's per-connection tab isolation (session A
and B ride the same socket, so the proxy cannot tell them apart).

LeapAide runs concurrent sessions in one process, so this patch fills
the default from the thread's ``HERMES_SESSION_KEY`` (same source as
``browser_cdp``). Each session then gets its own daemon → its own
receptionist connection → per-connection tab isolation and per-sid
turn-end cleanup both apply again.

The SDK's registry handler resolves ``browser_exec`` from the module
globals at call time, so replacing that one attribute is enough. No SDK
file is modified (AGENTS.md forbids editing the Hermes checkout).

This patch also toggles the SDK's ``_OWN_TAB_PREAMBLE`` — the "pin this
named session to a tab it created" preamble prepended to model code on
shared browsers. Under the receptionist the harness daemon already owns
an exclusive tab (the connection's filtered view contains only its own
tabs, and named daemons create one on attach), so the preamble would
only add a second, never-navigated blank tab to the shared desktop.
When the desktop is down the local Chrome is shared WITHOUT per-
connection filtering, so the original preamble is restored there.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# browser_use_cli._SESSION_RE and the harness's BU_NAME guard both accept
# 1-64 chars of [A-Za-z0-9_-] starting with an alphanumeric.
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]")

_installed = False


def _default_session_id() -> str:
    """This thread's LeapAide session id, sanitized for BU_NAME, or ""."""
    from app.compat.env_overrides import env_override_ctx

    overrides = env_override_ctx.get() or {}
    session_key = (overrides.get("HERMES_SESSION_KEY") or "").strip()
    if not session_key:
        return ""
    # webui format is agent:main:webui:dm:<session_id>; a bare session id
    # also works with rsplit (same rule as browser_cdp).
    sid = _SAFE_CHARS.sub("", session_key.rsplit(":", 1)[-1])[:64]
    if not sid:
        return ""
    if not sid[0].isalnum():
        sid = "s" + sid[:63]
    return sid


def _receptionist_active() -> bool:
    """True when this call's CDP endpoint is the shared receptionist."""
    from app.compat.browser_cdp import _receptionist_cdp_url

    return bool(_receptionist_cdp_url())


def sync_preamble(mod, active: bool) -> None:
    """Toggle ``mod._OWN_TAB_PREAMBLE`` for the current desktop state.

    The original string is captured on first use
    (``_leap_aide_orig_preamble``) so switching back to desktop-down
    restores the SDK's preamble exactly.
    """
    original = getattr(mod, "_leap_aide_orig_preamble", None)
    if original is None:
        original = mod._OWN_TAB_PREAMBLE
        setattr(mod, "_leap_aide_orig_preamble", original)
    mod._OWN_TAB_PREAMBLE = "" if active else original


def make_patched_browser_exec(orig, buc=None, is_active=None):
    """Wrap ``browser_exec`` so an omitted ``session`` defaults to the
    current LeapAide session id.

    Split out from :func:`install` so tests can exercise the logic
    against a stub ``orig`` without importing the heavy SDK module. The
    SDK registry handler calls ``browser_exec`` with keyword arguments
    only, so the wrapper inspects ``kwargs`` (a positional ``session``
    is never passed by that path).

    ``buc``: the SDK module whose ``_OWN_TAB_PREAMBLE`` is toggled per
    call (``None`` skips the preamble toggle). ``is_active``: override
    for the receptionist-active probe (tests).
    """

    def _patched(*args, **kwargs):
        if not kwargs.get("session"):
            sid = _default_session_id()
            if sid:
                kwargs["session"] = sid
        if buc is not None:
            try:
                active = (
                    is_active()
                    if is_active is not None
                    else _receptionist_active()
                )
                sync_preamble(buc, active)
            except Exception:
                logger.debug(
                    "compat.browser_session: preamble toggle failed",
                    exc_info=True,
                )
        return orig(*args, **kwargs)

    _patched.__name__ = getattr(orig, "__name__", "browser_exec")
    _patched.__doc__ = getattr(orig, "__doc__", None)
    return _patched


def install() -> None:
    """Patch the SDK's ``browser_exec`` with per-session defaulting."""
    global _installed
    if _installed:
        return
    try:
        from tools import browser_use_cli as _buc
    except Exception:
        # SDK not importable in this process (unit tests without the
        # hermes checkout on sys.path) — retry on the next install().
        logger.debug(
            "compat.browser_session: browser_use_cli not importable; skip",
            exc_info=True,
        )
        return

    if not getattr(_buc, "_leap_aide_session_patched", False):
        _buc.browser_exec = make_patched_browser_exec(_buc.browser_exec, _buc)
        # Keep the model-facing schema honest: omitting `session` no
        # longer means "join the shared default daemon".
        try:
            prop = _buc.BROWSER_EXEC_SCHEMA["parameters"]["properties"]["session"]
            prop["description"] = str(prop.get("description") or "") + (
                " When omitted, LeapAide defaults this to your current"
                " session id — sessions are always daemon-isolated."
            )
        except Exception:
            pass
        _buc._leap_aide_session_patched = True

    _installed = True
    logger.info(
        "compat.browser_session: browser_exec per-session default installed"
    )
