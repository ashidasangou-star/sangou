"""NPBペナントレースの優勝確率を、残り日程から直接計算する。

阪神がこの先どれだけの確率で優勝するかを出すのが目的。
やっていることは3段階しかない。

1. 各チームの「実力」をベイズ推定する（Bradley–Terry + 階層事前分布）
2. その事後分布から θ を引きながら、残り試合を1球団ずつ最後まで再生する
3. 順位決定規定に従って優勝チームを数え、頻度を確率として読む
"""

from .model import (
    DataError,
    Game,
    League,
    Team,
    apply_results,
    champion,
    contenders,
    load_league,
    max_possible_pct,
    min_possible_pct,
    pct,
)
from .simulate import LOSS, TIE, WIN, GameSplit, Result, simulate
from .strength import Posterior, fit, logistic

__all__ = [
    "DataError",
    "Game",
    "GameSplit",
    "League",
    "LOSS",
    "Posterior",
    "Result",
    "TIE",
    "Team",
    "WIN",
    "apply_results",
    "champion",
    "contenders",
    "fit",
    "load_league",
    "logistic",
    "max_possible_pct",
    "min_possible_pct",
    "pct",
    "simulate",
]
