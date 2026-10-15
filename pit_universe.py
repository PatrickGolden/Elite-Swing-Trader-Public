# =============================================================================
# pit_universe.py — point-in-time crypto universe builder (STRATEGY.md gate G1).
#
# Kills the survivorship bias in stage1c_replay's hand-picked 30-survivor universe
# by reconstructing "top-N by market cap AS OF each historical month" from
# CoinMarketCap's weekly snapshot archive (public data-api behind
# coinmarketcap.com/historical/, available back to 2013), including coins that
# later died or were delisted.
#
# One-time polite fetch: ~1 request per month (first Sunday), cached forever under
# data/pit_universe/snapshots/, resumable, 2s between requests, identifying UA.
#
# Outputs
#   data/pit_universe/snapshots/YYYY-MM-DD.json   raw monthly top-200 snapshot
#   data/pit_universe/membership.json             {"YYYY-MM": [{symbol,name,rank,mcap}...]}
#                                                 + provenance (git sha, params, dates)
#
# The replay consumes membership.json: a coin is entry-eligible on day d only if it
# was in the point-in-time top-N that month AND tradable on Coinbase that day
# (listing window from Coinbase candles; delist → forced exit, STRATEGY.md G1).
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import tls_setup  # noqa: F401  (OS trust store before any SSL context)

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(__file__)
PIT_DIR = os.path.join(_DIR, "data", "pit_universe")
SNAP_DIR = os.path.join(PIT_DIR, "snapshots")
MEMBERSHIP_FILE = os.path.join(PIT_DIR, "membership.json")

SNAPSHOT_URL = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical"
USER_AGENT = "Mozilla/5.0 (personal research; one-time monthly snapshot fetch; contact via repo)"
REQUEST_DELAY_S = 2.0
SNAPSHOT_LIMIT = 200          # rows fetched per snapshot (top-200 covers any reasonable top-N)
DEFAULT_TOP_N = 50            # membership cut used by the gate
DEFAULT_START = "2018-01"     # matches stage1c_replay daily-OHLCV start
USD_CONVERT_ID = 2781         # CMC convertId for USD

# Stablecoins and exchange-wrapped assets are never trade candidates; excluding them
# from membership keeps the top-N slots for actual candidates (BTC stays: regime anchor).
EXCLUDED_SYMBOLS = frozenset({
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "PAX", "UST", "USTC", "GUSD",
    "FDUSD", "PYUSD", "USDE", "USDS", "HUSD", "SUSD", "EURS", "WBTC", "WETH",
    "STETH", "WSTETH", "RETH", "CBETH", "WBETH", "LEO", "OKB", "CRO", "BGB", "HT",
})


def first_sunday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7)


def month_range(start_ym: str, end_ym: Optional[str] = None) -> List[date]:
    """First-Sunday snapshot dates from start_ym to end_ym inclusive (defaults to now)."""
    sy, sm = int(start_ym[:4]), int(start_ym[5:7])
    if end_ym:
        ey, em = int(end_ym[:4]), int(end_ym[5:7])
    else:
        today = datetime.now(timezone.utc).date()
        ey, em = today.year, today.month
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        snap = first_sunday(y, m)
        if snap <= datetime.now(timezone.utc).date():
            out.append(snap)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _snap_path(d: date) -> str:
    return os.path.join(SNAP_DIR, f"{d.isoformat()}.json")


