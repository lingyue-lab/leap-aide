"""Failing (RED) tests for the boot-time default-plugin merge (PR #103 fix).

Spec: docker-compose bind-mounts ``${HOME}/.hermes`` over the runtime
HERMES_HOME, and docker-entrypoint.sh's seed copy skips top-level entries
that already exist (no recursion). Existing deployment volumes therefore
never receive the image's ``plugins/`` tree and/or ``plugins.enabled`` →
the Task 1-4 default-plugin feature is dead on upgrade volumes (fresh
volumes work). The fix merges at boot:

- ``backend/scripts/install-plugins.sh`` — install-skills.sh-style
  managed-marker merge of the seed ``plugins/`` tree into
  ``$HERMES_HOME/plugins`` (install / refresh / skip user-owned / prune).
- ``backend/scripts/merge_plugins_config.py`` — idempotent append of the
  seed ``plugins.enabled`` entries missing from the runtime config.yaml;
  every user entry and unrelated key is preserved; the runtime file is
  rewritten only when something changed (byte-stable otherwise).
- ``backend/scripts/docker-entrypoint.sh`` calls both right after the
  install-skills.sh block.

Pure stdlib (subprocess/tempfile/pathlib); behaviour tests run the real
scripts against tmp dirs, static tests read the entrypoint directly.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "backend" / "scripts" / "docker-entrypoint.sh"
INSTALL_PLUGINS = REPO_ROOT / "backend" / "scripts" / "install-plugins.sh"
MERGE_CONFIG = REPO_ROOT / "backend" / "scripts" / "merge_plugins_config.py"

MARKER = ".leap-aide-managed"

# ---------------------------------------------------------------------------
# install-plugins.sh behaviour
# ---------------------------------------------------------------------------


def _run_install(source: Path, home: Path) -> "subprocess.CompletedProcess":
    """Run install-plugins.sh with an explicit source and HERMES_HOME."""
    env = dict(os.environ)
    env["HERMES_HOME"] = str(home)
    env.pop("HERMES_SEED_DIR", None)
    return subprocess.run(
        ["bash", str(INSTALL_PLUGINS), "--source", str(source)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _fixture(tmp_path: Path) -> tuple:
    """(source dir, hermes home, dest plugins root) all under tmp_path."""
    source = tmp_path / "seed_plugins"
    source.mkdir()
    home = tmp_path / "hermes_home"
    home.mkdir()
    return source, home, home / "plugins"


def test_install_missing_dest_installs_and_marks(tmp_path):
    """Destination missing -> cp -a + .leap-aide-managed marker."""
    source, home, dest = _fixture(tmp_path)
    plugin = source / "superpowers"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text("name: superpowers\n", encoding="utf-8")

    proc = _run_install(source, home)
    assert proc.returncode == 0, f"rc={proc.returncode}: {proc.stderr}"

    assert (dest / "superpowers" / "plugin.yaml").is_file(), (
        "source plugin dir must be copied into $HERMES_HOME/plugins"
    )
    assert (dest / "superpowers" / MARKER).is_file(), (
        "freshly installed plugin dir must carry the managed marker"
    )
    assert "install" in proc.stdout, f"expected an install log line: {proc.stdout}"


def test_install_marked_dest_refreshes_from_source(tmp_path):
    """Marker present -> contents refreshed so image updates propagate."""
    source, home, dest = _fixture(tmp_path)
    plugin = source / "superpowers"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text("name: superpowers\n", encoding="utf-8")

    # First run installs.
    assert _run_install(source, home).returncode == 0
    # Image update: file renamed + a stale file left behind in the dest.
    (plugin / "plugin.yaml").write_text("name: superpowers\nv: 2\n", encoding="utf-8")
    (dest / "superpowers" / "stale.txt").write_text("stale\n", encoding="utf-8")

    proc = _run_install(source, home)
    assert proc.returncode == 0, f"rc={proc.returncode}: {proc.stderr}"

    assert "v: 2" in (dest / "superpowers" / "plugin.yaml").read_text(
        encoding="utf-8"
    ), "marker'd dest must be refreshed from source"
    assert not (dest / "superpowers" / "stale.txt").exists(), (
        "refresh must replace contents so removed files do not accumulate"
    )
    assert (dest / "superpowers" / MARKER).is_file(), "marker must survive refresh"
    assert "refresh" in proc.stdout, f"expected a refresh log line: {proc.stdout}"


def test_install_user_owned_dest_is_skipped(tmp_path):
    """Dest exists WITHOUT the marker -> user-owned, never clobbered."""
    source, home, dest = _fixture(tmp_path)
    plugin = source / "superpowers"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text("name: superpowers\n", encoding="utf-8")

    user_dir = dest / "superpowers"
    user_dir.mkdir(parents=True)
    (user_dir / "plugin.yaml").write_text("name: superpowers\nmine: true\n",
                                         encoding="utf-8")

    proc = _run_install(source, home)
    assert proc.returncode == 0, f"rc={proc.returncode}: {proc.stderr}"

    body = (user_dir / "plugin.yaml").read_text(encoding="utf-8")
    assert "mine: true" in body, "user-owned plugin dir must not be overwritten"
    assert not (user_dir / MARKER).exists(), (
        "a skipped user-owned dir must not receive the managed marker"
    )
    assert "skip" in proc.stdout, f"expected a skip log line: {proc.stdout}"


def test_install_prunes_managed_dir_absent_from_source(tmp_path):
    """Marker'd dest whose source disappeared -> pruned (renames/removals)."""
    source, home, dest = _fixture(tmp_path)
    # Seed only has the new name; the volume still holds the old one.
    current = source / "superpowers"
    current.mkdir()
    (current / "plugin.yaml").write_text("name: superpowers\n", encoding="utf-8")

    old = dest / "superpowers-old"
    old.mkdir(parents=True)
    (old / "plugin.yaml").write_text("name: superpowers-old\n", encoding="utf-8")
    (old / MARKER).write_text("", encoding="utf-8")

    proc = _run_install(source, home)
    assert proc.returncode == 0, f"rc={proc.returncode}: {proc.stderr}"

    assert not old.exists(), "managed plugin absent from source must be pruned"
    assert (dest / "superpowers" / "plugin.yaml").is_file(), (
        "plugin present in source must still be installed"
    )
    assert "prune" in proc.stdout, f"expected a prune log line: {proc.stdout}"


