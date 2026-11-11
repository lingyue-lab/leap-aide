#!/usr/bin/env python3
"""review_package.py — hand the diff over as a file, not as chat prose.

A reviewer that reconstructs the range with its own git commands is a
reviewer that can get the range wrong, and a diff pasted into a prompt is a
diff whose context has been silently trimmed. This writes one file holding
the commit list, a stat summary and the full diff with surrounding context,
and prints its path. The reviewer reads the file; the supervisor never
re-types the range.

    python3 review_package.py --base <SHA> --head <SHA> [--out PATH]

Prints one JSON object:

    {"path": "/abs/path/to/package.md", "base": "...", "head": "...",
     "commits": 3, "files_changed": 7, "insertions": 120, "deletions": 40}

Exit code 0 on success, 1 when the range is empty or git fails.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_OUT_REL = Path(".leap-aide") / "supervise" / "review-package.md"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def workspace_root(explicit: str | None = None) -> Path:
    import os

    if explicit:
        return Path(explicit).expanduser().resolve()
    env_ws = os.environ.get("HERMES_WORKSPACE") or os.environ.get("WORKSPACE")
    if env_ws:
        return Path(env_ws).expanduser().resolve()
    return Path.cwd().resolve()


def _die(message: str) -> int:
    print(json.dumps({"error": message}, indent=2), file=sys.stderr)
    return 1


def safe_resolve(root: Path, requested: str) -> Path:
    """Resolve `requested` under `root`, rejecting anything that escapes it.

    Mirrors backend/app.safe_resolve: the check is on the resolved path, so
    `..`, absolute paths and symlinks that point outside are all refused.
    """
    root = root.resolve()
    candidate = (root / requested).resolve() if not Path(requested).is_absolute() \
        else Path(requested).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes the workspace: {requested}")
    return candidate


def _git(workspace: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return 127, "git is not installed"
    return proc.returncode, (proc.stdout or "")


def _stat_totals(diff_stat: str) -> tuple[int, int, int]:
    """Parse `files_changed, insertions, deletions` out of `git diff --stat`."""
    files = ins = dele = 0
    for line in diff_stat.splitlines():
        if "|" not in line:
            continue
        files += 1
        body = line.rsplit("|", 1)[1]
        nums = [int(n) for n in re.findall(r"\d+", body)]
        if nums:
            # A pure insertion line prints one number; a mixed line prints two.
            if len(nums) == 1:
                ins += nums[0]
            else:
                ins += nums[0]
                dele += nums[1]
    return files, ins, dele


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="review_package.py",
        description="Write the reviewable diff of a range to one file.",
    )
    parser.add_argument("--base", required=True, help="base SHA of the range")
    parser.add_argument("--head", required=True, help="head SHA of the range")
    parser.add_argument("--workspace", default=None,
                        help="workspace root (defaults to $HERMES_WORKSPACE or the cwd)")
    parser.add_argument("--out", default=None,
                        help="output path, relative to the workspace "
                             f"(default: {DEFAULT_OUT_REL.as_posix()})")
    parser.add_argument("--context", type=int, default=5,
                        help="diff context lines (default: 5)")
    args = parser.parse_args(argv)

    for label, sha in (("base", args.base), ("head", args.head)):
        if not _SHA_RE.match(sha or ""):
            return _die(f"--{label} must be a git SHA, got {sha!r}")
    if args.context < 0 or args.context > 100:
        return _die("--context must be between 0 and 100")

    workspace = workspace_root(args.workspace)
    try:
        out = safe_resolve(workspace, args.out or str(DEFAULT_OUT_REL))
    except ValueError as exc:
        return _die(str(exc))

    code, _ = _git(workspace, "rev-parse", "--verify", args.base)
    if code != 0:
        return _die(f"--base {args.base} is not a commit in this repository")
    code, _ = _git(workspace, "rev-parse", "--verify", args.head)
    if code != 0:
        return _die(f"--head {args.head} is not a commit in this repository")

    range_expr = f"{args.base}..{args.head}"
    code, log = _git(workspace, "log", "--oneline", "--no-decorate", range_expr)
    if code != 0:
        return _die(f"git log failed for {range_expr}: {log.strip()}")
    commits = [ln for ln in log.splitlines() if ln.strip()]
    if not commits:
        return _die(f"{range_expr} contains no commits — nothing to review")

    code, stat = _git(workspace, "diff", "--stat", range_expr)
    if code != 0:
        return _die(f"git diff --stat failed for {range_expr}: {stat.strip()}")
    files_changed, insertions, deletions = _stat_totals(stat)

    code, diff = _git(
        workspace, "diff",
        f"--unified={args.context}",
        "--no-color",
        range_expr,
    )
    if code != 0:
        return _die(f"git diff failed for {range_expr}: {diff.strip()}")

    body = "\n".join([
        "# Review package",
        "",
        f"- Base: `{args.base}`",
        f"- Head: `{args.head}`",
        f"- Commits: {len(commits)}",
        f"- Files changed: {files_changed} (+{insertions} / -{deletions})",
        "",
        "## Commits",
        "",
        *[f"- {line}" for line in commits],
        "",
        "## Stat",
        "",
        "```",
        stat.rstrip("\n"),
        "```",
        "",
        "## Diff",
        "",
        "```diff",
        diff.rstrip("\n"),
        "```",
        "",
    ])

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(out)

    print(json.dumps({
        "path": str(out),
        "base": args.base,
        "head": args.head,
        "commits": len(commits),
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
