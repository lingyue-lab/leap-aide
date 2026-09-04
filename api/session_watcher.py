"""
Hermes Web UI -- Session watcher.

Background daemon thread that polls state.db every 5 seconds for changes
to non-webui sessions (CLI, gateway, cron). When changes are detected,
it pushes upsert_session / delete_session events to the global notify stream.

Also handles subagent placeholder upgrades: when a real subagent session
appears in state.db, the placeholder created by subagent.start is upgraded
with real data. Once ended_at is set, the subagent is marked as finalized
and skipped on future polls (subagent sessions are immutable after ended_at).

Webui sessions do NOT need polling — their SESSIONS cache mutations
(new_session, get_session, end_stream callback) push notifications directly.
"""
import hashlib
import logging
import os
import threading
import time
from pathlib import Path

from api.config import HOME

logger = logging.getLogger(__name__)


# ── DB helpers ──────────────────────────────────────────────────────────

def _get_state_db_path() -> Path:
    """Resolve state.db path for the active profile."""
    try:
        from api.profiles import get_active_hermes_home
        hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        hermes_home = Path(os.getenv('HERMES_HOME', str(HOME / '.hermes'))).expanduser().resolve()
    return hermes_home / 'state.db'


def _get_agent_sessions_from_db() -> list:
    """Read all non-webui, non-subagent sessions from state.db.

    Returns list of session compact dicts for CLI/gateway/cron sessions.
    Returns empty list on any error.
    """
    import os as _os
    from api.agent_sessions import read_importable_agent_session_rows

    db_path = _get_state_db_path()
    if not db_path.exists():
        return []

    try:
        sessions = []
        for row in read_importable_agent_session_rows(db_path, limit=200, log=logger):
            _source = row['source'] or 'cli'
            # Skip subagent sessions — handled separately via placeholder upgrade.
            # Use parent_session_id (not source) because webui-created subagents
            # inherit HERMES_SESSION_SOURCE=webui and have source='webui' in DB.
            if row.get('parent_session_id'):
                continue
            _user_id = row.get('user_id') or _os.environ.get('USER', 'default')
            _cwd = row.get('cwd')
            if _cwd:
                _workspace = str(Path(_cwd).expanduser().resolve())
            else:
                from api.models import _resolve_gateway_workspace
                _workspace = _resolve_gateway_workspace()
            _msg_count = row['message_count'] or row['actual_message_count'] or 0
            _last_seen = row.get('last_seen_msg_count__webui')
            _clamped_last_seen = min(_last_seen or 0, _msg_count) if _last_seen is not None else None
            sessions.append({
                'session_id': row['id'],
                'title': row['title'] or row.get('first_user_message') or 'Agent Session',
                'workspace': _workspace,
                'model': row['model'] or None,
                'message_count': _msg_count,
                'last_seen_msg_count': _clamped_last_seen,
                'created_at': row['started_at'],
                'updated_at': row['last_activity'] or row['started_at'],
                'source': _source,
                'source_tag': _source,
                'user_id': _user_id,
                'is_cli_session': True,
                '_parent_session_id': row.get('parent_session_id'),
            })
        return sessions
    except Exception:
        return []


# ── SessionWatcher ──────────────────────────────────────────────────────

