# scripts/backup_sensitive_configs.ps1 — Back up execution_config.json and account_config.json to backup/ with date suffix.
# Run daily (e.g. Task Scheduler). From repo root: .\scripts\backup_sensitive_configs.ps1

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$backupDir = Join-Path $repoRoot "backup"
$dateStr = Get-Date -Format "yyyy-MM-dd"

if (-not (Test-Path $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir | Out-Null
}

$files = @("execution_config.json", "account_config.json")
foreach ($name in $files) {
    $src = Join-Path $repoRoot $name
    if (Test-Path $src) {
        $base = [System.IO.Path]::GetFileNameWithoutExtension($name)
        $ext = [System.IO.Path]::GetExtension($name)
        $dest = Join-Path $backupDir "${base}.${dateStr}${ext}"
        Copy-Item -Path $src -Destination $dest -Force
        Write-Host "Backed up $name -> backup/${base}.${dateStr}${ext}"
    } else {
        Write-Warning "Skip (not found): $name"
    }
}
