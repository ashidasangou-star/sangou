"""検知した受付の記録。

pokebox_ev の履歴と同じく、1行1観測のJSONL追記専用で持つ。同じ受付を
何度検知しても追記するだけで、過去の行は書き換えない。あとから
「この締切をいつ・どこで拾ったか」を検証できる状態を保つ。

読み出すときは event_id + 受付種別ごとに時系列でマージして、
最新の像を1件に畳む。空欄は既知の値を消さない（model.Reception.merge）。

申込済み・見送りといった状態は marks.jsonl に別で積む。受付の事実
（外から観測したもの）と、自分がそれに何をしたか（自分の行動）は
性質が違うので、混ぜない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .model import Reception, now_jst

# 自分の行動。notified = 通知済み、applied = 申込済み、skipped = 見送り。
MARKS = ("notified", "applied", "skipped", "failed")


@dataclass(frozen=True)
class Mark:
    key: str  # Reception.key
    mark: str
    at: str
    note: str = ""


def events_path(root: Path | str) -> Path:
    return Path(root) / "events.jsonl"


def marks_path(root: Path | str) -> Path:
    return Path(root) / "marks.jsonl"


def _append_lines(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_events(root: Path | str) -> dict[str, Reception]:
    """key -> 最新状態にマージ済みの Reception。"""
    path = events_path(root)
    if not path.exists():
        return {}
    out: dict[str, Reception] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = Reception.from_dict(json.loads(line))
        out[r.key] = out[r.key].merge(r) if r.key in out else r
    return out


def append_events(root: Path | str, receptions: list[Reception]) -> list[Reception]:
    """新しい情報を持つ観測だけを追記し、追記したものを返す。

    完全に既知の内容（全フィールドが既存とマージしても変わらない）は
    書かない。毎回のポーリングで同じ行が積み上がると、履歴が
    「何が新しく分かったか」を語らなくなるため。
    """
    known = load_events(root)
    fresh: list[Reception] = []
    for r in receptions:
        prev = known.get(r.key)
        if prev is not None and prev.merge(r) == prev:
            continue
        known[r.key] = prev.merge(r) if prev else r
        fresh.append(r)
    if fresh:
        _append_lines(events_path(root), [r.to_dict() for r in fresh])
    return fresh


def load_marks(root: Path | str) -> dict[str, set[str]]:
    """key -> 付いている印の集合。"""
    path = marks_path(root)
    if not path.exists():
        return {}
    out: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.setdefault(d["key"], set()).add(d["mark"])
    return out


def add_mark(root: Path | str, key: str, mark: str, note: str = "") -> Mark:
    m = Mark(key=key, mark=mark, at=now_jst().isoformat(), note=note)
    _append_lines(marks_path(root), [m.__dict__])
    return m
