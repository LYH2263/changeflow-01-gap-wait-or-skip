import json

import pytest

import app.db as db_module
from app import seed
from app.db import connect
from app.repositories import cursors as cursor_repo
from app.repositories import quarantine as quarantine_repo
from app.repositories import settings as settings_repo
from app.repositories import sink as sink_repo
from app.repositories import streams as streams_repo
from app.services import apply_service

GAP_EVENTS = [
    {"seq": 1, "op": "upsert", "entity_key": "P-1", "event_ts": "t1", "payload": {"v": 1}},
    {"seq": 2, "op": "upsert", "entity_key": "P-1", "event_ts": "t2", "payload": {"v": 2}},
    {"seq": 4, "op": "upsert", "entity_key": "P-2", "event_ts": "t4", "payload": {"v": 4}},
    {"seq": 5, "op": "upsert", "entity_key": "P-2", "event_ts": "t5", "payload": {"v": 5}},
    {"seq": 6, "op": "upsert", "entity_key": "P-3", "event_ts": "t6", "payload": {"v": 6}},
    {"seq": 9, "op": "delete", "entity_key": "P-3", "event_ts": "t9", "payload": {}},
]


@pytest.fixture()
def stream_id(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(apply_service, "FIXTURES_DIR", tmp_path)
    seed.init_db()
    (tmp_path / "gap.jsonl").write_text(
        "\n".join(json.dumps(e) for e in GAP_EVENTS) + "\n", encoding="utf-8"
    )
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT INTO streams(name, fixture_path, entity_type, apply_cursor, enabled, note)"
            " VALUES ('Gap', 'gap.jsonl', 'pay', 0, 1, '')"
        )
        conn.commit()
        sid = int(cur.lastrowid)
    finally:
        conn.close()
    return sid


def set_policy(policy, window=None):
    conn = connect()
    try:
        settings_repo.put(conn, "gap_policy", policy)
        if window is not None:
            settings_repo.put(conn, "gap_wait_window", str(window))
        conn.commit()
    finally:
        conn.close()


def ledger_rows(sid, run_id):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT seq, disposition FROM change_ledger WHERE stream_id=? AND run_id=? ORDER BY id",
            (sid, run_id),
        ).fetchall()
        return [(r["seq"], r["disposition"]) for r in rows]
    finally:
        conn.close()


def applied_seqs(sid):
    conn = connect()
    try:
        return {
            r["seq"]
            for r in conn.execute(
                "SELECT seq FROM applied_seqs WHERE stream_id=?", (sid,)
            ).fetchall()
        }
    finally:
        conn.close()


def quarantine_rows(sid):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT seq, reason FROM quarantine WHERE stream_id=? ORDER BY id", (sid,)
        ).fetchall()
        return [(r["seq"], r["reason"]) for r in rows]
    finally:
        conn.close()


def assert_reconciliation(run):
    rep = run["report"]
    dup = rep["skipped_duplicate_count"]
    assert (
        run["loaded_count"]
        == run["applied_count"]
        + run["quarantined_count"]
        + run["deferred_count"]
        + dup
    )
    # 账本行数 = 加载事件 + 合成跳过位点
    rows = ledger_rows(run["stream_id"], run["id"])
    assert len(rows) == run["loaded_count"] + len(rep["skipped_gap_seqs"])
    assert sum(1 for _, d in rows if d == "applied") == run["applied_count"]
    assert sum(1 for _, d in rows if d == "quarantine_gap") == run["quarantined_count"]
    assert sum(1 for _, d in rows if d == "deferred_gap") == run["deferred_count"]


