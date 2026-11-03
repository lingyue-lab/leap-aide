"""
API coverage for the absolute-path workspace file endpoints that back the
Workspace panel's file browser (issue #42).

These endpoints serve right-click Edit/Save, New File and New Folder in the
UI. The frontend used to discard the backend ``{"error": ...}`` body, so a
permission failure surfaced as the generic "Failed to save file"; these tests
pin the status codes and error text the UI now relies on.
"""
import os
import stat

import pytest

from tests._test_helpers import get, get_raw, post


_IS_ROOT = os.geteuid() == 0
_SKIP_IF_ROOT = pytest.mark.skipif(
    _IS_ROOT,
    reason="chmod-based unwritable checks are bypassed by uid=0; rerun as non-root",
)


# ── Save / read ────────────────────────────────────────────────────────────

def test_write_file_round_trip(tmp_path):
    """POST /api/workspace/file writes; GET reads back the same content."""
    target = tmp_path / "note.txt"

    body, status = post("/api/workspace/file", {"path": str(target), "content": "hello"})
    assert status == 200
    assert body["ok"] is True
    assert target.read_text(encoding="utf-8") == "hello"

    raw, content_type, status = get_raw(f"/api/workspace/file?path={target}")
    assert status == 200
    assert content_type.startswith("text/plain")
    assert raw == b"hello"


def test_write_file_requires_path():
    """A missing path is a 400, matching bad()'s default status."""
    body, status = post("/api/workspace/file", {"path": "", "content": "x"})
    assert status == 400
    assert "path is required" in body["error"]


# ── New file / folder ──────────────────────────────────────────────────────

def test_create_file_and_directory(tmp_path):
    """New File and New Folder both succeed at an absolute path."""
    new_file = tmp_path / "created.txt"
    body, status = post("/api/workspace/new", {"path": str(new_file), "is_dir": False})
    assert status == 200
    assert body["ok"] is True
    assert new_file.is_file()

    new_dir = tmp_path / "created-dir"
    body, status = post("/api/workspace/new", {"path": str(new_dir), "is_dir": True})
    assert status == 200
    assert body["is_dir"] is True
    assert new_dir.is_dir()


def test_create_existing_returns_409(tmp_path):
    """Creating something that already exists is a 409."""
    target = tmp_path / "exists.txt"
    target.write_text("x", encoding="utf-8")

    body, status = post("/api/workspace/new", {"path": str(target), "is_dir": False})
    assert status == 409
    assert body["error"] == "Already exists"


def test_blocked_system_path_returns_403(tmp_path):
    """Blocked roots are rejected before touching the filesystem."""
    blocked = "/etc/leap-aide-issue42-test.txt"
    body, status = post("/api/workspace/new", {"path": blocked, "is_dir": False})
    assert status == 403
    assert body["error"] == "Path not allowed"
    assert not os.path.exists(blocked)


@_SKIP_IF_ROOT
def test_unwritable_directory_returns_sanitized_500(tmp_path):
    """EACCES surfaces as 500 with the absolute path stripped (#42)."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x: traversable, not writable
    try:
        body, status = post(
            "/api/workspace/new",
            {"path": str(locked / "nope.txt"), "is_dir": False},
        )
        assert status == 500
        assert "Permission denied" in body["error"]
        # _sanitize_error must not leak the absolute path to the client.
        assert str(locked) not in body["error"]
    finally:
        locked.chmod(stat.S_IRWXU)


# ── Move / delete ──────────────────────────────────────────────────────────

def test_move_file(tmp_path):
    """Rename/move succeeds; collisions and missing sources are rejected."""
    src = tmp_path / "src.txt"
    src.write_text("x", encoding="utf-8")
    dst = tmp_path / "dst.txt"

    body, status = post("/api/workspace/move", {"src": str(src), "dst": str(dst)})
    assert status == 200
    assert dst.is_file() and not src.exists()

    body, status = post("/api/workspace/move", {"src": str(dst), "dst": str(dst)})
    assert status == 400  # same src/dst is rejected

    body, status = post("/api/workspace/move", {"src": str(tmp_path / "missing"), "dst": str(tmp_path / "other")})
    assert status == 404
    assert body["error"] == "Source not found"


def test_delete_file_and_missing(tmp_path):
    """Delete removes the target; deleting a missing path is a 404."""
    target = tmp_path / "gone.txt"
    target.write_text("x", encoding="utf-8")

    body, status = post("/api/workspace/delete", {"path": str(target)})
    assert status == 200
    assert not target.exists()

    body, status = post("/api/workspace/delete", {"path": str(target)})
    assert status == 404
    assert body["error"] == "Not found"


# ── List ───────────────────────────────────────────────────────────────────

def test_list_directory(tmp_path):
    """The browser's listing endpoint returns absolute child paths."""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    body, status = get(f"/api/workspace/list?path={tmp_path}")
    assert status == 200
    entries = {e["name"]: e for e in body["entries"]}
    assert entries["a.txt"]["is_dir"] is False
    assert entries["sub"]["is_dir"] is True
    assert entries["a.txt"]["path"] == str(tmp_path / "a.txt")
