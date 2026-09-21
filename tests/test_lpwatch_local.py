"""ブラウザを使わない部分のテスト。

Windows 実機なしで検証できるよう、判定ロジックは純粋関数に分けてある。
"""

from datetime import datetime

import pytest

from lpwatch.collect import livepocket
from lpwatch.local import apply as apply_mod
from lpwatch.model import JST

REF = datetime(2026, 9, 21, 12, 0, tzinfo=JST)


# --- HTML からテキストへ ---------------------------------------------------

def test_block_elements_become_newlines():
    """改行を潰すと、日付の正規表現が隣の見出しの数値を巻き込む。"""
    text = livepocket.to_text("<p>受付期間</p><p>10/1 12:00〜10/5 23:59</p><p>2枚まで</p>")
    assert "受付期間\n10/1 12:00〜10/5 23:59\n2枚まで" == text


def test_script_and_style_are_dropped():
    markup = "<style>.a{color:red}</style><script>var x=1;</script><p>本文</p>"
    assert livepocket.to_text(markup) == "本文"


def test_entities_are_unescaped():
    assert livepocket.to_text("<p>A&amp;B</p>") == "A&B"


# --- ページの解析 ----------------------------------------------------------

PAGE = """<html><head><title>秋のワンマン2026 | LivePocket-Ticket-</title></head>
<body>
<h1>秋のワンマン2026</h1>
<p>先行抽選受付</p>
<p>受付期間: 2026年10月1日(木) 12:00 〜 2026年10月5日(月) 23:59</p>
<a href="https://t.livepocket.jp/e/aki2026">申し込む</a>
</body></html>"""


def test_parse_page_reads_period_and_type():
    r = livepocket.parse_page(PAGE, "https://t.livepocket.jp/e/aki2026", ref=REF)
    assert r.event_id == "aki2026"
    assert r.reception_type == "lottery"
    assert r.opens_at == "2026-10-01T12:00:00+09:00"
    assert r.closes_at == "2026-10-05T23:59:00+09:00"


def test_parse_page_without_self_link_still_works():
    """ページ本文に自分のURLが書かれていないことがある。"""
    page = PAGE.replace('href="https://t.livepocket.jp/e/aki2026"', 'href="/e/aki2026"')
    r = livepocket.parse_page(page, "https://t.livepocket.jp/e/aki2026", ref=REF)
    assert r is not None
    assert r.event_id == "aki2026"
    assert r.closes_at == "2026-10-05T23:59:00+09:00"
    assert r.title == "秋のワンマン2026"


def test_page_title_strips_site_name():
    page = PAGE.replace('href="https://t.livepocket.jp/e/aki2026"', 'href="/e/aki2026"')
    r = livepocket.parse_page(page, "https://t.livepocket.jp/e/aki2026", ref=REF)
    assert "LivePocket" not in r.title


def test_parse_page_returns_none_when_nothing_readable():
    r = livepocket.parse_page("<html><body>準備中</body></html>", "https://t.livepocket.jp/e/x", ref=REF)
    assert r is None


# --- CAPTCHA 検出 ----------------------------------------------------------

@pytest.mark.parametrize(
    "markup",
    [
        '<div class="g-recaptcha"></div>',
        "<div>画像認証を完了してください</div>",
        "<title>LivePocket-Ticket- 不正なリクエスト</title>",
        '<div class="cf-turnstile"></div>',
    ],
)
def test_captcha_is_detected(markup):
    assert apply_mod.detect_captcha(markup)


def test_normal_page_is_not_captcha():
    assert not apply_mod.detect_captcha(PAGE)


# --- 金額と上限 ------------------------------------------------------------

def test_parse_price_takes_the_largest():
    """「1枚 5,000円」と「合計 10,000円」が並ぶ。上限判定は合計で行う。"""
    assert apply_mod.parse_price("チケット 1枚 5,000円 / 合計 10,000円") == 10000


def test_parse_price_returns_none_when_absent():
    assert apply_mod.parse_price("受付期間は10月5日まで") is None


def test_budget_blocks_over_limit():
    ok, detail = apply_mod.check_budget(12000, 10000)
    assert not ok
    assert "12,000" in detail


def test_budget_allows_within_limit():
    ok, _ = apply_mod.check_budget(8000, 10000)
    assert ok


def test_budget_blocks_when_price_unreadable_but_limit_set():
    """上限を設定しているのに金額が読めない = 較正が合っていない。通すと上限が無意味になる。"""
    ok, detail = apply_mod.check_budget(None, 10000)
    assert not ok
    assert "読み取れない" in detail


def test_budget_allows_unreadable_price_when_no_limit():
    ok, _ = apply_mod.check_budget(None, 0)
    assert ok


# --- 結果から印への変換 ----------------------------------------------------

@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("applied", "applied"),
        ("captcha", "failed"),
        ("closed", "failed"),
        ("over_budget", "failed"),
        ("failed", "failed"),
    ],
)
def test_only_success_marks_applied(outcome, expected):
    """成功以外を failed にして、自動再試行させない。"""
    assert apply_mod.ApplyResult(outcome, "").mark == expected


def test_selectors_fall_back_to_defaults(tmp_path):
    assert apply_mod.load_selectors(tmp_path) == apply_mod.DEFAULT_SELECTORS


def test_selectors_override_merges_over_defaults(tmp_path):
    (tmp_path / "selectors.json").write_text(
        '{"submit_button": "#go", "_comment": "メモ"}', encoding="utf-8"
    )
    sel = apply_mod.load_selectors(tmp_path)
    assert sel["submit_button"] == "#go"
    assert sel["entry_button"] == apply_mod.DEFAULT_SELECTORS["entry_button"]
