"""通知の送出。

Webhook (Discord / Slack) と標準出力に対応する。依存ライブラリを足さない
方針なので urllib で直接叩く。

送信に失敗しても例外を上に投げない。通知が落ちたせいで収集全体が止まり、
以降の受付を取りこぼす方が損害が大きいため、失敗は戻り値で報告する。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .model import TYPE_LABEL, Reception


@dataclass(frozen=True)
class Message:
    reception: Reception
    reason: str

    def as_text(self) -> str:
        r = self.reception
        lines = [f"【{TYPE_LABEL[r.reception_type]}】{r.title or r.event_id}", self.reason]
        if r.opens_at:
            lines.append(f"受付開始: {_jp(r.opens_at)}")
        if r.closes_at:
            lines.append(f"締切:     {_jp(r.closes_at)}")
        else:
            lines.append("締切:     不明（要確認）")
        lines.append(r.url)
        if r.source and r.source != r.url:
            lines.append(f"出典: {r.source}")
        return "\n".join(lines)


def _jp(iso: str) -> str:
    from .model import _parse_iso

    dt = _parse_iso(iso)
    if dt is None:
        return iso
    week = "月火水木金土日"[dt.weekday()]
    return f"{dt.year}/{dt.month}/{dt.day}({week}) {dt.hour:02d}:{dt.minute:02d}"


def to_stdout(messages: list[Message]) -> None:
    for i, m in enumerate(messages):
        if i:
            print()
        print(m.as_text())


def to_webhook(url: str, messages: list[Message], *, timeout: float = 10.0) -> str:
    """Webhook に投げる。成功なら空文字、失敗なら理由を返す。

    Discord と Slack はどちらも {"content"|"text": ...} を受けるので
    両方のキーを入れて送る。受け取らない側は無視する。
    """
    if not messages:
        return ""
    body = "\n\n".join(m.as_text() for m in messages)
    payload = json.dumps({"content": body, "text": body}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            if res.status >= 300:
                return f"webhook が {res.status} を返した"
    except urllib.error.HTTPError as e:
        return f"webhook が {e.code} を返した"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return f"webhook に到達できない: {e}"
    return ""
