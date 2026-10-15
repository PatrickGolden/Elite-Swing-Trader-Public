# ml/config.py — Paths and thresholds for the learning pipeline

import os
from pathlib import Path

# Repo root (parent of ml/)
REPO_ROOT = Path(__file__).resolve().parent.parent

# Data and report paths
DATA_DIR = REPO_ROOT / "data"
SCREENING_LOG_FILE = DATA_DIR / "screening_log.json"
LEARNING_REPORT_FILE = DATA_DIR / "learning_report.json"
RECOMMENDATIONS_FILE = REPO_ROOT / "ai_recommendations_v2.json"
POSITIONS_FILE = REPO_ROOT / "positions.json"

# Cap for screening log entries (keep last N)
# With ~50 coins × 4 refreshes/day × 90 days ≈ 18,000 entries needed for full analysis window
# 2026-08-08: the cap was 10000 — BELOW the window this comment says is required, so the
# log was a 13-day ring buffer silently rolling off history we believed we were keeping.
# At the observed ~770 rows/day and ~220 bytes/row, 75k ≈ 97 days ≈ 17MB.
SCREENING_LOG_MAX_ENTRIES = 75000

# Min outcome_7d (percent) to count as "missed opportunity"
MISSED_OPPORTUNITY_THRESHOLD_PCT = 5.0

# Backtest days for pipeline
BACKTEST_DAYS = 90

# File references for suggestion engine (relative to repo root for display)
QUANT_SIGNAL_FILE = "quant_signal.py"
VETO_SPECIALIST_FILE = "veto_specialist.py"
