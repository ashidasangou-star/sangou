"""Windows PC 側でのみ動く処理。

ログインセッションとブラウザを使うものだけを置く。GitHub Actions は
この配下を一切 import しない。そのため Playwright は遅延 import にし、
Actions 側のジョブが依存を背負わないようにしてある。

ここに置くもの:
  browser.py  永続プロファイルの管理
  session.py  ログイン状態の確認と初回ログイン
  apply.py    LivePocket の申込操作

ここに置かないもの:
  判定ロジック全般（agenda.py にある。実機なしでテストできる状態を保つ）
"""
