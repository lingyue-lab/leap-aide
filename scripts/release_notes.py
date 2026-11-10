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

Markdown in this repository is written one line per paragraph and per list
item, with no hard wrapping. GitHub renders a release body with hard breaks
on — every newline in the body becomes a line break — so a body that kept a
wrapped changelog would land as a narrow ribbon of short lines inside a
column twice as wide, with an empty gutter beside them. The renderer
therefore unwraps the prose before it hands the body over: a paragraph
becomes one line, and a list item absorbs its continuation lines. Structure
is left alone.

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
# A fenced code block. Its contents are not markup: a "## " line inside a
# fence is code, not a heading, and must not end the section it sits in.
FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})")
# A list item: an optional indent, a marker ("-", "*", "+", "1.", "1)") and
# the text after it. The gap after the marker is required, so "---" (a
# rule) and "*emphasis*" are not mistaken for items.
LIST_ITEM_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:[-*+]|\d+[.)])[ \t]+)(?P<text>.*)$")
# A thematic break ("---", "***") or a setext heading underline ("===").
RULE_RE = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,}|={2,})[ \t]*$")
# An ATX heading. A heading is a line of its own: the text under it must
# never be joined into it.
HEADING_RE = re.compile(r"^#{1,6}\s")
# A table row, a blockquote and an HTML block carry structure, not prose:
# their lines keep their own place.
TABLE_ROW_RE = re.compile(r"^[ \t]*\|")
BLOCKQUOTE_RE = re.compile(r"^[ \t]*>")
HTML_BLOCK_RE = re.compile(r"^[ \t]*<")
# Chinese text. A line break inside it is not a word boundary, so joining
# the lines with a space would put a gap in the middle of a sentence. The
# punctuation (、。，（）) is listed apart: a fullwidth comma already carries
# the space it needs, so nothing follows it either.
CJK_RE = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff"
    "\u3000-\u303f\uff00-\uffef]")
CJK_PUNCT_RE = re.compile("[\u3000-\u303f\uff00-\uffef]")
# Anchor ids for the language switcher. GitHub renders an element id with a
# "user-content-" prefix ("leap-aide-release-notes-en" becomes
# "user-content-leap-aide-release-notes-en") and leaves the fragment of a
# link alone. The switch therefore relies on GitHub's own fallback: on
# click, the frontend retries the fragment with the prefix
# ("#x" -> "#user-content-x") and scrolls to the element when it finds it.
# The prefix here keeps these ids clear of the ids GitHub generates for the
# headings inside the release body.
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


def _is_closing_fence(line: str, open_marker: str) -> bool:
    """True when the line closes the fence opened with `open_marker`."""
    stripped = line.strip()
    if not stripped:
        return False
    return (set(stripped) == {open_marker[0]}
            and len(stripped) >= len(open_marker))


def parse_sections(text: str) -> list[tuple[str, str]]:
    """Split a changelog into (version_key, body) pairs.

    The version key is the bracketed content of a level-2 heading when it has
    one ("## [0.1.0] - 2026-11-09" -> "0.1.0"), and the raw heading text
    otherwise ("## [Unreleased]" -> "Unreleased"). The body is everything
    between the heading and the next level-2 heading, with the surrounding
    blank lines stripped. The preamble above the first section (frontmatter,
    language switcher) is not part of any section.

    Fenced code blocks are opaque: a "## " line inside one is code, not a
    heading. The release-process doc quotes exactly such a block, so a
    changelog entry about it would otherwise end the section mid-way and
    silently drop the rest of the entry.
    """
    sections: list[tuple[str, str]] = []
    key: str | None = None
    body: list[str] = []
    in_fence = False
    fence_marker = ""

    for line in text.split("\n"):
        if in_fence:
            body.append(line)
            if _is_closing_fence(line, fence_marker):
                in_fence = False
            continue
        fence = FENCE_OPEN_RE.match(line)
        if fence:
            in_fence = True
            fence_marker = fence.group(1)
            if key is not None:
                body.append(line)
            continue
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


def _is_structural(line: str) -> bool:
    """True for a line that carries structure rather than prose."""
    return bool(RULE_RE.match(line) or TABLE_ROW_RE.match(line)
                or BLOCKQUOTE_RE.match(line) or HTML_BLOCK_RE.match(line))


def _joins_without_space(left: str, right: str) -> bool:
    """True when two wrapped lines meet with no space between them.

    Chinese text has no word spaces, so the line break in it is not a
    word boundary and a space would land in the middle of a sentence. A fullwidth comma already carries the space it needs, so
    nothing follows it either.
    """
    if not left or not right:
        return False
    if CJK_PUNCT_RE.match(left):
        return True
    return bool(CJK_RE.match(left) and CJK_RE.match(right))