def fetch_snapshot(d: date, session=None) -> Optional[List[Dict[str, Any]]]:
    """Fetch (or load cached) top-SNAPSHOT_LIMIT rows for snapshot date d."""
    p = _snap_path(d)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass  # corrupt cache → refetch
    import requests
    sess = session or requests
    try:
        r = sess.get(
            SNAPSHOT_URL,
            params={"date": d.isoformat(), "limit": SNAPSHOT_LIMIT, "start": 1,
                    "convertId": USD_CONVERT_ID},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if r.status_code != 200:
            logger.warning("  [pit] %s: HTTP %s — skipped", d, r.status_code)
            return None
        rows = r.json().get("data") or []
    except Exception as e:
        logger.warning("  [pit] %s: fetch failed (%s)", d, e)
        return None
    slim = []
    for x in rows:
        q = (x.get("quotes") or [{}])[0]
        slim.append({
            "rank": x.get("cmcRank"),
            "symbol": (x.get("symbol") or "").upper(),
            "name": x.get("name"),
            "cmc_id": x.get("id"),
            "market_cap": q.get("marketCap"),
        })
    if slim:
        os.makedirs(SNAP_DIR, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(slim, f)
    return slim or None


def fetch_all_snapshots(start_ym: str = DEFAULT_START, end_ym: Optional[str] = None) -> Dict[str, int]:
    """Fetch every monthly snapshot (cached → free). Returns {iso_date: n_rows}."""
    got: Dict[str, int] = {}
    dates = month_range(start_ym, end_ym)
    n_fetch = sum(1 for d in dates if not os.path.exists(_snap_path(d)))
    logger.info("[pit] %d monthly snapshots (%d to fetch, %d cached)",
                len(dates), n_fetch, len(dates) - n_fetch)
    import requests
    sess = requests.Session()
    for d in dates:
        cached = os.path.exists(_snap_path(d))
        rows = fetch_snapshot(d, session=sess)
        got[d.isoformat()] = len(rows) if rows else 0
        if not cached:
            time.sleep(REQUEST_DELAY_S)
    return got


def build_membership(top_n: int = DEFAULT_TOP_N, start_ym: str = DEFAULT_START,
                     end_ym: Optional[str] = None) -> Dict[str, Any]:
    """Reduce cached snapshots to monthly point-in-time top-N membership."""
    membership: Dict[str, List[Dict[str, Any]]] = {}
    for d in month_range(start_ym, end_ym):
        rows = None
        p = _snap_path(d)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                rows = json.load(f)
        if not rows:
            continue
        eligible = [r for r in rows if r.get("symbol") not in EXCLUDED_SYMBOLS
                    and r.get("rank") is not None]
        eligible.sort(key=lambda r: r["rank"])
        membership[d.isoformat()[:7]] = eligible[:top_n]

    all_syms = sorted({r["symbol"] for rows in membership.values() for r in rows})
    last_month = max(membership) if membership else None
    current_syms = {r["symbol"] for r in membership.get(last_month, [])} if last_month else set()
    gone = sorted(s for s in all_syms if s not in current_syms)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "coinmarketcap.com/historical monthly first-Sunday snapshots (public data-api)",
        "params": {"top_n": top_n, "start": start_ym, "snapshot_limit": SNAPSHOT_LIMIT,
                   "excluded_symbols": sorted(EXCLUDED_SYMBOLS)},
        "months": len(membership),
        "distinct_symbols": len(all_syms),
        "symbols_no_longer_in_topN": gone,
        "membership": membership,
    }
    os.makedirs(PIT_DIR, exist_ok=True)
    with open(MEMBERSHIP_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    logger.info("[pit] membership: %d months, %d distinct symbols (%d since dropped out of top-%d)",
                len(membership), len(all_syms), len(gone), top_n)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Point-in-time top-N universe builder (G1)")
    ap.add_argument("--start", default=DEFAULT_START, help="first month YYYY-MM (default 2018-01)")
    ap.add_argument("--end", default=None, help="last month YYYY-MM (default: now)")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP_N, help="membership cut (default 50)")
    args = ap.parse_args()
    fetch_all_snapshots(args.start, args.end)
    out = build_membership(args.top, args.start, args.end)
    print(f"months={out['months']} distinct={out['distinct_symbols']} "
          f"dropped_out={len(out['symbols_no_longer_in_topN'])}")
    print("no longer in top-N:", ", ".join(out["symbols_no_longer_in_topN"][:40]),
          "..." if len(out["symbols_no_longer_in_topN"]) > 40 else "")


if __name__ == "__main__":
    main()
