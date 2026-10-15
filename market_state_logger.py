# =============================================================================
# market_state_logger.py — persist the market state the refresh already fetched.
#
# WHY THIS EXISTS (STRATEGY.md v2.0, 2026-09-26): the hourly refresh pulled a 250-coin
# market snapshot (price, mcap, volume, 1h/24h/7d/30d changes) plus regime inputs and
# then OVERWROTE it (coins_cache.json). refresh_snapshots.jsonl kept only signal flags —
# no prices, no indicators, no environment. Four months of forward, unfittable data were
# discarded hourly. The owner's open hypothesis ("alts run when the macro environment is
# alt-favorable") cannot be graded without an environment record, and no backtest can
# supply one that is immune to fitting. This module starts that record.
#
# Three outputs, all append-only / write-once, all best-effort (never raise into refresh):
#   1. data/market_state.jsonl       — one ENVIRONMENT row per refresh (~500 bytes)
#   2. data/market_daily/YYYY-MM-DD.json — one compact FULL-UNIVERSE snapshot per UTC day
#   3. data/spread_snapshots.jsonl   — top-of-book for each OPEN paper-alt product
#
# Environment definitions are fixed here and must not be tuned against outcomes
# (H-2026Q4-2 pins ETH/BTC vs its 50-day SMA a priori). Adding a feature is fine;
# redefining one is a new trial.
# =============================================================================
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

MARKET_STATE_FILE = os.path.join('data', 'market_state.jsonl')
MARKET_STATE_MAX_LINES = 200_000          # ~22 years at 24 rows/day; rotates to .bak
MARKET_DAILY_DIR = os.path.join('data', 'market_daily')
ETH_BTC_CACHE = os.path.join('data', 'eth_btc_cache.json')
ETH_BTC_TTL_SEC = 12 * 3600               # same cadence as the BTC risk-on series
ETH_BTC_SMA_DAYS = 50                     # pinned by H-2026Q4-2 — do not tune
ALT_SPREAD_MAX_PRODUCTS = 40              # public Coinbase book calls per refresh, cap

# Excluded from breadth: pegged or wrapped assets are not "alts" in any hypothesis here.
NON_ALT_SYMBOLS = frozenset({
    'BTC', 'USDT', 'USDC', 'USDE', 'DAI', 'FDUSD', 'USDS', 'USD1', 'PYUSD', 'TUSD', 'BUIDL',
    'SUSDE', 'USDF', 'USDX', 'BSC-USD', 'STETH', 'WSTETH', 'WEETH', 'WBETH', 'RSETH', 'METH',
    'EZETH', 'RETH', 'SFRXETH', 'FRXETH', 'WBTC', 'CBBTC', 'SOLVBTC', 'LBTC', 'JITOSOL',
    'BNSOL', 'WSOL',
})

COMPACT_FIELDS = (
    'id', 'symbol', 'market_cap_rank', 'current_price', 'market_cap', 'total_volume',
    'price_change_percentage_1h_in_currency', 'price_change_percentage_24h_in_currency',
    'price_change_percentage_7d_in_currency', 'price_change_percentage_30d_in_currency',
    'ath_change_percentage',
)
_SHORT = {  # compact keys keep the daily files ~40KB
    'market_cap_rank': 'rank', 'current_price': 'px', 'market_cap': 'mcap',
    'total_volume': 'vol24', 'price_change_percentage_1h_in_currency': 'ch1h',
    'price_change_percentage_24h_in_currency': 'ch24h',
    'price_change_percentage_7d_in_currency': 'ch7d',
    'price_change_percentage_30d_in_currency': 'ch30d', 'ath_change_percentage': 'ath_dd',
}


def _f(x) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _is_alt(c: Dict[str, Any]) -> bool:
    sym = str(c.get('symbol') or '').upper()
    if not sym or sym in NON_ALT_SYMBOLS:
        return False
    px, ch7 = _f(c.get('current_price')), _f(c.get('price_change_percentage_7d_in_currency'))
    # Unlisted stablecoins: pegged near $1 and not moving.
    if px is not None and abs(px - 1.0) < 0.02 and (ch7 is None or abs(ch7) < 1.0):
        return False
    return True


