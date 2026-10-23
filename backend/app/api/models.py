"""Hermes Web UI -- Session model and in-memory session store."""
import collections
import json
import logging
import os
import queue
import threading
import time
import uuid
from pathlib import Path

import app.api.config as _cfg
from app.api.config import (
    SESSION_DIR, SESSION_INDEX_FILE, SESSIONS, SESSIONS_MAX,
    LOCK, DEFAULT_WORKSPACE, DEFAULT_MODEL, PROJECTS_FILE, HOME,
    get_effective_default_model, get_first_available_model,
)
from app.api.agent_sessions import read_importable_agent_session_rows
from app.api.stream_manager import stream_manager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stale temp-file cleanup
# ---------------------------------------------------------------------------
# Both Session.sync_webui_metadata() and _write_session_index() use the atomic-write pattern:
#   write to  <path>.tmp.<pid>.<tid>  →  os.replace() to final path
# If the process crashes between write and replace the .tmp file is left
# behind.  Because the name embeds pid + tid, leftover files can never be
# reused by a different process/thread, so they are safe to remove on the
# next startup.  _cleanup_stale_tmp_files() is called from the full-rebuild
# path of _write_session_index (i.e. at first index access / startup) and
# removes any *.tmp.* file whose mtime is older than one hour.
# ---------------------------------------------------------------------------

_STALE_TMP_AGE_SECONDS = 3600  # 1 hour

# Serializes index writers so concurrent Session.sync_webui_metadata() calls cannot race on
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


def _active_session_ids():
    return set(stream_manager.get_active_session_ids())


def _is_streaming_session(session_id, active_session_ids):
    """True iff this session_id currently has an active stream registered.

    `active_session_ids` is the set of session_ids with non-ended streams
    (see stream_manager.get_active_session_ids). Membership check is by
    session_id, not by stream_id.
    """
    return bool(session_id and session_id in active_session_ids)

def _session_sort_timestamp(session):
    if isinstance(session, dict):
        return session.get('last_activity_at') or session.get('updated_at') or 0
    return getattr(session, 'updated_at', 0) or 0


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
        from app.api.profiles import get_active_hermes_home
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
        try:
            # Always try to open: SessionDB.__init__ creates the file (with
            # schema) if it doesn't exist, so webui's first sync_webui_metadata
            # on a fresh server (or after the test-suite nuke) lands somewhere
            # visible to subsequent reads.  This also keeps /api/sessions from
            # silently dropping webui sessions that exist in the SESSIONS dict
            # but were never persisted because state.db was missing at write
            # time — the list_sessions_rich(source='webui') filter would
            # otherwise exclude them even though they exist in memory.
            _session_db = SessionDB(db_path)
            # Trigger column reconciliation so any newly-added SCHEMA_SQL
            # columns are added to the live DB before we read/write.
            if not _session_db._fts_enabled:
                # read_only path skipped FTS probe — but we're read-write,
                # so _init_schema already ran and _fts_enabled should be set.
                pass
            # Ensure __webui columns exist and have correct types.
            # check_webui_column_types() raises IncompatibleSchemaError on
            # type mismatch — propagate it so the process exits.
            from app.api.webui_schema import check_webui_column_types, ensure_webui_columns, ensure_original_content_webui_column, ensure_timestamp_webui_column, IncompatibleSchemaError
            check_webui_column_types(_session_db)
            ensure_webui_columns(_session_db)
            ensure_original_content_webui_column(_session_db)
            ensure_timestamp_webui_column(_session_db)
        except IncompatibleSchemaError:
            raise
        except Exception:
            logger.warning("Failed to open state.db — falling back to JSON storage",
                           exc_info=True)
            return None
    return _session_db


def _close_session_db():
    """Close the shared SessionDB connection. Idempotent and shutdown-safe.

    Designed to be safe to call from:
      - Normal request paths (server shutdown, test fixture teardown)
      - atexit handlers (sys.meta_path still alive)
      - pytest fixture finalizers (sys.meta_path still alive)
      - The SDK's internal __del__ path (sys.meta_path may be None —
        failures swallowed by BaseException)

    The trailing gc.collect() is load-bearing: it forces the SDK's
    SessionDB instance and its internal sqlite3.Connection objects to
    run __del__ NOW, while sys.meta_path is still populated. Without
    it, the SDK's __del__ may sit on the cyclic-GC list and only be
    collected during interpreter shutdown — exactly when sys.meta_path
    is None, which is when

        "read-conn close failed for ... : sys.meta_path is None,
         Python is likely shutting down"

    gets emitted by the hermes-agent SDK's finalizer. Forcing a GC
    pass here makes the finalizer run while sys.meta_path is still
    alive, so the SDK's close path can import sqlite3 normally and
    the warning never fires.
    """
    global _session_db
    with _session_db_lock:
        if _session_db is not None:
            try:
                _session_db.close()
            except BaseException:
                # Closing the SDK SessionDB may raise from inside its
                # close() (e.g. the SDK tries to print a warning while
                # sys.meta_path is being cleared). Swallow ALL — the
                # whole point of this function is to suppress that
                # kind of late-shutdown noise.
                pass
            _session_db = None
    # Force immediate reclamation of the SDK's internal sqlite3
    # connections while sys.meta_path is still alive. This runs ONCE
    # per shutdown — not on any request hot path. The hot path uses
    # _get_session_db(), which never reaches here.
    try:
        import gc as _gc_close_db
        _gc_close_db.collect()
    except BaseException:
        pass


def _upsert_webui_col(db, rowid, sid, col_name, value):
    """Idempotently write a __webui column to the messages table.

    If the column already has a non-NULL value for this row, skip the
    UPDATE and print a log line for debugging.  Otherwise UPDATE the
    single row identified by *rowid*.
    """
    try:
        existing = db._conn.execute(
            f"SELECT {col_name} FROM messages WHERE rowid = ?", (rowid,)
        ).fetchone()
    except Exception:
        return  # column may not exist yet
    if existing and existing[0] is not None:
        logger.debug(
            f"[webui:backfill:skip] {col_name} already set "
            f"sid={sid} rowid={rowid} existing={existing[0]!r} new={value!r}"
        )
        return
    try:
        db._conn.execute(
            f"UPDATE messages SET {col_name} = ? WHERE rowid = ?",
            (value, rowid),
        )
    except Exception:
        pass  # non-fatal


