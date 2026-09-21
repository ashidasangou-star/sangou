"""Windows 側でやるべき作業を決める。

GitHub Actions は情報を集めて events.jsonl に積むだけで、何を申し込むかは
決めない。決めるのはこのモジュールで、Windows 側の実行時に評価される。

**自動申込は明示的に指定された受付にしか行わない。** 検知した受付すべてに
申し込む設計にはしない。告知の解析を1つ誤っただけで、意図しない公演に
金を払うことになるため。対象は data/lpwatch/targets.json に人が書く。

ここは純粋な判定だけを持ち、ブラウザにもネットワークにも触らない。
Windows 実機がなくてもテストできる状態を保つ。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .model import DataError, Reception

# 先着受付で、開始の何分前に PC を起こすか。ブラウザの起動と
# ログインセッションの確認に要る時間を見込む。
WAKE_LEAD = timedelta(minutes=5)

# 申込を試みない残り時間。締切直前に走り出して途中で締め切られると、
# 申込済みか未申込かが分からない状態で終わる。
MIN_REMAINING = timedelta(minutes=2)


@dataclass(frozen=True)
class Target:
    """自動申込の対象。人が書く。"""

    match: str  # event_id かタイトルの一部
    quantity: int = 1
    max_price_jpy: int = 0  # 0 は上限なし。これを超える申込は止める
    note: str = ""

    def matches(self, r: Reception) -> bool:
        return self.match == r.event_id or (bool(self.match) and self.match in r.title)


@dataclass(frozen=True)
class Job:
    """Windows 側で実行する1件の作業。"""

    kind: str  # "apply"
    reception: Reception
    target: Target
    reason: str


def load_targets(root: Path | str) -> list[Target]:
    """targets.json を読む。無ければ空（＝自動申込しない）。"""
    path = Path(root) / "targets.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[Target] = []
    for i, row in enumerate(data.get("auto_apply", [])):
        if not isinstance(row, dict) or not row.get("match"):
            raise DataError(f"targets.json auto_apply[{i}]: match が必要")
        quantity = int(row.get("quantity", 1))
        if quantity < 1:
            raise DataError(f"targets.json auto_apply[{i}]: quantity は1以上")
        out.append(
            Target(
                match=str(row["match"]),
                quantity=quantity,
                max_price_jpy=int(row.get("max_price_jpy", 0)),
                note=str(row.get("note", "")),
            )
        )
    return out


def pending_jobs(
    events: dict[str, Reception],
    marks: dict[str, set[str]],
    targets: list[Target],
    now: datetime,
) -> list[Job]:
    """いま Windows で実行すべき申込を返す。

    一度でも applied / skipped / failed が付いたものは対象外。failed を
    自動で再試行しないのは、失敗の原因（セッション切れ・CAPTCHA・
    フォーム変更）がどれも人の確認を要するもので、機械的に叩き直すと
    重複申込や連続失敗によるアカウント停止を招くため。
    """
    jobs: list[Job] = []
    for key, r in events.items():
        got = marks.get(key, set())
        if got & {"applied", "skipped", "failed"}:
            continue

        target = next((t for t in targets if t.matches(r)), None)
        if target is None:
            continue

        opens, closes = r.opens(), r.closes()
        if opens is not None and now < opens:
            continue
        if closes is not None and closes - now < MIN_REMAINING:
            continue

        if r.reception_type == "first_come":
            reason = "先着受付が開始済み"
        elif closes is not None:
            reason = f"抽選受付中（締切 {closes:%m/%d %H:%M}）"
        else:
            reason = "受付中（締切不明）"
        jobs.append(Job(kind="apply", reception=r, target=target, reason=reason))

    # 先着を先に処理する。抽選は申込順が結果に影響しないが、先着は
    # 1秒でも早い方がよく、同時に複数の作業が溜まったときの順序が効く。
    jobs.sort(key=lambda j: (j.reception.reception_type != "first_come", j.reception.opens_at))
    return jobs


def wake_times(
    events: dict[str, Reception],
    marks: dict[str, set[str]],
    targets: list[Target],
    now: datetime,
) -> list[tuple[datetime, Reception]]:
    """PC を起こすべき時刻を (時刻, 対象) で返す。近い順。

    先着だけが時刻に厳しい。受付開始が事前に分かっているので、その直前に
    スリープ解除タイマーを仕掛ける。抽選は締切が数日先なので、定期の
    ポーリング窓で拾えれば足り、専用の起床は要らない。
    """
    out: list[tuple[datetime, Reception]] = []
    for key, r in events.items():
        if r.reception_type != "first_come":
            continue
        if marks.get(key, set()) & {"applied", "skipped", "failed"}:
            continue
        if not any(t.matches(r) for t in targets):
            continue
        opens = r.opens()
        if opens is None or opens <= now:
            continue
        out.append((opens - WAKE_LEAD, r))
    out.sort(key=lambda p: p[0])
    return out
