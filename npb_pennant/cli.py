"""コマンドライン。優勝確率を1枚のレポートとして出す。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .model import League, apply_results, contenders, load_league, max_possible_pct, min_possible_pct
from .simulate import LOSS, TIE, WIN, Result, simulate
from .strength import fit

DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data" / "npb_2026_central.json"


def _fmt_pct(x: float) -> str:
    return f".{round(x * 1000):03d}"


def _standings(league: League, out) -> None:
    alive = set(contenders(league))
    print(f"■ {league.season}年 {league.name}（{league.as_of} 終了時点）", file=out)
    print("  チーム   勝  敗 分   勝率   残り  最高勝率 最低勝率  優勝の目", file=out)
    for t in sorted(league.teams, key=lambda x: -x.pct):
        rem = league.remaining_count(t.id)
        mark = "あり" if t.id in alive else "消滅"
        print(
            f"  {t.name:<5s}{t.w:4d}{t.l:4d}{t.t:3d}   {_fmt_pct(t.pct)}   {rem:3d}    "
            f"{_fmt_pct(max_possible_pct(t, rem))}    {_fmt_pct(min_possible_pct(t, rem))}   {mark}",
            file=out,
        )
    print(file=out)


def _report(league: League, res: Result, out) -> None:
    focus = league.team(res.focus)
    se = res.stderr(res.entering)
    print(f"■ 優勝確率（{res.trials:,}回のシミュレーション）", file=out)
    for tid, p in sorted(res.champion_prob.items(), key=lambda kv: -kv[1]):
        print(f"  {league.team(tid).name:<5s} {p * 100:6.2f}%", file=out)
    print(f"  ※ {focus.name}の確率のモンテカルロ誤差は ±{se * 100 * 1.96:.2f}pt（95%）", file=out)
    print(file=out)

    print(f"■ {focus.name}の残り{len(res.splits)}試合：1試合ごとの優勝確率", file=out)
    print("   日付        相手      球場   勝つ確率 │ 勝てば   分けば   負ければ │ 勝敗の差", file=out)
    for sp in res.splits:
        place = "本拠地" if sp.home else "ビジター"
        print(
            f"   {sp.date}  {sp.opponent:<6s} {place:<4s}  {sp.prob[WIN] * 100:5.1f}%  │"
            f" {sp.champ_given[WIN] * 100:6.1f}%  {sp.champ_given[TIE] * 100:6.1f}%  {sp.champ_given[LOSS] * 100:6.1f}%  │"
            f"  {sp.swing * 100:5.1f}pt",
            file=out,
        )
    print("   ※「勝てば」はその試合に勝った場合の優勝確率。他の試合の結果は平均して均している。", file=out)
    print(file=out)

    print(f"■ {focus.name}が残り何勝すれば優勝できるか", file=out)
    print("   勝数   そうなる確率   そのときの優勝確率", file=out)
    for w, (p_w, p_champ) in res.champ_by_wins.items():
        if p_w < 0.002:
            continue
        bar = "█" * round(p_w * 60)
        print(f"   {w:2d}勝     {p_w * 100:5.1f}%        {p_champ * 100:6.1f}%  {bar}", file=out)
    print(file=out)

    print(f"■ 優勝が数学的に確定する日（{focus.name}が優勝する場合の内訳ではなく全体に対する割合）", file=out)
    prev = 0.0
    for date, cum in res.clinch_by_date:
        if cum - prev > 0.0005 or cum >= res.entering - 1e-9:
            print(f"   {date} までに確定  {cum * 100:5.1f}%", file=out)
        prev = cum
    print(file=out)


def _sensitivity(league: League, args, out) -> None:
    print("■ 感度分析（仮定を変えたときに答えがどれだけ動くか）", file=out)
    cases = [
        ("基準", {}, {}),
        ("実力差が小さい (σ=0.10固定)", {"fixed_sigma": 0.10}, {}),
        ("実力差が大きい (σ=0.25固定)", {"fixed_sigma": 0.25}, {}),
        ("本拠地補正なし", {}, {"home_advantage": 0.0}),
        ("本拠地補正 2倍 (h=0.28)", {}, {"home_advantage": 0.28}),
        ("引き分けなし (τ=0)", {}, {"tie_rate": 0.0}),
        ("引き分け 3%", {}, {"tie_rate": 0.03}),
    ]
    for label, fit_kw, sim_kw in cases:
        post = fit(league, draws=args.draws // 4, burn_in=5000, thin=5, seed=args.seed, **fit_kw)
        res = simulate(
            league, post, focus=args.focus, trials_per_draw=args.per_draw, seed=args.seed,
            track_clinch=False, **sim_kw,
        )
        print(f"   {label:<28s} {res.entering * 100:6.2f}%", file=out)
    print(file=out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="npb_pennant", description="NPBの優勝確率を残り日程から計算する"
    )
    parser.add_argument("data", nargs="?", default=str(DEFAULT_DATA), help="順位・日程のJSON")
    parser.add_argument("--focus", default="T", help="注目するチームID（既定: T=阪神）")
    parser.add_argument("--assume", default="", help="注目チームの残り試合の結果を先頭から順に当てはめる（例: WWLT）")
    parser.add_argument("--draws", type=int, default=20000, help="事後分布から取るサンプル数")
    parser.add_argument("--per-draw", type=int, default=15, help="1サンプルあたりのシーズン再生回数")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--sensitivity", action="store_true", help="仮定を変えた場合も計算する")
    parser.add_argument("--json", action="store_true", help="結果をJSONで出す")
    args = parser.parse_args(argv)

    league = load_league(args.data)
    if args.assume:
        league = apply_results(league, args.assume, focus=args.focus)

    posterior = fit(league, draws=args.draws, burn_in=20000, thin=10, seed=args.seed)
    result = simulate(
        league, posterior, focus=args.focus, trials_per_draw=args.per_draw, seed=args.seed
    )

    if args.json:
        payload = {
            "as_of": league.as_of,
            "assumed_results": args.assume,
            "trials": result.trials,
            "champion_prob": {league.team(k).name: v for k, v in result.champion_prob.items()},
            "games": [
                {
                    "date": s.date,
                    "opponent": s.opponent,
                    "home": s.home,
                    "p_win": s.prob[WIN],
                    "champ_if_win": s.champ_given[WIN],
                    "champ_if_tie": s.champ_given[TIE],
                    "champ_if_loss": s.champ_given[LOSS],
                }
                for s in result.splits
            ],
            "champ_by_remaining_wins": {
                str(w): {"p": p, "champ": c} for w, (p, c) in result.champ_by_wins.items()
            },
            "clinch_by_date": dict(result.clinch_by_date),
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    out = sys.stdout
    if args.assume:
        print(f"（残り試合に {args.assume} を当てはめた状態から計算）\n", file=out)
    _standings(league, out)
    _report(league, result, out)
    if args.sensitivity:
        _sensitivity(league, args, out)
    return 0
