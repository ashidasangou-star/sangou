"""期待値の解析計算。

モンテカルロと違い、こちらは厳密解。線形性より
    EV(BOX) = Σ_レアリティ (1BOXの期待枚数 × そのレアリティの平均相場) + バルク
となる。枚数と単価が独立でなくても期待値の線形性は成り立つため、
枠が「1枚 or 2枚」のようにブレても平均枚数だけ分かれば十分。
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import BoxSet


@dataclass(frozen=True)
class RarityBreakdown:
    rarity: str
    kinds: int
    expected_cards: float
    mean_price: float
    expected_value: float


@dataclass(frozen=True)
class EVResult:
    mode: str
    breakdown: tuple[RarityBreakdown, ...]
    bulk: float
    ev_box: float
    ev_pack: float
    box_price: float
    packs_per_box: int

    @property
    def roi(self) -> float:
        """還元率。1.0を超えると理論上プラス。"""
        return self.ev_box / self.box_price if self.box_price else float("inf")

    @property
    def profit(self) -> float:
        return self.ev_box - self.box_price


def compute_ev(box: BoxSet, mode: str) -> EVResult:
    rows = []
    for rarity in box.rarities():
        expected_cards = box.expected_count(rarity)
        mean_price = box.mean_price(rarity, mode)
        rows.append(
            RarityBreakdown(
                rarity=rarity,
                kinds=box.kinds(rarity),
                expected_cards=expected_cards,
                mean_price=mean_price,
                expected_value=expected_cards * mean_price,
            )
        )

    rows.sort(key=lambda r: r.expected_value, reverse=True)
    bulk = box.bulk(mode)
    ev_box = sum(r.expected_value for r in rows) + bulk

    return EVResult(
        mode=mode,
        breakdown=tuple(rows),
        bulk=bulk,
        ev_box=ev_box,
        ev_pack=ev_box / box.packs_per_box,
        box_price=box.box_price(mode),
        packs_per_box=box.packs_per_box,
    )
