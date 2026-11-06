"""Unit tests for the add-workspace directory autocomplete (issue #22).

``list_workspace_suggestions`` must offer exactly the directories that
``validate_workspace_to_add`` accepts: any existing directory that is not
inside a blocked system root.  When the two disagree the form either hides a
path the user may add, or offers a path the user may not add — the "two
calibers" problem this PR set out to remove.

These are pure unit tests (no HTTP server round-trip): they exercise the
resolution rules directly.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import workspace as workspace_api
from app.api.workspace import (
    list_workspace_suggestions,
    validate_workspace_to_add,
)


def _dirs(paths):
    """Return *paths* as a set of resolved strings."""
    return {str(Path(p).resolve()) for p in paths}


# ── Blocked system roots stay blocked ─────────────────────────────────────


class TestSystemRootsNotSuggested:
    @pytest.mark.parametrize("prefix", ["/etc/", "/usr/", "/bin/", "/proc/"])
    def test_system_root_returns_no_suggestions(self, prefix):
        """A blocked system root must never be enumerated."""
        assert list_workspace_suggestions(prefix) == []

    def test_filesystem_root_is_listed(self):
        """The filesystem root itself is not blocked — "/" lists top level."""
        result = list_workspace_suggestions("/")
        assert result, "listing '/' should return the top-level directories"
        # …and no blocked root slips through the listing.
        for entry in result:
            assert not Path(entry).is_relative_to("/etc")
            assert not Path(entry).is_relative_to("/usr")


# ── Hidden directories and the trailing-dot query ──────────────────────────


class TestHiddenDirectories:
    def test_hidden_entries_are_listed(self, tmp_path):
        """A workspace may legitimately live in a dot-directory."""
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "visible").mkdir()
        result = list_workspace_suggestions(str(tmp_path) + "/")
        names = {Path(p).name for p in result}
        assert ".hidden" in names
        assert "visible" in names

    def test_trailing_dot_lists_only_hidden_entries(self, tmp_path):
        """A trailing "." must not be normalised away by Path()."""
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "visible").mkdir()
        result = list_workspace_suggestions(str(tmp_path) + "/.")
        names = {Path(p).name for p in result}
        assert names == {".hidden"}

    def test_prefix_filters_entries(self, tmp_path):
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        result = list_workspace_suggestions(str(tmp_path) + "/al")
        assert [Path(p).name for p in result] == ["alpha"]

    def test_limit_truncates_the_list(self, tmp_path):
        for i in range(5):
            (tmp_path / f"dir{i}").mkdir()
        assert len(list_workspace_suggestions(str(tmp_path) + "/", limit=3)) == 3


# ── Home directory under a blocked system root (systemd-homed) ────────────


class TestHomeExemption:
    """``validate_workspace_to_add`` trusts ``Path.home()`` before the
    blocklist, because systemd-homed puts ``$HOME`` under ``/var/home`` on
    Fedora/RHEL.  The menu must use the same order, otherwise the
    autocomplete is permanently empty on those machines while Add works."""

    @pytest.fixture
    def home_under_blocked_root(self, tmp_path, monkeypatch):
        blocked_root = tmp_path / "var"
        home = blocked_root / "home" / "user"
        (home / "child").mkdir(parents=True)
        (blocked_root / "other").mkdir(parents=True)

        monkeypatch.setenv("HOME", str(home))
        # Isolate the blocklist: the tmp tree must not be carved out by the
        # per-user tmp prefixes, or the exemption would never be exercised.
        monkeypatch.setattr(workspace_api, "_USER_TMP_PREFIXES", ())
        monkeypatch.setattr(
            workspace_api, "_workspace_blocked_roots", lambda: (blocked_root,)
        )
        return home.resolve()

    def test_home_is_suggested(self, home_under_blocked_root):
        result = list_workspace_suggestions(str(home_under_blocked_root) + "/")
        assert _dirs(result) == _dirs([home_under_blocked_root / "child"])

    def test_home_child_is_suggested(self, home_under_blocked_root):
        result = list_workspace_suggestions(
            str(home_under_blocked_root / "child") + "/"
        )
        assert result == []

    def test_add_accepts_the_same_path(self, home_under_blocked_root):
        """The two calibers agree: what the menu offers, Add accepts."""
        child = home_under_blocked_root / "child"
        assert validate_workspace_to_add(str(child)) == child
        assert str(child) in _dirs(
            list_workspace_suggestions(str(home_under_blocked_root) + "/")
        )

    def test_non_home_path_under_blocked_root_stays_blocked(self, home_under_blocked_root):
        """The exemption covers the home tree only, not the whole root."""
        other = home_under_blocked_root.parent.parent / "other"
        assert list_workspace_suggestions(str(other) + "/") == []


# ── Relative prefixes resolve the same way Add resolves them ──────────────


class TestRelativePrefixMatchesAdd:
    def test_bare_word_resolves_against_cwd(self, tmp_path, monkeypatch):
        """A bare word ("proj") must resolve the way ``validate_workspace_to_add``
        resolves it — against the process cwd, not $HOME."""
        (tmp_path / "proj").mkdir()
        monkeypatch.chdir(tmp_path)

        result = list_workspace_suggestions("proj")
        assert _dirs(result) == _dirs([tmp_path / "proj"])
        assert validate_workspace_to_add("proj") == (tmp_path / "proj").resolve()

    def test_relative_prefix_with_slash_resolves_against_cwd(self, tmp_path, monkeypatch):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "proj").mkdir()
        monkeypatch.chdir(tmp_path)

        result = list_workspace_suggestions("sub/proj")
        assert _dirs(result) == _dirs([tmp_path / "sub" / "proj"])


# ── Every suggestion must be addable ───────────────────────────────────────


class TestSuggestionsAreAddable:
    def test_every_suggestion_passes_validate(self, tmp_path):
        for name in ("alpha", ".beta", "gamma"):
            (tmp_path / name).mkdir()

        for suggestion in list_workspace_suggestions(str(tmp_path) + "/"):
            # Must not raise — the menu may only offer addable paths.
            assert validate_workspace_to_add(suggestion) is not None

    def test_empty_prefix_returns_nothing(self):
        assert list_workspace_suggestions("") == []
        assert list_workspace_suggestions("   ") == []

    def test_symlink_to_filesystem_root_is_not_suggested(self, tmp_path):
        """``/`` is listable as a *parent*, but it is not addable — a symlink
        pointing at it inside a listed directory must stay filtered."""
        (tmp_path / "real").mkdir()
        try:
            (tmp_path / "rootlink").symlink_to("/")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available on this platform")

        result = list_workspace_suggestions(str(tmp_path) + "/")

        assert _dirs(result) == _dirs([tmp_path / "real"])
        with pytest.raises(ValueError):
            validate_workspace_to_add("/")
