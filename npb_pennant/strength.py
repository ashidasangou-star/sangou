"""チーム強さのベイズ推定（Bradley–Terry モデル）。

「今の勝率」をそのまま残り試合の勝率に使うのは誤りである。130試合程度の
勝敗には無視できない量の偶然（二項ばらつき）が乗っているため、観測された
勝率は実力を過大／過小評価している。標本勝率をそのまま外挿すると、上位
チームの優勝確率は必ず過大に出る。

そこで各チームに潜在的な強さ θ を与える Bradley–Terry モデルを置く。

    P(i が j に勝つ | 引き分けない) = logistic(θ_i - θ_j + h)     h は本拠地補正

θ には階層事前分布 θ_i ~ N(0, σ²) を置き、σ 自体も推定する（σ が小さいほど
「リーグ内の実力差は小さい＝勝率は運」という推定になる）。σ を固定せず
データに決めさせるのが、この手の予測でいちばん効く。

交流戦は無視できない。2026年はパ・リーグの65勝39敗4分で、セ全体が18試合
ずつパと戦って大きく負け越している。この負け越し分をセ内部の実力差と
取り違えないよう、パ・リーグを1つの仮想チーム θ_PL として明示的に持つ。

推定は乱歩メトロポリス法（MCMC）。パラメータは7個（セ6球団 + パ）しか
ないので、標準ライブラリだけで十分速い。事後分布からサンプルを取り出し、
そのままモンテカルロの入力にすることで「強さの不確かさ」も確率に反映する
（＝点推定ではなく事後予測分布）。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .model import League

PL = "_PL"  # パ・リーグ平均を表す仮想チーム


def logistic(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


@dataclass(frozen=True)
class Draw:
    """事後分布からの1サンプル。"""

    theta: dict[str, float]
    sigma: float


@dataclass(frozen=True)
class Posterior:
    draws: tuple[Draw, ...]
    accept_rate: float

    def summary(self, team_ids: tuple[str, ...]) -> dict[str, tuple[float, float, float]]:
        """各チームの θ の (中央値, 5%点, 95%点)。"""
        out = {}
        for tid in team_ids:
            xs = sorted(d.theta[tid] for d in self.draws)
            n = len(xs)
            out[tid] = (xs[n // 2], xs[int(0.05 * n)], xs[int(0.95 * n)])
        return out

    def expected_pct_vs_average(self, team_ids: tuple[str, ...]) -> dict[str, float]:
        """リーグ平均（θ=0）のチームと中立球場でやったときの期待勝率。"""
        out = {}
        for tid in team_ids:
            out[tid] = sum(logistic(d.theta[tid]) for d in self.draws) / len(self.draws)
        return out


# σ（リーグ内の実力の標準偏差）の事前分布。過去のNPBの最終成績のばらつきは
# 勝率にして .03〜.08 程度で、そこから二項ばらつき（運）を除くと実力の
# 標準偏差はおおむね .03〜.06。dp/dθ = 0.25 なので θ に直すと 0.12〜0.24。
# その中心 0.18 を対数正規の中央値に置き、幅を広めにとって今季データで更新する。
SIGMA_PRIOR_MEDIAN = 0.18
SIGMA_PRIOR_LOG_SD = 0.35


def _log_prior(
    theta: dict[str, float],
    sigma: float,
    cl_ids: tuple[str, ...],
    sigma_median: float,
    pl_mean: float,
    pl_sd: float,
) -> float:
    if sigma <= 0.0:
        return -math.inf
    # θ_i ~ N(0, σ²)
    lp = 0.0
    for tid in cl_ids:
        lp += -0.5 * (theta[tid] / sigma) ** 2 - math.log(sigma)
    # σ ~ LogNormal(log sigma_median, SIGMA_PRIOR_LOG_SD)
    z = (math.log(sigma) - math.log(sigma_median)) / SIGMA_PRIOR_LOG_SD
    lp += -0.5 * z * z - math.log(sigma)
    # θ_PL は交流戦の成績で決まる（球団別の通算成績からはほとんど情報が
    # 取れないので、事前分布として明示的に入れる）。
    lp += -0.5 * ((theta[PL] - pl_mean) / pl_sd) ** 2
    return lp


def pl_prior(league: League) -> tuple[float, float]:
    """パ・リーグ平均の強さ θ_PL の事前分布 (平均, 標準偏差)。

    交流戦の通算成績（2026年はパ65勝39敗4分）から直接決まる。
    θ_PL = log(パの勝数 / セの勝数)、標準誤差は決着した試合数から。
    各球団の交流戦成績の内訳は公表資料から拾えなかったので、この
    「リーグ全体としてどれだけ負け越したか」だけを使う。内訳の不確かさは
    尤度側（1試合あたりの平均勝率として交流戦分を混ぜる形）で吸収される。
    """
    cl_w, cl_l, _ = league.interleague
    decided = cl_w + cl_l
    mean = math.log(cl_l / cl_w) if cl_w and cl_l else 0.0
    sd = math.sqrt(1.0 / (decided * 0.25)) if decided else 1.0
    return mean, sd


def _log_likelihood(theta: dict[str, float], league: League) -> float:
    """各チームの通算成績（決着した試合のみ）に対する対数尤度。

    リーグ内の日程はほぼ均等（各カード25試合）なので、チームiの1試合あたり
    平均勝率を「セ5球団との平均 + パとの試合」の加重平均で近似する。
    個々のカードの消化ペースのずれは無視できる大きさ。
    """
    cl_ids = league.ids
    il_games = league.interleague_games_per_team
    ll = 0.0
    for team in league.teams:
        ti = theta[team.id]
        vs_cl = sum(logistic(ti - theta[o]) for o in cl_ids if o != team.id) / (len(cl_ids) - 1)
        vs_pl = logistic(ti - theta[PL])
        n = team.played
        n_il = min(il_games, n)
        p = ((n - n_il) * vs_cl + n_il * vs_pl) / n
        p = min(max(p, 1e-9), 1 - 1e-9)
        ll += team.w * math.log(p) + team.l * math.log(1.0 - p)
    return ll


def fit(
    league: League,
    *,
    draws: int = 20000,
    burn_in: int = 20000,
    thin: int = 10,
    sigma_median: float = SIGMA_PRIOR_MEDIAN,
    fixed_sigma: float | None = None,
    seed: int = 0,
) -> Posterior:
    """メトロポリス法で事後分布からサンプリングする。

    sigma_median: σ の事前分布（対数正規）の中央値。
    fixed_sigma: σ を固定したいとき（感度分析用）。
    """
    rng = random.Random(seed)
    cl_ids = league.ids
    pl_mean, pl_sd = pl_prior(league)
    theta = {tid: 0.0 for tid in cl_ids}
    theta[PL] = pl_mean
    sigma = fixed_sigma if fixed_sigma is not None else sigma_median
    step = 0.06

    def posterior_at(th: dict[str, float], sg: float) -> float:
        return _log_prior(th, sg, cl_ids, sigma_median, pl_mean, pl_sd) + _log_likelihood(th, league)

    current = posterior_at(theta, sigma)
    samples: list[Draw] = []
    accepted = 0
    total = burn_in + draws * thin

    for it in range(total):
        cand = {tid: theta[tid] + rng.gauss(0.0, step) for tid in cl_ids}
        # セ6球団の平均を0に固定する（θの全体シフトは識別できないため）。
        mean = sum(cand.values()) / len(cand)
        for tid in cand:
            cand[tid] -= mean
        cand[PL] = theta[PL] + rng.gauss(0.0, step)
        if fixed_sigma is None:
            cand_sigma = sigma * math.exp(rng.gauss(0.0, 0.10))
            # 対数正規の提案なので、ヤコビアン log(cand_sigma/sigma) を補正に入れる。
            jacobian = math.log(cand_sigma / sigma)
        else:
            cand_sigma, jacobian = fixed_sigma, 0.0
        proposed = posterior_at(cand, cand_sigma) + jacobian
        if math.log(rng.random()) < proposed - current:
            theta, sigma, current = cand, cand_sigma, proposed
            accepted += 1
        if it >= burn_in and (it - burn_in) % thin == 0:
            samples.append(Draw(dict(theta), sigma))
        # バーンイン中だけ採択率25〜40%になるよう歩幅を調整する。
        if it < burn_in and it % 200 == 199:
            rate = accepted / (it + 1)
            if rate < 0.25:
                step *= 0.9
            elif rate > 0.40:
                step *= 1.1

    return Posterior(tuple(samples), accepted / total)
