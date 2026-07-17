"""Hermes Web UI -- Session model and in-memory session store."""
import collections
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

import api.config as _cfg
from api.config import (
    SESSION_DIR, SESSION_INDEX_FILE, SESSIONS, SESSIONS_MAX,
    LOCK, STREAMS, STREAMS_LOCK, DEFAULT_WORKSPACE, DEFAULT_MODEL, PROJECTS_FILE, HOME,
    get_effective_default_model,
)
from api.workspace import get_last_workspace
from api.agent_sessions import read_importable_agent_session_rows

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stale temp-file cleanup
# ---------------------------------------------------------------------------
# Both Session.save() and _write_session_index() use the atomic-write pattern:
#   write to  <path>.tmp.<pid>.<tid>  →  os.replace() to final path
# If the process crashes between write and replace the .tmp file is left
# behind.  Because the name embeds pid + tid, leftover files can never be
# reused by a different process/thread, so they are safe to remove on the
# next startup.  _cleanup_stale_tmp_files() is called from the full-rebuild
# path of _write_session_index (i.e. at first index access / startup) and
# removes any *.tmp.* file whose mtime is older than one hour.
# ---------------------------------------------------------------------------

_STALE_TMP_AGE_SECONDS = 3600  # 1 hour

# Serializes index writers so concurrent Session.save() calls cannot race on
# stale baselines while still allowing LOCK to be released before disk I/O.
_INDEX_WRITE_LOCK = threading.RLock()


def _cleanup_stale_tmp_files() -> None:
    """Best-effort removal of stale ``*.tmp.*`` files from SESSION_DIR.

    Only files whose mtime is older than ``_STALE_TMP_AGE_SECONDS`` are
    removed so that in-flight writes from a long-running sibling process
    are not disturbed.  Errors are logged and swallowed — this must never
    prevent startup.
    """
    cutoff = time.time() - _STALE_TMP_AGE_SECONDS
    try:
        for p in SESSION_DIR.glob('*.tmp.*'):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    logger.debug("Cleaned up stale tmp file: %s", p.name)
            except OSError:
                pass  # best-effort
    except Exception:
        pass  # SESSION_DIR may not exist yet; that's fine


def _index_entry_exists(session_id: str, in_memory_ids=None) -> bool:
    """Return True if an index entry still has backing state.

    A session can legitimately exist either as a persisted JSON file or as an
    in-memory Session object that has not been flushed yet.  This helper is used
    to prune stale `_index.json` rows left behind after session-id rotation or
    file removal.
    """
    if not session_id:
        return False
    if in_memory_ids is None:
        with LOCK:
            in_memory_ids = set(SESSIONS.keys())
    if session_id in in_memory_ids:
        return True
    p = SESSION_DIR / f'{session_id}.json'
    return p.exists()


def _write_session_index(updates=None):
    """No-op: state.db is the single source of truth for session listings."""


def _active_stream_ids():
    with STREAMS_LOCK:
        return set(STREAMS.keys())


def _is_streaming_session(active_stream_id, active_stream_ids):
    return bool(active_stream_id and active_stream_id in active_stream_ids)

def _session_sort_timestamp(session):
    if isinstance(session, dict):
        return session.get('last_message_at') or session.get('updated_at') or 0
    return _last_message_timestamp(getattr(session, 'messages', None)) or getattr(session, 'updated_at', 0) or 0


def _message_timestamp(message):
    if not isinstance(message, dict):
        return None
    raw = message.get('_ts') or message.get('timestamp')
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _last_message_timestamp(messages):
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get('role') == 'tool':
            continue
        ts = _message_timestamp(message)
        if ts:
            return ts
    return None


def _find_top_level_json_key(text, key):
    """Return the byte offset of a top-level JSON object key, if present."""
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            start = i
            i += 1
            escaped = False
            chars = []
            while i < n:
                c = text[i]
                if escaped:
                    chars.append(c)
                    escaped = False
                elif c == '\\':
                    escaped = True
                elif c == '"':
                    break
                else:
                    chars.append(c)
                i += 1
            if i >= n:
                return None
            if depth == 1 and ''.join(chars) == key:
                j = i + 1
                while j < n and text[j] in ' \t\r\n':
                    j += 1
                if j < n and text[j] == ':':
                    return start
        elif ch in '{[':
            depth += 1
        elif ch in '}]':
            depth -= 1
        i += 1
    return None


def _read_metadata_json_prefix(path, max_prefix_bytes=65536):
    """Read only the metadata portion before the top-level messages array."""
    buf = ''
    with open(path, 'r', encoding='utf-8') as f:
        while len(buf.encode('utf-8')) < max_prefix_bytes:
            chunk = f.read(4096)
            if not chunk:
                return None
            buf += chunk
            messages_pos = _find_top_level_json_key(buf, 'messages')
            if messages_pos is None:
                continue
            prefix = buf[:messages_pos].rstrip()
            if prefix.endswith(','):
                prefix = prefix[:-1].rstrip()
            return f'{prefix}\n}}'
    return None


def _lookup_index_message_count(session_id):
    """Return the indexed message count without loading the full session file."""
    try:
        entries = json.loads(SESSION_INDEX_FILE.read_text(encoding='utf-8'))
    except Exception:
        return None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if entry.get('session_id') != session_id:
            continue
        count = entry.get('message_count')
        if isinstance(count, int) and count >= 0:
            return count
        try:
            count = int(count)
        except (TypeError, ValueError):
            return None
        return count if count >= 0 else None
    return None


# ---------------------------------------------------------------------------
# SessionDB singleton — long-lived read-write connection to state.db
# ---------------------------------------------------------------------------

_session_db = None
_session_db_lock = threading.Lock()

def _resolve_state_db_path() -> Path:
    """Resolve the state.db path for the active profile."""
    try:
        from api.profiles import get_active_hermes_home
        hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        hermes_home = Path(os.getenv('HERMES_HOME', str(Path.home() / '.hermes')))
    return hermes_home / 'state.db'


