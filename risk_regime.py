# =============================================================================
# risk_regime.py — Stage 1b market-trend (risk-on/off) master gate.
#
# risk_on = (BTC close > 200d-MA * (1 + band%)) AND (BTC 30d-change > 0)
#
# Evidence (blueprint Sec 8-9): on a broad 72-coin universe, 30d-momentum entries
# gated by BTC>200d-MA earned ~+6.7% costed OOS expectancy, significant in both
# walk-forward halves; the same entries in risk-off were flat/negative, and
# contrarian fear-dip buying in risk-on was catastrophic (-9..-11%).
#
# Design: network-isolated from market_regime (computed here, injected into the
# regime data dict). Cached to disk (12h) + memory. FAIL-CLOSED: if the 200d
# series is unavailable, fall back to BTC 30d-change>0; if that is also missing,
# return risk_on=False (suppress new longs rather than fabricate risk-on).
# =============================================================================
import json
import os
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_CACHE_PATH = os.path.join('data', 'btc_risk_on_cache.json')
_TTL = 12 * 3600  # 12h for successful computations
# Failed computations ('unavailable') must NOT poison the cache for 12h — a transient
# network error would blind the rotation/lean gates for half a day (seen 2026-06-11:
# one SSL hiccup cached risk_on=unavailable overnight). Retry failures quickly.
_FAIL_TTL = 15 * 60
_mem = {'ts': 0.0, 'val': None}


def _effective_ttl(val) -> float:
    if isinstance(val, dict) and val.get('source') == 'unavailable':
        return _FAIL_TTL
    return _TTL


def _fetch_btc_daily_closes(days: int = 220, coin_id: str = 'bitcoin'):
    """Daily closes via CoinGecko (daily granularity for days>90, within free 365d cap).
    Defaults to BTC; market_state_logger reuses it for ETH (ETH/BTC environment feature)."""
    from resilient_http import resilient_get
    cg_key = os.getenv('COINGECKO_API_KEY', '')
    headers = {'x-cg-demo-api-key': cg_key} if cg_key else {}
    resp = resilient_get(
        f'https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart',
        params={'vs_currency': 'usd', 'days': str(days)},
        headers=headers, timeout=20, source='coingecko', max_retries=2,
    )
    resp.raise_for_status()
    prices = resp.json().get('prices', [])
    byday = {}
    for ts, px in prices:
        d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime('%Y-%m-%d')
        byday[d] = float(px)
    return [byday[d] for d in sorted(byday)]


def _compute_fresh(band_pct: float, fallback_30d):
    """Compute risk_on from a fresh BTC daily series; fail-closed to 30d fallback."""
    try:
        closes = _fetch_btc_daily_closes(220)
        if len(closes) >= 200:
            ma200 = sum(closes[-200:]) / 200.0
            close = closes[-1]
            ch30 = ((close / closes[-31]) - 1.0) * 100 if len(closes) >= 31 else (
                float(fallback_30d) if fallback_30d is not None else 0.0)
            vs_ma = (close / ma200 - 1.0) * 100 if ma200 else None
            # Conservative band: must be clearly above the MA to count as risk-on (anti-whipsaw).
            risk_on = (close > ma200 * (1 + band_pct / 100.0)) and (ch30 > 0)
            return {
                'risk_on': bool(risk_on),
                'btc_vs_ma200_pct': round(vs_ma, 2) if vs_ma is not None else None,
                'btc_30d_change': round(ch30, 2),
                'source': 'ma200',
            }
        logger.warning('[risk_regime] BTC daily series too short (%d) — using 30d fallback', len(closes))
    except Exception as e:
        logger.warning('[risk_regime] BTC 200d-MA fetch failed (%s) — using 30d fallback', e)

    if fallback_30d is not None:
        return {
            'risk_on': bool(float(fallback_30d) > 0),
            'btc_vs_ma200_pct': None,
            'btc_30d_change': round(float(fallback_30d), 2),
            'source': 'fallback_30d',
        }
    # Nothing available: fail-closed (no new longs).
    return {'risk_on': False, 'btc_vs_ma200_pct': None, 'btc_30d_change': None, 'source': 'unavailable'}


def compute_btc_risk_on(band_pct: float = 1.5, btc_30d_change_fallback=None, force_refresh: bool = False) -> dict:
    """Cached risk-on computation. Returns dict with risk_on/btc_vs_ma200_pct/btc_30d_change/source.

    Never raises. 12h memory + disk cache to avoid per-refresh network cost.
    """
    now = time.time()
    if not force_refresh and _mem['val'] is not None and (now - _mem['ts']) < _effective_ttl(_mem['val']):
        return _mem['val']
    if not force_refresh:
        try:
            if os.path.exists(_CACHE_PATH):
                with open(_CACHE_PATH) as f:
                    v = json.load(f)
                if (isinstance(v, dict) and 'risk_on' in v
                        and (now - os.path.getmtime(_CACHE_PATH)) < _effective_ttl(v)):
                    _mem['ts'], _mem['val'] = now, v
                    return v
        except Exception:
            pass

    result = _compute_fresh(band_pct, btc_30d_change_fallback)
    _mem['ts'], _mem['val'] = now, result
    try:
        os.makedirs('data', exist_ok=True)
        with open(_CACHE_PATH, 'w') as f:
            json.dump(result, f)
    except Exception:
        pass
    return result
