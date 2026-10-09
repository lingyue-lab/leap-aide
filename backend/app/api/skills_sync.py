"""
Skills seed — copy SDK-bundled skills into HERMES_HOME/skills/ at startup.

Background
----------
The hermes-agent SDK ships its bundled skills inside the SDK install at
``<agent_dir>/skills/`` (e.g. ``/usr/local/lib/hermes-agent/skills/`` in the
cicd Docker image). The :mod:`tools.skills_tool` SDK module exposes them
through ``SKILLS_DIR = get_hermes_home() / "skills"`` — i.e. skills are read
from the *active profile's* ``$HERMES_HOME/skills/``, not from the SDK install
root.

In a regular developer install, ``scripts/install.sh`` runs the hermes-agent
installer once and seeds ``~/.hermes/skills/`` from the SDK bundle, so the
two locations stay in sync. The Docker cicd image is different: ``install.sh
--deps-only`` runs during the build, but the resulting ``HERMES_HOME``
defaults to ``/usr/local/lib`` (the SDK install root). ``$HERMES_HOME/skills``
therefore does NOT exist there, and leap-aide's ``/api/skills`` would return
an empty list — leaving the WebUI unable to see the 15 bundled skills.

This module closes that gap at startup: if ``$HERMES_HOME/skills/`` is empty
(or absent) and the SDK bundle at ``<agent_dir>/skills/`` is available, copy
each bundled skill into ``$HERMES_HOME/skills/`` (without overwriting any
skill the user has already created or modified there).

The copy is best-effort and idempotent — a second run is a no-op, and a
failure on a single skill (permission, missing file, etc.) does not abort the
rest of the boot sequence. Permissions: requires ``$HERMES_HOME`` to be
writable by the leap-aide process; if it isn't (e.g. ``HERMES_HOME=/usr/local/lib``
in a locked-down container), the function logs a warning and returns 0 —
the operator then needs to point ``HERMES_HOME`` at a user-owned location
via ``docker-compose.yml`` / ``.env`` / ``docker-entrypoint.sh``.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def seed_bundled_skills() -> int:
    """Copy SDK-bundled skills into ``$HERMES_HOME/skills/`` at startup.

    Idempotent. Returns the number of skills newly copied (existing skills
    on the destination side are left untouched).

    Skips silently when the agent dir isn't configured, the SDK ``skills/``
    directory is missing, or the destination ``$HERMES_HOME/skills`` cannot
    be created (read-only filesystem).
    """
    from app.api.config import _AGENT_DIR, HOME

    if _AGENT_DIR is None:
        logger.debug("seed_bundled_skills: agent dir not configured, skipping")
        return 0

    src_dir = Path(_AGENT_DIR) / "skills"
    if not src_dir.is_dir():
        logger.debug("seed_bundled_skills: source %s missing, skipping", src_dir)
        return 0

    # Resolve HERMES_HOME the same way leap-aide does everywhere else —
    # read env first, fall back to $HOME/.hermes — so the destination
    # matches SKILLS_DIR = get_hermes_home() / "skills" the SDK uses at
    # runtime, regardless of whether HERMES_HOME == HERMES_WEBUI_STATE_DIR
    # (the post-simplification deployment shape) or not (the legacy
    # shape where STATE_DIR is a subdirectory of HERMES_HOME).
    hermes_home = Path(os.getenv("HERMES_HOME", str(HOME / ".hermes"))).expanduser().resolve()
    dst_dir = hermes_home / "skills"

    # No-op when src and dst resolve to the same tree (e.g. a developer
    # install where HERMES_HOME == agent install root).
    try:
        if src_dir.resolve() == dst_dir.resolve():
            logger.debug("seed_bundled_skills: src == dst (%s); skipping", dst_dir)
            return 0
    except OSError:
        pass

    # Create destination. If HERMES_HOME points at a read-only location
    # (e.g. /usr/local/lib in a misconfigured Docker deployment), bail
    # with a single warning — operator needs to set HERMES_HOME to a
    # writable path; we do NOT silently fall back to a different dir.
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        logger.warning(
            "seed_bundled_skills: cannot create %s (%s). "
            "Set HERMES_HOME to a writable path so bundled skills can be seeded.",
            dst_dir, e,
        )
        return 0

    copied = 0
    skipped = 0
    failed = 0
    for entry in src_dir.iterdir():
        # Skip non-directory entries (e.g. .DS_Store, README) — skills are
        # one-directory-per-skill with SKILL.md inside, per the SDK's
        # skills_tool.py:24-44 docs.
        if not entry.is_dir():
            continue
        dst = dst_dir / entry.name
        if dst.exists():
            skipped += 1
            continue
        try:
            shutil.copytree(entry, dst)
            copied += 1
            logger.info("seed_bundled_skills: copied %s -> %s", entry, dst)
        except (PermissionError, OSError) as e:
            failed += 1
            logger.warning("seed_bundled_skills: failed to copy %s: %s", entry, e)

    if copied or failed:
        logger.info(
            "seed_bundled_skills: copied=%d skipped=%d failed=%d (src=%s dst=%s)",
            copied, skipped, failed, src_dir, dst_dir,
        )
    else:
        logger.debug("seed_bundled_skills: all %d skill(s) already present at %s", skipped, dst_dir)
    return copied