def test_install_missing_source_is_a_clean_noop(tmp_path):
    """No seed plugins dir -> exit 0 (entrypoint guard also covers this)."""
    home = tmp_path / "hermes_home"
    home.mkdir()
    missing = tmp_path / "nope"
    env = dict(os.environ)
    env["HERMES_HOME"] = str(home)
    proc = subprocess.run(
        ["bash", str(INSTALL_PLUGINS), "--source", str(missing)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, f"rc={proc.returncode}: {proc.stderr}"


# ---------------------------------------------------------------------------
# merge_plugins_config.py behaviour
# ---------------------------------------------------------------------------

_SEED_CONFIG = """\
plugins:
  enabled:
    - superpowers
    - wiserkit
display:
  show_reasoning: true
"""


def _run_merge(seed: Path, runtime: Path) -> "subprocess.CompletedProcess":
    return subprocess.run(
        [sys.executable, str(MERGE_CONFIG), str(seed), str(runtime)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _merge_fixture(tmp_path: Path, runtime_text: str):
    seed = tmp_path / "seed_config.yaml"
    seed.write_text(_SEED_CONFIG, encoding="utf-8")
    runtime = tmp_path / "config.yaml"
    runtime.write_text(runtime_text, encoding="utf-8")
    return seed, runtime


def test_merge_runtime_without_plugins_key_gains_seed_enabled(tmp_path):
    """Runtime config has no plugins key at all -> gains the seed list."""
    seed, runtime = _merge_fixture(
        tmp_path, "display:\n  show_reasoning: true\n"
    )
    proc = _run_merge(seed, runtime)
    assert proc.returncode == 0, f"rc={proc.returncode}: {proc.stderr}"

    text = runtime.read_text(encoding="utf-8")
    assert "superpowers" in text and "wiserkit" in text, (
        f"seed plugins.enabled must land in the runtime config: {text}"
    )
    # Unrelated keys survive the rewrite.
    assert "show_reasoning: true" in text


def test_merge_partial_overlap_appends_and_preserves_user_entries(tmp_path):
    """User already enabled their own plugin -> seed entries appended, user kept."""
    seed, runtime = _merge_fixture(
        tmp_path,
        "plugins:\n"
        "  enabled:\n"
        "    - my-own-plugin\n"
        "  disabled:\n"
        "    - never-once\n",
    )
    proc = _run_merge(seed, runtime)
    assert proc.returncode == 0, f"rc={proc.returncode}: {proc.stderr}"

    text = runtime.read_text(encoding="utf-8")
    assert "my-own-plugin" in text, "user's enabled entry must be preserved"
    assert "never-once" in text, "user's disabled entry must be preserved"
    assert "superpowers" in text and "wiserkit" in text, (
        f"missing seed entries must be appended: {text}"
    )
    # Order: user's own entry stays first, seed entries are appended after it.
    assert text.index("my-own-plugin") < text.index("superpowers")


def test_merge_already_equal_leaves_bytes_unchanged(tmp_path):
    """Idempotent: when runtime already lists everything, no rewrite happens."""
    seed, runtime = _merge_fixture(
        tmp_path,
        "plugins:\n"
        "  enabled:\n"
        "    - my-own-plugin\n"
        "    - superpowers\n"
        "    - wiserkit\n",
    )
    before = runtime.read_bytes()
    proc = _run_merge(seed, runtime)
    assert proc.returncode == 0, f"rc={proc.returncode}: {proc.stderr}"
    assert runtime.read_bytes() == before, (
        "runtime config must not be rewritten when nothing is missing"
    )


def test_merge_disabled_list_and_unrelated_keys_untouched(tmp_path):
    """A disabled list and foreign top-level keys pass through verbatim."""
    seed, runtime = _merge_fixture(
        tmp_path,
        "display:\n"
        "  show_reasoning: false\n"
        "  theme: dark\n"
        "plugins:\n"
        "  enabled:\n"
        "    - superpowers\n"
        "    - wiserkit\n"
        "  disabled:\n"
        "    - never-once\n",
    )
    proc = _run_merge(seed, runtime)
    assert proc.returncode == 0, f"rc={proc.returncode}: {proc.stderr}"

    text = runtime.read_text(encoding="utf-8")
    assert "never-once" in text, "disabled list must be preserved"
    assert "show_reasoning: false" in text, (
        "unrelated key values must be preserved, not reset to seed defaults"
    )
    assert "theme: dark" in text, "unrelated keys must survive the merge"


# ---------------------------------------------------------------------------
# entrypoint wiring (static)
# ---------------------------------------------------------------------------


def test_entrypoint_invokes_both_merges():
    """docker-entrypoint.sh must call install-plugins.sh and the config merge."""
    assert ENTRYPOINT.is_file(), f"missing {ENTRYPOINT}"
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "install-plugins.sh" in text, (
        "entrypoint must invoke backend/scripts/install-plugins.sh"
    )
    assert "merge_plugins_config.py" in text, (
        "entrypoint must invoke backend/scripts/merge_plugins_config.py"
    )
