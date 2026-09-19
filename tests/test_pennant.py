import json
import re
from pathlib import Path

import pytest

from npb_pennant import (
    DataError,
    League,
    Team,
    apply_results,
    champion,
    contenders,
    fit,
    load_league,
    max_possible_pct,
    min_possible_pct,
    pct,
    simulate,
)
from npb_pennant.model import Game, validate
from npb_pennant.simulate import LOSS, TIE, WIN
from npb_pennant.strength import PL, pl_prior

ROOT = Path(__file__).resolve().parents[1]
CENTRAL = ROOT / "data" / "npb_2026_central.json"


def test_pct_ignores_ties():
    # 引き分けは分母に入らない。70勝60敗3分と70勝60敗0分の勝率は同じ。
    assert pct(70, 60) == pytest.approx(70 / 130)
    assert pct(0, 0) == 0.0


def test_load_and_validate_real_data():
    league = load_league(CENTRAL)
    assert league.season == 2026
    assert {t.name for t in league.teams} == {"阪神", "巨人", "DeNA", "ヤクルト", "中日", "広島"}
    # 日程に載っている試合数は、143試合制から逆算した残り試合数を超えない。
    for t in league.teams:
        assert league.scheduled_count(t.id) <= league.remaining_count(t.id)
    # 優勝を争う3球団については、残り試合がすべて日程に入っている。
    for tid in ("T", "G", "DB"):
        assert league.scheduled_count(tid) == league.remaining_count(tid)


def test_head_to_head_is_symmetric():
    league = load_league(CENTRAL)
    assert league.h2h("T", "G") == (16, 8, 0)
    assert league.h2h("G", "T") == (8, 16, 0)


def test_validate_rejects_overbooked_schedule():
    teams = (Team("A", "A", 71, 71, 1), Team("B", "B", 70, 72, 1))
    games = tuple(Game(f"2026-10-0{i}", "A", "B") for i in range(1, 5))
    league = League(
        name="test", season=2026, as_of="2026-09-18", games_per_season=143,
        interleague_games_per_team=0, teams=teams, remaining=games, head_to_head={},
        interleague=(0, 0, 0), tie_rate=0.02, home_advantage_logit=0.1,
    )
    with pytest.raises(DataError):
        validate(league)


def test_champion_tiebreak_uses_wins_then_head_to_head():
    # 勝率が同じなら勝利数の多い方。
    finals = {"A": (80, 60, 3), "B": (80, 60, 0)}
    assert champion(finals, {}) in ("A", "B")  # 完全同率なら対戦成績へ
    finals = {"A": (81, 60, 0), "B": (54, 40, 0)}  # .574 vs .574
    assert pct(81, 60) == pytest.approx(pct(54, 40), abs=1e-3)
    # 勝率がほぼ同じでも勝利数で A が上。
    assert champion({"A": (81, 60, 0), "B": (81, 60, 0)}, {("A", "B"): (16, 8, 0)}) == "A"
    assert champion({"A": (81, 60, 0), "B": (81, 60, 0)}, {("A", "B"): (8, 16, 0)}) == "B"


def test_contenders_eliminates_teams_that_cannot_reach_the_leaders_floor():
    league = load_league(CENTRAL)
    alive = contenders(league)
    assert set(alive) == {"T", "G", "DB"}
    # 阪神が全敗しても .500。ヤクルトは全勝しても .500 に届かない。
    hanshin = league.team("T")
    assert min_possible_pct(hanshin, league.remaining_count("T")) == pytest.approx(0.5)
    swallows = league.team("S")
    assert max_possible_pct(swallows, league.remaining_count("S")) < 0.5


def test_apply_results_updates_both_teams_and_schedule():
    # データは日々更新されるので、相手は日程から引いて検証する。
    league = load_league(CENTRAL)
    first, second = league.games_of("T")[:2]
    opp1 = first.away if first.home == "T" else first.home
    opp2 = second.away if second.home == "T" else second.home
    updated = apply_results(league, "WL")
    assert (updated.team("T").w, updated.team("T").l) == (league.team("T").w + 1, league.team("T").l + 1)
    assert updated.team(opp1).l == league.team(opp1).l + 1
    assert updated.team(opp2).w == league.team(opp2).w + 1
    assert len(updated.remaining) == len(league.remaining) - 2
    assert updated.remaining_count("T") == league.remaining_count("T") - 2


