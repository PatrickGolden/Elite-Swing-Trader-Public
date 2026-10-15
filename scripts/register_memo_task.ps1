# register_memo_task.ps1 - (re)create the SwingTrader-WeeklyMemo scheduled task.
#
# Runs scripts/falsifier_cycle.py every Sunday 08:00 - the deterministic weekly
# self-learning cycle (STRATEGY.md v1.6e). Writes a dated decision memo to
# data/decision_memos/; never changes config or trades. This replaces the retired
# autonomous optimizer-audit agent with a script whose every output is auditable.
#
# Run once per machine:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_memo_task.ps1

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$script = Join-Path $repo 'scripts\falsifier_cycle.py'

$action = New-ScheduledTaskAction -Execute $python `
    -Argument "`"$script`"" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 08:00
# -WakeToRun: Modern Standby cuts power/network when the display sleeps; wake to run.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName "SwingTrader-WeeklyMemo" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Weekly deterministic falsifier cycle - decision memo to data/decision_memos (STRATEGY.md v1.6e)." `
    -Force | Out-Null

$t = Get-ScheduledTask -TaskName "SwingTrader-WeeklyMemo"
Write-Output "Registered: $($t.TaskName) (state $($t.State))"
