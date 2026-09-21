import json
from datetime import datetime, timedelta

import pytest

from lpwatch import agenda
from lpwatch.model import JST, DataError, Reception

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=JST)


def make(event_id="e1", kind="lottery", **kw):
    base = dict(
        event_id=event_id,
        title="ライブ",
        url=f"https://t.livepocket.jp/e/{event_id}",
        reception_type=kind,
        opens_at=(NOW - timedelta(hours=1)).isoformat(),
        closes_at=(NOW + timedelta(days=3)).isoformat(),
    )
    base.update(kw)
    return Reception(**base)


def write_targets(tmp_path, rows):
    (tmp_path / "targets.json").write_text(
        json.dumps({"auto_apply": rows}, ensure_ascii=False), encoding="utf-8"
    )
    return agenda.load_targets(tmp_path)


def test_no_targets_means_no_auto_apply(tmp_path):
    """対象未設定なら何も申し込まない。検知した全件に申し込む設計にはしない。"""
    assert agenda.load_targets(tmp_path) == []
    assert agenda.pending_jobs({"e1:lottery": make()}, {}, [], NOW) == []


def test_only_listed_targets_are_applied(tmp_path):
    targets = write_targets(tmp_path, [{"match": "e1"}])
    events = {"e1:lottery": make("e1"), "e2:lottery": make("e2")}
    jobs = agenda.pending_jobs(events, {}, targets, NOW)
    assert [j.reception.event_id for j in jobs] == ["e1"]


def test_target_matches_by_title(tmp_path):
    targets = write_targets(tmp_path, [{"match": "ワンマン"}])
    events = {"e1:lottery": make(title="秋のワンマンツアー")}
    assert len(agenda.pending_jobs(events, {}, targets, NOW)) == 1


def test_invalid_target_is_rejected(tmp_path):
    (tmp_path / "targets.json").write_text('{"auto_apply": [{"quantity": 2}]}', encoding="utf-8")
    with pytest.raises(DataError):
        agenda.load_targets(tmp_path)


def test_zero_quantity_is_rejected(tmp_path):
    (tmp_path / "targets.json").write_text(
        '{"auto_apply": [{"match": "e1", "quantity": 0}]}', encoding="utf-8"
    )
    with pytest.raises(DataError):
        agenda.load_targets(tmp_path)


def test_already_handled_receptions_are_skipped(tmp_path):
    targets = write_targets(tmp_path, [{"match": "e1"}])
    events = {"e1:lottery": make()}
    for mark in ("applied", "skipped", "failed"):
        assert agenda.pending_jobs(events, {"e1:lottery": {mark}}, targets, NOW) == []


def test_failed_is_not_retried_automatically(tmp_path):
    """失敗の原因はどれも人の確認を要する。機械的な再試行は重複申込を招く。"""
    targets = write_targets(tmp_path, [{"match": "e1"}])
    assert agenda.pending_jobs({"e1:lottery": make()}, {"e1:lottery": {"failed"}}, targets, NOW) == []


def test_not_yet_open_is_skipped(tmp_path):
    targets = write_targets(tmp_path, [{"match": "e1"}])
    events = {"e1:lottery": make(opens_at=(NOW + timedelta(hours=2)).isoformat())}
    assert agenda.pending_jobs(events, {}, targets, NOW) == []


def test_too_close_to_deadline_is_skipped(tmp_path):
    """締切直前に走り出すと、申込済みか未申込か分からない状態で終わる。"""
    targets = write_targets(tmp_path, [{"match": "e1"}])
    events = {"e1:lottery": make(closes_at=(NOW + timedelta(seconds=30)).isoformat())}
    assert agenda.pending_jobs(events, {}, targets, NOW) == []


def test_first_come_is_ordered_before_lottery(tmp_path):
    """抽選は申込順が結果に影響しないが、先着は1秒でも早い方がよい。"""
    targets = write_targets(tmp_path, [{"match": "e1"}, {"match": "e2"}])
    events = {"e1:lottery": make("e1", "lottery"), "e2:first_come": make("e2", "first_come")}
    jobs = agenda.pending_jobs(events, {}, targets, NOW)
    assert [j.reception.event_id for j in jobs] == ["e2", "e1"]


def test_wake_times_only_for_first_come(tmp_path):
    """抽選は締切が数日先なので、専用の起床は要らない。"""
    targets = write_targets(tmp_path, [{"match": "e1"}, {"match": "e2"}])
    opens = NOW + timedelta(hours=5)
    events = {
        "e1:lottery": make("e1", "lottery", opens_at=opens.isoformat()),
        "e2:first_come": make("e2", "first_come", opens_at=opens.isoformat()),
    }
    times = agenda.wake_times(events, {}, targets, NOW)
    assert [r.event_id for _, r in times] == ["e2"]
    assert times[0][0] == opens - agenda.WAKE_LEAD


def test_wake_times_ignore_untargeted_and_past(tmp_path):
    targets = write_targets(tmp_path, [{"match": "e1"}])
    events = {
        "e1:first_come": make("e1", "first_come", opens_at=(NOW - timedelta(hours=1)).isoformat()),
        "e2:first_come": make("e2", "first_come", opens_at=(NOW + timedelta(hours=5)).isoformat()),
    }
    assert agenda.wake_times(events, {}, targets, NOW) == []
