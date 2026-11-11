#!/usr/bin/env python3
"""arm_goal.py — attach a standing goal to the current session.

The supervise loop spans several turns: a delegation is dispatched, the
conversation goes quiet while the sub-session works, and the loop resumes
when the sub-session reports back. A standing goal keeps the session alive
across that gap — the WebUI already re-evaluates the goal at the end of every
turn (backend/app/api/streaming.py) and queues the next turn when the goal is
not achieved yet.

This script uses the SDK's own ``hermes_cli.goals.GoalManager`` — the same API
the ``/goal`` slash command uses — so the state lands in the same place and
the WebUI's existing goal handling picks it up with no extra wiring.

Commands
--------
    arm_goal.py arm --text "..." [--max-turns N] [--outcome ...] [...]
        Set (or replace) the standing goal of the current session.

    arm_goal.py status
        Print the current goal state.

    arm_goal.py clear
        Clear the standing goal (call this when the loop is done).

The session id is taken from --session-id, else HERMES_SESSION_KEY (the
WebUI sets it to agent:main:webui:dm:<session_id>), else HERMES_SESSION_ID.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

AGENT_DIR_CANDIDATES = (
    "/usr/local/lib/hermes-agent",
)


def _agent_dir() -> Path | None:
    for env_name in ("HERMES_WEBUI_AGENT_DIR", "HERMES_AGENT_DIR", "HERMES_HOME"):
        raw = (os.environ.get(env_name) or "").strip()
        if raw and (Path(raw) / "run_agent.py").is_file():
            return Path(raw)
    for candidate in AGENT_DIR_CANDIDATES:
        if (Path(candidate) / "run_agent.py").is_file():
            return Path(candidate)
    return None


def _reexec_with_agent_python() -> None:
    """Re-run this script under the hermes-agent venv interpreter.

    The terminal tool's ``python3`` is the system interpreter, which does not
    have the SDK on its path. The agent ships its own venv, so hand over to it
    once and never come back.
    """
    agent_dir = _agent_dir()
    if agent_dir is None:
        return
    venv_python = agent_dir / "venv" / "bin" / "python3"
    if not venv_python.is_file():
        venv_python = agent_dir / "venv" / "bin" / "python"
    if not venv_python.is_file() or venv_python.resolve() == Path(sys.executable).resolve():
        return
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


def _import_goals():
    try:
        from hermes_cli import goals as goals_module  # type: ignore[import-not-found]
    except ImportError:
        agent_dir = _agent_dir()
        if agent_dir is not None and str(agent_dir) not in sys.path:
            sys.path.insert(0, str(agent_dir))
        try:
            from hermes_cli import goals as goals_module  # type: ignore[import-not-found]
        except ImportError:
            _reexec_with_agent_python()
            print(
                "error: the hermes-agent SDK is not importable.\n"
                "       Set HERMES_WEBUI_AGENT_DIR to the agent checkout, or run this\n"
                "       script with the agent venv interpreter:\n"
                "         <agent-dir>/venv/bin/python3 arm_goal.py ...\n"
                "       (LeapAide's container runs the WebUI with that interpreter.)",
                file=sys.stderr,
            )
            raise SystemExit(2)
    return goals_module


def resolve_session_id(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    key = (os.environ.get("HERMES_SESSION_KEY") or "").strip()
    if key:
        # agent:main:webui:dm:<session_id>
        return key.rsplit(":", 1)[-1]
    sid = (os.environ.get("HERMES_SESSION_ID") or "").strip()
    if sid:
        return sid
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="arm_goal.py",
        description="Attach a standing goal to the current session.",
    )
    parser.add_argument("--session-id", default=None,
                        help="session id (defaults to the current session)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_arm = sub.add_parser("arm", help="set the standing goal")
    p_arm.add_argument("--text", required=True, help="the goal text")
    p_arm.add_argument("--max-turns", type=int, default=None)
    p_arm.add_argument("--outcome", default="")
    p_arm.add_argument("--verification", default="")
    p_arm.add_argument("--constraints", default="")
    p_arm.add_argument("--boundaries", default="")
    p_arm.add_argument("--stop-when", default="")

    sub.add_parser("status", help="print the current goal state")
    sub.add_parser("clear", help="clear the standing goal")

    args = parser.parse_args(argv)
    goals_module = _import_goals()

    session_id = resolve_session_id(args.session_id)
    if not session_id:
        print(json.dumps({
            "error": "cannot determine the session id",
            "hint": "pass --session-id, or run this from inside the session so "
                    "HERMES_SESSION_KEY is set",
        }, indent=2))
        return 1

    manager = goals_module.GoalManager(session_id)

    if args.command == "arm":
        contract = goals_module.GoalContract(
            outcome=args.outcome,
            verification=args.verification,
            constraints=args.constraints,
            boundaries=args.boundaries,
            stop_when=args.stop_when,
        )
        state = manager.set(args.text, max_turns=args.max_turns, contract=contract)
        print(json.dumps({
            "session_id": session_id,
            "goal": state.goal,
            "status": state.status,
            "max_turns": state.max_turns,
            "contract": state.contract.to_dict() if state.contract else {},
        }, indent=2, ensure_ascii=False))
        return 0

    if args.command == "status":
        state = manager.state
        if state is None:
            print(json.dumps({"session_id": session_id, "status": "none"}, indent=2))
            return 0
        print(json.dumps({
            "session_id": session_id,
            "goal": state.goal,
            "status": state.status,
            "turns_used": state.turns_used,
            "max_turns": state.max_turns,
        }, indent=2, ensure_ascii=False))
        return 0

    # clear
    manager.clear()
    print(json.dumps({"session_id": session_id, "status": "cleared"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
