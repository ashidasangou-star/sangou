"""収集経路。

どの経路も「テキストを持ってきて extract に渡す」という同じ形に
落とす。X をブラウザで見るのか、LivePocket の公開ページを読むのか、
手で貼るのかは、この層より下の都合であって、記録・通知側は知らない。

経路ごとに実行できる場所が違う点に注意:

- manual     : どこでも動く
- livepocket : 公開ページのみ。ログイン不要なので CI でも動く
- twitter    : ログイン必須。データセンターIPからだとアカウントが
               ロックされるため、ローカル実行に限る
"""

from __future__ import annotations

from typing import Protocol

from ..model import Reception


class Collector(Protocol):
    """収集経路の共通インターフェース。"""

    name: str

    def collect(self) -> list[Reception]:
        """受付情報を集めて返す。失敗時は例外ではなく空リストを返す。"""
        ...
