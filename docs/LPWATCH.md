# lpwatch — LivePocket 抽選・先着受付の検知と通知

抽選の告知を見逃さず、締切前に必ず思い出すための道具。

## 前提として押さえたこと

**抽選は申込順が結果に影響しない。** 自動化しても当選確率は上がらない。
このツールの価値は「速く申し込む」ことではなく「見逃さない」「締切前に
気づく」ことにある。一方で **先着** は開始時刻が本番なので、こちらは
締切ではなく開始の直前に鳴らす。`remind.py` がこの2つを別扱いする。

**実行場所を分ける必要がある。** ログインが要る経路を GitHub Actions で
回すと壊れる。Actions のランナーはデータセンターの既知IPレンジで毎回IPが
変わるため、X も LivePocket も不審ログインとして扱い、チャレンジや
SMS認証を出す。無人のランナーはそこで止まる。

| 層 | 実行場所 | 理由 |
| --- | --- | --- |
| 検知・記録・通知 | GitHub Actions | 公開ページのみ。ログイン不要 |
| X 検索 (browser-use) | ローカルPC | 自宅IPとログインセッションを保つ |
| LivePocket 申込 | ローカルPC | 同上。SMS認証に人が対応できる |

## 使い方

依存ライブラリなし（Python 3.10+）。

```bash
python3 -m lpwatch collect            # inbox の告知テキストを取り込む
python3 -m lpwatch list               # 受付を締切順に一覧する
python3 -m lpwatch notify --dry-run   # 通知内容を確認する
python3 -m lpwatch notify --webhook "$LPWATCH_WEBHOOK"
python3 -m lpwatch mark tokyo2026a applied   # 申込済みにして通知を止める
python3 -m lpwatch parse post.txt     # 抽出結果だけ見る（正規表現の検証用）
```

記録は `data/lpwatch/` に置く。`--root` で変えられる。

## データの持ち方

pokebox_ev と同じく **1行1観測の JSONL 追記専用**。

- `events.jsonl` — 検知した受付。外から観測した事実
- `marks.jsonl` — 通知済み・申込済み・見送り。自分の行動

この2つを混ぜない。前者は何度でも上書き観測されるが、後者は自分にしか
書けない。読み出すときは `event_id:受付種別` をキーに時系列でマージし、
**空欄で既知の値を消さない**。X の投稿で URL だけ先に拾い、あとで
LivePocket 本体から締切を取り直す、という順序が普通に起きるため。

同じ公演でも先行抽選と一般先着は申込URLも締切も別なので、別レコードとして
持つ。

## 読み取れなかったものは空にする

締切が読めなかった告知は `closes_at` を空のまま記録し、通知には
「締切: 不明（要確認）」と出す。推測で埋めない。締切を1つ誤ると、その
受付の通知が丸ごと無意味になる（早すぎれば忘れ、遅すぎれば間に合わない）。

年が省略された日付（「10/5 23:59まで」）は基準日以降で最も近い年を採るが、
基準日より前にしかならない入力では推定を諦めて空を返す。過去の告知を
拾ったときに翌年の日付をでっち上げないため。

## 通知の段階

締切の 72 / 24 / 3 時間前に一度ずつ。該当する中で **最も短い** 段階を採る。
長い順に見ると、残り20時間で初めて検知した受付が「72時間前の段階」を
消費してしまい、24時間前の通知が来なくなる。

先着受付は加えて開始15分前に鳴らす。抽選では鳴らさない。

## 実行環境の分担

ログインが要る処理は自宅 Windows PC、要らない処理は GitHub Actions。
**X と LivePocket のセッションを Actions に持ち込まない。**

```
GitHub Actions (lpwatch refresh / notify)
   └─ 公開ページのみ。events.jsonl を更新してコミット
        ↓  git
Windows PC (scripts/windows/lpwatch-poll.ps1)
   └─ git pull → local check → local run → marks.jsonl を push
```

トリガーは **プル型**。Actions から押し込むのではなく Windows が取りに行く。
自宅PCは NAT の内側で、受信口を作るとポート開放と動的DNSが要って脆い。

書き込み担当を分けてあるので、両者が同じファイルに追記せず競合しない。

| ファイル | 書く側 |
| --- | --- |
| `data/lpwatch/events.jsonl` | Actions（観測した事実） |
| `data/lpwatch/marks.jsonl` | Windows（自分の行動） |

### 時刻は Windows が持つ

GitHub Actions の `schedule` は時刻を保証しない。高負荷時（毎時00分付近）は
遅延し、負荷次第で破棄される。先着受付への張り付きを Actions から
トリガーすると間に合わない。

先着は `opens_at` が判明した時点で、Windows 側が開始5分前に
スリープ解除タイマーを登録する（`schedule-wakes.ps1`）。

### 常時起動は不要

- 抽選 … 締切が数日先。1日2回の定期ポーリングで足りる
- 先着 … 開始時刻に one-shot の起床タイマーを仕掛ける

ただし **スリープ (S3) からは復帰できるが、シャットダウン (S5) からは
復帰しない**。電源オプションのスリープ解除タイマー有効化は
`register-tasks.ps1` が行う。

### セッションの持ち方

Playwright の永続プロファイルを `%LOCALAPPDATA%\lpwatch\profiles\` に置く。
**リポジトリ外**。証跡のスクリーンショットも同様（氏名・住所・決済手段が写る）。

- ヘッドレスで動かさない
- **自動再ログインしない。** 切れたら人に通知して止める
- パスワードはどこにも保存しない

## Windows セットアップ

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\register-tasks.ps1 -Repo C:\src\sangou
py -m pip install playwright
py -m playwright install chromium
py -m lpwatch local login x
py -m lpwatch local login livepocket
py -m lpwatch local run          # dry-run。セレクタの較正を確認
```

`data/lpwatch/targets.json` に書いた受付にしか申し込まない。空なら何もしない。

## まだ無いもの

- `collect/twitter.py` — browser-use による X 検索。ローカルで回す側
- `apply.py` のセレクタ較正（実ページを見ないと確定できない）

### 申込の自動化について決めてあること

単一アカウントでの自動申込まで作る。作らないものが2つある。

- **CAPTCHA の突破** — 出たら停止して人に渡す
- **複数アカウントでの重複申込** — 抽選の公平性を直接壊すため

また LivePocket の規約は自動化プログラムによるアクセスを禁止している
（規約PDFは開発環境のegress制限で原文を確認できていない）。自動申込は
アカウント停止のリスクを負った上での選択であることを前提にする。
