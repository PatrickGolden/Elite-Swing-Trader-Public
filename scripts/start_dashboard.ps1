# start_dashboard.ps1 - idempotent launcher for the Swing Trader dashboard.
#
# Used by the "SwingTrader-Dashboard" logon scheduled task to keep the app running
# hands-free (survives Cursor close + reboot-then-login). Safe to run anytime:
#   - if the app is already running, it does nothing (no double launch)
#   - otherwise it starts app.py DETACHED, redirecting stdout/stderr to data/ logs.
#
# Kept deliberately simple: a scheduled task must never hard-fail on a transient
# port-check hiccup (that would trigger restart storms), so detection is tolerant
# and the script always exits 0 once a launch has been issued.

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$stdout = Join-Path $repo 'data\app_stdout.log'
$stderr = Join-Path $repo 'data\app_stderr.log'

function Test-DashboardUp {
    # True if something owns port 5000 OR a python process is running app.py.
    for ($i = 0; $i -lt 2; $i++) {
        if (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue) { return $true }
        $appPy = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                 Where-Object { $_.CommandLine -like '*app.py*' }
        if ($appPy) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

if (Test-DashboardUp) {
    Write-Output "$(Get-Date -Format s) start_dashboard: already running - no action"
    exit 0
}

if (-not (Test-Path $python)) {
    Write-Output "$(Get-Date -Format s) start_dashboard: ERROR venv python not found at $python"
    exit 1
}

Start-Process -FilePath $python -ArgumentList 'app.py' `
    -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null

Start-Sleep -Seconds 20
if (Test-DashboardUp) {
    Write-Output "$(Get-Date -Format s) start_dashboard: started OK"
} else {
    Write-Output "$(Get-Date -Format s) start_dashboard: launch issued but not confirmed up - check $stderr"
}
exit 0
