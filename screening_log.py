# screening_log.py — Persist every screened coin (including screening_failed) for ML missed-opportunity analysis.
# App calls append_screening_entry() after each coin is screened; ml pipeline reads for outcome backfill and analysis.

import os
from datetime import datetime, timezone
from typing import Dict, Any, List
import safe_json
from ml.config import SCREENING_LOG_MAX_ENTRIES

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SCREENING_LOG_FILE = os.path.join(DATA_DIR, "screening_log.json")
# 2026-09-26: this writer had its own hardcoded 10,000 cap, so the 2026-08-08 raise of
# SCREENING_LOG_MAX_ENTRIES to 75,000 in ml/config.py never took effect — the file sat at
# exactly 10,000 rows (~13 days) for seven weeks. One constant, one owner.
MAX_LOG_ENTRIES = SCREENING_LOG_MAX_ENTRIES


def _load_log() -> List[Dict[str, Any]]:
    data = safe_json.load(SCREENING_LOG_FILE, default=list)
    return data if isinstance(data, list) else []


def _save_log(entries: List[Dict[str, Any]]) -> None:
    safe_json.save(SCREENING_LOG_FILE, entries[-MAX_LOG_ENTRIES:], default=str)


def append_screening_entry(
    coin_id: str,
    symbol: str,
    entry_price: float,
    quant_tier: str,
    market_regime: str,
    *,
    timestamp: str = None,
) -> None:
    """
    Append one screened coin to the log for ML missed-opportunity analysis.
    Call from refresh_data() after screening each coin in top_10.
    """
    entry = {
        "coin_id": coin_id,
        "symbol": (symbol or "").upper(),
        "timestamp": timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": float(entry_price),
        "quant_tier": quant_tier or "",
        "market_regime": market_regime or "",
        "outcome_7d": None,
    }
    entries = _load_log()
    entries.append(entry)
    _save_log(entries)
