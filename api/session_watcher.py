"""
Hermes Web UI -- Gateway session watcher.

Background daemon thread that polls state.db every 5 seconds for changes
to gateway sessions (telegram, discord, slack, etc.). When changes are
detected, it pushes notifications to all subscribed SSE clients.

This enables real-time session list updates in the sidebar without
requiring any changes to hermes-agent.
"""
import hashlib
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

from api.config import HOME
from api.agent_sessions import read_importable_agent_session_rows

logger = logging.getLogger(__name__)


# ── State hash tracking ─────────────────────────────────────────────────────

def _snapshot_hash(sessions: list) -> str:
    """Create a lightweight hash of session IDs and timestamps for change detection.

    Includes active_stream_id from SESSIONS cache so that backend-initiated
    streaming state changes (e.g., drain → _run_agent_streaming) also trigger
    sessions_changed events even when db fields haven't changed yet.
    Also includes webui sessions with active_stream_id that aren't in the
    db sessions list (webui sessions are excluded by read_importable_agent_session_rows).
    """
    try:
        from api.models import LOCK, SESSIONS
    except ImportError:
        LOCK = None
        SESSIONS = {}

    parts = []
    db_ids = set()
    for s in sorted(sessions, key=lambda x: x['session_id']):
        sid = s['session_id']
        db_ids.add(sid)
        stream_id = ''
        if LOCK is not None:
            with LOCK:
                cached = SESSIONS.get(sid)
            if cached:
                stream_id = getattr(cached, 'active_stream_id', '') or ''
        parts.append(f"{sid}:{s.get('updated_at', 0)}:{s.get('message_count', 0)}:{stream_id}")

    # Include webui sessions with active_stream_id that aren't in the db list.
    # These are sessions with backend-initiated streaming (drain → _run_agent_streaming).
    if LOCK is not None:
        with LOCK:
            for sid, cached in SESSIONS.items():
                if sid in db_ids:
                    continue
                stream_id = getattr(cached, 'active_stream_id', '') or ''
                if stream_id:
                    parts.append(f"{sid}:streaming:{stream_id}")

    key = '|'.join(sorted(parts))
    return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()


def _get_delegate_children_hash() -> str:
    """获取delegate children的哈希值，用于变化检测。"""
    try:
        from api.models import _get_session_db, _get_all_delegate_children_map
        db = _get_session_db()
        if db is None:
            return ""
        children_map = _get_all_delegate_children_map(db)
        # 创建哈希值：parent_id:child_id:ended_at
        # 包含每个子session的ended_at状态，以便检测子session完成
        parts = []
        for pid, children in sorted(children_map.items()):
            for child in sorted(children, key=lambda c: c['id']):
                ended = child.get('ended_at') or 0
                parts.append(f"{pid}:{child['id']}:{ended}")
        key = '|'.join(parts)
        return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()
    except Exception:
        return ""


# ── DB resolution (shared pattern with state_sync.py) ──────────────────────

def _get_state_db_path() -> Path:
    """Resolve state.db path for the active profile."""
    try:
        from api.profiles import get_active_hermes_home
        hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        hermes_home = Path(os.getenv('HERMES_HOME', str(HOME / '.hermes'))).expanduser().resolve()
    return hermes_home / 'state.db'


def _get_agent_sessions_from_db() -> list:
    """Read all non-webui sessions from state.db.

    Returns list of session dicts compatible with the frontend SSE handler,
    including workspace, user_id, and source_tag for proper sidebar grouping.
    Returns empty list on any error.
    """
    import os as _os

    db_path = _get_state_db_path()
    if not db_path.exists():
        return []

    try:
        sessions = []
        for row in read_importable_agent_session_rows(db_path, limit=200, log=logger):
            _source = row['source'] or 'cli'
            # Skip subagent sessions — they only appear as delegate children,
            # not in the main sidebar list.
            if _source == 'subagent':
                continue
            _user_id = row.get('user_id') or _os.environ.get('USER', 'default')
            # Use cwd from state.db as workspace when available (CLI sessions have a real working directory).
            # For sessions without cwd (gateway platforms), resolve the actual gateway working directory.
            _cwd = row.get('cwd')
            if _cwd:
                _workspace = str(Path(_cwd).expanduser().resolve())
            else:
                from api.models import _resolve_gateway_workspace
                _workspace = _resolve_gateway_workspace()
            _msg_count = row['message_count'] or row['actual_message_count'] or 0
            # Read-time clamp: last_seen_msg_count must never exceed message_count.
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
                'active_stream_id': row.get('active_stream_id'),
            })

        # 添加delegate children信息
        try:
            from api.models import _get_session_db, _get_all_delegate_children_map
            db = _get_session_db()
            if db is not None:
                children_map = _get_all_delegate_children_map(db)
                for session in sessions:
                    sid = session['session_id']
                    children = children_map.get(sid, [])
                    session['_delegate_children_count'] = len(children)
                    session['_has_delegate_children'] = len(children) > 0
        except Exception:
            pass  # non-fatal

        return sessions
    except Exception:
        return []


