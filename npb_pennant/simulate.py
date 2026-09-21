"""残り試合の事後予測モンテカルロ。

強さの点推定を1つ決めて回すのではなく、`strength.fit` が返す事後分布から
θ を引き直しながらシーズンを再生する（事後予測分布）。こうすると
「どのくらい強いか自体わからない」という不確かさが確率に乗る。点推定で
回すと、先頭を走っているチームの優勝確率は必ず過大に出る。

1試合の生成規則:

    引き分け     : 確率 τ（リーグの引き分け発生率）
    ホーム勝ち   : (1-τ) × logistic(θ_home - θ_away + h)
    ビジター勝ち : 残り

順位は勝率 = 勝/(勝+敗) で決まるので、引き分けは「勝率据え置きで残り試合が
1つ減る」＝ リードしている側に有利に働く。この非対称性まで含めて数える。
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

from .model import Game, League, champion, contenders
from .strength import Posterior, logistic

WIN, LOSS, TIE = 0, 1, 2
OUTCOME_LABEL = {WIN: "勝", LOSS: "敗", TIE: "分"}


@dataclass
class GameSplit:
    """注目チームの1試合について、結果別の優勝確率。"""

    index: int
    date: str
    opponent: str
    home: bool
    prob: dict[int, float]  # 結果 -> その結果になる確率
    champ_given: dict[int, float]  # 結果 -> その結果のときの優勝確率
    decider: float = 0.0  # この試合が優勝決定戦になる確率（結果しだいで優勝の行方が決まる）
    settled: float = 0.0  # 実際にこの試合で優勝が決まる確率
    clinch_if_win: float = 0.0  # 勝てばその日に優勝が確定する確率
    clinch_if_draw: float = 0.0  # 引き分けでもその日に優勝が確定する確率
    eliminated_if_loss: float = 0.0  # 負ければその日に他球団の優勝が確定する確率

    @property
    def swing(self) -> float:
        """勝ったときと負けたときの優勝確率の差（この試合の重み）。"""
        return self.champ_given[WIN] - self.champ_given[LOSS]


@dataclass
class Result:
    trials: int
    champion_prob: dict[str, float]
    focus: str
    entering: float
    splits: list[GameSplit]
    champ_by_wins: dict[int, tuple[float, float]]  # 残り勝ち数 -> (その勝ち数になる確率, 優勝確率)
    clinch_by_date: list[tuple[str, float]]  # 日付 -> その日までに注目チームの優勝が確定している確率
    decided_by_date: list[tuple[str, dict[str, float]]]  # 日付 -> {優勝チーム: その日に決着する確率}
    final_wins: dict[int, float] = field(default_factory=dict)

    def stderr(self, p: float) -> float:
        return (p * (1.0 - p) / self.trials) ** 0.5


def _sorted_games(league: League, alive: tuple[str, ...]) -> list[Game]:
    return sorted(
        (g for g in league.remaining if g.home in alive or g.away in alive),
        key=lambda g: (g.date, g.home, g.away),
    )


def simulate(
    league: League,
    posterior: Posterior,
    *,
    focus: str = "T",
    trials_per_draw: int = 10,
    seed: int = 0,
    tie_rate: float | None = None,
    home_advantage: float | None = None,
    track_clinch: bool = True,
) -> Result:
    rng = random.Random(seed)
    tau = league.tie_rate if tie_rate is None else tie_rate
    h = league.home_advantage_logit if home_advantage is None else home_advantage

    alive = contenders(league)
    if focus not in alive:
        raise ValueError(f"{league.team(focus).name} はすでに優勝の可能性がない")

    games = _sorted_games(league, alive)
    focus_games = [g for g in games if g.involves(focus)]
    focus_index = {id(g): i for i, g in enumerate(focus_games)}
    dates = sorted({g.date for g in games})

    base = {tid: (league.team(tid).w, league.team(tid).l, league.team(tid).t) for tid in alive}
    # 残り試合数は143試合制から逆算した数（日程表に載せていない消化試合も含む）。
    rem_total = {tid: league.remaining_count(tid) for tid in alive}
    h2h_base = {(a, b): league.h2h(a, b) for a in alive for b in alive if a != b}

    champ_count: dict[str, int] = defaultdict(int)
    split_n = [[0, 0, 0] for _ in focus_games]
    split_champ = [[0, 0, 0] for _ in focus_games]
    wins_n: dict[int, int] = defaultdict(int)
    wins_champ: dict[int, int] = defaultdict(int)
    clinch_n = [0] * len(dates)
    decided_n: list[dict[str, int]] = [defaultdict(int) for _ in dates]
    decider_n = [0] * len(focus_games)
    settled_n = [0] * len(focus_games)
    clinch_win_n = [0] * len(focus_games)
    clinch_draw_n = [0] * len(focus_games)
    elim_loss_n = [0] * len(focus_games)
    date_index = {d: i for i, d in enumerate(dates)}
    trials = 0

    for draw in posterior.draws:
        theta = draw.theta
        # このθのもとでの各試合のホーム勝率（試行ごとに計算し直さない）。
        phome = [tau + (1.0 - tau) * logistic(theta[g.home] - theta[g.away] + h) for g in games]

        for _ in range(trials_per_draw):
            trials += 1
            rec = {tid: list(base[tid]) for tid in alive}
            left = dict(rem_total)
            h2h = dict(h2h_base)
            outcomes = [0] * len(focus_games)
            clinched_at = None
            focus_wins = 0
            day_focus_game: tuple[int, str, int] | None = None

            for gi, g in enumerate(games):
                r = rng.random()
                if r < tau:
                    winner = None
                elif r < phome[gi]:
                    winner = g.home
                else:
                    winner = g.away

                for tid in (g.home, g.away):
                    if tid in rec:
                        left[tid] -= 1
                if winner is None:
                    for tid in (g.home, g.away):
                        if tid in rec:
                            rec[tid][2] += 1
                else:
                    loser = g.away if winner == g.home else g.home
                    if winner in rec:
                        rec[winner][0] += 1
                    if loser in rec:
                        rec[loser][1] += 1
                if g.home in rec and g.away in rec:
                    a, b = g.home, g.away
                    aw, al, at = h2h[(a, b)]
                    if winner is None:
                        h2h[(a, b)] = (aw, al, at + 1)
                        h2h[(b, a)] = (al, aw, at + 1)
                    elif winner == a:
                        h2h[(a, b)] = (aw + 1, al, at)
                        h2h[(b, a)] = (al, aw + 1, at)
                    else:
                        h2h[(a, b)] = (aw, al + 1, at)
                        h2h[(b, a)] = (al + 1, aw, at)

                if g.involves(focus):
                    k = focus_index[id(g)]
                    if winner == focus:
                        outcomes[k] = WIN
                        focus_wins += 1
                    elif winner is None:
                        outcomes[k] = TIE
                    else:
                        outcomes[k] = LOSS
                    day_focus_game = (k, g.away if g.home == focus else g.home, outcomes[k])

                # その日の最終試合を終えた時点で、優勝が数学的に確定したかを見る。
                if track_clinch and (gi + 1 == len(games) or games[gi + 1].date != g.date):
                    if clinched_at is None and day_focus_game is not None:
                        # この日を迎えた時点では未決着。注目チームの結果を差し替えて、
                        # 「勝っていたら／引き分けていたら／負けていたら」その日に決着したかを見る。
                        k, opp, actual = day_focus_game
                        champ_if = {}
                        for alt in (WIN, TIE, LOSS):
                            if alt == actual:
                                alt_rec, alt_h2h = rec, h2h
                            else:
                                alt_rec = {t: list(v) for t, v in rec.items()}
                                alt_h2h = dict(h2h)
                                _swap(alt_rec, alt_h2h, focus, opp, actual, alt)
                            champ_if[alt] = _decided_champion(alt_rec, left, alt_h2h)
                        # その試合の結果が、決着するか・誰が優勝するかを左右した場合だけ
                        # 「決定戦」と数える（巨人の結果だけで決まる日は含めない）。
                        matters = len(set(champ_if.values())) > 1
                        if matters:
                            decider_n[k] += 1
                            if champ_if[WIN] == focus:
                                clinch_win_n[k] += 1
                            if champ_if[TIE] == focus:
                                clinch_draw_n[k] += 1
                            if champ_if[LOSS] is not None and champ_if[LOSS] != focus:
                                elim_loss_n[k] += 1
                            if champ_if[actual] is not None:
                                settled_n[k] += 1
                    day_focus_game = None
                    if clinched_at is None and _decided(rec, left, h2h):
                        clinched_at = date_index[g.date]

            finals = {tid: tuple(rec[tid]) for tid in alive}
            champ = champion(finals, h2h)
            champ_count[champ] += 1
            won = champ == focus
            for k, o in enumerate(outcomes):
                split_n[k][o] += 1
                if won:
                    split_champ[k][o] += 1
            wins_n[focus_wins] += 1
            if won:
                wins_champ[focus_wins] += 1
            # 最終戦を終えても確定しないのは、勝率が並んで対戦成績で決まるケース。
            # それも「最終日に決着」として数える。
            if track_clinch:
                day = len(dates) - 1 if clinched_at is None else clinched_at
                decided_n[day][champ] += 1
                if won:
                    clinch_n[day] += 1

    champion_prob = {tid: champ_count[tid] / trials for tid in alive}
    splits = []
    for k, g in enumerate(focus_games):
        opp = g.away if g.home == focus else g.home
        splits.append(
            GameSplit(
                index=k,
                date=g.date,
                opponent=league.team(opp).name,
                home=g.home == focus,
                prob={o: split_n[k][o] / trials for o in (WIN, LOSS, TIE)},
                champ_given={
                    o: (split_champ[k][o] / split_n[k][o] if split_n[k][o] else 0.0) for o in (WIN, LOSS, TIE)
                },
                decider=decider_n[k] / trials,
                settled=settled_n[k] / trials,
                clinch_if_win=clinch_win_n[k] / trials,
                clinch_if_draw=clinch_draw_n[k] / trials,
                eliminated_if_loss=elim_loss_n[k] / trials,
            )
        )
    champ_by_wins = {
        w: (wins_n[w] / trials, wins_champ[w] / wins_n[w] if wins_n[w] else 0.0) for w in sorted(wins_n)
    }
    cum = 0
    clinch_by_date = []
    decided_by_date = []
    for i, d in enumerate(dates):
        cum += clinch_n[i]
        clinch_by_date.append((d, cum / trials))
        decided_by_date.append((d, {tid: n / trials for tid, n in sorted(decided_n[i].items())}))

    return Result(
        trials=trials,
        champion_prob=champion_prob,
        focus=focus,
        entering=champion_prob[focus],
        splits=splits,
        champ_by_wins=champ_by_wins,
        clinch_by_date=clinch_by_date,
        decided_by_date=decided_by_date,
        final_wins={w: n / trials for w, n in sorted(wins_n.items())},
    )


def _swap(
    rec: dict[str, list[int]],
    h2h: dict[tuple[str, str], tuple[int, int, int]],
    focus: str,
    opp: str,
    old: int,
    new: int,
) -> None:
    """注目チームの1試合の結果を old から new に差し替える（相手と対戦成績も直す）。"""
    delta = {WIN: (1, 0, 0), LOSS: (0, 1, 0), TIE: (0, 0, 1)}
    for sign, res in ((-1, old), (+1, new)):
        dw, dl, dt = delta[res]
        if focus in rec:
            rec[focus][0] += sign * dw
            rec[focus][1] += sign * dl
            rec[focus][2] += sign * dt
        if opp in rec:
            rec[opp][0] += sign * dl
            rec[opp][1] += sign * dw
            rec[opp][2] += sign * dt
        if (focus, opp) in h2h:
            w, l, t = h2h[(focus, opp)]
            h2h[(focus, opp)] = (w + sign * dw, l + sign * dl, t + sign * dt)
            h2h[(opp, focus)] = (l + sign * dl, w + sign * dw, t + sign * dt)


def _can_still_win(
    tid: str,
    rec: dict[str, list[int]],
    left: dict[str, int],
    h2h: dict[tuple[str, str], tuple[int, int, int]],
) -> bool:
    """tid にまだ1位の目があるか。

    「tid が残り全勝したときの最高勝率」対「相手が残り全敗したときの最低勝率」を
    順位決定規定（勝率 → 勝利数 → 当該球団間の対戦成績）で突き合わせる。
    同率で並んだ場合に対戦成績でどちらが上かまで見るので、マジック計算より
    1日早く決着が確定することがある。
    """
    w, l, t = rec[tid]
    ceiling = (w + left[tid], l, t)
    for other in rec:
        if other == tid:
            continue
        ow, ol, ot = rec[other]
        floor = (ow, ol + left[other], ot)
        if champion({tid: ceiling, other: floor}, h2h) != tid:
            return False
    return True


def _decided_champion(
    rec: dict[str, list[int]],
    left: dict[str, int],
    h2h: dict[tuple[str, str], tuple[int, int, int]],
) -> str | None:
    """優勝チームが1つに絞られていればそのID、まだ決まっていなければ None。"""
    alive = [tid for tid in rec if _can_still_win(tid, rec, left, h2h)]
    return alive[0] if len(alive) == 1 else None


def _decided(
    rec: dict[str, list[int]],
    left: dict[str, int],
    h2h: dict[tuple[str, str], tuple[int, int, int]],
) -> bool:
    """優勝チームが1つに絞られたか。"""
    return _decided_champion(rec, left, h2h) is not None
