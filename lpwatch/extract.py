"""告知テキストから受付情報を取り出す。

X の投稿本文でも LivePocket のページ本文でも同じ関数で処理する。
どちらも「日本語の告知文」という点では同じ形をしているため、
収集経路ごとに抽出器を分けると同じ正規表現が二重管理になる。

方針は「読めたものだけ返す」。日付の年が書かれていない告知
（「10/5(日) 23:59まで」が圧倒的多数）は基準日から推定するが、
推定が破綻する入力では空を返し、誤った締切を作らない。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .model import JST, Reception

# LivePocket のイベントURL。受付ページは /e/<id> 形式。
_URL_RE = re.compile(r"https?://t\.livepocket\.jp/e/([0-9A-Za-z_-]+)")

# 受付種別を示す語。先着の判定を優先する（「先行抽選のあと一般先着」の
# ような複合告知では、締切が近い先着側を見落とす方が損害が大きい）。
_FIRST_COME_WORDS = ("先着", "一般発売", "即時販売", "先着順")
_LOTTERY_WORDS = ("抽選", "先行抽選", "抽選申込", "抽選受付", "プレオーダー")

# 受付期間の区切り。全角チルダ・波ダッシュ・ハイフンが混在する。
_RANGE_SEP = r"[〜～~\-−–—]"

# 「2026年10月5日(日) 23:59」「10/5(日)23:59」「10月5日 12時」
_DATE_RE = re.compile(
    r"(?:(?P<year>20\d{2})\s*[年/.\-]\s*)?"
    r"(?P<month>1[0-2]|0?[1-9])\s*[月/.\-]\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])\s*日?"
    r"(?:\s*[（(][^）)]{0,3}[）)])?"
    r"(?:\s*(?P<hour>2[0-4]|[01]?\d)\s*[:：時]\s*(?P<minute>[0-5]?\d)?\s*分?)?"
)


def find_event_urls(text: str) -> list[tuple[str, str]]:
    """本文中の LivePocket イベントURLを (event_id, url) で返す。出現順・重複排除。"""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _URL_RE.finditer(text):
        event_id = m.group(1)
        if event_id not in seen:
            seen.add(event_id)
            found.append((event_id, m.group(0)))
    return found


def detect_reception_type(text: str) -> str:
    """本文から受付種別を判定する。

    抽選と先着の両方が書かれている告知では先着を返す。抽選は申込順が
    結果に影響しないため取りこぼしの損害が小さいが、先着は締切ではなく
    開始時刻に張り付く必要があり、見落とすと申し込めない。
    """
    has_first_come = any(w in text for w in _FIRST_COME_WORDS)
    has_lottery = any(w in text for w in _LOTTERY_WORDS)
    if has_first_come:
        return "first_come"
    if has_lottery:
        return "lottery"
    return "unknown"


def _build(
    m: re.Match[str], ref: datetime, *, end_of_day: bool
) -> datetime | None:
    """正規表現のマッチ1件を datetime にする。年が無ければ ref から推定する。"""
    month, day = int(m.group("month")), int(m.group("day"))
    hour_s, minute_s = m.group("hour"), m.group("minute")

    if hour_s is None:
        # 時刻が書かれていない。締切側は「その日いっぱい」、
        # 開始側は「その日の0時」と解釈するのが告知の慣習に合う。
        hour, minute = (23, 59) if end_of_day else (0, 0)
    else:
        hour, minute = int(hour_s), int(minute_s or 0)

    # 「24:00」は翌日0時の意味で使われる。
    rollover = hour == 24
    if rollover:
        hour = 0

    year_s = m.group("year")
    if year_s is not None:
        year = int(year_s)
    else:
        year = _infer_year(month, day, ref)
        if year is None:
            return None

    try:
        dt = datetime(year, month, day, hour, minute, tzinfo=JST)
    except ValueError:
        return None  # 2月30日などの実在しない日付
    return dt + timedelta(days=1) if rollover else dt


def _infer_year(month: int, day: int, ref: datetime) -> int | None:
    """年が省略された日付の年を推定する。

    告知は原則これから先の日付を指すので、基準日以降で最も近い年を採る。
    ただし基準日より 1 日以上前になる候補しか作れない場合は推定を諦める
    （過去の告知を拾ったときに、翌年の日付をでっち上げないため）。
    """
    for year in (ref.year, ref.year + 1):
        try:
            cand = datetime(year, month, day, tzinfo=JST)
        except ValueError:
            continue
        if cand >= ref - timedelta(days=1):
            return year
    return None


def parse_period(text: str, ref: datetime) -> tuple[str, str]:
    """受付期間を (開始, 終了) の ISO8601 で返す。読めなければ空文字。

    ref は年の推定に使う基準日（通常は検知時刻）。
    """
    # まず「A 〜 B」の範囲表記を探す。これが最も信頼できる。
    for m in _DATE_RE.finditer(text):
        tail = text[m.end() : m.end() + 12]
        sep = re.match(rf"\s*{_RANGE_SEP}\s*", tail)
        if not sep:
            continue
        rest = text[m.end() + sep.end() :]
        m2 = _DATE_RE.match(rest.lstrip())
        if not m2:
            continue
        start = _build(m, ref, end_of_day=False)
        end = _build(m2, ref, end_of_day=True)
        if start and end and end >= start:
            return start.isoformat(), end.isoformat()

    # 範囲が取れない場合、「〜10/5 23:59まで」のような締切単独表記を探す。
    deadline = re.search(rf"{_RANGE_SEP}?\s*({_DATE_RE.pattern})\s*(?:まで|締切|〆切)", text)
    if deadline:
        m = _DATE_RE.match(deadline.group(1))
        if m:
            end = _build(m, ref, end_of_day=True)
            if end:
                return "", end.isoformat()

    return "", ""


def extract(text: str, *, source: str = "", ref: datetime | None = None) -> list[Reception]:
    """告知テキストから Reception を組み立てる。

    1つの投稿に複数のイベントURLが並ぶこと（ツアー全公演の一括告知）が
    あるため、URL ごとに1件返す。受付種別と期間は本文全体から取るので、
    複数公演の告知では同じ期間が入る。実際それで正しいことが多い。
    """
    ref = ref or datetime.now(JST)
    urls = find_event_urls(text)
    if not urls:
        return []

    reception_type = detect_reception_type(text)
    opens_at, closes_at = parse_period(text, ref)
    found_at = ref.isoformat()

    return [
        Reception(
            event_id=event_id,
            title=_guess_title(text),
            url=url,
            reception_type=reception_type,
            opens_at=opens_at,
            closes_at=closes_at,
            source=source,
            found_at=found_at,
        )
        for event_id, url in urls
    ]


def _guess_title(text: str) -> str:
    """本文の1行目をタイトルの当たりとして使う。

    正確なタイトルは LivePocket 本体から取り直す前提なので、ここでは
    通知に出したときに人が識別できる程度の文字列が取れればよい。
    """
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("http", "#", "@")):
            return re.sub(r"\s+", " ", line)[:80]
    return ""