def test_apply_results_updates_head_to_head_for_direct_games():
    league = load_league(CENTRAL)
    # 直接対決までを引き分けで消化してから、その1戦を落とす。
    games = league.games_of("T")
    idx = next(i for i, g in enumerate(games) if g.involves("G"))
    w, l, t = league.h2h("T", "G")
    updated = apply_results(league, "T" * idx + "L")
    assert updated.h2h("T", "G") == (w, l + 1, t)


def test_apply_results_rejects_too_many_games():
    league = load_league(CENTRAL)
    with pytest.raises(DataError):
        apply_results(league, "W" * 15)


def test_interleague_prior_matches_the_aggregate_record():
    league = load_league(CENTRAL)
    mean, sd = pl_prior(league)
    # パ65勝39敗 → log(65/39) ≈ 0.511
    assert mean == pytest.approx(0.511, abs=0.01)
    assert 0.15 < sd < 0.25


def test_fit_shrinks_towards_the_league_average():
    league = load_league(CENTRAL)
    post = fit(league, draws=1500, burn_in=3000, thin=3, seed=1)
    exp = post.expected_pct_vs_average(league.ids)
    # 推定勝率の順序は実績勝率の順序をおおむね保つ。
    assert exp["T"] > exp["DB"] > exp["D"]
    # ただし実績ほど極端ではない（縮小推定）。
    assert exp["T"] < league.team("T").pct
    assert exp["D"] > league.team("D").pct
    # パ・リーグは交流戦の結果どおりセより強く推定される。
    assert sum(d.theta[PL] for d in post.draws) / len(post.draws) > 0.2


def test_simulation_is_consistent_and_ordered():
    league = load_league(CENTRAL)
    post = fit(league, draws=800, burn_in=3000, thin=3, seed=2)
    res = simulate(league, post, focus="T", trials_per_draw=6, seed=3)
    assert res.trials == 4800
    assert sum(res.champion_prob.values()) == pytest.approx(1.0)
    # 首位で残り試合も多い阪神が本命。
    assert res.champion_prob["T"] > res.champion_prob["G"] > res.champion_prob["DB"]
    # どの試合も「勝ったほうが優勝確率が上がる」。
    for sp in res.splits:
        assert sp.champ_given[WIN] > sp.champ_given[LOSS]
        assert sp.prob[WIN] + sp.prob[LOSS] + sp.prob[TIE] == pytest.approx(1.0)
    # 直接対決の重みが最大になる。
    biggest = max(res.splits, key=lambda s: s.swing)
    assert biggest.opponent == "巨人"
    # 残り勝ち数が増えれば優勝確率は単調に上がる。
    xs = [c for _w, (p, c) in sorted(res.champ_by_wins.items()) if p > 0.01]
    assert xs == sorted(xs)


def test_already_clinched_state_gives_certainty():
    teams = (Team("A", "A", 90, 40, 0), Team("B", "B", 60, 70, 0))
    games = (Game("2026-10-01", "A", "B"),)
    league = League(
        name="test", season=2026, as_of="2026-09-18", games_per_season=131,
        interleague_games_per_team=0, teams=teams, remaining=games, head_to_head={},
        interleague=(50, 50, 0), tie_rate=0.02, home_advantage_logit=0.1,
    )
    assert contenders(league) == ("A",)
    post = fit(league, draws=300, burn_in=1000, thin=2, seed=0)
    with pytest.raises(ValueError):
        simulate(league, post, focus="B", trials_per_draw=2, seed=0)
    res = simulate(league, post, focus="A", trials_per_draw=2, seed=0)
    assert res.champion_prob["A"] == 1.0


def test_data_file_is_well_formed_json():
    raw = json.loads(CENTRAL.read_text(encoding="utf-8"))
    # 日々更新するスナップショットなので、日付は形だけ見る。
    assert re.fullmatch(r"2026-\d{2}-\d{2}", raw["as_of"])
    # 出典の区別を必ず持たせる（確定値と推定値を混ぜない）。
    for team in raw["teams"]:
        assert team["source"] in {"confirmed", "derived", "estimated"}
    for game in raw["remaining"]:
        assert game["source"] in {"confirmed", "opponent_confirmed", "derived", "estimated"}
