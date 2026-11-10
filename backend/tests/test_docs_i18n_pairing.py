"""
Tests for the bilingual-pair consistency gate over docs/ and the repo root
(issue #32).

Filesystem-only — validates that every bilingual pair is complete (English
anchor + Chinese companion + .i18n.yaml record) and that the records still
match the documents. The negative tests copy a pair into a tmp dir and
assert that the checker actually rejects the drift, so a gate that stopped
detecting anything would fail here rather than pass silently.

No server required.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
CHECKER = REPO_ROOT / "backend" / "scripts" / "check_translation_pairing.py"

# Every bilingual pair the gate covers: (English anchor, Chinese companion).
PAIRS = [
    (DOCS_DIR / "architecture.md", DOCS_DIR / "architecture.zh-CN.md"),
    (DOCS_DIR / "roadmap.md", DOCS_DIR / "roadmap.zh-CN.md"),
    (DOCS_DIR / "testing.md", DOCS_DIR / "testing.zh-CN.md"),
    (DOCS_DIR / "release.md", DOCS_DIR / "release.zh-CN.md"),
    (REPO_ROOT / "README.md", REPO_ROOT / "README.zh-CN.md"),
    (REPO_ROOT / "AGENTS.md", REPO_ROOT / "AGENTS.zh-CN.md"),
    (REPO_ROOT / "CONTRIBUTING.md", REPO_ROOT / "CONTRIBUTING.zh-CN.md"),
    (REPO_ROOT / "CHANGELOG.md", REPO_ROOT / "CHANGELOG.zh-CN.md"),
]

pytestmark = pytest.mark.skipif(
    not DOCS_DIR.is_dir(), reason="docs/ not present in this image")


def _run(*args):
    """Run the checker with the given arguments. Returns the CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )


def _run_in(cwd: Path, *args):
    """Run the checker with a different working directory."""
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True, text=True, cwd=str(cwd),
    )


def _record_for(anchor: Path) -> Path:
    return anchor.with_name(anchor.name[: -len(".md")] + ".i18n.yaml")


def _copy_pair(tmp_path: Path, anchor: Path, companion: Path):
    """Copy a pair and its record into tmp_path. Returns the copied anchor."""
    shutil.copy(anchor, tmp_path / anchor.name)
    shutil.copy(companion, tmp_path / companion.name)
    record = _record_for(anchor)
    if record.is_file():
        shutil.copy(record, tmp_path / record.name)
    return tmp_path / anchor.name


# ── completeness ─────────────────────────────────────────────────────────

def test_every_pair_has_all_three_files():
    """Each bilingual pair ships the English file, the Chinese file and the
    consistency record."""
    for anchor, companion in PAIRS:
        assert anchor.is_file(), (
            f"missing {anchor} — inside the testing image the repo-root pair "
            f"must be whitelisted in .dockerignore (see the HARD CONSTRAINTS "
            f"block there), otherwise the image carries a half pair")
        assert companion.is_file(), f"missing {companion}"
        record = _record_for(anchor)
        assert record.is_file(), f"missing {record}"


def test_every_pair_verifies():
    """The checker's verify mode exits 0 for every pair."""
    for anchor, _companion in PAIRS:
        proc = _run(str(anchor))
        assert proc.returncode == 0, (
            f"bilingual pairing gate failed for {anchor}:\n"
            f"{proc.stderr or proc.stdout}")


def test_check_all_passes():
    """--check-all verifies every recorded pair in the repo."""
    proc = _run("--check-all")
    assert proc.returncode == 0, (
        f"--check-all failed:\n{proc.stderr or proc.stdout}")


def test_docs_tree_is_complete():
    """--check-tree docs finds no document missing its companion or record."""
    proc = _run("--check-tree", "docs")
    assert proc.returncode == 0, (
        f"--check-tree docs failed:\n{proc.stderr or proc.stdout}")