# ----------------------------------------------------------------------------- pure
def compute_breadth(coins: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Cross-sectional alt participation relative to BTC, from the coins the refresh
    already holds. Pure; no network."""
    coins = list(coins or [])
    btc = next((c for c in coins if str(c.get('symbol') or '').upper() == 'BTC'
                or c.get('id') == 'bitcoin'), None)
    alts = [c for c in coins if _is_alt(c)]
    b7 = _f((btc or {}).get('price_change_percentage_7d_in_currency'))
    b30 = _f((btc or {}).get('price_change_percentage_30d_in_currency'))

    def _pct(pred) -> Optional[float]:
        vals = [c for c in alts if pred(c) is not None]
        if not vals:
            return None
        return round(100.0 * sum(1 for c in vals if pred(c)) / len(vals), 1)

    beat7 = _pct(lambda c: None if b7 is None or _f(c.get('price_change_percentage_7d_in_currency')) is None
                 else _f(c['price_change_percentage_7d_in_currency']) > b7)
    beat30 = _pct(lambda c: None if b30 is None or _f(c.get('price_change_percentage_30d_in_currency')) is None
                  else _f(c['price_change_percentage_30d_in_currency']) > b30)
    up24 = _pct(lambda c: None if _f(c.get('price_change_percentage_24h_in_currency')) is None
                else _f(c['price_change_percentage_24h_in_currency']) > 0)
    mcaps = [_f(c.get('market_cap')) or 0.0 for c in coins]
    total = sum(mcaps)
    btc_mcap = _f((btc or {}).get('market_cap')) or 0.0
    return {
        'alts_n': len(alts),
        'alts_beat_btc_7d_pct': beat7,
        'alts_beat_btc_30d_pct': beat30,
        'alts_up_24h_pct': up24,
        'btc_dominance_topn_pct': round(100.0 * btc_mcap / total, 2) if total and btc_mcap else None,
        'total_mcap_topn_usd': round(total) if total else None,
        'universe_n': len(coins),
    }


def compute_eth_btc(eth_closes: List[float], btc_closes: List[float],
                    sma_days: int = ETH_BTC_SMA_DAYS) -> Dict[str, Any]:
    """ETH/BTC ratio vs its own SMA — the pinned alt-favorable proxy (H-2026Q4-2).
    Uses the last len(min) aligned closes; the SMA includes today's close (the live
    reading); the backtest form (SMA through t-1) is applied in the trial harness."""
    n = min(len(eth_closes or []), len(btc_closes or []))
    if n < sma_days + 1:
        return {'eth_btc': None, 'eth_btc_sma50': None, 'eth_btc_vs_sma50_pct': None,
                'eth_btc_30d_pct': None, 'eth_btc_above_sma50': None}
    ratio = [e / b for e, b in zip(eth_closes[-n:], btc_closes[-n:]) if b]
    cur = ratio[-1]
    sma = sum(ratio[-sma_days:]) / sma_days
    ch30 = (cur / ratio[-31] - 1) * 100 if len(ratio) >= 31 else None
    return {
        'eth_btc': round(cur, 6),
        'eth_btc_sma50': round(sma, 6),
        'eth_btc_vs_sma50_pct': round((cur / sma - 1) * 100, 2),
        'eth_btc_30d_pct': round(ch30, 2) if ch30 is not None else None,
        'eth_btc_above_sma50': bool(cur > sma),
    }


def compact_coin(c: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k in COMPACT_FIELDS:
        if k in c and c[k] is not None:
            v = c[k]
            if isinstance(v, float):
                v = round(v, 6 if k == 'current_price' else 3)
            out[_SHORT.get(k, k)] = v
    return out


def build_env_row(coins: Iterable[Dict[str, Any]], regime_data: Optional[Dict[str, Any]],
                  risk: Optional[Dict[str, Any]], eth_btc: Optional[Dict[str, Any]],
                  ts: Optional[str] = None) -> Dict[str, Any]:
    coins = list(coins or [])
    btc = next((c for c in coins if c.get('id') == 'bitcoin'
                or str(c.get('symbol') or '').upper() == 'BTC'), None) or {}
    chars = (regime_data or {}).get('characteristics') or {}
    row = {
        'ts': ts or datetime.now(timezone.utc).isoformat(),
        'btc_price': _f(btc.get('current_price')),
        'btc_ch7d': _f(btc.get('price_change_percentage_7d_in_currency')),
        'btc_ch30d': _f(btc.get('price_change_percentage_30d_in_currency')),
        'btc_vs_ma200_pct': (risk or {}).get('btc_vs_ma200_pct'),
        'risk_on': (risk or {}).get('risk_on'),
        'risk_source': (risk or {}).get('source'),
        'regime': (regime_data or {}).get('regime'),
        'fear_greed': _f(chars.get('fear_greed')),
        'alt_breadth_24h_pct_regime': _f(chars.get('alt_breadth_24h_pct')),
    }
    row.update(compute_breadth(coins))
    row.update(eth_btc or compute_eth_btc([], []))
    return row


# ------------------------------------------------------------------------ side effects
def _eth_btc_live() -> Dict[str, Any]:
    """ETH/BTC block, disk-cached 12h. Never raises; returns None-filled block on failure."""
    now = time.time()
    try:
        if os.path.exists(ETH_BTC_CACHE) and (now - os.path.getmtime(ETH_BTC_CACHE)) < ETH_BTC_TTL_SEC:
            with open(ETH_BTC_CACHE, encoding='utf-8') as f:
                v = json.load(f)
            if isinstance(v, dict) and 'eth_btc' in v:
                return v
    except Exception:
        pass
    try:
        from risk_regime import _fetch_btc_daily_closes
        btc = _fetch_btc_daily_closes(220, coin_id='bitcoin')
        eth = _fetch_btc_daily_closes(220, coin_id='ethereum')
        block = compute_eth_btc(eth, btc)
        if block.get('eth_btc') is not None:
            import safe_json
            safe_json.check_test_write(ETH_BTC_CACHE)
            os.makedirs(os.path.dirname(ETH_BTC_CACHE), exist_ok=True)
            with open(ETH_BTC_CACHE, 'w', encoding='utf-8') as f:
                json.dump(block, f)
        return block
    except Exception as e:
        logger.debug('eth/btc feature skipped: %s', e)
        return compute_eth_btc([], [])


def write_daily_snapshot(coins: Iterable[Dict[str, Any]], day: Optional[str] = None,
                         directory: Optional[str] = None) -> Optional[str]:
    """Write-once per UTC day: the compact full universe. Returns the path if written.
    `directory` resolves at CALL time (a def-time default ignored monkeypatching and let
    a dry run target the live folder)."""
    directory = directory or MARKET_DAILY_DIR
    day = day or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    path = os.path.join(directory, f'{day}.json')
    if os.path.exists(path):
        return None
    rows = [compact_coin(c) for c in (coins or []) if c.get('symbol')]
    if not rows:
        return None
    import safe_json
    safe_json.check_test_write(path)
    os.makedirs(directory, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'day': day, 'captured_at': datetime.now(timezone.utc).isoformat(),
                   'n': len(rows), 'coins': rows}, f, separators=(',', ':'))
    os.replace(tmp, path)
    return path


def _open_paper_alt_products() -> List[str]:
    try:
        from trade_journal import get_open_positions
        syms = []
        for p in get_open_positions():
            if (p.get('source') or '') != 'paper_trade':
                continue
            # The journal stores the ticker as 'coin_symbol'; 'symbol' is absent on these
            # rows (first live run 2026-09-26 captured zero alt books because of this).
            s = str(p.get('coin_symbol') or p.get('symbol') or '').upper()
            if s and s != 'BTC' and s not in syms:
                syms.append(s)
        return [f'{s}-USD' for s in syms[:ALT_SPREAD_MAX_PRODUCTS]]
    except Exception as e:
        logger.debug('open alt products unavailable: %s', e)
        return []


def record_market_state(coins: Iterable[Dict[str, Any]], regime_data: Optional[Dict[str, Any]],
                        risk: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Called once per refresh from app.py. Best-effort; returns the env row or None."""
    coins = list(coins or [])
    if not coins:
        return None
    try:
        if risk is None:
            from risk_regime import compute_btc_risk_on
            risk = compute_btc_risk_on()
        row = build_env_row(coins, regime_data, risk, _eth_btc_live())
        import safe_json
        safe_json.append_jsonl(MARKET_STATE_FILE, row, max_lines=MARKET_STATE_MAX_LINES, default=str)
    except Exception as e:
        logger.debug('market state row skipped: %s', e)
        row = None
    try:
        write_daily_snapshot(coins)
    except Exception as e:
        logger.debug('daily market snapshot skipped: %s', e)
    try:
        from spread_logger import capture_spread_snapshot
        for product in _open_paper_alt_products():
            capture_spread_snapshot(product)
    except Exception as e:
        logger.debug('alt spread capture skipped: %s', e)
    return row
