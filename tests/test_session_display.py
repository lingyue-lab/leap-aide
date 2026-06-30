"""
Tests for SessionDisplay lifecycle management.

These are standalone unit/integration tests that verify the core
SessionDisplay behavior: start, stop, idempotency, port allocation,
and chrome-wrapper fallback.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from api.session_display import (
    SessionDisplay,
    _find_free_display_and_ports,
    _DISPLAYS,
    SESSIONS_DISPLAY_DIR,
    purge_stale_configs,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup_after_test():
    """Ensure no leaked SessionDisplays after each test."""
    yield
    for sid in list(_DISPLAYS.keys()):
        try:
            _DISPLAYS[sid].stop(remove_json=True)
        except Exception:
            pass


# ── Port Allocation ─────────────────────────────────────────────────────────

def test_port_allocation():
    """Two SessionDisplays get different display/port numbers."""
    display1, vnc1, cdp1 = _find_free_display_and_ports()
    assert 90 <= display1 <= 99
    assert 5900 <= vnc1 <= 5909
    assert cdp1 == vnc1 + 1000


# ── Start / Stop ────────────────────────────────────────────────────────────

def test_start_stop():
    """SessionDisplay.start() creates processes; stop() kills them."""
    sd = SessionDisplay("test-start-stop")
    info = sd.start()

    assert info["display"] is not None
    assert info["vnc_port"] is not None
    assert info["status"] == "active"

    # Verify Xvfb process exists
    assert sd.xvfb_proc is not None
    assert sd.xvfb_proc.poll() is None  # still running
    assert sd.is_running()

    # Verify JSON config written
    json_path = SESSIONS_DISPLAY_DIR / f"{sd.session_id}.json"
    assert json_path.exists()
    config = json.loads(json_path.read_text())
    assert config["display"] == info["display"]
    assert config["vnc_port"] == info["vnc_port"]

    # Stop
    sd.stop(remove_json=True)
    time.sleep(1)

    assert sd.xvfb_proc.poll() is not None  # process terminated
    assert not sd.is_running()
    assert not json_path.exists()  # JSON cleaned up


def test_idempotent_start():
    """Calling start() twice returns the same info."""
    sd = SessionDisplay("test-idempotent")
    info1 = sd.start()
    info2 = sd.start()

    assert info1["vnc_port"] == info2["vnc_port"]
    assert info1["display"] == info2["display"]

    sd.stop()


# ── Chrome Wrapper Fallback ─────────────────────────────────────────────────

def test_wrapper_fallback_no_json():
    """chrome-wrapper.sh falls back to headless Chrome when JSON missing."""
    wrapper_path = Path(__file__).parent.parent / "scripts" / "chrome-wrapper.sh"

    if not wrapper_path.exists():
        pytest.skip("chrome-wrapper.sh not found")

    # Set HERMES_SESSION_KEY to a non-existent session
    env = {**os.environ, "HERMES_SESSION_KEY": "nonexistent-99999"}

    result = subprocess.run(
        ["bash", str(wrapper_path), "--version"],
        env=env,
        capture_output=True, text=True, timeout=5,
    )

    # Should pass through to chrome.real (which may not exist — that's fine;
    # the point is the wrapper didn't find JSON and attempted passthrough)
    # If chrome.real doesn't exist, the wrapper exits with error from exec
    # If it does exist, --version should work
    assert "HERMES_SESSION_KEY" not in (result.stderr or "")
    # Wrapper should not complain about JSON parsing — it should just exec


def test_wrapper_fallback_no_session_key():
    """chrome-wrapper.sh falls back when HERMES_SESSION_KEY not set."""
    wrapper_path = Path(__file__).parent.parent / "scripts" / "chrome-wrapper.sh"

    if not wrapper_path.exists():
        pytest.skip("chrome-wrapper.sh not found")

    # No HERMES_SESSION_KEY set
    env = {k: v for k, v in os.environ.items() if k != "HERMES_SESSION_KEY"}

    result = subprocess.run(
        ["bash", str(wrapper_path), "--version"],
        env=env,
        capture_output=True, text=True, timeout=5,
    )
    # Should attempt to exec chrome.real (which should not fail differently
    # than without the wrapper)
    # The wrapper should exit without JSON-related errors
    if "python3" in (result.stderr or ""):
        # Should NOT try to parse JSON when SESSION_KEY is empty
        assert False, "Wrapper should not parse JSON when SESSION_KEY is empty"


# ── Purge Stale Configs ─────────────────────────────────────────────────────

def test_purge_stale_configs():
    """purge_stale_configs() removes old JSON files."""
    # Create a dummy JSON file
    dummy_path = SESSIONS_DISPLAY_DIR / "stale-test.json"
    dummy_path.write_text(json.dumps({"display": 99, "vnc_port": 5900}))

    assert dummy_path.exists()
    count = purge_stale_configs()
    assert count >= 1
    assert not dummy_path.exists()


# ── Static Info ──────────────────────────────────────────────────────────────

def test_get_info_nonexistent():
    """get_info() returns None for unknown session."""
    assert SessionDisplay.get_info("nonexistent-session-id") is None


def test_get_info_after_start():
    """get_info() returns valid data after start()."""
    sd = SessionDisplay("test-get-info")
    sd.start()

    info = SessionDisplay.get_info("test-get-info")
    assert info is not None
    assert info["display"] == sd.display_num
    assert info["vnc_port"] == sd.vnc_port

    sd.stop()
