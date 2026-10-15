# =============================================================================
# spread_logger.py — ambient BTC spread capture for the future maker-fill trial.
#
# WHY THIS EXISTS (STRATEGY.md v1.7b): the single proven cost lever is switching
# taker fills (1.20%/side measured) to maker fills (0.60%/side schedule). Whether
# maker fills are ACHIEVABLE depends on spread width and fill probability — data
# that cannot be backfilled. One top-of-book snapshot per hourly refresh starts
# the clock now, so when the promotion ladder reaches live execution, the
# maker-fill trial has months of spread history instead of none.
#
# Deliberately minimal: BTC-USD only (the sole live candidate), best-effort
# (never raises into the refresh), public endpoint (no keys), one JSONL row/hour.
# =============================================================================

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SPREAD_FILE = 'data/spread_snapshots.jsonl'
# 2026-09-26 (v2.0): rows now also carry open paper-alt products (up to ~40/refresh), so
# the cap is sized for ~1.5 years at that rate; rotation archives to .bak, nothing is lost.
SPREAD_MAX_LINES = 500_000
BOOK_URL_TMPL = 'https://api.exchange.coinbase.com/products/{product}/book'
BOOK_URL = BOOK_URL_TMPL.format(product='BTC-USD')


def capture_spread_snapshot(product: str = 'BTC-USD') -> Optional[Dict[str, Any]]:
    """Record one top-of-book snapshot for *product* (default BTC-USD). Returns the row
    or None. Best-effort by contract: any failure logs at debug and returns None.

    v2.0: alt products are captured too (market_state_logger), because the alt paper
    cohort's fills assume 0.05% slip on every coin and nothing ever measured alt spreads.
    """
    try:
        from resilient_http import resilient_get
        resp = resilient_get(BOOK_URL_TMPL.format(product=product), params={'level': 1},
                             timeout=10, source='coinbase', max_retries=1)
        resp.raise_for_status()
        book = resp.json()
        bid = float(book['bids'][0][0])
        ask = float(book['asks'][0][0])
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        mid = (bid + ask) / 2.0
        row = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'product': product,
            'bid': bid, 'ask': ask,
            'spread_pct': round((ask - bid) / mid * 100, 5),
        }
        import safe_json
        safe_json.append_jsonl(SPREAD_FILE, row, max_lines=SPREAD_MAX_LINES, default=str)
        return row
    except Exception as e:
        logger.debug('spread snapshot skipped: %s', e)
        return None
