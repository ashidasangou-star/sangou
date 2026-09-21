from datetime import datetime, timedelta

from lpwatch import remind, store
from lpwatch.model import JST, Reception

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=JST)


def make(event_id="e1", **kw):
    base = dict(event_id=event_id, title="ライブ", url=f"https://t.livepocket.jp/e/{event_id}")
    base.update(kw)
    return Reception(**base)


def test_merge_does_not_erase_known_values_with_blanks():
    """URLだけ先に拾い、あとで締切を取り直す順序が実運用で起きる。"""
    first = make(closes_at="2026-10-05T23:59:00+09:00")
    later = make(venue="Zepp Tokyo")
    merged = first.merge(later)
    assert merged.closes_at == "2026-10-05T23:59:00+09:00"
    assert merged.venue == "Zepp Tokyo"


def test_different_reception_types_are_separate_records():
    a = make(reception_type="lottery")
    b = make(reception_type="first_come")
    assert a.key != b.key


def test_append_skips_observations_that_add_nothing(tmp_path):
    r = make(closes_at="2026-10-05T23:59:00+09:00")
    assert len(store.append_events(tmp_path, [r])) == 1
    assert store.append_events(tmp_path, [r]) == []
    assert len(store.events_path(tmp_path).read_text().splitlines()) == 1


def test_append_records_newly_learned_fields(tmp_path):
    store.append_events(tmp_path, [make()])
    fresh = store.append_events(tmp_path, [make(closes_at="2026-10-05T23:59:00+09:00")])
    assert len(fresh) == 1
    assert store.load_events(tmp_path)["e1:unknown"].closes_at == "2026-10-05T23:59:00+09:00"


def test_marks_accumulate(tmp_path):
    store.add_mark(tmp_path, "e1:lottery", "notified")
    store.add_mark(tmp_path, "e1:lottery", "applied")
    assert store.load_marks(tmp_path)["e1:lottery"] == {"notified", "applied"}


def test_is_open_respects_window():
    r = make(opens_at="2026-09-20T12:00:00+09:00", closes_at="2026-09-22T12:00:00+09:00")
    assert r.is_open(NOW)
    assert not r.is_open(NOW + timedelta(days=2))
    assert not r.is_open(NOW - timedelta(days=2))


def test_unknown_window_counts_as_open():
    """締切不明を「終了」と扱うと申し込めるものを見落とす。"""
    assert make().is_open(NOW)


def test_new_reception_is_notified_once():
    events = {"e1:lottery": make(reception_type="lottery")}
    due = remind.due(events, {}, NOW)
    assert [m for _, m, _ in due] == ["notified"]
    assert remind.due(events, {"e1:lottery": {"notified"}}, NOW) == []


def test_deadline_stages_fire_once_each():
    closes = NOW + timedelta(hours=20)
    events = {"e1:lottery": make(reception_type="lottery", closes_at=closes.isoformat())}
    marks = {"e1:lottery": {"notified"}}

    due = remind.due(events, marks, NOW)
    assert [m for _, m, _ in due] == ["reminded:close24"]

    marks["e1:lottery"].add("reminded:close24")
    assert remind.due(events, marks, NOW) == []

    # 締切が迫ればさらに一段階だけ鳴る
    later = closes - timedelta(hours=2)
    assert [m for _, m, _ in remind.due(events, marks, later)] == ["reminded:close3"]


def test_applied_reception_stops_reminding():
    closes = NOW + timedelta(hours=1)
    events = {"e1:lottery": make(reception_type="lottery", closes_at=closes.isoformat())}
    assert remind.due(events, {"e1:lottery": {"notified", "applied"}}, NOW) == []


def test_expired_reception_is_not_reminded():
    events = {"e1:lottery": make(reception_type="lottery", closes_at=(NOW - timedelta(hours=1)).isoformat())}
    assert remind.due(events, {"e1:lottery": {"notified"}}, NOW) == []


def test_first_come_warns_before_opening():
    """先着は締切ではなく開始時刻が本番なので、開始前に鳴らす。"""
    opens = NOW + timedelta(minutes=10)
    events = {
        "e1:first_come": make(
            reception_type="first_come",
            opens_at=opens.isoformat(),
            closes_at=(NOW + timedelta(days=3)).isoformat(),
        )
    }
    due = remind.due(events, {"e1:first_come": {"notified"}}, NOW)
    assert [m for _, m, _ in due] == ["reminded:open15"]


def test_lottery_does_not_warn_before_opening():
    """抽選は申込順が結果に影響しないため、開始前に急かさない。"""
    opens = NOW + timedelta(minutes=10)
    events = {
        "e1:lottery": make(
            reception_type="lottery",
            opens_at=opens.isoformat(),
            closes_at=(NOW + timedelta(days=10)).isoformat(),
        )
    }
    assert remind.due(events, {"e1:lottery": {"notified"}}, NOW) == []


def test_has_ended_only_after_deadline():
    r = make(opens_at="2026-09-25T12:00:00+09:00", closes_at="2026-09-30T23:59:00+09:00")
    # 開始前はまだ終了ではない。ここを is_open で判定すると一覧から消える
    assert not r.has_ended(NOW)
    assert not r.is_open(NOW)
    assert r.has_ended(datetime(2026, 10, 1, tzinfo=JST))


def test_unknown_deadline_never_counts_as_ended():
    assert not make().has_ended(NOW)
