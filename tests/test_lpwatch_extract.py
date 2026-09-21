from datetime import datetime

import pytest

from lpwatch import extract
from lpwatch.model import JST

REF = datetime(2026, 9, 21, 12, 0, tzinfo=JST)


def test_finds_event_url():
    text = "受付中！ https://t.livepocket.jp/e/abc_12 よろしく"
    assert extract.find_event_urls(text) == [("abc_12", "https://t.livepocket.jp/e/abc_12")]


def test_dedupes_repeated_urls():
    text = "https://t.livepocket.jp/e/aaa と https://t.livepocket.jp/e/aaa"
    assert len(extract.find_event_urls(text)) == 1


def test_ignores_other_domains():
    assert extract.find_event_urls("https://example.com/e/aaa") == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("先行抽選受付中", "lottery"),
        ("一般発売は先着順です", "first_come"),
        # 抽選と先着が併記された告知では、取りこぼすと申し込めない先着を優先する
        ("先行抽選のあと一般先着", "first_come"),
        ("チケット情報", "unknown"),
    ],
)
def test_detect_reception_type(text, expected):
    assert extract.detect_reception_type(text) == expected


@pytest.mark.parametrize(
    "text,opens,closes",
    [
        (
            "受付期間: 2026年10月1日(水) 12:00 〜 2026年10月5日(日) 23:59",
            "2026-10-01T12:00:00+09:00",
            "2026-10-05T23:59:00+09:00",
        ),
        # 年の省略。基準日以降で最も近い年を採る
        ("10/1 12:00〜10/5 23:59", "2026-10-01T12:00:00+09:00", "2026-10-05T23:59:00+09:00"),
        # 年をまたぐ告知
        ("1/10 10:00〜1/20 23:59", "2027-01-10T10:00:00+09:00", "2027-01-20T23:59:00+09:00"),
        # 締切単独。時刻なしの締切は「その日いっぱい」
        ("〜10月5日まで", "", "2026-10-05T23:59:00+09:00"),
    ],
)
def test_parse_period(text, opens, closes):
    assert extract.parse_period(text, REF) == (opens, closes)


def test_24h_notation_rolls_over_to_next_day():
    """「24:00」は翌日0時の意味で使われる。"""
    _, closes = extract.parse_period("10/1 12:00〜10/5 24:00", REF)
    assert closes == "2026-10-06T00:00:00+09:00"


def test_impossible_date_is_not_invented():
    assert extract.parse_period("2月30日まで", REF) == ("", "")


def test_unparseable_period_returns_empty():
    """読めない表記から締切をでっち上げない。"""
    assert extract.parse_period("受付期間は決まり次第お知らせします", REF) == ("", "")


def test_extract_builds_reception():
    text = (
        "【チケット情報】ワンマンライブ2026\n"
        "先行抽選受付: 10月1日(水) 12:00 〜 10月5日(日) 23:59\n"
        "https://t.livepocket.jp/e/xyz99"
    )
    (r,) = extract.extract(text, source="https://x.com/foo/status/1", ref=REF)
    assert r.event_id == "xyz99"
    assert r.reception_type == "lottery"
    assert r.closes_at == "2026-10-05T23:59:00+09:00"
    assert r.title == "【チケット情報】ワンマンライブ2026"
    assert r.source == "https://x.com/foo/status/1"


def test_extract_handles_multiple_events_in_one_post():
    """ツアー全公演の一括告知では URL ごとに1件返す。"""
    text = (
        "ツアー受付開始 10/1 12:00〜10/5 23:59 抽選\n"
        "東京 https://t.livepocket.jp/e/tokyo1\n"
        "大阪 https://t.livepocket.jp/e/osaka1"
    )
    found = extract.extract(text, ref=REF)
    assert [r.event_id for r in found] == ["tokyo1", "osaka1"]
    assert all(r.closes_at == "2026-10-05T23:59:00+09:00" for r in found)


def test_extract_without_url_returns_nothing():
    assert extract.extract("抽選受付中 10/1〜10/5", ref=REF) == []