def _join_flow(lines: list[str]) -> list[str]:
    """Join prose lines into one line, keeping an intentional hard break.

    A line ending in two spaces — or a backslash — is a deliberate break in
    markdown: it ends a line of its own instead of being folded into the
    next one.
    """
    out: list[str] = []
    buffer = ""
    for raw in lines:
        content = raw.strip()
        trailing = len(raw) - len(raw.rstrip())
        if buffer and not _joins_without_space(buffer[-1], content[:1]):
            buffer += " "
        buffer += content
        if trailing >= 2 or content.endswith("\\"):
            if trailing >= 2:
                buffer += "  "
            out.append(buffer)
            buffer = ""
    if buffer:
        # The trailing whitespace of the last line is kept: joining lines
        # is all this does, tidying the text is not its business.
        out.append(buffer + lines[-1][len(lines[-1].rstrip()):])
    return out


def _unwrap_list(block: list[str]) -> list[str]:
    """Join each list item's continuation lines into the item itself.

    A changelog bullet is written across several lines, indented under its
    marker. On the release page those lines are separate bullets unless
    they are folded back into the item they belong to.
    """
    items: list[tuple[str, list[str]]] = []
    for line in block:
        match = LIST_ITEM_RE.match(line)
        if match:
            items.append((match.group("prefix"), [match.group("text")]))
        elif items:
            items[-1][1].append(line)
        else:
            items.append(("", [line]))
    out: list[str] = []
    for prefix, texts in items:
        for joined in _join_flow(texts):
            out.append(prefix + joined)
    return out


def _unwrap_block(block: list[str]) -> list[str]:
    """Join one blank-line-separated block of markdown."""
    if any(_is_structural(line) for line in block):
        return block
    if LIST_ITEM_RE.match(block[0]):
        return _unwrap_list(block)
    return _join_flow(block)


def unwrap_flow(text: str) -> str:
    """Join hard-wrapped prose lines so the rendered text fills its column.

    Markdown in this repository is written one line per paragraph and per
    list item — no hard wrapping — but a changelog section can still
    arrive wrapped: an older tag's text, or an editor that reflows on
    save. GitHub renders a release body with hard breaks on, so every
    newline in the body becomes a line break. Left wrapped, the notes
    land as a narrow ribbon of short lines inside a column twice as wide,
    with an empty gutter beside them.

    So the body is normalized before it is handed over: a paragraph
    becomes one line, and a list item absorbs its continuation lines.
    Structure is left alone — fenced code, tables, blockquotes, HTML
    blocks and rules keep their own lines — and a line ending in two
    spaces, an intentional hard break, keeps it.
    """
    out: list[str] = []
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            out.append(line)
            index += 1
            continue
        if HEADING_RE.match(line):
            # A heading stands alone; the text under it is its own block.
            out.append(line)
            index += 1
            continue
        if "<!--" in line:
            # An HTML comment is opaque: copy it through, comment and all.
            # Its prose is not markdown, so it is not joined.
            out.append(line)
            index += 1
            if "-->" in line.split("<!--", 1)[1]:
                continue
            while index < len(lines):
                out.append(lines[index])
                closed = "-->" in lines[index]
                index += 1
                if closed:
                    break
            continue
        fence = FENCE_OPEN_RE.match(line)
        if fence:
            # A fenced block is opaque: copy it through, fences included.
            out.append(line)
            index += 1
            while index < len(lines):
                out.append(lines[index])
                closed = _is_closing_fence(lines[index], fence.group(1))
                index += 1
                if closed:
                    break
            continue
        block: list[str] = []
        while (index < len(lines) and lines[index].strip()
               and not FENCE_OPEN_RE.match(lines[index])
               and not HEADING_RE.match(lines[index])):
            block.append(lines[index])
            index += 1
        out.extend(_unwrap_block(block))
    return "\n".join(out)


def render_bilingual(en_body: str, zh_body: str) -> str:
    """Render the two language sections into one release body."""
    en_block = _details(unwrap_flow(en_body), "English", EN_ANCHOR, True)
    zh_block = _details(unwrap_flow(zh_body), "中文", ZH_ANCHOR, False)
    return f"{SWITCHER}\n\n{en_block}\n\n{zh_block}\n"


def render_single(body: str, lang: str) -> str:
    """Render one language section on its own (no switcher)."""
    label = "English" if lang == "en" else "中文"
    anchor = EN_ANCHOR if lang == "en" else ZH_ANCHOR
    return _details(unwrap_flow(body), label, anchor, True)


def _details(body: str, label: str, anchor: str, opened: bool) -> str:
    """One language section, wrapped in a disclosure block.

    The label is a heading, not bold text: a <summary> renders in the
    body font size, which makes the language switcher's own entry look
    like a caption above the notes rather than the section title it is.
    An <h2> inside the summary takes the same size and rule as the
    "##" headings of the notes themselves.
    """
    attr = " open" if opened else ""
    return (
        f"<details{attr}>\n"
        f'<summary><a id="{anchor}"></a><h2>{label}</h2></summary>\n'
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
