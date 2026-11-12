from __future__ import annotations

import sqlite3
from typing import Any


def list_streams(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, name, fixture_path, entity_type, apply_cursor, enabled, note FROM streams ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_stream(conn: sqlite3.Connection, stream_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, name, fixture_path, entity_type, apply_cursor, enabled, note FROM streams WHERE id=?",
        (stream_id,),
    ).fetchone()
    return dict(row) if row else None


def set_cursor(conn: sqlite3.Connection, stream_id: int, cursor: int) -> None:
    conn.execute("UPDATE streams SET apply_cursor=? WHERE id=?", (cursor, stream_id))


def set_enabled(conn: sqlite3.Connection, stream_id: int, enabled: bool) -> None:
    conn.execute("UPDATE streams SET enabled=? WHERE id=?", (1 if enabled else 0, stream_id))


def reset_stream_state(conn: sqlite3.Connection, stream_id: int) -> None:
    """清空一条流的游标/已应用集合/账本/隔离/Sink/Runs，便于按新策略重放演示。"""
    conn.execute("DELETE FROM applied_seqs WHERE stream_id=?", (stream_id,))
    conn.execute("DELETE FROM change_ledger WHERE stream_id=?", (stream_id,))
    conn.execute("DELETE FROM quarantine WHERE stream_id=?", (stream_id,))
    conn.execute("DELETE FROM sink_records WHERE stream_id=?", (stream_id,))
    conn.execute("DELETE FROM apply_runs WHERE stream_id=?", (stream_id,))
    set_cursor(conn, stream_id, 0)
