# 先着受付の開始時刻に合わせて、スリープ解除タイマーを仕掛ける。
#
# PC を常時起動しておく必要をなくすための仕組み。抽選は締切が数日先なので
# 定期ポーリングの窓で足りるが、先着は開始時刻が本番なので、その直前に
# 起きる必要がある。開始時刻は事前に分かっているので予約できる。
#
# 前提:
#   - スリープ (S3 / モダンスタンバイ) からのみ復帰できる。
#     シャットダウン (S5) からは復帰しない。
#   - 電源オプションでスリープ解除タイマーが有効であること。
#     register-tasks.ps1 が初回に設定する。
#     現在の状態は powercfg /waketimers で確認できる。

param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [string]$TaskPrefix = "lpwatch-wake"
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location $Repo

# 既存の起床タスクを一旦消す。受付が終わったものや締切が変わったものが
# 残り続けると、用のない時刻に PC が起きる。
Get-ScheduledTask -TaskName "$TaskPrefix-*" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false

$json = py -m lpwatch wake --json
$wakes = @($json | ConvertFrom-Json)

if ($wakes.Count -eq 0) {
    Write-Host "予約すべき起床時刻はありません"
    exit 0
}

foreach ($w in $wakes) {
    $at = [DateTime]::Parse($w.at)
    if ($at -le (Get-Date)) { continue }

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSScriptRoot\lpwatch-poll.ps1`" -Repo `"$Repo`" -Execute"

    $trigger = New-ScheduledTaskTrigger -Once -At $at

    # WakeToRun がスリープ解除の本体。AllowStartIfOnBatteries を付けないと
    # ノートPCがバッテリー駆動のとき黙って実行されない。
    # StartWhenAvailable は、起床に失敗した場合に復帰後すぐ実行させる保険。
    $settings = New-ScheduledTaskSettingsSet `
        -WakeToRun `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

    $name = "$TaskPrefix-$($w.event_id)"
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "予約: $($at.ToString('yyyy-MM-dd HH:mm'))  $($w.title)"
}
