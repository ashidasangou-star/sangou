"""BOX買取価格の時系列記録。

期待値計算（カード単位）とは別系統。買取表から読み取ったBOXの買取額を
セット×状態ごとに追記していく。

この用途で一番壊れやすいのは「同じセットが表記ゆれで別系列に割れること」
（アビスアイ / M5 / アビス など）。そのため書き込み前にエイリアス表で
正規化し、未知の名前は明示的に許可しない限り弾く。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date as _date
from pathlib import Path

# 買取表は同じセットでも状態で値段が違う。シュリンクの有無が最重要。
CONDITIONS = {
    "shrink": "シュリンク付き",
    "noshrink": "シュリンク無し",
    "carton": "カートン",
    "other": "その他",
}


class AliasError(ValueError):
    """未知のセット名が渡されたときに送出する。"""


@dataclass(frozen=True)
class BoxPrice:
    """ある日・ある店の、あるBOXの買取額。"""

    date: str  # 記録日 (YYYY-MM-DD)
    set_name: str  # 正規化済みのセット名
    condition: str  # CONDITIONS のキー
    price: float
    shop: str
    source: str = ""  # ツイートURL、または読み取った画像のファイル名
    note: str = ""

    def key(self) -> tuple[str, str, str]:
        return (self.set_name, self.condition, self.shop)


# ---- セット名の正規化 ------------------------------------------------


class Aliases:
    """表記ゆれを正規名に寄せる。"""

    def __init__(self, mapping: dict[str, list[str]], meta: dict[str, str] | None = None) -> None:
        self._canonical = set(mapping)
        self._meta = meta or {}  # "_comment" など、書き戻しで保持したいキー
        self._reverse: dict[str, str] = {}
        for canonical, variants in mapping.items():
            self._reverse[self._norm(canonical)] = canonical
            for v in variants:
                self._reverse[self._norm(v)] = canonical

    @staticmethod
    def _norm(s: str) -> str:
        return s.replace(" ", "").replace("　", "").lower()

    @classmethod
    def load(cls, path: Path | str) -> Aliases:
        path = Path(path)
        if not path.exists():
            return cls({})
        raw = json.loads(path.read_text(encoding="utf-8"))
        # "_comment" のようなメモ用キーはセット名として扱わないが、書き戻しでは残す。
        return cls(
            {k: v for k, v in raw.items() if not k.startswith("_")},
            {k: v for k, v in raw.items() if k.startswith("_")},
        )

    def canonical_names(self) -> list[str]:
        return sorted(self._canonical)

    def resolve(self, name: str, *, allow_new: bool = False) -> str:
        hit = self._reverse.get(self._norm(name))
        if hit:
            return hit
        if allow_new:
            self._canonical.add(name)
            self._reverse[self._norm(name)] = name
            return name
        raise AliasError(
            f"'{name}' は未知のセット名です。表記ゆれなら data/set_aliases.json に追加し、"
            f"新しいセットなら --new を付けてください。"
        )

    def as_mapping(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {c: [] for c in self._canonical}
        for variant, canonical in self._reverse.items():
            if self._norm(canonical) != variant:
                out.setdefault(canonical, []).append(variant)
        return {k: sorted(v) for k, v in sorted(out.items())}

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {**self._meta, **self.as_mapping()}
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


# ---- 読み書き --------------------------------------------------------


def append(path: Path | str, rows: list[BoxPrice]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    return len(rows)


def load(path: Path | str) -> list[BoxPrice]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(BoxPrice(**json.loads(line)))
    return out


def dates(rows: list[BoxPrice]) -> list[str]:
    return sorted({r.date for r in rows})


def sets(rows: list[BoxPrice]) -> list[str]:
    return sorted({r.set_name for r in rows})


def sources(rows: list[BoxPrice]) -> set[str]:
    """取り込み済みの出典。画像の二重取り込みを防ぐのに使う。"""
    return {r.source for r in rows if r.source}


def latest_on(rows: list[BoxPrice], on: str) -> dict[tuple[str, str, str], BoxPrice]:
    """指定日時点の最新値。観測が無い日は直近を持ち越す。"""
    latest: dict[tuple[str, str, str], BoxPrice] = {}
    for r in rows:
        if r.date > on:
            continue
        prev = latest.get(r.key())
        if prev is None or r.date >= prev.date:
            latest[r.key()] = r
    return latest


@dataclass(frozen=True)
class BoxChange:
    set_name: str
    condition: str
    shop: str
    old: float
    new: float
    old_date: str
    new_date: str

    @property
    def diff(self) -> float:
        return self.new - self.old

    @property
    def ratio(self) -> float:
        return (self.new / self.old - 1.0) if self.old else 0.0


def changes(rows: list[BoxPrice], frm: str, to: str) -> list[BoxChange]:
    """2時点を比較して、動いたBOXだけを変動幅の大きい順に返す。"""
    a = latest_on(rows, frm)
    b = latest_on(rows, to)
    out = []
    for key, new in b.items():
        old = a.get(key)
        if old is None or old.price == new.price:
            continue
        out.append(
            BoxChange(
                set_name=key[0],
                condition=key[1],
                shop=key[2],
                old=old.price,
                new=new.price,
                old_date=old.date,
                new_date=new.date,
            )
        )
    out.sort(key=lambda c: abs(c.diff), reverse=True)
    return out


def series(rows: list[BoxPrice], set_name: str, condition: str, shop: str) -> list[BoxPrice]:
    """1系列ぶんの推移を日付順で返す。"""
    key = (set_name, condition, shop)
    return sorted((r for r in rows if r.key() == key), key=lambda r: r.date)


def parse_price(raw: str) -> float:
    """'7,000' '7000円' '¥7000' を数値にする。買取表の表記ゆれを吸収する。"""
    cleaned = raw.replace(",", "").replace("円", "").replace("¥", "").replace("￥", "").strip()
    if not cleaned:
        raise ValueError("価格が空です")
    return float(cleaned)


def snapshot_table(rows: list[BoxPrice], on: str | None = None) -> list[BoxPrice]:
    """指定日時点の全セットの最新値を、セット名順で返す。"""
    on = on or _date.today().isoformat()
    return sorted(latest_on(rows, on).values(), key=lambda r: (r.set_name, r.condition, r.shop))
