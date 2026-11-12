from app.db import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS streams(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    fixture_path TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    apply_cursor INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT
);
CREATE TABLE IF NOT EXISTS apply_runs(
    id INTEGER PRIMARY KEY,
    stream_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    loaded_count INTEGER NOT NULL,
    applied_count INTEGER NOT NULL,
    quarantined_count INTEGER NOT NULL,
    skipped_count INTEGER NOT NULL,
    deferred_count INTEGER NOT NULL DEFAULT 0,
    cursor_before INTEGER NOT NULL,
    cursor_after INTEGER NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_ledger(
    id INTEGER PRIMARY KEY,
    stream_id INTEGER NOT NULL,
    run_id INTEGER,
    seq INTEGER NOT NULL,
    op TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    event_ts TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    disposition TEXT NOT NULL,
    UNIQUE(stream_id, seq, run_id)
);
CREATE TABLE IF NOT EXISTS sink_records(
    id INTEGER PRIMARY KEY,
    stream_id INTEGER NOT NULL,
    entity_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    last_seq INTEGER NOT NULL,
    last_event_ts TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(stream_id, entity_key)
);
CREATE TABLE IF NOT EXISTS quarantine(
    id INTEGER PRIMARY KEY,
    stream_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    entity_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applied_seqs(
    stream_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY(stream_id, seq)
);
"""

SEED_STREAMS = [
    (
        "Orders CDC",
        "orders_cdc.jsonl",
        "order",
        0,
        1,
        "clean：seq 1→5 连续；含一次 delete",
    ),
    (
        "Inventory CDC",
        "inventory_cdc.jsonl",
        "inventory",
        0,
        1,
        "clean：seq 1→3 连续库存变更",
    ),
    (
        "Payments CDC",
        "payments_cdc_gap.jsonl",
        "payment",
        0,
        1,
        "含缺口：缺 seq 3 与 7-8，用于缺口策略（停隔离/等待/跳过）演示",
    ),
]

SEED_SETTINGS = [
    ("project_label", "Changeflow"),
    ("gap_policy", "stop_quarantine"),
    ("gap_wait_window", "2"),
]


def _migrate(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(apply_runs)").fetchall()}
    if "deferred_count" not in cols:
        conn.execute(
            "ALTER TABLE apply_runs ADD COLUMN deferred_count INTEGER NOT NULL DEFAULT 0"
        )


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.executemany(
        "INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)", SEED_SETTINGS
    )
    if conn.execute("SELECT COUNT(*) c FROM streams").fetchone()["c"] == 0:
        conn.executemany(
            """
            INSERT INTO streams(name, fixture_path, entity_type, apply_cursor, enabled, note)
            VALUES (?,?,?,?,?,?)
            """,
            SEED_STREAMS,
        )
    else:
        # 已有库补齐演示流（按夹具路径幂等）
        for name, fixture, entity_type, cursor, enabled, note in SEED_STREAMS:
            exists = conn.execute(
                "SELECT 1 FROM streams WHERE fixture_path=?", (fixture,)
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO streams(name, fixture_path, entity_type, apply_cursor, enabled, note)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (name, fixture, entity_type, cursor, enabled, note),
                )
    conn.commit()
    conn.close()