def _get_session_db():
    """Get or create the shared SessionDB singleton (read-write, WAL mode).

    The connection is opened lazily on first access and reused across all
    requests.  Thread safety is provided by SessionDB's internal
    threading.Lock + SQLite WAL mode.
    """
    global _session_db
    if _session_db is not None:
        return _session_db
    with _session_db_lock:
        if _session_db is not None:
            return _session_db
        try:
            from hermes_state import SessionDB
        except ImportError:
            logger.warning("hermes_state unavailable — falling back to JSON storage")
            return None
        db_path = _resolve_state_db_path()
        if not db_path.exists():
            # state.db doesn't exist yet (fresh install).  The first save()
            # will create it; loads will return None.
            return None
        try:
            _session_db = SessionDB(db_path)
            # Trigger column reconciliation so any newly-added SCHEMA_SQL
            # columns are added to the live DB before we read/write.
            if not _session_db._fts_enabled:
                # read_only path skipped FTS probe — but we're read-write,
                # so _init_schema already ran and _fts_enabled should be set.
                pass
        except Exception:
            logger.warning("Failed to open state.db — falling back to JSON storage",
                           exc_info=True)
            return None
    return _session_db


def _close_session_db():
    """Close the shared SessionDB connection. Called on server shutdown."""
    global _session_db
    with _session_db_lock:
        if _session_db is not None:
            try:
                _session_db.close()
            except Exception:
                pass
            _session_db = None


# ── Error-message filter ─────────────────────────────────────────────────

# Error messages generated by the agent framework when a turn fails
# (streaming.py apperror path) carry _error=True in memory, but this flag
# is not stored in state.db.  After a session reload, error messages become
# indistinguishable from real replies.  Filter them before persistence.
_ERROR_MSG_PREFIXES = (
    '**Error:**',
    '**No response received:**',
    '**Authentication failed:**',
    '**Out of credits:**',
    '**Request timed out:**',
)


def _filter_persistable_messages(messages):
    """Return messages with error-turn pairs stripped.

    When an assistant message matches an error pattern, both it and the
    preceding user message (the failed turn) are removed from the
    persisted set.  Messages remain in the in-memory session so the UI
    still shows the inline error for the current page session.
    """
    if not messages:
        return messages
    # Find indices of error assistant messages
    error_indices = set()
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        # _error flag (in-memory marker from streaming.py)
        if m.get('_error'):
            error_indices.add(i)
            continue
        # Content-pattern match (defense-in-depth for reloaded sessions)
        if m.get('role') == 'assistant':
            content = str(m.get('content', '') or '')
            for prefix in _ERROR_MSG_PREFIXES:
                if content.startswith(prefix):
                    error_indices.add(i)
                    break
    if not error_indices:
        return messages
    # Also strip the preceding user message for each error
    for i in list(error_indices):
        for j in range(i - 1, -1, -1):
            if isinstance(messages[j], dict) and messages[j].get('role') == 'user':
                error_indices.add(j)
                break
    return [m for i, m in enumerate(messages) if i not in error_indices]


