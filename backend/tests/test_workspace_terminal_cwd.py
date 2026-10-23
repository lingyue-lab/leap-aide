"""terminal.cwd is a CLI/gateway concern — it must not shape the WebUI
workspace list.

Behavior under test (option B design):
  * profile_default_workspace() ignores config terminal.cwd entirely; the
    WebUI default falls back to the built-in Default workspace.
  * The stale 'Home' entry older versions persisted into workspaces.json
    from terminal.cwd is dropped on load (user entries are untouched).
  * A fresh start never seeds a terminal.cwd entry — CLI workspaces surface
    only as runtime-derived external (EXT) entries built from state.db
    sessions by the /api/workspaces handler.
"""
import json
from pathlib import Path

import app.api.config as cfg_mod
import app.api.workspace as ws_mod


def _patch_config(monkeypatch, cfg):
    """Replace app.api.config.get_config (lazy-imported by workspace.py)."""
    monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)


def _patch_builtin(monkeypatch, env_dir):
    """Point both built-in-default handles at the same temp directory."""
    env_dir = Path(env_dir)
    env_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ws_mod, "_BOOT_DEFAULT_WORKSPACE", env_dir.resolve())
    monkeypatch.setattr(ws_mod, "_BOOT_DEFAULT_WORKSPACE_PATH", str(env_dir.resolve()))
    monkeypatch.setattr(ws_mod, "_LEGACY_DEFAULT_WORKSPACE_PATH", "")
    return env_dir


# ── profile_default_workspace ignores terminal.cwd ─────────────────────────

def test_profile_default_workspace_ignores_absolute_terminal_cwd(monkeypatch, tmp_path):
    cli_dir = tmp_path / "cli-proj"
    cli_dir.mkdir()
    _patch_config(monkeypatch, {"terminal": {"cwd": str(cli_dir)}})

    assert ws_mod.profile_default_workspace() == str(ws_mod._BOOT_DEFAULT_WORKSPACE)


def test_profile_default_workspace_ignores_terminal_cwd_placeholder(monkeypatch, tmp_path):
    _patch_config(monkeypatch, {"terminal": {"cwd": "."}})

    assert ws_mod.profile_default_workspace() == str(ws_mod._BOOT_DEFAULT_WORKSPACE)


def test_profile_default_workspace_prefers_explicit_webui_key(monkeypatch, tmp_path):
    """Explicit workspace keys remain the WebUI's own config — still honored."""
    webui_ws = tmp_path / "webui-ws"
    webui_ws.mkdir()
    cli_dir = tmp_path / "cli-proj"
    cli_dir.mkdir()
    _patch_config(monkeypatch, {
        "workspace": str(webui_ws),
        "terminal": {"cwd": str(cli_dir)},
    })

    assert ws_mod.profile_default_workspace() == str(webui_ws.resolve())


# ── workspaces.json loading & migration ────────────────────────────────────

def test_stale_terminal_cwd_home_dropped_on_load(monkeypatch, tmp_path):
    cli_dir = tmp_path / "cli-proj"
    cli_dir.mkdir()
    keep = tmp_path / "keep"
    keep.mkdir()
    env_dir = _patch_builtin(monkeypatch, tmp_path / "workspace")
    ws_file = tmp_path / "workspaces.json"
    ws_file.write_text(json.dumps([
        {"path": str(cli_dir.resolve()), "name": "Home"},
        {"path": str(keep.resolve()), "name": "keep"},
    ]), encoding="utf-8")

    monkeypatch.setattr(ws_mod, "_GLOBAL_WS_FILE", ws_file)
    _patch_config(monkeypatch, {"terminal": {"cwd": str(cli_dir)}})

    result = ws_mod.load_workspaces()
    by_path = {w["path"]: w["name"] for w in result}

    # Stale terminal.cwd entry is gone; the user entry and builtin survive.
    assert str(cli_dir.resolve()) not in by_path
    assert by_path[str(keep.resolve())] == "keep"
    assert by_path[str(env_dir.resolve())] == "Default"
    # The drop is persisted so the entry never reappears.
    saved = json.loads(ws_file.read_text(encoding="utf-8"))
    assert str(cli_dir.resolve()) not in {w["path"] for w in saved}


def test_stale_home_not_dropped_without_terminal_cwd_match(monkeypatch, tmp_path):
    """A user entry merely named 'Home' must survive when it is not the
    directory config terminal.cwd points at."""
    other = tmp_path / "other"
    other.mkdir()
    env_dir = _patch_builtin(monkeypatch, tmp_path / "workspace")
    ws_file = tmp_path / "workspaces.json"
    ws_file.write_text(json.dumps([
        {"path": str(other.resolve()), "name": "Home"},
    ]), encoding="utf-8")

    monkeypatch.setattr(ws_mod, "_GLOBAL_WS_FILE", ws_file)
    _patch_config(monkeypatch, {"terminal": {"cwd": "."}})

    result = ws_mod.load_workspaces()
    by_path = {w["path"]: w["name"] for w in result}

    assert by_path[str(other.resolve())] == "Home"
    assert by_path[str(env_dir.resolve())] == "Default"


def test_fresh_start_never_seeds_terminal_cwd_home(monkeypatch, tmp_path):
    cli_dir = tmp_path / "cli-proj"
    cli_dir.mkdir()
    env_dir = _patch_builtin(monkeypatch, tmp_path / "workspace")
    ws_file = tmp_path / "workspaces.json"  # does not exist yet

    monkeypatch.setattr(ws_mod, "_GLOBAL_WS_FILE", ws_file)
    _patch_config(monkeypatch, {"terminal": {"cwd": str(cli_dir)}})

    result = ws_mod.load_workspaces()

    assert result == [{"path": str(env_dir.resolve()), "name": "Default"}]
