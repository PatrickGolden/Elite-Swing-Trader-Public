# testing_log.py — Auto-log ULTRA_BUY / STRONG_BUY signals for paper trading validation
# Stores: quant_tier, veto_reason, on-chain summary, position size, risk %

import os
import csv
import io
from datetime import datetime
from typing import Dict, Any, List, Optional
import safe_json

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SIGNAL_LOG_FILE = os.path.join(DATA_DIR, 'testing_signal_log.json')
MAX_LOG_ENTRIES = 500  # Keep last N entries


def _load_log() -> List[Dict[str, Any]]:
    data = safe_json.load(SIGNAL_LOG_FILE, default=list)
    return data if isinstance(data, list) else []


def _save_log(entries: List[Dict[str, Any]]):
    safe_json.save(SIGNAL_LOG_FILE, entries[-MAX_LOG_ENTRIES:], default=str)


def log_signal(
    coin: Dict[str, Any],
    consensus_result: Dict[str, Any],
    position_sizing: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Append one ULTRA_BUY or STRONG_BUY signal to the testing log.
    Call from refresh_data() when final_signal is ULTRA_BUY or STRONG_BUY.
    """
    final_signal = (consensus_result or {}).get('consensus_signal', '')
    if final_signal not in ('ULTRA_BUY', 'STRONG_BUY'):
        return
    enh = coin.get('enhanced') or {}
    pos = position_sizing or {}
    entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'coin_id': coin.get('id', ''),
        'coin_name': coin.get('name', ''),
        'symbol': (coin.get('symbol') or '').upper(),
        'quant_tier': (consensus_result or {}).get('quant_tier', ''),
        'final_signal': final_signal,
        'veto_reason': (consensus_result or {}).get('veto_reason') or '',
        'sopr': enh.get('sopr'),
        'lth_sopr': enh.get('lth_sopr'),
        'exchange_netflow_24h': enh.get('exchange_netflow_24h'),
        'lth_supply_change_24h_pct': enh.get('lth_supply_change_24h_pct'),
        'whale_netflow': enh.get('whale_netflow', ''),
        'realized_cap_hodl_signal': enh.get('realized_cap_hodl_signal', ''),
        'position_size_usd': pos.get('position_size_usd'),
        'risk_pct': pos.get('max_loss_pct'),
        'pct_of_account': pos.get('pct_of_account'),
        'outcome_7d': None,
        'notes': '',
    }
    entries = _load_log()
    entries.append(entry)
    _save_log(entries)


def get_signal_log(limit: int = 20) -> List[Dict[str, Any]]:
    """Return the most recent signals (newest first)."""
    entries = _load_log()
    return list(reversed(entries[-limit:])) if limit else list(reversed(entries))


def get_testing_stats() -> Dict[str, Any]:
    """Return counts and hypothetical win rate (when outcomes recorded)."""
    entries = _load_log()
    total = len(entries)
    with_outcome = [e for e in entries if e.get('outcome_7d') is not None and str(e.get('outcome_7d')).strip() not in ('', 'None')]
    wins = sum(1 for e in with_outcome if str(e.get('outcome_7d', '')).lower() in ('win', 'w', '1', 'yes'))
    win_rate = round(wins / len(with_outcome) * 100, 1) if with_outcome else None
    ultra = sum(1 for e in entries if e.get('final_signal') == 'ULTRA_BUY')
    strong = sum(1 for e in entries if e.get('final_signal') == 'STRONG_BUY')
    return {
        'signals_logged': total,
        'ultra_buy_count': ultra,
        'strong_buy_count': strong,
        'outcomes_recorded': len(with_outcome),
        'hypothetical_win_rate_pct': win_rate,
    }


def export_signal_log_csv() -> str:
    """Return CSV string of full signal log (oldest first for spreadsheet)."""
    entries = _load_log()
    if not entries:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(['Date', 'Coin', 'Quant Tier', 'Final Signal', 'Veto Reason', 'SOPR', 'Netflow', 'LTH Δ', 'Whale', 'Position Size', 'Risk %', 'Outcome 7d', 'Notes'])
        return out.getvalue()
    cols = ['timestamp', 'symbol', 'quant_tier', 'final_signal', 'veto_reason', 'sopr', 'exchange_netflow_24h', 'lth_supply_change_24h_pct', 'whale_netflow', 'position_size_usd', 'risk_pct', 'outcome_7d', 'notes']
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Date', 'Coin', 'Quant Tier', 'Final Signal', 'Veto Reason', 'SOPR', 'Netflow', 'LTH Δ', 'Whale', 'Position Size', 'Risk %', 'Outcome 7d', 'Notes'])
    for e in entries:
        w.writerow([e.get(c) for c in cols])
    return out.getvalue()
