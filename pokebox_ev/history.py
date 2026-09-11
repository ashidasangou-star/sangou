"""相場の時系列記録。

1行1観測のJSONL（追記専用）で持つ。同じカードを別の日に何度観測しても
追記するだけで、過去の記録は書き換えない。あとから出典を検証できるよう、
観測ごとに source と、その価格が「いつ時点の相場か」を残す。

観測が無い日は直近の値を持ち越す（forward fill）。相場は毎日全カード分の
情報が出るわけではないため、これがないと系列が穴だらけになる。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date as _date
from pathlib import Path

from .model import PRICE_MODES, BoxSet, CardGroup


@dataclass(frozen=True)
class Observation:
    """ある日に観測した、あるカードの相場。"""

    date: str  # 記録日 (YYYY-MM-DD)
    set_code: str
    rarity: str
    card: str
    mode: str  # kaitori / hanbai
    price: float
    source: str = ""
    quoted_at: str = ""  # その価格が出た日。記録日と違うことがある
    estimated: bool = False

    def key(self) -> tuple[str, str, str]:
        return (self.rarity, self.card, self.mode)


def history_path(root: Path | str, set_code: str) -> Path:
    return Path(root) / f"{set_code}.jsonl"


def append(path: Path | str, observations: list[Observation]) -> int:
    """観測を追記する。書き込んだ件数を返す。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for o in observations:
            fh.write(json.dumps(asdict(o), ensure_ascii=False) + "\n")
    return len(observations)


def load(path: Path | str) -> list[Observation]:
    """観測を読み込む。ファイルが無ければ空。"""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(Observation(**json.loads(line)))
    return out


def snapshot(box: BoxSet, on: str | None = None) -> list[Observation]:
    """セット定義の現在の相場を、その日の観測として書き出す形に変換する。"""
    on = on or _date.today().isoformat()
    out = []
    for rarity, groups in box.cards.items():
        for g in groups:
            for mode in PRICE_MODES:
                out.append(
                    Observation(
                        date=on,
                        set_code=box.set_code,
                        rarity=rarity,
                        card=g.name,
                        mode=mode,
                        price=g.price(mode),
                        source=g.source,
                        quoted_at=g.date,
                        estimated=g.estimated,
                    )
                )
    return out


def dates(observations: list[Observation]) -> list[str]:
    return sorted({o.date for o in observations})


def prices_on(observations: list[Observation], on: str) -> dict[tuple[str, str, str], float]:
    """指定日時点の相場。その日に観測が無いカードは直近の値を持ち越す。"""
    latest: dict[tuple[str, str, str], tuple[str, float]] = {}
    for o in observations:
        if o.date > on:
            continue
        prev = latest.get(o.key())
        # 同じ日に複数回観測されていれば、ファイル上で後に書かれたものを採る。
        if prev is None or o.date >= prev[0]:
            latest[o.key()] = (o.date, o.price)
    return {k: v[1] for k, v in latest.items()}


def with_prices(box: BoxSet, prices: dict[tuple[str, str, str], float]) -> BoxSet:
    """相場を差し替えた BoxSet を返す。封入率などの構造はそのまま。

    差し替え先に無いカードは元の値を残す。
    """
    cards = {}
    for rarity, groups in box.cards.items():
        new_groups = []
        for g in groups:
            overrides = {
                mode: prices.get((rarity, g.name, mode), g.price(mode)) for mode in PRICE_MODES
            }
            new_groups.append(
                CardGroup(
                    name=g.name,
                    count=g.count,
                    prices=overrides,
                    estimated=g.estimated,
                    source=g.source,
                    date=g.date,
                )
            )
        cards[rarity] = tuple(new_groups)

    return BoxSet(
        set_name=box.set_name,
        set_code=box.set_code,
        release_date=box.release_date,
        packs_per_box=box.packs_per_box,
        cards_per_pack=box.cards_per_pack,
        msrp_box_jpy=box.msrp_box_jpy,
        box_price_jpy=dict(box.box_price_jpy),
        bulk_per_box_jpy=dict(box.bulk_per_box_jpy),
        slots=box.slots,
        cards=cards,
        notes=box.notes,
        sources=box.sources,
    )


@dataclass(frozen=True)
class Change:
    """2時点間のカード単位の変動。"""

    rarity: str
    card: str
    old: float
    new: float

    @property
    def diff(self) -> float:
        return self.new - self.old

    @property
    def ratio(self) -> float:
        return (self.new / self.old - 1.0) if self.old else 0.0


def changes(
    observations: list[Observation], frm: str, to: str, mode: str
) -> list[Change]:
    """2時点を比較して、動いたカードだけを変動幅の大きい順に返す。"""
    a = prices_on(observations, frm)
    b = prices_on(observations, to)
    out = []
    for key, new in b.items():
        rarity, card, m = key
        if m != mode:
            continue
        old = a.get(key)
        if old is None or old == new:
            continue
        out.append(Change(rarity=rarity, card=card, old=old, new=new))
    out.sort(key=lambda c: abs(c.diff), reverse=True)
    return out
