#!/usr/bin/env python3
"""check_translation_pairing.py — bilingual pair consistency gate (issue #9).

LeapAide bilingual convention: <name>.md (English) + <name>.zh-CN.md
(Chinese companion) + <name>.i18n.yaml (consistency record). Inspired by
deepseek-ai/deepseek-harness scripts/verify-translation-pairing.ts and
adapted to Python stdlib and the .zh-CN.md suffix.

The record stores, per English heading slug path, the sha256-16 digest of
each side's section prose (fenced code blocks excluded, whitespace
normalized). Fenced code blocks are additionally required to be
byte-identical across the pair — code blocks carry non-translatable facts
(ports, paths, schemas) and must never drift.

Modes:
  verify (default):
      python3 check_translation_pairing.py <anchor.md>
  re-record (after BOTH sides are aligned):
      python3 check_translation_pairing.py --write <anchor.md>
  git co-change gate (CI; needs a git checkout):
      python3 check_translation_pairing.py --check-cochange <base-sha>

Parser assumptions (deliberate, kept minimal): ATX headings only
(#..######), standard ```/~~~ fences, no nested fences, no setext
headings. Skill/docs files in this repo follow these conventions.

Exit codes: 0 ok, 1 violations found, 2 usage error.
"""

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})")
RECORD_SUFFIX = ".i18n.yaml"
ZH_SUFFIX = ".zh-CN.md"
HASH_HEX_LEN = 16


# ── markdown parsing ─────────────────────────────────────────────────────

def strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block, if present."""
    if not text.startswith("---"):
        return text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:])
    return text  # unterminated frontmatter — treat as body


def _is_closing_fence(line: str, open_marker: str) -> bool:
    s = line.strip()
    ch = open_marker[0]
    return bool(s) and set(s) == {ch} and len(s) >= len(open_marker)


def parse_sections(text: str):
    """Split markdown into a preamble plus ATX-heading sections.

    Returns (preamble, sections) where sections is a list of dicts:
        {"level": int, "title": str, "body": str, "fences": [str]}
    "body" excludes fenced code regions; "fences" holds each fenced region
    verbatim (markers included), newline-normalized.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    preamble_lines = []
    sections = []
    cur = None  # current section dict or None while in preamble
    in_fence = False
    fence_marker = None
    fence_lines = None
    fences = []

    def flush():
        nonlocal cur, fences
        if cur is not None:
            cur["body"] = "\n".join(cur.pop("_lines"))
            cur["fences"] = fences
            sections.append(cur)
        cur = None
        fences = []

    for line in lines:
        if in_fence:
            fence_lines.append(line)
            if _is_closing_fence(line, fence_marker):
                fences.append("\n".join(fence_lines))
                fence_lines = None
                in_fence = False
            continue
        m = HEADING_RE.match(line)
        if m:
            flush()
            cur = {"level": len(m.group(1)), "title": m.group(2).strip(),
                   "_lines": []}
            continue
        if FENCE_OPEN_RE.match(line):
            in_fence = True
            fence_marker = FENCE_OPEN_RE.match(line).group(1)
            fence_lines = [line]
            continue
        if cur is None:
            preamble_lines.append(line)
        else:
            cur["_lines"].append(line)
    flush()
    return "\n".join(preamble_lines), sections


def slugify(title: str) -> str:
    t = title.strip().lower()
    t = re.sub(r"[^\w\s-]", "", t, flags=re.UNICODE)
    t = re.sub(r"[\s_]+", "-", t)
    t = re.sub(r"-+", "-", t).strip("-")
    return t or "section"


