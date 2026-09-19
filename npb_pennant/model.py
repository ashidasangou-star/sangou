"""セ・リーグの順位状況・残り日程・順位決定規定を表すデータモデル。

優勝確率の計算に必要なのは次の3つだけである。

1. 今の勝敗（勝・敗・分）
2. 残り試合の対戦カード（誰と何試合やるか）
3. 順位決定規定（同率のときどちらが上か）

NPBの順位は **勝率 = 勝 / (勝 + 敗)** で決まり、引き分けは分母に入らない。
つまり引き分けは「勝率を現状維持したまま残り試合を1つ減らす」効果を持つ。
勝率同率のときは (1) 勝利数 (2) 当該球団間の対戦成績 の順で上位を決める
（2023年以降のNPB順位決定規定）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DataError(ValueError):
    """入力データが不正なときに送出する。"""


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    w: int
    l: int
    t: int
    source: str = "estimated"

    @property
    def played(self) -> int:
        return self.w + self.l + self.t

    @property
    def pct(self) -> float:
        return pct(self.w, self.l)


@dataclass(frozen=True)
class Game:
    date: str
    home: str
    away: str
    source: str = "estimated"

    def involves(self, team_id: str) -> bool:
        return team_id in (self.home, self.away)


def pct(w: int, l: int) -> float:
    """勝率。引き分けは分母に入れない。1試合も決着していなければ .000 扱い。"""
    decided = w + l
    return w / decided if decided else 0.0


@dataclass(frozen=True)
class League:
    name: str
    season: int
    as_of: str
    games_per_season: int
    interleague_games_per_team: int
    teams: tuple[Team, ...]
    remaining: tuple[Game, ...]
    head_to_head: dict[tuple[str, str], tuple[int, int, int]]
    interleague: tuple[int, int, int]
    tie_rate: float
    home_advantage_logit: float

    def team(self, team_id: str) -> Team:
        for t in self.teams:
            if t.id == team_id:
                return t
        raise DataError(f"未知のチームID: {team_id}")

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(t.id for t in self.teams)

    def remaining_count(self, team_id: str) -> int:
        """公式日程上の残り試合数（143試合制から逆算）。"""
        return self.games_per_season - self.team(team_id).played

    def scheduled_count(self, team_id: str) -> int:
        """このデータで明示的にシミュレーション対象にしている残り試合数。"""
        return sum(1 for g in self.remaining if g.involves(team_id))

    def games_of(self, team_id: str) -> tuple[Game, ...]:
        return tuple(g for g in self.remaining if g.involves(team_id))

    def h2h(self, a: str, b: str) -> tuple[int, int, int]:
        """a から見た b との対戦成績 (勝, 敗, 分)。"""
        if (a, b) in self.head_to_head:
            return self.head_to_head[(a, b)]
        if (b, a) in self.head_to_head:
            w, l, t = self.head_to_head[(b, a)]
            return (l, w, t)
        return (0, 0, 0)


def load_league(path: str | Path) -> League:
    raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        teams = tuple(
            Team(t["id"], t["name"], int(t["w"]), int(t["l"]), int(t["t"]), t.get("source", "estimated"))
            for t in raw["teams"]
        )
        remaining = tuple(
            Game(g["date"], g["home"], g["away"], g.get("source", "estimated")) for g in raw["remaining"]
        )
        h2h = {
            (h["a"], h["b"]): (int(h["w"]), int(h["l"]), int(h["t"]))
            for h in raw.get("head_to_head", [])
        }
        il = raw["interleague"]
        league = League(
            name=raw["league"],
            season=int(raw["season"]),
            as_of=raw["as_of"],
            games_per_season=int(raw["games_per_season"]),
            interleague_games_per_team=int(raw["interleague_games_per_team"]),
            teams=teams,
            remaining=remaining,
            head_to_head=h2h,
            interleague=(int(il["cl_w"]), int(il["cl_l"]), int(il["cl_t"])),
            tie_rate=float(raw["tie_rate"]),
            home_advantage_logit=float(raw["home_advantage_logit"]),
        )
    except KeyError as exc:  # pragma: no cover - 壊れた入力の水際
        raise DataError(f"必須項目がない: {exc}") from exc
    validate(league)
    return league


def validate(league: League) -> None:
    ids = set(league.ids)
    if len(ids) != len(league.teams):
        raise DataError("チームIDが重複している")
    for t in league.teams:
        if t.played > league.games_per_season:
            raise DataError(f"{t.name}: 消化試合が{league.games_per_season}を超えている")
    for g in league.remaining:
        if g.home not in ids or g.away not in ids:
            raise DataError(f"未知のチームを含む日程: {g}")
        if g.home == g.away:
            raise DataError(f"同一チーム同士の日程: {g}")
    for t in league.teams:
        if league.scheduled_count(t.id) > league.remaining_count(t.id):
            raise DataError(
                f"{t.name}: 日程{league.scheduled_count(t.id)}試合 > 残り{league.remaining_count(t.id)}試合"
            )
    if not 0.0 <= league.tie_rate < 0.2:
        raise DataError("引き分け率が不正")


def max_possible_pct(team: Team, remaining: int) -> float:
    """残り全勝したときの最終勝率（上限）。"""
    return pct(team.w + remaining, team.l)


def min_possible_pct(team: Team, remaining: int) -> float:
    """残り全敗したときの最終勝率（下限）。"""
    return pct(team.w, team.l + remaining)


def contenders(league: League) -> tuple[str, ...]:
    """数学的に優勝の可能性が残っているチーム。

    「他のどのチームの下限勝率も上回れないなら脱落」という判定。
    引き分けで勝率が上下しない点まで含めて、上限・下限で厳密に挟む。
    """
    floors = {t.id: min_possible_pct(t, league.remaining_count(t.id)) for t in league.teams}
    best_floor = max(floors.values())
    alive = []
    for t in league.teams:
        ceiling = max_possible_pct(t, league.remaining_count(t.id))
        # 上限が誰かの下限を下回るなら、どう転んでも1位になれない。
        # 同率のときは同率決着の可能性が残るので脱落とはみなさない。
        if ceiling >= best_floor:
            alive.append(t.id)
    return tuple(alive)


def rank_key(w: int, l: int, wins: int) -> tuple[float, int]:
    """勝率 → 勝利数 の順に比較するためのキー（大きいほど上位）。"""
    return (pct(w, l), wins)


def champion(
    finals: dict[str, tuple[int, int, int]],
    h2h: dict[tuple[str, str], tuple[int, int, int]],
) -> str:
    """最終成績から優勝チームを決める。

    finals: team_id -> (勝, 敗, 分)
    同率の場合は (1)勝利数 (2)当該球団間の対戦成績 で決める。
    そこまで並んだ場合は決定戦（プレーオフ）だが、確率的には無視できるので
    IDの辞書順で決め打ちする（実務上は起こらない）。
    """
    best: list[str] = []
    best_key: tuple[float, int] | None = None
    for tid, (w, l, _t) in finals.items():
        key = rank_key(w, l, w)
        if best_key is None or key > best_key:
            best_key, best = key, [tid]
        elif key == best_key:
            best.append(tid)
    if len(best) == 1:
        return best[0]
    # 当該球団間の対戦成績で比較する。
    def h2h_score(tid: str) -> tuple[int, str]:
        score = 0
        for other in best:
            if other == tid:
                continue
            w, l, _ = h2h.get((tid, other), (0, 0, 0))
            score += w - l
        return (score, tid)

    return max(best, key=h2h_score)


def apply_results(league: League, outcomes: str, focus: str = "T") -> League:
    """注目チームの残り試合に、先頭から順に結果を当てはめた League を返す。

    outcomes は "WLTW" のような文字列（W=勝, L=敗, T=分, D=分でも可）。
    試合が終わるたびに確率を引き直すための入口。相手チームの勝敗も同時に
    更新し、消化した試合は日程から取り除く。
    """
    table = {"W": "W", "L": "L", "T": "T", "D": "T", "勝": "W", "敗": "L", "分": "T"}
    seq = [table[c] for c in outcomes.strip() if not c.isspace()]
    games = list(league.games_of(focus))
    if len(seq) > len(games):
        raise DataError(f"{len(seq)}試合ぶんの結果を渡されたが、残りは{len(games)}試合しかない")

    records = {t.id: [t.w, t.l, t.t] for t in league.teams}
    h2h = dict(league.head_to_head)
    played: set[int] = set()
    for res, game in zip(seq, games):
        opp = game.away if game.home == focus else game.home
        if res == "T":
            records[focus][2] += 1
            records[opp][2] += 1
            delta = (0, 0, 1)
        elif res == "W":
            records[focus][0] += 1
            records[opp][1] += 1
            delta = (1, 0, 0)
        else:
            records[focus][1] += 1
            records[opp][0] += 1
            delta = (0, 1, 0)
        key = (focus, opp) if (focus, opp) in h2h else (opp, focus)
        if key in h2h:
            w, l, t = h2h[key]
            dw, dl, dt = delta if key[0] == focus else (delta[1], delta[0], delta[2])
            h2h[key] = (w + dw, l + dl, t + dt)
        played.add(id(game))

    teams = tuple(
        Team(t.id, t.name, records[t.id][0], records[t.id][1], records[t.id][2], t.source)
        for t in league.teams
    )
    remaining = tuple(g for g in league.remaining if id(g) not in played)
    updated = League(
        name=league.name,
        season=league.season,
        as_of=league.as_of,
        games_per_season=league.games_per_season,
        interleague_games_per_team=league.interleague_games_per_team,
        teams=teams,
        remaining=remaining,
        head_to_head=h2h,
        interleague=league.interleague,
        tie_rate=league.tie_rate,
        home_advantage_logit=league.home_advantage_logit,
    )
    validate(updated)
    return updated
