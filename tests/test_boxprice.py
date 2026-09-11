import json
from pathlib import Path

import pytest

from pokebox_ev import boxprice
from pokebox_ev.boxprice import AliasError, Aliases, BoxPrice

ROOT = Path(__file__).resolve().parents[1]


def obs(date: str, set_name: str, price: float, *, condition="shrink", shop="トレカマサイ", source=""):
    return BoxPrice(
        date=date, set_name=set_name, condition=condition, price=price, shop=shop, source=source
    )


# ---- セット名の正規化 ------------------------------------------------


@pytest.fixture
def aliases() -> Aliases:
    return Aliases({"アビスアイ": ["M5", "アビス"], "ホワイトフレア": ["白フレア"]})


def test_alias_resolves_variants(aliases: Aliases) -> None:
    assert aliases.resolve("M5") == "アビスアイ"
    assert aliases.resolve("アビス") == "アビスアイ"
    assert aliases.resolve("アビスアイ") == "アビスアイ"


def test_alias_ignores_spacing_and_case(aliases: Aliases) -> None:
    assert aliases.resolve(" 白フレア ") == "ホワイトフレア"
    assert aliases.resolve("m5") == "アビスアイ"


def test_alias_rejects_unknown_name(aliases: Aliases) -> None:
    """表記ゆれで系列が割れるのを防ぐ。未知の名前は黙って通さない。"""
    with pytest.raises(AliasError, match="未知のセット名"):
        aliases.resolve("アビスアイex")


def test_alias_accepts_new_name_when_allowed(aliases: Aliases) -> None:
    assert aliases.resolve("30th CELEBRATION", allow_new=True) == "30th CELEBRATION"
    # 一度登録すれば以降は許可なしでも通る。
    assert aliases.resolve("30th CELEBRATION") == "30th CELEBRATION"


def test_alias_roundtrip_preserves_comment_keys(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"_comment": "メモ", "アビスアイ": ["M5"]}), encoding="utf-8")
    a = Aliases.load(p)
    assert "_comment" not in a.canonical_names()
    a.resolve("新弾", allow_new=True)
    a.save(p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["_comment"] == "メモ"
    assert "新弾" in raw
    assert raw["アビスアイ"] == ["m5"]


# ---- 価格のパース ----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("7000", 7000), ("7,000", 7000), ("7000円", 7000), ("¥7000", 7000), ("￥7,000", 7000)],
)
def test_parse_price_handles_buylist_notation(raw: str, expected: float) -> None:
    assert boxprice.parse_price(raw) == expected


def test_parse_price_rejects_empty() -> None:
    with pytest.raises(ValueError):
        boxprice.parse_price("円")


# ---- 読み書きと集計 --------------------------------------------------


def test_append_and_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "box.jsonl"
    rows = [obs("2026-01-01", "アビスアイ", 7000)]
    assert boxprice.append(p, rows) == 1
    assert boxprice.load(p) == rows


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert boxprice.load(tmp_path / "nope.jsonl") == []


def test_latest_carries_forward(tmp_path: Path) -> None:
    p = tmp_path / "box.jsonl"
    boxprice.append(p, [obs("2026-01-01", "アビスアイ", 7000), obs("2026-01-01", "白", 5000)])
    boxprice.append(p, [obs("2026-02-01", "アビスアイ", 6000)])
    latest = boxprice.latest_on(boxprice.load(p), "2026-02-01")
    assert latest[("アビスアイ", "shrink", "トレカマサイ")].price == 6000
    # 2/1に観測が無い「白」は1/1の値を持ち越す。
    assert latest[("白", "shrink", "トレカマサイ")].price == 5000


def test_latest_ignores_future_observations(tmp_path: Path) -> None:
    p = tmp_path / "box.jsonl"
    boxprice.append(p, [obs("2026-01-01", "アビスアイ", 7000), obs("2026-02-01", "アビスアイ", 6000)])
    latest = boxprice.latest_on(boxprice.load(p), "2026-01-15")
    assert latest[("アビスアイ", "shrink", "トレカマサイ")].price == 7000


