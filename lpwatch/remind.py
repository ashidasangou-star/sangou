"""いつ通知するかを決める。

締切の何時間前に通知するかを段階で持ち、同じ段階を二度通知しない。
抽選は申込順が結果に影響しないため、検知した瞬間に急かす通知より
「締切前に確実に思い出させる」方が効く。一方で先着は開始時刻が本番なので、
受付開始前の通知を別枠で出す。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .model import Reception

# 締切の何時間前に通知するか。該当する中で最も短い段階を採る。
# 長い順に見ると、例えば残り20時間で初めて検知した受付が「72時間前の段階」を
# 消費してしまい、24時間前の通知が永久に来なくなる。
DEADLINE_STAGES = (72, 24, 3)

# 先着受付で、開始の何分前に通知するか。
OPENING_MINUTES = 15


def stage_mark(kind: str, value: int) -> str:
    return f"reminded:{kind}{value}"


def due(
    receptions: dict[str, Reception],
    marks: dict[str, set[str]],
    now: datetime,
) -> list[tuple[Reception, str, str]]:
    """通知すべきものを (受付, 付ける印, 理由) で返す。"""
    out: list[tuple[Reception, str, str]] = []
    for key, r in receptions.items():
        got = marks.get(key, set())
        if "applied" in got or "skipped" in got:
            continue

        if "notified" not in got:
            out.append((r, "notified", "新しい受付を検知"))
            continue

        opens = r.opens()
        if (
            r.reception_type == "first_come"
            and opens is not None
            and now < opens
            and opens - now <= timedelta(minutes=OPENING_MINUTES)
        ):
            mark = stage_mark("open", OPENING_MINUTES)
            if mark not in got:
                out.append((r, mark, f"先着受付が{OPENING_MINUTES}分後に開始"))
                continue

        closes = r.closes()
        if closes is None or now > closes:
            continue
        remaining = closes - now
        for hours in sorted(DEADLINE_STAGES):
            if remaining > timedelta(hours=hours):
                continue
            mark = stage_mark("close", hours)
            if mark not in got:
                out.append((r, mark, f"締切まで残り{_human(remaining)}"))
            break
    return out


def _human(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 3600:
        return f"{total // 60}分"
    if total < 86400:
        return f"{total // 3600}時間"
    return f"{total // 86400}日{total % 86400 // 3600}時間"