class Session:
    def __init__(self, session_id: str=None, title: str='Untitled',
                 workspace=str(DEFAULT_WORKSPACE), model=DEFAULT_MODEL,
                 messages=None, created_at=None, updated_at=None,
                 tool_calls=None, pinned: bool=False, archived: bool=False,
                 project_id: str=None, profile=None,
                 input_tokens: int=0, output_tokens: int=0, estimated_cost=None,
                 personality=None,
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
        self.workspace = str(Path(workspace).expanduser().resolve()) if workspace else ''
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
        # Per-session 主循环（类似 CLI 的 process_loop）
        self._process_loop_thread = None
        self._process_loop_stop = threading.Event()
        self._process_loop_last_active = time.time()
        self._pending_input = queue.Queue()  # 统一队列：用户输入 + 完成事件
        # Store any extra fields (e.g. session_approved, llm_title_generated) so
        # they survive a round-trip through save() → JSON → load().
        self.__dict__.update(kwargs)

    # ── copy_from / clone ────────────────────────────────────────────────

    def copy_from(self, other: 'Session') -> None:
        """Update data fields from another Session in-place.

        Preserves runtime fields (session_id, process loop, pending state)
        that are managed by the streaming/threading infrastructure.
        All holders of this Session reference automatically see the updated data.
        """
        self.title = other.title
        self.workspace = getattr(other, 'workspace', None)
        self.model = other.model
        self.messages = list(other.messages)       # copy content, not reference
        self.tool_calls = list(other.tool_calls)
        self.created_at = other.created_at
        self.updated_at = other.updated_at
        self.pinned = other.pinned
        self.archived = other.archived
        self.project_id = other.project_id
        self.profile = other.profile
        self.input_tokens = other.input_tokens
        self.output_tokens = other.output_tokens
        self.estimated_cost = other.estimated_cost
        self.personality = other.personality
        self.skills = other.skills
        self.last_seen_msg_count = other.last_seen_msg_count
        self._mode = other._mode
        self.reasoning_effort = other.reasoning_effort
        self.service_tier = other.service_tier
        self._metadata_message_count = other._metadata_message_count
        self.parent_session_id = other.parent_session_id
        self.compression_segment_count = other.compression_segment_count
        self.context_length = other.context_length
        self.last_prompt_tokens = other.last_prompt_tokens
        self.threshold_tokens = other.threshold_tokens
        self.last_compression_time = other.last_compression_time
        self.lineage_root_id = other.lineage_root_id
        self.lineage_tip_id = other.lineage_tip_id
        self.compression_anchor_visible_idx = other.compression_anchor_visible_idx
        self.compression_anchor_message_key = other.compression_anchor_message_key
        # Preserve runtime fields: session_id, pending_*, _process_loop_*, _pending_input

    def clone(self) -> 'Session':
        """Return a frozen snapshot for read-only use (notify, compact, etc.).

        The snapshot has no runtime state (no process loop, no pending input).
        Mutable fields (messages, tool_calls) are deep-copied so the snapshot
        is immune to subsequent mutations on the original.
        """
        snapshot = Session.__new__(Session)
        snapshot.session_id = self.session_id
        snapshot.title = self.title
        snapshot.workspace = getattr(self, 'workspace', None)
        snapshot.model = getattr(self, 'model', None)
        snapshot.messages = list(self.messages)
        snapshot.tool_calls = list(self.tool_calls)
        snapshot.created_at = getattr(self, 'created_at', 0)
        snapshot.updated_at = getattr(self, 'updated_at', 0)
        snapshot.pinned = getattr(self, 'pinned', False)
        snapshot.archived = getattr(self, 'archived', False)
        snapshot.project_id = getattr(self, 'project_id', None)
        snapshot.profile = getattr(self, 'profile', None)
        snapshot.input_tokens = getattr(self, 'input_tokens', 0)
        snapshot.output_tokens = getattr(self, 'output_tokens', 0)
        snapshot.estimated_cost = getattr(self, 'estimated_cost', 0.0)
        snapshot.personality = getattr(self, 'personality', None)
        snapshot.skills = getattr(self, 'skills', None)
        snapshot.last_seen_msg_count = getattr(self, 'last_seen_msg_count', None)
        snapshot._mode = getattr(self, '_mode', None)
        snapshot.reasoning_effort = getattr(self, 'reasoning_effort', None)
        snapshot.service_tier = getattr(self, 'service_tier', None)
        snapshot._metadata_message_count = getattr(self, '_metadata_message_count', None)
        snapshot.parent_session_id = getattr(self, 'parent_session_id', None)
        snapshot.compression_segment_count = getattr(self, 'compression_segment_count', 0)
        snapshot.context_length = getattr(self, 'context_length', None)
        snapshot.last_prompt_tokens = getattr(self, 'last_prompt_tokens', None)
        snapshot.threshold_tokens = getattr(self, 'threshold_tokens', None)
        snapshot.last_compression_time = getattr(self, 'last_compression_time', None)
        snapshot.lineage_root_id = getattr(self, 'lineage_root_id', None)
        snapshot.lineage_tip_id = getattr(self, 'lineage_tip_id', None)
        snapshot.compression_anchor_visible_idx = getattr(self, 'compression_anchor_visible_idx', None)
        snapshot.compression_anchor_message_key = getattr(self, 'compression_anchor_message_key', None)
        # Runtime fields: empty sentinel (snapshot has no live state)
        snapshot.pending_user_message = None
        snapshot.pending_attachments = []
        snapshot.pending_started_at = None
        snapshot._process_loop_thread = None
        snapshot._process_loop_stop = threading.Event()
        snapshot._process_loop_last_active = 0
        snapshot._pending_input = queue.Queue()
        # Preserve extra kwargs (session_approved, llm_title_generated, etc.)
        snapshot.__dict__.update({
            k: v for k, v in self.__dict__.items()
            if k not in snapshot.__dict__
        })
        return snapshot

    @staticmethod
    def _normalize_model(model: str) -> str:
        """Normalize bare model names to @provider:model format.

        Bare names like "deepseek-v4-pro" cannot uniquely identify a model
        across providers. This method adds the @provider: prefix when the
        model belongs to a non-default provider group.
        """
        if not model:
            return model
        # Already prefixed or namespaced — nothing to do.
        if model.startswith("@") or "/" in model:
            return model
        try:
            from app.api.config import normalize_model_prefix
            return normalize_model_prefix(model)
        except Exception:
            return model

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

    def sync_webui_metadata(self, touch_updated_at: bool = True, skip_index: bool = False,
             sync_db: bool = True) -> None:
        """Persist session metadata to state.db.

        Only webui-specific columns (``__webui`` suffix) and
        ``original_content__webui`` are written.  Message content, role,
        tool_calls, tokens, and other SDK-managed fields are left untouched.
        """
        if touch_updated_at:
            self.updated_at = time.time()

        # ── Sync webui metadata to state.db ───────────────────────────────
        db = _get_session_db()
        if db is not None:
            try:
                self._sync_to_db(db)
            except Exception:
                logger.warning("state.db sync failed for %s", self.session_id,
                               exc_info=True)

        # ── Update sidebar index ────────────────────────────────────────
        if not skip_index:
            _write_session_index(updates=[self])

    # ── Internal sync helpers ─────────────────────────────────────────

    def _sync_to_db(self, db) -> None:
        """Sync webui-specific metadata to state.db.

        Pre-condition: the session row must already exist in state.db.
        For sessions created through chat turns, the SDK creates the row
        (source='cli' or source='webui' depending on the channel). For
        sessions constructed in-memory by webui (e.g. new_session), the
        caller must explicitly call db.create_session first. Callers that
        reach _sync_to_db knowing the row was created upstream (e.g.
        import_cli_session, which asserts the CLI SDK created it during
        the chat turn) do not need to create_session themselves.

        _sync_to_db deliberately does NOT call db.create_session itself:
        webui-owned metadata writes must only touch __webui columns;
        the SDK owns row creation. Calling _sync_to_db on a non-existent
        row would silently no-op the UPDATE (matches 0 rows) and leave
        the caller in an inconsistent state.

        Only updates webui-owned columns (``__webui`` suffix,
        ``original_content__webui``).  Non-webui fields (content, role,
        tool_calls, tokens, model, cwd, message_count,
        ``last_activity_at``, etc.) are managed by the hermes-agent SDK
        and are never touched here.

        Messages are NOT written by this method — normal turns are
        persisted by the SDK's ``append_message`` inside
        ``run_conversation``.  Operations that need to mutate the message
        list (/retry, /undo, /compress) call the SDK method
        ``replace_messages`` directly.

        The sole exception to the webui-no-message-writes rule is
        ``cancel_stream`` in ``api/streaming.py``, which may append a
        ``_partial`` assistant message (when the user cancelled
        mid-stream) and always appends a ``*Task cancelled.*``
        marker, both via ``_db.append_message``.  This maintains the
        user/assistant role alternation invariant so the SDK's
        ``repair_message_sequence`` does not merge consecutive users
        and break ``_persist_user_message_idx`` tracking, and lets
        the model see the partial content as prior context on the
        next turn.  Every other message-row write is owned by the SDK.
        """
        sid = self.session_id

        def _do_write(conn):
            # Ensure llm_title_generated__webui column exists (added post-migration)
            try:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN llm_title_generated__webui INTEGER DEFAULT 0"
                )
            except Exception:
                pass  # column already exists
            conn.execute(
                """UPDATE sessions SET
                   pinned__webui = ?,
                   archived__webui = ?,
                   project_id__webui = ?,
                   profile__webui = ?,
                   personality__webui = ?,
                   reasoning_effort__webui = ?,
                   service_tier__webui = ?,
                   skills__webui = ?,
                   mode__webui = ?,
                   compression_anchor_visible_idx__webui = ?,
                   compression_anchor_message_key__webui = ?,
                   last_seen_msg_count__webui = ?,
                   last_compression_time__webui = ?,
                   session_approved__webui = ?,
                   lineage_root_id__webui = ?,
                   lineage_tip_id__webui = ?,
                   compression_segment_count__webui = ?,
                   context_length__webui = ?,
                   last_prompt_tokens__webui = ?,
                   threshold_tokens__webui = ?,
                   auto_compress_disabled__webui = ?,
                   auto_compress_fail_count__webui = ?,
                   llm_title_generated__webui = ?
                   WHERE id = ?""",
                (
                    int(bool(self.pinned)),
                    int(bool(self.archived)),
                    self.project_id,
                    self.profile,
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
                    getattr(self, 'lineage_root_id', None),
                    getattr(self, 'lineage_tip_id', None),
                    getattr(self, 'compression_segment_count', 0) or 0,
                    getattr(self, 'context_length', 0) or 0,
                    getattr(self, 'last_prompt_tokens', 0) or 0,
                    getattr(self, 'threshold_tokens', 0) or 0,
                    int(bool(getattr(self, 'auto_compress_disabled', False))),
                    getattr(self, 'auto_compress_fail_count', 0) or 0,
                    int(bool(getattr(self, 'llm_title_generated', False))),
                    sid,
                ),
            )

        db._execute_write(_do_write)

        # NOTE: title column is owned by the hermes-agent SDK
        # (agent.title_generator + hermes_state.set_auto_title). WebUI must
        # not write the title column directly — CLAUDE.md forbids writing
        # non-__webui columns. The SDK's maybe_auto_title() fires on every
        # turn completion and writes a derived title, then upgrades it to an
        # LLM-generated title via background thread.

        # ── Backfill __webui columns ──────────────────────────────────
        # The SDK's append_message does not write webui-only columns.
        # Use rowid-positional matching (instead of content matching)
        # to reliably UPDATE the exact row that corresponds to each
        # in-memory message.
        try:
            rowids = [r[0] for r in db._conn.execute(
                "SELECT rowid FROM messages "
                "WHERE session_id = ? AND active = 1 ORDER BY id",
                (sid,),
            ).fetchall()]
        except Exception:
            rowids = []
        for i, _m in enumerate(self.messages):
            if i >= len(rowids):
                break
            _rowid = rowids[i]

            # original_content__webui
            _oc = _m.get("original_content__webui") if isinstance(_m, dict) else None
            if _oc and _m.get("role") == "user":
                _upsert_webui_col(db, _rowid, sid, "original_content__webui", _oc)

            # timestamp__webui
            _ts = _m.get("timestamp__webui") if isinstance(_m, dict) else None
            if _ts is not None:
                _upsert_webui_col(db, _rowid, sid, "timestamp__webui", _ts)

    # ── DB column → Session field mapping ──────────────────────────────
    # Columns that map directly (same name in both)
    _DB_DIRECT_FIELDS = (
        'title', 'model', 'pinned__webui', 'archived__webui', 'project_id__webui', 'profile__webui',
        'input_tokens', 'output_tokens', 'personality__webui', 'reasoning_effort__webui',
        'service_tier__webui', 'skills__webui', 'mode__webui', 'parent_session_id',
        'compression_segment_count__webui', 'compression_anchor_visible_idx__webui',
        'compression_anchor_message_key__webui', 'last_seen_msg_count__webui',
        'last_compression_time__webui', 'lineage_root_id__webui', 'lineage_tip_id__webui',
        'context_length__webui', 'last_prompt_tokens__webui', 'threshold_tokens__webui',
        'auto_compress_disabled__webui', 'auto_compress_fail_count__webui',
        'llm_title_generated__webui',
    )
    # Columns that need name mapping or type conversion
    _DB_MAPPED_FIELDS = {
        'id': 'session_id',
        'cwd': 'workspace',
        'started_at': 'created_at',
        'last_activity_at': 'updated_at',
        'estimated_cost_usd': 'estimated_cost',
    }

    @classmethod
    def _from_db_row(cls, row: dict, messages: list = None):
        """Build a Session from a state.db sessions table row + message list.

        ``row`` is a dict from ``SessionDB.get_session()``.
        ``messages`` is a list of message dicts from ``SessionDB.get_messages()``.
        """
        kwargs = {}

        # Helper to strip __webui suffix from DB column names → Python attr names
        def _attr_name(db_col: str) -> str:
            return db_col.replace("__webui", "")

        # Direct-mapped fields
        for field in cls._DB_DIRECT_FIELDS:
            if field in row and row[field] is not None:
                kwargs[_attr_name(field)] = row[field]

        # Name-mapped fields
        for db_col, session_attr in cls._DB_MAPPED_FIELDS.items():
            if db_col in row and row[db_col] is not None:
                kwargs[session_attr] = row[db_col]
            elif db_col in row and row[db_col] is None and session_attr == 'workspace':
                # cwd can be NULL in state.db — pass empty string so Session
                # does not fall back to DEFAULT_WORKSPACE
                kwargs[session_attr] = ''
            elif db_col == 'last_activity_at' and row.get('last_activity_at') is None:
                # last_activity_at is NULL for sessions that have never had a
                # message appended through the SDK (e.g. freshly imported CLI
                # sessions before the SDK bumps it on the next append). Falling
                # back to time.time() in Session.__init__ (the default for a
                # missing updated_at kwarg) would make a Session.load() reload
                # of a freshly-imported gateway session sort above newer webui
                # sessions in the sidebar. Mirror _all_sessions_from_db's
                # `last_activity_at or started_at` projection so the in-memory
                # view matches what the DB read returns.
                _started = row.get('started_at')
                if _started is not None:
                    kwargs['updated_at'] = _started

        # Deserialize JSON columns
        for col in ('skills__webui', 'session_approved__webui', 'compression_anchor_message_key__webui'):
            val = row.get(col)
            if isinstance(val, str) and val:
                try:
                    kwargs[_attr_name(col)] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    kwargs[_attr_name(col)] = None
            elif val is not None:
                kwargs[_attr_name(col)] = val

        # Boolean columns stored as INTEGER
        for col in ('pinned__webui', 'archived__webui', 'auto_compress_disabled__webui', 'llm_title_generated__webui'):
            _attr = col.replace("__webui", "")
            if _attr in kwargs:
                kwargs[_attr] = bool(kwargs[_attr])

        # Carry through any extra DB columns as kwargs so they survive
        # round-trips (e.g. llm_title_generated, session_approved already handled above)
        known = set(cls._DB_DIRECT_FIELDS) | set(cls._DB_MAPPED_FIELDS.keys())
        # Also block bare columns that have a __webui counterpart — the
        # __webui value was already mapped above and the bare value (always
        # 0 from the SDK schema) must not overwrite it.
        for field in cls._DB_DIRECT_FIELDS:
            if field.endswith('__webui'):
                known.add(field.replace('__webui', ''))
        for k, v in row.items():
            if k not in known and v is not None and not k.startswith('_'):
                kwargs[k] = v

        kwargs['messages'] = messages or []
        # Gateway/CLI sessions are read-only (except subagent sessions).
        # Use parent_session_id (not source) to detect subagent sessions —
        # webui-created subagents inherit HERMES_SESSION_SOURCE=webui from
        # the parent process, so their DB source is 'webui', not 'subagent'.
        source = row.get('source')
        _parent = row.get('parent_session_id')
        if _parent:
            kwargs['_readonly'] = False
            kwargs['is_subagent_session'] = True
            kwargs['source_tag'] = source or 'subagent'
        elif source and source != 'webui':
            kwargs['_readonly'] = True
            kwargs['is_cli_session'] = True
            kwargs['source_tag'] = source
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
        # ── Display-layer reasoning dedup ──────────────────────────
        # The SDK may echo identical reasoning onto multiple assistant messages
        # within a turn (DeepSeek provider echo).  Only strip the reasoning from
        # a content-empty assistant when the NEXT assistant carries the SAME
        # reasoning string — otherwise the reasoning is legitimate (e.g. the
        # thinking that led to tool calls before any text was emitted) and must
        # be preserved.  Mirrors the frontend dedup in ChatMessages.tsx.
        for i, _m in enumerate(messages):
            if not (isinstance(_m, dict) and _m.get('role') == 'assistant' and _m.get('reasoning')):
                continue
            _ct = _m.get('content', '')
            if _ct and not (isinstance(_ct, str) and not _ct.strip()):
                continue  # has visible text — keep reasoning
            # Content empty: strip only if the next assistant echoes the same reasoning.
            for _n in messages[i + 1:]:
                if isinstance(_n, dict) and _n.get('role') == 'assistant':
                    if _n.get('reasoning') == _m.get('reasoning'):
                        _m['reasoning'] = ''
                    break
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

    def compact(self, include_runtime=False, active_session_ids=None, active_stream_id=None) -> dict:
        active_session_ids = active_session_ids if active_session_ids is not None else set()
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
        _clamped_last_seen = min(int(self.last_seen_msg_count or 0), _msg_count)
        return {
            'session_id': self.session_id,
            'title': self.title,
            'workspace': self.workspace,
            'model': self.model,
            'message_count': _msg_count,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'last_activity_at': self.updated_at,
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
            'active_stream_id': active_stream_id,
            'is_streaming': _is_streaming_session(
                self.session_id, active_session_ids
            ) if include_runtime else False,
            # Gateway/CLI session markers (set by _from_db_row for non-webui sessions)
            '_readonly': getattr(self, '_readonly', None),
            'is_cli_session': getattr(self, 'is_cli_session', None),
            'is_subagent_session': getattr(self, 'is_subagent_session', None) or getattr(self, 'parent_session_id', None) is not None,
            'source_tag': getattr(self, 'source_tag', None),
            'auto_compress_disabled': getattr(self, 'auto_compress_disabled', False),
            '_has_process_loop': getattr(self, '_process_loop_thread', None) is not None and self._process_loop_thread.is_alive(),
            # Delegate children info (computed by _update_parent_delegate_count)
            '_has_delegate_children': getattr(self, '_has_delegate_children', False),
            '_delegate_children_count': getattr(self, '_delegate_children_count', 0),
            '_delegate_running_count': getattr(self, '_delegate_running_count', 0),
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
        # Compute delegate children info before notifying so the frontend
        # gets accurate _has_delegate_children / _delegate_running_count.
        _compute_delegate_info(s)
        _notify_session_event('upsert_session', s)
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
    last_seen = int(s.last_seen_msg_count or 0)
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
            s.sync_webui_metadata(touch_updated_at=False)
        except Exception:
            logger.debug(
                "Failed to persist stale last_seen fix for session %s",
                s.session_id,
            )


def new_session(workspace=None, model=None, profile=None, defer_notify=False):
    """Create a new session with sensible defaults and persist to state.db.

    Defaults are resolved as follows (caller-supplied values take precedence):
      - workspace: the built-in Default workspace (issue #7) — there is no
        global "last workspace"; workspace ownership lives on each session
        (state.db cwd), and welcome-state/"+" sessions default to Default
      - model: first available model from the provider list
      - reasoning_effort: "high"
      - personality: None (global default)
      - skills: None (all skills available)
      - session_approved: None

    The session is persisted to state.db immediately so that the composer
    footer reflects the resolved defaults, even before the first message.

    *profile* — when supplied by the caller (e.g. from the request body sent
    by the active browser tab), it is used directly so that concurrent clients
    on different profiles don't fight over a shared process-global.  If not
    supplied, we fall back to the process-level active profile.

    *defer_notify* — when True, skip the upsert_session notify.  Use this
    when the caller is about to emit a closely-related notify (e.g.
    begin_stream) so we don't pay for two notify IPCs in a row.
    """
    if profile is None:
        try:
            from app.api.profiles import get_active_profile_name
            profile = get_active_profile_name()
        except ImportError:
            profile = None
    effective_model = model or get_first_available_model()
    # Normalize bare model names to @provider:model so downstream paths
    # (drain, replay) can resolve the provider without ambiguity.
    effective_model = Session._normalize_model(effective_model)
    # Inherit default_mode from global settings for new sessions
    try:
        from app.api.config import load_settings
        default_mode = load_settings().get("default_mode", "manual")
    except Exception:
        default_mode = "manual"
    s = Session(
        workspace=workspace or str(DEFAULT_WORKSPACE),
        model=effective_model,
        profile=profile,
        mode=default_mode,
        reasoning_effort="high",
    )
    with LOCK:
        SESSIONS[s.session_id] = s
        SESSIONS.move_to_end(s.session_id)
        while len(SESSIONS) > SESSIONS_MAX:
            SESSIONS.popitem(last=False)
    if not defer_notify:
        _notify_session_event('upsert_session', s)

    # Ensure row exists in state.db before sync_webui_metadata can write
    # __webui columns. _sync_to_db deliberately does not auto-create rows
    # (webui-owned metadata writes must only touch __webui columns; the
    # SDK owns row creation).
    try:
        db = _get_session_db()
        if db is not None:
            db.create_session(s.session_id, 'webui')
    except Exception:
        logger.warning("Failed to create session row %s in state.db",
                       s.session_id, exc_info=True)

    # Persist defaults to state.db immediately so the composer footer
    # reflects the selected workspace / model / reasoning / etc.
    try:
        s.sync_webui_metadata()
    except Exception:
        logger.warning("Failed to persist new session %s to state.db",
                       s.session_id, exc_info=True)

    # ── Persist cwd to state.db ──────────────────────────────────────
    # Background:
    #   The cwd column in state.db stores the session's workspace path,
    #   used by the sidebar for grouping and display.  For CLI/gateway
    #   sessions, cwd is written by the SDK's update_session_cwd() when
    #   the terminal directory changes (tui_gateway/server.py).
    #
    # Problem:
    #   WebUI sessions never go through the gateway.  sync_webui_metadata()
    #   only writes __webui columns and never touches cwd.  As a result,
    #   cwd stays NULL for all WebUI sessions, causing the sidebar to
    #   display "未分配工作区" (unassigned workspace).
    #
    # Fix:
    #   Call the SDK's SessionDB.update_session_cwd() after
    #   sync_webui_metadata() to persist the workspace to the cwd column.
    #   This is the SDK's high-level API — not a direct DB write.
    if s.workspace:
        # B.4 — cwd write runs on a daemon thread so we don't block the
        # caller on a separate IPC/SQL write.  cwd is only used by the
        # sidebar for workspace grouping, so a brief async window (a few
        # ms) is invisible to the user.  Capture locals by alias to avoid
        # late-binding the loop variable.
        _sid_for_cwd = s.session_id
        _workspace_for_cwd = s.workspace

        def _defer_cwd_write():
            try:
                db = _get_session_db()
                if db is not None:
                    db.update_session_cwd(_sid_for_cwd, _workspace_for_cwd)
            except Exception:
                logger.debug("Failed to persist cwd for new session %s",
                             _sid_for_cwd, exc_info=True)

        threading.Thread(target=_defer_cwd_write, daemon=True).start()

    return s

def _hide_from_default_sidebar(session: dict) -> bool:
    """Return True for internal/background sessions hidden from the default list."""
    sid = str(session.get('session_id') or '')
    source = session.get('source_tag') or session.get('source')
    return source == 'cron' or sid.startswith('cron_')


def _hide_empty_untitled(title, message_count, input_tokens, created_at, updated_at) -> bool:
    """Sidebar visibility predicate for #1171.

    Hide Untitled + 0-message sessions that have never had any activity
    (no input_tokens, created_at == updated_at). Applied in both the
    state.db path and the JSON-fallback path so the sidebar stays consistent
    regardless of which storage backend answered the read.

    `title` is matched via prefix because ``set_session_title`` auto-suffixes
    ``Untitled #2``, ``Untitled #3`` etc. on collisions (#1171) — those
    variants are also "untouched empty scratch-pad sessions" from the
    user's perspective and must be hidden the same way.
    """
    title_str = title or ''
    return (
        (title_str == 'Untitled' or title_str.startswith('Untitled #'))
        and (message_count or 0) == 0
        and (input_tokens or 0) == 0
        and abs((updated_at or 0) - (created_at or 0)) < 5
    )


def _append_archived_sessions(result):
    """Append archived (read-only compaction snapshots) for sessions whose
    ``archived`` flag is set, avoiding filesystem scans for all other sessions.
    """
    try:
        for s in result:
            if not s.get('archived'):
                continue
            sid = s.get('session_id', '')
            archive_dir = SESSION_DIR / f'{sid}_archived'
            if not archive_dir.is_dir():
                continue
            for p in sorted(archive_dir.glob('compaction_*.json')):
                try:
                    data = json.loads(p.read_text(encoding='utf-8'))
                    result.append({
                        'session_id': f'{sid}/{p.stem}',
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
                        '_parent_session_id': sid,
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
    active_session_ids = _active_session_ids()

    # ── Primary: state.db query ────────────────────────────────────────
    db = _get_session_db()
    if db is not None:
        try:
            result = _all_sessions_from_db(db, active_session_ids)
            if result is not None:
                return result
        except Exception:
            logger.debug("state.db session list failed, falling back to JSON index")

    # ── Fallback: JSON index or directory scan ─────────────────────────
    return _all_sessions_from_json(active_session_ids)


def _all_sessions_from_db(db, active_session_ids):
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
            'workspace': str(Path(row['cwd']).expanduser().resolve()) if row.get('cwd') else '',
            'model': row.get('model', ''),
            'message_count': row.get('message_count', 0),
            'created_at': row.get('started_at', 0),
            'updated_at': row.get('last_activity_at') or row.get('started_at', 0),
            'last_activity_at': row.get('last_activity_at') or row.get('started_at', 0),
            'pinned': bool(row.get('pinned__webui', False)),
            'archived': bool(row.get('archived__webui', False)),
            'project_id': row.get('project_id'),
            'profile': row.get('profile') or 'default',
            'input_tokens': row.get('input_tokens', 0),
            'output_tokens': row.get('output_tokens', 0),
            'estimated_cost': row.get('estimated_cost_usd'),
            # WebUI columns use __webui suffix in state.db after migration
            'personality': row.get('personality__webui'),
            'reasoning_effort': row.get('reasoning_effort__webui'),
            'service_tier': row.get('service_tier__webui'),
            'skills': _deserialize_json_field(row.get('skills__webui')),
            'last_seen_msg_count': row.get('last_seen_msg_count__webui'),
            'compression_anchor_visible_idx': row.get('compression_anchor_visible_idx__webui'),
            'compression_anchor_message_key': row.get('compression_anchor_message_key__webui'),
            'mode': row.get('mode__webui', 'manual'),
            '_parent_session_id': row.get('parent_session_id'),
            # Mirror Session.compact() (line 924): subagent = has parent_session_id.
            # The DB `source` value is 'webui' for webui-spawned subagents (the SDK
            # inherits HERMES_SESSION_SOURCE from the parent process), so we MUST
            # derive from parent_session_id, not source_tag.
            'is_subagent_session': row.get('parent_session_id') is not None,
            '_compression_segment_count': row.get('compression_segment_count__webui', 0) or 0,
            '_lineage_root_id': row.get('lineage_root_id__webui'),
            '_lineage_tip_id': row.get('lineage_tip_id__webui'),
            'context_length': row.get('context_length__webui', 0) or 0,
            'last_prompt_tokens': row.get('last_prompt_tokens__webui', 0) or 0,
            'threshold_tokens': row.get('threshold_tokens__webui', 0) or 0,
            'last_compression_time': row.get('last_compression_time__webui', 0) or 0,
            'is_streaming': False,
        }
        compact_list.append(compact)

    # Overlay in-memory sessions (may have runtime state not yet flushed to DB).
    # The state.db title is authoritative (it goes through set_session_title
    # with a UNIQUE constraint), so preserve it over the in-memory value.
    # Subagent sessions: overlay active_stream_id so loadSessions() returns
    # a complete baseline for stream state reconciliation on reconnect.
    with LOCK:
        for s in SESSIONS.values():
            _sid = s.session_id
            _stream_id = stream_manager.get_stream_id(_sid)
            if getattr(s, 'is_subagent_session', False):
                # Subagent: only overlay active_stream_id, don't expand to full compact
                for c in compact_list:
                    if c['session_id'] == _sid:
                        c['active_stream_id'] = _stream_id
                        break
                continue
            found = False
            for c in compact_list:
                if c['session_id'] == _sid:
                    db_title = c.get('title', '')
                    c.update(s.compact(include_runtime=True,
                                       active_session_ids=active_session_ids,
                                       active_stream_id=_stream_id))
                    if db_title:
                        c['title'] = db_title
                    found = True
                    break
            if not found:
                compact_list.append(s.compact(include_runtime=True,
                                               active_session_ids=active_session_ids,
                                               active_stream_id=_stream_id))

    # Hide empty Untitled sessions (#1171) — applied AFTER the overlay so it
    # covers both DB rows and SESSIONS-only entries (e.g. a brand-new
    # session that's never been persisted).
    result = [s for s in compact_list if not _hide_empty_untitled(
        s.get('title', 'Untitled'),
        s.get('message_count', 0),
        s.get('input_tokens', 0),
        s.get('created_at', 0),
        s.get('updated_at', 0),
    )]
    result = [s for s in result if not _hide_from_default_sidebar(s)]
    # Subagent sessions live only inside the parent's delegate tree — never in
    # the primary workspace list. session_visible() (line 1611) already excludes
    # them in get_cli_sessions(); mirror that here so an older client/server
    # pair that doesn't read `is_subagent_session` from the wire cannot leak
    # them. Strict AND: drop only when BOTH the flag and the parent are present.
    result = [s for s in result
              if not (s.get('is_subagent_session') is True
                      and s.get('_parent_session_id'))]
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


def _all_sessions_from_index(active_session_ids):
    """Build session list from _index.json (legacy fast path)."""
    index = json.loads(SESSION_INDEX_FILE.read_text(encoding='utf-8'))
    index = [
        s for s in index
        if _index_entry_exists(s.get('session_id'))
    ]
    backfilled = []
    for i, s in enumerate(index):
        if 'last_activity_at' not in s:
            full = Session.load(s.get('session_id'))
            if full:
                index[i] = full.compact()
                backfilled.append(full)
    if backfilled:
        try:
            _write_session_index(updates=backfilled)
        except Exception:
            logger.debug("Failed to persist last_activity_at backfill")
    for s in index:
        _sid = s.get('session_id')
        _stream_id = stream_manager.get_stream_id(_sid) if _sid else None
        s['active_stream_id'] = _stream_id
        s['is_streaming'] = _is_streaming_session(
            _sid,
            active_session_ids,
        )
    # Overlay any in-memory sessions that may be newer than the index
    index_map = {s['session_id']: s for s in index}
    with LOCK:
        for s in SESSIONS.values():
            _sid = s.session_id
            _stream_id = stream_manager.get_stream_id(_sid)
            index_map[_sid] = s.compact(
                include_runtime=True,
                active_session_ids=active_session_ids,
                active_stream_id=_stream_id,
            )
    result = sorted(index_map.values(), key=lambda s: (s.get('pinned', False), _session_sort_timestamp(s)), reverse=True)
    result = [s for s in result if not _hide_empty_untitled(
        s.get('title', 'Untitled'),
        s.get('message_count', 0),
        s.get('input_tokens', 0),
        s.get('created_at', 0),
        s.get('updated_at', 0),
    )]
    result = [s for s in result if not _hide_from_default_sidebar(s)]
    # Subagent exclusion — see _all_sessions_from_db() for the rationale.
    # Strict AND: drop only when BOTH the flag and the parent are present.
    result = [s for s in result
              if not (s.get('is_subagent_session') is True
                      and s.get('_parent_session_id'))]
    for s in result:
        if not s.get('profile'):
            s['profile'] = 'default'
    result = _append_archived_sessions(result)
    return result


def _all_sessions_from_json(active_session_ids):
    """Legacy fallback: build session list from JSON index or directory scan."""
    # Phase C: try index first for O(1) read; fall back to full scan
    if SESSION_INDEX_FILE.exists():
        try:
            return _all_sessions_from_index(active_session_ids)
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
    # DB-path filter above. No grace window: a 0-message Untitled session is
    # never shown regardless of age (#1171).
    result = [s.compact(include_runtime=True, active_session_ids=active_session_ids,
                        active_stream_id=stream_manager.get_stream_id(s.session_id))
              for s in out if not _hide_empty_untitled(
        s.title, len(s.messages), s.input_tokens, s.created_at, s.updated_at,
    )]
    result = [s for s in result if not _hide_from_default_sidebar(s)]
    # Subagent exclusion — see _all_sessions_from_db() for the rationale.
    # Strict AND: drop only when BOTH the flag and the parent are present.
    result = [s for s in result
              if not (s.get('is_subagent_session') is True
                      and s.get('_parent_session_id'))]
    for s in result:
        if not s.get('profile'):
            s['profile'] = 'default'
    result = _append_archived_sessions(result)
    return result


def _get_all_delegate_children_map(db) -> dict[str, list[dict]]:
    """批量查询所有委托子 agent session，返回 parent_id -> children 映射。

    一次查询获取所有子 session 关系，避免 N+1 查询问题。
    只返回带有 _delegate_from 标记的子 session，排除压缩延续。

    返回的 message_count 只统计 user + assistant 消息（不含 tool/system），
    与前端 msg-row 的渲染逻辑保持一致。
    """
    try:
        rows = db._conn.execute(
            """
            SELECT s.id, s.title, s.model, s.started_at, s.ended_at,
                   (SELECT COUNT(*) FROM messages m
                    WHERE m.session_id = s.id
                    AND m.role IN ('user', 'assistant')) AS message_count,
                   s.parent_session_id
            FROM sessions s
            WHERE s.parent_session_id IS NOT NULL
            AND json_extract(COALESCE(s.model_config, '{}'), '$._delegate_from') IS NOT NULL
            ORDER BY s.started_at DESC
            """
        ).fetchall()

        children_map: dict[str, list[dict]] = {}
        for row in rows:
            row_dict = dict(row)
            parent_id = row_dict.pop('parent_session_id')
            if parent_id not in children_map:
                children_map[parent_id] = []
            children_map[parent_id].append(row_dict)

        return children_map
    except Exception:
        return {}


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
    workspace=None,
):
    """Create a new WebUI session populated with CLI messages.

    *workspace* is the CLI session's cwd from state.db (via
    get_cli_sessions()); when absent, fall back to the built-in Default
    workspace — there is no global "last workspace" state.

    Returns the Session object.
    """
    from app.api.config import normalize_model_prefix

    # Pre-condition: the session row must already exist in state.db.
    # CLI SDK and webui share state.db (via get_active_hermes_home());
    # the CLI chat turn creates the row with source='cli' before
    # _handle_session_import_cli reaches here (see Session.load check
    # in api/routes.py:_handle_session_import_cli). _sync_to_db no
    # longer auto-creates rows (webui-owned metadata writes must only
    # touch __webui columns; the SDK owns row creation). If we got
    # here with no row, that's an invariant violation — fail loud so
    # the upstream bug is visible, don't silently mask it with a
    # fallback create_session.
    db = _get_session_db()
    if db is not None and db.get_session(session_id) is None:
        raise RuntimeError(
            f"Cannot import CLI session {session_id}: row missing from "
            f"state.db. CLI SDK should have created it during the chat turn."
        )

    s = Session(
        session_id=session_id,
        title=title,
        workspace=workspace or str(DEFAULT_WORKSPACE),
        model=normalize_model_prefix(model),
        messages=messages,
        profile=profile,
        created_at=created_at,
        updated_at=updated_at,
    )
    s.sync_webui_metadata(touch_updated_at=False)
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
    # 2. config.yaml terminal.cwd (canonical source for the gateway) — use cached config
    try:
        from app.api.config import get_config
        _cfg = get_config()
        _terminal_cfg = _cfg.get('terminal', {})
        if isinstance(_terminal_cfg, dict):
            _cfg_cwd = str(_terminal_cfg.get('cwd', '')).strip()
            if _cfg_cwd and _cfg_cwd not in {'.', 'auto', 'cwd'}:
                return str(Path(_cfg_cwd).expanduser().resolve())
    except Exception:
        pass
    # 3. Fall back to home directory
    return str(Path.home())


MESSAGING_SOURCE_TAGS = frozenset({
    'weixin', 'telegram', 'whatsapp', 'signal', 'line', 'discord', 'slack',
})


def session_source_category(source, parent_session_id=None) -> str:
    """Classify a state.db session `source` into a visibility category.

    - 'webui'     → WebUI-native sessions (always shown)
    - 'cli'       → hermes CLI sessions
    - 'cron'      → scheduled cron-job sessions
    - 'messaging' → IM/gateway platforms (weixin, telegram, discord, ...)
    - 'subagent'  → delegate subagent sessions (never shown in main list,
                     only as delegate children under their parent)

    Subagent detection uses ``parent_session_id`` (not ``source``) because
    webui-created subagents inherit HERMES_SESSION_SOURCE=webui and their
    DB source is 'webui', not 'subagent'.
    """
    if parent_session_id:
        return 'subagent'
    s = source or 'cli'  # NULL/empty → treat as CLI (matches legacy fallback)
    if s == 'webui':
        return 'webui'
    if s == 'cron':
        return 'cron'
    if s == 'cli':
        return 'cli'
    return 'messaging'


def session_visible(source, show_cli: bool, show_messaging: bool, show_cron: bool,
                    parent_session_id=None) -> bool:
    """Return True if a session with `source` should appear given the toggles."""
    category = session_source_category(source, parent_session_id)
    if category == 'webui':
        return True
    if category == 'cli':
        return bool(show_cli)
    if category == 'cron':
        return bool(show_cron)
    if category == 'subagent':
        return False  # Subagent sessions only appear as delegate children
    return bool(show_messaging)


def get_cli_sessions(show_cli: bool = True, show_messaging: bool = True,
                     show_cron: bool = False) -> list:
    """Read agent sessions from the SQLite store and return them as dicts in a
    format the WebUI sidebar can render alongside local sessions.

    Each of the three source categories (cli / messaging / cron) is included
    only when its corresponding toggle is on.  Returns empty list if the SQLite
    DB is missing or any error occurs -- the bridge is purely additive and
    never crashes the WebUI.
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
        from app.api.profiles import get_active_hermes_home
        hermes_home = Path(get_active_hermes_home()).expanduser().resolve()
    except Exception:
        hermes_home = Path(os.getenv('HERMES_HOME', str(HOME / '.hermes'))).expanduser().resolve()

    db_path = hermes_home / 'state.db'
    if not db_path.exists():
        return cli_sessions

    # Try to resolve the active CLI profile so imported sessions integrate
    # with the WebUI profile filter (available since Sprint 22).
    try:
        from app.api.profiles import get_active_profile_name
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
            _parent_sid = row.get('parent_session_id')
            if not session_visible(_source, show_cli, show_messaging, show_cron,
                                   parent_session_id=_parent_sid):
                continue
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
            _msg_count = row['message_count'] or row['actual_message_count'] or 0
            # Read-time clamp: last_seen_msg_count must never exceed message_count.
            # When last_seen > message_count, it means history was cleared or compressed.
            # Clamp to message_count (= "seen all current messages").
            _last_seen = row.get('last_seen_msg_count__webui')
            # CLI sessions without a stored seen-position have never been
            # opened in WebUI — treat as "fully seen" so they don't light
            # the unread dot permanently.
            _clamped_last_seen = min(_last_seen or 0, _msg_count) if _last_seen is not None else _msg_count
            cli_sessions.append({
                'session_id': sid,
                'title': _display_title,
                'workspace': _workspace,
                'model': row['model'] or None,
                'message_count': _msg_count,
                'last_seen_msg_count': _clamped_last_seen,
                'created_at': row['started_at'],
                'updated_at': raw_ts,
                'pinned': False,
                'archived': False,
                'project_id': None,
                'profile': profile,
                'source_tag': _source,
                'is_cli_session': not _parent_sid,
                'is_subagent_session': bool(_parent_sid),
                '_readonly': not _parent_sid,
                'user_id': _user_id,
                'active_stream_id': stream_manager.get_stream_id(sid),
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
        from app.api.profiles import get_active_hermes_home
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
        from app.api.profiles import get_active_hermes_home
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


# ── Notify stream helper ────────────────────────────────────────────────

def _notify_session_event(event_type: str, session, stream_id: str = None):
    """Push session event to the global notify stream.

    Takes a clone() snapshot to ensure data consistency even if the
    original session is mutated concurrently.
    event_type: 'upsert_session' | 'delete_session' | 'begin_stream'
    """
    from app.api.notify_stream import notify_stream
    from app.api.stream_manager import stream_manager
    snap = session.clone() if hasattr(session, 'clone') else session
    # Query whether this session currently has an active stream
    active_sid = stream_manager.get_stream_id(snap.session_id)
    data = {
        'session_id': snap.session_id,
        'parent_session_id': snap.parent_session_id,
        'stream_id': stream_id,
        'active_stream_id': active_sid,
        'source_tag': getattr(snap, 'source_tag', None),
        'title': snap.title,
        'workspace': snap.workspace,
        'message_count': len(snap.messages or []),
        'last_seen_msg_count': snap.last_seen_msg_count,
        'ts': time.time(),
        # Delegate children info — always included for consistent updates.
        # Defaults to False/0 when not explicitly set (e.g. from DB load).
        '_has_delegate_children': getattr(snap, '_has_delegate_children', False),
        '_delegate_children_count': getattr(snap, '_delegate_children_count', 0),
        '_delegate_running_count': getattr(snap, '_delegate_running_count', 0),
    }
    notify_stream.put(event_type, data)


def _update_parent_delegate_count(parent_session_id: str):
    """Update parent session's delegate children count and notify frontend."""
    with LOCK:
        parent = SESSIONS.get(parent_session_id)
    if not parent:
        return
    _compute_delegate_info(parent)
    _notify_session_event('upsert_session', parent)


def _compute_compression_lineage(
    session_id: str,
    *,
    parent_session_id: str | None,
    end_reason: str | None,
) -> tuple[int, str, str]:
    """Walk the parent_session_id chain in the session index, returning
    (count, root, tip) for the lineage containing session_id.

    - count: total sessions in the same lineage (sharing the same root).
    - root:  the lineage's root session_id (the one with no parent).
    - tip:   the session_id passed in (the most recent segment).

    parent_session_id and end_reason are accepted for API stability and
    future use (e.g., cross-check the caller's claimed parent, or filter
    by end_reason once persisted). They are intentionally not used by
    the current implementation, which derives everything from the index.

    The index path is resolved at call time via the module-level
    SESSION_DIR (which tests reassign to a tmpdir) — NOT the import-time
    SESSION_INDEX_FILE constant — so tests can swap the path without
    polluting the production index. Without this, SESSION_INDEX_FILE is
    bound at import time and would keep pointing at the production
    path even after a test reassigns SESSION_DIR.
    """
    # Resolve the index path at call time (not import time) so tests
    # that swap api.models.SESSION_DIR take effect.
    _index_path = SESSION_DIR / "_index.json"
    try:
        with open(_index_path) as _f:
            _index = json.load(_f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Missing / corrupt / mid-rebuild index → treat as a single-
        # session lineage (no chain visible from current state).
        return (1, session_id, session_id)

    parents: dict[str, str | None] = {
        entry['session_id']: entry.get('_parent_session_id')
        for entry in _index
        if isinstance(entry, dict) and 'session_id' in entry
    }

    # Walk back from session_id to find the lineage root (a session with
    # no parent_session_id). Memoized to keep total work O(n).
    _root_cache: dict[str, str | None] = {}

    def resolve_root(sid: str) -> str | None:
        if sid in _root_cache:
            return _root_cache[sid]
        seen: set[str] = set()
        cur = sid
        result: str | None = None
        while cur and cur not in seen:
            seen.add(cur)
            if cur not in parents:
                result = None
                break
            parent = parents[cur]
            if not parent:
                result = cur
                break
            cur = parent
        _root_cache[sid] = result
        return result

    root = resolve_root(session_id)
    if root is None:
        # session_id not in index → treat as a single-session lineage.
        return (1, session_id, session_id)

    count = sum(1 for sid in parents if resolve_root(sid) == root)
    return (count, root, session_id)


def _compute_delegate_info(session):
    """Compute delegate children info using cache-first strategy.

    Data source priority:
    1. SESSIONS cache → children list (real-time, has parent_session_id)
    2. DB → ended_at (accurate, persisted)
    3. StreamManager → active streams (real-time)
    """
    try:
        from app.api.stream_manager import stream_manager

        # 1. Get children from SESSIONS cache (real-time data)
        with LOCK:
            cached_children = [
                s for s in SESSIONS.values()
                if getattr(s, 'parent_session_id', None) == session.session_id
                and getattr(s, 'is_subagent_session', False)
            ]

        # 2. Get ended_at mapping from DB (persisted data)
        db_ended_map = {}
        try:
            db = _get_session_db()
            if db:
                rows = db._conn.execute(
                    "SELECT id, ended_at FROM sessions WHERE parent_session_id = ?",
                    (session.session_id,)
                ).fetchall()
                db_ended_map = {r['id']: r['ended_at'] for r in rows}
        except Exception:
            pass

        # 3. Compute running count
        active_ids = set(stream_manager.get_active_session_ids())
        running_count = 0
        for child in cached_children:
            # Prefer DB ended_at, fallback to cache attribute
            ended_at = db_ended_map.get(child.session_id) or getattr(child, 'ended_at', None)
            if not ended_at and child.session_id in active_ids:
                running_count += 1

        # 4. Merge children count (cache + DB, deduplicated)
        seen_ids = {c.session_id for c in cached_children}
        all_children_count = len(cached_children)
        for sid in db_ended_map:
            if sid not in seen_ids:
                all_children_count += 1

        logger.info(
            "[delegate_info] session=%s cached=%d db=%d active=%d running=%d",
            session.session_id[:12], len(cached_children), len(db_ended_map),
            len(active_ids), running_count
        )
        session._delegate_children_count = all_children_count
        session._has_delegate_children = all_children_count > 0
        session._delegate_running_count = running_count
    except Exception:
        pass  # non-fatal


# ── Stream end cache invalidation ─────────────────────────────────────────
# Register a callback so that when a stream ends (end_stream), the SESSIONS
# cache entry for that session is reloaded from state.db via copy_from(),
# and an upsert_session notification is pushed to all connected frontends.

def _setup_stream_cache_invalidation():
    from app.api.stream_manager import stream_manager
    def _on_stream_end(session_id: str):
        with LOCK:
            cached = SESSIONS.get(session_id)
        if cached:
            try:
                fresh = Session.load(session_id)
                if fresh:
                    with LOCK:
                        cached.copy_from(fresh)
                        SESSIONS.move_to_end(session_id)
                    _notify_session_event('upsert_session', cached)
            except (KeyError, Exception):
                pass
    stream_manager.register_on_end(_on_stream_end)

_setup_stream_cache_invalidation()