def normalize_prose(text: str) -> str:
    lines = [ln.rstrip() for ln in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def prose_digest(text: str) -> str:
    norm = normalize_prose(text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:HASH_HEX_LEN]


def section_keys_and_digests(preamble: str, sections):
    """Ordered keys and per-section prose digests for one side.

    Returns (keys, digests) where digests[i] is the digest for keys[i].
    Keys come from this side's heading slugs — for the Chinese side they
    are informational only; pairing across languages is positional
    (structure signature must match). Raises ValueError on duplicate slugs.
    """
    keys = ["/"]
    digests = [prose_digest(preamble)]
    seen = {"/"}
    for sec in sections:
        key = "/" + slugify(sec["title"])
        if key in seen:
            raise ValueError(f"duplicate heading slug: {key} "
                             f"(from title {sec['title']!r})")
        seen.add(key)
        keys.append(key)
        digests.append(prose_digest(sec["body"]))
    return keys, digests


# ── record file (minimal YAML subset — no pyyaml) ────────────────────────

def parse_record(path: Path):
    record = {}
    order = []
    key = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            if not line.endswith(":"):
                raise ValueError(f"{path}: malformed key line: {line!r}")
            key = line[:-1].strip()
            if key in record:
                raise ValueError(f"{path}: duplicate key {key!r}")
            record[key] = {}
            order.append(key)
            continue
        m = re.match(r"^\s+(en|zh):\s*([0-9a-f]+)\s*$", line)
        if not m or key is None:
            raise ValueError(f"{path}: malformed entry line: {line!r}")
        record[key][m.group(1)] = m.group(2)
    return record, order


# ── checks ───────────────────────────────────────────────────────────────

def check_switcher(en_text: str, zh_text: str, en_name: str, zh_name: str):
    violations = []
    head_en = "\n".join(en_text.split("\n")[:20])
    head_zh = "\n".join(zh_text.split("\n")[:20])
    if f"]({zh_name})" not in head_en:
        violations.append(f"language switcher in {en_name} must link to "
                          f"{zh_name} within the first 20 lines")
    if f"]({en_name})" not in head_zh:
        violations.append(f"language switcher in {zh_name} must link to "
                          f"{en_name} within the first 20 lines")
    return violations


def verify_pair(anchor: Path):
    """Full verify of one pair. Returns a list of violation strings."""
    violations = []
    zh = anchor.with_name(anchor.name[: -len(".md")] + ZH_SUFFIX) \
        if anchor.name.endswith(".md") else None
    record_path = anchor.with_name(anchor.name[: -len(".md")] + RECORD_SUFFIX) \
        if anchor.name.endswith(".md") else None
    if zh is None or record_path is None:
        return [f"anchor must end with .md: {anchor}"]

    if not anchor.is_file():
        return [f"missing English file: {anchor}"]
    if not zh.is_file():
        return [f"missing Chinese companion: {zh}"]
    if not record_path.is_file():
        return [f"missing record file: {record_path} "
                f"(create it with --write after aligning both sides)"]

    en_raw = anchor.read_text(encoding="utf-8")
    zh_raw = zh.read_text(encoding="utf-8")
    if not en_raw.startswith("---"):
        violations.append(f"{anchor.name}: frontmatter must start at byte 0")

    violations += check_switcher(en_raw, zh_raw, anchor.name, zh.name)

    en_pre, en_secs = parse_sections(strip_frontmatter(en_raw))
    zh_pre, zh_secs = parse_sections(strip_frontmatter(zh_raw))

    en_levels = [s["level"] for s in en_secs]
    zh_levels = [s["level"] for s in zh_secs]
    if en_levels != zh_levels:
        violations.append(
            f"heading structure mismatch: {anchor.name} levels {en_levels} "
            f"vs {zh.name} levels {zh_levels}")

    en_fences = [f for s in en_secs for f in s["fences"]]
    zh_fences = [f for s in zh_secs for f in s["fences"]]
    if en_fences != zh_fences:
        violations.append(
            f"fenced code blocks differ between {anchor.name} and {zh.name} "
            f"({len(en_fences)} vs {len(zh_fences)} blocks, or content "
            f"differs) — code blocks are facts and must be byte-identical")

    try:
        keys_en, en_d = section_keys_and_digests(en_pre, en_secs)
        keys_zh, zh_d = section_keys_and_digests(zh_pre, zh_secs)
    except ValueError as e:
        return violations + [str(e)]
    if len(keys_en) != len(keys_zh):
        # Structure mismatch already reported; digests cannot be paired.
        return violations

    try:
        recorded, _order = parse_record(record_path)
    except ValueError as e:
        return violations + [str(e)]

    fix = (f"after aligning BOTH language files, re-record with:\n"
           f"    python3 backend/scripts/check_translation_pairing.py "
           f"--write {anchor.as_posix()}")

    current = {k: {"en": en_d[i], "zh": zh_d[i]}
               for i, k in enumerate(keys_en)}
    for k in keys_en:
        if k not in recorded:
            violations.append(f"record {record_path.name}: missing key {k}; "
                              f"{fix}")
            continue
        for side in ("en", "zh"):
            want = current[k][side]
            got = recorded[k].get(side)
            if got != want:
                side_name = "English" if side == "en" else "Chinese"
                violations.append(
                    f"out-of-sync: {k} ({side_name}) — recorded {got}, "
                    f"actual {want}; {fix}")
    for k in recorded:
        if k not in current:
            violations.append(
                f"record {record_path.name}: stale key {k} not present in "
                f"documents; {fix}")
    return violations


def write_record(anchor: Path):
    """Recompute and write the record. Refuses on structural violations."""
    zh = anchor.with_name(anchor.name[: -len(".md")] + ZH_SUFFIX)
    record_path = anchor.with_name(anchor.name[: -len(".md")] + RECORD_SUFFIX)
    if not anchor.is_file() or not zh.is_file():
        print(f"error: pair incomplete: {anchor} / {zh}", file=sys.stderr)
        return 1
    en_raw = anchor.read_text(encoding="utf-8")
    zh_raw = zh.read_text(encoding="utf-8")
    # Structure/switcher/code-block violations block writing; hash drift is
    # expected and is exactly what --write re-records.
    structural = [v for v in verify_pair_quiet_hashes(anchor, zh, en_raw, zh_raw)
                  if "out-of-sync" not in v and "missing key" not in v
                  and "stale key" not in v]
    if structural:
        for v in structural:
            print(f"error: {v}", file=sys.stderr)
        print("error: refusing to write record while structural violations "
              "exist", file=sys.stderr)
        return 1

    en_pre, en_secs = parse_sections(strip_frontmatter(en_raw))
    zh_pre, zh_secs = parse_sections(strip_frontmatter(zh_raw))
    keys_en, en_d = section_keys_and_digests(en_pre, en_secs)
    _keys_zh, zh_d = section_keys_and_digests(zh_pre, zh_secs)

    lines = [
        f"# Bilingual-pair consistency record for {anchor.name} / "
        f"{zh.name}.",
        "# Per heading-slug section: sha256-16 of prose outside code blocks.",
        "# Editing EITHER side requires aligning the OTHER side first, then",
        "# re-recording:",
        f"#   python3 backend/scripts/check_translation_pairing.py --write "
        f"{anchor.as_posix()}",
    ]
    for i, k in enumerate(keys_en):
        lines.append(f"{k}:")
        lines.append(f"  en: {en_d[i]}")
        lines.append(f"  zh: {zh_d[i]}")
    record_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {record_path} ({len(keys_en)} sections)")
    return 0


def verify_pair_quiet_hashes(anchor, zh, en_raw, zh_raw):
    """Structural subset of verify_pair (switcher/structure/fences only)."""
    violations = check_switcher(en_raw, zh_raw, anchor.name, zh.name)
    en_pre, en_secs = parse_sections(strip_frontmatter(en_raw))
    zh_pre, zh_secs = parse_sections(strip_frontmatter(zh_raw))
    en_levels = [s["level"] for s in en_secs]
    zh_levels = [s["level"] for s in zh_secs]
    if en_levels != zh_levels:
        violations.append(
            f"heading structure mismatch: {anchor.name} levels {en_levels} "
            f"vs {zh.name} levels {zh_levels}")
    en_fences = [f for s in en_secs for f in s["fences"]]
    zh_fences = [f for s in zh_secs for f in s["fences"]]
    if en_fences != zh_fences:
        violations.append(
            f"fenced code blocks differ between {anchor.name} and {zh.name} "
            f"— code blocks are facts and must be byte-identical")
    return violations


# ── co-change gate ───────────────────────────────────────────────────────

def _repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def discover_pairs(root: Path):
    """Tracked pairs = every *.i18n.yaml under the repo (minus noise dirs)."""
    skip = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"}
    pairs = []
    for rec in sorted(root.rglob(f"*{RECORD_SUFFIX}")):
        if skip & set(rec.relative_to(root).parts):
            continue
        name = rec.name[: -len(RECORD_SUFFIX)]
        anchor = rec.with_name(name + ".md")
        zh = rec.with_name(name + ZH_SUFFIX)
        pairs.append((anchor, zh, rec))
    return pairs


def check_cochange(base_sha: str):
    try:
        root = _repo_root()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("error: --check-cochange must run inside a git checkout",
              file=sys.stderr)
        return 2
    diff = subprocess.run(
        ["git", "diff", "--name-only", f"{base_sha}...HEAD"],
        capture_output=True, text=True, check=True, cwd=root)
    changed = {line.strip() for line in diff.stdout.splitlines() if line.strip()}

    violations = []
    pairs = discover_pairs(root)
    if not pairs:
        print("check-cochange: no *.i18n.yaml records found — nothing to do")
        return 0
    for anchor, zh, rec in pairs:
        a_rel = anchor.relative_to(root).as_posix()
        z_rel = zh.relative_to(root).as_posix()
        if not anchor.is_file() or not zh.is_file():
            violations.append(
                f"{rec.relative_to(root).as_posix()}: tracked pair incomplete "
                f"({a_rel} / {z_rel} missing on disk)")
            continue
        en_changed = a_rel in changed
        zh_changed = z_rel in changed
        if en_changed and not zh_changed:
            violations.append(
                f"{a_rel} changed but {z_rel} did not — both language files "
                f"must change together (align the Chinese side, then "
                f"--write)")
        elif zh_changed and not en_changed:
            violations.append(
                f"{z_rel} changed but {a_rel} did not — both language files "
                f"must change together (align the English side, then "
                f"--write)")
    if violations:
        for v in violations:
            print(f"error: {v}", file=sys.stderr)
        print(f"check-cochange: {len(violations)} violation(s)", file=sys.stderr)
        return 1
    print(f"check-cochange: ok ({len(pairs)} pair(s), "
          f"{len(changed)} changed file(s))")
    return 0


# ── main ─────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Bilingual pair consistency gate (LeapAide).")
    ap.add_argument("anchor", nargs="?",
                    help="English markdown file of the pair (verify/write)")
    ap.add_argument("--write", action="store_true",
                    help="re-record the .i18n.yaml hashes")
    ap.add_argument("--check-cochange", metavar="BASE_SHA",
                    help="git co-change gate over tracked pairs")
    args = ap.parse_args(argv)

    if args.check_cochange:
        return check_cochange(args.check_cochange)

    if not args.anchor:
        ap.print_usage(sys.stderr)
        print("error: anchor markdown file required", file=sys.stderr)
        return 2
    anchor = Path(args.anchor)
    if args.write:
        return write_record(anchor)

    violations = verify_pair(anchor)
    if violations:
        for v in violations:
            print(f"error: {v}", file=sys.stderr)
        print(f"verify: {len(violations)} violation(s)", file=sys.stderr)
        return 1
    print(f"verify: ok ({anchor})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