class Session:
    def __init__(self, session_id: str=None, title: str='Untitled',
                 workspace=str(DEFAULT_WORKSPACE), model=DEFAULT_MODEL,
                 messages=None, created_at=None, updated_at=None,
                 tool_calls=None, pinned: bool=False, archived: bool=False,
                 project_id: str=None, profile=None,
                 input_tokens: int=0, output_tokens: int=0, estimated_cost=None,
                 personality=None,
                 active_stream_id: str=None,
                 pending_user_message: str=None,
                 pending_attachments=None,
                 pending_started_at=None,
                 compression_anchor_visible_idx=None,
                 compression_anchor_message_key=None,
                 skills=None,
                 last_seen_msg_count=None,
                 mode: str='manual',
                 reasoning_effort: str=None,
                 service_tier: str=None,
                 parent_session_id: str=None,
                 compression_segment_count: int=0,
                 context_length: int=0,
                 last_prompt_tokens: int=0,
                 threshold_tokens: int=0,
                 last_compression_time: float=0,
                 lineage_root_id: str=None,
                 lineage_tip_id: str=None,
                 **kwargs):
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.title = title
        self.workspace = str(Path(workspace).expanduser().resolve())
        self.model = model
        self.messages = messages or []
        self.tool_calls = tool_calls or []
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()
        self.pinned = bool(pinned)
        self.archived = bool(archived)
        self.project_id = project_id or None
        self.profile = profile
        self.input_tokens = input_tokens or 0
        self.output_tokens = output_tokens or 0
        self.estimated_cost = estimated_cost
        self.personality = personality
        self.active_stream_id = active_stream_id
        self.skills = skills if isinstance(skills, list) else None
        self.pending_user_message = pending_user_message
        self.pending_attachments = pending_attachments or []
        self.pending_started_at = pending_started_at
        self.compression_anchor_visible_idx = compression_anchor_visible_idx
        self.compression_anchor_message_key = compression_anchor_message_key
        self.last_seen_msg_count = last_seen_msg_count  # None = never explicitly seen
        self._mode = mode if mode in ('manual', 'smart', 'yolo') else 'manual'
        self.reasoning_effort = reasoning_effort  # None = use global config.yaml default
        self.service_tier = service_tier  # None = normal, "priority" = fast mode enabled
        self._metadata_message_count = None
        # Compression lineage (v1 — WebUI-native sessions only)
        self.parent_session_id = parent_session_id
        self.compression_segment_count = compression_segment_count or 0
        self.context_length = context_length or 0
        self.last_prompt_tokens = last_prompt_tokens or 0
        self.threshold_tokens = threshold_tokens or 0
        self.last_compression_time = last_compression_time or 0
        self.lineage_root_id = lineage_root_id
        self.lineage_tip_id = lineage_tip_id
        # Store any extra fields (e.g. session_approved, llm_title_generated) so
        # they survive a round-trip through save() → JSON → load().
        self.__dict__.update(kwargs)

    @property
    def mode(self) -> str:
        return getattr(self, '_mode', 'manual')

    @mode.setter
    def mode(self, value: str):
        if value not in ('manual', 'smart', 'yolo'):
            raise ValueError(f"Invalid mode: {value}")
        self._mode = value

    @property
    def path(self):
        return SESSION_DIR / f'{self.session_id}.json'

    def save(self, touch_updated_at: bool = True, skip_index: bool = False,
             sync_db: bool = True, write_json: bool = False) -> None:
        """Persist session to state.db (primary) and optionally to JSON (backup).

        State.db is the primary store; the JSON file write is retained as a
        safety net during the transition period and can be disabled with
        ``write_json=False`` once migration is complete.
        """
        if touch_updated_at:
            self.updated_at = time.time()

        # ── Stamp timestamps on messages lacking one ────────────────────
        _now_ts = int(time.time())
        for _m in self.messages:
            if isinstance(_m, dict) and not _m.get('timestamp') and not _m.get('_ts'):
                _m['timestamp'] = _now_ts

        # ── Strip error messages before persisting ────────────────────
        # Error assistant messages carry _error=True in memory but the flag
        # is not stored in state.db.  Once a session is reloaded, error
        # messages become indistinguishable from real replies and are sent
        # to the LLM as context.  Filter them out before any persistence.
        _persistable = _filter_persistable_messages(self.messages)

        # ── Primary: write to state.db ──────────────────────────────────
        db = _get_session_db()
        if db is not None:
            try:
                self._save_to_db(db, _persistable)
            except Exception:
                logger.warning("state.db save failed for %s", self.session_id,
                               exc_info=True)

        # ── Backup: write JSON file (transition safety net) ─────────────
        if write_json:
            self._save_to_json(_persistable)

        # ── Update sidebar index ────────────────────────────────────────
        if not skip_index:
            _write_session_index(updates=[self])

    # ── Internal save helpers ──────────────────────────────────────────

    def _save_to_db(self, db, messages=None) -> None:
        """Write session metadata + messages to state.db.

        If *messages* is provided, those are written; otherwise ``self.messages``.
        """
        sid = self.session_id
        _msgs = messages if messages is not None else self.messages

        # ── Ensure session row exists + update metadata ─────────────
        def _do_write(conn):
            conn.execute(
                """INSERT OR IGNORE INTO sessions (id, source, model, cwd,
                   parent_session_id, started_at)
                   VALUES (?, 'webui', ?, ?, ?, ?)""",
                (sid,
                 self.model,
                 self.workspace,
                 getattr(self, 'parent_session_id', None),
                 self.created_at or time.time()),
            )
            conn.execute(
                """UPDATE sessions SET
                   pinned = ?,
                   archived = ?,
                   project_id = ?,
                   profile = ?,
                   input_tokens = ?,
                   output_tokens = ?,
                   estimated_cost_usd = ?,
                   personality = ?,
                   reasoning_effort = ?,
                   service_tier = ?,
                   skills = ?,
                   mode = ?,
                   compression_anchor_visible_idx = ?,
                   compression_anchor_message_key = ?,
                   last_seen_msg_count = ?,
                   last_compression_time = ?,
                   session_approved = ?,
                   last_message_at = ?,
                   lineage_root_id = ?,
                   lineage_tip_id = ?,
                   compression_segment_count = ?,
                   context_length = ?,
                   last_prompt_tokens = ?,
                   threshold_tokens = ?,
                   auto_compress_disabled = ?,
                   auto_compress_fail_count = ?,
                   message_count = ?,
                   cwd = COALESCE(?, cwd)
                   WHERE id = ?""",
                (
                    int(bool(self.pinned)),
                    int(bool(self.archived)),
                    self.project_id,
                    self.profile,
                    self.input_tokens or 0,
                    self.output_tokens or 0,
                    self.estimated_cost,
                    self.personality,
                    getattr(self, 'reasoning_effort', None),
                    getattr(self, 'service_tier', None),
                    json.dumps(self.skills) if self.skills else None,
                    getattr(self, '_mode', 'manual'),
                    self.compression_anchor_visible_idx,
                    json.dumps(self.compression_anchor_message_key) if self.compression_anchor_message_key else None,
                    self.last_seen_msg_count,
                    self.last_compression_time or None,
                    json.dumps(getattr(self, 'session_approved', None))
                        if getattr(self, 'session_approved', None) else None,
                    self.updated_at,
                    getattr(self, 'lineage_root_id', None),
                    getattr(self, 'lineage_tip_id', None),
                    getattr(self, 'compression_segment_count', 0) or 0,
                    getattr(self, 'context_length', 0) or 0,
                    getattr(self, 'last_prompt_tokens', 0) or 0,
                    getattr(self, 'threshold_tokens', 0) or 0,
                    int(bool(getattr(self, 'auto_compress_disabled', False))),
                    getattr(self, 'auto_compress_fail_count', 0) or 0,
                    len(_msgs),
                    self.workspace,
                    sid,
                ),
            )

        db._execute_write(_do_write)

        # ── Title: set via set_session_title() which handles the UNIQUE
        # constraint (appends " #2", " #3", etc. on duplicates)
        final_title = self.title
        try:
            db.set_session_title(sid, final_title)
        except ValueError:
            base = self.title
            for n in range(2, 100):
                candidate = f"{base} #{n}"
                try:
                    db.set_session_title(sid, candidate)
                    final_title = candidate
                    break
                except ValueError:
                    continue
        if final_title != self.title:
            self.title = final_title

        # ── Write messages ──────────────────────────────────────────────
        if _msgs:
            try:
                db.replace_messages(sid, _msgs)
            except Exception:
                logger.debug("Failed to sync messages to state.db for %s", sid)

    def _save_to_json(self, messages=None) -> None:
        """Write session to JSON file (backup / transition safety net)."""
        _msgs = messages if messages is not None else self.messages
        METADATA_FIELDS = [
            'session_id', 'title', 'workspace', 'model', 'created_at', 'updated_at',
            'pinned', 'archived', 'project_id', 'profile',
            'input_tokens', 'output_tokens', 'estimated_cost',
            'personality', 'reasoning_effort', 'service_tier', 'active_stream_id', 'skills',
            'pending_user_message', 'pending_attachments', 'pending_started_at',
            'compression_anchor_visible_idx', 'compression_anchor_message_key',
            'last_seen_msg_count', 'mode', 'session_approved',
            'parent_session_id', 'compression_segment_count',
            'context_length', 'last_prompt_tokens', 'threshold_tokens',
            'last_compression_time',
            'lineage_root_id', 'lineage_tip_id',
        ]
        meta = {k: getattr(self, k, None) for k in METADATA_FIELDS}
        meta['messages'] = _msgs
        meta['tool_calls'] = self.tool_calls
        extra = {k: v for k, v in self.__dict__.items()
                 if k not in METADATA_FIELDS and k not in ('messages', 'tool_calls')
                 and not k.startswith('_')}
        payload = json.dumps({**meta, **extra}, ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix(f'.tmp.{os.getpid()}.{threading.current_thread().ident}')
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    # ── DB column → Session field mapping ──────────────────────────────
    # Columns that map directly (same name in both)
    _DB_DIRECT_FIELDS = (
        'title', 'model', 'pinned', 'archived', 'project_id', 'profile',
        'input_tokens', 'output_tokens', 'personality', 'reasoning_effort',
        'service_tier', 'skills', 'mode', 'parent_session_id',
        'compression_segment_count', 'compression_anchor_visible_idx',
        'compression_anchor_message_key', 'last_seen_msg_count',
        'last_compression_time', 'lineage_root_id', 'lineage_tip_id',
        'context_length', 'last_prompt_tokens', 'threshold_tokens',
        'auto_compress_disabled', 'auto_compress_fail_count',
    )
    # Columns that need name mapping or type conversion
    _DB_MAPPED_FIELDS = {
        'id': 'session_id',
        'cwd': 'workspace',
        'started_at': 'created_at',
        'last_message_at': 'updated_at',
        'estimated_cost_usd': 'estimated_cost',
    }

    @classmethod
    def _from_db_row(cls, row: dict, messages: list = None):
        """Build a Session from a state.db sessions table row + message list.

        ``row`` is a dict from ``SessionDB.get_session()``.
        ``messages`` is a list of message dicts from ``SessionDB.get_messages()``.
        """
        kwargs = {}

        # Direct-mapped fields
        for field in cls._DB_DIRECT_FIELDS:
            if field in row and row[field] is not None:
                kwargs[field] = row[field]

        # Name-mapped fields
        for db_col, session_attr in cls._DB_MAPPED_FIELDS.items():
            if db_col in row and row[db_col] is not None:
                kwargs[session_attr] = row[db_col]

        # Deserialize JSON columns
        for col in ('skills', 'session_approved', 'compression_anchor_message_key'):
            val = row.get(col)
            if isinstance(val, str) and val:
                try:
                    kwargs[col] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    kwargs[col] = None
            elif val is not None:
                kwargs[col] = val

        # Boolean columns stored as INTEGER
        for col in ('pinned', 'archived', 'auto_compress_disabled'):
            if col in kwargs:
                kwargs[col] = bool(kwargs[col])

        # Carry through any extra DB columns as kwargs so they survive
        # round-trips (e.g. llm_title_generated, session_approved already handled above)
        known = set(cls._DB_DIRECT_FIELDS) | set(cls._DB_MAPPED_FIELDS.keys())
        for k, v in row.items():
            if k not in known and v is not None and not k.startswith('_'):
                kwargs[k] = v

        kwargs['messages'] = messages or []
        # Gateway/CLI sessions are read-only
        if row.get('source') and row['source'] != 'webui':
            kwargs['_readonly'] = True
            kwargs['is_cli_session'] = True
            kwargs['source_tag'] = row['source']
        # Normalize: strip empty tool_calls (JSON "[]" stored in DB)
        # and convert None→absent. LLM APIs reject empty tool_calls arrays.
        for _m in kwargs['messages']:
            if isinstance(_m, dict):
                _tc = _m.get('tool_calls')
                if _tc is None or (isinstance(_tc, list) and not _tc):
                    _m.pop('tool_calls', None)
        kwargs['tool_calls'] = []
        return cls(**kwargs)

    @classmethod
    def load(cls, sid):
        """Load a full session (metadata + messages) from state.db."""
        if not sid or not all(c in '0123456789abcdefghijklmnopqrstuvwxyz_' for c in sid):
            return None
        db = _get_session_db()
        if db is None:
            return cls._load_json_fallback(sid)
        try:
            row = db.get_session(sid)
        except Exception:
            logger.debug("state.db read failed for %s — falling back to JSON", sid)
            return cls._load_json_fallback(sid)
        if row is None:
            return cls._load_json_fallback(sid)
        try:
            messages = db.get_messages(sid, include_inactive=True)
        except Exception:
            messages = []
        return cls._from_db_row(row, messages)

    @classmethod
    def _load_json_fallback(cls, sid):
        """Fallback: load session from JSON file (used when state.db unavailable)."""
        p = SESSION_DIR / f'{sid}.json'
        if not p.exists():
            return None
        return cls(**json.loads(p.read_text(encoding='utf-8')))

    @classmethod
    def load_metadata_only(cls, sid):
        """Load session metadata from state.db without messages.

        Used for fast sidebar switching — avoids loading the full message
        array.  Falls back to JSON when state.db is unavailable.
        """
        if not sid or not all(c in '0123456789abcdefghijklmnopqrstuvwxyz_' for c in sid):
            return None
        db = _get_session_db()
        if db is None:
            return cls._load_json_metadata_fallback(sid)
        try:
            row = db.get_session(sid)
        except Exception:
            logger.debug("state.db metadata read failed for %s", sid)
            return cls._load_json_metadata_fallback(sid)
        if row is None:
            return cls._load_json_metadata_fallback(sid)
        session = cls._from_db_row(row, [])
        # message_count from DB is authoritative; use it for the metadata hint
        msg_count = row.get('message_count')
        if isinstance(msg_count, int) and msg_count >= 0:
            session._metadata_message_count = msg_count
        return session

    @classmethod
    def _load_json_metadata_fallback(cls, sid):
        """Fallback: load metadata from JSON prefix (used when state.db unavailable)."""
        p = SESSION_DIR / f'{sid}.json'
        if not p.exists():
            return None
        try:
            prefix = _read_metadata_json_prefix(p)
            if not prefix:
                return cls.load(sid)
            parsed = json.loads(prefix)
            needed = {'session_id', 'title', 'created_at', 'updated_at'}
            if not needed.issubset(parsed.keys()):
                return cls.load(sid)
            parsed['messages'] = []
            parsed['tool_calls'] = []
            session = cls(**parsed)
            session._metadata_message_count = _lookup_index_message_count(sid)
            return session
        except Exception:
            return cls.load(sid)

    def compact(self, include_runtime=False, active_stream_ids=None) -> dict:
        active_stream_ids = active_stream_ids if active_stream_ids is not None else set()
        # Clamp last_seen_msg_count: it should never exceed message_count.
        # When last_seen > message_count, it means history was cleared or
        # compressed. Clamp to message_count (= "seen all current messages").
        # The actual data-level fix happens in _fix_stale_last_seen_msg_count()
        # called when loading a session for viewing.
        _msg_count = (
            self._metadata_message_count
            if self._metadata_message_count is not None
            else len(self.messages)
        )
        _clamped_last_seen = min(self.last_seen_msg_count or 0, _msg_count)
        return {
            'session_id': self.session_id,
            'title': self.title,
            'workspace': self.workspace,
            'model': self.model,
            'message_count': _msg_count,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'last_message_at': _last_message_timestamp(self.messages) or self.updated_at,
            'pinned': self.pinned,
            'archived': self.archived,
            'project_id': self.project_id,
            'profile': self.profile,
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
            'estimated_cost': self.estimated_cost,
            'personality': self.personality,
            'reasoning_effort': getattr(self, 'reasoning_effort', None),
            'service_tier': getattr(self, 'service_tier', None),
            'skills': self.skills,
            'last_seen_msg_count': _clamped_last_seen,
            'compression_anchor_visible_idx': self.compression_anchor_visible_idx,
            'compression_anchor_message_key': self.compression_anchor_message_key,
            'mode': getattr(self, 'mode', 'manual'),
            '_parent_session_id': getattr(self, 'parent_session_id', None),
            '_compression_segment_count': getattr(self, 'compression_segment_count', 0) or 0,
            '_lineage_root_id': getattr(self, 'lineage_root_id', None),
            '_lineage_tip_id': getattr(self, 'lineage_tip_id', None),
            'context_length': getattr(self, 'context_length', 0) or 0,
            'last_prompt_tokens': getattr(self, 'last_prompt_tokens', 0) or 0,
            'threshold_tokens': getattr(self, 'threshold_tokens', 0) or 0,
            'last_compression_time': getattr(self, 'last_compression_time', 0) or 0,
            'active_stream_id': self.active_stream_id,
            'is_streaming': _is_streaming_session(
                self.active_stream_id, active_stream_ids
            ) if include_runtime else False,
            # Gateway/CLI session markers (set by _from_db_row for non-webui sessions)
            '_readonly': getattr(self, '_readonly', None),
            'is_cli_session': getattr(self, 'is_cli_session', None),
            'source_tag': getattr(self, 'source_tag', None),
            'auto_compress_disabled': getattr(self, 'auto_compress_disabled', False),
        }

def get_session(sid, metadata_only=False):
    """Load a session, optionally with metadata only (skipping the messages array).

    Metadata-only loads intentionally do not populate the full-session cache.
    Otherwise a later full load could return a compact object with an empty
    messages list. Use this when you only need compact() metadata and not the
    actual message history (e.g., for fast sidebar switching).
    """
    with LOCK:
        if sid in SESSIONS:
            cached = SESSIONS[sid]
            # External-source sessions (gateway/CLI) are written by a
            # separate process — always reload from state.db so new
            # messages are visible without a server restart.
            if not getattr(cached, 'is_cli_session', None):
                SESSIONS.move_to_end(sid)
                return cached
            # Stale cache for external session — evict and fall through to reload
            SESSIONS.pop(sid, None)
    if metadata_only:
        s = Session.load_metadata_only(sid)
        if s:
            return s
    else:
        s = Session.load(sid)
    if s:
        # Restore persisted session approvals into the in-memory approval set
        # so they survive server restarts.
        _saved = getattr(s, 'session_approved', None)
        if _saved and isinstance(_saved, list) and len(_saved) > 0:
            try:
                from tools.approval import restore_session_approvals
                restore_session_approvals(sid, _saved)
            except ImportError:
                pass
        with LOCK:
            SESSIONS[sid] = s
            SESSIONS.move_to_end(sid)
            while len(SESSIONS) > SESSIONS_MAX:
                SESSIONS.popitem(last=False)  # evict least recently used
        return s
    raise KeyError(sid)


def _fix_stale_last_seen_msg_count(s):
    """Detect and fix sessions where last_seen_msg_count > message_count.

    This condition occurs when history was cleared (or compressed) but
    last_seen_msg_count was not reset — a bug in earlier clear-history code.
    When detected, clamp last_seen_msg_count to message_count (= "seen all
    current messages") and persist the fix to disk.
    """
    actual_msg_count = len(s.messages)
    last_seen = s.last_seen_msg_count or 0
    if last_seen > actual_msg_count:
        logger.info(
            "[fix-stale-last_seen] session=%s title='%s' "
            "last_seen_msg_count=%d > actual_message_count=%d — "
            "clamping to %d (history was cleared)",
            s.session_id, s.title, last_seen, actual_msg_count,
            actual_msg_count,
        )
        s.last_seen_msg_count = actual_msg_count
        try:
            s.save(touch_updated_at=False)
        except Exception:
            logger.debug(
                "Failed to persist stale last_seen fix for session %s",
                s.session_id,
            )


def new_session(workspace=None, model=None, profile=None):
    """Create a new in-memory session.

    The session lives in the SESSIONS dict only — no disk write happens until
    the first message is appended (#1171 follow-up).  This avoids the
    "ghost Untitled session on disk" pile-up that occurred when users clicked
    New Conversation, reloaded the page, or completed onboarding without ever
    sending a message.  Subsequent code paths that populate state immediately
    (btw / background agent at api/routes.py) call ``s.save()`` themselves
    after setting title/messages, and ``_handle_chat_start`` saves the
    session as soon as the user actually sends a message — both are the
    natural first-write moments for a real session.

    Crash-safety: if the process exits between session creation and first
    message, the session is lost.  Since it had no messages, there is
    nothing to lose.

    *profile* — when supplied by the caller (e.g. from the request body sent
    by the active browser tab), it is used directly so that concurrent clients
    on different profiles don't fight over a shared process-global.  If not
    supplied, we fall back to the process-level active profile (the pre-#798
    behaviour, preserved for calls that originate outside a request context).
    """
    if profile is None:
        # Fallback: read process-level global (single-client or startup path)
        try:
            from api.profiles import get_active_profile_name
            profile = get_active_profile_name()
        except ImportError:
            profile = None
    effective_model = model or get_effective_default_model()
    # Inherit default_mode from global settings for new sessions
    try:
        from api.config import load_settings
        default_mode = load_settings().get("default_mode", "manual")
    except Exception:
        default_mode = "manual"
    # Inherit reasoning_effort from global config.yaml for new sessions
    try:
        from api.config import _load_yaml_config_file, _get_config_path
        _config_data = _load_yaml_config_file(_get_config_path())
        _agent_cfg = _config_data.get("agent") if isinstance(_config_data, dict) else {}
        _effort_default = _agent_cfg.get("reasoning_effort") if isinstance(_agent_cfg, dict) else None
    except Exception:
        _effort_default = None
    s = Session(
        workspace=workspace or get_last_workspace(),
        model=effective_model,
        profile=profile,
        mode=default_mode,
        reasoning_effort=_effort_default,
    )
    with LOCK:
        SESSIONS[s.session_id] = s
        SESSIONS.move_to_end(s.session_id)
        while len(SESSIONS) > SESSIONS_MAX:
            SESSIONS.popitem(last=False)
    return s

def _hide_from_default_sidebar(session: dict) -> bool:
    """Return True for internal/background sessions hidden from the default list."""
    sid = str(session.get('session_id') or '')
    source = session.get('source_tag') or session.get('source')
    return source == 'cron' or sid.startswith('cron_')


def _append_archived_sessions(result):
    """Append archived (read-only compaction snapshots) from *archived/ subdirectories."""
    try:
        for archive_dir in sorted(SESSION_DIR.glob('*_archived')):
            parent_id = archive_dir.name[:-len('_archived')]
            for p in sorted(archive_dir.glob('compaction_*.json')):
                try:
                    data = json.loads(p.read_text(encoding='utf-8'))
                    result.append({
                        'session_id': f'{parent_id}/{p.stem}',
                        'title': data.get('title', ''),
                        'workspace': data.get('workspace', ''),
                        'model': data.get('model'),
                        'message_count': len(data.get('messages', [])),
                        'created_at': data.get('created_at', 0),
                        'updated_at': data.get('updated_at', 0),
                        'archived': False,
                        'pinned': False,
                        'project_id': data.get('project_id'),
                        'profile': data.get('profile'),
                        '_parent_session_id': parent_id,
                        '_readonly': True,
                        '_compaction_n': data.get('_compaction_n', 0),
                    })
                except Exception:
                    logger.debug("Failed to read archived session %s", p)
    except Exception:
        pass
    return result


def all_sessions():
    """Return compact session list for the sidebar.

    Primary: query state.db via list_sessions_rich for O(1) indexed access.
    Fallback: JSON index file or directory scan when state.db is unavailable.
    """
    active_stream_ids = _active_stream_ids()

    # ── Primary: state.db query ────────────────────────────────────────
    db = _get_session_db()
    if db is not None:
        try:
            result = _all_sessions_from_db(db, active_stream_ids)
            if result is not None:
                return result
        except Exception:
            logger.debug("state.db session list failed, falling back to JSON index")

    # ── Fallback: JSON index or directory scan ─────────────────────────
    return _all_sessions_from_json(active_stream_ids)


def _all_sessions_from_db(db, active_stream_ids):
    """Build session list from state.db."""
    try:
        rows = db.list_sessions_rich(source='webui', order_by_last_active=True)
    except Exception:
        return None
    if not rows:
        return []

    compact_list = []
    for row in rows:
        sid = row.get('id') or row.get('session_id')
        compact = {
            'session_id': sid,
            'title': row.get('title', 'Untitled'),
            'workspace': row.get('cwd', ''),
            'model': row.get('model', ''),
            'message_count': row.get('message_count', 0),
            'created_at': row.get('started_at', 0),
            'updated_at': row.get('last_message_at') or row.get('started_at', 0),
            'last_message_at': row.get('last_message_at') or row.get('started_at', 0),
            'pinned': bool(row.get('pinned', False)),
            'archived': bool(row.get('archived', False)),
            'project_id': row.get('project_id'),
            'profile': row.get('profile') or 'default',
            'input_tokens': row.get('input_tokens', 0),
            'output_tokens': row.get('output_tokens', 0),
            'estimated_cost': row.get('estimated_cost_usd'),
            'personality': row.get('personality'),
            'reasoning_effort': row.get('reasoning_effort'),
            'service_tier': row.get('service_tier'),
            'skills': _deserialize_json_field(row.get('skills')),
            'last_seen_msg_count': row.get('last_seen_msg_count'),
            'compression_anchor_visible_idx': row.get('compression_anchor_visible_idx'),
            'compression_anchor_message_key': row.get('compression_anchor_message_key'),
            'mode': row.get('mode', 'manual'),
            '_parent_session_id': row.get('parent_session_id'),
            '_compression_segment_count': row.get('compression_segment_count', 0) or 0,
            '_lineage_root_id': row.get('lineage_root_id'),
            '_lineage_tip_id': row.get('lineage_tip_id'),
            'context_length': row.get('context_length', 0) or 0,
            'last_prompt_tokens': row.get('last_prompt_tokens', 0) or 0,
            'threshold_tokens': row.get('threshold_tokens', 0) or 0,
            'last_compression_time': row.get('last_compression_time', 0) or 0,
            'is_streaming': False,
        }
        compact_list.append(compact)

    # Overlay in-memory sessions (may have runtime state not yet flushed to DB)
    with LOCK:
        for s in SESSIONS.values():
            found = False
            for c in compact_list:
                if c['session_id'] == s.session_id:
                    c.update(s.compact(include_runtime=True,
                                       active_stream_ids=active_stream_ids))
                    found = True
                    break
            if not found:
                compact_list.append(s.compact(include_runtime=True,
                                               active_stream_ids=active_stream_ids))

    # Filter empty Untitled sessions
    result = [s for s in compact_list if not (
        s.get('title', 'Untitled') == 'Untitled'
        and s.get('message_count', 0) == 0
        and (s.get('input_tokens') or 0) == 0
        and abs((s.get('updated_at') or 0) - (s.get('created_at') or 0)) < 5
    )]
    result = [s for s in result if not _hide_from_default_sidebar(s)]
    result = _append_archived_sessions(result)
    return result


def _deserialize_json_field(val):
    """Deserialize a JSON-encoded TEXT column, or pass through if already parsed."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return None
    return val


def _all_sessions_from_index(active_stream_ids):
    """Build session list from _index.json (legacy fast path)."""
    index = json.loads(SESSION_INDEX_FILE.read_text(encoding='utf-8'))
    index = [
        s for s in index
        if _index_entry_exists(s.get('session_id'))
    ]
    backfilled = []
    for i, s in enumerate(index):
        if 'last_message_at' not in s:
            full = Session.load(s.get('session_id'))
            if full:
                index[i] = full.compact()
                backfilled.append(full)
    if backfilled:
        try:
            _write_session_index(updates=backfilled)
        except Exception:
            logger.debug("Failed to persist last_message_at backfill")
    for s in index:
        s['is_streaming'] = _is_streaming_session(
            s.get('active_stream_id'),
            active_stream_ids,
        )
    # Overlay any in-memory sessions that may be newer than the index
    index_map = {s['session_id']: s for s in index}
    with LOCK:
        for s in SESSIONS.values():
            index_map[s.session_id] = s.compact(
                include_runtime=True,
                active_stream_ids=active_stream_ids,
            )
    result = sorted(index_map.values(), key=lambda s: (s.get('pinned', False), _session_sort_timestamp(s)), reverse=True)
    result = [s for s in result if not (
        s.get('title', 'Untitled') == 'Untitled'
        and s.get('message_count', 0) == 0
        and (s.get('input_tokens') or 0) == 0
        and abs((s.get('updated_at') or 0) - (s.get('created_at') or 0)) < 5
    )]
    result = [s for s in result if not _hide_from_default_sidebar(s)]
    for s in result:
        if not s.get('profile'):
            s['profile'] = 'default'
    result = _append_archived_sessions(result)
    return result


def _all_sessions_from_json(active_stream_ids):
    """Legacy fallback: build session list from JSON index or directory scan."""
    # Phase C: try index first for O(1) read; fall back to full scan
    if SESSION_INDEX_FILE.exists():
        try:
            return _all_sessions_from_index(active_stream_ids)
        except Exception:
            logger.debug("Failed to load session index, falling back to full scan")
    # Full scan fallback
    out = []
    for p in SESSION_DIR.glob('*.json'):
        if p.name.startswith('_'): continue
        try:
            s = Session.load(p.stem)
            if s: out.append(s)
        except Exception:
            logger.debug("Failed to load session from %s", p)
    for s in SESSIONS.values():
        if all(s.session_id != x.session_id for x in out): out.append(s)
    out.sort(key=lambda s: (getattr(s, 'pinned', False), _session_sort_timestamp(s)), reverse=True)
    # Hide empty Untitled sessions from the UI entirely — kept consistent with the
    # index-path filter above. No grace window: a 0-message Untitled session is
    # never shown regardless of age (#1171).
    # Exception: sessions that have had prior activity are NOT hidden.
    result = [s.compact(include_runtime=True, active_stream_ids=active_stream_ids) for s in out if not (
        s.title == 'Untitled'
        and len(s.messages) == 0
        and (s.input_tokens or 0) == 0
        and abs((s.updated_at or 0) - (s.created_at or 0)) < 5
    )]
    result = [s for s in result if not _hide_from_default_sidebar(s)]
    for s in result:
        if not s.get('profile'):
            s['profile'] = 'default'
    result = _append_archived_sessions(result)
    return result


def title_from(messages, fallback: str='Untitled'):
    """Derive a session title from the first user message."""
    for m in messages:
        if m.get('role') == 'user':
            c = m.get('content', '')
            if isinstance(c, list):
                c = ' '.join(p.get('text', '') for p in c if isinstance(p, dict) and p.get('type') == 'text')
            text = str(c).strip()
            if text:
                return text[:64]
    return fallback


# ── Migration: JSON → state.db ──────────────────────────────────────────────

_MIGRATION_META_KEY = 'webui_migration_complete'


def migrate_json_sessions_to_db(force: bool = False) -> dict:
    """One-time migration: import all JSON sessions into state.db.

    Scans ``SESSION_DIR/*.json``, loads each session, and writes it to
    state.db.  Idempotent — sessions that already exist in state.db are
    skipped.  Tracks completion via a ``state_meta`` key so subsequent
    startups skip the migration automatically.

    Set ``force=True`` to re-run even if the migration marker exists.

    Returns a dict with ``{imported, skipped, errors, total}`` counts.
    """
    db = _get_session_db()
    if db is None:
        return {'imported': 0, 'skipped': 0, 'errors': 0, 'total': 0,
                'message': 'state.db unavailable'}

    # Check if already migrated
    if not force:
        try:
            marker = db.get_meta(_MIGRATION_META_KEY)
            if marker and marker.get('value') == '1':
                return {'imported': 0, 'skipped': 0, 'errors': 0, 'total': 0,
                        'message': 'already migrated'}
        except Exception:
            pass  # state_meta table might not exist yet

    json_files = sorted(SESSION_DIR.glob('*.json'))
    result = {'imported': 0, 'skipped': 0, 'errors': 0, 'total': len(json_files)}

    for p in json_files:
        if p.name.startswith('_'):
            result['skipped'] += 1
            continue
        sid = p.stem
        try:
            # Check if already in state.db
            existing = db.get_session(sid)
            if existing is not None and not force:
                result['skipped'] += 1
                continue

            # Load from JSON and save to state.db
            data = json.loads(p.read_text(encoding='utf-8'))
            messages = data.pop('messages', [])
            data.pop('tool_calls', None)
            # Remove runtime-only fields that shouldn't be persisted
            for rk in ('active_stream_id', 'pending_user_message',
                       'pending_attachments', 'pending_started_at'):
                data.pop(rk, None)
            s = Session(**data)
            s.messages = messages
            s.save(write_json=False, skip_index=True)
            result['imported'] += 1
            logger.info("Migrated session %s to state.db", sid)
        except Exception:
            logger.warning("Failed to migrate session %s to state.db", sid,
                           exc_info=True)
            result['errors'] += 1

    # Mark migration complete
    try:
        db.set_meta(_MIGRATION_META_KEY, '1')
    except Exception:
        logger.debug("Failed to set migration marker")

    logger.info("JSON→DB migration: %d imported, %d skipped, %d errors, %d total",
                result['imported'], result['skipped'], result['errors'],
                result['total'])
    return result


# ── Project helpers ──────────────────────────────────────────────────────────

def load_projects() -> list:
    """Load project list from disk. Returns list of project dicts."""
    if not PROJECTS_FILE.exists():
        return []
    try:
        return json.loads(PROJECTS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return []

def save_projects(projects) -> None:
    """Write project list to disk."""
    PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding='utf-8')


def import_cli_session(
    session_id: str,
    title: str,
    messages,
    model: str='unknown',
    profile=None,
    created_at=None,
    updated_at=None,
):
    """Create a new WebUI session populated with CLI messages.
    Returns the Session object.
    """
    s = Session(
        session_id=session_id,
        title=title,
        workspace=get_last_workspace(),
        model=model,
        messages=messages,
        profile=profile,
        created_at=created_at,
        updated_at=updated_at,
    )
    s.save(touch_updated_at=False)
    return s


# ── CLI session bridge ──────────────────────────────────────────────────────

def _resolve_gateway_workspace() -> str:
    """Resolve the actual working directory for gateway sessions (no cwd in state.db).

    Priority: TERMINAL_CWD env var → config.yaml terminal.cwd → home directory.
    This mirrors the gateway process's own cwd resolution in gateway/run.py.
    """
    import os as _os
    # 1. TERMINAL_CWD environment variable (set by .env or the gateway config bridge)
    _env_cwd = _os.environ.get('TERMINAL_CWD', '').strip()
    if _env_cwd and _env_cwd not in {'.', 'auto', 'cwd'}:
        return str(Path(_env_cwd).expanduser().resolve())
    # 2. config.yaml terminal.cwd (canonical source for the gateway)
    try:
        from api.config import _load_yaml_config_file, _get_config_path
        _cfg = _load_yaml_config_file(_get_config_path())
        _terminal_cfg = _cfg.get('terminal', {})
        if isinstance(_terminal_cfg, dict):
            _cfg_cwd = str(_terminal_cfg.get('cwd', '')).strip()
            if _cfg_cwd and _cfg_cwd not in {'.', 'auto', 'cwd'}:
                return str(Path(_cfg_cwd).expanduser().resolve())
    except Exception:
        pass
    # 3. Fall back to home directory
    return str(Path.home())


def get_cli_sessions() -> list:
    """Read CLI sessions from the agent's SQLite store and return them as
    dicts in a format the WebUI sidebar can render alongside local sessions.

    Returns empty list if the SQLite DB is missing or any error occurs -- the
    bridge is purely additive and never crashes the WebUI.
    """
    import os
    cli_sessions = []

    # Use the active WebUI profile's HERMES_HOME to find state.db.
    # The active profile is determined by what the user has selected in the UI
    # (stored in the server's runtime config). This means:
    #   - default profile  -> ~/.hermes/state.db
    #   - named profile X  -> ~/.hermes/profiles/X/state.db
    # We resolve the active profile's home directory rather than just using
    # HERMES_HOME (which is the server's launch profile, not necessarily the
    # active one after a profile switch).
    try:
        from api.profiles import get_active_hermes_home
        hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        hermes_home = Path(os.getenv('HERMES_HOME', str(HOME / '.hermes'))).expanduser().resolve()

    db_path = hermes_home / 'state.db'
    if not db_path.exists():
        return cli_sessions

    # Try to resolve the active CLI profile so imported sessions integrate
    # with the WebUI profile filter (available since Sprint 22).
    try:
        from api.profiles import get_active_profile_name
        _cli_profile = get_active_profile_name()
    except ImportError:
        _cli_profile = None  # older agent -- fall back to no profile

    try:
        for row in read_importable_agent_session_rows(db_path, limit=200, log=logger, exclude_sources=None):
            sid = row['id']
            raw_ts = row['last_activity'] or row['started_at']
            # Prefer the CLI session's own profile from the DB; fall back to
            # the active CLI profile so sidebar filtering works either way.
            profile = _cli_profile  # CLI DB has no profile column; use active profile

            _source = row['source'] or 'cli'
            _title = row['title'] or row.get('first_user_message')
            if not _title and _source == 'cron' and sid.startswith('cron_'):
                # Extract job_id from session ID (cron_{job_id}_{timestamp})
                # and look up the human-friendly job name from jobs.json
                parts = sid.split('_')
                if len(parts) >= 3:
                    _job_id = parts[1]
                    try:
                        _jobs_path = hermes_home / 'cron' / 'jobs.json'
                        if _jobs_path.exists():
                            import json as _json
                            _jobs_data = _json.loads(_jobs_path.read_text())
                            for _j in _jobs_data.get('jobs', []):
                                if _j.get('id') == _job_id:
                                    _title = _j.get('name') or _title
                                    break
                    except Exception:
                        pass  # degrade gracefully
            _user_id = row.get('user_id') or os.environ.get('USER', 'default')
            # Use cwd from state.db as workspace when available (CLI sessions have a real working directory).
            # For sessions without cwd (gateway platforms), resolve the actual gateway working directory
            # from TERMINAL_CWD env var → config.yaml terminal.cwd → home directory.
            _cwd = row.get('cwd')
            _workspace = str(Path(_cwd).expanduser().resolve()) if _cwd else _resolve_gateway_workspace()
            _display_title = _title or f'{_source.title()} Session'
            cli_sessions.append({
                'session_id': sid,
                'title': _display_title,
                'workspace': _workspace,
                'model': row['model'] or None,
                'message_count': row['message_count'] or row['actual_message_count'] or 0,
                'created_at': row['started_at'],
                'updated_at': raw_ts,
                'pinned': False,
                'archived': False,
                'project_id': None,
                'profile': profile,
                'source_tag': _source,
                'is_cli_session': True,
                'user_id': _user_id,
            })
    except Exception as _cli_err:
        # DB schema changed, locked, or corrupted -- log warning so admins can diagnose.
        # Still degrade gracefully (don't crash the WebUI).
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "get_cli_sessions() failed — check state.db schema or path (%s): %s",
            db_path, _cli_err,
        )
        return []

    return cli_sessions


def get_cli_session_messages(sid) -> list:
    """Read messages for a single CLI session from the SQLite store.
    Returns a list of {role, content, timestamp, tool_call_id, tool_name, tool_calls} dicts.
    Returns empty list on any error.
    """
    import os, json as _json
    try:
        import sqlite3
    except ImportError:
        return []

    try:
        from api.profiles import get_active_hermes_home
        hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        hermes_home = Path(os.getenv('HERMES_HOME', str(HOME / '.hermes'))).expanduser().resolve()
    db_path = hermes_home / 'state.db'
    if not db_path.exists():
        return []

    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT role, content, timestamp, tool_call_id, tool_name, tool_calls
                FROM messages
                WHERE session_id = ? AND active = 1
                ORDER BY timestamp ASC
            """, (sid,))
            msgs = []
            for row in cur.fetchall():
                # Skip internal metadata rows (session lifecycle markers)
                if row['role'] in ('session_meta',):
                    continue
                msg = {
                    'role': row['role'],
                    'content': row['content'],
                    'timestamp': row['timestamp'],
                }
                if row['tool_call_id']:
                    msg['tool_call_id'] = row['tool_call_id']
                if row['tool_name']:
                    msg['tool_name'] = row['tool_name']
                if row['tool_calls']:
                    try:
                        msg['tool_calls'] = _json.loads(row['tool_calls'])
                    except (_json.JSONDecodeError, TypeError):
                        pass  # keep as-is if unparseable
                msgs.append(msg)
    except Exception:
        return []
    return msgs


def delete_cli_session(sid) -> bool:
    """Delete a CLI session from state.db (messages + session row).
    Returns True if deleted, False if not found or error.
    """
    import os
    try:
        import sqlite3
    except ImportError:
        return False

    try:
        from api.profiles import get_active_hermes_home
        hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        hermes_home = Path(os.getenv('HERMES_HOME', str(HOME / '.hermes'))).expanduser().resolve()
    db_path = hermes_home / 'state.db'
    if not db_path.exists():
        return False

    try:
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
            cur.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            conn.commit()
            return cur.rowcount > 0
    except Exception:
        return False
