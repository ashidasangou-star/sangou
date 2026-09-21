# 初回セットアップ。定期ポーリングのタスクを登録する。
#
# 管理者権限の PowerShell で1度だけ実行する。
#   powershell -ExecutionPolicy Bypass -File register-tasks.ps1 -Repo C:\src\sangou
#
# 既定では dry-run のタスクを登録する。セレクタの較正が済んで、
# 実際に申し込ませてよいと判断してから -Execute を付けて登録し直す。

param(
    [Parameter(Mandatory = $true)][string]$Repo,
    # PC が起きている見込みの時刻。ここで抽選受付を拾う。
    # 抽選は締切が数日先なので、1日2回で十分間に合う。
    [string[]]$Times = @("09:30", "21:30"),
    [switch]$Execute
)

$ErrorActionPreference = "Stop"

# スリープ解除タイマーを有効にする。これが無効だと、先着受付の
# 起床タイマーを登録しても PC が起きない。
Write-Host "スリープ解除タイマーを有効にします"
powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /SETACTIVE SCHEME_CURRENT

$arg = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Repo\scripts\windows\lpwatch-poll.ps1`" -Repo `"$Repo`""
if ($Execute) { $arg += " -Execute" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg

$triggers = @()
foreach ($t in $Times) { $triggers += New-ScheduledTaskTrigger -Daily -At $t }

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "lpwatch-poll" -Action $action -Trigger $triggers -Settings $settings -Force | Out-Null

$mode = if ($Execute) { "本番（実際に申し込む）" } else { "dry-run（送信しない）" }
Write-Host ""
Write-Host "登録しました: lpwatch-poll  ($($Times -join ', '))  モード: $mode"
Write-Host ""
Write-Host "次にやること:"
Write-Host "  1. py -m pip install playwright"
Write-Host "  2. py -m playwright install chromium"
Write-Host "  3. py -m lpwatch local login x"
Write-Host "  4. py -m lpwatch local login livepocket"
Write-Host "  5. data\lpwatch\targets.json に申込対象を書く"
Write-Host "  6. py -m lpwatch local run          # dry-run で較正を確認"
Write-Host ""
Write-Host "スリープ解除タイマーの状態: powercfg /waketimers"
