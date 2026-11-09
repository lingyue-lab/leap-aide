#!/usr/bin/env python3
"""release_notes.py — render a bilingual GitHub release body from the changelog.

The changelog is the single source of truth for what a release contains.
GitHub can generate release notes from merged pull requests, but that view is
pull-request-level and monolingual: it says what the pull requests were
called, not what the user gets. This script takes the other side of that
bargain — the curated, bilingual `## [x.y.z]` section of CHANGELOG.md and
CHANGELOG.zh-CN.md — and renders it as the release body, so the release page
and the changelog cannot drift apart.

The body is bilingual with a click-to-switch header. GitHub strips <script>
and <style> from release bodies, so a strict tab widget is not available.
The switch is therefore built from two <details> blocks plus anchor links,
which is as close to a toggle as a static release body gets:

    [English](#...)  |  [中文](#...)

The English block starts open, so the page is readable without a click; the
Chinese block starts closed, and the switcher jumps to its disclosure bar.

Usage:
    python3 scripts/release_notes.py --version 0.1.0
    python3 scripts/release_notes.py --version 0.1.0 --out notes.md
    python3 scripts/release_notes.py --version v0.1.0 --lang zh
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# A level-2 heading. The version, when the heading carries one, sits in
# square brackets: "## [0.1.0] - 2026-11-09".
SECTION_HEADING_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
BRACKETED_RE = re.compile(r"^\[(?P<version>[^\]]+)\]")

# Anchor ids for the language switcher. GitHub rewrites ids it renders
# ("user-content-" prefix) and rewrites the matching fragments, so the
# switcher links follow. The prefix keeps them clear of the ids GitHub
# generates for the headings inside the release body.
EN_ANCHOR = "leap-aide-release-notes-en"
ZH_ANCHOR = "leap-aide-release-notes-zh"

SWITCHER = (
    '<div align="center">\n'
    '\n'
    f'**[English](#{EN_ANCHOR})** · **[中文](#{ZH_ANCHOR})**\n'
    '\n'
    '</div>'
)


def repo_root(explicit: str | None = None) -> Path:
    """Return the repository root: the directory holding CHANGELOG.md."""
    if explicit:
        return Path(explicit).resolve()
    return Path(__file__).resolve().parent.parent


def parse_sections(text: str) -> list[tuple[str, str]]:
    """Split a changelog into (version_key, body) pairs.

    The version key is the bracketed content of a level-2 heading when it has
    one ("## [0.1.0] - 2026-11-09" -> "0.1.0"), and the raw heading text
    otherwise ("## [Unreleased]" -> "Unreleased"). The body is everything
    between the heading and the next level-2 heading, with the surrounding
    blank lines stripped. The preamble above the first section (frontmatter,
    language switcher) is not part of any section.
    """
    sections: list[tuple[str, str]] = []
    key: str | None = None
    body: list[str] = []

    for line in text.split("\n"):
        heading = SECTION_HEADING_RE.match(line)
        if heading:
            if key is not None:
                sections.append((key, "\n".join(body).strip("\n")))
            title = heading.group("title")
            bracketed = BRACKETED_RE.match(title)
            key = bracketed.group("version") if bracketed else title
            body = []
        elif key is not None:
            body.append(line)
    if key is not None:
        sections.append((key, "\n".join(body).strip("\n")))
    return sections


def normalize_version(version: str) -> str:
    """Accept both "0.1.0" and "v0.1.0"."""
    version = version.strip()
    return version[1:] if version.startswith("v") else version


def find_section(text: str, version: str) -> str | None:
    """Return the body of the section for `version`, or None if absent."""
    wanted = normalize_version(version)
    for key, body in parse_sections(text):
        if normalize_version(key) == wanted:
            return body
    return None


def available_versions(text: str) -> list[str]:
    """The version keys the changelog carries, in file order."""
    return [key for key, _ in parse_sections(text)]


def render_bilingual(en_body: str, zh_body: str) -> str:
    """Render the two language sections into one release body."""
    en_block = (
        '<details open>\n'
        f'<summary><a id="{EN_ANCHOR}"></a><b>English</b></summary>\n'
        '\n'
        f'{en_body}\n'
        '\n'
        '</details>'
    )
    zh_block = (
        '<details>\n'
        f'<summary><a id="{ZH_ANCHOR}"></a><b>中文</b></summary>\n'
        '\n'
        f'{zh_body}\n'
        '\n'
        '</details>'
    )
    return f"{SWITCHER}\n\n{en_block}\n\n{zh_block}\n"


def render_single(body: str, lang: str) -> str:
    """Render one language section on its own (no switcher)."""
    label = "English" if lang == "en" else "中文"
    anchor = EN_ANCHOR if lang == "en" else ZH_ANCHOR
    return (
        '<details open>\n'
        f'<summary><a id="{anchor}"></a><b>{label}</b></summary>\n'
        '\n'
        f'{body}\n'
        '\n'
        '</details>'
    )


def git_tags(root: Path) -> list[str]:
    """Version tags in the repository, newest version first.

    An empty list when git is unavailable or this is not a checkout — the
    compare link is a nicety, not a requirement.
    """
    try:
        proc = subprocess.run(
            ["git", "tag", "--list", "v*", "--sort=-v:refname"],
            cwd=str(root), capture_output=True, text=True,
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.split("\n") if line.strip()]


def compare_link(root: Path, repo_url: str, version: str) -> str | None:
    """A "Full Changelog" link comparing the previous tag with this one."""
    current = f"v{normalize_version(version)}"
    previous = next((t for t in git_tags(root) if t != current), None)
    if previous is None:
        return None
    base = repo_url.rstrip("/")
    return (f"**Full Changelog**: {base}/compare/{previous}...{current}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a bilingual GitHub release body from the changelog.")
    parser.add_argument(
        "--version", required=True,
        help="the release version, e.g. 0.1.0 or v0.1.0")
    parser.add_argument(
        "--lang", choices=("both", "en", "zh"), default="both",
        help="which language section(s) to render (default: both)")
    parser.add_argument(
        "--out", help="write the body to this file instead of stdout")
    parser.add_argument(
        "--repo-root", help="repository root (default: the checkout of this script)")
    parser.add_argument(
        "--repo-url",
        help="repository web URL, e.g. https://github.com/owner/repo — "
             "enables the \"Full Changelog\" compare link")
    args = parser.parse_args(argv)

    root = repo_root(args.repo_root)
    changelog = root / "CHANGELOG.md"
    changelog_zh = root / "CHANGELOG.zh-CN.md"
    for path in (changelog, changelog_zh):
        if not path.is_file():
            print(f"error: {path} not found — run this from a LeapAide checkout",
                  file=sys.stderr)
            return 1

    en_text = changelog.read_text(encoding="utf-8")
    zh_text = changelog_zh.read_text(encoding="utf-8")

    en_body = find_section(en_text, args.version)
    zh_body = find_section(zh_text, args.version)
    if en_body is None or zh_body is None:
        missing = []
        if en_body is None:
            missing.append("CHANGELOG.md")
        if zh_body is None:
            missing.append("CHANGELOG.zh-CN.md")
        print(f"error: no section for version {args.version} in "
              f"{', '.join(missing)}", file=sys.stderr)
        print("known versions: "
              + ", ".join(available_versions(en_text)), file=sys.stderr)
        return 1

    if args.lang == "en":
        body = render_single(en_body, "en")
    elif args.lang == "zh":
        body = render_single(zh_body, "zh")
    else:
        body = render_bilingual(en_body, zh_body)

    if args.repo_url:
        link = compare_link(root, args.repo_url, args.version)
        if link:
            body = f"{body}\n---\n\n{link}\n"

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        print(f"wrote {out}", file=sys.stderr)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
