"""ログイン状態の確認と、初回ログインの受け渡し。

**パスワードを保存しないし、自動でログインもしない。** 自動ログインは
追加認証（SMS・メール確認・デバイス確認）を誘発し、失敗を重ねると
アカウント停止に繋がる。人が一度手で入り、そのセッションを
永続プロファイルが保持する、という形にする。

セッションが切れていたら、そこで止めて人に知らせる。切れたまま
申込処理に進むと、ログイン画面をフォームと誤認して操作しかねない。
"""

from __future__ import annotations

from dataclasses import dataclass

from .browser import SITES, open_context


@dataclass(frozen=True)
class SessionState:
    site: str
    logged_in: bool
    detail: str = ""


def check(site: str, *, timeout: float = 20000) -> SessionState:
    """ログイン済みかを確認する。"""
    conf = SITES[site]
    with open_context(site) as context:
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(conf.home_url, wait_until="domcontentloaded", timeout=timeout)
        except Exception as e:  # noqa: BLE001 - 到達できない理由は問わず未確認として扱う
            return SessionState(site, False, f"{conf.home_url} を開けない: {type(e).__name__}")
        return _judge(page, site, timeout)


def login(site: str, *, timeout_minutes: int = 10) -> SessionState:
    """ブラウザを開いて人にログインしてもらい、完了を待つ。

    コンソールで Enter を待つのではなく、ログイン済みの目印が現れるまで
    ポーリングする。人が別タブで認証を挟んでも追従できる。
    """
    conf = SITES[site]
    with open_context(site) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(conf.home_url, wait_until="domcontentloaded", timeout=60000)
        print(f"ブラウザで {conf.name} にログインしてください。")
        print(f"  完了を検出すると自動で閉じます（最大 {timeout_minutes} 分待ちます）")
        try:
            page.wait_for_selector(
                conf.logged_in_selector, timeout=timeout_minutes * 60_000
            )
        except Exception:  # noqa: BLE001
            return SessionState(site, False, "時間内にログインを確認できませんでした")
        return SessionState(site, True, "ログインを確認しました")


def _judge(page, site: str, timeout: float) -> SessionState:
    conf = SITES[site]
    # ログイン済みの目印を先に見る。両方の条件に当たる過渡状態では、
    # 「入れている」方を信じると未ログインのまま操作に進む危険がある。
    # そのため未ログインの目印を優先して否定側に倒す。
    try:
        page.wait_for_selector(
            f"{conf.logged_in_selector}, {conf.logged_out_selector}", timeout=timeout
        )
    except Exception:  # noqa: BLE001
        return SessionState(site, False, "ログイン状態を判定できませんでした")

    if page.query_selector(conf.logged_out_selector):
        return SessionState(site, False, "ログアウト状態です。再ログインが必要です")
    if page.query_selector(conf.logged_in_selector):
        return SessionState(site, True, "ログイン済み")
    return SessionState(site, False, "ログイン状態を判定できませんでした")