def test_stop_quarantine_then_skip_gap_switch(stream_id):
    set_policy("stop_quarantine")
    run1 = apply_service.run_apply(stream_id)
    assert run1["status"] == "partial"
    assert (run1["loaded_count"], run1["applied_count"], run1["quarantined_count"]) == (6, 2, 4)
    assert (run1["skipped_count"], run1["deferred_count"]) == (0, 0)
    assert (run1["cursor_before"], run1["cursor_after"]) == (0, 2)
    assert run1["report"]["gap_policy"] == "stop_quarantine"
    assert quarantine_rows(stream_id) == [(s, "gap_before_seq") for s in (4, 5, 6, 9)]
    assert applied_seqs(stream_id) == {1, 2}
    assert_reconciliation(run1)

    conn = connect()
    try:
        assert sink_repo.count_sink(conn, stream_id=stream_id) == 1  # 仅 P-1
    finally:
        conn.close()

    # 策略切换后再次 Apply：按新策略 skip_gap 生效
    set_policy("skip_gap")
    run2 = apply_service.run_apply(stream_id)
    assert run2["status"] == "succeeded"
    assert run2["report"]["gap_policy"] == "skip_gap"
    assert run2["applied_count"] == 4  # seq 4,5,6,9
    assert run2["skipped_count"] == 5  # 2 重复 + 3 跳过位点
    assert run2["report"]["skipped_gap_seqs"] == [3, 7, 8]
    assert run2["cursor_after"] == 9
    assert_reconciliation(run2)

    # 跳过位点可审计但不假装已应用
    assert applied_seqs(stream_id) == {1, 2, 4, 5, 6, 9}
    gap_rows = [(s, d) for s, d in ledger_rows(stream_id, run2["id"]) if d == "skipped_gap"]
    assert [s for s, _ in gap_rows] == [3, 7, 8]

    conn = connect()
    try:
        # Sink：P-1、P-2 在；P-3 seq6 建、seq9 删 → 不在
        assert sink_repo.count_sink(conn, stream_id=stream_id) == 2
        assert quarantine_repo.count_quarantine(conn, stream_id=stream_id) == 4  # run1 历史
        view = [v for v in cursor_repo.list_cursor_view(conn) if v["stream_id"] == stream_id][0]
        assert view["apply_cursor"] == 9
        assert view["applied_seq_count"] == 6
        assert view["max_applied_seq"] == 9
        assert view["skipped_gap_seqs"] == [3, 7, 8]
        assert view["waiting"] is False
    finally:
        conn.close()


def test_wait_window_holds_then_idempotent(stream_id):
    set_policy("wait_window", window=2)
    run1 = apply_service.run_apply(stream_id)
    assert run1["status"] == "waiting"
    assert (run1["applied_count"], run1["deferred_count"], run1["quarantined_count"]) == (2, 4, 0)
    assert run1["cursor_after"] == 2
    rep = run1["report"]
    assert rep["waiting"] is True
    assert rep["awaiting_seqs"] == [3]
    assert rep["deferred_seqs"] == [4, 5, 6, 9]
    assert_reconciliation(run1)

    # 暂缓事件未落地：不进 applied_seqs / Sink / 隔离区
    assert applied_seqs(stream_id) == {1, 2}
    conn = connect()
    try:
        assert sink_repo.count_sink(conn, stream_id=stream_id) == 1
        assert quarantine_repo.count_quarantine(conn, stream_id=stream_id) == 0
        view = [v for v in cursor_repo.list_cursor_view(conn) if v["stream_id"] == stream_id][0]
        assert view["waiting"] is True
        assert view["awaiting_seqs"] == [3]
    finally:
        conn.close()

    # 缺口未补前重跑：保持等待，不重复应用、不混用语义
    run2 = apply_service.run_apply(stream_id)
    assert run2["status"] == "waiting"
    assert run2["applied_count"] == 0
    assert run2["skipped_count"] == 2  # seq 1,2 重复
    assert run2["deferred_count"] == 4
    assert run2["cursor_after"] == 2
    assert applied_seqs(stream_id) == {1, 2}
    assert_reconciliation(run2)


def test_wait_window_beyond_window_quarantines(stream_id):
    set_policy("wait_window", window=0)
    run = apply_service.run_apply(stream_id)
    assert run["status"] == "partial"
    assert run["quarantined_count"] == 4
    assert quarantine_rows(stream_id) == [(s, "gap_beyond_wait_window") for s in (4, 5, 6, 9)]
    assert_reconciliation(run)


def test_reset_stream_allows_replay(stream_id):
    set_policy("stop_quarantine")
    apply_service.run_apply(stream_id)
    conn = connect()
    try:
        streams_repo.reset_stream_state(conn, stream_id)
        conn.commit()
        stream = streams_repo.get_stream(conn, stream_id)
        assert stream["apply_cursor"] == 0
        assert sink_repo.count_sink(conn, stream_id=stream_id) == 0
        assert quarantine_repo.count_quarantine(conn, stream_id=stream_id) == 0
    finally:
        conn.close()
    assert applied_seqs(stream_id) == set()

    set_policy("skip_gap")
    run = apply_service.run_apply(stream_id)
    assert run["status"] == "succeeded"
    assert run["cursor_after"] == 9
    assert run["report"]["skipped_gap_seqs"] == [3, 7, 8]