class SessionWatcher:
    """Background daemon that polls state.db for non-webui session changes.

    Pushes upsert_session / delete_session to the global notify stream.
    Subagent placeholders are upgraded once when real data appears, then
    skipped once finalized (ended_at set).
    """

    POLL_INTERVAL = 5  # seconds

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # CLI/gateway sessions: session_id -> compact dict from last poll
        self._last_cli_sessions: dict[str, dict] = {}
        # Subagent sessions: session_id -> True when finalized (ended_at set)
        self._finalized_subagents: set[str] = set()

    def start(self):
        """Start the watcher daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name='session-watcher',
        )
        self._thread.start()

    def is_alive(self) -> bool:
        """Return True when the poll thread is running."""
        t = self._thread
        return t is not None and t.is_alive()

    def stop(self):
        """Stop the watcher thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    # ── Main loop ────────────────────────────────────────────────────────

    def _poll_loop(self):
        from api.notify_stream import notify_stream

        while not self._stop_event.is_set():
            try:
                self._poll_cli_sessions(notify_stream)
                self._poll_subagent_sessions(notify_stream)
            except Exception:
                logger.debug("Error in session watcher poll loop", exc_info=True)

            # Sleep in small increments for responsive stop
            for _ in range(self.POLL_INTERVAL * 10):
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

    # ── CLI/Gateway session polling ──────────────────────────────────────

    def _poll_cli_sessions(self, notify_stream):
        """Detect CLI/gateway session changes. Push upsert/delete on diff."""
        current_list = _get_agent_sessions_from_db()
        current_map = {s['session_id']: s for s in current_list}
        current_ids = set(current_map.keys())
        last_ids = set(self._last_cli_sessions.keys())

        # New or updated sessions
        for sid in current_ids:
            old = self._last_cli_sessions.get(sid)
            new = current_map[sid]
            if old is None or self._session_changed(old, new):
                self._push_upsert(notify_stream, new)

        # Deleted sessions
        for sid in last_ids - current_ids:
            old = self._last_cli_sessions[sid]
            self._push_delete(notify_stream, sid, old)

        self._last_cli_sessions = current_map

    @staticmethod
    def _session_changed(old: dict, new: dict) -> bool:
        """Compare two session compact dicts for meaningful changes."""
        return (
            old.get('message_count') != new.get('message_count')
            or old.get('title') != new.get('title')
            or old.get('updated_at') != new.get('updated_at')
            or old.get('last_seen_msg_count') != new.get('last_seen_msg_count')
        )

    # ── Subagent placeholder upgrade ─────────────────────────────────────

    def _poll_subagent_sessions(self, notify_stream):
        """Detect real subagent sessions and upgrade placeholders.

        For each placeholder in SESSIONS with _is_placeholder=True:
        - Try to load real data from state.db
        - If found and ended_at is set → finalize (upgrade + mark finalized)
        - If found but not ended → upgrade placeholder (keep polling)
        - If not found → keep placeholder, retry next poll

        Once finalized (ended_at set), the session is skipped on future polls.
        Subagent sessions are immutable after ended_at — no further updates.
        """
        from api.models import LOCK as MODELS_LOCK, SESSIONS, Session, _notify_session_event

        with MODELS_LOCK:
            placeholders = [
                sid for sid, s in SESSIONS.items()
                if getattr(s, '_is_placeholder', False)
                and sid not in self._finalized_subagents
            ]

        for child_sid in placeholders:
            try:
                real = Session.load(child_sid)
                if real is None:
                    continue

                # Check if ended_at is set (session is final)
                ended_at = None
                try:
                    from api.models import _get_session_db
                    db = _get_session_db()
                    if db:
                        row = db.get_session(child_sid)
                        if row:
                            ended_at = row.get('ended_at')
                except Exception:
                    pass

                # Upgrade placeholder with real data (in-place to preserve references)
                with MODELS_LOCK:
                    cached = SESSIONS.get(child_sid)
                    if cached and getattr(cached, '_is_placeholder', False):
                        cached.copy_from(real)
                        cached._is_placeholder = False
                    else:
                        SESSIONS[child_sid] = real
                    SESSIONS.move_to_end(child_sid)
                _notify_session_event('upsert_session', real)

                if ended_at:
                    self._finalized_subagents.add(child_sid)
                    logger.debug("[session-watcher] subagent %s finalized", child_sid[:12])

            except (KeyError, Exception):
                pass  # Not yet persisted, retry next poll

    # ── Push helpers ─────────────────────────────────────────────────────

    def _push_upsert(self, notify_stream, session_data: dict):
        notify_stream.put('upsert_session', {
            'session_id': session_data['session_id'],
            'parent_session_id': session_data.get('_parent_session_id'),
            'stream_id': None,
            'source_tag': session_data.get('source_tag'),
            'title': session_data.get('title'),
            'message_count': session_data.get('message_count', 0),
            'last_seen_msg_count': session_data.get('last_seen_msg_count'),
            'ts': time.time(),
        })

    def _push_delete(self, notify_stream, session_id: str, old_data: dict):
        notify_stream.put('delete_session', {
            'session_id': session_id,
            'parent_session_id': old_data.get('_parent_session_id'),
            'stream_id': None,
            'source_tag': old_data.get('source_tag'),
            'title': old_data.get('title'),
            'message_count': 0,
            'last_seen_msg_count': None,
            'ts': time.time(),
        })


# ── Module-level singleton ─────────────────────────────────────────────

_watcher: SessionWatcher | None = None
_watcher_lock = threading.Lock()


def start_watcher():
    """Start the global session watcher (idempotent)."""
    global _watcher
    with _watcher_lock:
        if _watcher is None:
            _watcher = SessionWatcher()
            _watcher.start()


def stop_watcher():
    """Stop the global session watcher."""
    global _watcher
    with _watcher_lock:
        if _watcher is not None:
            _watcher.stop()
            _watcher = None


def get_watcher() -> SessionWatcher | None:
    """Get the global watcher instance (or None if not started)."""
    with _watcher_lock:
        return _watcher
