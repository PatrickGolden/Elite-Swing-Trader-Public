"""
One-shot backfill: record model performance outcomes for all closed live trades
whose original prediction was rotated out of the 500-entry buffer.

Run once from the project root:
    python scripts/backfill_model_outcomes.py
"""

import json
import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model_performance_tracker import record_trade_outcome

POSITIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'positions.json')

CONF_MAP = {'Very High': 90, 'High': 80, 'Medium': 70, 'Low': 50}


def run():
    with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
        positions = json.load(f)

    closed_live = [
        p for p in positions
        if p.get('status') == 'CLOSED'
        and not p.get('is_test')
        and p.get('model_prediction_ts')
        and p.get('pnl_pct') is not None
    ]

    print(f"Found {len(closed_live)} closed live positions with prediction timestamps")

    matched = synthetic = skipped = 0
    for pos in closed_live:
        pred_ts = pos['model_prediction_ts']
        coin_id = pos.get('coin_id', '')
        symbol = (pos.get('coin_symbol') or '').upper()
        signal = pos.get('signal', 'BUY')
        conf = CONF_MAP.get((pos.get('ai_confidence') or '').strip(), 60)
        regime = pos.get('market_regime') or 'UNKNOWN'
        pnl_pct = float(pos['pnl_pct'])

        ok = record_trade_outcome(
            coin_id=coin_id,
            symbol=symbol,
            signal=signal,
            confidence=conf,
            regime=regime,
            pnl_pct=pnl_pct,
            pred_ts=pred_ts,
        )
        if ok:
            matched += 1
        else:
            skipped += 1

    print(f"Done — recorded {matched} outcomes ({skipped} skipped)")
    print("Check model_performance.json to verify 'outcome_source' distribution.")


if __name__ == '__main__':
    run()
