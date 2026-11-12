from app.modules.gap_wait_or_skip import (
    ACT_APPLY,
    ACT_DEFERRED_GAP,
    ACT_QUARANTINE_GAP,
    ACT_SKIPPED_DUPLICATE,
    ACT_SKIPPED_GAP,
    REASON_GAP_BEFORE_SEQ,
    REASON_GAP_BEYOND_WAIT_WINDOW,
    normalize_policy,
    normalize_wait_window,
    plan_gap_run,
)


def ev(seq):
    return {"seq": seq, "op": "upsert", "entity_key": f"E{seq}", "event_ts": "t", "payload": {}}


def kinds(plan):
    return [a.kind for a in plan.actions]


GAP = [ev(s) for s in (1, 2, 4, 5, 6, 9)]  # 缺 3 与 7-8
NONE = lambda seq: False  # noqa: E731


def test_stop_quarantine_quarantines_all_after_gap():
    plan = plan_gap_run(events=GAP, cursor=0, is_applied=NONE, policy="stop_quarantine", wait_window=2)
    assert kinds(plan) == [ACT_APPLY, ACT_APPLY] + [ACT_QUARANTINE_GAP] * 4
    assert plan.status == "partial"
    assert plan.stopped_on_gap and not plan.waiting
    assert plan.stop_reason == REASON_GAP_BEFORE_SEQ
    assert plan.cursor_after == 2
    assert [a.seq for a in plan.actions if a.kind == ACT_QUARANTINE_GAP] == [4, 5, 6, 9]
    assert [a.reason for a in plan.actions if a.kind == ACT_QUARANTINE_GAP] == [
        REASON_GAP_BEFORE_SEQ
    ] * 4


def test_skip_gap_records_positions_and_applies_higher_seqs():
    plan = plan_gap_run(events=GAP, cursor=0, is_applied=NONE, policy="skip_gap", wait_window=2)
    assert kinds(plan) == [
        ACT_APPLY,
        ACT_APPLY,
        ACT_SKIPPED_GAP,
        ACT_APPLY,
        ACT_APPLY,
        ACT_APPLY,
        ACT_SKIPPED_GAP,
        ACT_SKIPPED_GAP,
        ACT_APPLY,
    ]
    assert plan.skipped_gap_seqs == [3, 7, 8]
    assert plan.status == "succeeded"
    assert plan.cursor_after == 9
    # 被跳过位点不产生 apply 动作（不得假装已写入 Sink）
    assert all(a.kind != ACT_APPLY for a in plan.actions if a.seq in (3, 7, 8))


def test_wait_window_defers_within_window_without_committing_cursor():
    plan = plan_gap_run(events=GAP, cursor=0, is_applied=NONE, policy="wait_window", wait_window=2)
    assert kinds(plan) == [ACT_APPLY, ACT_APPLY] + [ACT_DEFERRED_GAP] * 4
    assert plan.status == "waiting"
    assert plan.waiting and not plan.stopped_on_gap
    assert plan.awaiting_seqs == [3]
    assert plan.deferred_seqs == [4, 5, 6, 9]
    assert plan.cursor_after == 2


def test_wait_window_beyond_window_adjudicates_to_quarantine():
    plan = plan_gap_run(events=GAP, cursor=0, is_applied=NONE, policy="wait_window", wait_window=0)
    assert kinds(plan) == [ACT_APPLY, ACT_APPLY] + [ACT_QUARANTINE_GAP] * 4
    assert plan.status == "partial"
    assert plan.stop_reason == REASON_GAP_BEYOND_WAIT_WINDOW


def test_contiguous_and_duplicates():
    plan = plan_gap_run(
        events=[ev(1), ev(2), ev(3)], cursor=0, is_applied=NONE,
        policy="stop_quarantine", wait_window=0,
    )
    assert kinds(plan) == [ACT_APPLY] * 3
    assert plan.status == "succeeded"
    assert plan.cursor_after == 3

    replay = plan_gap_run(
        events=[ev(1), ev(2), ev(3)], cursor=3, is_applied=lambda s: s <= 3,
        policy="skip_gap", wait_window=0,
    )
    assert kinds(replay) == [ACT_SKIPPED_DUPLICATE] * 3
    assert replay.cursor_after == 3


def test_unknown_policy_falls_back_to_stop_quarantine():
    plan = plan_gap_run(events=GAP, cursor=0, is_applied=NONE, policy="bogus", wait_window=9)
    assert plan.stopped_on_gap
    assert plan.stop_reason == REASON_GAP_BEFORE_SEQ


def test_normalizers():
    assert normalize_policy("nope") == "stop_quarantine"
    assert normalize_policy("skip_gap") == "skip_gap"
    assert normalize_wait_window("5") == 5
    assert normalize_wait_window("x") == 2
    assert normalize_wait_window(-3) == 0
