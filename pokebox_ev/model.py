"""ポケモンカードBOXの構成（封入率・収録カード・相場）を表すデータモデル。

JSONで記述されたセット定義を読み込み、整合性を検証する。
封入率の一次資料が存在しない（メーカー非公表）ため、すべての数値は
「開封報告ベースの推定値」である点に注意。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 価格モード。kaitori = 買取価格（ショップに売る額）、hanbai = 販売価格（市場で買う額）。
PRICE_MODES = ("kaitori", "hanbai")

# 確率の合計を検証するときの許容誤差。
_TOLERANCE = 1e-6


class DataError(ValueError):
    """セット定義JSONが不正なときに送出する。"""


@dataclass(frozen=True)
class CardGroup:
    """同一レアリティ内で同じ相場として扱うカードのまとまり。

    個別に相場が判明しているカードは count=1 で1件ずつ、相場不明のカードは
    「その他N種（推定）」としてまとめて count=N で持たせる。
    """

    name: str
    count: int
    prices: dict[str, float]
    estimated: bool = False
    source: str = ""

    def price(self, mode: str) -> float:
        return self.prices[mode]


@dataclass(frozen=True)
class Outcome:
    """1枠を引いたときに、そのレアリティが出る条件付き確率。"""

    rarity: str
    p: float


@dataclass(frozen=True)
class CountDist:
    """1BOXにその枠が何回出るかの分布。

    kind="fixed"     : 常に n 回（例: AR枠は必ず3枚）
    kind="bernoulli" : 確率 p で1回、それ以外は0回
    kind="discrete"  : {回数: 確率} の任意の分布（例: SR以上枠は1枚65% / 2枚35%）
    """

    kind: str
    params: dict[str, Any]

    @property
    def mean(self) -> float:
        if self.kind == "fixed":
            return float(self.params["n"])
        if self.kind == "bernoulli":
            return float(self.params["p"])
        return sum(int(k) * v for k, v in self.params["values"].items())

    def support(self) -> dict[int, float]:
        """{回数: 確率} 形式に正規化して返す。"""
        if self.kind == "fixed":
            return {int(self.params["n"]): 1.0}
        if self.kind == "bernoulli":
            p = float(self.params["p"])
            return {0: 1.0 - p, 1: p}
        return {int(k): float(v) for k, v in self.params["values"].items()}


@dataclass(frozen=True)
class Slot:
    """1BOXあたりの「当たり枠」。

    例: 「SR以上枠」は1BOXに1〜2回出現し、その中身はMUR/SAR/SRに分岐する。
    """

    name: str
    count: CountDist
    outcomes: tuple[Outcome, ...]

    @property
    def per_box(self) -> float:
        return self.count.mean


@dataclass
class BoxSet:
    """1セット（拡張パック）の定義一式。"""

    set_name: str
    set_code: str
    release_date: str
    packs_per_box: int
    cards_per_pack: int
    msrp_box_jpy: float
    box_price_jpy: dict[str, float]
    bulk_per_box_jpy: dict[str, float]
    slots: tuple[Slot, ...]
    cards: dict[str, tuple[CardGroup, ...]]
    notes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    # ---- 派生値 -----------------------------------------------------

    def rarities(self) -> list[str]:
        return list(self.cards.keys())

    def kinds(self, rarity: str) -> int:
        """そのレアリティの収録種類数。"""
        return sum(g.count for g in self.cards[rarity])

    def mean_price(self, rarity: str, mode: str) -> float:
        """レアリティ内で各カードが等確率に出ると仮定したときの平均相場。"""
        groups = self.cards[rarity]
        total_kinds = sum(g.count for g in groups)
        if total_kinds == 0:
            return 0.0
        return sum(g.count * g.price(mode) for g in groups) / total_kinds

    def expected_count(self, rarity: str) -> float:
        """1BOXあたり、そのレアリティが出る期待枚数。"""
        return sum(
            slot.per_box * o.p for slot in self.slots for o in slot.outcomes if o.rarity == rarity
        )

    def box_price(self, mode: str) -> float:
        return self.box_price_jpy[mode]

    def bulk(self, mode: str) -> float:
        return self.bulk_per_box_jpy.get(mode, 0.0)


# ---- 読み込み --------------------------------------------------------


def _require(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d:
        raise DataError(f"{where}: 必須キー '{key}' がありません")
    return d[key]


def _parse_prices(raw: Any, where: str) -> dict[str, float]:
    if isinstance(raw, (int, float)):
        # 単一の数値なら両モード共通の相場とみなす。
        return {mode: float(raw) for mode in PRICE_MODES}
    if not isinstance(raw, dict):
        raise DataError(f"{where}: 価格は数値かモード別オブジェクトで指定してください")
    missing = [m for m in PRICE_MODES if m not in raw]
    if missing:
        raise DataError(f"{where}: 価格モード {missing} が欠けています")
    return {m: float(raw[m]) for m in PRICE_MODES}


def _parse_count(raw: dict[str, Any], where: str) -> CountDist:
    kind = _require(raw, "type", where)
    if kind == "fixed":
        n = int(_require(raw, "n", where))
        if n < 0:
            raise DataError(f"{where}: n は0以上である必要があります")
        return CountDist("fixed", {"n": n})
    if kind == "bernoulli":
        p = float(_require(raw, "p", where))
        if not 0.0 <= p <= 1.0:
            raise DataError(f"{where}: p は0〜1である必要があります")
        return CountDist("bernoulli", {"p": p})
    if kind == "discrete":
        values = _require(raw, "values", where)
        parsed = {int(k): float(v) for k, v in values.items()}
        total = sum(parsed.values())
        if abs(total - 1.0) > _TOLERANCE:
            raise DataError(f"{where}: 回数分布の確率合計が {total} で1.0になりません")
        return CountDist("discrete", {"values": parsed})
    raise DataError(f"{where}: 未知の count.type '{kind}'")


def load_set(path: str | Path) -> BoxSet:
    """セット定義JSONを読み込んで検証する。"""
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)

    where = path.name

    cards: dict[str, tuple[CardGroup, ...]] = {}
    for rarity, groups in _require(raw, "cards", where).items():
        parsed_groups = []
        for i, g in enumerate(groups):
            gw = f"{where} cards.{rarity}[{i}]"
            count = int(g.get("count", 1))
            if count < 1:
                raise DataError(f"{gw}: count は1以上である必要があります")
            parsed_groups.append(
                CardGroup(
                    name=_require(g, "name", gw),
                    count=count,
                    prices=_parse_prices(_require(g, "price", gw), gw),
                    estimated=bool(g.get("estimated", False)),
                    source=g.get("source", ""),
                )
            )
        cards[rarity] = tuple(parsed_groups)

    slots = []
    for i, s in enumerate(_require(raw, "slots", where)):
        sw = f"{where} slots[{i}]"
        outcomes = tuple(
            Outcome(rarity=_require(o, "rarity", sw), p=float(_require(o, "p", sw)))
            for o in _require(s, "outcomes", sw)
        )
        total = sum(o.p for o in outcomes)
        if abs(total - 1.0) > _TOLERANCE:
            raise DataError(f"{sw}: outcomes の確率合計が {total} で1.0になりません")
        for o in outcomes:
            if o.rarity not in cards:
                raise DataError(f"{sw}: レアリティ '{o.rarity}' が cards に定義されていません")
        slots.append(
            Slot(
                name=_require(s, "name", sw),
                count=_parse_count(_require(s, "count", sw), sw),
                outcomes=outcomes,
            )
        )

    return BoxSet(
        set_name=_require(raw, "set_name", where),
        set_code=raw.get("set_code", ""),
        release_date=raw.get("release_date", ""),
        packs_per_box=int(_require(raw, "packs_per_box", where)),
        cards_per_pack=int(_require(raw, "cards_per_pack", where)),
        msrp_box_jpy=float(raw.get("msrp_box_jpy", 0)),
        box_price_jpy=_parse_prices(_require(raw, "box_price_jpy", where), f"{where} box_price_jpy"),
        bulk_per_box_jpy=_parse_prices(raw.get("bulk_per_box_jpy", 0), f"{where} bulk_per_box_jpy"),
        slots=tuple(slots),
        cards=cards,
        notes=list(raw.get("notes", [])),
        sources=list(raw.get("sources", [])),
    )
