"""Cross-session env isolation and global CDP resolution (regressions).

Hermes runs each tool call on a ThreadPoolExecutor worker wrapped in
propagate_context_to_thread() (tools/thread_context.py), which copies
contextvars into the worker. The old threading.local-based overrides in
config._set_thread_env never reached those workers, so a tool-spawned
subprocess inherited the raw process-global os.environ — leaking one
concurrent session's values into another session's terminal commands.

The CDP endpoint itself deliberately does NOT ride any env var: the
single source of truth is the global desktop state file
($HERMES_WEBUI_STATE_DIR/browser_desktop.json — issue #37's one shared
desktop). In-process SDK readers get it through the
app.compat.browser_cdp patch (session id from the ContextVar-carried
HERMES_SESSION_KEY → receptionist discovery URL), and the agent-browser
wrapper resolves the same file in the child env. These tests pin down
both mechanisms against concurrent-session interference.
"""

import contextvars
import json
import os
import subprocess
import threading

from app.api import config as app_config


def _run_on_copied_context(fn):
    """Run fn on a worker thread with the parent's contextvars copied.

    Mirrors tools/thread_context.propagate_context_to_thread — the
    propagation Hermes applies to concurrent tool-worker submissions.
    """
    ctx = contextvars.copy_context()
    box = {}

    def _target():
        try:
            box["value"] = ctx.run(fn)
        except Exception as exc:  # pragma: no cover - diagnostic path
            box["exc"] = exc

    t = threading.Thread(target=_target)
    t.start()
    t.join(timeout=30)
    if "exc" in box:
        raise box["exc"]
    return box.get("value")