# ── SessionWatcher ──────────────────────────────────────────────────────────

class SessionWatcher:
    """Background thread that polls state.db for session changes.

    Monitors:
    - Gateway session changes (CLI, messaging platforms)
    - Delegate children changes (subagent sessions)

    Usage:
        watcher = SessionWatcher()
        watcher.start()
        q = watcher.subscribe()
        # ... receive change events via q.get() ...
        watcher.unsubscribe(q)
        watcher.stop()
    """

    POLL_INTERVAL = 5  # seconds between polls
    SUBSCRIBER_TIMEOUT = 30  # seconds before sending keepalive comment

    def __init__(self):
        self._subscribers: list[queue.Queue] = []
        self._sub_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_hash: str = ''
        self._last_sessions: list = []
        self._last_delegate_hash: str = ''  # delegate children哈希

    def start(self):
        """Start the watcher daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name='gateway-watcher')
        self._thread.start()

    def is_alive(self) -> bool:
        """Return True when the poll thread is running.

        Public accessor used by ``/api/sessions/gateway/stream`` probe mode and
        the live SSE handler to detect a watcher instance whose poll thread
        died silently (e.g. uncaught exception in ``_poll_loop``).  Callers
        use this to decide whether to return 503 and trigger the client-side
        polling fallback, instead of handing out an SSE connection that would
        never emit events.
        """
        t = self._thread
        return t is not None and t.is_alive()

    def stop(self):
        """Stop the watcher thread."""
        self._stop_event.set()
        # Wake up any subscribers
        with self._sub_lock:
            for q in self._subscribers:
                try:
                    q.put(None)  # sentinel
                except Exception:
                    logger.debug("Failed to send sentinel to subscriber")
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def subscribe(self) -> queue.Queue:
        """Subscribe to change events. Returns a queue.Queue.
        Events are dicts: {'type': 'sessions_changed', 'sessions': [...]}
        A None sentinel means the watcher is stopping.
        """
        q = queue.Queue(maxsize=10)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        """Remove a subscriber queue."""
        with self._sub_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _notify_subscribers(self, sessions: list):
        """Push change event to all subscribers."""
        event = {
            'type': 'sessions_changed',
            'sessions': sessions,
        }
        with self._sub_lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)  # remove slow consumers
                except Exception:
                    dead.append(q)
            for q in dead:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass
                # Send a None sentinel so the SSE handler unblocks, closes,
                # and lets the browser's EventSource auto-reconnect.
                try:
                    q.put_nowait(None)
                except Exception:
                    logger.debug("Failed to send sentinel to dead subscriber")

    def _poll_loop(self):
        """Main polling loop. Runs in a daemon thread."""
        while not self._stop_event.is_set():
            try:
                sessions = _get_agent_sessions_from_db()
                current_hash = _snapshot_hash(sessions)
                current_delegate_hash = _get_delegate_children_hash()

                # 检测 session 变化
                if current_hash != self._last_hash:
                    # Evict stale SESSIONS cache entries before notifying frontend.
                    # This ensures the next GET /api/session reloads from state.db.
                    self._evict_stale_sessions(self._last_sessions, sessions)

                    # Enrich sessions with active_stream_id from SESSIONS cache.
                    # active_stream_id is in-memory only (not in state.db), so we
                    # read it from the cache to let the frontend know which sessions
                    # are currently streaming (e.g., from drain notifications).
                    self._enrich_active_stream_ids(sessions)

                    # Include webui sessions with active_stream_id.
                    # sessions_changed normally excludes webui sessions (they're
                    # managed by the frontend).  But when the backend starts
                    # streaming on a webui session (drain → _run_agent_streaming),
                    # the frontend needs to know so it can connect to the SSE stream.
                    self._include_streaming_webui_sessions(sessions)

                    self._last_hash = current_hash
                    self._last_sessions = sessions
                    event = {
                        'type': 'sessions_changed',
                        'sessions': sessions,
                    }
                    with self._sub_lock:
                        dead = []
                        for q in self._subscribers:
                            try:
                                q.put_nowait(event)
                            except queue.Full:
                                dead.append(q)
                        for q in dead:
                            try:
                                self._subscribers.remove(q)
                            except ValueError:
                                pass

                # 检测 delegate children 变化
                if current_delegate_hash != self._last_delegate_hash:
                    self._last_delegate_hash = current_delegate_hash
                    changes = self._get_delegate_children_changes()
                    if changes:
                        event = {
                            'type': 'delegate_children_changed',
                            'changes': changes,
                        }
                        with self._sub_lock:
                            dead = []
                            for q in self._subscribers:
                                try:
                                    q.put_nowait(event)
                                except queue.Full:
                                    dead.append(q)
                            for q in dead:
                                try:
                                    self._subscribers.remove(q)
                                except ValueError:
                                    pass
            except Exception:
                logger.debug("Error in gateway watcher poll loop", exc_info=True)

            # Sleep in small increments so we can stop promptly
            for _ in range(self.POLL_INTERVAL * 10):
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

    @staticmethod
    def _enrich_active_stream_ids(sessions: list):
        """Add active_stream_id from SESSIONS cache to session data.

        active_stream_id is in-memory only (set by _run_agent_streaming,
        not persisted to state.db).  This lets the frontend know which
        sessions are currently streaming so it can connect to the SSE stream.
        """
        try:
            from api.models import LOCK, SESSIONS
        except ImportError:
            return

        for s in sessions:
            sid = s.get('session_id')
            if not sid:
                continue
            with LOCK:
                cached = SESSIONS.get(sid)
            if cached:
                stream_id = getattr(cached, 'active_stream_id', None)
                if stream_id:
                    s['active_stream_id'] = stream_id

    @staticmethod
    def _include_streaming_webui_sessions(sessions: list):
        """Add webui sessions with active_stream_id to the sessions list.

        sessions_changed events only contain non-webui sessions (from
        read_importable_agent_session_rows).  Webui sessions that have
        active_stream_id set (from backend-initiated streaming via drain
        notifications) need to be included so the frontend can detect
        the streaming state and connect to the StreamManager SSE stream.
        """
        try:
            from api.models import LOCK, SESSIONS
        except ImportError:
            return

        existing_ids = {s['session_id'] for s in sessions}
        with LOCK:
            for sid, cached in SESSIONS.items():
                if sid in existing_ids:
                    continue
                stream_id = getattr(cached, 'active_stream_id', None)
                if not stream_id:
                    continue
                sessions.append({
                    'session_id': sid,
                    'title': getattr(cached, 'title', None),
                    'workspace': str(getattr(cached, 'workspace', '')),
                    'model': getattr(cached, 'model', None),
                    'message_count': len(getattr(cached, 'messages', []) or []),
                    'created_at': getattr(cached, 'created_at', 0),
                    'updated_at': getattr(cached, 'updated_at', 0),
                    'source': 'webui',
                    'source_tag': 'webui',
                    'is_cli_session': False,
                    'active_stream_id': stream_id,
                })

    @staticmethod
    def _evict_stale_sessions(old_sessions: list, new_sessions: list):
        """Evict SESSIONS cache entries whose message_count increased.

        Compares old and new session snapshots to find sessions where the
        message_count changed (indicating the agent wrote new messages to
        state.db).  Evicts those entries so the next GET /api/session reloads
        the fresh data.
        """
        try:
            from api.models import LOCK, SESSIONS
        except ImportError:
            return

        old_map = {s['session_id']: s for s in (old_sessions or [])}
        evict_ids = []
        for new_s in (new_sessions or []):
            sid = new_s.get('session_id')
            if not sid:
                continue
            old_s = old_map.get(sid)
            old_count = (old_s or {}).get('message_count', 0) or 0
            new_count = new_s.get('message_count', 0) or 0
            if new_count > old_count:
                evict_ids.append(sid)

        if evict_ids:
            with LOCK:
                actually_evicted = []
                for sid in evict_ids:
                    if SESSIONS.pop(sid, None) is not None:
                        actually_evicted.append(sid)
            if actually_evicted:
                # These entries survived the end_stream callback — indicates
                # an external write (non-streaming, CLI, or direct state.db mutation).
                logger.warning(
                    "[session-watcher] Evicted %d cache entries NOT covered by end_stream: %s",
                    len(actually_evicted),
                    ', '.join(s[:12] for s in actually_evicted),
                )

    def _get_delegate_children_changes(self) -> list:
        """获取delegate children的变化信息。"""
        try:
            from api.models import _get_session_db, _get_all_delegate_children_map
            db = _get_session_db()
            if db is None:
                return []
            children_map = _get_all_delegate_children_map(db)
            changes = []
            for parent_id, children in children_map.items():
                # 统计正在运行的子session
                running_count = sum(1 for c in children if not c.get('ended_at'))
                changes.append({
                    'session_id': parent_id,
                    'children_count': len(children),
                    'has_children': len(children) > 0,
                    'running_count': running_count,
                    'children': [{
                        'id': c['id'],
                        'ended_at': c.get('ended_at'),
                    } for c in children],
                })
            return changes
        except Exception:
            return []


# ── Module-level singleton ─────────────────────────────────────────────────

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
