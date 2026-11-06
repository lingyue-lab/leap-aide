"""
Tests for the global shared browser desktop (issue #37).

Unit-level: process launches and the receptionist are mocked so these
tests run without Xvfb/Chrome installed.
"""

import json
import time
from pathlib import Path

import pytest

from app.api import global_desktop as gd_mod
from app.api.global_desktop import (
    GlobalDesktop,
    DesktopSupervisor,
    DISPLAY_NUM,
    VNC_PORT,
    RECEPTIONIST_PORT,
    cleanup_legacy_configs,
)


class _FakeProc:
    """Minimal subprocess.Popen stand-in."""

    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    """A GlobalDesktop with all process launches and the receptionist faked."""
    state_json = tmp_path / "browser_desktop.json"
    monkeypatch.setattr(gd_mod, "BROWSER_DESKTOP_JSON", state_json)

    procs = []

    def fake_popen(*args, **kwargs):
        p = _FakeProc()
        procs.append(p)
        return p

    monkeypatch.setattr(gd_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(gd_mod, "_wait_for_socket", lambda *a, **k: True)
    monkeypatch.setattr(gd_mod, "_wait_for_tcp_port", lambda *a, **k: True)
    monkeypatch.setattr(gd_mod.time, "sleep", lambda s: None)
    # dbus-launch uses check_output — keep it fake too
    monkeypatch.setattr(
        gd_mod.subprocess, "check_output",
        lambda *a, **k: b"export DBUS_SESSION_BUS_ADDRESS='unix:abstract=/tmp/dbus-test';\nexport DBUS_SESSION_BUS_PID=4242;\n",
    )
    monkeypatch.setattr(gd_mod.os, "kill", lambda *a, **k: None)
    monkeypatch.setattr(GlobalDesktop, "_ensure_fcitx5_profile", staticmethod(lambda: None))
    monkeypatch.setattr(GlobalDesktop, "_configure_fcitx5_trigger_key", staticmethod(lambda env: None))
    monkeypatch.setattr(GlobalDesktop, "_find_chrome_binary", staticmethod(lambda: "/bin/true"))

    d = GlobalDesktop()
    monkeypatch.setattr(d, "_start_receptionist", lambda: None)
    monkeypatch.setattr(d, "_stop_receptionist", lambda: None)
    monkeypatch.setattr(d, "_receptionist_healthy", lambda: True)
    # Do not actually run the supervisor thread
    monkeypatch.setattr(DesktopSupervisor, "start", lambda self: None)
    monkeypatch.setattr(DesktopSupervisor, "stop", lambda self: None)

    yield d
    try:
        d.stop(remove_json=True)
    except Exception:
        pass


# ── Fixed ports (no pool) ───────────────────────────────────────────────────

def test_fixed_global_ports():
    """The global desktop uses fixed, env-overridable ports — no pool."""
    assert DISPLAY_NUM == 99
    assert VNC_PORT == 5900
    assert RECEPTIONIST_PORT == 6900


# ── Start / Stop ────────────────────────────────────────────────────────────

def test_start_stop(desktop):
    """start() brings up the stack and writes the single state file."""
    info = desktop.start()

    assert info["display"] == DISPLAY_NUM
    assert info["vnc_port"] == VNC_PORT
    assert info["receptionist_port"] == RECEPTIONIST_PORT
    assert info["receptionist_url"] == f"http://127.0.0.1:{RECEPTIONIST_PORT}"
    assert info["status"] == "active"
    assert desktop.is_running()
    assert desktop.xvfb_proc is not None

    # Single global JSON written
    assert gd_mod.BROWSER_DESKTOP_JSON.exists()
    config = json.loads(gd_mod.BROWSER_DESKTOP_JSON.read_text())
    assert config["display"] == DISPLAY_NUM
    assert config["vnc_port"] == VNC_PORT
    assert config["receptionist_url"] == f"http://127.0.0.1:{RECEPTIONIST_PORT}"
    assert config["status"] == "active"

    desktop.stop(remove_json=True)
    time.sleep(0.1)

    assert desktop.xvfb_proc is None
    assert not desktop.is_running()
    assert not gd_mod.BROWSER_DESKTOP_JSON.exists()


def test_idempotent_start(desktop):
    """Calling start() twice returns the same info."""
    info1 = desktop.start()
    info2 = desktop.start()
    assert info1["vnc_port"] == info2["vnc_port"]
    assert info1["display"] == info2["display"]
    desktop.stop()


def test_degraded_without_receptionist(desktop, monkeypatch):
    """State is 'degraded' (not advertised) when the receptionist is down."""
    monkeypatch.setattr(desktop, "_receptionist_healthy", lambda: False)
    info = desktop.start()
    assert info["status"] == "degraded"
    assert not desktop.cdp_available()
    desktop.stop()


# ── cdp_available / get_info ────────────────────────────────────────────────

def test_cdp_available_reads_state(desktop):
    desktop.start()
    assert desktop.cdp_available() is True
    # Simulate supervisor marking degraded
    data = json.loads(gd_mod.BROWSER_DESKTOP_JSON.read_text())
    data["status"] = "degraded"
    gd_mod.BROWSER_DESKTOP_JSON.write_text(json.dumps(data))
    assert desktop.cdp_available() is False
    desktop.stop()


def test_get_info_nonexistent(desktop):
    assert GlobalDesktop.get_info() is None


def test_disabled_boot_removes_stale_state(monkeypatch, tmp_path):
    """HERMES_GLOBAL_DESKTOP=0 must not leave an 'active' state file behind.

    The wrapper script and browser_cdp read the JSON directly; a stale
    entry would advertise a receptionist that never starts.
    """
    state_json = tmp_path / "browser_desktop.json"
    monkeypatch.setattr(gd_mod, "BROWSER_DESKTOP_JSON", state_json)
    state_json.write_text(json.dumps({"status": "active",
                                      "receptionist_url": "http://127.0.0.1:6900"}))
    monkeypatch.setattr(gd_mod, "DESKTOP_ENABLED", False)
    assert gd_mod.ensure_global_desktop() is False
    assert not state_json.exists()
    assert GlobalDesktop.get_info() is None


# ── Legacy cleanup ──────────────────────────────────────────────────────────

def test_cleanup_legacy_configs(monkeypatch, tmp_path):
    legacy = tmp_path / "sessions_display"
    legacy.mkdir()
    (legacy / "old-sid.json").write_text("{}")
    monkeypatch.setattr(gd_mod, "LEGACY_DISPLAY_DIR", legacy)
    cleanup_legacy_configs()
    assert not legacy.exists()


# ── Supervisor backoff ──────────────────────────────────────────────────────

def test_supervisor_backoff(desktop):
    """Repeated failures back the interval off, capped at 60s."""
    sup = DesktopSupervisor(desktop)
    assert sup._interval() == gd_mod.HEALTH_INTERVAL
    sup._fail_streak = 1
    assert sup._interval() == gd_mod.HEALTH_INTERVAL * 2
    sup._fail_streak = 10
    assert sup._interval() == 60.0


# ── xterm accounting ────────────────────────────────────────────────────────

def test_xterm_cap_is_global(desktop):
    """MAX_XTERMS is a global cap keyed by tid, entries carry session_id."""
    from app.api.global_desktop import XtermEntry

    desktop.start()
    for i in range(3):
        desktop.xterm_procs[f"tid{i}"] = XtermEntry(
            proc=_FakeProc(), session_id=f"sess{i % 2}", started_at=time.time(),
        )
    assert len(desktop.xterm_procs) == 3
    assert desktop.xterm_procs["tid0"].session_id == "sess0"
    desktop.stop()


# ── fcitx5 profile seeding ──────────────────────────────────────────────────

# fcitx5's own first-run profile: the group is named "Default" and holds
# keyboard-us only. The group name is NOT an input method.
PRISTINE_FCITX5_PROFILE = """\
[Groups/0]
# Group Name
Name=Default
# Layout
Default Layout=us
# Default Input Method
DefaultIM=keyboard-us

[Groups/0/Items/0]
# Name
Name=keyboard-us
# Layout
Layout=

[GroupOrder]
0=Default
"""


def _fcitx5_profile(tmp_path, text=None):
    """Return the path of an fcitx5 profile inside a fake $HOME."""
    home = tmp_path / "home"
    profile = home / ".config" / "fcitx5" / "profile"
    profile.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        profile.write_text(text, encoding="utf-8")
    return profile


def test_profile_input_methods_ignores_group_name():
    """The group's own Name= line is not an input method."""
    from app.api.global_desktop import _fcitx5_profile_input_methods

    assert _fcitx5_profile_input_methods(PRISTINE_FCITX5_PROFILE) == ["keyboard-us"]


def test_ensure_fcitx5_profile_seeds_when_missing(monkeypatch, tmp_path):
    """A fresh $HOME gets a keyboard-us + pinyin profile."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    GlobalDesktop._ensure_fcitx5_profile()

    text = _fcitx5_profile(tmp_path).read_text(encoding="utf-8")
    assert "Name=pinyin" in text
    assert "DefaultIM=pinyin" in text


def test_ensure_fcitx5_profile_upgrades_pristine_default(monkeypatch, tmp_path):
    """fcitx5's own keyboard-only profile is upgraded to include pinyin.

    The group-level "Name=Default" line used to be read as a custom
    input method, so the seeding bailed out on every start where the
    profile already existed — the k8s deployment keeps a persistent
    $HOME, the local root run did not.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    profile = _fcitx5_profile(tmp_path, PRISTINE_FCITX5_PROFILE)

    GlobalDesktop._ensure_fcitx5_profile()

    text = profile.read_text(encoding="utf-8")
    assert "Name=pinyin" in text
    assert "DefaultIM=pinyin" in text


def test_ensure_fcitx5_profile_keeps_custom_im(monkeypatch, tmp_path):
    """A user profile with a custom input method is left untouched."""
    custom = PRISTINE_FCITX5_PROFILE.replace("Name=keyboard-us", "Name=mozc")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    profile = _fcitx5_profile(tmp_path, custom)

    GlobalDesktop._ensure_fcitx5_profile()

    assert profile.read_text(encoding="utf-8") == custom


def test_ensure_fcitx5_profile_keeps_existing_pinyin(monkeypatch, tmp_path):
    """A profile that already lists pinyin is never rewritten."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    seeded = (
        "[Groups/0]\n"
        "Name=Default\n"
        "Default Layout=us\n"
        "DefaultIM=pinyin\n"
        "\n"
        "[Groups/0/Items/0]\n"
        "Name=keyboard-us\n"
        "Layout=\n"
        "\n"
        "[Groups/0/Items/1]\n"
        "Name=pinyin\n"
        "Layout=\n"
        "\n"
        "[GroupOrder]\n"
        "0=Default\n"
    )
    profile = _fcitx5_profile(tmp_path, seeded)

    GlobalDesktop._ensure_fcitx5_profile()

    assert profile.read_text(encoding="utf-8") == seeded
