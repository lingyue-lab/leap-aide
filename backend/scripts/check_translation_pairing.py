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
  verify every recorded pair in the repo:
      python3 check_translation_pairing.py --check-all
  check a directory tree for pair/record completeness:
      python3 check_translation_pairing.py --check-tree docs
      python3 check_translation_pairing.py --check-tree . --depth 1
  git co-change gate (CI; needs a git checkout):
      python3 check_translation_pairing.py --check-cochange <base-sha>

A record may carry a `waiver:` stanza for an intentional one-sided edit
(e.g. a typo fix that has no Chinese counterpart):

    waiver:
      side: en
      reason: "English typo fix only"
      expires: "2026-12-31"

The waiver is honoured by --check-cochange only; the digests still have to
match, and an expired waiver is reported as a violation, so it cannot
linger unnoticed.

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
from datetime import date
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_OPEN_RE = re.compile(r"^(`{3,}|~{3,})")
RECORD_SUFFIX = ".i18n.yaml"
ZH_SUFFIX = ".zh-CN.md"
HASH_HEX_LEN = 16

# Directories never walked when discovering records or markdown files.
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"}

# Optional top-level stanzas of a record file, parsed as free-form
# `key: value` lines instead of the en:/zh: digest pairs.
META_KEYS = ("waiver",)
WAIVER_FIELDS = ("side", "reason", "expires")
_SCALAR_RE = re.compile(r"^\s+([A-Za-z_][\w-]*):\s*(.*?)\s*$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def check_frontmatter(raw: str, name: str) -> list:
    """Both sides of a pair must open with a frontmatter block at byte 0.

    The block must also be terminated by a '---' line: an unterminated
    one is silently treated as body by strip_frontmatter(), which would
    corrupt both the section split and the digests.
    """
    if not raw.startswith("---"):
        return [f"{name}: frontmatter must start at byte 0"]
    lines = raw.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return []
    return [f"{name}: frontmatter block is not terminated by a '---' line"]


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
    A key is the heading's slug *path* — "/changelog",
    "/changelog/unreleased", "/changelog/010-2026-11-09/added" — so a
    document may repeat a heading title under different parents. The
    changelog needs exactly that: every release section carries its own
    "Added" / "Changed" / "Fixed", and a flat slug collided on the second
    release. For the Chinese side the keys are informational only; pairing
    across languages is positional (structure signature must match).
    Raises ValueError on duplicate paths.
    """
    keys = ["/"]
    digests = [prose_digest(preamble)]
    seen = {"/"}
    ancestors = []  # (level, slug) of the enclosing headings
    for sec in sections:
        level = sec["level"]
        while ancestors and ancestors[-1][0] >= level:
            ancestors.pop()
        slug = slugify(sec["title"])
        ancestors.append((level, slug))
        key = "/" + "/".join(s for _, s in ancestors)
        if key in seen:
            raise ValueError(f"duplicate heading path: {key} "
                             f"(from title {sec['title']!r})")
        seen.add(key)
        keys.append(key)
        digests.append(prose_digest(sec["body"]))
    return keys, digests


# ── record file (minimal YAML subset — no pyyaml) ────────────────────────

def _unquote(value: str) -> str:
    """Strip one layer of matching quotes from a scalar value."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_record(path: Path):
    """Parse a *.i18n.yaml record file.

    Returns (sections, order, meta):
      sections — {"/slug": {"en": hex, "zh": hex}}
      order    — section keys in file order
      meta     — {"waiver": {...}} for the optional stanza(s)
    """
    sections = {}
    order = []
    meta = {}
    key = None          # current section key
    meta_key = None     # current stanza key
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            if not line.endswith(":"):
                raise ValueError(f"{path}: malformed key line: {line!r}")
            name = line[:-1].strip()
            if name in META_KEYS:
                if name in meta:
                    raise ValueError(f"{path}: duplicate stanza {name!r}")
                meta[name] = {}
                meta_key = name
                key = None
                continue
            if name in sections:
                raise ValueError(f"{path}: duplicate key {name!r}")
            sections[name] = {}
            order.append(name)
            key = name
            meta_key = None
            continue
        if meta_key is not None:
            m = _SCALAR_RE.match(line)
            if not m:
                raise ValueError(f"{path}: malformed stanza line: {line!r}")
            field = m.group(1)
            if field in meta[meta_key]:
                raise ValueError(f"{path}: duplicate stanza field {field!r}")
            meta[meta_key][field] = _unquote(m.group(2))
            continue
        m = re.match(r"^\s+(en|zh):\s*([0-9a-f]+)\s*$", line)
        if not m or key is None:
            raise ValueError(f"{path}: malformed entry line: {line!r}")
        sections[key][m.group(1)] = m.group(2)
    return sections, order, meta


def validate_waiver(waiver, today=None) -> list:
    """Return violation strings for a malformed or expired waiver.

    An empty list means the waiver is well-formed and still in date.
    """
    if waiver is None:
        return []
    if today is None:
        today = date.today()
    violations = []
    for field in sorted(set(waiver) - set(WAIVER_FIELDS)):
        violations.append(f"waiver: unknown field {field!r}")
    side = waiver.get("side")
    if side not in ("en", "zh"):
        violations.append(f"waiver: side must be 'en' or 'zh', got {side!r}")
    if not (waiver.get("reason") or "").strip():
        violations.append("waiver: reason must not be empty")
    raw_expires = (waiver.get("expires") or "").strip()
    if not raw_expires:
        violations.append("waiver: expires must not be empty")
    elif not _ISO_DATE_RE.match(raw_expires):
        violations.append(f"waiver: expires must be YYYY-MM-DD, "
                          f"got {raw_expires!r}")
    elif date.fromisoformat(raw_expires) < today:
        violations.append(f"waiver expired on {raw_expires}")
    return violations


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

    violations += verify_pair_quiet_hashes(anchor, zh, en_raw, zh_raw)

    en_pre, en_secs = parse_sections(strip_frontmatter(en_raw))
    zh_pre, zh_secs = parse_sections(strip_frontmatter(zh_raw))

    try:
        keys_en, en_d = section_keys_and_digests(en_pre, en_secs)
        keys_zh, zh_d = section_keys_and_digests(zh_pre, zh_secs)
    except ValueError as e:
        return violations + [str(e)]
    if len(keys_en) != len(keys_zh):
        # Structure mismatch already reported; digests cannot be paired.
        return violations

    try:
        recorded, _order, meta = parse_record(record_path)
    except ValueError as e:
        return violations + [str(e)]

    for v in validate_waiver(meta.get("waiver")):
        violations.append(f"record {record_path.name}: {v}")

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
    # Frontmatter/switcher/code-block violations block writing; hash drift
    # is expected and is exactly what --write re-records.
    structural = verify_pair_quiet_hashes(anchor, zh, en_raw, zh_raw)
    if structural:
        for v in structural:
            print(f"error: {v}", file=sys.stderr)
        print("error: refusing to write record while structural violations "
              "exist", file=sys.stderr)
        return 1

    # A waiver on the existing record survives the re-record — dropping it
    # silently would turn an intentional one-sided edit into a co-change
    # violation with no explanation. An invalid waiver has to be fixed by
    # hand first, so that it can never linger unnoticed.
    waiver = None
    if record_path.is_file():
        try:
            _sections, _order, meta = parse_record(record_path)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            print("error: refusing to write record while the existing "
                  "record is malformed", file=sys.stderr)
            return 1
        waiver = meta.get("waiver")
        if waiver is not None:
            waiver_violations = validate_waiver(waiver)
            if waiver_violations:
                for v in waiver_violations:
                    print(f"error: {record_path.name}: {v}", file=sys.stderr)
                print(f"error: fix or remove the waiver stanza in "
                      f"{record_path.name}, then re-run --write",
                      file=sys.stderr)
                return 1

    en_pre, en_secs = parse_sections(strip_frontmatter(en_raw))
    zh_pre, zh_secs = parse_sections(strip_frontmatter(zh_raw))
    keys_en, en_d = section_keys_and_digests(en_pre, en_secs)
    _keys_zh, zh_d = section_keys_and_digests(zh_pre, zh_secs)

    lines = [
        f"# Bilingual-pair consistency record for {anchor.name} / "
        f"{zh.name}.",
        "# Per heading-slug path: sha256-16 of prose outside code blocks.",
        "# Editing EITHER side requires aligning the OTHER side first, then",
        "# re-recording:",
        f"#   python3 backend/scripts/check_translation_pairing.py --write "
        f"{anchor.as_posix()}",
    ]
    if waiver is not None:
        lines.append("waiver:")
        for field in WAIVER_FIELDS:
            lines.append(f"  {field}: {waiver.get(field, '')}")
    for i, k in enumerate(keys_en):
        lines.append(f"{k}:")
        lines.append(f"  en: {en_d[i]}")
        lines.append(f"  zh: {zh_d[i]}")
    record_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {record_path} ({len(keys_en)} sections)")
    if waiver is not None:
        print(f"waiver active: side={waiver.get('side')}, "
              f"expires={waiver.get('expires')} — remove it once both "
              f"sides are re-synced")
    return 0


def verify_pair_quiet_hashes(anchor, zh, en_raw, zh_raw):
    """Structural subset of verify_pair.

    Frontmatter / switcher / heading levels / fenced code blocks. These
    are the invariants that must hold before a record can be written, so
    --write refuses to record a pair that violates them.
    """
    violations = []
    violations += check_frontmatter(en_raw, anchor.name)
    violations += check_frontmatter(zh_raw, zh.name)
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
    return violations


# ── discovery ────────────────────────────────────────────────────────────

def _repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def find_repo_root() -> Path:
    """Repository root: the git toplevel, or the checkout layout.

    This script lives at <root>/backend/scripts/, so outside a git
    checkout (e.g. inside the testing image) the root is two levels up.
    """
    try:
        top = _repo_root()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path(__file__).resolve().parents[2]
    return top


def record_to_pair(rec: Path):
    """Derive (english anchor, chinese companion, record) from a record."""
    name = rec.name[: -len(RECORD_SUFFIX)]
    return (rec.with_name(name + ".md"),
            rec.with_name(name + ZH_SUFFIX),
            rec)


def discover_pairs(root: Path):
    """Tracked pairs = every *.i18n.yaml under the repo (minus noise dirs)."""
    pairs = []
    for rec in sorted(root.rglob(f"*{RECORD_SUFFIX}")):
        if SKIP_DIRS & set(rec.relative_to(root).parts):
            continue
        pairs.append(record_to_pair(rec))
    return pairs


def discover_tracked_records(root: Path):
    """Every *.i18n.yaml in the repo, tracked or untracked-but-not-ignored.

    git ls-files keeps gitignored scratch directories (.hermes/,
    .buildah-storage-root/, ...) out of the result — a plain filesystem
    walk would happily pick those up. Without git we fall back to the
    walk, which is still correct, just less selective.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard",
             "--", f"*{RECORD_SUFFIX}"],
            capture_output=True, text=True, check=True, cwd=str(root))
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [rec for _a, _z, rec in discover_pairs(root)]
    return [root / line.strip() for line in out.stdout.splitlines()
            if line.strip()]


def _git_ignored_paths(paths: list) -> set:
    """The subset of paths that git ignores.

    --check-tree walks the filesystem, so a local scratch file that
    .gitignore lists (CLAUDE.md, copilot-instructions.md) would be reported
    as a document missing its Chinese companion — even though the
    repository does not contain it. git check-ignore answers that question
    without walking the working tree, and it does not report tracked
    files as ignored. Outside a git checkout nothing is ignored.
    """
    try:
        top = _repo_root()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    by_rel = {}
    for p in paths:
        try:
            by_rel[p.resolve().relative_to(top).as_posix()] = p
        except ValueError:
            continue  # outside the checkout — not git's business
    if not by_rel:
        return set()
    try:
        out = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            input="\0".join(by_rel) + "\0",
            capture_output=True, text=True, cwd=str(top), check=False)
    except (OSError, FileNotFoundError):
        return set()
    if out.returncode not in (0, 1):
        return set()  # 0 = at least one ignored, 1 = none ignored
    ignored = {line for line in out.stdout.split("\0") if line}
    return {by_rel[rel] for rel in ignored if rel in by_rel}


def _iter_files(root: Path, suffix: str, depth=None):
    """Yield files with the given suffix under root.

    depth limits the walk: 1 means only root's own children, None means
    unlimited recursion. Hidden and noise directories are skipped.
    """
    def walk(directory: Path, level: int):
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.is_dir():
                if entry.name.startswith(".") or entry.name in SKIP_DIRS:
                    continue
                if depth is None or level < depth:
                    yield from walk(entry, level + 1)
            elif entry.name.endswith(suffix):
                yield entry

    if root.is_file():
        if root.name.endswith(suffix):
            yield root
        return
    yield from walk(root, 1)


# ── bulk checks ──────────────────────────────────────────────────────────

def check_all() -> int:
    """Verify every bilingual pair that has a consistency record."""
    root = find_repo_root()
    records = discover_tracked_records(root)
    if not records:
        print("check-all: no *.i18n.yaml records found — nothing to do")
        return 0
    violations = []
    for rec in records:
        anchor, _zh, _rec = record_to_pair(rec)
        rel = rec.relative_to(root).as_posix() if rec.is_relative_to(root) \
            else rec.as_posix()
        for v in verify_pair(anchor):
            violations.append(f"{rel}: {v}")
    if violations:
        for v in violations:
            print(f"error: {v}", file=sys.stderr)
        print(f"check-all: {len(violations)} violation(s) in "
              f"{len(records)} pair(s)", file=sys.stderr)
        return 1
    print(f"check-all: ok ({len(records)} pair(s))")
    return 0


def check_tree(root: Path, depth=None) -> int:
    """Check a directory tree for pair/record completeness, then verify.

    The digest records only cover pairs that already have one, so they
    cannot catch a NEW document added without its Chinese side. This
    mode does: every English markdown must have a .zh-CN.md companion
    and an .i18n.yaml record, and every Chinese file and record must
    have an English anchor.
    """
    if not root.exists():
        print(f"error: {root}: path does not exist", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"error: {root}: not a directory", file=sys.stderr)
        return 2

    violations = []
    anchors = []
    md_files = list(_iter_files(root, ".md", depth))
    rec_files = list(_iter_files(root, RECORD_SUFFIX, depth))
    # A file git ignores is not part of the repository — reporting it as a
    # document without a companion would be a false positive.
    ignored = _git_ignored_paths(md_files + rec_files)
    md_files = [p for p in md_files if p not in ignored]
    rec_files = [p for p in rec_files if p not in ignored]

    for md in md_files:
        if md.name.endswith(ZH_SUFFIX):
            anchor = md.with_name(md.name[: -len(ZH_SUFFIX)] + ".md")
            if not anchor.is_file():
                violations.append(f"{md}: orphan Chinese file "
                                  f"(no {anchor.name} alongside)")
            continue
        anchors.append(md)
        zh = md.with_name(md.name[: -len(".md")] + ZH_SUFFIX)
        record = md.with_name(md.name[: -len(".md")] + RECORD_SUFFIX)
        if not zh.is_file():
            violations.append(f"{md}: missing Chinese companion {zh.name}")
        if not record.is_file():
            violations.append(f"{md}: missing consistency record "
                              f"{record.name} (create it with --write)")

    for rec in rec_files:
        anchor, zh, _rec = record_to_pair(rec)
        if not anchor.is_file() or not zh.is_file():
            violations.append(f"{rec}: orphan record "
                              f"(needs {anchor.name} and {zh.name})")

    for anchor in anchors:
        record = anchor.with_name(anchor.name[: -len(".md")] + RECORD_SUFFIX)
        if not record.is_file():
            continue  # already reported above
        for v in verify_pair(anchor):
            violations.append(f"{anchor}: {v}")

    if violations:
        for v in violations:
            print(f"error: {v}", file=sys.stderr)
        print(f"check-tree: {len(violations)} violation(s) in {root}/ "
              f"({len(anchors)} markdown file(s))", file=sys.stderr)
        return 1
    print(f"check-tree: ok ({root}/ — {len(anchors)} pair(s))")
    return 0


def _load_waiver(record_path: Path):
    """The waiver stanza of a record, or None.

    A malformed record yields None: the digest verify reports the
    malformation separately, and the co-change gate must not crash on it.
    """
    try:
        _sections, _order, meta = parse_record(record_path)
    except (ValueError, OSError):
        return None
    return meta.get("waiver")


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
    pairs = [record_to_pair(rec)
             for rec in discover_tracked_records(root)]
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
            side, changed_rel, other_rel = "en", a_rel, z_rel
        elif zh_changed and not en_changed:
            side, changed_rel, other_rel = "zh", z_rel, a_rel
        else:
            continue
        # An intentional one-sided edit is declared by a waiver stanza on
        # the pair's record: diff-visible, directional, and it expires.
        waiver = _load_waiver(rec)
        if waiver is not None and not validate_waiver(waiver) \
                and waiver.get("side") == side:
            print(f"check-cochange: waived {changed_rel} — "
                  f"{waiver.get('reason')} "
                  f"(expires {waiver.get('expires')})")
            continue
        violations.append(
            f"{changed_rel} changed but {other_rel} did not — both language "
            f"files must change together (align the other side, then "
            f"--write); a deliberate one-sided edit needs a `waiver:` "
            f"stanza in {rec.relative_to(root).as_posix()}")
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
    ap.add_argument("--check-all", action="store_true",
                    help="verify every recorded bilingual pair in the repo")
    ap.add_argument("--check-tree", metavar="DIR",
                    help="check a directory tree for pair/record "
                         "completeness")
    ap.add_argument("--depth", type=int, metavar="N",
                    help="limit --check-tree to N directory levels "
                         "(1 = top level only)")
    ap.add_argument("--check-cochange", metavar="BASE_SHA",
                    help="git co-change gate over tracked pairs")
    args = ap.parse_args(argv)

    if args.check_cochange:
        return check_cochange(args.check_cochange)

    if args.check_all and args.check_tree:
        print("error: --check-all and --check-tree are mutually exclusive",
              file=sys.stderr)
        return 2

    if args.check_all:
        if args.anchor:
            print("error: --check-all takes no anchor file", file=sys.stderr)
            return 2
        return check_all()

    if args.check_tree:
        if args.anchor:
            print("error: --check-tree takes no anchor file", file=sys.stderr)
            return 2
        return check_tree(Path(args.check_tree), args.depth)

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
