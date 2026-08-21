"""Column management for WebUI-added columns in the shared state.db.

The sessions table in ~/.hermes/state.db is shared between the hermes-agent and
the WebUI.  Columns added by the WebUI are suffixed with ``__webui`` so they are
immediately recognisable as WebUI-managed and never conflict with future agent
schema additions.
"""

import logging

logger = logging.getLogger(__name__)


class IncompatibleSchemaError(Exception):
    """state.db has __webui columns with wrong types."""
    pass

# ── Expected schema for WebUI-added columns ───────────────────────────────────
# Maps column name → expected SQLite type.  Checked at startup; a type mismatch
# means the database is from an incompatible older version and must be recreated.

WEBUI_EXPECTED_COLUMNS: dict[str, str] = {
    "personality__webui":                   "TEXT",
    "reasoning_effort__webui":              "TEXT",
    "service_tier__webui":                  "TEXT",
    "skills__webui":                        "TEXT",
    "mode__webui":                          "TEXT",
    "session_approved__webui":              "TEXT",
    "project_id__webui":                    "TEXT",
    "profile__webui":                       "TEXT",
    "last_seen_msg_count__webui":           "INTEGER",
    "compression_anchor_visible_idx__webui": "INTEGER",
    "compression_anchor_message_key__webui": "TEXT",
    "last_compression_time__webui":         "REAL",
    "lineage_root_id__webui":               "TEXT",
    "lineage_tip_id__webui":                "TEXT",
    "compression_segment_count__webui":     "INTEGER",
    "auto_compress_disabled__webui":        "INTEGER",
    "auto_compress_fail_count__webui":      "INTEGER",
    "context_length__webui":                "INTEGER",
    "last_prompt_tokens__webui":            "INTEGER",
    "threshold_tokens__webui":              "INTEGER",
    "pinned__webui":                        "INTEGER",
    "archived__webui":                      "INTEGER",
    "llm_title_generated__webui":           "INTEGER",
}


def check_webui_column_types(db) -> None:
    """Verify WebUI column types match expectations.  Die on mismatch.

    Called once at startup after SessionDB opens the database.  If any
    __webui column exists with an unexpected type, the database is from
    an incompatible older version — log the details and exit so the
    operator can delete state.db and restart.
    """

    def _do_check(conn):
        actual = {
            row[1]: row[2]
            for row in conn.execute("PRAGMA table_info(sessions)")
        }
        mismatches = []
        for col, expected_type in WEBUI_EXPECTED_COLUMNS.items():
            if col in actual and actual[col] != expected_type:
                mismatches.append(f"  {col}: expected {expected_type}, got {actual[col]}")
        return mismatches

    try:
        mismatches = db._execute_write(_do_check)
    except Exception:
        return

    if mismatches:
        msg = (
            "state.db schema incompatible with this version of WebUI.\n"
            "The following __webui columns have wrong types:\n"
            + "\n".join(mismatches)
            + "\n\nFix: delete state.db and restart:\n"
            "  rm ~/.hermes/state.db ~/.hermes/state.db-shm ~/.hermes/state.db-wal"
        )
        logger.error(msg)
        raise IncompatibleSchemaError(msg)


def ensure_webui_columns(db) -> None:
    """Add missing __webui columns to the sessions table.

    Called once at startup.  Idempotent — safe to call repeatedly.
    Only adds columns that don't exist yet; never modifies existing ones.
    """

    def _do_ensure(conn):
        existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        for col, col_type in WEBUI_EXPECTED_COLUMNS.items():
            if col not in existing:
                try:
                    conn.execute(f'ALTER TABLE sessions ADD COLUMN "{col}" {col_type}')
                except Exception:
                    pass

    try:
        db._execute_write(_do_ensure)
    except Exception:
        pass


def ensure_original_content_webui_column(db) -> None:
    """Add ``original_content__webui`` column to the messages table.

    Stores the user's original slash-command input alongside the expanded
    skill content.  Called once on startup.  Idempotent.
    """

    def _do_ensure(conn):
        existing = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        if "original_content__webui" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN original_content__webui TEXT"
                )
            except Exception:
                pass

    try:
        db._execute_write(_do_ensure)
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

    def _do_ensure(conn):
        existing = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        if "timestamp__webui" not in existing:
            try:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN timestamp__webui REAL"
                )
            except Exception:
                pass

    try:
        db._execute_write(_do_ensure)
    except Exception:
        pass