def test_root_tree_is_complete():
    """--check-tree . --depth 1 covers the repo-root bilingual files."""
    proc = _run("--check-tree", ".", "--depth", "1")
    assert proc.returncode == 0, (
        f"--check-tree . --depth 1 failed:\n{proc.stderr or proc.stdout}")


def test_check_tree_ignores_gitignored_files(tmp_path):
    """A gitignored file must not fail --check-tree.

    The gate reads the filesystem, so a local scratch file that .gitignore
    lists (CLAUDE.md, copilot-instructions.md) would otherwise be reported
    as a document missing its Chinese companion.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    (repo / ".gitignore").write_text("scratch.md\n", encoding="utf-8")
    (repo / "scratch.md").write_text("# scratch\n", encoding="utf-8")
    (repo / "fresh.md").write_text("# fresh\n", encoding="utf-8")
    proc = _run_in(repo, "--check-tree", ".", "--depth", "1")
    assert "scratch.md" not in proc.stderr, (
        f"a gitignored file was reported:\n{proc.stderr}")
    assert "fresh.md" in proc.stderr, (
        f"a real document was not reported:\n{proc.stderr}")
    assert proc.returncode == 1


# ── document shape ───────────────────────────────────────────────────────

def test_frontmatter_present_on_both_sides():
    """Both sides open with a YAML frontmatter block at byte 0."""
    for anchor, companion in PAIRS:
        for path in (anchor, companion):
            raw = path.read_text(encoding="utf-8")
            assert raw.startswith("---\n"), (
                f"{path} must start with a frontmatter block at byte 0")
            assert re.search(r"^---\n.*?\n---\n", raw, re.DOTALL), (
                f"{path} frontmatter block is not terminated")


def test_switcher_links_present():
    """Each side links to the other within the first 20 lines."""
    for anchor, companion in PAIRS:
        en_raw = anchor.read_text(encoding="utf-8")
        zh_raw = companion.read_text(encoding="utf-8")
        en_head = "\n".join(en_raw.split("\n")[:20])
        zh_head = "\n".join(zh_raw.split("\n")[:20])
        assert f"]({companion.name})" in en_head, (
            f"{anchor} must link to {companion.name} in its first 20 lines")
        assert f"]({anchor.name})" in zh_head, (
            f"{companion} must link to {anchor.name} in its first 20 lines")


def test_records_are_not_gitignored():
    """The docs records must be trackable — docs/* is gitignored, so the
    negation that re-includes *.i18n.yaml has to exist. Without it the
    records are never committed and the gate dies in CI."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!docs/*.i18n.yaml" in gitignore, (
        ".gitignore must re-include docs/*.i18n.yaml — docs/* would "
        "otherwise swallow the consistency records")


def test_dockerignore_keeps_the_repo_root_pair_complete():
    """The testing image must carry the whole repo-root bilingual pair.

    .dockerignore excludes AGENTS.md, and **/.* hides .gitignore, so both
    need an explicit `!` whitelist entry. Without them the image holds a
    half pair (the Chinese side and the record, but not the English
    anchor) and the gate fails inside the image — which is where CI runs it.
    """
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "!AGENTS.md" in dockerignore, (
        ".dockerignore must re-include AGENTS.md — the bilingual-pair test "
        "verifies the complete repo-root pair inside the testing image")
    assert "!.gitignore" in dockerignore, (
        ".dockerignore must re-include .gitignore — the bilingual-pair test "
        "asserts the ignore rules from inside the testing image")


# ── the gate actually rejects drift ──────────────────────────────────────

def test_detects_one_sided_drift(tmp_path):
    """Editing only one language side makes the gate fail."""
    anchor, companion = DOCS_DIR / "roadmap.md", DOCS_DIR / "roadmap.zh-CN.md"
    copied = _copy_pair(tmp_path, anchor, companion)
    with (tmp_path / companion.name).open("a", encoding="utf-8") as fh:
        fh.write("\nA typo that has no counterpart.\n")
    proc = _run(str(copied))
    assert proc.returncode != 0, "one-sided drift was not detected"
    assert "out-of-sync" in proc.stderr, proc.stderr


def test_detects_missing_companion(tmp_path):
    """An English document without its Chinese companion is rejected."""
    anchor = DOCS_DIR / "roadmap.md"
    shutil.copy(anchor, tmp_path / anchor.name)
    proc = _run(str(tmp_path / anchor.name))
    assert proc.returncode != 0, "missing companion was not detected"
    assert "missing Chinese companion" in proc.stderr, proc.stderr


def test_detects_missing_record(tmp_path):
    """A pair without a consistency record is rejected."""
    anchor, companion = DOCS_DIR / "roadmap.md", DOCS_DIR / "roadmap.zh-CN.md"
    shutil.copy(anchor, tmp_path / anchor.name)
    shutil.copy(companion, tmp_path / companion.name)
    proc = _run(str(tmp_path / anchor.name))
    assert proc.returncode != 0, "missing record was not detected"
    assert "missing record file" in proc.stderr, proc.stderr


def test_detects_missing_switcher(tmp_path):
    """A pair whose switcher link is gone is rejected."""
    anchor, companion = DOCS_DIR / "roadmap.md", DOCS_DIR / "roadmap.zh-CN.md"
    copied = _copy_pair(tmp_path, anchor, companion)
    for path in (tmp_path / anchor.name, tmp_path / companion.name):
        raw = path.read_text(encoding="utf-8")
        lines = [line for line in raw.split("\n") if "](" not in line]
        path.write_text("\n".join(lines), encoding="utf-8")
    proc = _run(str(copied))
    assert proc.returncode != 0, "missing switcher was not detected"
    assert "language switcher" in proc.stderr, proc.stderr


def test_detects_missing_frontmatter(tmp_path):
    """A pair without frontmatter is rejected."""
    anchor, companion = DOCS_DIR / "roadmap.md", DOCS_DIR / "roadmap.zh-CN.md"
    copied = _copy_pair(tmp_path, anchor, companion)
    for path in (tmp_path / anchor.name, tmp_path / companion.name):
        raw = path.read_text(encoding="utf-8")
        path.write_text(raw.split("---\n", 2)[-1].lstrip("\n"), encoding="utf-8")
    proc = _run(str(copied))
    assert proc.returncode != 0, "missing frontmatter was not detected"
    assert "frontmatter must start at byte 0" in proc.stderr, proc.stderr


def test_detects_new_doc_without_chinese(tmp_path):
    """--check-tree catches a document added without its Chinese side."""
    tree = tmp_path / "docs"
    tree.mkdir()
    (tree / "fresh.md").write_text(
        "# Fresh\n\nBrand new document.\n", encoding="utf-8")
    proc = _run("--check-tree", str(tree))
    assert proc.returncode != 0, "incomplete document was not detected"
    assert "missing Chinese companion" in proc.stderr, proc.stderr
    assert "missing consistency record" in proc.stderr, proc.stderr


# ── repeated section titles ─────────────────────────────────────────────

def test_repeated_section_titles_are_kept_apart(tmp_path):
    """Two sections may carry the same title under different parents.

    A changelog gives every release its own "Added" / "Changed" / "Fixed",
    so the second release repeats the titles of the first. The record keys
    are heading paths, not bare slugs — with flat slugs the second release
    collided and the gate died on a duplicate-slug error, which is exactly
    what a release cut would hit.
    """
    anchor = tmp_path / "CHANGELOG.md"
    companion = tmp_path / "CHANGELOG.zh-CN.md"
    anchor.write_text(
        "---\ntitle: Changelog\n---\n\n"
        '<div align="center">\n\n**English** | [中文](CHANGELOG.zh-CN.md)\n\n'
        "</div>\n\n"
        "# Changelog\n\n"
        "## [0.1.0] - 2026-01-01\n\nFirst release.\n\n"
        "### Added\n\n- One.\n\n### Fixed\n\n- Two.\n\n"
        "## [0.2.0] - 2026-02-01\n\nSecond release.\n\n"
        "### Added\n\n- Three.\n\n### Fixed\n\n- Four.\n",
        encoding="utf-8")
    companion.write_text(
        "---\ntitle: 更新日志\n---\n\n"
        '<div align="center">\n\n[English](CHANGELOG.md) | **中文**\n\n'
        "</div>\n\n"
        "# 更新日志\n\n"
        "## [0.1.0] - 2026-01-01\n\n第一次发布。\n\n"
        "### Added\n\n- 一。\n\n### Fixed\n\n- 二。\n\n"
        "## [0.2.0] - 2026-02-01\n\n第二次发布。\n\n"
        "### Added\n\n- 三。\n\n### Fixed\n\n- 四。\n",
        encoding="utf-8")

    write = _run_in(tmp_path, "--write", "CHANGELOG.md")
    assert write.returncode == 0, write.stderr
    verify = _run_in(tmp_path, "CHANGELOG.md")
    assert verify.returncode == 0, verify.stderr

    record = (tmp_path / "CHANGELOG.i18n.yaml").read_text(encoding="utf-8")
    assert "010-2026-01-01/added" in record, record
    assert "020-2026-02-01/added" in record, record


# ── the waiver escape hatch ─────────────────────────────────────────────

def _waived_record(tmp_path, anchor, companion, side, expires):
    """Copy a pair and stamp a waiver stanza onto its record."""
    copied = _copy_pair(tmp_path, anchor, companion)
    record = tmp_path / _record_for(anchor).name
    raw = record.read_text(encoding="utf-8")
    stanza = (f"waiver:\n  side: {side}\n  reason: \"typo fix only\"\n"
              f"  expires: \"{expires}\"\n")
    marker = "/:\n"
    raw = raw.replace(marker, stanza + marker, 1)
    record.write_text(raw, encoding="utf-8")
    return copied


def test_waiver_is_parsed_and_preserved(tmp_path):
    """A valid waiver survives a --write re-record."""
    anchor, companion = DOCS_DIR / "roadmap.md", DOCS_DIR / "roadmap.zh-CN.md"
    copied = _waived_record(tmp_path, anchor, companion, "zh", "2099-01-01")
    proc = _run("--write", str(copied))
    assert proc.returncode == 0, proc.stderr
    record = (tmp_path / _record_for(anchor).name).read_text(encoding="utf-8")
    assert "waiver:" in record, "the waiver was dropped by --write"
    assert "side: zh" in record, record
    # The waiver does not excuse the digests: they still have to match.
    verify = _run(str(copied))
    assert verify.returncode == 0, verify.stderr


def test_expired_waiver_is_a_violation(tmp_path):
    """An expired waiver is reported — it cannot linger unnoticed."""
    anchor, companion = DOCS_DIR / "roadmap.md", DOCS_DIR / "roadmap.zh-CN.md"
    copied = _waived_record(tmp_path, anchor, companion, "zh", "2000-01-01")
    proc = _run(str(copied))
    assert proc.returncode != 0, "expired waiver was not detected"
    assert "waiver expired" in proc.stderr, proc.stderr


def test_malformed_waiver_is_a_violation(tmp_path):
    """A waiver pointing at no side is reported."""
    anchor, companion = DOCS_DIR / "roadmap.md", DOCS_DIR / "roadmap.zh-CN.md"
    copied = _copy_pair(tmp_path, anchor, companion)
    record = tmp_path / _record_for(anchor).name
    raw = record.read_text(encoding="utf-8")
    raw = raw.replace("/:\n",
                      "waiver:\n  side: sideways\n  reason: \"x\"\n"
                      "  expires: \"2099-01-01\"\n/:\n", 1)
    record.write_text(raw, encoding="utf-8")
    proc = _run(str(copied))
    assert proc.returncode != 0, "malformed waiver was not detected"
    assert "side must be" in proc.stderr, proc.stderr
