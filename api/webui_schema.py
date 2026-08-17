"""Column migration for WebUI-added columns in the shared state.db.

The sessions table in ~/.hermes/state.db is shared between the hermes-agent and
the WebUI.  Columns added by the WebUI are suffixed with ``__webui`` so they are
immediately recognisable as WebUI-managed and never conflict with future agent
schema additions.
"""

# ── Map of (old column name → new column name) ───────────────────────────────

WEBUI_COLUMN_RENAMES: dict[str, str] = {
    # Session metadata
    "personality":                      "personality__webui",
    "reasoning_effort":                 "reasoning_effort__webui",
    "service_tier":                     "service_tier__webui",
    "skills":                           "skills__webui",
    "mode":                             "mode__webui",
    "session_approved":                 "session_approved__webui",
    # Project / profile
    "project_id":                       "project_id__webui",
    "profile":                          "profile__webui",
    # Read-position tracking
    "last_seen_msg_count":              "last_seen_msg_count__webui",
    # Compression / lineage
    "compression_anchor_visible_idx":   "compression_anchor_visible_idx__webui",
    "compression_anchor_message_key":   "compression_anchor_message_key__webui",
    "last_compression_time":            "last_compression_time__webui",
    "lineage_root_id":                  "lineage_root_id__webui",
    "lineage_tip_id":                   "lineage_tip_id__webui",
    "compression_segment_count":        "compression_segment_count__webui",
    "auto_compress_disabled":           "auto_compress_disabled__webui",
    "auto_compress_fail_count":         "auto_compress_fail_count__webui",
    # Context / token tracking
    "context_length":                   "context_length__webui",
    "last_prompt_tokens":               "last_prompt_tokens__webui",
    "threshold_tokens":                 "threshold_tokens__webui",
}


def migrate_webui_columns(db) -> None:
    """Migrate WebUI columns to __webui names and drop old columns.

    Called once on every startup after SessionDB opens the database.
    Idempotent — safe to call repeatedly.
    """
    import sqlite3

    def _do_migrate(conn):
        existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        for old, new in WEBUI_COLUMN_RENAMES.items():
            has_new = new in existing
            has_old = old in existing

            # 1. Ensure __webui column exists
            if not has_new:
                try:
                    conn.execute(f'ALTER TABLE sessions ADD COLUMN "{new}" TEXT')
                except sqlite3.OperationalError:
                    pass

            # 2. Copy data from old to new (if old still exists)
            if has_old:
                try:
                    conn.execute(
                        f'UPDATE sessions SET "{new}" = "{old}" '
                        f'WHERE "{old}" IS NOT NULL AND "{new}" IS NULL'
                    )
                except sqlite3.OperationalError:
                    pass

            # 3. Drop old column (SQLite 3.35+)
            if has_old:
                try:
                    conn.execute(f'ALTER TABLE sessions DROP COLUMN "{old}"')
                except sqlite3.OperationalError:
                    pass

    try:
        db._execute_write(_do_migrate)
    except Exception:
        pass


def ensure_pinned_archived_webui_columns(db) -> None:
    """Add ``pinned__webui`` and ``archived__webui`` columns alongside the
    agent-owned ``pinned`` / ``archived`` columns, and copy existing values.

    Called once on every startup.  Idempotent — safe to call repeatedly.
    The original agent columns are **not** dropped — they belong to the
    hermes-agent schema.  After this migration the WebUI reads and writes
    only the ``__webui`` variants.
    """

    def _do_migrate(conn):
        existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        for col in ("pinned", "archived"):
            webui_col = f"{col}__webui"

            # 1. Ensure __webui column exists
            if webui_col not in existing:
                try:
                    conn.execute(
                        f'ALTER TABLE sessions ADD COLUMN "{webui_col}" '
                        f"INTEGER NOT NULL DEFAULT 0"
                    )
                except Exception:
                    pass

            # 2. Copy values from agent column → __webui column
            #    Only copy when agent column has a truthy value and __webui
            #    column is still 0/NULL (one-time migration for existing rows).
            try:
                conn.execute(
                    f'UPDATE sessions SET "{webui_col}" = "{col}" '
                    f'WHERE "{col}" = 1 AND ("{webui_col}" IS NULL OR "{webui_col}" = 0)'
                )
            except Exception:
                pass

    try:
        db._execute_write(_do_migrate)
    except Exception:
        pass


def ensure_original_content_webui_column(db) -> None:
    """Add ``original_content__webui`` column to the messages table.

    Stores the user's original slash-command input alongside the expanded
    skill content.  Called once on startup.  Idempotent.
    """

    def _do_migrate(conn):
        existing = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        if "original_content__webui" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN original_content__webui TEXT"
                )
            except Exception:
                pass

    try:
        db._execute_write(_do_migrate)
    except Exception:
        pass


def ensure_timestamp_webui_column(db) -> None:
    """Add ``timestamp__webui`` column to the messages table.

    Stores the webui-assigned per-message display timestamp (Unix seconds).
    Unlike the SDK-managed ``timestamp`` column which is written at
    turn-start/turn-end checkpoints, this column captures the actual
    moment each message appeared during streaming.  Called once on
    startup.  Idempotent.
    """

    def _do_migrate(conn):
        existing = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        if "timestamp__webui" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN timestamp__webui REAL"
                )
            except Exception:
                pass

    try:
        db._execute_write(_do_migrate)
    except Exception:
        pass
