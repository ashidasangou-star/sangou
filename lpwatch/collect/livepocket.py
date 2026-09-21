"""LivePocket の公開ページから受付情報を取り直す。

ログイン不要なページしか触らないので、この経路は GitHub Actions で回せる。
依存ライブラリを増やさないため urllib で取得する。

**HTML の構造に依存した抽出はしない。** タグを落として本文テキストにし、
X の投稿と同じパーサ（extract.parse_period / detect_reception_type）に
食わせる。理由は2つある。

1. 告知の本文は投稿もページも同じ日本語の書式で、すでにテストのある
   パーサが使える。経路ごとに正規表現を二重管理しない。
2. サイトの DOM 構造が変わっても壊れない。CSSセレクタに依存すると、
   改装のたびに黙って締切を取りこぼす。

URL の探索は経由しない。タグを落とすと href も消えるうえ、どのイベントの
ページかは取得した URL 自身が既に知っているため。

取得した情報は X 経由で先に拾った記録に上書きマージされる
(model.Reception.merge)。空欄で既知の値を消さないので、
ページから読めなかった項目は投稿から拾った値が残る。
"""

from __future__ import annotations

import gzip
import html
import re
import urllib.error
import urllib.request
from datetime import datetime

from .. import extract
from ..model import Reception, now_jst

EVENT_URL = "https://t.livepocket.jp/e/{event_id}"

# 実在のブラウザに寄せる。既定の python-urllib/x.y は弾かれることがある。
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 本文に出てこないタグごと落とす要素。
_DROP_RE = re.compile(
    r"<(script|style|noscript|svg|head)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<(br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>", re.IGNORECASE)


class FetchError(RuntimeError):
    """ページを取得できなかった。"""


def to_text(markup: str) -> str:
    """HTML を本文テキストにする。改行を伴う要素は改行として残す。

    改行を潰すと「受付期間: 10/1 12:00」と次の見出しが1行に連結され、
    日付の正規表現が隣接する別の数値を巻き込む。区切りの維持が要る。
    """
    s = _DROP_RE.sub(" ", markup)
    s = _BR_RE.sub("\n", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t　]+", " ", s)
    # 改行の前後に残った空白を落とす。<p> が空白に、</p> が改行になるため
    # 「\n 本文」の形になり、そのままでは本文の行頭が揃わない。
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def fetch(url: str, *, timeout: float = 20.0) -> str:
    """ページの HTML を取得する。"""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept-Language": "ja,en;q=0.8",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
            if res.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            charset = res.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        raise FetchError(f"{url} が {e.code} を返した") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(f"{url} に到達できない: {e}") from e
    return raw.decode(charset, errors="replace")


def parse_page(markup: str, url: str, *, ref: datetime | None = None) -> Reception | None:
    """イベントページの HTML から Reception を1件作る。

    どのイベントのページかは url が知っているので、本文から URL を
    探さない。本文から読むのは受付種別と期間、タイトルだけ。

    何も読み取れなかったページでは None を返す。公演前で内容が
    まだ無いページを、空の受付として記録しないため。
    """
    ref = ref or now_jst()
    text = to_text(markup)

    opens_at, closes_at = extract.parse_period(text, ref)
    reception_type = extract.detect_reception_type(text)
    title = _page_title(markup)

    if not (opens_at or closes_at or title):
        return None

    return Reception(
        event_id=_event_id(url),
        title=title,
        url=url,
        reception_type=reception_type,
        opens_at=opens_at,
        closes_at=closes_at,
        source=url,
        found_at=ref.isoformat(),
    )


def _event_id(url: str) -> str:
    m = re.search(r"/e/([0-9A-Za-z_-]+)", url)
    return m.group(1) if m else url


def _page_title(markup: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", markup, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    title = html.unescape(_TAG_RE.sub("", m.group(1))).strip()
    # 「イベント名 | LivePocket-Ticket-」のサイト名部分を落とす
    return re.sub(r"\s*[|｜]\s*LivePocket.*$", "", title)[:80]


class LivePocketCollector:
    """既知のイベントの情報を公開ページから取り直す。

    新規イベントの発見はしない。それは X 側の仕事で、この経路は
    「URL は分かっているが締切が読めていない」受付を埋めるために使う。
    ログイン不要なので Actions で回せる。
    """

    name = "livepocket"

    def __init__(self, event_ids: list[str], *, ref: datetime | None = None) -> None:
        self.event_ids = event_ids
        self.ref = ref

    def collect(self) -> list[Reception]:
        out: list[Reception] = []
        for event_id in self.event_ids:
            url = EVENT_URL.format(event_id=event_id)
            try:
                markup = fetch(url)
            except FetchError:
                # 1件の取得失敗で全体を止めない。次回のポーリングで拾い直す。
                continue
            r = parse_page(markup, url, ref=self.ref)
            if r is not None:
                out.append(r)
        return out
