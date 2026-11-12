# 0-1 cut: gap_wait_or_skip
"""缺口三策略（互斥）：停隔离 / 等待窗口 / 跳过前进。

同一次 run 只取一种提交语义：

- ``stop_quarantine``：遇到缺口立即停，缺口起所有后续事件进隔离区
  （reason=gap_before_seq），游标停在缺口前。
- ``wait_window``：缺失 seq 数 <= 等待窗口 → 暂缓（deferred）：游标不提交过缺口、
  事件不记已应用、不进 Sink，留待后续 run 重新裁决；缺失数 > 窗口 → 当场裁决，
  缺口起所有后续事件进隔离区（reason=gap_beyond_wait_window）。
- ``skip_gap``：跳过缺失 seq 继续应用更高 seq；被跳过的位点记账本
  （disposition=skipped_gap，可审计），但不进 applied_seqs、不假装写入 Sink。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

POLICY_STOP_QUARANTINE = "stop_quarantine"
POLICY_WAIT_WINDOW = "wait_window"
POLICY_SKIP_GAP = "skip_gap"
POLICIES = (POLICY_STOP_QUARANTINE, POLICY_WAIT_WINDOW, POLICY_SKIP_GAP)
DEFAULT_WAIT_WINDOW = 2

REASON_GAP_BEFORE_SEQ = "gap_before_seq"
REASON_GAP_BEYOND_WAIT_WINDOW = "gap_beyond_wait_window"

# 动作类型；除 apply 外与账本 disposition 同名（apply 入账记为 applied）
ACT_APPLY = "apply"
ACT_SKIPPED_DUPLICATE = "skipped_duplicate"
ACT_SKIPPED_GAP = "skipped_gap"
ACT_DEFERRED_GAP = "deferred_gap"
ACT_QUARANTINE_GAP = "quarantine_gap"

LEDGER_DISPOSITION = {
    ACT_APPLY: "applied",
    ACT_SKIPPED_DUPLICATE: ACT_SKIPPED_DUPLICATE,
    ACT_SKIPPED_GAP: ACT_SKIPPED_GAP,
    ACT_DEFERRED_GAP: ACT_DEFERRED_GAP,
    ACT_QUARANTINE_GAP: ACT_QUARANTINE_GAP,
}


@dataclass
class GapAction:
    kind: str
    seq: int
    event: dict[str, Any] | None = None  # skipped_gap 无真实事件（缺失位点）
    reason: str = ""


@dataclass
class GapPlan:
    actions: list[GapAction] = field(default_factory=list)
    status: str = "succeeded"  # succeeded | partial | waiting
    stopped_on_gap: bool = False
    waiting: bool = False
    stop_reason: str = ""
    awaiting_seqs: list[int] = field(default_factory=list)  # 正在等待的缺失位点
    deferred_seqs: list[int] = field(default_factory=list)  # 暂缓（未提交）的事件 seq
    skipped_gap_seqs: list[int] = field(default_factory=list)  # 被跳过的缺失位点
    cursor_after: int = 0


def normalize_policy(raw: str | None) -> str:
    return raw if raw in POLICIES else POLICY_STOP_QUARANTINE


def normalize_wait_window(raw: Any) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_WAIT_WINDOW


def plan_gap_run(
    *,
    events: list[dict[str, Any]],
    cursor: int,
    is_applied: Callable[[int], bool],
    policy: str,
    wait_window: Any,
) -> GapPlan:
    """对一批按 seq 升序的事件做缺口裁决。纯函数，不写库，便于测试与复用。"""
    policy = normalize_policy(policy)
    window = normalize_wait_window(wait_window)
    plan = GapPlan(cursor_after=int(cursor))
    cur = int(cursor)

    for event in events:
        seq = int(event["seq"])

        # 已裁决停隔离：缺口起所有后续事件一律隔离（同一 run 不混用语义）
        if plan.stopped_on_gap:
            plan.actions.append(GapAction(ACT_QUARANTINE_GAP, seq, event, plan.stop_reason))
            continue
        # 已进入等待：游标未提交过缺口，后续事件一律暂缓，不算已应用
        if plan.waiting:
            plan.deferred_seqs.append(seq)
            plan.actions.append(GapAction(ACT_DEFERRED_GAP, seq, event))
            continue

        if seq <= cur or is_applied(seq):
            plan.actions.append(GapAction(ACT_SKIPPED_DUPLICATE, seq, event))
            continue

        expected = cur + 1
        if seq == expected:
            plan.actions.append(GapAction(ACT_APPLY, seq, event))
            cur = seq
            continue

        # seq > expected：缺口为 [expected, seq-1]
        missing = list(range(expected, seq))
        if policy == POLICY_SKIP_GAP:
            for m in missing:
                plan.skipped_gap_seqs.append(m)
                plan.actions.append(GapAction(ACT_SKIPPED_GAP, m, None))
            plan.actions.append(GapAction(ACT_APPLY, seq, event))
            cur = seq
            continue
        if policy == POLICY_WAIT_WINDOW and len(missing) <= window:
            plan.waiting = True
            plan.awaiting_seqs = missing
            plan.deferred_seqs.append(seq)
            plan.actions.append(GapAction(ACT_DEFERRED_GAP, seq, event))
            continue
        # stop_quarantine，或等待窗口外 → 当场裁决为停隔离
        plan.stopped_on_gap = True
        plan.stop_reason = (
            REASON_GAP_BEYOND_WAIT_WINDOW
            if policy == POLICY_WAIT_WINDOW
            else REASON_GAP_BEFORE_SEQ
        )
        plan.actions.append(GapAction(ACT_QUARANTINE_GAP, seq, event, plan.stop_reason))

    plan.cursor_after = cur
    if plan.stopped_on_gap:
        plan.status = "partial"
    elif plan.waiting:
        plan.status = "waiting"
    else:
        plan.status = "succeeded"
    return plan
