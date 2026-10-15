# register_dashboard_task.ps1 - (re)create the SwingTrader-Dashboard scheduled task.
#
# The task is the app's supervisor (STRATEGY.md watch phase). Two triggers:
#   1. At logon            - app comes up after reboot-then-login
#   2. Every 30 minutes    - WATCHDOG: self-heals any app death within 30 min
#      (added 2026-07-02 after a 6-day silent outage: the app had been restarted
#      from a Cursor-child shell and died when Cursor closed; logon-only trigger
#      never re-fired because no logout happened)
# Both triggers run scripts/start_dashboard.ps1, which is idempotent (no-ops when
# the app is already up), so the watchdog never double-launches.
#
# Run this once on any new machine / after task loss:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_dashboard_task.ps1
#
# OPS RULE: to restart the app manually, use
#   Start-ScheduledTask -TaskName SwingTrader-Dashboard
# NEVER run start_dashboard.ps1 from an editor/agent shell - the app becomes a
# child of that process tree and dies with it (root cause of the June outage).

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo 'scripts\start_dashboard.ps1'

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $repo

# AtStartup (added 2026-08-16): a Windows Update reboot on 2026-08-14 left the app
# dead for ~2.5 days because the only "come back" trigger was AtLogOn and nobody
# logged in. Boot-time start removes the dependency on a human session.
$startup = New-ScheduledTaskTrigger -AtStartup
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"
# PS 5.1 rejects [TimeSpan]::MaxValue for repetition duration; 10 years is effectively forever.
$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)

# WakeToRun (added 2026-08-10): this hardware only supports Modern Standby, where the
# DISPLAY-off transition drops the whole system into standby and cuts the network — so
# the app stops refreshing whenever the monitor sleeps. Until 2026-08-08 the machine
# happened to be kept awake by an editor being open; when that stopped, refreshes
# gapped 8-14h overnight and the health check fired CRITICAL. Waking for each run is
# the recovery path. The real fix is disabling Modern Standby (see README note).
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -WakeToRun -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

# Run as SYSTEM so the task works with NO user logged on.
#
# Why not S4U ("run whether user is logged on or not" as Patrick): this account is a
# MICROSOFT ACCOUNT (PrincipalSource=MicrosoftAccount) and Windows refuses S4U
# registration for MSA-backed accounts — Register-ScheduledTask returns Access Denied
# even from an elevated shell. Why not Interactive (the old setting): it cannot run at
# all while the machine sits at the lock screen, which is exactly how the 2026-08-14
# Windows Update reboot left the app dead for ~2.5 days with no alert.
#
# SYSTEM trade-off, accepted deliberately: higher privilege than the app needs, and no
# access to user-mapped drives (irrelevant here — the dashboard only touches the repo
# and the network; the Google Drive backup task stays Interactive because it needs G:).
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
    -LogonType ServiceAccount -RunLevel Limited

Register-ScheduledTask -TaskName "SwingTrader-Dashboard" -Action $action `
    -Trigger @($startup, $logon, $watchdog) -Settings $settings -Principal $principal `
    -Description "Auto-start + 30-min watchdog for the Swing Trader dashboard (idempotent launcher; STRATEGY.md paper-cohort watch phase)." `
    -Force | Out-Null

$t = Get-ScheduledTask -TaskName "SwingTrader-Dashboard"
Write-Output "Registered: $($t.TaskName) (state $($t.State))"
$t.Triggers | ForEach-Object {
    Write-Output "  trigger: $($_.CimClass.CimClassName) repetition=$($_.Repetition.Interval)"
}
