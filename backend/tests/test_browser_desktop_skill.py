"""
Tests for the x-browser-desktop skill package (issue #9).

Filesystem-only — validates the skill files, the Hermes frontmatter
hardlines, and the bilingual pairing gate (check_translation_pairing.py).
No server required.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "leap-aide" / "x-browser-desktop"
SKILL_MD = SKILL_DIR / "SKILL.md"
SKILL_ZH = SKILL_DIR / "SKILL.zh-CN.md"
SKILL_RECORD = SKILL_DIR / "SKILL.i18n.yaml"
CHECKER = REPO_ROOT / "backend" / "scripts" / "check_translation_pairing.py"


def _frontmatter_block(raw: str) -> str:
    """Return the raw frontmatter block including both --- fences."""
    assert raw.startswith("---"), "frontmatter must start at byte 0"
    lines = raw.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[: i + 1])
    raise AssertionError("unterminated frontmatter")


def _frontmatter_fields(raw: str) -> dict:
    block = _frontmatter_block(raw)
    fields = {}
    for line in block.split("\n")[1:-1]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def test_skill_files_exist():
    """The skill package ships all three bilingual files."""
    assert SKILL_MD.is_file(), f"missing {SKILL_MD}"
    assert SKILL_ZH.is_file(), f"missing {SKILL_ZH}"
    assert SKILL_RECORD.is_file(), f"missing {SKILL_RECORD}"


def test_frontmatter_hardlines():
    """Frontmatter satisfies the Hermes skill-authoring hardlines."""
    raw = SKILL_MD.read_text(encoding="utf-8")
    fields = _frontmatter_fields(raw)
    assert fields.get("name") == "x-browser-desktop"
    desc = fields.get("description", "")
    assert desc, "description must be non-empty"
    assert len(desc) <= 60, f"description too long ({len(desc)} > 60)"
    assert desc.endswith("."), "description must end with a period"
    # Body must be non-empty after the closing frontmatter fence.
    body = raw.split("---", 2)[2] if raw.count("---") >= 2 else ""
    assert body.strip(), "skill body must be non-empty"


def test_chinese_companion_mirrors_frontmatter_and_switcher():
    """The zh-CN companion mirrors the frontmatter (except the localized
    description) and keeps the language switcher.

    The description is the one frontmatter field allowed to diverge: the
    Chinese companion carries a Chinese description for human readers,
    while Hermes indexes the English SKILL.md only.
    """
    en_raw = SKILL_MD.read_text(encoding="utf-8")
    zh_raw = SKILL_ZH.read_text(encoding="utf-8")
    en_fields = _frontmatter_fields(en_raw)
    zh_fields = _frontmatter_fields(zh_raw)
    assert {k: v for k, v in zh_fields.items() if k != "description"} == (
        {k: v for k, v in en_fields.items() if k != "description"}
    ), "companion frontmatter must mirror the English one (except description)"
    assert zh_fields.get("description"), "Chinese description must be non-empty"
    assert zh_fields.get("name") == en_fields.get("name"), (
        "skill name must match across the pair")
    assert "[中文](SKILL.zh-CN.md)" in en_raw, (
        "English file must link to the Chinese companion")
    assert "[English](SKILL.md)" in zh_raw, (
        "Chinese file must link back to the English original")


def test_bilingual_pairing_gate_passes():
    """check_translation_pairing.py verify mode exits 0 for the pair.

    This is the drift gate: a one-sided edit (or a --write without
    aligning the other side, or a one-sided code-block change) makes
    this test fail.
    """
    proc = subprocess.run(
        [sys.executable, str(CHECKER), str(SKILL_MD)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, (
        f"bilingual pairing gate failed:\n{proc.stderr or proc.stdout}")
