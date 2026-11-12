from __future__ import annotations

from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.parse_change import parse_change_fixture
from app.modules.gap_wait_or_skip import (
    ACT_APPLY,
    ACT_DEFERRED_GAP,
    ACT_QUARANTINE_GAP,
    ACT_SKIPPED_DUPLICATE,
    ACT_SKIPPED_GAP,
    DEFAULT_WAIT_WINDOW,
    LEDGER_DISPOSITION,
    POLICY_STOP_QUARANTINE,
    normalize_policy,
    normalize_wait_window,
    plan_gap_run,
)
from app.repositories import cursors as cursor_repo
from app.repositories import ledger as ledger_repo
from app.repositories import quarantine as quarantine_repo
from app.repositories import runs as runs_repo
from app.repositories import settings as settings_repo
from app.repositories import sink as sink_repo
from app.repositories import streams as streams_repo


def preview_stream(stream_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        stream = streams_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        path = FIXTURES_DIR / stream["fixture_path"]
        events = parse_change_fixture(path)
        return {"stream": stream, "count": len(events), "events": events[:50]}
    finally:
        conn.close()


def _gap_event(seq: int) -> dict[str, Any]:
    """被跳过位点的合成账本事件：无真实事件，仅留审计轨迹。"""
    return {"seq": seq, "op": "gap", "entity_key": "", "event_ts": "", "payload": {}}


def run_apply(stream_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        stream = streams_repo.get_stream(conn, stream_id)
        if not stream:
            raise ValueError("stream not found")
        if not stream["enabled"]:
            raise ValueError("stream disabled")

        cursor_before = int(stream["apply_cursor"] or 0)
        # 策略在 run 开始时读取一次：同一次 run 内不混用提交语义；
        # 切换设置后再次 Apply 即按新策略生效。
        gap_policy = normalize_policy(settings_repo.get(conn, "gap_policy", POLICY_STOP_QUARANTINE))
        wait_window = normalize_wait_window(
            settings_repo.get(conn, "gap_wait_window", str(DEFAULT_WAIT_WINDOW))
        )
        run_id = runs_repo.create_run(conn, stream_id, cursor_before)
        path = FIXTURES_DIR / stream["fixture_path"]

        try:
            events = parse_change_fixture(path)
        except Exception as exc:  # noqa: BLE001
            runs_repo.finish_run(
                conn,
                run_id,
                status="failed",
                loaded=0,
                applied=0,
                quarantined=0,
                skipped=0,
                deferred=0,
                cursor_after=cursor_before,
                report={"error": str(exc), "gap_policy": gap_policy},
            )
            conn.commit()
            raise

        plan = plan_gap_run(
            events=events,
            cursor=cursor_before,
            is_applied=lambda seq: cursor_repo.is_applied(conn, stream_id, seq),
            policy=gap_policy,
            wait_window=wait_window,
        )

        loaded = len(events)
        applied = 0
        quarantined = 0
        skipped = 0
        deferred = 0
        skipped_duplicate = 0
        dispositions: list[str] = []

        for action in plan.actions:
            disposition = LEDGER_DISPOSITION[action.kind]
            dispositions.append(disposition)

            if action.kind == ACT_APPLY:
                event = action.event
                if event["op"] == "upsert":
                    sink_repo.upsert(
                        conn,
                        stream_id=stream_id,
                        entity_key=event["entity_key"],
                        payload=event.get("payload") or {},
                        seq=int(event["seq"]),
                        event_ts=event["event_ts"],
                    )
                else:
                    sink_repo.delete(conn, stream_id=stream_id, entity_key=event["entity_key"])
                cursor_repo.mark_applied(conn, stream_id, int(event["seq"]), run_id)
                streams_repo.set_cursor(conn, stream_id, int(event["seq"]))
                ledger_repo.insert_ledger(
                    conn, stream_id=stream_id, run_id=run_id, event=event, disposition=disposition
                )
                applied += 1
                continue

            if action.kind == ACT_SKIPPED_GAP:
                # 缺失位点：只记账本审计，不进 applied_seqs / Sink
                ledger_repo.insert_ledger(
                    conn,
                    stream_id=stream_id,
                    run_id=run_id,
                    event=_gap_event(action.seq),
                    disposition=disposition,
                )
                skipped += 1
                continue

            event = action.event
            ledger_repo.insert_ledger(
                conn, stream_id=stream_id, run_id=run_id, event=event, disposition=disposition
            )
            if action.kind == ACT_SKIPPED_DUPLICATE:
                skipped += 1
                skipped_duplicate += 1
            elif action.kind == ACT_DEFERRED_GAP:
                # 等待窗口内暂缓：不提交游标、不记已应用、不进 Sink
                deferred += 1
            elif action.kind == ACT_QUARANTINE_GAP:
                quarantine_repo.insert(
                    conn,
                    stream_id=stream_id,
                    run_id=run_id,
                    seq=int(event["seq"]),
                    entity_key=event["entity_key"],
                    reason=action.reason,
                    payload=event.get("payload") or {},
                )
                quarantined += 1

        report = {
            "stream_id": stream_id,
            "gap_policy": gap_policy,
            "wait_window": wait_window,
            "dispositions": dispositions,
            "stopped_on_gap": plan.stopped_on_gap,
            "stop_reason": plan.stop_reason,
            "waiting": plan.waiting,
            "awaiting_seqs": plan.awaiting_seqs,
            "deferred_seqs": plan.deferred_seqs,
            "skipped_gap_seqs": plan.skipped_gap_seqs,
            "skipped_duplicate_count": skipped_duplicate,
            "sink_written": applied,
            "sink_count_after": sink_repo.count_sink(conn, stream_id=stream_id),
            "quarantine_count_after": quarantine_repo.count_quarantine(conn, stream_id=stream_id),
        }
        runs_repo.finish_run(
            conn,
            run_id,
            status=plan.status,
            loaded=loaded,
            applied=applied,
            quarantined=quarantined,
            skipped=skipped,
            deferred=deferred,
            cursor_after=plan.cursor_after,
            report=report,
        )
        conn.commit()
        return runs_repo.get_run(conn, run_id) or {}
    finally:
        conn.close()
