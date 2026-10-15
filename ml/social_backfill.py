# ml/social_backfill.py — Backfill volume_spike_ratio into ai_recommendations_v2.json
# using CoinGecko historical daily market data.
#
# volume_spike_ratio = 24h_volume_at_date / rolling_7d_avg_volume_prior_to_date
# A ratio >= 2.0 indicates unusual buying interest that the live scorer now rewards.
# This backfill ensures the optimizer has historical social-momentum context from day 1.

import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

import safe_json
from ml.config import RECOMMENDATIONS_FILE


_MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
_REQUEST_DELAY_S = 1.5   # stay well within free-tier rate limit (30 req/min)
_LOOKBACK_DAYS = 90


def _fetch_daily_volumes(coin_id: str, cg_key: str = None) -> Dict[str, float]:
    """
    Fetch daily volumes from CoinGecko market_chart for past 90 days.
    Returns {date_str: volume_usd} e.g. {"2026-03-01": 4.5e9}.
    """
    from resilient_http import resilient_get
    params = {"vs_currency": "usd", "days": _LOOKBACK_DAYS, "interval": "daily"}
    headers = {}
    if cg_key:
        headers["x-cg-demo-api-key"] = cg_key
    try:
        url = _MARKET_CHART_URL.format(coin_id=coin_id)
        resp = resilient_get(url, headers=headers, params=params, timeout=15, source="coingecko")
        resp.raise_for_status()
        data = resp.json()
        volumes = data.get("total_volumes") or []
        result: Dict[str, float] = {}
        for ts_ms, vol in volumes:
            date_str = datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
            result[date_str] = float(vol)
        return result
    except Exception as e:
        logger.warning("social_backfill: volume fetch failed for %s: %s", coin_id, e)
        return {}


def _compute_spike_ratio(date_str: str, volume_by_date: Dict[str, float]) -> float:
    """
    Compute volume_spike_ratio for a given date:
      24h volume on date / mean of the 7 prior days' volumes.
    Returns 1.0 if not enough history.
    """
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d")
        vol_today = volume_by_date.get(date_str)
        if vol_today is None:
            # Try the nearest available date (within ±1 day)
            for offset in (0, -1, 1):
                d = (target + timedelta(days=offset)).strftime("%Y-%m-%d")
                if d in volume_by_date:
                    vol_today = volume_by_date[d]
                    break
        if vol_today is None or vol_today <= 0:
            return 1.0
        prior_vols = []
        for i in range(1, 8):
            d = (target - timedelta(days=i)).strftime("%Y-%m-%d")
            if d in volume_by_date and volume_by_date[d] > 0:
                prior_vols.append(volume_by_date[d])
        if len(prior_vols) < 3:
            return 1.0
        avg_prior = sum(prior_vols) / len(prior_vols)
        if avg_prior <= 0:
            return 1.0
        return round(vol_today / avg_prior, 3)
    except Exception:
        return 1.0


def run(cg_key: str = None) -> Dict[str, Any]:
    """
    Backfill volume_spike_ratio into ai_recommendations_v2.json.
    Only processes entries that already have outcome_7d (resolved) and lack volume_spike_ratio.
    Returns stats dict.
    """
    import os
    if cg_key is None:
        from dotenv import load_dotenv
        load_dotenv()
        cg_key = os.getenv("COINGECKO_API_KEY") or None

    recs: List[Dict[str, Any]] = safe_json.load(str(RECOMMENDATIONS_FILE), default=list)
    if not isinstance(recs, list):
        recs = []

    # Identify which coins need backfill: have outcome_7d but no volume_spike_ratio
    needs_backfill: Dict[str, List[int]] = {}  # coin_id -> [indices]
    for i, rec in enumerate(recs):
        if rec.get("outcome_7d") is None:
            continue
        if rec.get("volume_spike_ratio") is not None:
            continue
        coin_id = rec.get("coin_id") or ""
        if not coin_id:
            continue
        needs_backfill.setdefault(coin_id, []).append(i)

    if not needs_backfill:
        logger.info("social_backfill: nothing to backfill")
        return {"coins_fetched": 0, "entries_updated": 0}

    logger.info("social_backfill: %d coins to backfill (%d entries)", len(needs_backfill), sum(len(v) for v in needs_backfill.values()))

    entries_updated = 0
    coins_fetched = 0

    for coin_id, indices in needs_backfill.items():
        volume_by_date = _fetch_daily_volumes(coin_id, cg_key=cg_key)
        if not volume_by_date:
            logger.debug("social_backfill: no volume data for %s — skipping", coin_id)
            time.sleep(_REQUEST_DELAY_S)
            continue

        coins_fetched += 1
        for idx in indices:
            rec = recs[idx]
            ts = rec.get("timestamp") or ""
            date_str = ts[:10]
            if not date_str or len(date_str) < 10:
                continue
            spike = _compute_spike_ratio(date_str, volume_by_date)
            rec["volume_spike_ratio"] = spike
            # Also compute is_trending_proxy: strong multi-day momentum = likely attracted social attention
            try:
                ch7d = rec.get("price_change_7d") or rec.get("outcome_7d") or 0
                ch24 = rec.get("price_change_24h") or 0
                rec["is_trending_proxy"] = bool(
                    (float(ch7d) > 15.0 and float(ch24) > 3.0)
                    or spike >= 3.0
                )
            except (TypeError, ValueError):
                rec["is_trending_proxy"] = spike >= 3.0
            entries_updated += 1

        time.sleep(_REQUEST_DELAY_S)

    safe_json.save(str(RECOMMENDATIONS_FILE), recs, default=str)
    logger.info("social_backfill: updated %d entries across %d coins", entries_updated, coins_fetched)
    return {"coins_fetched": coins_fetched, "entries_updated": entries_updated}
