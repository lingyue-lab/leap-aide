"""Unified slash-command registry and dispatch for the WebUI.

Merges hermes-agent's COMMAND_REGISTRY (built-in), plugin commands, and
skill commands into a single list for the frontend autocomplete, and
dispatches /command messages to the right handler at chat time.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_category_from_skill_path(skill_dir: str) -> str | None:
    """Derive a human-readable category from a skill's filesystem path."""
    try:
        from tools.skills_tool import SKILLS_DIR
    except ImportError:
        return None
    try:
        p = Path(skill_dir)
        rel = p.relative_to(SKILLS_DIR)
        parts = rel.parts
        if len(parts) >= 2:
            return parts[0]  # e.g. "software-development"
    except (ValueError, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# T002 / T003 / T004: Unified command list + dispatch
# ---------------------------------------------------------------------------

def get_unified_commands() -> dict[str, list[dict[str, Any]]]:
    """Return built-in + skill + plugin commands as a single JSON-friendly dict.

    Response shape::

        {
          "builtin": [{name, description, category, args_hint, aliases, ...}],
          "skills":  [{name, slug, description, category, dir_name}],
        }
    """
    builtins: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []

    # 1. Built-in commands (reuse api/commands.py bridge)
    try:
        from api.commands import list_commands
        builtins = list_commands()
    except Exception:
        logger.debug("api.commands.list_commands failed – returning empty builtins", exc_info=True)

    # 2. Skill commands
    try:
        from agent.skill_commands import get_skill_commands as _get_skill_cmds
        _skill_cmds = _get_skill_cmds()
        for _slug, _info in _skill_cmds.items():
            _name = _slug.lstrip("/")          # "simplify-code"
            _category = _get_category_from_skill_path(str(_info.get("skill_dir", "")))
            skills.append({
                "name": _name,
                "slug": _name,
                "description": _info.get("description", ""),
                "category": _category,
                "dir_name": _info.get("skill_dir", "").split("/")[-1] if _info.get("skill_dir") else _name,
            })
    except Exception:
        logger.debug("skill_commands scan failed – returning empty skills", exc_info=True)

    # 3. Plugin commands (placeholder – extend when plugin system is surfaced)
    plugins: list[dict[str, Any]] = []

    return {"builtin": builtins, "skills": skills, "plugins": plugins}


# ── Direct-response command handlers ──────────────────────────────────────

def _cmd_help(session, args: str) -> dict[str, Any]:
    """Return a formatted list of all available commands."""
    unified = get_unified_commands()
    lines = ["## Available Commands\n"]

    # Built-in commands grouped by category
    by_cat: dict[str, list[dict]] = {}
    for cmd in unified["builtin"]:
        cat = cmd.get("category", "Built-in")
        by_cat.setdefault(cat, []).append(cmd)

    for cat in sorted(by_cat):
        lines.append(f"### {cat}")
        for cmd in sorted(by_cat[cat], key=lambda c: c["name"]):
            args_hint = cmd.get("args_hint", "")
            hint = f" {args_hint}" if args_hint else ""
            lines.append(f"- `/{cmd['name']}{hint}` — {cmd.get('description', '')}")
        lines.append("")

    # Skill commands grouped by category
    skill_by_cat: dict[str, list[dict]] = {}
    for sk in unified["skills"]:
        cat = sk.get("category") or "Skills"
        skill_by_cat.setdefault(cat, []).append(sk)

    if skill_by_cat:
        lines.append("### Skills")
        for cat in sorted(skill_by_cat):
            lines.append(f"**{cat}**")
            for sk in sorted(skill_by_cat[cat], key=lambda s: s["name"]):
                lines.append(f"- `/{sk['name']}` — {sk.get('description', '')}")
            lines.append("")

    return {"content": "\n".join(lines), "session_update": {"_as_card": True}}


def _cmd_model(session, args: str) -> dict[str, Any]:
    """Switch the session model."""
    from api.routes import _resolve_compatible_session_model
    model_name = args.strip()
    if not model_name:
        return {
            "content": "Usage: `/model <model_id>`. Type `/help` for available commands.",
            "session_update": {"_as_card": True},
        }
    try:
        model, normalized = _resolve_compatible_session_model(model_name)
        session.model = normalized or model
        session.save()
        return {
            "content": f"Model switched to **{normalized or model}**.",
            "session_update": {"model": normalized or model, "_as_card": True},
        }
    except Exception as e:
        return {
            "content": f"Failed to switch model: {e}",
            "session_update": {"_as_card": True},
        }


def _cmd_clear(session, args: str) -> dict[str, Any]:
    """Clear all messages in the current session."""
    session.messages = []
    session.save()
    return {"content": "Chat history cleared.", "session_update": {"cleared": True}}


def _cmd_new(session, args: str) -> dict[str, Any]:
    """Create a new session and return its id for frontend navigation."""
    from api.routes import get_session, _sessions_file
    import uuid
    title = args.strip() or "New Session"
    new_id = uuid.uuid4().hex[:12]
    # Create minimal session record
    new_session = get_session.__wrapped__ if hasattr(get_session, '__wrapped__') else None
    try:
        from api.routes import Session as _SessionClass
        ns = _SessionClass(
            session_id=new_id,
            title=title,
            model=session.model,
            workspace=session.workspace,
            messages=[],
            created_at=__import__('time').time(),
        )
        ns.save()
    except Exception:
        # Fallback: create via the sessions file
        import json, time as _time
        sf = _sessions_file()
        sessions_data = {}
        if sf.exists():
            sessions_data = json.loads(sf.read_text())
        sessions_data[new_id] = {
            "session_id": new_id, "title": title,
            "model": session.model, "workspace": session.workspace,
            "messages": [], "created_at": _time.time(),
        }
        sf.write_text(json.dumps(sessions_data, indent=2))
    return {
        "content": f"New session created: **{title}**.",
        "session_update": {"new_session_id": new_id},
    }


def _cmd_branch(session, args: str) -> dict[str, Any]:
    """Branch the current session — copy history to a new session to explore
    a different path without affecting the original conversation."""
    branch_title = args.strip() or "Branch"
    import uuid
    new_id = uuid.uuid4().hex[:12]
    try:
        from api.routes import Session as _SessionClass
        ns = _SessionClass(
            session_id=new_id,
            title=branch_title,
            model=session.model,
            workspace=session.workspace,
            messages=list(session.messages or []),
            created_at=__import__('time').time(),
        )
        ns.save()
    except Exception:
        from api.routes import _sessions_file
        import json, time as _time
        sf = _sessions_file()
        sessions_data = {}
        if sf.exists():
            sessions_data = json.loads(sf.read_text())
        sessions_data[new_id] = {
            "session_id": new_id, "title": branch_title,
            "model": session.model, "workspace": session.workspace,
            "messages": list(session.messages or []),
            "created_at": _time.time(),
        }
        sf.write_text(json.dumps(sessions_data, indent=2))
    return {
        "content": f"Session branched: **{branch_title}**.",
        "session_update": {"new_session_id": new_id},
    }


def _cmd_title(session, args: str) -> dict[str, Any]:
    """Rename the current session."""
    title = args.strip()
    if not title:
        return {"content": "Usage: `/title <new title>`.", "session_update": {}}
    session.title = title
    session.save()
    return {
        "content": f"Session renamed to **{title}**.",
        "session_update": {"title": title},
    }


def _cmd_usage(session, args: str) -> dict[str, Any]:
    """Show token usage for the current session."""
    input_tokens = getattr(session, 'input_tokens', 0) or 0
    output_tokens = getattr(session, 'output_tokens', 0) or 0
    cost = getattr(session, 'estimated_cost', None)
    lines = [
        "## Session Usage",
        f"- Input tokens: **{input_tokens:,}**",
        f"- Output tokens: **{output_tokens:,}**",
        f"- Total tokens: **{input_tokens + output_tokens:,}**",
    ]
    if cost is not None:
        lines.append(f"- Estimated cost: **${cost:.4f}**")
    return {"content": "\n".join(lines), "session_update": {"_as_card": True}}


def _cmd_version(session, args: str) -> dict[str, Any]:
    """Show Hermes Agent + WebUI version."""
    try:
        from hermes_cli import __version__ as agent_ver
    except ImportError:
        agent_ver = "unknown"

    webui_ver = "unknown"
    try:
        from pathlib import Path
        import json
        pkg_json = Path(__file__).resolve().parent.parent / "frontend" / "package.json"
        if pkg_json.exists():
            webui_ver = json.loads(pkg_json.read_text()).get("version", "unknown")
    except Exception:
        pass

    lines = [
        f"Hermes Agent: **v{agent_ver}**",
        f"WebUI: **v{webui_ver}**",
    ]
    return {"content": "\n".join(lines), "session_update": {"_as_card": True}}


def _cmd_status(session, args: str) -> dict[str, Any]:
    """Show session status."""
    msg_count = len(getattr(session, 'messages', []) or [])
    ctx_len = getattr(session, 'context_length', None)
    lines = [
        "## Session Status",
        f"- Session ID: `{session.session_id}`",
        f"- Title: **{getattr(session, 'title', 'Untitled')}**",
        f"- Model: `{getattr(session, 'model', 'unknown')}`",
        f"- Messages: **{msg_count}**",
    ]
    if ctx_len:
        lines.append(f"- Context length: **{ctx_len:,}** tokens")
    return {"content": "\n".join(lines), "session_update": {"_as_card": True}}


def _cmd_compress(session, args: str) -> dict[str, Any]:
    """Trigger context compression."""
    try:
        from api.routes import _auto_compress_session
        _auto_compress_session(session)
        return {"content": "Context compression triggered.", "session_update": {}}
    except Exception as e:
        return {"content": f"Compression failed: {e}", "session_update": {}}


def _cmd_goal(session, args: str) -> dict[str, Any]:
    """Set or manage a standing goal for the current session."""
    from hermes_cli.goals import GoalManager
    mgr = GoalManager(session.session_id)
    arg = args.strip()
    lower = arg.lower()

    # /goal or /goal status → show current state
    if not arg or lower == "status":
        return {"content": mgr.status_line(), "session_update": {"_as_card": True}}

    # /goal clear / stop / done
    if lower in ("clear", "stop", "done"):
        had = mgr.has_goal()
        mgr.clear()
        msg = "Goal cleared." if had else "No active goal."
        return {"content": msg, "session_update": {"_as_card": True}}

    # /goal pause
    if lower == "pause":
        st = mgr.pause()
        if st is None:
            return {"content": "No active goal to pause.", "session_update": {"_as_card": True}}
        return {"content": f"Goal paused: {st.goal}", "session_update": {"_as_card": True}}

    # /goal resume
    if lower == "resume":
        st = mgr.resume()
        if st is None:
            return {"content": "No paused goal to resume.", "session_update": {"_as_card": True}}
        # Kick off the next turn immediately with a continuation prompt,
        # matching CLI behaviour where the user sends any message to continue.
        prompt = GoalManager(session.session_id).next_continuation_prompt()
        if prompt:
            return {"type": "force_mode", "mode": "interrupt", "prompt": prompt}
        return {"content": f"Goal resumed: {st.goal}", "session_update": {"_as_card": True}}

    # /goal <text> → set new goal and kick off immediately
    mgr.set(arg)
    return {
        "type": "force_mode",
        "mode": "interrupt",
        "prompt": (
            f"[NEW STANDING GOAL]\n{arg}\n\n"
            f"Begin working on this goal immediately. Take the first concrete step."
        ),
    }


def _cmd_agents(session, args: str) -> dict[str, Any]:
    """Show active background tasks and queued messages for the current session."""
    lines = []
    found = False

    # ── Queued messages ──
    from api.config import SESSION_MESSAGE_QUEUE, SESSION_MESSAGE_QUEUE_LOCK
    with SESSION_MESSAGE_QUEUE_LOCK:
        q = SESSION_MESSAGE_QUEUE.get(session.session_id, [])
    if q:
        lines.append(f"**Queued:** {len(q)} message(s)")

    # ── Background tasks ──
    from api.background import get_background_tasks
    bg_tasks = get_background_tasks(session.session_id)
    running = [t for t in bg_tasks if t.get("status") == "running"]
    done = [t for t in bg_tasks if t.get("status") == "done"]
    if running:
        lines.append("**Running Background Tasks:**")
        for t in running:
            lines.append(f"  • {str(t.get('prompt', ''))[:80]}")
        found = True
    if done:
        lines.append("**Completed Background Tasks:**")
        for t in done:
            lines.append(f"  • {str(t.get('prompt', ''))[:80]}")
        found = True

    if not found and not lines:
        lines.append("Nothing active — no background tasks or queued messages.")

    return {"content": "\n".join(lines), "session_update": {"_as_card": True}}


def _cmd_subgoal(session, args: str) -> dict[str, Any]:
    """Manage subgoals (extra criteria) on the active goal."""
    from hermes_cli.goals import GoalManager
    mgr = GoalManager(session.session_id)
    if not mgr.has_goal():
        return {"content": "No active goal. Set one with `/goal <text>`.", "session_update": {"_as_card": True}}

    arg = (args or "").strip()
    if not arg:
        # /subgoal → list current subgoals
        return {"content": f"{mgr.status_line()}\n{mgr.render_subgoals()}", "session_update": {"_as_card": True}}

    tokens = arg.split(None, 1)
    verb = tokens[0].lower()
    rest = tokens[1].strip() if len(tokens) > 1 else ""

    if verb == "remove":
        if not rest:
            return {"content": "Usage: `/subgoal remove <n>` (1-based index).", "session_update": {"_as_card": True}}
        try:
            idx = int(rest.split()[0])
        except ValueError:
            return {"content": "`/subgoal remove`: `<n>` must be an integer.", "session_update": {"_as_card": True}}
        try:
            removed = mgr.remove_subgoal(idx)
            session._pending_subgoals = list(mgr._state.subgoals) if mgr._state else []
            return {"content": f"Removed subgoal {idx}: {removed}", "session_update": {"_as_card": True}}
        except IndexError as e:
            return {"content": str(e), "session_update": {"_as_card": True}}
        except RuntimeError:
            return {"content": "No active goal.", "session_update": {"_as_card": True}}

    if verb == "clear":
        try:
            n = mgr.clear_subgoals()
            session._pending_subgoals = []
            return {"content": f"Cleared {n} subgoal(s).", "session_update": {"_as_card": True}}
        except RuntimeError:
            return {"content": "No active goal.", "session_update": {"_as_card": True}}

    # /subgoal <text> → add a subgoal
    try:
        text = mgr.add_subgoal(arg)
        # Also tag the in-memory session so the continuation thread can pick
        # up concurrent subgoal changes without a SessionDB race.
        existing = getattr(session, '_pending_subgoals', None)
        if existing is None:
            session._pending_subgoals = list(mgr._state.subgoals)
        else:
            session._pending_subgoals = list(mgr._state.subgoals)
        return {"content": f"Subgoal added: {text}\n\n{mgr.status_line()}", "session_update": {"_as_card": True}}
    except ValueError:
        return {"content": "Subgoal text cannot be empty.", "session_update": {"_as_card": True}}
    except RuntimeError:
        return {"content": "No active goal.", "session_update": {"_as_card": True}}


def _cmd_curator(session, args: str) -> dict[str, Any]:
    """Manage skill maintenance — status, run, pin, archive."""
    from agent import curator
    from tools import skill_usage

    arg = (args or '').strip().lower()
    lines: list[str] = []

    if not arg or arg == 'status':
        state = curator.load_state()
        enabled = curator.is_enabled()
        paused = state.get('paused', False)
        runs = state.get('run_count', 0)
        last_run = state.get('last_run_at') or 'never'
        summary = state.get('last_run_summary') or '(none)'
        status_line = 'PAUSED' if paused else ('ENABLED' if enabled else 'DISABLED')
        ih = curator.get_interval_hours()
        interval = f'{ih // 24}d' if ih >= 24 else f'{ih}h'

        lines = [
            f'**Curator**: {status_line}',
            f'- Runs: {runs}',
            f'- Last run: {last_run}',
            f'- Interval: every {interval}',
            f'- Stale after: {curator.get_stale_after_days()}d unused',
            f'- Archive after: {curator.get_archive_after_days()}d unused',
            f'- Last summary: {summary}',
        ]
        report = state.get('last_report_path')
        if report:
            lines.append(f'- Last report: `{report}`')

        # Skill usage breakdown
        rows = skill_usage.agent_created_report()
        if rows:
            active = [r for r in rows if r.get('state') == 'active']
            stale = [r for r in rows if r.get('state') == 'stale']
            archived = [r for r in rows if r.get('state') == 'archived']
            lines.append(f'\n**Skills**: {len(rows)} total ({len(active)} active, {len(stale)} stale, {len(archived)} archived)')
            if stale:
                lines.append('- Stale: ' + ', '.join(f'`{r["name"]}`' for r in stale[:10]))
            if archived:
                lines.append('- Archived: ' + ', '.join(f'`{r["name"]}`' for r in archived[:10]))
        else:
            lines.append('\nNo agent-created skills.')

        return {'content': '\n'.join(lines), 'session_update': {'_as_card': True}}

    if arg == 'run':
        if not curator.is_enabled():
            return {'content': 'Curator is disabled. Enable with `curator.enabled: true` in config.', 'session_update': {'_as_card': True}}
        lines.append('**Curator review**:\n')
        try:
            result = curator.run_curator_review(
                on_summary=lambda msg: lines.append(msg),
                synchronous=True,
                dry_run=False,
            )
            auto = result.get('auto_transitions', {})
            if auto:
                lines.append(f'\nAuto-transitions: {auto.get("checked", 0)} checked, {auto.get("applied", 0)} applied.')
            return {'content': '\n'.join(lines), 'session_update': {'_as_card': True}}
        except Exception as e:
            return {'content': f'Curator run failed: {e}', 'session_update': {'_as_card': True}}

    if arg.startswith('pin '):
        name = arg[4:].strip()
        if not name:
            return {'content': 'Usage: `/curator pin <skill_name>`.', 'session_update': {'_as_card': True}}
        if not skill_usage.is_agent_created(name):
            return {'content': f"`{name}` is bundled or hub-installed — only agent-created skills can be pinned.", 'session_update': {'_as_card': True}}
        skill_usage.set_pinned(name, True)
        return {'content': f"Pinned `{name}` — will bypass auto-transitions.", 'session_update': {'_as_card': True}}

    if arg.startswith('unpin '):
        name = arg[5:].strip()
        if not name:
            return {'content': 'Usage: `/curator unpin <skill_name>`.', 'session_update': {'_as_card': True}}
        skill_usage.set_pinned(name, False)
        return {'content': f"Unpinned `{name}`.", 'session_update': {'_as_card': True}}

    if arg.startswith('archive '):
        name = arg[8:].strip()
        if not name:
            return {'content': 'Usage: `/curator archive <skill_name>`.', 'session_update': {'_as_card': True}}
        ok, msg = skill_usage.archive_skill(name)
        return {'content': msg, 'session_update': {'_as_card': True}}

    if arg.startswith('restore '):
        name = arg[8:].strip()
        if not name:
            return {'content': 'Usage: `/curator restore <skill_name>`.', 'session_update': {'_as_card': True}}
        ok, msg = skill_usage.restore_skill(name)
        return {'content': msg, 'session_update': {'_as_card': True}}

    if arg == 'list-archived':
        archived = skill_usage.list_archived_skill_names()
        if not archived:
            return {'content': 'No archived skills.', 'session_update': {'_as_card': True}}
        lines = ['**Archived Skills**:']
        for s in archived:
            lines.append(f'- `{s}`')
        return {'content': '\n'.join(lines), 'session_update': {'_as_card': True}}

    return {'content': 'Usage: `/curator [status|run|list-archived]`.', 'session_update': {'_as_card': True}}


def _cmd_bundles(session, args: str) -> dict[str, Any]:
    """List installed skill bundles."""
    try:
        from agent.skill_bundles import list_bundles
        bundles = list_bundles()
    except Exception as e:
        return {"content": f"Bundle subsystem unavailable: {e}", "session_update": {"_as_card": True}}

    if not bundles:
        return {"content": "No skill bundles installed.", "session_update": {"_as_card": True}}

    lines = [f"**Skill Bundles** ({len(bundles)} installed):\n"]
    for info in bundles:
        slug = info.get('slug', '?')
        skill_count = len(info.get('skills', []))
        desc = info.get('description', f'Load {skill_count} skills')
        lines.append(f"- `/{slug}` — {desc} ({skill_count} skills)")
    return {"content": "\n".join(lines), "session_update": {"_as_card": True}}


def _cmd_rollback(session, args: str) -> dict[str, Any]:
    """List or restore filesystem checkpoints for the current session workspace."""
    from tools.checkpoint_manager import CheckpointManager, format_checkpoint_list

    cwd = str(getattr(session, 'workspace', '') or '')
    if not cwd:
        return {"content": "No workspace set for this session.", "session_update": {"_as_card": True}}

    # Try to use the cached agent's checkpoint manager; fall back to standalone.
    mgr = None
    try:
        from api.config import SESSION_AGENT_CACHE
        with __import__('threading').Lock():  # read-only access, brief lock
            cached = SESSION_AGENT_CACHE.get(session.session_id)
        if cached:
            _agent, _sig = cached
            mgr = getattr(_agent, '_checkpoint_mgr', None)
    except Exception:
        pass

    if mgr is None:
        # Standalone CheckpointManager (e.g. no agent running, or first message).
        # Read from hermes config.yaml, not webui settings.
        try:
            from hermes_cli.config import load_config
            _cp_cfg = (load_config() or {}).get('checkpoints', {})
        except Exception:
            _cp_cfg = {}
        mgr = CheckpointManager(
            enabled=_cp_cfg.get('enabled', False),
            max_snapshots=_cp_cfg.get('max_snapshots', 20),
            max_total_size_mb=_cp_cfg.get('max_total_size_mb', 500),
            max_file_size_mb=_cp_cfg.get('max_file_size_mb', 10),
        )

    if not mgr.enabled:
        return {"content": "Checkpoints are not enabled. Add `checkpoints: { enabled: true }` to `~/.hermes/config.yaml`.", "session_update": {"_as_card": True}}

    parts = (args or '').strip().split()
    checkpoints = mgr.list_checkpoints(cwd)

    # ── /rollback (no args) → list ──
    if not parts:
        if not checkpoints:
            return {"content": f"No checkpoints found for `{cwd}`.", "session_update": {"_as_card": True}}
        return {"content": format_checkpoint_list(checkpoints, cwd), "session_update": {"_as_card": True}}

    sub = parts[0].lower()

    # ── /rollback diff N ──
    if sub == 'diff':
        if len(parts) < 2:
            return {"content": "Usage: `/rollback diff <N>`.", "session_update": {"_as_card": True}}
        if not checkpoints:
            return {"content": "No checkpoints found.", "session_update": {"_as_card": True}}
        try:
            idx = int(parts[1]) - 1
            target = checkpoints[idx]['hash'] if 0 <= idx < len(checkpoints) else None
        except (ValueError, IndexError):
            return {"content": f"Invalid checkpoint number. Use 1-{len(checkpoints)}.", "session_update": {"_as_card": True}}
        result = mgr.diff(cwd, target)
        if result.get('success'):
            return {"content": f"**Diff against checkpoint {parts[1]}:**\n\n```diff\n{result.get('diff', '')}\n```", "session_update": {"_as_card": True}}
        return {"content": f"Diff failed: {result.get('error', 'unknown')}", "session_update": {"_as_card": True}}

    # ── /rollback N [file] → restore ──
    if not checkpoints:
        return {"content": "No checkpoints found.", "session_update": {"_as_card": True}}
    try:
        idx = int(parts[0]) - 1
        target = checkpoints[idx]['hash'] if 0 <= idx < len(checkpoints) else None
    except (ValueError, IndexError):
        return {"content": f"Invalid checkpoint number. Use 1-{len(checkpoints)}.", "session_update": {"_as_card": True}}

    file_path = parts[1] if len(parts) > 1 else None
    result = mgr.restore(cwd, target, file_path=file_path)
    if result.get('success'):
        msg = f"Restored to checkpoint {result.get('restored_to', '?')}: {result.get('reason', '')}"
        if file_path:
            msg = f"Restored `{file_path}` from checkpoint {result.get('restored_to', '?')}"
        msg += "\n\nA pre-rollback snapshot was saved automatically."
        return {"content": msg, "session_update": {"_as_card": True}}
    return {"content": f"Restore failed: {result.get('error', 'unknown')}", "session_update": {"_as_card": True}}


def _cmd_background(session, args: str) -> dict[str, Any]:
    """Run a prompt in a background session (non-blocking)."""
    prompt = args.strip()
    if not prompt:
        return {
            "content": "Usage: `/background <prompt>`. Runs a prompt in a non-blocking background session.",
            "session_update": {},
        }
    from api.routes import _start_background_task
    result = _start_background_task(session, prompt, keep_session=True)
    bg_sid = result["session_id"]
    return {
        "content": f"Background task started: **{prompt[:60]}**\n\nClick to view: `?session={bg_sid}`",
        "session_update": {
            "background_session": {
                "session_id": bg_sid,
                "title": result["title"],
                "created_at": result["created_at"],
                "workspace": result["workspace"],
                "stream_id": result["stream_id"],
                "prompt": prompt,
            },
        },
    }


def _cmd_memory(session, args: str) -> dict[str, Any]:
    """Manage pending memory writes and the write-approval gate.

    Subcommands: pending, approve <id>, reject <id>, approval <on|off>.
    Mirrors the CLI's _handle_memory_command and the gateway's equivalent.
    """
    from hermes_cli.write_approval_commands import handle_pending_subcommand
    from tools import write_approval as wa
    from tools.memory_tool import MemoryStore
    from api.config import _get_config_path, _evict_session_agent

    arg_parts = (args or "").strip().split()
    config_path = _get_config_path()

    def _set_approval(enabled: bool) -> None:
        import yaml as _yaml
        user_config: dict = {}
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                user_config = _yaml.safe_load(f) or {}
        user_config.setdefault("memory", {})["write_approval"] = bool(enabled)
        try:
            config_path.write_text(
                _yaml.safe_dump(user_config, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to persist memory.write_approval: %s", e)
        _evict_session_agent(session.session_id)

    store = MemoryStore()
    store.load_from_disk()

    out = handle_pending_subcommand(
        wa.MEMORY, arg_parts, memory_store=store, set_mode_fn=_set_approval,
    )
    if out is None:
        out = (
            "Unknown /memory subcommand. "
            "Use: pending, approve <id>, reject <id>, approval <on|off>."
        )
    return {"content": out, "session_update": {"_as_card": True}}


def _cmd_reload_skills(session, args: str) -> dict[str, Any]:
    """Re-scan ~/.hermes/skills/ and refresh the slash-command registry."""
    try:
        from agent.skill_commands import reload_skills, get_skill_commands
        result = reload_skills()
        added = result.get("added", [])
        removed = result.get("removed", [])
        total = result.get("total", 0)

        lines = [f"**Skills reloaded**: {total} total"]
        if added:
            names = ", ".join(f"`{s['name']}`" for s in added)
            lines.append(f"- Added: {names}")
        if removed:
            names = ", ".join(f"`{s['name']}`" for s in removed)
            lines.append(f"- Removed: {names}")
        if not added and not removed:
            lines.append("- No changes detected.")
        return {"content": "\n".join(lines), "session_update": {"_as_card": True}}
    except Exception as e:
        return {"content": f"Failed to reload skills: {e}", "session_update": {}}


def _cmd_reload_mcp(session, args: str) -> dict[str, Any]:
    """Reload MCP servers: disconnect all, re-read config.yaml, reconnect.

    Evicts the cached agent so the next message picks up the refreshed
    tool list.  Note: this invalidates the provider prompt cache (tool
    schemas are baked into the system prompt).
    """
    from api.config import _evict_session_agent

    try:
        from tools.mcp_tool import shutdown_mcp_servers, discover_mcp_tools, _servers, _lock
    except ImportError:
        return {"content": "MCP subsystem is not available.", "session_update": {}}

    try:
        with _lock:
            old_servers = set(_servers.keys())

        shutdown_mcp_servers()
        new_tools = discover_mcp_tools()

        with _lock:
            connected = set(_servers.keys())

        added = connected - old_servers
        removed = old_servers - connected
        reconnected = connected & old_servers

        lines = ["**MCP servers reloaded**"]
        if reconnected:
            lines.append(f"- ♻ Reconnected: {', '.join(sorted(reconnected))}")
        if added:
            lines.append(f"- ➕ Added: {', '.join(sorted(added))}")
        if removed:
            lines.append(f"- ➖ Removed: {', '.join(sorted(removed))}")
        if not connected:
            lines.append("- No MCP servers connected.")
        else:
            lines.append(
                f"- 🔧 {len(new_tools)} tool(s) from {len(connected)} server(s)"
            )

        _evict_session_agent(session.session_id)

        return {"content": "\n".join(lines), "session_update": {"_as_card": True}}
    except Exception as e:
        return {"content": f"Failed to reload MCP servers: {e}", "session_update": {}}


def _cmd_reasoning(session, args: str) -> dict[str, Any]:
    """Manage per-session reasoning effort and global display toggle.

    /reasoning                  Show current state
    /reasoning default          Clear session override, use global default
    /reasoning <level>          Set effort: none, minimal, low, medium, high, xhigh
    /reasoning show|hide|on|off Toggle reasoning display (global)
    """
    from api.config import (
        get_reasoning_status,
        set_reasoning_display,
        _evict_session_agent,
    )

    arg = (args or "").strip().lower()
    valid_levels = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})

    if not arg:
        status = get_reasoning_status()
        global_effort = status.get("reasoning_effort") or "medium"
        session_effort = getattr(session, "reasoning_effort", None) or None
        display = "on" if status.get("show_reasoning") else "off"
        if session_effort:
            lines = [
                f"**Reasoning**: `{session_effort}` (session override, global default: `{global_effort}`)",
                f"Display: {display}",
                "Usage: `/reasoning <none|minimal|low|medium|high|xhigh|default|show|hide>`",
            ]
        else:
            lines = [
                f"**Reasoning**: `{global_effort}` (global default)",
                f"Display: {display}",
                "Usage: `/reasoning <none|minimal|low|medium|high|xhigh|default|show|hide>`",
            ]
        return {"content": "\n".join(lines), "session_update": {"_as_card": True}}

    if arg in ("show", "on"):
        set_reasoning_display(True)
        return {
            "content": "Reasoning display: **ON** (saved).",
            "session_update": {"_as_card": True, "show_reasoning": True},
        }

    if arg in ("hide", "off"):
        set_reasoning_display(False)
        return {
            "content": "Reasoning display: **OFF** (saved).",
            "session_update": {"_as_card": True, "show_reasoning": False},
        }

    if arg == "default":
        session.reasoning_effort = None
        session.save()
        _evict_session_agent(session.session_id)
        status = get_reasoning_status()
        global_effort = status.get("reasoning_effort") or "medium"
        return {
            "content": f"Reasoning reset to global default (`{global_effort}`).",
            "session_update": {"_as_card": True, "reasoning_effort": None},
        }

    if arg in valid_levels:
        session.reasoning_effort = arg
        session.save()
        _evict_session_agent(session.session_id)
        return {
            "content": f"Reasoning effort set to **{arg}** (this session only).",
            "session_update": {"_as_card": True, "reasoning_effort": arg},
        }

    return {
        "content": (
            f"Unknown argument: `{arg}`. "
            f"Valid: none, minimal, low, medium, high, xhigh, default, show, hide."
        ),
        "session_update": {"_as_card": True},
    }


def _cmd_fast(session, args: str) -> dict[str, Any]:
    """Toggle fast mode — Anthropic Fast Mode / OpenAI Priority Processing.

    /fast                  Show current status
    /fast fast|on          Enable fast mode
    /fast normal|off       Disable fast mode
    /fast status           Show current status
    """
    from hermes_cli.models import (
        model_supports_fast_mode,
        _is_anthropic_fast_model,
    )

    model = getattr(session, "model", None) or "unknown"

    if not model_supports_fast_mode(model):
        return {
            "content": (
                f"**Fast mode is not supported** for the current model (`{model}`).\n\n"
                "Fast mode is available on:\n"
                "- **Claude Opus 4.6** — Anthropic Fast Mode (`speed=fast`)\n"
                "- **OpenAI flagship models** (GPT-*, o1, o3, o4) — Priority Processing (`service_tier=priority`)\n\n"
                "Usage: `/fast [fast|normal|status]`"
            ),
            "session_update": {"_as_card": True},
        }

    feature_name = "Anthropic Fast Mode" if _is_anthropic_fast_model(model) else "Priority Processing"
    arg = (args or "").strip().lower()

    # ── /fast or /fast status ──
    if not arg or arg == "status":
        current = "fast" if getattr(session, "service_tier", None) == "priority" else "normal"
        return {
            "content": (
                f"**{feature_name}**: `{current}`\n\n"
                f"Usage: `/fast [fast|normal|status]`"
            ),
            "session_update": {"_as_card": True},
        }

    # ── /fast on|fast ──
    if arg in ("fast", "on"):
        from api.config import _evict_session_agent
        session.service_tier = "priority"
        session.save()
        _evict_session_agent(session.session_id)
        return {
            "content": f"**{feature_name}** set to **FAST** (session only).",
            "session_update": {"_as_card": True, "service_tier": "priority"},
        }

    # ── /fast off|normal ──
    if arg in ("normal", "off"):
        from api.config import _evict_session_agent
        session.service_tier = None
        session.save()
        _evict_session_agent(session.session_id)
        return {
            "content": f"**{feature_name}** set to **NORMAL** (session only).",
            "session_update": {"_as_card": True, "service_tier": None},
        }

    return {
        "content": (
            f"Unknown argument: `{arg}`. "
            f"Valid: fast, normal, status (aliases: on, off)."
        ),
        "session_update": {"_as_card": True},
    }


def _cmd_personality(session, args: str) -> dict[str, Any]:
    """Set or clear the session personality overlay.

    Reads available personalities from config.yaml's agent.personalities
    (same source the CLI /personality command uses).

    /personality                    List available + show current
    /personality <name>             Set personality
    /personality none|default       Clear personality overlay
    """
    from api.config import reload_config, get_config

    reload_config()
    cfg = get_config()
    agent_cfg = cfg.get("agent", {}) if isinstance(cfg, dict) else {}
    personalities: dict[str, Any] = agent_cfg.get("personalities", {}) or {}

    arg = (args or "").strip().lower()
    current = getattr(session, "personality", None) or None

    # ── /personality (no args) — list available ──
    if not arg:
        lines = ["**Available Personalities**\n"]
        active_mark = " ← current" if current is None else ""
        lines.append(f"- `none` — clear personality overlay{active_mark}")
        for name in sorted(personalities):
            prompt = personalities[name]
            if isinstance(prompt, dict):
                desc = (prompt.get("description") or prompt.get("system_prompt", ""))[:70]
            else:
                desc = str(prompt)[:70]
            active = " ← current" if name == current else ""
            lines.append(f"- `{name}`{active} — {desc}")
        lines.append(f"\nUsage: `/personality <name>`  (use `none` to clear)")
        return {"content": "\n".join(lines), "session_update": {"_as_card": True}}

    # ── /personality none|default|neutral — clear ──
    if arg in {"none", "default", "neutral"}:
        session.personality = None
        session.save()
        return {
            "content": "Personality cleared — using base agent behavior.",
            "session_update": {"_as_card": True, "personality": None},
        }

    # ── /personality <name> — set ──
    if not isinstance(personalities, dict) or arg not in personalities:
        available = "none, " + ", ".join(sorted(personalities.keys()))
        return {
            "content": (
                f"Unknown personality: `{arg}`.\n\n"
                f"Available: {available}\n\n"
                f"Define custom personalities in `config.yaml` → `agent.personalities`."
            ),
            "session_update": {"_as_card": True},
        }

    session.personality = arg
    session.save()
    from api.streaming import _resolve_personality_prompt
    prompt = _resolve_personality_prompt(cfg, arg) or ""
    preview = prompt[:100] + ("..." if len(prompt) > 100 else "")
    return {
        "content": f"Personality set to **{arg}**.\n\n> {preview}",
        "session_update": {"_as_card": True, "personality": arg},
    }


def _cmd_whoami(session, args: str) -> dict[str, Any]:
    """Show current user access — always available."""
    lines = [
        "**WebUI User**",
        f"Tier: **unrestricted** — all commands available",
        f"Session: `{session.session_id}`",
    ]
    return {"content": "\n".join(lines), "session_update": {"_as_card": True}}


def _cmd_profile(session, args: str) -> dict[str, Any]:
    """Show active profile name and home directory."""
    import os
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    profile_name = os.environ.get("HERMES_PROFILE", "default")
    lines = [
        f"Profile: **{profile_name}**",
        f"Home: `{hermes_home}`",
    ]
    return {"content": "\n".join(lines), "session_update": {"_as_card": True}}


def _cmd_debug(session, args: str) -> dict[str, Any]:
    """Upload debug report (system info + logs) and return shareable links."""
    import io
    import contextlib
    from types import SimpleNamespace

    try:
        from hermes_cli.debug import run_debug_share
    except ImportError:
        return {
            "content": "Debug report unavailable — hermes CLI not installed.",
            "session_update": {"_as_card": True},
        }

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            run_debug_share(SimpleNamespace(lines=200, expire=7, local=False, no_redact=False))
    except SystemExit:
        pass
    except Exception as e:
        return {
            "content": f"Debug report failed: {e}",
            "session_update": {"_as_card": True},
        }

    output = buf.getvalue().strip()
    if not output:
        output = "Debug report generated but empty — check server logs."
    return {"content": output, "session_update": {"_as_card": True}}


def _cmd_insights(session, args: str) -> dict[str, Any]:
    """Show usage insights and analytics across all sessions."""
    # Parse days argument (default 30)
    days = 30
    arg = (args or "").strip()
    if arg:
        try:
            days = int(arg)
        except ValueError:
            return {
                "content": f"Invalid days: `{arg}`. Usage: `/insights [days]`",
                "session_update": {"_as_card": True},
            }

    try:
        from hermes_state import SessionDB
        from agent.insights import InsightsEngine

        db = SessionDB()
        try:
            engine = InsightsEngine(db)
            report = engine.generate(days=days)
            content = engine.format_gateway(report)
        finally:
            db.close()
    except Exception as e:
        # Fallback: show basic per-session summary from WebUI data
        try:
            from pathlib import Path
            import json, os
            sessions_dir = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "sessions"
            total_input = 0
            total_output = 0
            total_cost = 0.0
            count = 0
            if sessions_dir.is_dir():
                for f in sessions_dir.glob("*.json"):
                    try:
                        data = json.loads(f.read_text())
                        total_input += data.get("input_tokens", 0) or 0
                        total_output += data.get("output_tokens", 0) or 0
                        cost = data.get("estimated_cost")
                        if cost is not None:
                            total_cost += float(cost)
                        count += 1
                    except Exception:
                        pass
            content = (
                f"**Usage Summary** (all {count} sessions)\n\n"
                f"- Input tokens: **{total_input:,}**\n"
                f"- Output tokens: **{total_output:,}**\n"
                f"- Total tokens: **{total_input + total_output:,}**\n"
                f"- Estimated cost: **${total_cost:.4f}**\n\n"
                f"(InsightsEngine unavailable — showing WebUI session aggregate. Error: {e})"
            )
        except Exception:
            content = f"Insights unavailable: {e}"

    return {"content": content, "session_update": {"_as_card": True}}


# ── Direct handler lookup ─────────────────────────────────────────────────

_DIRECT_HANDLERS: dict[str, Any] = {
    "help": _cmd_help,
    "model": _cmd_model,
    "clear": _cmd_clear,
    "new": _cmd_new,
    "branch": _cmd_branch,
    "title": _cmd_title,
    "usage": _cmd_usage,
    "status": _cmd_status,
    "compress": _cmd_compress,
    "background": _cmd_background,
    "agents": _cmd_agents,
    "rollback": _cmd_rollback,
    "bundles": _cmd_bundles,
    "curator": _cmd_curator,
    "memory": _cmd_memory,
    "reload-skills": _cmd_reload_skills,
    "reload-mcp": _cmd_reload_mcp,
    "reasoning": _cmd_reasoning,
    "fast": _cmd_fast,
    "personality": _cmd_personality,
    "version": _cmd_version,
    "whoami": _cmd_whoami,
    "profile": _cmd_profile,
    "debug": _cmd_debug,
    "insights": _cmd_insights,
}


# ── Agent built-in commands (not skill, not direct) ───────────────────────

def _build_retry_message() -> str:
    return (
        "[IMPORTANT: The user has requested to retry the last exchange. "
        "Re-execute your previous response with corrections if needed.]"
    )


def _build_undo_message(n: int) -> str:
    return (
        f"[IMPORTANT: The user has requested to undo the last {n} turn(s). "
        f"Remove the last {n} exchange(s) from the conversation "
        f"and continue as if they never happened.]"
    )


# ── Dispatch ──────────────────────────────────────────────────────────────

def dispatch_command(msg: str, session) -> dict[str, Any] | None:
    """Parse a /command message and return a dispatch result.

    Returns:
        None              – not a / command; proceed as normal message
        {"type": "direct", "response": {...}}   – return to frontend directly
        {"type": "agent", "transformed_msg": "..."}  – replace msg, continue streaming
    """
    if not msg or not msg.startswith("/"):
        return None

    parts = msg.split(maxsplit=1)
    first_token = parts[0].lstrip("/")
    if not first_token:
        return None  # just "/"

    user_args = parts[1] if len(parts) > 1 else ""

    # ── 1. Built-in direct handlers ──
    try:
        from hermes_cli.commands import resolve_command
        cmd_def = resolve_command(first_token)
    except Exception:
        cmd_def = None

    # ── 0. Mode-override commands (queue/steer) — forces mode for this msg ──
    if cmd_def is not None:
        if cmd_def.name == "queue":
            prompt = user_args.strip()
            if not prompt:
                return {"type": "direct", "response": {
                    "content": "Usage: `/queue <prompt>`. Queues a message for the next turn.",
                    "session_update": {},
                }}
            return {"type": "force_mode", "mode": "queue", "prompt": prompt}
        if cmd_def.name == "steer":
            prompt = user_args.strip()
            if not prompt:
                return {"type": "direct", "response": {
                    "content": "Usage: `/steer <prompt>`. Steers the running agent mid-turn.",
                    "session_update": {},
                }}
            return {"type": "force_mode", "mode": "steer", "prompt": prompt}
        if cmd_def.name == "goal":
            resp = _cmd_goal(session, user_args)
            if resp.get("type") == "force_mode":
                return resp
            return {"type": "direct", "response": resp}
        if cmd_def.name == "subgoal":
            return {"type": "direct", "response": _cmd_subgoal(session, user_args)}

    if cmd_def is not None and cmd_def.name in _DIRECT_HANDLERS:
        handler = _DIRECT_HANDLERS[cmd_def.name]
        try:
            response = handler(session, user_args)
            return {"type": "direct", "response": response}
        except Exception as e:
            logger.warning("Direct handler '%s' failed: %s", cmd_def.name, e)
            return {"type": "direct", "response": {
                "content": f"Command `/{first_token}` failed: {e}",
            }}

    # ── 2. Agent built-in commands (retry, undo) ──
    if first_token == "retry":
        return {"type": "agent", "transformed_msg": _build_retry_message()}
    if first_token == "undo":
        try:
            n = int(user_args) if user_args else 1
        except ValueError:
            n = 1
        return {"type": "agent", "transformed_msg": _build_undo_message(n)}

    # ── 3. Skill commands ──
    try:
        from agent.skill_commands import resolve_skill_command_key, build_skill_invocation_message
        cmd_key = resolve_skill_command_key(first_token)
        if cmd_key is not None:
            try:
                skill_msg = build_skill_invocation_message(
                    cmd_key, user_instruction=user_args,
                )
                if skill_msg is not None:
                    return {"type": "agent", "transformed_msg": skill_msg}
                # Skill not found
                return {"type": "direct", "response": {
                    "content": (
                        f"Skill not found: `/{first_token}`. "
                        f"Type `/help` to see available commands."
                    ),
                }}
            except Exception as e:
                logger.warning("Skill command '%s' failed: %s", first_token, e)
                return {"type": "direct", "response": {
                    "content": f"Failed to load skill `/{first_token}`: {e}",
                }}
    except Exception:
        logger.debug("skill command resolution unavailable", exc_info=True)

    # ── 4. No match – passthrough ──
    return None
