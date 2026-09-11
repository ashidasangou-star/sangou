"""モンテカルロによる開封シミュレーション。

期待値だけ見ると誤解する。BOXの価値は MUR / 高額SAR という極端に低確率の
カードが引き上げているため、分布は強く右に歪む。
「平均」ではなく「中央値」と「元が取れる確率」を見るためにこれを使う。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .model import BoxSet


@dataclass(frozen=True)
class SimResult:
    mode: str
    trials: int
    box_price: float
    mean: float
    median: float
    percentiles: dict[int, float]
    prob_break_even: float
    prob_by_rarity: dict[str, float]
    max_value: float

    @property
    def profit_median(self) -> float:
        return self.median - self.box_price


class _RarityDraw:
    """あるレアリティから1枚引いたときの相場を返すサンプラー。

    レアリティ内では各種類が等確率に出ると仮定する（種類ごとの封入差は
    公表されていないため）。相場不明分をまとめたグループは count 種ぶんの
    重みを持つ。
    """

    def __init__(self, box: BoxSet, rarity: str, mode: str) -> None:
        groups = box.cards[rarity]
        self._prices = [g.price(mode) for g in groups]
        self._weights = [float(g.count) for g in groups]

    def draw(self, rng: random.Random) -> float:
        return rng.choices(self._prices, weights=self._weights, k=1)[0]


def simulate(box: BoxSet, mode: str, trials: int = 200_000, seed: int | None = 0) -> SimResult:
    rng = random.Random(seed)

    # サンプラーと分布をループ外で作っておく。
    draws = {r: _RarityDraw(box, r, mode) for r in box.rarities()}
    bulk = box.bulk(mode)

    slot_plans = []
    for slot in box.slots:
        support = slot.count.support()
        counts = list(support.keys())
        count_weights = list(support.values())
        rarities = [o.rarity for o in slot.outcomes]
        rarity_weights = [o.p for o in slot.outcomes]
        slot_plans.append((counts, count_weights, rarities, rarity_weights))

    values: list[float] = []
    hits: dict[str, int] = {r: 0 for r in box.rarities()}

    for _ in range(trials):
        total = bulk
        seen: set[str] = set()
        for counts, count_weights, rarities, rarity_weights in slot_plans:
            n = rng.choices(counts, weights=count_weights, k=1)[0]
            for _ in range(n):
                rarity = rng.choices(rarities, weights=rarity_weights, k=1)[0]
                total += draws[rarity].draw(rng)
                seen.add(rarity)
        for rarity in seen:
            hits[rarity] += 1
        values.append(total)

    values.sort()
    box_price = box.box_price(mode)

    def pct(q: int) -> float:
        idx = min(len(values) - 1, max(0, int(round(q / 100 * (len(values) - 1)))))
        return values[idx]

    # values はソート済みなので、二分探索的に数えるより単純な線形カウントで十分。
    break_even = sum(1 for v in values if v >= box_price)

    return SimResult(
        mode=mode,
        trials=trials,
        box_price=box_price,
        mean=sum(values) / len(values),
        median=pct(50),
        percentiles={q: pct(q) for q in (1, 5, 10, 25, 50, 75, 90, 95, 99)},
        prob_break_even=break_even / len(values),
        prob_by_rarity={r: hits[r] / trials for r in box.rarities()},
        max_value=values[-1],
    )
