# register_healthcheck_task.ps1 - (re)create SwingTrader-DailyHealthCheck as SYSTEM.
#
# WHY (2026-08-20): the daily 7am health email is the DEAD-MAN SIGNAL — "no email
# arrived" is supposed to mean "something is wrong". That only works if the check can
# always run. It was registered Interactive, so it could not run at all while the
# machine sat at the lock screen: during the 2026-08-14 Windows Update reboot the app
# was down 2.5 days AND the health check never fired, so the silence meant nothing.
#
# Runs as SYSTEM (ServiceAccount) like the dashboard task. S4U is not an option on this
# machine — the account is a Microsoft Account and Windows refuses S4U for MSA.
#
# NOTE: SwingTrader-GDrive-Backup deliberately stays Interactive — it copies to G:,
# a user-mapped drive that SYSTEM cannot see. Converting it would silently break backups.
#
# Run ONCE from an elevated prompt (Win+X -> Terminal (Admin)):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_healthcheck_task.ps1

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $repo 'scripts\run_health_check.bat'

if (-not (Test-Path $bat)) { Write-Error "Not found: $bat"; exit 1 }

$action    = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $repo
$trigger   = New-ScheduledTaskTrigger -Daily -At 07:00
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Limited

Register-ScheduledTask -TaskName "SwingTrader-DailyHealthCheck" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Daily 7am health check as SYSTEM so it runs even with no user logged on (dead-man signal)." `
    -Force | Out-Null

$t = Get-ScheduledTask -TaskName "SwingTrader-DailyHealthCheck"
Write-Output "Registered: $($t.TaskName) (state $($t.State))"
Write-Output "  RunAs     : $($t.Principal.UserId) / $($t.Principal.LogonType)"
Write-Output "  WakeToRun : $($t.Settings.WakeToRun)"
Write-Output "  NextRun   : $((Get-ScheduledTaskInfo -TaskName $t.TaskName).NextRunTime)"
