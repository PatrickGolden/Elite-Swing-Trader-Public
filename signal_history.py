# signal_history.py — Log per-refresh signal snapshots and return 30-day aggregates
# Used by overview tiles to show "signals reached in the last 30 days" (quant + AI, incl. HOLD / MODERATE).

import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import safe_json

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
HISTORY_FILE = os.path.join(DATA_DIR, 'signal_history.json')
DAYS_CUTOFF = 30    # read-time aggregation window only (not a storage trim)
# 2026-08-08: was 2000, which held only ~6 weeks at the live refresh rate — the file
# was rolling off signal history during the watch phase. ~48 rows/day, ~150 B/row,
# so 20k ≈ 14 months ≈ 3MB. Storage is capped HERE, not by DAYS_CUTOFF.
MAX_ENTRIES = 20000


def _load_history() -> List[Dict[str, Any]]:
    data = safe_json.load(HISTORY_FILE, default=list)
    return data if isinstance(data, list) else []


def _save_history(entries: List[Dict[str, Any]]):
    safe_json.save(HISTORY_FILE, entries[-MAX_ENTRIES:])


def _parse_ts(ts: str) -> datetime:
    try:
        return datetime.strptime(ts[:19], '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return datetime.min


def log_snapshot(
    summary: Dict[str, Any],
    coins: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Append one snapshot (quant buy/hold/sell + AI ultra/strong/moderate/hold) and return 30-day aggregates.
    Call after each refresh with current summary and coins.
    """
    now = datetime.now()
    ts = now.strftime('%Y-%m-%d %H:%M:%S')
    buy = int(summary.get('buy') or 0)
    hold = int(summary.get('hold') or 0)
    sell = int(summary.get('sell') or 0)

    ultra = strong = moderate = ai_hold = 0
    if coins and isinstance(coins, list):
        for c in coins:
            rec = c.get('ai_recommendation') or {}
            cons = rec.get('consensus') or {}
            sig = (cons.get('consensus_signal') or '').strip()
            if sig == 'ULTRA_BUY':
                ultra += 1
            elif sig == 'STRONG_BUY':
                strong += 1
            elif sig == 'MODERATE_BUY':
                moderate += 1
            elif sig and sig not in ('ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY'):
                ai_hold += 1

    entry = {
        'ts': ts,
        'buy': buy,
        'hold': hold,
        'sell': sell,
        'ultra': ultra,
        'strong': strong,
        'moderate': moderate,
        'ai_hold': ai_hold,
    }
    entries = _load_history()
    entries.append(entry)
    _save_history(entries)

    return get_30d_aggregates(entries=entries)


def get_30d_aggregates(entries: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Return 30-day totals: signal_30d { buy, hold, sell }, ai_30d { ultra, strong, moderate, hold }.
    If entries is None, loads from file.
    """
    if entries is None:
        entries = _load_history()
    cutoff = datetime.now() - timedelta(days=DAYS_CUTOFF)
    signal_buy = signal_hold = signal_sell = 0
    ai_ultra = ai_strong = ai_moderate = ai_hold = 0
    for e in entries:
        if _parse_ts(e.get('ts') or '') < cutoff:
            continue
        signal_buy += int(e.get('buy') or 0)
        signal_hold += int(e.get('hold') or 0)
        signal_sell += int(e.get('sell') or 0)
        ai_ultra += int(e.get('ultra') or 0)
        ai_strong += int(e.get('strong') or 0)
        ai_moderate += int(e.get('moderate') or 0)
        ai_hold += int(e.get('ai_hold') or 0)
    return {
        'signal_30d': {'buy': signal_buy, 'hold': signal_hold, 'sell': signal_sell},
        'ai_30d': {'ultra': ai_ultra, 'strong': ai_strong, 'moderate': ai_moderate, 'hold': ai_hold},
    }
