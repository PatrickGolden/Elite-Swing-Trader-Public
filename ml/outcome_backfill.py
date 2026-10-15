# ml/outcome_backfill.py — Backfill outcome_7d for recommendations and screening log using current prices.

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any

from ml.config import (
    REPO_ROOT,
    RECOMMENDATIONS_FILE,
    SCREENING_LOG_FILE,
    DATA_DIR,
    SCREENING_LOG_MAX_ENTRIES,
)

# Ensure project root is on path for safe_json import
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)
import safe_json


def _load_json(path) -> List[Dict[str, Any]]:
    data = safe_json.load(str(path), default=list)
    return data if isinstance(data, list) else []


def _save_json(path, data: List[Dict[str, Any]]) -> None:
    safe_json.save(str(path), data, default=str)


def _parse_ts(ts: str) -> datetime:
    if not ts or len(ts) < 19:
        raise ValueError("invalid timestamp")
    return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")


def get_coin_ids_needing_prices() -> set:
    """
    Return set of coin_ids that run_backfill would request prices for.
    Used to batch one price fetch in refresh (outcomes + backfill) and avoid multiple simple/price calls.
    """
    now = datetime.now()
    min_age_days = 7
    cutoff = now - timedelta(days=min_age_days)
    need = set()
    for rec in _load_json(RECOMMENDATIONS_FILE):
        if rec.get("outcome_7d") is not None:
            continue
        ts, entry_price, coin_id = rec.get("timestamp"), rec.get("entry_price"), rec.get("coin_id")
        if not ts or entry_price is None or not coin_id:
            continue
        try:
            if _parse_ts(ts) >= cutoff:
                continue
        except (ValueError, TypeError):
            continue
        need.add(coin_id)
    screening_path = Path(SCREENING_LOG_FILE)
    for ent in _load_json(screening_path):
        if ent.get("outcome_7d") is not None:
            continue
        ts, entry_price, coin_id = ent.get("timestamp"), ent.get("entry_price"), ent.get("coin_id")
        if not ts or entry_price is None or not coin_id:
            continue
        try:
            if _parse_ts(ts) >= cutoff:
                continue
        except (ValueError, TypeError):
            continue
        need.add(coin_id)
    try:
        from paper_evidence import shadow_skip_coin_ids_needing_prices
        need.update(shadow_skip_coin_ids_needing_prices())
    except Exception:
        pass
    return need


def run_backfill(get_prices_fn) -> Dict[str, int]:
    """
    Backfill outcome_7d for recommendations and screening log.
    get_prices_fn: callable(coin_ids: list) -> {coin_id: price}.
    Returns dict with keys: recommendations_updated, screening_updated.
    """
    now = datetime.now()
    min_age_days = 7
    cutoff = now - timedelta(days=min_age_days)
    stats = {"recommendations_updated": 0, "screening_updated": 0}

    # --- Recommendations ---
    recs = _load_json(RECOMMENDATIONS_FILE)
    need_price = set()
    rec_indices = []
    for i, rec in enumerate(recs):
        if rec.get("outcome_7d") is not None:
            continue
        ts = rec.get("timestamp")
        entry_price = rec.get("entry_price")
        coin_id = rec.get("coin_id")
        if not ts or entry_price is None or not coin_id:
            continue
        try:
            t = _parse_ts(ts)
            if t >= cutoff:
                continue
        except (ValueError, TypeError):
            continue
        need_price.add(coin_id)
        rec_indices.append((i, rec, float(entry_price), coin_id))

    if rec_indices:
        coin_ids = list(need_price)
        prices = get_prices_fn(coin_ids) if coin_ids else {}
        for i, rec, entry_price, coin_id in rec_indices:
            current = prices.get(coin_id)
            if current is None:
                continue
            pct = round(((float(current) - entry_price) / entry_price) * 100, 2)
            rec["outcome_7d"] = pct
            rec["outcome_mtm_pct"] = pct
            rec["backfill_note"] = "mtm_at_backfill"
            stats["recommendations_updated"] += 1
        _save_json(RECOMMENDATIONS_FILE, recs)

    # --- Screening log ---
    screening_path = Path(SCREENING_LOG_FILE)
    entries = _load_json(screening_path)
    need_price = set()
    entry_indices = []
    for i, ent in enumerate(entries):
        if ent.get("outcome_7d") is not None:
            continue
        ts = ent.get("timestamp")
        entry_price = ent.get("entry_price")
        coin_id = ent.get("coin_id")
        if not ts or entry_price is None or not coin_id:
            continue
        try:
            t = _parse_ts(ts)
            if t >= cutoff:
                continue
        except (ValueError, TypeError):
            continue
        need_price.add(coin_id)
        entry_indices.append((i, ent, float(entry_price), coin_id))

    if entry_indices:
        coin_ids = list(need_price)
        prices = get_prices_fn(coin_ids) if coin_ids else {}
        for i, ent, entry_price, coin_id in entry_indices:
            current = prices.get(coin_id)
            if current is None:
                continue
            pct = round(((float(current) - entry_price) / entry_price) * 100, 2)
            ent["outcome_7d"] = pct
            stats["screening_updated"] += 1
        if len(entries) > SCREENING_LOG_MAX_ENTRIES:
            entries = entries[-SCREENING_LOG_MAX_ENTRIES:]
        _save_json(screening_path, entries)

    try:
        from paper_evidence import backfill_shadow_skips
        stats["shadow_skips_updated"] = backfill_shadow_skips(get_prices_fn).get(
            "shadow_skips_updated", 0
        )
    except Exception:
        stats["shadow_skips_updated"] = 0

    return stats
