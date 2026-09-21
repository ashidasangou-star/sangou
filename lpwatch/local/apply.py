"""LivePocket の申込操作。

**このモジュールの既定は「止まる」。** 想定した画面でなければ進まず、
判定できなければ進まず、金額が読めなければ進まない。自動申込の失敗で
最も高くつくのは「意図しない公演に金を払う」ことで、次に高くつくのが
「申し込めたのか分からない」状態なので、迷ったら止める側に倒す。

作らないもの:
  - CAPTCHA の突破。検出したら中断して人に渡す
  - 自動ログイン。セッションは session.py が確認するだけ
  - 複数アカウントでの重複申込

セレクタは data/lpwatch/selectors.json に外出ししてある。LivePocket の
申込フォームは主催者ごとに項目が違い、コードに焼き込むと他の公演で
黙って誤動作するため。実際のページを見て較正すること。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..agenda import Job
from ..model import now_jst
from .browser import SITES, open_context, profiles_root

# CAPTCHA・ボット検知の画面に出る印。1つでも当たれば中断する。
_CAPTCHA_MARKS = (
    "recaptcha",
    "g-recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "画像認証",
    "ロボットではありません",
    "不正なリクエスト",
)

_PRICE_RE = re.compile(r"([0-9][0-9,]*)\s*円")

# 申込の結果。applied 以外はすべて人の確認を要する。
OUTCOMES = ("applied", "dry_run", "captcha", "closed", "over_budget", "failed")


@dataclass(frozen=True)
class ApplyResult:
    outcome: str
    detail: str
    price_jpy: int | None = None
    evidence: list[str] = field(default_factory=list)  # 保存した証跡のパス

    @property
    def mark(self) -> str:
        """store に付ける印。成功以外は failed にして自動再試行させない。"""
        return "applied" if self.outcome == "applied" else "failed"


def load_selectors(root: Path | str) -> dict[str, str]:
    path = Path(root) / "selectors.json"
    if not path.exists():
        return dict(DEFAULT_SELECTORS)
    data = json.loads(path.read_text(encoding="utf-8"))
    merged = dict(DEFAULT_SELECTORS)
    merged.update({k: v for k, v in data.items() if isinstance(v, str)})
    return merged


DEFAULT_SELECTORS = {
    # 申込ページで最初に押すボタン
    "entry_button": 'a:has-text("申し込む"), button:has-text("申し込む")',
    # 枚数の選択
    "quantity_select": 'select[name*="quantity"], select[name*="count"]',
    # 規約同意
    "agree_checkbox": 'input[type="checkbox"][name*="agree"]',
    # 確認画面へ進む
    "confirm_button": 'button:has-text("確認"), a:has-text("確認画面")',
    # 最終送信。ここを押すと申込が確定する
    "submit_button": 'button:has-text("申込を確定"), button:has-text("確定する")',
    # 申込完了の目印
    "done_marker": 'text=/申込.*完了|受付.*完了/',
    # 受付終了・売切の目印
    "closed_marker": 'text=/受付終了|受付は終了|販売終了|完売/',
}


def detect_captcha(markup: str) -> bool:
    """ページに CAPTCHA・ボット検知が出ているか。"""
    low = markup.lower()
    return any(mark.lower() in low for mark in _CAPTCHA_MARKS)


def parse_price(text: str) -> int | None:
    """本文から申込金額を読む。複数あれば最大を採る。

    最大を採るのは、ページに「1枚 5,000円」と「合計 10,000円」が
    並ぶときに、上限判定を合計側で行うため。少ない方で判定すると
    上限を超えた申込を通してしまう。
    """
    values = [int(m.group(1).replace(",", "")) for m in _PRICE_RE.finditer(text)]
    return max(values) if values else None


def check_budget(price: int | None, max_price_jpy: int) -> tuple[bool, str]:
    """金額が上限内か。判定できないときは通さない。"""
    if max_price_jpy <= 0:
        return True, "上限指定なし"
    if price is None:
        # 上限を設定しているのに金額が読めない = 較正が合っていない。
        # ここを通すと上限の意味が無くなる。
        return False, "金額を読み取れないため中断（上限が設定されています）"
    if price > max_price_jpy:
        return False, f"金額 {price:,}円 が上限 {max_price_jpy:,}円 を超える"
    return True, f"金額 {price:,}円（上限 {max_price_jpy:,}円）"


def _evidence_dir() -> Path:
    """証跡の保存先。リポジトリ外に置く。

    ログイン済みのページのスクリーンショットには氏名・住所・決済手段が
    写る。git の管理下に置くと、そのままリモートへ出る。
    """
    return profiles_root().parent / "evidence"


def _save_evidence(page, tag: str) -> list[str]:
    stamp = now_jst().strftime("%Y%m%d-%H%M%S")
    out_dir = _evidence_dir() / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    shot = out_dir / f"{tag}.png"
    try:
        page.screenshot(path=str(shot), full_page=True)
        saved.append(str(shot))
    except Exception:  # noqa: BLE001
        pass
    dump = out_dir / f"{tag}.html"
    try:
        dump.write_text(page.content(), encoding="utf-8")
        saved.append(str(dump))
    except Exception:  # noqa: BLE001
        pass
    return saved


def apply(job: Job, selectors: dict[str, str], *, dry_run: bool = True) -> ApplyResult:
    """1件の申込を実行する。

    dry_run が True のときは最終送信の直前まで進んで止まる。既定を True に
    してあるのは、セレクタの較正が済んでいない状態で呼ばれても金が動かない
    ようにするため。実際に申し込むときは明示的に False を渡す。
    """
    r = job.reception
    with open_context("livepocket") as context:
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(r.url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:  # noqa: BLE001
            return ApplyResult("failed", f"申込ページを開けない: {type(e).__name__}")

        markup = page.content()
        if detect_captcha(markup):
            return ApplyResult(
                "captcha",
                "CAPTCHA/ボット検知が出ました。手動で申し込んでください",
                evidence=_save_evidence(page, "captcha"),
            )

        if page.query_selector(selectors["closed_marker"]):
            return ApplyResult("closed", "受付が終了しています")

        body = page.inner_text("body")
        price = parse_price(body)
        ok, detail = check_budget(price, job.target.max_price_jpy)
        if not ok:
            return ApplyResult(
                "over_budget", detail, price_jpy=price,
                evidence=_save_evidence(page, "budget"),
            )

        steps = [
            ("entry_button", "click"),
            ("quantity_select", "select"),
            ("agree_checkbox", "check"),
            ("confirm_button", "click"),
        ]
        for name, action in steps:
            result = _do_step(page, selectors[name], action, job)
            if result is not None:
                return result
            if detect_captcha(page.content()):
                return ApplyResult(
                    "captcha",
                    f"{name} の後に CAPTCHA が出ました。手動で申し込んでください",
                    price_jpy=price,
                    evidence=_save_evidence(page, "captcha"),
                )

        if dry_run:
            return ApplyResult(
                "dry_run",
                f"送信の直前まで到達（{detail}）。--execute で実際に申し込みます",
                price_jpy=price,
                evidence=_save_evidence(page, "dry-run"),
            )

        submit = page.query_selector(selectors["submit_button"])
        if submit is None:
            return ApplyResult(
                "failed", "送信ボタンが見つかりません。セレクタの較正が必要です",
                price_jpy=price, evidence=_save_evidence(page, "no-submit"),
            )
        submit.click()

        try:
            page.wait_for_selector(selectors["done_marker"], timeout=30000)
        except Exception:  # noqa: BLE001
            # 押したが完了を確認できない。最も危険な状態なので、
            # 申込済みとも未申込とも断定せず、人に確認させる。
            return ApplyResult(
                "failed",
                "送信しましたが完了画面を確認できませんでした。"
                "LivePocketのマイページで申込状況を確認してください",
                price_jpy=price,
                evidence=_save_evidence(page, "unconfirmed"),
            )

        return ApplyResult(
            "applied", f"申込完了（{detail}）", price_jpy=price,
            evidence=_save_evidence(page, "done"),
        )


def _do_step(page, selector: str, action: str, job: Job) -> ApplyResult | None:
    """1操作。成功なら None、失敗なら ApplyResult を返す。

    要素が無い操作は飛ばす。LivePocket の申込フォームは公演によって
    枚数選択や規約同意が無いことがあり、無いことを失敗にすると
    正常なページで止まってしまう。押せないと困るのは送信ボタンだけで、
    そこは呼び出し側が別に検査している。
    """
    element = page.query_selector(selector)
    if element is None:
        return None
    try:
        if action == "click":
            element.click()
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        elif action == "select":
            element.select_option(str(job.target.quantity))
        elif action == "check":
            if not element.is_checked():
                element.check()
    except Exception as e:  # noqa: BLE001
        return ApplyResult(
            "failed",
            f"操作に失敗しました（{action} on {selector}）: {type(e).__name__}",
            evidence=_save_evidence(page, "step-failed"),
        )
    return None
