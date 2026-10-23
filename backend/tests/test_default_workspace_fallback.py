"""Tests for the single-source default workspace resolver (issue #7).

Since the refactor, HERMES_WEBUI_DEFAULT_WORKSPACE (with the managed
STATE_DIR/workspaces/default fallback) is the only source for
DEFAULT_WORKSPACE — the old ~/workspace discovery chain and the
settings.json override are gone.
"""
import json
import os

import pytest

import app.api.config as config


# These tests use chmod to make a path "unwritable" and assert that the
# resolver refuses to use it. Linux bypasses DAC permission checks for uid=0,
# so root can write to chmod-0o500 directories and the fallback path is never
# exercised. Skip on root rather than refactor every test — the chmod approach
# is the standard idiom and is correct on any non-root CI / dev box.
_IS_ROOT = os.geteuid() == 0
_SKIP_IF_ROOT = pytest.mark.skipif(
    _IS_ROOT,
    reason="chmod-based unwritable checks are bypassed by uid=0; rerun as non-root",
)


def test_env_var_used_when_dir_exists(monkeypatch, tmp_path):
    env_ws = tmp_path / "env_workspace"
    env_ws.mkdir()
    state_dir = tmp_path / "state"
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setenv("HERMES_WEBUI_DEFAULT_WORKSPACE", str(env_ws))
    assert config._resolve_default_workspace() == env_ws.resolve()


def test_env_var_creates_missing_dir(monkeypatch, tmp_path):
    env_ws = tmp_path / "new_workspace"
    state_dir = tmp_path / "state"
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setenv("HERMES_WEBUI_DEFAULT_WORKSPACE", str(env_ws))
    resolved = config._resolve_default_workspace()
    assert resolved == env_ws.resolve()
    assert resolved.is_dir()


def test_falls_back_to_state_dir_when_env_unset(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.delenv("HERMES_WEBUI_DEFAULT_WORKSPACE", raising=False)
    assert config._resolve_default_workspace() == state_dir / "workspaces" / "default"


def test_falls_back_when_env_path_is_a_file(monkeypatch, tmp_path):
    """An existing regular file at the env path is unusable — fall back."""
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    state_dir = tmp_path / "state"
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setenv("HERMES_WEBUI_DEFAULT_WORKSPACE", str(blocker))
    assert config._resolve_default_workspace() == state_dir / "workspaces" / "default"


@_SKIP_IF_ROOT
def test_falls_back_when_env_path_uncreatable(monkeypatch, tmp_path):
    import stat
    ro_parent = tmp_path / "ro_parent"
    ro_parent.mkdir()
    ro_parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(config, "STATE_DIR", state_dir)
    monkeypatch.setenv(
        "HERMES_WEBUI_DEFAULT_WORKSPACE", str(ro_parent / "child")
    )
    try:
        assert config._resolve_default_workspace() == state_dir / "workspaces" / "default"
    finally:
        ro_parent.chmod(stat.S_IRWXU)


def test_stale_settings_key_is_dropped_on_load(monkeypatch, tmp_path):
    """A default_workspace value stored by an older version must not survive.

    Regression for issue #609 semantics after the issue #7 refactor: settings
    can no longer shadow HERMES_WEBUI_DEFAULT_WORKSPACE because the key is
    dropped at load time.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    settings_file = state_dir / "settings.json"
    settings_ws = tmp_path / "settings_workspace"
    settings_ws.mkdir()
    settings_file.write_text(
        json.dumps({"default_workspace": str(settings_ws), "send_key": "enter"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(config, "_settings_cache", None)
    monkeypatch.setattr(config, "_settings_mtime", -1.0)

    settings = config.load_settings()
    assert "default_workspace" not in settings
    assert settings["send_key"] == "enter"


def test_save_settings_ignores_default_workspace(monkeypatch, tmp_path):
    """POSTing default_workspace must neither persist nor echo a stored value."""
    state_dir = tmp_path / "state"
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(config, "_settings_cache", None)
    monkeypatch.setattr(config, "_settings_mtime", -1.0)

    saved = config.save_settings({"default_workspace": "/definitely/not/used"})
    on_disk = json.loads(settings_file.read_text(encoding="utf-8"))

    assert "default_workspace" not in saved
    assert "default_workspace" not in on_disk
