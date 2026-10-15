<#
.SYNOPSIS
    Daily backup of swing-trader runtime state to Google Drive.
    Scheduled via Windows Task Scheduler to run every 6 hours.

.DESCRIPTION
    Copies critical runtime files (positions, thresholds, trade history,
    optimizer data) to Google Drive for disaster recovery. Keeps dated
    snapshots of the most critical file (positions.json) for 30 days.

.NOTES
    Google Drive path: G:\My Drive\Trading App\Backup Files
    Source: the repo root (parent of this scripts\ folder)
#>

$ErrorActionPreference = "Stop"

$SRC = Split-Path -Parent $PSScriptRoot
$DEST = "G:\My Drive\Trading App\Backup Files"
$DATE = Get-Date -Format "yyyy-MM-dd"
$TIMESTAMP = Get-Date -Format "yyyy-MM-dd_HHmmss"
$LOG_FILE = "$SRC\data\backup_log.txt"

function Write-Log {
    param([string]$Message)
    $entry = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $Message"
    Add-Content -Path $LOG_FILE -Value $entry -Encoding utf8
    Write-Host $entry
}

# Ensure destination directories exist
$dirs = @("$DEST\config", "$DEST\data", "$DEST\snapshots")
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }
}

Write-Log "=== Backup started ==="

# --- Critical config files (always overwrite with latest) ---
$configFiles = @(
    "positions.json",
    "live_thresholds.json",
    "execution_config.json",
    "account_config.json",
    "scheduler_config.json",
    "user_alert_settings.json",
    "model_performance.json",
    "model_attribution.json",
    "ai_recommendations_v2.json",
    "cost_log.json",
    "alert_history.json"
)

$copied = 0
$skipped = 0
foreach ($f in $configFiles) {
    $src_path = Join-Path $SRC $f
    if (Test-Path $src_path) {
        Copy-Item $src_path -Destination "$DEST\config\$f" -Force
        $copied++
    } else {
        $skipped++
    }
}
Write-Log "Config files: $copied copied, $skipped missing"

# --- Dated snapshot of positions.json (most critical — money at risk) ---
$posFile = Join-Path $SRC "positions.json"
if (Test-Path $posFile) {
    $snapName = "positions_$TIMESTAMP.json"
    Copy-Item $posFile -Destination "$DEST\snapshots\$snapName" -Force
    Write-Log "Positions snapshot: $snapName"

    # Prune snapshots older than 30 days
    $cutoff = (Get-Date).AddDays(-30)
    $old = Get-ChildItem "$DEST\snapshots\positions_*.json" |
        Where-Object { $_.LastWriteTime -lt $cutoff }
    if ($old) {
        $old | Remove-Item -Force -Confirm:$false
        Write-Log "Pruned $($old.Count) old position snapshots"
    }
}

# --- Data directory (trade history, telemetry, optimizer logs) ---
$dataFiles = @(
    "data\execution_audit.jsonl",
    "data\execution_log.jsonl",
    "data\signal_history.json",
    "data\screening_log.json",
    "data\learning_report.json",
    "data\monitoring_expectancy.json",
    "data\optimizer_log.json",
    "data\optimizer_audit.json",
    "data\optimizer_audit_report.md",
    "data\agent_recommendations.json",
    "data\roadmap_state.json",
    "data\roadmap_transitions.jsonl",
    "data\dynamic_stop_telemetry.jsonl",
    "data\skipped_insufficient_cash.jsonl",
    "data\refresh_snapshots.jsonl",
    "data\pending_orders.json",
    "data\sold_pending_close.json",
    "data\backtest_trades.json",
    "data\veto_log.jsonl",
    # --- Added 2026-08-08: watch-phase evidence that was NOT being backed up. ---
    # These are the datasets the funding decision will rest on, and several cannot
    # be reconstructed if lost (see also $archiveGlobs below).
    "data\shadow_skips.jsonl",          # counterfactual dataset (declined signals + fwd returns)
    "data\spread_snapshots.jsonl",      # maker-fill evidence — NOT backfillable
    "data\trials.jsonl",                # registered-trial registry (G5 hurdle depends on it)
    "data\gate_watch_report.json",
    "data\stage1c_q3_rotation_trials.json",
    "data\stage1c_adjudication.json",
    "data\stage1c_pit_gate.json",
    "data\stage1c_lean_full_gate.json",
    "data\pit_universe\membership.json",  # point-in-time universe (G1)
    "data\attribution_quality.json",
    # --- Added 2026-09-26 (STRATEGY.md v2.0): the forward environment record. ---
    "data\market_state.jsonl",          # hourly environment row (ETH/BTC, breadth, dominance)
    "data\eth_btc_cache.json",
    "data\known_contamination.json"      # rows to exclude from analysis (2026-09-26)
)

# Rotated archives + decision memos. These are the ONLY copy of pre-June history
# (execution_audit .bak = 650k rows Mar-Jun; refresh_snapshots .bak = 12.9k rows),
# so a disk failure without these in Drive loses that window permanently.
# Uncompressed *.bak too: safe_json rotation keeps APPENDING to <file>.bak after the
# old archive was gzipped, so a fresh .bak holds unique history (2026-09-08 rotation:
# refresh_snapshots Jun 11 -> Jul 30, 2,579 rows) that *.bak.gz never matched.
$archiveGlobs = @(
    "data\*.bak.gz",
    "data\*.bak",
    "data\decision_memos\*.md",
    "data\decision_memos\*.json",
    "data\market_daily\*.json",          # one compact 250-coin snapshot per UTC day (v2.0)
    "data\task_backups\worktree_patches\*",
    "data\historical_cache\*.tar.gz"
)

$dataCopied = 0
foreach ($f in $dataFiles) {
    $src_path = Join-Path $SRC $f
    if (Test-Path $src_path) {
        $destDir = Join-Path $DEST (Split-Path $f)
        if (-not (Test-Path $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        Copy-Item $src_path -Destination (Join-Path $DEST $f) -Force
        $dataCopied++
    }
}
Write-Log "Data files: $dataCopied copied"

# --- Archives + memos (glob-matched; only copy when newer to keep the run fast) ---
$archiveCopied = 0
foreach ($g in $archiveGlobs) {
    $pattern = Join-Path $SRC $g
    $relDir = Split-Path $g
    $destDir = Join-Path $DEST $relDir
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue | ForEach-Object {
        $target = Join-Path $destDir $_.Name
        $existing = Get-Item $target -ErrorAction SilentlyContinue
        if (-not $existing -or $_.LastWriteTime -gt $existing.LastWriteTime) {
            Copy-Item $_.FullName -Destination $target -Force
            $archiveCopied++
        }
    }
}
Write-Log "Archives/memos: $archiveCopied copied"

# --- Summary ---
$totalSize = 0
Get-ChildItem $DEST -Recurse -File | ForEach-Object { $totalSize += $_.Length }
$sizeMB = [math]::Round($totalSize / 1MB, 1)
Write-Log "=== Backup complete: ${sizeMB} MB total in Google Drive ==="
