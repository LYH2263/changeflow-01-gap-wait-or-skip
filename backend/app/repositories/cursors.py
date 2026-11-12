from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_applied(conn: sqlite3.Connection, stream_id: int, seq: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM applied_seqs WHERE stream_id=? AND seq=?",
        (stream_id, seq),
    ).fetchone()
    return row is not None


def mark_applied(conn: sqlite3.Connection, stream_id: int, seq: int, run_id: int) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO applied_seqs(stream_id, seq, run_id, applied_at)
        VALUES (?,?,?,?)
        """,
        (stream_id, seq, run_id, _now()),
    )


def list_cursor_view(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.id AS stream_id, s.name, s.apply_cursor,
               (SELECT COUNT(*) FROM applied_seqs a WHERE a.stream_id=s.id) AS applied_seq_count,
               (SELECT MAX(seq) FROM applied_seqs a WHERE a.stream_id=s.id) AS max_applied_seq,
               (SELECT COUNT(*) FROM quarantine q WHERE q.stream_id=s.id) AS quarantine_count
        FROM streams s
        ORDER BY s.id
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        item["skipped_gap_seqs"] = [
            int(x["seq"])
            for x in conn.execute(
                """
                SELECT DISTINCT seq FROM change_ledger
                WHERE stream_id=? AND disposition='skipped_gap' ORDER BY seq
                """,
                (item["stream_id"],),
            ).fetchall()
        ]
        latest = conn.execute(
            "SELECT report_json FROM apply_runs WHERE stream_id=? ORDER BY id DESC LIMIT 1",
            (item["stream_id"],),
        ).fetchone()
        waiting = False
        awaiting: list[int] = []
        if latest:
            try:
                report = json.loads(latest["report_json"] or "{}")
            except json.JSONDecodeError:
                report = {}
            if report.get("waiting"):
                waiting = True
                awaiting = [int(s) for s in (report.get("awaiting_seqs") or [])]
        item["waiting"] = waiting
        item["awaiting_seqs"] = awaiting
        out.append(item)
    return out