def test_shrink_and_noshrink_are_separate_series(tmp_path: Path) -> None:
    p = tmp_path / "box.jsonl"
    boxprice.append(
        p,
        [
            obs("2026-01-01", "アビスアイ", 7000, condition="shrink"),
            obs("2026-01-01", "アビスアイ", 6300, condition="noshrink"),
        ],
    )
    latest = boxprice.latest_on(boxprice.load(p), "2026-01-01")
    assert latest[("アビスアイ", "shrink", "トレカマサイ")].price == 7000
    assert latest[("アビスアイ", "noshrink", "トレカマサイ")].price == 6300


def test_same_set_at_different_shops_is_separate(tmp_path: Path) -> None:
    p = tmp_path / "box.jsonl"
    boxprice.append(
        p,
        [
            obs("2026-01-01", "アビスアイ", 7000, shop="トレカマサイ"),
            obs("2026-01-01", "アビスアイ", 6800, shop="カードラッシュ"),
        ],
    )
    assert len(boxprice.latest_on(boxprice.load(p), "2026-01-01")) == 2


def test_changes_reports_only_moved_boxes(tmp_path: Path) -> None:
    p = tmp_path / "box.jsonl"
    boxprice.append(p, [obs("2026-01-01", "アビスアイ", 7000), obs("2026-01-01", "白", 5000)])
    boxprice.append(p, [obs("2026-02-01", "アビスアイ", 6000), obs("2026-02-01", "白", 5000)])
    rows = boxprice.changes(boxprice.load(p), "2026-01-01", "2026-02-01")
    assert len(rows) == 1
    c = rows[0]
    assert (c.set_name, c.old, c.new, c.diff) == ("アビスアイ", 7000, 6000, -1000)
    assert c.ratio == pytest.approx(-1 / 7)


def test_changes_sorted_by_absolute_move(tmp_path: Path) -> None:
    p = tmp_path / "box.jsonl"
    boxprice.append(p, [obs("2026-01-01", "A", 7000), obs("2026-01-01", "B", 5000)])
    boxprice.append(p, [obs("2026-02-01", "A", 6500), obs("2026-02-01", "B", 9000)])
    assert [c.set_name for c in boxprice.changes(boxprice.load(p), "2026-01-01", "2026-02-01")] == [
        "B",
        "A",
    ]


def test_sources_tracks_ingested_images(tmp_path: Path) -> None:
    p = tmp_path / "box.jsonl"
    boxprice.append(
        p,
        [
            obs("2026-01-01", "アビスアイ", 7000, source="2026-01-01_ttt.jpg"),
            obs("2026-01-01", "白", 5000, source=""),
        ],
    )
    assert boxprice.sources(boxprice.load(p)) == {"2026-01-01_ttt.jpg"}


def test_series_is_ordered_by_date(tmp_path: Path) -> None:
    p = tmp_path / "box.jsonl"
    boxprice.append(p, [obs("2026-02-01", "A", 6000), obs("2026-01-01", "A", 7000)])
    s = boxprice.series(boxprice.load(p), "A", "shrink", "トレカマサイ")
    assert [r.date for r in s] == ["2026-01-01", "2026-02-01"]


# ---- 同梱データ ------------------------------------------------------


def test_bundled_aliases_load_without_comment_key() -> None:
    a = Aliases.load(ROOT / "data" / "set_aliases.json")
    assert "_comment" not in a.canonical_names()
    assert a.resolve("M5") == "アビスアイ"


def test_bundled_box_prices_resolve_against_aliases() -> None:
    """記録済みのセット名が全てエイリアス表の正規名になっていること。"""
    a = Aliases.load(ROOT / "data" / "set_aliases.json")
    rows = boxprice.load(ROOT / "data" / "box_prices.jsonl")
    assert rows
    for r in rows:
        assert a.resolve(r.set_name) == r.set_name
        assert r.condition in boxprice.CONDITIONS
