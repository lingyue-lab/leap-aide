"""Issue #7: the built-in Default workspace honors HERMES_WEBUI_DEFAULT_WORKSPACE.

Since the refactor, DEFAULT_WORKSPACE (env var, fallback
STATE_DIR/workspaces/default) is the single source of truth: it drives the
protected "Default" workspace-list entry, the session default cwd, and the
/workspace trust root. The old BUILTIN_DEFAULT_WORKSPACE constant, the
~/workspace discovery chain, and the settings.json override are gone.
"""
import json
import os
from pathlib import Path

import app.api.workspace as ws_mod
from tests._test_helpers import get, post


# ── In-process: workspace list loading & migration ─────────────────────────

def _seed_and_load(monkeypatch, tmp_path, seed, builtin, legacy=None,
                   profile_ws=None):
    """Point the workspace module at an isolated state file, seed
    workspaces.json, and return the reloaded list."""
    env_dir = Path(builtin)
    env_dir.mkdir(parents=True, exist_ok=True)
    ws_file = tmp_path / "workspaces.json"
    ws_file.write_text(json.dumps(seed), encoding="utf-8")

    monkeypatch.setattr(ws_mod, "_GLOBAL_WS_FILE", ws_file)
    monkeypatch.setattr(ws_mod, "_BOOT_DEFAULT_WORKSPACE_PATH", str(env_dir.resolve()))
    monkeypatch.setattr(
        ws_mod, "_LEGACY_DEFAULT_WORKSPACE_PATH",
        str(Path(legacy).resolve()) if legacy else "",
    )
    if profile_ws is not None:
        monkeypatch.setattr(ws_mod, "profile_default_workspace", lambda: str(profile_ws))
    return ws_mod.load_workspaces()


def test_existing_json_single_default_at_env(monkeypatch, tmp_path):
    """Scenario A (Docker with old state): stale builtin demoted, one Default."""
    env_dir = tmp_path / "workspace"
    legacy = tmp_path / "state" / "workspaces" / "default"
    legacy.mkdir(parents=True)
    seed = [
        {"path": str(legacy.resolve()), "name": "Default"},
        {"path": str(env_dir.resolve()), "name": "Home"},
    ]

    result = _seed_and_load(monkeypatch, tmp_path, seed, env_dir, legacy)

    defaults = [w for w in result if w["name"] == "Default"]
    assert len(defaults) == 1
    assert defaults[0]["path"] == str(env_dir.resolve())
    # Old entry survives as a plain removable 'Home' entry; dir untouched
    homes = [w for w in result if w["path"] == str(legacy.resolve())]
    assert len(homes) == 1 and homes[0]["name"] == "Home"
    assert legacy.is_dir()
    # No duplicate names
    names = [w["name"] for w in result]
    assert len(names) == len(set(names))


def test_migration_relabels_legacy_when_home_taken(monkeypatch, tmp_path):
    """Scenario E: demoted legacy entry would collide with a real Home."""
    env_dir = tmp_path / "workspace"
    legacy = tmp_path / "state" / "workspaces" / "default"
    legacy.mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    seed = [
        {"path": str(legacy.resolve()), "name": "Default"},
        {"path": str(other.resolve()), "name": "Home"},
    ]

    result = _seed_and_load(monkeypatch, tmp_path, seed, env_dir, legacy)

    by_path = {w["path"]: w["name"] for w in result}
    assert by_path[str(env_dir.resolve())] == "Default"
    assert by_path[str(legacy.resolve())] == "Default (old)"
    assert by_path[str(other.resolve())] == "Home"
    names = [w["name"] for w in result]
    assert len(names) == len(set(names))
    assert legacy.is_dir()


def test_fresh_start_collapses_home_into_builtin(monkeypatch, tmp_path):
    """Scenario C: no state file, profile default == builtin → single entry."""
    env_dir = tmp_path / "workspace"
    monkeypatch.setattr(ws_mod, "_GLOBAL_WS_FILE", tmp_path / "workspaces.json")
    monkeypatch.setattr(ws_mod, "_BOOT_DEFAULT_WORKSPACE_PATH", str(env_dir.resolve()))
    monkeypatch.setattr(
        ws_mod, "_LEGACY_DEFAULT_WORKSPACE_PATH", str(env_dir.resolve())
    )
    monkeypatch.setattr(ws_mod, "profile_default_workspace", lambda: str(env_dir))

    result = ws_mod.load_workspaces()
    assert result == [{"path": str(env_dir.resolve()), "name": "Default"}]


def test_env_unset_builtin_stays_legacy(monkeypatch, tmp_path):
    """Scenario D: env var unset → builtin is the legacy state-dir path."""
    legacy = tmp_path / "state" / "workspaces" / "default"
    legacy.mkdir(parents=True)
    seed = [{"path": str(legacy.resolve()), "name": "Home"}]

    result = _seed_and_load(monkeypatch, tmp_path, seed, legacy, legacy)

    assert result == [{"path": str(legacy.resolve()), "name": "Default"}]


# ── HTTP: /api/workspaces & guards (test server has env var set) ───────────

def _env_default_path() -> str:
    return str(Path(os.environ["HERMES_WEBUI_DEFAULT_WORKSPACE"]).resolve())


def test_get_workspaces_default_entry_uses_env_workspace():
    d, status = get("/api/workspaces")
    assert status == 200
    defaults = [w for w in d["workspaces"] if w.get("name") == "Default"]
    assert len(defaults) == 1, f"expected exactly one Default entry: {d['workspaces']}"
    assert defaults[0]["path"] == _env_default_path()
    assert defaults[0].get("builtin") is True
    # The Default entry must stay selectable in the composer (external would
    # filter it out for WebUI sessions).
    assert not defaults[0].get("external")
    names = [w["name"] for w in d["workspaces"]]
    assert len(names) == len(set(names))


def test_default_workspace_cannot_be_removed_or_renamed():
    path = _env_default_path()
    d, _ = get("/api/workspaces")
    assert any(w.get("name") == "Default" for w in d["workspaces"])

    d1, s1 = post("/api/workspaces/remove", {"path": path})
    assert s1 == 403
    assert "default workspace" in (d1.get("error") or "").lower()

    d2, s2 = post("/api/workspaces/rename", {"path": path, "name": "Hacked"})
    assert s2 == 403
    assert "default workspace" in (d2.get("error") or "").lower()

    d3, _ = get("/api/workspaces")
    entry = next((w for w in d3["workspaces"] if w.get("path") == path), None)
    assert entry is not None and entry.get("name") == "Default"


# ── HTTP: settings echo is read-only ───────────────────────────────────────

def test_settings_default_workspace_is_read_only_echo():
    import app.api.config as config

    d, status = get("/api/settings")
    assert status == 200
    assert d["default_workspace"] == str(config.DEFAULT_WORKSPACE)

    # POSTing a different value must be ignored — env var stays the truth.
    saved, s2 = post("/api/settings", {"default_workspace": "/definitely/not/used"})
    assert s2 == 200
    assert saved["default_workspace"] == str(config.DEFAULT_WORKSPACE)

    d2, _ = get("/api/settings")
    assert d2["default_workspace"] == str(config.DEFAULT_WORKSPACE)