def _child_env_value():
    """Spawn a subprocess that prints TERMINAL_CWD; return what it saw."""
    out = subprocess.run(
        [
            "python3",
            "-c",
            "import os; print(os.environ.get('TERMINAL_CWD', '<unset>'))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


class _PatchedEnviron:
    """Temporarily set/unset a global os.environ entry around a block."""

    def __init__(self, key, value):
        self.key = key
        self.value = value
        self.old = None
        self.had = key in os.environ

    def __enter__(self):
        if self.had:
            self.old = os.environ[self.key]
        os.environ[self.key] = self.value
        return self

    def __exit__(self, *exc):
        if self.had:
            os.environ[self.key] = self.old
        else:
            os.environ.pop(self.key, None)
        return False


# ── Subprocess path (_IsolatedPopen + ContextVar overrides) ─────────────────


def test_override_reaches_context_copied_worker():
    """Session overrides set on the session thread reach copied workers.

    A *foreign* stale value sits in the global os.environ; the per-session
    override must win inside the worker's spawned subprocess.
    """
    app_config._set_thread_env(TERMINAL_CWD="/tmp/sessA")
    try:
        with _PatchedEnviron("TERMINAL_CWD", "/tmp/sessB"):
            seen = _run_on_copied_context(_child_env_value)
    finally:
        app_config._clear_thread_env()
    assert seen == "/tmp/sessA"


def test_empty_override_masks_foreign_global_value():
    """An explicit empty override never sees another session's value."""
    app_config._set_thread_env(TERMINAL_CWD="")
    try:
        with _PatchedEnviron("TERMINAL_CWD", "/tmp/sessB"):
            seen = _run_on_copied_context(_child_env_value)
    finally:
        app_config._clear_thread_env()
    assert seen == ""


def test_no_override_context_is_unaffected():
    """Contexts that never registered overrides keep legacy behavior."""
    app_config._clear_thread_env()
    with _PatchedEnviron("TERMINAL_CWD", "/tmp/sessB"):
        seen = _run_on_copied_context(_child_env_value)
    # No override in this context → the raw global value is inherited,
    # exactly as before the isolation mechanism existed.
    assert seen == "/tmp/sessB"


# ── In-process SDK reader (browser_tool._get_cdp_override_raw) ──────────────
#
# browser_tool runs inside the hermes process and used to read the
# process-global os.environ directly — concurrent sessions would read a
# shared value and cross-attach. app.compat.browser_cdp patches the
# getter to resolve the endpoint from the GLOBAL desktop state file via
# the ContextVar-carried HERMES_SESSION_KEY (sid rides the discovery URL
# query, not an env var). The tests below exercise the patched getter
# via a stub ``orig`` (the real SDK module is heavy to import; install()
# covers the wiring separately).


def _make_stub_getter():
    """Stub of the SDK's original _get_cdp_override_raw (global-env reader)."""
    return lambda: os.environ.get("BROWSER_CDP_URL", "").strip()


def _write_desktop_state(monkeypatch, tmp_path, **overrides):
    """Point the module-level state-file path at a temp file and write it."""
    from app.api import global_desktop as gd_mod

    state_json = tmp_path / "browser_desktop.json"
    monkeypatch.setattr(gd_mod, "BROWSER_DESKTOP_JSON", state_json)
    body = {
        "display": 99,
        "vnc_port": 5900,
        "receptionist_port": 6900,
        "receptionist_url": "http://127.0.0.1:6900",
        "status": "active",
    }
    body.update(overrides)
    state_json.write_text(json.dumps(body))
    return state_json


def test_cdp_getter_resolves_receptionist_url_with_sid(monkeypatch, tmp_path):
    """Session context + active desktop → receptionist URL with this sid.

    The sid must ride the discovery URL query so the receptionist can
    attribute the connection; a foreign BROWSER_CDP_URL in the global
    env must never win.
    """
    from app.compat.browser_cdp import make_patched_getter

    _write_desktop_state(monkeypatch, tmp_path)
    getter = make_patched_getter(_make_stub_getter())
    app_config._set_thread_env(
        HERMES_SESSION_KEY="agent:main:webui:dm:sessA"
    )
    try:
        with _PatchedEnviron("BROWSER_CDP_URL", "http://127.0.0.1:6901"):
            seen = _run_on_copied_context(getter)
    finally:
        app_config._clear_thread_env()
    assert seen == "http://127.0.0.1:6900/json/version?sid=sessA"


def test_cdp_getter_concurrent_sessions_share_endpoint_not_identity(
    monkeypatch, tmp_path
):
    """Two concurrent sessions resolve the same endpoint with distinct sids.

    Regression: isolation used to come from per-session ports; now it
    comes from the sid query — so both sessions must see the same base
    URL, and each must carry its own sid.
    """
    from app.compat.browser_cdp import make_patched_getter

    _write_desktop_state(monkeypatch, tmp_path)
    getter = make_patched_getter(_make_stub_getter())

    def resolve(session_id):
        app_config._set_thread_env(
            HERMES_SESSION_KEY=f"agent:main:webui:dm:{session_id}"
        )
        try:
            return _run_on_copied_context(getter)
        finally:
            app_config._clear_thread_env()

    # Sequential is enough here: the ContextVar path is already proven
    # concurrent-safe by the subprocess tests above; this pins the
    # per-session sid attribution.
    assert resolve("sessA") == "http://127.0.0.1:6900/json/version?sid=sessA"
    assert resolve("sessB") == "http://127.0.0.1:6900/json/version?sid=sessB"


def test_cdp_getter_desktop_down_reads_empty(monkeypatch, tmp_path):
    """Desktop degraded → "" so browser tools fall back to local headless.

    A session must never attach to a foreign Chrome just because a
    stale env var points somewhere.
    """
    from app.compat.browser_cdp import make_patched_getter

    _write_desktop_state(monkeypatch, tmp_path, status="degraded")
    getter = make_patched_getter(_make_stub_getter())
    app_config._set_thread_env(
        HERMES_SESSION_KEY="agent:main:webui:dm:sessB"
    )
    try:
        with _PatchedEnviron("BROWSER_CDP_URL", "http://127.0.0.1:6900"):
            seen = _run_on_copied_context(getter)
    finally:
        app_config._clear_thread_env()
    assert seen == ""


def test_cdp_getter_no_session_context_falls_back_to_orig():
    """Contexts without a session key keep SDK precedence (global env)."""
    from app.compat.browser_cdp import make_patched_getter

    getter = make_patched_getter(_make_stub_getter())
    app_config._clear_thread_env()
    with _PatchedEnviron("BROWSER_CDP_URL", "http://127.0.0.1:6901"):
        assert getter() == "http://127.0.0.1:6901"


def test_cdp_getter_translates_legacy_sentinel():
    """The legacy 19999 sentinel is never a live endpoint — reads as unconfigured."""
    from app.compat.browser_cdp import CDP_URL_SENTINEL, make_patched_getter

    getter = make_patched_getter(_make_stub_getter())
    app_config._clear_thread_env()
    with _PatchedEnviron("BROWSER_CDP_URL", CDP_URL_SENTINEL):
        assert getter() == ""


def test_cdp_getter_unreadable_state_reads_empty(monkeypatch, tmp_path):
    """Corrupt desktop state → no desktop, rather than guessing a port."""
    from app.compat.browser_cdp import make_patched_getter

    from app.api import global_desktop as gd_mod

    state_json = tmp_path / "browser_desktop.json"
    monkeypatch.setattr(gd_mod, "BROWSER_DESKTOP_JSON", state_json)
    state_json.write_text("{not json")
    getter = make_patched_getter(_make_stub_getter())
    app_config._set_thread_env(
        HERMES_SESSION_KEY="agent:main:webui:dm:sessA"
    )
    try:
        assert getter() == ""
    finally:
        app_config._clear_thread_env()


def test_install_patches_sdk_module_if_importable():
    """install() rebinds browser_tool._get_cdp_override_raw when SDK is present."""
    from app.compat import browser_cdp

    try:
        from tools import browser_tool as _bt
    except Exception:
        import pytest
        pytest.skip("hermes SDK browser_tool not importable in this process")
    browser_cdp.install()
    assert getattr(_bt, "_leap_aide_cdp_patched", False)
    assert _bt._get_cdp_override_raw.__name__ == "_patched"
