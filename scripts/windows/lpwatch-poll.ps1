# 定期ポーリング。タスクスケジューラから呼ぶ。
#
# GitHub Actions が events.jsonl に積んだ情報を取りに行き、申込すべきものが
# あれば実行する。Actions から Windows へ通知を押し込むのではなく、
# Windows が取りに行く形にしてある。自宅PCは NAT の内側にあり、外から
# 届く受信口を用意するとポート開放と動的DNSが要って脆くなるため。
#
# 実行例:
#   powershell -ExecutionPolicy Bypass -File lpwatch-poll.ps1 -Repo C:\src\sangou
#   powershell -ExecutionPolicy Bypass -File lpwatch-poll.ps1 -Repo C:\src\sangou -Execute

param(
    [Parameter(Mandatory = $true)][string]$Repo,
    # 付けないと dry-run。実際に申し込むときだけ明示する。
    [switch]$Execute
)

$ErrorActionPreference = "Stop"

# Windows の既定は cp932。Python 側の入出力を UTF-8 に固定しないと、
# 日本語のイベント名で UnicodeEncodeError になる。
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location $Repo

function Write-Log($msg) {
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
}

$branch = git rev-parse --abbrev-ref HEAD

# --- 1. Actions が積んだ情報を取り込む -------------------------------------
Write-Log "リポジトリを更新します"
git pull --rebase origin $branch
if ($LASTEXITCODE -ne 0) {
    # rebase が途中で止まると、以降の判断が古い情報の上で行われる。
    Write-Log "git pull に失敗しました。手で解決してください"
    exit 1
}

# --- 2. ログインセッションの確認 -------------------------------------------
# 切れたまま申込に進むと、ログイン画面をフォームと誤認して操作しかねない。
Write-Log "ログイン状態を確認します"
py -m lpwatch local check livepocket
if ($LASTEXITCODE -ne 0) {
    Write-Log "LivePocket のセッションが切れています。再ログインが必要です:"
    Write-Log "  py -m lpwatch local login livepocket"
    exit 1
}

# --- 3. 申込 ---------------------------------------------------------------
$runArgs = @("-m", "lpwatch", "local", "run")
if ($Execute) {
    $runArgs += "--execute"
    Write-Log "申込を実行します（本番）"
} else {
    Write-Log "申込を確認します（dry-run）"
}
if ($env:LPWATCH_WEBHOOK) { $runArgs += @("--webhook", $env:LPWATCH_WEBHOOK) }

py @runArgs
$runExit = $LASTEXITCODE

# --- 4. 自分が書いた記録だけを push ----------------------------------------
# marks.jsonl は Windows が書き、events.jsonl は Actions が書く。
# 担当を分けてあるので、この push が Actions と競合しない。
git add data/lpwatch/marks.jsonl
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "lpwatch: 申込の記録を更新"
    git pull --rebase origin $branch
    git push origin HEAD
    Write-Log "記録を push しました"
}

# --- 5. 次の起床を予約する -------------------------------------------------
Write-Log "先着受付の起床タイマーを更新します"
& "$PSScriptRoot\schedule-wakes.ps1" -Repo $Repo

exit $runExit
