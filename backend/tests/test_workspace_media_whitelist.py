"""Tests: /api/media per-session image whitelist (workspace-state redesign).

Contract:
  roots(sid) = {HERMES_HOME}/{images,screenshots,cache} ∪ {/tmp} ∪ resolve_cwd(sid)
  resolve_cwd: state.db row.cwd → empty → gateway cwd fallback.
  Image-extension allowlist + 25 MiB cap (Hermes SDK parity).
  missing session_id → 400, unknown session → 404, non-image → 415.

Denial cases rely on the default test layout (TEST_STATE_DIR under
$HOME/.hermes, outside /tmp): a file directly under the HERMES_HOME root —
not in its {images,screenshots,cache} subtrees — is outside every root.

cwd determinism: POST /api/session/workspace performs a synchronous state.db
UPDATE, so the roots lookup never races new_session's async cwd write.
"""
import uuid
import urllib.error
from pathlib import Path
from urllib.parse import quote

from tests._test_helpers import (
    make_session_tracked,
    get_raw,
    get_raw_with_headers,
    post,
)
from tests.conftest import TEST_STATE_DIR

# 1×1 pixel PNG (same bytes as test_api_file_raw_serving.py)
PNG = (b"\x89PNG\r\n\x1a\n" b"\x00\x00\x00\rIHDR\x00\x00\x00\x01"
       b"\x00\x00\x00\x01\x08\x02\x00\x00\x00"
       b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc"
       b"\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
       b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def _media_url(path, sid) -> str:
    return f"/api/media?path={quote(str(path))}&session_id={quote(str(sid))}"


def _expect_status(url: str, expected: int) -> None:
    """GET url, asserting the HTTP status (works for 2xx and error codes)."""
    try:
        _, _, status = get_raw(url)
        assert status == expected, f"expected {expected}, got {status}"
    except urllib.error.HTTPError as e:
        assert e.code == expected, f"expected {expected}, got {e.code}"


def _pin_cwd(sid, workspace) -> None:
    """Assign the workspace synchronously so state.db cwd is set before the
    media request (new_session's cwd write runs on a daemon thread)."""
    d, status = post(
        "/api/session/workspace",
        {"session_id": sid, "workspace": str(workspace)},
    )
    assert status == 200, d


# ── Allowed: session cwd ────────────────────────────────────────────────────

def test_media_served_from_session_cwd(cleanup_test_sessions):
    sid, ws = make_session_tracked(cleanup_test_sessions)
    _pin_cwd(sid, ws)
    (ws / "shot.png").write_bytes(PNG)

    raw, ct, status = get_raw(_media_url(ws / "shot.png", sid))
    assert status == 200
    assert "image/png" in ct
    assert raw == PNG


# ── Parameter / session guards ──────────────────────────────────────────────

def test_media_requires_session_id(cleanup_test_sessions):
    _, ws = make_session_tracked(cleanup_test_sessions)
    (ws / "shot.png").write_bytes(PNG)

    _expect_status(f"/api/media?path={quote(str(ws / 'shot.png'))}", 400)


def test_media_unknown_session_returns_404(cleanup_test_sessions):
    _, ws = make_session_tracked(cleanup_test_sessions)
    (ws / "shot.png").write_bytes(PNG)

    _expect_status(
        _media_url(ws / "shot.png", "no-such-session"), 404
    )


# ── Denials: outside every root ─────────────────────────────────────────────

def test_media_denied_outside_roots(cleanup_test_sessions):
    """HERMES_HOME root itself (not images/screenshots/cache, not the session
    cwd, not /tmp) is outside the whitelist."""
    sid, ws = make_session_tracked(cleanup_test_sessions)
    _pin_cwd(sid, ws)
    outsider = TEST_STATE_DIR / f"outsider-{uuid.uuid4().hex}.png"
    outsider.write_bytes(PNG)
    try:
        _expect_status(_media_url(outsider, sid), 403)
    finally:
        outsider.unlink(missing_ok=True)


def test_media_denied_for_wrong_session(cleanup_test_sessions):
    """Session B's workspace is not on session A's whitelist — each session
    only sees its own cwd plus the shared roots."""
    ws_b = TEST_STATE_DIR / f"ws-b-{uuid.uuid4().hex}"
    ws_b.mkdir(parents=True)
    try:
        sid_a, ws_a = make_session_tracked(cleanup_test_sessions)
        sid_b, _ = make_session_tracked(cleanup_test_sessions, ws=ws_b)
        _pin_cwd(sid_a, ws_a)
        _pin_cwd(sid_b, ws_b)
        (ws_b / "only-b.png").write_bytes(PNG)

        # Owner session: allowed
        raw, ct, status = get_raw(_media_url(ws_b / "only-b.png", sid_b))
        assert status == 200
        assert raw == PNG
        # Other session: 403 (ws_b is outside /tmp and HERMES_HOME media dirs)
        _expect_status(_media_url(ws_b / "only-b.png", sid_a), 403)
    finally:
        if ws_b.is_dir() and not any(ws_b.iterdir()):
            ws_b.rmdir()


# ── Shared roots: HERMES_HOME media dirs and /tmp ───────────────────────────

def test_media_served_from_hermes_home_screenshots(cleanup_test_sessions):
    """{HERMES_HOME}/{images,screenshots,cache} is whitelisted regardless of
    the session's cwd."""
    sid, ws = make_session_tracked(cleanup_test_sessions)
    _pin_cwd(sid, ws)
    shots = TEST_STATE_DIR / "screenshots"
    shots.mkdir(exist_ok=True)
    shot = shots / f"s-{uuid.uuid4().hex}.png"
    shot.write_bytes(PNG)
    try:
        raw, _, status = get_raw(_media_url(shot, sid))
        assert status == 200
        assert raw == PNG
    finally:
        shot.unlink(missing_ok=True)


def test_media_served_from_tmp(cleanup_test_sessions):
    """/tmp is a shared root for every session."""
    sid, ws = make_session_tracked(cleanup_test_sessions)
    _pin_cwd(sid, ws)
    tmp_png = Path("/tmp") / f"leap-media-{uuid.uuid4().hex}.png"
    tmp_png.write_bytes(PNG)
    try:
        raw, _, status = get_raw(_media_url(tmp_png, sid))
        assert status == 200
        assert raw == PNG
    finally:
        tmp_png.unlink(missing_ok=True)


# ── Content policy: images only, size cap, SVG attachment ───────────────────

def test_media_non_image_rejected(cleanup_test_sessions):
    """Non-image extensions are rejected outright (415), not served as
    downloads."""
    sid, ws = make_session_tracked(cleanup_test_sessions)
    _pin_cwd(sid, ws)
    (ws / "notes.txt").write_text("not an image", encoding="utf-8")

    _expect_status(_media_url(ws / "notes.txt", sid), 415)


def test_media_oversize_rejected(cleanup_test_sessions):
    """Files above the SDK's 25 MiB cap → 413. Sparse file: instant to
    create, and st_size is all the cap checks."""
    sid, ws = make_session_tracked(cleanup_test_sessions)
    _pin_cwd(sid, ws)
    big = ws / "big.png"
    with big.open("wb") as fh:
        fh.truncate(25 * 1024 * 1024 + 1)
    try:
        _expect_status(_media_url(big, sid), 413)
    finally:
        big.unlink(missing_ok=True)


def test_media_svg_forced_attachment(cleanup_test_sessions):
    """SVG is served as attachment (XSS risk when rendered inline)."""
    sid, ws = make_session_tracked(cleanup_test_sessions)
    _pin_cwd(sid, ws)
    (ws / "icon.svg").write_bytes(
        b"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"1\" height=\"1\"/>"
    )

    _, headers, status = get_raw_with_headers(_media_url(ws / "icon.svg", sid))
    assert status == 200
    assert "attachment" in headers.get("Content-Disposition", "")
