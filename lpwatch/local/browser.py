"""ブラウザの永続プロファイル管理。

一度ログインしたセッションを使い回すため、Playwright の
launch_persistent_context を使う。プロファイルは **リポジトリの外**、
OS のユーザーローカル領域に置く。Cookie と認証トークンが入るため、
git の管理下に入る場所には絶対に作らない。

ヘッドレスでは動かさない。headless は User-Agent 以外にも多数の差異で
検出され、ログインセッションの維持と相性が悪い。人が普段使っている
ブラウザと同じ見え方にするのが、結局いちばん安定する。
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# 対応サイト。ログイン済みかどうかの判定材料をここに集める。
@dataclass(frozen=True)
class Site:
    name: str
    home_url: str
    # このセレクタが在ればログイン済みと見なす
    logged_in_selector: str
    # このセレクタが在ればログアウト状態と見なす
    logged_out_selector: str


SITES = {
    "x": Site(
        name="x",
        home_url="https://x.com/home",
        logged_in_selector='[data-testid="SideNav_AccountSwitcher_Button"]',
        logged_out_selector='a[href="/login"], [data-testid="loginButton"]',
    ),
    "livepocket": Site(
        name="livepocket",
        home_url="https://t.livepocket.jp/mypage",
        logged_in_selector='a[href*="logout"], a[href*="mypage"]',
        logged_out_selector='input[type="password"], a[href*="login"]',
    ),
}


class PlaywrightMissing(RuntimeError):
    """Playwright が入っていない。"""


class ProfileLocked(RuntimeError):
    """同じプロファイルのブラウザが既に開いている。"""


def profiles_root() -> Path:
    """プロファイルの置き場所。リポジトリ外のユーザーローカル領域。"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "lpwatch" / "profiles"


def profile_dir(site: str) -> Path:
    if site not in SITES:
        raise ValueError(f"未知のサイト: {site}")
    return profiles_root() / site


def _lock_path(site: str) -> Path:
    # Chromium はプロファイル使用中に SingletonLock を作る。
    return profile_dir(site) / "SingletonLock"


def is_locked(site: str) -> bool:
    """同じプロファイルのブラウザが開いているか。

    開いたまま起動すると Playwright は起動に失敗するが、その例外は
    原因が読み取りにくい。先に見て、人が理解できる文言で止める。
    """
    lock = _lock_path(site)
    if not lock.exists() and not lock.is_symlink():
        return False
    if sys.platform == "win32":
        return True
    # Linux/macOS では中身が <host>-<pid> のシンボリックリンク。
    try:
        pid = int(str(os.readlink(lock)).rsplit("-", 1)[-1])
    except (OSError, ValueError):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # 前回が異常終了して残った錠。無視してよい
    except PermissionError:
        return True
    return True


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise PlaywrightMissing(
            "Playwright が入っていません。Windows PC で次を実行してください:\n"
            "  py -m pip install playwright\n"
            "  py -m playwright install chromium"
        ) from e
    return sync_playwright


@contextmanager
def open_context(site: str, *, headless: bool = False, slow_mo: int = 0) -> Iterator:
    """永続プロファイルでブラウザを開く。

    headless は既定で False。テスト用に切り替えられるようにはしてあるが、
    実運用で True にするとログインが維持できなくなる可能性が高い。
    """
    if is_locked(site):
        raise ProfileLocked(
            f"{site} のプロファイルが使用中です。\n"
            f"lpwatch が開いた Chromium のウィンドウを閉じてから再実行してください。\n"
            f"  プロファイル: {profile_dir(site)}"
        )

    sync_playwright = _playwright()
    user_data_dir = profile_dir(site)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(user_data_dir),
            headless=headless,
            slow_mo=slow_mo,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            yield context
        finally:
            context.close()
