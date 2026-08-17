"""
Hermes Web UI -- Optional state.db sync bridge.

Mirrors WebUI session metadata (token usage, title, model) into the
hermes-agent state.db so that /insights, session lists, and cost
tracking include WebUI activity.

All operations are wrapped in try/except -- if state.db is unavailable,
locked, or the schema doesn't match, the WebUI continues normally.

The bridge uses absolute token counts (not deltas) because the WebUI
Session object already accumulates totals across turns. This avoids
any double-counting risk.
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_state_db():
    """Get a SessionDB instance for the active profile's state.db.
    Returns None if hermes_state is not importable or DB is unavailable.
    Each caller is responsible for calling db.close() when done.
    """
    try:
        from hermes_state import SessionDB
    except ImportError:
        return None

    try:
        from api.profiles import get_active_hermes_home
        hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        logger.debug("Failed to resolve hermes home, using default")
        hermes_home = Path(os.getenv('HERMES_HOME', str(Path.home() / '.hermes')))

    db_path = hermes_home / 'state.db'
    if not db_path.exists():
        return None

    try:
        return SessionDB(db_path)
    except Exception:
        logger.debug("Failed to open state.db")
        return None


def sync_session_start(session_id: str, model=None) -> None:
    """Register a WebUI session in state.db (idempotent).
    Called when a session's first message is sent.
    """
    db = _get_state_db()
    if not db:
        return
    try:
        db.ensure_session(
            session_id=session_id,
            source='webui',
            model=model,
        )
    except Exception:
        logger.debug("Failed to sync session start to state.db")
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close state.db")


def sync_session_save(session) -> None:
    """Full session save → DB sync (delegates to Session._save_to_db).

    State.db is now the primary store; this function delegates to the
    Session's own DB write method.  Kept as a thin wrapper for backward
    compatibility with existing callers.
    """
    db = _get_state_db()
    if not db:
        return
    try:
        session._save_to_db(db)
    except Exception:
        logger.debug("Failed to sync session %s to state.db", getattr(session, 'session_id', '?'))

    except Exception:
        logger.debug("Failed to sync session save to state.db")
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close state.db")
