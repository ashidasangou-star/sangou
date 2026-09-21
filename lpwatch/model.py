"""抽選・先着受付の情報を表すデータモデル。

LivePocket の「受付」1件 = Reception 1件として持つ。同じ公演でも
「先行抽選」と「一般先着」は申込URLも締切も別なので、別レコードにする。

このツールが出したい答えは「いつまでに何を申し込むか」なので、
締切 (closes_at) が最重要フィールドになる。読み取れなかった項目は
空のまま持ち、推測で埋めない。締切を1つ誤ると通知が丸ごと無意味になる。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# LivePocket は日本国内向けなので、日時はすべて JST で解釈する。
JST = timezone(timedelta(hours=9))

# 受付種別。lottery = 抽選（申込順は結果に影響しない）、
# first_come = 先着（速さが結果を左右する）、unknown = 判別できなかった。
RECEPTION_TYPES = ("lottery", "first_come", "unknown")

TYPE_LABEL = {"lottery": "抽選", "first_come": "先着", "unknown": "種別不明"}


class DataError(ValueError):
    """受付データが不正なときに送出する。"""


@dataclass(frozen=True)
class Reception:
    """ある公演の、ある受付枠。"""

    event_id: str  # LivePocket の URL 末尾。同一公演を束ねるキー
    title: str
    url: str
    reception_type: str = "unknown"
    opens_at: str = ""  # 受付開始 ISO8601 (+09:00)
    closes_at: str = ""  # 受付終了 ISO8601 (+09:00)
    performer: str = ""
    venue: str = ""
    performed_on: str = ""  # 公演日 YYYY-MM-DD
    source: str = ""  # この情報をどこで拾ったか（投稿URL等）
    found_at: str = ""  # 検知時刻 ISO8601

    def __post_init__(self) -> None:
        if not self.event_id:
            raise DataError("event_id が空")
        if self.reception_type not in RECEPTION_TYPES:
            raise DataError(f"未知の受付種別: {self.reception_type}")

    @property
    def key(self) -> str:
        """重複排除のキー。同じイベントでも受付種別が違えば別物として扱う。"""
        return f"{self.event_id}:{self.reception_type}"

    def closes(self) -> datetime | None:
        return _parse_iso(self.closes_at)

    def opens(self) -> datetime | None:
        return _parse_iso(self.opens_at)

    def has_ended(self, now: datetime) -> bool:
        """締切を過ぎたか。締切が不明なものは終了と見なさない。

        一覧の既定はこちらで絞る。受付開始前のものこそ一番見たい対象なので、
        「今まさに申し込めるか (is_open)」で絞ると、これから始まる受付が
        丸ごと画面から消えてしまう。
        """
        c = self.closes()
        return c is not None and now > c

    def is_open(self, now: datetime) -> bool:
        """now の時点で申込可能か。開始・終了が不明な側は「開いている」と見なす。"""
        o, c = self.opens(), self.closes()
        if o is not None and now < o:
            return False
        if c is not None and now > c:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Reception:
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)

    def merge(self, other: Reception) -> Reception:
        """同じ受付の新しい観測を取り込む。

        後から読んだ方を優先するが、空欄で既知の値を消さない。実運用では
        X の投稿で URL だけ先に拾い、あとで LivePocket 本体から締切を
        取り直す、という順序になるため、この片方向マージが要る。
        """
        if other.key != self.key:
            raise DataError(f"別の受付はマージできない: {self.key} / {other.key}")
        merged = self.to_dict()
        for field, value in other.to_dict().items():
            if value:
                merged[field] = value
        return Reception.from_dict(merged)


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=JST) if dt.tzinfo is None else dt


def now_jst() -> datetime:
    return datetime.now(JST)
