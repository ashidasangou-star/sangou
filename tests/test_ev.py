import json
from pathlib import Path

import pytest

from pokebox_ev import DataError, compute_ev, load_set, simulate

ROOT = Path(__file__).resolve().parents[1]
ABYSSEYE = ROOT / "data" / "abysseye_m5.json"


# 手計算しやすい最小構成。SR枠は常に1枚、SR(2種)と当たり(1種)に分岐する。
TOY = {
    "set_name": "テストパック",
    "packs_per_box": 10,
    "cards_per_pack": 5,
    "box_price_jpy": 1000,
    "bulk_per_box_jpy": 50,
    "slots": [
        {
            "name": "SR枠",
            "count": {"type": "fixed", "n": 1},
            "outcomes": [{"rarity": "HIT", "p": 0.1}, {"rarity": "SR", "p": 0.9}],
        },
        {
            "name": "AR枠",
            "count": {"type": "discrete", "values": {"1": 0.5, "3": 0.5}},
            "outcomes": [{"rarity": "AR", "p": 1.0}],
        },
    ],
    "cards": {
        "HIT": [{"name": "当たり", "count": 1, "price": 10000}],
        "SR": [{"name": "SR-A", "count": 1, "price": 300}, {"name": "SR-B", "count": 1, "price": 100}],
        "AR": [{"name": "AR各種", "count": 4, "price": 200}],
    },
}


@pytest.fixture
def toy(tmp_path: Path) -> Path:
    p = tmp_path / "toy.json"
    p.write_text(json.dumps(TOY), encoding="utf-8")
    return p


def test_expected_counts(toy: Path) -> None:
    box = load_set(toy)
    assert box.expected_count("HIT") == pytest.approx(0.1)
    assert box.expected_count("SR") == pytest.approx(0.9)
    # 1枚50% / 3枚50% → 平均2枚
    assert box.expected_count("AR") == pytest.approx(2.0)


def test_mean_price_is_weighted_by_kinds(toy: Path) -> None:
    box = load_set(toy)
    assert box.mean_price("SR", "kaitori") == pytest.approx(200.0)
    assert box.kinds("AR") == 4
    assert box.mean_price("AR", "kaitori") == pytest.approx(200.0)


def test_ev_matches_hand_calculation(toy: Path) -> None:
    box = load_set(toy)
    res = compute_ev(box, "kaitori")
    # 0.1*10000 + 0.9*200 + 2.0*200 + バルク50 = 1000 + 180 + 400 + 50
    assert res.ev_box == pytest.approx(1630.0)
    assert res.ev_pack == pytest.approx(163.0)
    assert res.profit == pytest.approx(630.0)
    assert res.roi == pytest.approx(1.63)


def test_breakdown_is_sorted_by_contribution(toy: Path) -> None:
    res = compute_ev(load_set(toy), "kaitori")
    values = [r.expected_value for r in res.breakdown]
    assert values == sorted(values, reverse=True)


def test_scalar_price_applies_to_both_modes(toy: Path) -> None:
    box = load_set(toy)
    assert compute_ev(box, "kaitori").ev_box == pytest.approx(compute_ev(box, "hanbai").ev_box)


def test_simulation_mean_converges_to_analytic_ev(toy: Path) -> None:
    box = load_set(toy)
    ev = compute_ev(box, "kaitori").ev_box
    sim = simulate(box, "kaitori", trials=100_000, seed=1)
    # 当たりが期待値の6割を占めるので分散が大きい。5%の幅で判定する。
    assert sim.mean == pytest.approx(ev, rel=0.05)


def test_simulation_median_is_far_below_mean(toy: Path) -> None:
    """低確率の高額カードが平均を吊り上げる構造を検証する。"""
    sim = simulate(load_set(toy), "kaitori", trials=50_000, seed=2)
    assert sim.median < sim.mean / 2


def test_simulation_is_deterministic_for_a_seed(toy: Path) -> None:
    box = load_set(toy)
    a = simulate(box, "kaitori", trials=5_000, seed=7)
    b = simulate(box, "kaitori", trials=5_000, seed=7)
    assert a.mean == b.mean
    assert a.percentiles == b.percentiles


def test_hit_probability_matches_slot_rate(toy: Path) -> None:
    sim = simulate(load_set(toy), "kaitori", trials=200_000, seed=3)
    assert sim.prob_by_rarity["HIT"] == pytest.approx(0.1, abs=0.005)


def _broken(tmp_path: Path, mutate) -> Path:
    data = json.loads(json.dumps(TOY))
    mutate(data)
    p = tmp_path / "broken.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_rejects_outcomes_that_do_not_sum_to_one(tmp_path: Path) -> None:
    def mutate(d):
        d["slots"][0]["outcomes"][0]["p"] = 0.5

    with pytest.raises(DataError, match="確率合計"):
        load_set(_broken(tmp_path, mutate))


def test_rejects_count_distribution_that_does_not_sum_to_one(tmp_path: Path) -> None:
    def mutate(d):
        d["slots"][1]["count"]["values"]["3"] = 0.9

    with pytest.raises(DataError, match="確率合計"):
        load_set(_broken(tmp_path, mutate))


def test_rejects_slot_referencing_unknown_rarity(tmp_path: Path) -> None:
    def mutate(d):
        d["slots"][0]["outcomes"][0]["rarity"] = "UR"

    with pytest.raises(DataError, match="cards に定義されていません"):
        load_set(_broken(tmp_path, mutate))


def test_rejects_missing_required_key(tmp_path: Path) -> None:
    def mutate(d):
        del d["packs_per_box"]

    with pytest.raises(DataError, match="packs_per_box"):
        load_set(_broken(tmp_path, mutate))


# ---- 同梱データセット -------------------------------------------------


def test_bundled_dataset_loads() -> None:
    box = load_set(ABYSSEYE)
    assert box.packs_per_box == 30
    assert box.kinds("SAR") == 6
    assert box.kinds("SR") == 18
    assert box.kinds("AR") == 12
    assert box.kinds("RR") == 8
    assert box.kinds("MUR") == 1


def test_bundled_dataset_pull_rates_match_reported_figures() -> None:
    box = load_set(ABYSSEYE)
    assert box.expected_count("AR") == pytest.approx(3.0)
    assert box.expected_count("RR") == pytest.approx(4.0)
    # MUR は 50BOXに1枚。
    assert box.expected_count("MUR") == pytest.approx(0.02, abs=0.001)
    # SAR は狙いの1種が約4.7% × 6種。
    assert box.expected_count("SAR") == pytest.approx(0.282, abs=0.002)
    # SR以上枠の合計は1.35枚。
    total = sum(box.expected_count(r) for r in ("MUR", "SAR", "SR"))
    assert total == pytest.approx(1.35, abs=0.001)


def test_bundled_dataset_is_negative_ev_in_both_modes() -> None:
    """定価割れではなく、BOX相場が期待値を上回っていることの回帰テスト。"""
    box = load_set(ABYSSEYE)
    for mode in ("kaitori", "hanbai"):
        assert compute_ev(box, mode).roi < 1.0
