"""コマンドラインインターフェース。"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from .ev import EVResult, compute_ev
from .model import PRICE_MODES, BoxSet, DataError, load_set
from .simulate import SimResult, simulate

_MODE_LABEL = {"kaitori": "買取価格ベース", "hanbai": "販売価格ベース"}


def _yen(v: float) -> str:
    return f"{v:,.0f}円"


def _width(s: str) -> int:
    """端末上の表示幅。全角文字を2桁として数える。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _ljust(s: str, n: int) -> str:
    return s + " " * max(0, n - _width(s))


def _rjust(s: str, n: int) -> str:
    return " " * max(0, n - _width(s)) + s


def _print_header(box: BoxSet) -> None:
    print(f"■ {box.set_name}  [{box.set_code}]  {box.release_date}")
    print(f"  1BOX = {box.packs_per_box}パック × {box.cards_per_pack}枚 / 定価 {_yen(box.msrp_box_jpy)}")
    kinds = "  ".join(f"{r} {box.kinds(r)}種" for r in box.rarities())
    print(f"  収録: {kinds}")
    print()
    print("  封入率（1BOXあたりの期待枚数）")
    for slot in box.slots:
        inner = " / ".join(f"{o.rarity} {o.p:.1%}" for o in slot.outcomes)
        print(f"    {_ljust(slot.name, 12)}{slot.per_box:>5.2f}枚   内訳: {inner}")
    print()


# レアリティ / 種類 / 期待枚数 / 平均相場 / 期待値
_COLS = (12, 6, 10, 12, 12)
_TABLE_WIDTH = sum(_COLS)


def _row(cells: tuple[str, str, str, str, str]) -> str:
    head, *rest = cells
    return _ljust(head, _COLS[0]) + "".join(_rjust(c, w) for c, w in zip(rest, _COLS[1:]))


def _print_ev(box: BoxSet, res: EVResult) -> None:
    print(f"── {_MODE_LABEL[res.mode]} ──")
    print(_row(("レアリティ", "種類", "期待枚数", "平均相場", "期待値")))
    for r in res.breakdown:
        print(
            _row(
                (
                    r.rarity,
                    str(r.kinds),
                    f"{r.expected_cards:.3f}",
                    _yen(r.mean_price),
                    _yen(r.expected_value),
                )
            )
        )
    print(_row(("バルク", "", "", "", _yen(res.bulk))))
    print("-" * _TABLE_WIDTH)
    print(_row(("BOX期待値", "", "", "", _yen(res.ev_box))))
    print(_row(("1パック期待値", "", "", "", _yen(res.ev_pack))))
    print()
    print(f"  BOX価格   {_yen(res.box_price)}")
    print(f"  収支      {_yen(res.profit)}  （還元率 {res.roi:.1%}）")
    print()


def _print_sim(box: BoxSet, sim: SimResult) -> None:
    print(f"  開封シミュレーション（{sim.trials:,}BOX）")
    print(f"    平均       {_yen(sim.mean)}")
    print(f"    中央値     {_yen(sim.median)}   収支 {_yen(sim.profit_median)}")
    pcts = "  ".join(f"P{q}={_yen(sim.percentiles[q])}" for q in (5, 25, 50, 75, 95))
    print(f"    分位点     {pcts}")
    print(f"    元が取れる確率  {sim.prob_break_even:.1%}")
    hit = "  ".join(f"{r} {p:.2%}" for r, p in sim.prob_by_rarity.items() if p < 0.999)
    if hit:
        print(f"    1BOXで1枚以上引ける確率  {hit}")
    print()


def _as_dict(box: BoxSet, res: EVResult, sim: SimResult | None) -> dict:
    out = {
        "set_name": box.set_name,
        "set_code": box.set_code,
        "mode": res.mode,
        "packs_per_box": box.packs_per_box,
        "box_price": res.box_price,
        "ev_box": res.ev_box,
        "ev_pack": res.ev_pack,
        "profit": res.profit,
        "roi": res.roi,
        "breakdown": [
            {
                "rarity": r.rarity,
                "kinds": r.kinds,
                "expected_cards": r.expected_cards,
                "mean_price": r.mean_price,
                "expected_value": r.expected_value,
            }
            for r in res.breakdown
        ],
        "bulk": res.bulk,
    }
    if sim is not None:
        out["simulation"] = {
            "trials": sim.trials,
            "mean": sim.mean,
            "median": sim.median,
            "percentiles": sim.percentiles,
            "prob_break_even": sim.prob_break_even,
            "prob_by_rarity": sim.prob_by_rarity,
            "max_value": sim.max_value,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pokebox-ev",
        description="ポケモンカードBOXの開封期待値を計算する",
    )
    parser.add_argument("dataset", type=Path, help="セット定義JSONのパス")
    parser.add_argument(
        "--mode",
        choices=[*PRICE_MODES, "both"],
        default="both",
        help="kaitori=買取価格ベース / hanbai=販売価格ベース / both=両方（既定）",
    )
    parser.add_argument("--box-price", type=float, help="BOX価格を上書きする")
    parser.add_argument("--trials", type=int, default=200_000, help="シミュレーション回数（0で省略）")
    parser.add_argument("--seed", type=int, default=0, help="乱数シード")
    parser.add_argument("--json", action="store_true", help="結果をJSONで出力する")
    args = parser.parse_args(argv)

    try:
        box = load_set(args.dataset)
    except (OSError, json.JSONDecodeError, DataError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    if args.box_price is not None:
        box.box_price_jpy = {m: args.box_price for m in PRICE_MODES}

    modes = list(PRICE_MODES) if args.mode == "both" else [args.mode]
    payload = []

    if not args.json:
        _print_header(box)

    for mode in modes:
        res = compute_ev(box, mode)
        sim = simulate(box, mode, trials=args.trials, seed=args.seed) if args.trials > 0 else None
        if args.json:
            payload.append(_as_dict(box, res, sim))
        else:
            _print_ev(box, res)
            if sim is not None:
                _print_sim(box, sim)

    if args.json:
        print(json.dumps(payload if len(payload) > 1 else payload[0], ensure_ascii=False, indent=2))
    elif box.notes:
        print("── 前提と注意 ──")
        for note in box.notes:
            print(f"  ・{note}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
