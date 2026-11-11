#!/usr/bin/env python3
"""supervise_state.py — durable state for the supervise-fix-issue loop.

The supervisor drives a Plan -> Implement -> Test -> Review loop. The loop has
to survive a context compaction, a session restart or a crashed sub-session,
so its progress lives in a small JSON file inside the workspace rather than in
the conversation:

    <workspace>/.leap-aide/supervise/state.json

The file is written atomically (temp file + rename) and every command prints
one JSON object on stdout, so the agent can read the result without parsing
prose.

Commands
--------
    supervise_state.py init --issue URL [--repo OWNER/NAME] [--issue-number N]
        Create (or reset) the state for this issue.

    supervise_state.py show
        Print the current state.

    supervise_state.py attempt --phase PHASE
        Count one attempt of PHASE and report the new count plus whether the
        phase has run out of attempts.

    supervise_state.py advance --phase PHASE [--note TEXT]
        Record that PHASE passed and move the loop to PHASE.

    supervise_state.py fail --phase PHASE --note TEXT
        Record that PHASE failed; the loop re-runs the phase.

    supervise_state.py set-pr --number N [--url URL]
    supervise_state.py set-branch --name NAME
    supervise_state.py finish
    supervise_state.py reset

Phases: plan, implement, test, review.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

STATE_REL = Path(".leap-aide") / "supervise" / "state.json"
PHASES = ("plan", "implement", "test", "review")
DEFAULT_MAX_ATTEMPTS = 3


def workspace_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_ws = os.environ.get("HERMES_WORKSPACE") or os.environ.get("WORKSPACE")
    if env_ws:
        return Path(env_ws).expanduser().resolve()
    return Path.cwd().resolve()


def state_path(workspace: Path) -> Path:
    return workspace / STATE_REL


def empty_state(issue: str, repo: str | None, issue_number: int | None,
                max_attempts: int) -> dict[str, Any]:
    return {
        "version": 1,
        "issue": issue,
        "repo": repo,
        "issue_number": issue_number,
        "phase": "plan",
        "attempts": {p: 0 for p in PHASES},
        "max_attempts": max_attempts,
        "branch": None,
        "pr": None,
        "status": "running",
        "history": [],
        "updated_at": time.time(),
    }


def load_state(workspace: Path) -> dict[str, Any] | None:
    path = state_path(workspace)
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def save_state(workspace: Path, state: dict[str, Any]) -> None:
    path = state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = time.time()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def record(state: dict[str, Any], event: str, **fields: Any) -> None:
    entry = {"event": event, "at": time.time()}
    entry.update(fields)
    history = state.setdefault("history", [])
    history.append(entry)
    # Keep the trail bounded — a long supervise run can loop many times.
    if len(history) > 200:
        del history[:-200]


def emit(state: dict[str, Any]) -> int:
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    workspace = workspace_root(args.workspace)
    existing = load_state(workspace)
    if existing and not args.force:
        existing["issue"] = args.issue or existing.get("issue")
        if args.repo:
            existing["repo"] = args.repo
        if args.issue_number is not None:
            existing["issue_number"] = args.issue_number
        existing["status"] = "running"
        record(existing, "reinit", issue=existing.get("issue"))
        save_state(workspace, existing)
        return emit(existing)
    state = empty_state(
        args.issue or "",
        args.repo,
        args.issue_number,
        args.max_attempts,
    )
    record(state, "init", issue=state["issue"], repo=state["repo"])
    save_state(workspace, state)
    return emit(state)


def cmd_show(args: argparse.Namespace) -> int:
    workspace = workspace_root(args.workspace)
    state = load_state(workspace)
    if state is None:
        print(json.dumps({"error": "no supervise state; run `init` first"}, indent=2))
        return 1
    return emit(state)


def _require_state(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    workspace = workspace_root(args.workspace)
    state = load_state(workspace)
    if state is None:
        raise SystemExit(_die(f"no supervise state at {state_path(workspace)}; run `init` first"))
    return workspace, state


def _die(message: str) -> int:
    print(json.dumps({"error": message}, indent=2))
    return 1


def cmd_attempt(args: argparse.Namespace) -> int:
    workspace, state = _require_state(args)
    if args.phase not in PHASES:
        return _die(f"unknown phase {args.phase!r}; expected one of {', '.join(PHASES)}")
    attempts = state.setdefault("attempts", {p: 0 for p in PHASES})
    attempts[args.phase] = int(attempts.get(args.phase, 0)) + 1
    max_attempts = int(state.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
    state["phase"] = args.phase
    record(state, "attempt", phase=args.phase, count=attempts[args.phase])
    save_state(workspace, state)
    print(json.dumps({
        "phase": args.phase,
        "attempt": attempts[args.phase],
        "max_attempts": max_attempts,
        "exhausted": attempts[args.phase] >= max_attempts,
    }, indent=2))
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    workspace, state = _require_state(args)
    if args.phase not in PHASES:
        return _die(f"unknown phase {args.phase!r}; expected one of {', '.join(PHASES)}")
    record(state, "advance", phase=args.phase, note=args.note or "")
    state["phase"] = args.phase
    save_state(workspace, state)
    return emit(state)


def cmd_fail(args: argparse.Namespace) -> int:
    workspace, state = _require_state(args)
    if args.phase not in PHASES:
        return _die(f"unknown phase {args.phase!r}; expected one of {', '.join(PHASES)}")
    record(state, "fail", phase=args.phase, note=args.note or "")
    state["phase"] = args.phase
    save_state(workspace, state)
    return emit(state)


def cmd_set_pr(args: argparse.Namespace) -> int:
    workspace, state = _require_state(args)
    state["pr"] = {"number": args.number, "url": args.url}
    record(state, "set-pr", number=args.number)
    save_state(workspace, state)
    return emit(state)


def cmd_set_branch(args: argparse.Namespace) -> int:
    workspace, state = _require_state(args)
    state["branch"] = args.name
    record(state, "set-branch", name=args.name)
    save_state(workspace, state)
    return emit(state)


def cmd_finish(args: argparse.Namespace) -> int:
    workspace, state = _require_state(args)
    state["status"] = "done"
    record(state, "finish", note=args.note or "")
    save_state(workspace, state)
    return emit(state)


def cmd_reset(args: argparse.Namespace) -> int:
    workspace = workspace_root(args.workspace)
    path = state_path(workspace)
    if path.exists():
        path.unlink()
    print(json.dumps({"reset": True, "path": str(path)}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="supervise_state.py",
        description="Durable state for the supervise-fix-issue loop.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workspace", default=None,
                       help="workspace root (defaults to $HERMES_WORKSPACE or the cwd)")

    p_init = sub.add_parser("init", help="create or reset the loop state")
    add_common(p_init)
    p_init.add_argument("--issue", default=None, help="issue URL")
    p_init.add_argument("--repo", default=None, help="OWNER/NAME")
    p_init.add_argument("--issue-number", type=int, default=None)
    p_init.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    p_init.add_argument("--force", action="store_true",
                        help="discard an existing state instead of reusing it")
    p_init.set_defaults(func=cmd_init)

    p_show = sub.add_parser("show", help="print the current state")
    add_common(p_show)
    p_show.set_defaults(func=cmd_show)

    p_attempt = sub.add_parser("attempt", help="count one attempt of a phase")
    add_common(p_attempt)
    p_attempt.add_argument("--phase", required=True, choices=PHASES)
    p_attempt.set_defaults(func=cmd_attempt)

    p_adv = sub.add_parser("advance", help="record that a phase passed")
    add_common(p_adv)
    p_adv.add_argument("--phase", required=True, choices=PHASES)
    p_adv.add_argument("--note", default="")
    p_adv.set_defaults(func=cmd_advance)

    p_fail = sub.add_parser("fail", help="record that a phase failed")
    add_common(p_fail)
    p_fail.add_argument("--phase", required=True, choices=PHASES)
    p_fail.add_argument("--note", default="")
    p_fail.set_defaults(func=cmd_fail)

    p_pr = sub.add_parser("set-pr", help="record the pull request number")
    add_common(p_pr)
    p_pr.add_argument("--number", type=int, required=True)
    p_pr.add_argument("--url", default=None)
    p_pr.set_defaults(func=cmd_set_pr)

    p_branch = sub.add_parser("set-branch", help="record the working branch")
    add_common(p_branch)
    p_branch.add_argument("--name", required=True)
    p_branch.set_defaults(func=cmd_set_branch)

    p_finish = sub.add_parser("finish", help="mark the loop as done")
    add_common(p_finish)
    p_finish.add_argument("--note", default="")
    p_finish.set_defaults(func=cmd_finish)

    p_reset = sub.add_parser("reset", help="delete the loop state")
    add_common(p_reset)
    p_reset.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
