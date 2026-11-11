#!/usr/bin/env python3
"""preflight.py — check the workspace before the supervise loop starts.

The supervise loop assumes a real repository with a real GitHub remote and a
set of per-role credentials. Running the loop against a half-prepared
workspace wastes a whole delegation cycle, so the checks happen first and
every failure comes with the fix that resolves it.

    python3 preflight.py --issue https://github.com/OWNER/NAME/issues/57

Prints one JSON object:

    {
      "ok": false,
      "workspace": "/path/to/repo",
      "repo": "OWNER/NAME",
      "issue": "...",
      "errors":   [{"code": "...", "message": "...", "fix": "..."}],
      "warnings": [{"code": "...", "message": "...", "fix": "..."}]
    }

Exit code 0 when there are no errors (warnings do not fail the run).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gh_roles  # noqa: E402  (local sibling module)

ISSUE_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)",
    re.IGNORECASE,
)


def _git(workspace: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return 127, "git is not installed"
    return proc.returncode, (proc.stdout or "").strip()


def parse_issue(url: str) -> dict[str, Any] | None:
    m = ISSUE_URL_RE.match((url or "").strip())
    if not m:
        return None
    return {
        "owner": m.group("owner"),
        "repo": m.group("repo"),
        "repo_full": f"{m.group('owner')}/{m.group('repo')}",
        "number": int(m.group("number")),
    }


def remote_repo(workspace: Path) -> tuple[str | None, str | None]:
    """Return ``(owner/repo, remote_name)`` for the GitHub origin remote."""
    code, out = _git(workspace, "remote", "-v")
    if code != 0 or not out:
        return None, None
    best: tuple[str | None, str | None] = (None, None)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, url = parts[0], parts[1]
        if "github.com" not in url:
            continue
        cleaned = re.sub(r"^git@github\.com:", "https://github.com/", url)
        cleaned = re.sub(r"\.git$", "", cleaned)
        cleaned = cleaned.replace("https://github.com/", "").strip("/")
        if "/" in cleaned:
            return cleaned, name
        best = (None, name)
    return best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="preflight.py",
        description="Check the workspace and the role credentials before supervising.",
    )
    parser.add_argument("--issue", default="", help="issue URL the loop will work on")
    parser.add_argument("--workspace", default=None,
                        help="workspace root (defaults to $HERMES_WORKSPACE or the cwd)")
    parser.add_argument("--skip-credentials", action="store_true",
                        help="do not contact GitHub to validate the role tokens")
    args = parser.parse_args(argv)

    workspace = gh_roles.workspace_root(args.workspace)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    issue_info = parse_issue(args.issue) if args.issue else None
    if args.issue and not issue_info:
        errors.append({
            "code": "issue-url-invalid",
            "message": f"{args.issue!r} is not a GitHub issue URL.",
            "fix": "Use the full issue URL, for example "
                   "https://github.com/OWNER/NAME/issues/57",
        })

    # ── git repository ────────────────────────────────────────────────
    code, top = _git(workspace, "rev-parse", "--show-toplevel")
    if code != 0:
        errors.append({
            "code": "not-a-git-repository",
            "message": f"{workspace} is not a git repository.",
            "fix": "Clone the repository first, add it as a Workspace in the Work "
                   "Panel, and start this session in that workspace.",
        })
    elif top and Path(top).resolve() != workspace:
        warnings.append({
            "code": "workspace-is-a-subdirectory",
            "message": f"The git repository root is {top}, not {workspace}.",
            "fix": "Start the session in the repository root so branch and PR "
                   "operations land in the right place.",
        })

    repo_full: str | None = None
    remote_name: str | None = None
    if not errors:
        repo_full, remote_name = remote_repo(workspace)
        if not repo_full:
            errors.append({
                "code": "no-github-remote",
                "message": f"{workspace} has no GitHub remote.",
                "fix": "Add one:  git remote add origin git@github.com:OWNER/NAME.git",
            })
        elif issue_info and repo_full.lower() != issue_info["repo_full"].lower():
            errors.append({
                "code": "issue-repo-mismatch",
                "message": (f"The issue belongs to {issue_info['repo_full']} but the "
                            f"workspace remote points at {repo_full}."),
                "fix": "Point the workspace at the right repository, or run the "
                       "skill against an issue of the repository that is checked out.",
            })

    # ── working tree ─────────────────────────────────────────────────
    if not errors:
        code, status = _git(workspace, "status", "--porcelain")
        if code == 0 and status:
            warnings.append({
                "code": "working-tree-dirty",
                "message": "The working tree has uncommitted changes.",
                "fix": "Commit or stash them before the loop starts — the "
                       "implementer phase commits onto the working branch and "
                       "unrelated changes would end up in its commit.",
            })

    # ── gh CLI ───────────────────────────────────────────────────────
    if gh_roles.shutil.which("gh") is None:
        errors.append({
            "code": "gh-missing",
            "message": "The `gh` CLI is not on PATH.",
            "fix": "LeapAide ships `gh` in its runtime image. Outside the container, "
                   "install it from https://cli.github.com/.",
        })

    # ── role credentials ─────────────────────────────────────────────
    config = gh_roles.load_config(workspace)
    if config is None:
        errors.append({
            "code": "role-config-missing",
            "message": f"No role configuration at {gh_roles.config_path(workspace)}.",
            "fix": "Run:  python3 ${HERMES_SKILL_DIR}/scripts/gh_roles.py init  "
                   "and fill in each role's token.",
        })
    elif not args.skip_credentials:
        roles = config.get("roles") or {}
        for role in gh_roles.ROLES:
            entry = gh_roles._role_entry(config, role)
            if not entry:
                errors.append({
                    "code": "role-missing",
                    "message": f"The '{role}' role is not configured.",
                    "fix": f"Add a '{role}' entry to {gh_roles.config_path(workspace)}.",
                })
                continue
            ok, err_key, detail = gh_roles._check_role(workspace, role, entry)
            if ok:
                continue
            errors.append({
                "code": err_key.split(" (")[0],
                "message": f"The '{role}' role failed to authenticate: "
                           f"{gh_roles._err_title(err_key)}.",
                "fix": gh_roles._guidance(err_key, role, workspace),
            })

    report = {
        "ok": not errors,
        "workspace": str(workspace),
        "repo": repo_full,
        "remote": remote_name,
        "issue": args.issue or None,
        "issue_number": issue_info["number"] if issue_info else None,
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
