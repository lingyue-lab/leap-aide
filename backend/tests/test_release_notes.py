"""
Tests for the bilingual release-notes renderer (scripts/release_notes.py).

The changelog is the single source of truth for a release: the GitHub
Release body is rendered from it, so the release page and the changelog
cannot drift. These tests pin the extraction and the rendering down as text,
because both fail silently rather than loudly — a renderer that quietly
drops the Chinese section still produces a release page, just a monolingual
one.

Filesystem-only — no server required.
"""

import importlib.util
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release_notes.py"
CHANGELOG = REPO / "CHANGELOG.md"
CHANGELOG_ZH = REPO / "CHANGELOG.zh-CN.md"
WORKFLOW = REPO / ".github" / "workflows" / "release-runtime-image.yml"

# The version the released changelog carries. Both sides must have it.
VERSION = "0.1.0"


def _load_module():
    """Import scripts/release_notes.py by path (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(path: pathlib.Path) -> str:
    assert path.is_file(), (
        f"{path} is missing under {REPO} — inside the testing image this "
        f"usually means .dockerignore excludes it; whitelist it with a `!` "
        f"entry AFTER the `**/.*` rule"
    )
    return path.read_text(encoding="utf-8")


class TestExtraction:

    def test_finds_the_section_in_both_languages(self):
        module = _load_module()
        en = module.find_section(_read(CHANGELOG), VERSION)
        zh = module.find_section(_read(CHANGELOG_ZH), VERSION)
        assert en and "First public release." in en, en
        assert zh and "首个公开发布版" in zh, zh

    def test_section_excludes_its_own_heading(self):
        module = _load_module()
        body = module.find_section(_read(CHANGELOG), VERSION)
        assert not body.lstrip().startswith("## "), body
        assert f"## [{VERSION}]" not in body, body

    def test_section_stops_at_the_next_version(self):
        module = _load_module()
        sections = module.parse_sections(_read(CHANGELOG))
        keys = [key for key, _ in sections]
        assert VERSION in keys, keys
        index = keys.index(VERSION)
        if index + 1 < len(keys):
            following = sections[index + 1][1]
            assert f"## [{keys[index + 1]}]" not in sections[index][1]

    def test_the_version_argument_accepts_a_v_prefix(self):
        module = _load_module()
        text = _read(CHANGELOG)
        assert module.find_section(text, f"v{VERSION}") == \
            module.find_section(text, VERSION)

    def test_unknown_version_is_reported_with_the_known_ones(self):
        module = _load_module()
        assert module.find_section(_read(CHANGELOG), "9.9.9") is None
        known = module.available_versions(_read(CHANGELOG))
        assert VERSION in known, known


class TestRendering:

    def _render(self, **kwargs):
        module = _load_module()
        en = module.find_section(_read(CHANGELOG), VERSION)
        zh = module.find_section(_read(CHANGELOG_ZH), VERSION)
        return module.render_bilingual(en, zh)

    def test_both_languages_are_present(self):
        body = self._render()
        assert "First public release." in body
        assert "首个公开发布版" in body

    def test_the_switcher_links_resolve_to_real_anchors(self):
        """Every link in the switcher must point at an anchor in the body.

        A switcher that scrolls nowhere is worse than no switcher: the reader
        clicks "中文" and the page does not move.
        """
        import re
        module = _load_module()
        body = self._render()
        links = re.findall(r"\]\(#([^)]+)\)", body)
        assert links, "the body carries no language switcher"
        for link in links:
            assert f'id="{link}"' in body, (
                f"the switcher links to #{link}, but the body has no such "
                f"anchor — the link goes nowhere")
        assert module.EN_ANCHOR in links
        assert module.ZH_ANCHOR in links

    def test_the_code_block_is_copied_verbatim(self):
        """Fenced code carries non-translatable facts and must not drift."""
        module = _load_module()
        en = module.find_section(_read(CHANGELOG), VERSION)
        body = self._render()
        for block in _code_blocks(en):
            assert block in body, f"the code block was rewritten:\n{block}"

    def test_single_language_mode_drops_the_other_language(self):
        module = _load_module()
        en = module.find_section(_read(CHANGELOG), VERSION)
        zh = module.find_section(_read(CHANGELOG_ZH), VERSION)
        en_only = module.render_single(en, "en")
        zh_only = module.render_single(zh, "zh")
        assert "First public release." in en_only
        assert "首个公开发布版" not in en_only
        assert "首个公开发布版" in zh_only
        assert "First public release." not in zh_only


class TestCommandLine:

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True, cwd=str(REPO),
        )

    def test_renders_the_bilingual_body_on_stdout(self):
        proc = self._run("--version", VERSION)
        assert proc.returncode == 0, proc.stderr
        assert "First public release." in proc.stdout
        assert "首个公开发布版" in proc.stdout

    def test_writes_the_body_to_a_file(self, tmp_path):
        out = tmp_path / "notes.md"
        proc = self._run("--version", VERSION, "--out", str(out))
        assert proc.returncode == 0, proc.stderr
        assert out.is_file()
        assert "首个公开发布版" in out.read_text(encoding="utf-8")

    def test_an_unknown_version_fails_with_a_hint(self):
        proc = self._run("--version", "9.9.9")
        assert proc.returncode == 1, proc.stdout
        assert VERSION in proc.stderr, proc.stderr

    def test_the_compare_link_points_at_the_previous_tag(self):
        module = _load_module()
        link = module.compare_link(REPO, "https://example.invalid/leap-aide",
                                   VERSION)
        if link is None:
            # No earlier tag in this checkout — nothing to compare against.
            return
        assert link.endswith(f"...v{VERSION}"), link
        assert "https://example.invalid/leap-aide/compare/" in link, link


class TestWorkflowWiring:

    """The release page is produced by CI, so the wiring is pinned as text."""

    def _workflow(self):
        return _read(WORKFLOW)

    def test_the_workflow_renders_the_body_from_the_changelog(self):
        workflow = self._workflow()
        assert "scripts/release_notes.py" in workflow, (
            "the release job must render the body from the changelog, not "
            "hand-write it")
        assert "publish-release" in workflow

    def test_the_workflow_never_uses_generated_release_notes(self):
        """GitHub's generated notes would become a second source of truth."""
        workflow = self._workflow()
        assert "--generate-notes" not in workflow, (
            "the release job must not ask GitHub to generate release notes — "
            "the changelog is the source of truth")
        assert "--notes-from-tag" not in workflow, (
            "the release body must come from the changelog, not the tag "
            "message")

    def test_the_release_waits_for_the_image(self):
        workflow = self._workflow()
        assert "needs: release-runtime-image" in workflow, (
            "the release page must not appear before the runtime image for "
            "that tag is in the registry")

    def test_the_release_job_may_write_to_the_repository(self):
        workflow = self._workflow()
        assert "contents: write" in workflow, (
            "creating a release needs contents: write")


def _code_blocks(text: str) -> list[str]:
    """The fenced code blocks of a markdown text, fences included."""
    blocks = []
    current = None
    for line in text.split("\n"):
        if current is None and line.startswith("```"):
            current = [line]
        elif current is not None:
            current.append(line)
            if line.startswith("```"):
                blocks.append("\n".join(current))
                current = None
    return blocks
