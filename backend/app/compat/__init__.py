"""Runtime compatibility shims for the unmodifiable Hermes Agent SDK.

LeapAide must never modify files under the Hermes Agent SDK checkout
(see AGENTS.md). Where the SDK's in-process behaviour needs to adapt to
LeapAide's per-session environment isolation, install a runtime
monkey-patch here instead — from LeapAide-owned code, at server startup
and lazily on the first session turn.
"""

from app.compat.env_overrides import env_override_ctx

__all__ = ["env_override_ctx", "install"]


def install() -> None:
    """Install all SDK compatibility patches (idempotent, thread-safe)."""
    from app.compat import browser_cdp, browser_session, desktop_guard

    browser_cdp.install()
    browser_session.install()
    desktop_guard.install()
