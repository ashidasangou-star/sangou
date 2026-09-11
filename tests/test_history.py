import json
from pathlib import Path

import pytest

from pokebox_ev import compute_ev, history, load_set

ROOT = Path(__file__).resolve().parents[1]

SET = {
    "set_name": "テストパック",
    "set_code": "T1",
    "packs_per_box": 10,
    "cards_per_pack": 5,
    "box_price_jpy": 1000,
    "bulk_per_box_jpy": 0,
    "slots": [
        {
            "name": "SR枠",
            "count": {"type": "fixed", "n": 1},
            "outcomes": [{"rarity": "SAR", "p": 1.0}],
        }
    ],
    "cards": {
        "SAR": [
            {"name": "当たり", "count": 1, "price": 10000, "date": "2026-01-01", "source": "x"},
            {"name": "ハズレ", "count": 1, "price": 100, "date": "2026-01-01", "source": "x"},
        ]
    },
}


@pytest.fixture
def box(tmp_path: Path):
    p = tmp_path / "set.json"
    p.write_text(json.dumps(SET), encoding="utf-8")
    return load_set(p)


@pytest.fixture
def hist(tmp_path: Path) -> Path:
    return tmp_path / "hist" / "T1.jsonl"


def test_append_and_load_roundtrip(box, hist: Path) -> None:
    obs = history.snapshot(box, on="2026-01-01")
    assert history.append(hist, obs) == len(obs)
    loaded = history.load(hist)
    assert loaded == obs


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert history.load(tmp_path / "nope.jsonl") == []


def test_snapshot_covers_every_card_and_mode(box) -> None:
    obs = history.snapshot(box, on="2026-01-01")
    # カード2種 × モード2種
    assert len(obs) == 4
    assert {o.card for o in obs} == {"当たり", "ハズレ"}
    assert {o.mode for o in obs} == {"kaitori", "hanbai"}


def test_prices_carry_forward_when_a_day_has_no_observation(box, hist: Path) -> None:
    history.append(hist, history.snapshot(box, on="2026-01-01"))
    history.append(
        hist,
        [
            history.Observation(
                date="2026-02-01",
                set_code="T1",
                rarity="SAR",
                card="当たり",
                mode="kaitori",
                price=5000,
            )
        ],
    )
    obs = history.load(hist)
    prices = history.prices_on(obs, "2026-02-01")
    # 2/1に観測があった当たりは新しい値、観測が無いハズレは1/1の値を持ち越す。
    assert prices[("SAR", "当たり", "kaitori")] == 5000
    assert prices[("SAR", "ハズレ", "kaitori")] == 100


def test_prices_on_ignores_later_observations(box, hist: Path) -> None:
    history.append(hist, history.snapshot(box, on="2026-01-01"))
    history.append(
        hist,
        [
            history.Observation(
                date="2026-02-01",
                set_code="T1",
                rarity="SAR",
                card="当たり",
                mode="kaitori",
                price=5000,
            )
        ],
    )
    prices = history.prices_on(history.load(hist), "2026-01-15")
    assert prices[("SAR", "当たり", "kaitori")] == 10000


def test_with_prices_rebuilds_ev_without_touching_pull_rates(box, hist: Path) -> None:
    before = compute_ev(box, "kaitori").ev_box
    # 当たりが半額になれば、SARの平均相場が下がりEVも下がる。
    swapped = history.with_prices(box, {("SAR", "当たり", "kaitori"): 5000})
    after = compute_ev(swapped, "kaitori").ev_box
    assert after < before
    # 封入率は不変。
    assert swapped.expected_count("SAR") == box.expected_count("SAR")
    assert swapped.kinds("SAR") == box.kinds("SAR")


def test_with_prices_keeps_untouched_cards(box) -> None:
    swapped = history.with_prices(box, {("SAR", "当たり", "kaitori"): 5000})
    hazure = next(g for g in swapped.cards["SAR"] if g.name == "ハズレ")
    assert hazure.price("kaitori") == 100
    assert hazure.price("hanbai") == 100


def test_changes_reports_only_moved_cards(box, hist: Path) -> None:
    history.append(hist, history.snapshot(box, on="2026-01-01"))
    history.append(
        hist,
        [
            history.Observation(
                date="2026-02-01",
                set_code="T1",
                rarity="SAR",
                card="当たり",
                mode="kaitori",
                price=5000,
            )
        ],
    )
    rows = history.changes(history.load(hist), "2026-01-01", "2026-02-01", "kaitori")
    assert len(rows) == 1
    c = rows[0]
    assert (c.card, c.old, c.new) == ("当たり", 10000, 5000)
    assert c.diff == -5000
    assert c.ratio == pytest.approx(-0.5)


def test_changes_sorted_by_absolute_move(box, hist: Path) -> None:
    history.append(hist, history.snapshot(box, on="2026-01-01"))
    history.append(
        hist,
        [
            history.Observation(
                date="2026-02-01", set_code="T1", rarity="SAR", card="当たり",
                mode="kaitori", price=9000,
            ),
            history.Observation(
                date="2026-02-01", set_code="T1", rarity="SAR", card="ハズレ",
                mode="kaitori", price=5000,
            ),
        ],
    )
    rows = history.changes(history.load(hist), "2026-01-01", "2026-02-01", "kaitori")
    # ハズレの+4,900円のほうが、当たりの-1,000円より動きが大きい。
    assert [r.card for r in rows] == ["ハズレ", "当たり"]


def test_dates_are_sorted_and_deduplicated(box, hist: Path) -> None:
    history.append(hist, history.snapshot(box, on="2026-02-01"))
    history.append(hist, history.snapshot(box, on="2026-01-01"))
    assert history.dates(history.load(hist)) == ["2026-01-01", "2026-02-01"]


# ---- 同梱の履歴 -------------------------------------------------------


def test_bundled_history_reproduces_the_recorded_decline() -> None:
    """7月→9月でBOX期待値が下がっている記録が残っていること。"""
    box = load_set(ROOT / "data" / "abysseye_m5.json")
    obs = history.load(ROOT / "data" / "history" / "M5.jsonl")
    assert len(history.dates(obs)) >= 2

    first, last = history.dates(obs)[0], history.dates(obs)[-1]
    ev_first = compute_ev(history.with_prices(box, history.prices_on(obs, first)), "kaitori").ev_box
    ev_last = compute_ev(history.with_prices(box, history.prices_on(obs, last)), "kaitori").ev_box
    assert ev_last < ev_first
