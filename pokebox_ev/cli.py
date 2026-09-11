"""コマンドラインインターフェース。"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import date
from pathlib import Path

from . import boxprice, history
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


def _print_cards(box: BoxSet, mode: str) -> None:
    """カード単位の相場を出典・取得日つきで並べる。数値の監査用。"""
    print(f"── 相場の内訳（{_MODE_LABEL[mode]}）──")
    for rarity in box.rarities():
        print(f"  [{rarity}]")
        for g in box.cards[rarity]:
            kinds = f"×{g.count}" if g.count > 1 else "  "
            mark = "≈" if g.estimated else " "
            date = g.date or "-"
            print(
                f"    {mark}{_ljust(g.name, 30)}{kinds}"
                f"{_rjust(_yen(g.price(mode)), 11)}  {_ljust(date, 11)}{g.source}"
            )
    print("  ≈ は相場を取得できず推定で埋めた項目")
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


def _cmd_ev(box: BoxSet, args: argparse.Namespace) -> int:
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
            if args.cards:
                _print_cards(box, mode)
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


def _cmd_record(box: BoxSet, args: argparse.Namespace) -> int:
    """現在のセット定義の相場を、その日の観測として履歴に追記する。"""
    path = history.history_path(args.history, box.set_code)
    on = args.date or date.today().isoformat()

    existing = history.load(path)
    if any(o.date == on for o in existing) and not args.force:
        print(f"{on} の記録は既にあります。上書きするなら --force を付けてください。", file=sys.stderr)
        return 1

    obs = history.snapshot(box, on=on)
    n = history.append(path, obs)
    print(f"{path} に {on} 時点の {n} 件を記録しました。")

    prev = [d for d in history.dates(existing) if d < on]
    if prev:
        _print_changes(history.changes(existing + obs, prev[-1], on, args.mode), prev[-1], on)
    return 0


def _print_changes(rows: list[history.Change], frm: str, to: str) -> None:
    if not rows:
        print(f"  {frm} → {to}: 相場の変動はありません。")
        return
    print(f"\n── 相場の変動 {frm} → {to} ──")
    for c in rows:
        arrow = "↑" if c.diff > 0 else "↓"
        print(
            f"  {arrow} {_ljust(f'[{c.rarity}] {c.card}', 32)}"
            f"{_rjust(_yen(c.old), 11)} → {_rjust(_yen(c.new), 11)}"
            f"{_rjust(f'{c.ratio:+.1%}', 9)}"
        )
    print()


def _cmd_add(box: BoxSet, args: argparse.Namespace) -> int:
    """個別のカード相場を履歴に追記する。

    買取表から読み取った値を入れる口。`レアリティ/カード名=価格` の形で渡す。
    """
    path = history.history_path(args.history, box.set_code)
    on = args.date or date.today().isoformat()

    known = {(r, g.name) for r, gs in box.cards.items() for g in gs}
    obs, bad = [], []
    for item in args.price:
        try:
            ident, raw = item.rsplit("=", 1)
            rarity, card = ident.split("/", 1)
            price = float(raw.replace(",", "").replace("円", "").strip())
        except ValueError:
            bad.append(f"{item}: 形式は レアリティ/カード名=価格")
            continue
        rarity, card = rarity.strip(), card.strip()
        if (rarity, card) not in known:
            bad.append(f"{item}: {rarity}/{card} はセット定義にありません")
            continue
        obs.append(
            history.Observation(
                date=on,
                set_code=box.set_code,
                rarity=rarity,
                card=card,
                mode=args.price_mode,
                price=price,
                source=args.source,
                quoted_at=args.quoted_at or on,
            )
        )

    if bad:
        for b in bad:
            print(f"エラー: {b}", file=sys.stderr)
        return 1

    n = history.append(path, obs)
    print(f"{path} に {on} 時点の {n} 件を追記しました。")

    prev = [d for d in history.dates(history.load(path)) if d < on]
    if prev:
        _print_changes(
            history.changes(history.load(path), prev[-1], on, args.price_mode), prev[-1], on
        )
    return 0


def _cmd_trend(box: BoxSet, args: argparse.Namespace) -> int:
    """履歴からBOX期待値の推移を出す。"""
    path = history.history_path(args.history, box.set_code)
    obs = history.load(path)
    if not obs:
        print(f"{path} に記録がありません。先に record を実行してください。", file=sys.stderr)
        return 1

    all_dates = history.dates(obs)
    modes = list(PRICE_MODES) if args.mode == "both" else [args.mode]

    print(f"■ {box.set_name}  [{box.set_code}]  記録 {len(all_dates)}日分")
    for mode in modes:
        print(f"\n── BOX期待値の推移（{_MODE_LABEL[mode]}）──")
        print(_ljust("日付", 14) + _rjust("BOX期待値", 13) + _rjust("前回比", 11) + _rjust("還元率", 10))
        prev_ev = None
        for d in all_dates:
            snap = history.with_prices(box, history.prices_on(obs, d))
            res = compute_ev(snap, mode)
            delta = "-" if prev_ev is None else f"{res.ev_box - prev_ev:+,.0f}円"
            print(
                _ljust(d, 14)
                + _rjust(_yen(res.ev_box), 13)
                + _rjust(delta, 11)
                + _rjust(f"{res.roi:.1%}", 10)
            )
            prev_ev = res.ev_box

    if len(all_dates) >= 2:
        target = args.mode if args.mode != "both" else PRICE_MODES[0]
        _print_changes(history.changes(obs, all_dates[-2], all_dates[-1], target), *all_dates[-2:])
    return 0


# ---- BOX買取価格 -----------------------------------------------------


def _cond_label(c: str) -> str:
    return boxprice.CONDITIONS.get(c, c)


def _cmd_box_add(args: argparse.Namespace) -> int:
    """買取表から読み取ったBOX買取額を記録する。"""
    aliases = boxprice.Aliases.load(args.aliases)
    on = args.date or date.today().isoformat()

    rows, bad = [], []
    for item in args.price:
        try:
            ident, raw = item.rsplit("=", 1)
            name, _, condition = ident.partition("/")
            condition = condition or "shrink"
            if condition not in boxprice.CONDITIONS:
                raise ValueError(f"状態は {'/'.join(boxprice.CONDITIONS)} のいずれか")
            price = boxprice.parse_price(raw)
            set_name = aliases.resolve(name.strip(), allow_new=args.new)
        except (ValueError, boxprice.AliasError) as exc:
            bad.append(f"{item}: {exc}")
            continue
        rows.append(
            boxprice.BoxPrice(
                date=on,
                set_name=set_name,
                condition=condition,
                price=price,
                shop=args.shop,
                source=args.source,
                note=args.note,
            )
        )

    if bad:
        for b in bad:
            print(f"エラー: {b}", file=sys.stderr)
        return 1

    existing = boxprice.load(args.box_prices)
    if args.source and args.source in boxprice.sources(existing) and not args.force:
        print(
            f"出典 '{args.source}' は取り込み済みです。再取り込みするなら --force。",
            file=sys.stderr,
        )
        return 1

    n = boxprice.append(args.box_prices, rows)
    if args.new:
        aliases.save(args.aliases)
    print(f"{args.box_prices} に {on} 時点の {n} 件を記録しました。")

    prev = [d for d in boxprice.dates(existing) if d < on]
    if prev:
        _print_box_changes(boxprice.changes(existing + rows, prev[-1], on), prev[-1], on)
    return 0


def _print_box_changes(rows: list[boxprice.BoxChange], frm: str, to: str) -> None:
    if not rows:
        print(f"  {frm} → {to}: 買取価格の変動はありません。")
        return
    print(f"\n── BOX買取の変動 {frm} → {to} ──")
    for c in rows:
        arrow = "↑" if c.diff > 0 else "↓"
        label = f"{c.set_name}（{_cond_label(c.condition)}）"
        print(
            f"  {arrow} {_ljust(label, 34)}"
            f"{_rjust(_yen(c.old), 10)} → {_rjust(_yen(c.new), 10)}"
            f"{_rjust(f'{c.ratio:+.1%}', 9)}"
        )
    print()


def _cmd_box_list(args: argparse.Namespace) -> int:
    """指定日時点の全セットの買取額を一覧する。"""
    rows = boxprice.load(args.box_prices)
    if not rows:
        print(f"{args.box_prices} に記録がありません。", file=sys.stderr)
        return 1

    on = args.date or date.today().isoformat()
    table = boxprice.snapshot_table(rows, on)
    print(f"■ BOX買取価格  {on} 時点（{len(table)}件）")
    print(_ljust("セット", 26) + _ljust("状態", 16) + _rjust("買取", 10) + "  " + _ljust("観測日", 14) + "店")
    for r in table:
        # その日の観測でなければ、持ち越しと分かるよう括弧を付ける。
        shown = r.date if r.date == on else f"({r.date})"
        print(
            _ljust(r.set_name, 26)
            + _ljust(_cond_label(r.condition), 16)
            + _rjust(_yen(r.price), 10)
            + "  "
            + _ljust(shown, 14)
            + r.shop
        )
    return 0


def _cmd_box_trend(args: argparse.Namespace) -> int:
    """BOX買取額の推移を出す。"""
    rows = boxprice.load(args.box_prices)
    if not rows:
        print(f"{args.box_prices} に記録がありません。", file=sys.stderr)
        return 1

    targets = [args.set] if args.set else boxprice.sets(rows)
    for set_name in targets:
        keys = sorted({r.key() for r in rows if r.set_name == set_name})
        if not keys:
            print(f"'{set_name}' の記録がありません。", file=sys.stderr)
            return 1
        print(f"\n■ {set_name}")
        for key in keys:
            s = boxprice.series(rows, *key)
            if len(s) < 2 and not args.all:
                continue
            print(f"  [{_cond_label(key[1])} / {key[2]}]")
            prev = None
            for r in s:
                delta = "-" if prev is None else f"{r.price - prev:+,.0f}円"
                print(f"    {_ljust(r.date, 14)}{_rjust(_yen(r.price), 10)}{_rjust(delta, 11)}")
                prev = r.price

    all_dates = boxprice.dates(rows)
    if len(all_dates) >= 2:
        _print_box_changes(boxprice.changes(rows, all_dates[-2], all_dates[-1]), *all_dates[-2:])
    return 0


def _cmd_box_pending(args: argparse.Namespace) -> int:
    """未取り込みの買取表画像を一覧する。

    観測の source に画像のファイル名を入れているので、まだ source に
    現れていない画像が未処理ぶん。別途の状態ファイルを持たずに済む。
    """
    inbox = Path(args.inbox)
    if not inbox.exists():
        print(f"{inbox} がありません。買取表の画像をここに置いてください。")
        return 0

    done = boxprice.sources(boxprice.load(args.box_prices))
    images = sorted(
        p for p in inbox.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    pending = [p for p in images if p.name not in done]

    if not pending:
        print(f"未処理の画像はありません。（{inbox} に {len(images)}枚、全て取り込み済み）")
        return 0

    print(f"未処理の画像 {len(pending)}枚:")
    for p in pending:
        print(f"  {p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pokebox-ev",
        description="ポケモンカードBOXの開封期待値を計算し、相場の推移を記録する",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("dataset", type=Path, help="セット定義JSONのパス")
        p.add_argument(
            "--mode",
            choices=[*PRICE_MODES, "both"],
            default="both",
            help="kaitori=買取価格ベース / hanbai=販売価格ベース / both=両方（既定）",
        )
        p.add_argument(
            "--history",
            type=Path,
            default=Path("data/history"),
            help="相場履歴の保存先ディレクトリ",
        )

    p_ev = sub.add_parser("ev", help="期待値を計算する")
    add_common(p_ev)
    p_ev.add_argument("--box-price", type=float, help="BOX価格を上書きする")
    p_ev.add_argument("--trials", type=int, default=200_000, help="シミュレーション回数（0で省略）")
    p_ev.add_argument("--seed", type=int, default=0, help="乱数シード")
    p_ev.add_argument("--cards", action="store_true", help="カード単位の相場を出典つきで表示する")
    p_ev.add_argument("--json", action="store_true", help="結果をJSONで出力する")

    p_rec = sub.add_parser("record", help="現在の相場をその日の観測として履歴に追記する")
    add_common(p_rec)
    p_rec.add_argument("--date", help="記録日 (YYYY-MM-DD)。既定は今日")
    p_rec.add_argument("--force", action="store_true", help="同じ日の記録が既にあっても追記する")

    p_add = sub.add_parser("add", help="買取表から読み取った相場を1枚単位で履歴に追記する")
    add_common(p_add)
    p_add.add_argument(
        "--price",
        action="append",
        required=True,
        metavar="レアリティ/カード名=価格",
        help="例: --price 'SAR/メガダークライex=22000'（複数指定可）",
    )
    p_add.add_argument("--price-mode", choices=PRICE_MODES, default="kaitori", help="買取か販売か")
    p_add.add_argument("--source", default="", help="出典（ツイートURLなど）")
    p_add.add_argument("--quoted-at", help="その価格が出た日。既定は記録日と同じ")
    p_add.add_argument("--date", help="記録日 (YYYY-MM-DD)。既定は今日")

    p_tr = sub.add_parser("trend", help="履歴からBOX期待値の推移を出す")
    add_common(p_tr)

    # ---- BOX買取価格（セット定義に依存しない） ----
    def add_box_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--box-prices",
            type=Path,
            default=Path("data/box_prices.jsonl"),
            help="BOX買取価格の記録ファイル",
        )
        p.add_argument(
            "--aliases",
            type=Path,
            default=Path("data/set_aliases.json"),
            help="セット名の表記ゆれ対応表",
        )

    p_ba = sub.add_parser("box-add", help="買取表から読み取ったBOX買取額を記録する")
    add_box_common(p_ba)
    p_ba.add_argument(
        "--price",
        action="append",
        required=True,
        metavar="セット名[/状態]=価格",
        help="例: --price 'アビスアイ/shrink=7000'。状態の既定は shrink",
    )
    p_ba.add_argument("--shop", default="トレカマサイ", help="買取店名")
    p_ba.add_argument("--source", default="", help="出典。画像ファイル名かツイートURL")
    p_ba.add_argument("--date", help="記録日 (YYYY-MM-DD)。既定は今日")
    p_ba.add_argument("--note", default="", help="備考")
    p_ba.add_argument("--new", action="store_true", help="未知のセット名を新規として登録する")
    p_ba.add_argument("--force", action="store_true", help="同じ出典を再取り込みする")

    p_bl = sub.add_parser("box-list", help="指定日時点の全セットの買取額を一覧する")
    add_box_common(p_bl)
    p_bl.add_argument("--date", help="基準日 (YYYY-MM-DD)。既定は今日")

    p_bt = sub.add_parser("box-trend", help="BOX買取額の推移を出す")
    add_box_common(p_bt)
    p_bt.add_argument("--set", help="セット名。省略すると全セット")
    p_bt.add_argument("--all", action="store_true", help="記録が1件だけの系列も表示する")

    p_bp = sub.add_parser("box-pending", help="未取り込みの買取表画像を一覧する")
    add_box_common(p_bp)
    p_bp.add_argument("--inbox", type=Path, default=Path("data/inbox"), help="画像の置き場")

    args = parser.parse_args(argv)

    box_handlers = {
        "box-add": _cmd_box_add,
        "box-list": _cmd_box_list,
        "box-trend": _cmd_box_trend,
        "box-pending": _cmd_box_pending,
    }
    if args.command in box_handlers:
        try:
            return box_handlers[args.command](args)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"エラー: {exc}", file=sys.stderr)
            return 1

    try:
        box = load_set(args.dataset)
    except (OSError, json.JSONDecodeError, DataError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    handlers = {"ev": _cmd_ev, "record": _cmd_record, "add": _cmd_add, "trend": _cmd_trend}
    return handlers[args.command](box, args)


if __name__ == "__main__":
    raise SystemExit(main())
