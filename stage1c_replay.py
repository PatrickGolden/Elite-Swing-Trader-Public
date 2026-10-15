# =============================================================================
# stage1c_replay.py — Multi-year, daily-resolution Stage 1c walk-forward gate.
#
# WHY THIS EXISTS
#   historical_replay.py is hardwired to CoinGecko HOURLY data, capped at 90
#   days on the free tier — far too short to run the costed, out-of-sample
#   walk-forward gate the blueprint (§9.5) requires. This module runs the EXACT
#   same decision pipeline (calculate_indicators → score_coin →
#   generate_quant_signal → simulate) at 1-bar-per-day resolution over many
#   YEARS of CCXT daily OHLCV, with a TRUE BTC 200-day moving average feeding
#   the Stage 1b risk-on gate (not the 30d-change proxy the 90d replay uses).
#
# WHAT IT PRODUCES
#   An A/B walk-forward gate: it runs the pipeline twice — once with the Stage 1b
#   risk-on gate ACTIVE and once with it DISABLED (legacy baseline) — then
#   reports the §9.5 PASS/FAIL verdict (costed expectancy > 0 in both halves,
#   beats baseline by ≥ target ΔE, contrarian-in-uptrend leak eliminated, robust
#   to a 1.5% round-trip cost stress).
#
# HONEST CAVEATS (documented, not hidden)
#   - DAILY resolution for entries AND exits (free tier has no multi-year
#     hourly), so intraday stop/trail fidelity is approximate (uses daily H/L).
#   - Indicators (RSI/BB/ATR/MTF) are computed on DAILY bars, so their semantics
#     differ from the live HOURLY pipeline — this is a daily-resolution proxy of
#     the live strategy, matching the resolution at which the entry edge was
#     proven (§8). Still vastly more powerful than a single 90-day window.
#   - Coinbase-listed + currently-trading ⇒ residual survivorship bias.
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Wire OS trust store into ssl BEFORE ccxt/requests create any SSL context.
import tls_setup  # noqa: F401

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "historical_cache", "daily")
SUMMARY_FILE = os.path.join(DATA_DIR, "stage1c_gate_summary.json")

# Costs. simulate_daily charges slippage on BOTH sides (entry ×1.003, exit ×0.997)
# plus per-side fees, so the true modeled round trip is ~0.96% — NOT the 0.66%
# historical_replay declares (its constant has the same per-side/total mismatch and
# is consumed nowhere). Audit 2026-06-11 (M1): the declared constant must equal what
# the simulator charges, and the rotation benchmark must bear the same drag, so the
# measured-cost overlays (MEASURED_RT_COST_PCT − ROUND_TRIP_COST_PCT) are symmetric.
SLIPPAGE_PCT = 0.30
FEE_PCT = 0.18
ROUND_TRIP_COST_PCT = SLIPPAGE_PCT * 2 + FEE_PCT * 2  # 0.96% actually modeled
COST_STRESS_PCT = 1.5  # §9.5 robustness stress

MAX_HOLD_DAYS = 21      # matches live STALE_POSITION_MAX_DAYS / TRADE_MAX_HOLD
SPARKLINE_DAYS = 150    # daily closes fed to calculate_indicators
MA200_DAYS = 200
WF_SPLIT_YEAR = 2023    # train: entry year < 2023; test: entry year >= 2023

# Universe: Coinbase USD pairs spanning majors + mid-caps + memecoins.
# (gecko_id, SYMBOL, ccxt_symbol). gecko_id only matters so BTC is detectable.
UNIVERSE: List[Tuple[str, str, str]] = [
    ("bitcoin", "BTC", "BTC/USD"), ("ethereum", "ETH", "ETH/USD"),
    ("solana", "SOL", "SOL/USD"), ("ripple", "XRP", "XRP/USD"),
    ("cardano", "ADA", "ADA/USD"), ("dogecoin", "DOGE", "DOGE/USD"),
    ("chainlink", "LINK", "LINK/USD"), ("avalanche-2", "AVAX", "AVAX/USD"),
    ("polkadot", "DOT", "DOT/USD"), ("litecoin", "LTC", "LTC/USD"),
    ("uniswap", "UNI", "UNI/USD"), ("near", "NEAR", "NEAR/USD"),
    ("cosmos", "ATOM", "ATOM/USD"), ("aptos", "APT", "APT/USD"),
    ("sui", "SUI", "SUI/USD"), ("polygon", "MATIC", "MATIC/USD"),
    ("bitcoin-cash", "BCH", "BCH/USD"), ("ethereum-classic", "ETC", "ETC/USD"),
    ("filecoin", "FIL", "FIL/USD"), ("algorand", "ALGO", "ALGO/USD"),
    ("stellar", "XLM", "XLM/USD"), ("aave", "AAVE", "AAVE/USD"),
    ("maker", "MKR", "MKR/USD"), ("injective", "INJ", "INJ/USD"),
    ("render", "RNDR", "RNDR/USD"), ("fetch-ai", "FET", "FET/USD"),
    ("arbitrum", "ARB", "ARB/USD"), ("optimism", "OP", "OP/USD"),
    ("shiba-inu", "SHIB", "SHIB/USD"), ("pepe", "PEPE", "PEPE/USD"),
]

CONTRARIAN_PATHS = frozenset({
    "blood_in_the_street", "dip", "capitulation", "volatility_harvest",
})


# -----------------------------------------------------------------------------
# Data fetch (CCXT daily OHLCV, paginated + cached)
# -----------------------------------------------------------------------------

def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol.replace('/', '_')}.json")


def _load_daily_cache(symbol: str, max_age_h: int = 48) -> Optional[List[list]]:
    p = _cache_path(symbol)
    if not os.path.exists(p):
        return None
    if (time.time() - os.path.getmtime(p)) > max_age_h * 3600:
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _save_daily_cache(symbol: str, ohlcv: List[list]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(_cache_path(symbol), "w") as f:
            json.dump(ohlcv, f)
    except Exception:
        pass


def _make_exchange():
    import ccxt
    last_err = None
    for ex_id in ("coinbaseexchange", "coinbase"):
        try:
            ex = getattr(ccxt, ex_id)({"enableRateLimit": True})
            ex.load_markets()
            return ex
        except Exception as e:  # pragma: no cover
            last_err = e
            continue
    raise RuntimeError(f"No usable Coinbase CCXT exchange: {last_err}")


def fetch_daily_ohlcv(ex, ccxt_symbol: str, since_iso: str = "2018-01-01T00:00:00Z") -> List[list]:
    """Paginated daily OHLCV [[ts_ms,o,h,l,c,v],...] ascending, deduped.

    Walks from `since` to ~now in 300-bar pages. Empty batches (e.g. dates before
    a coin's listing) advance the cursor rather than stopping, so coins listed
    after the start date (SOL/ARB/APT/SUI/…) are still picked up at their listing.
    """
    day_ms = 86400_000
    now_ms = ex.milliseconds()
    since = ex.parse8601(since_iso)
    out: Dict[int, list] = {}
    while since < now_ms - day_ms:
        try:
            batch = ex.fetch_ohlcv(ccxt_symbol, timeframe="1d", since=since, limit=300)
        except Exception as e:
            logger.warning("  [fetch] %s error: %s", ccxt_symbol, e)
            break
        if not batch:
            # Pre-listing gap or sparse range: skip a page forward and keep looking.
            since += 300 * day_ms
            continue
        for row in batch:
            out[int(row[0])] = row
        last = int(batch[-1][0])
        # Always advance strictly forward to avoid loops; jump a full page if stalled.
        since = max(last + day_ms, since + 300 * day_ms) if last <= since else last + day_ms
        time.sleep(getattr(ex, "rateLimit", 250) / 1000.0)
    return [out[k] for k in sorted(out)]


def load_universe_daily(refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """Return {gecko_id: {symbol, ohlcv:[[ts,o,h,l,c,v]], closes, highs, lows, dates}}."""
    ex = None
    data: Dict[str, Dict[str, Any]] = {}
    for gecko_id, symbol, ccxt_symbol in UNIVERSE:
        ohlcv = None if refresh else _load_daily_cache(symbol)
        if ohlcv is None:
            if ex is None:
                ex = _make_exchange()
            ohlcv = fetch_daily_ohlcv(ex, ccxt_symbol)
            if ohlcv:
                _save_daily_cache(symbol, ohlcv)
            time.sleep(0.2)
        if not ohlcv or len(ohlcv) < (MA200_DAYS + MAX_HOLD_DAYS + 5):
            logger.warning("  [universe] %s: insufficient history (%d bars) — skip",
                           symbol, len(ohlcv) if ohlcv else 0)
            continue
        ohlcv = [r for r in ohlcv if r and len(r) >= 6 and r[4] and float(r[4]) > 0]
        # Reject coins with large internal gaps (e.g. delisting windows): the index-based
        # 30d/200d math assumes ~contiguous daily bars, so a multi-day hole would corrupt it.
        ts_sorted = sorted(int(r[0]) for r in ohlcv)
        max_gap_d = max((ts_sorted[i] - ts_sorted[i - 1]) for i in range(1, len(ts_sorted))) / 86400_000
        if max_gap_d > 7:
            logger.warning("  [universe] %s: %.0fd internal gap — skip (non-contiguous)", symbol, max_gap_d)
            continue
        ts = [int(r[0]) for r in ohlcv]
        data[gecko_id] = {
            "symbol": symbol,
            "ts": ts,
            "highs": [float(r[2]) for r in ohlcv],
            "lows": [float(r[3]) for r in ohlcv],
            "closes": [float(r[4]) for r in ohlcv],
            "vols": [float(r[5]) for r in ohlcv],
            "dates": [datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d") for t in ts],
            "by_date": {datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d"): i
                        for i, t in enumerate(ts)},
        }
        logger.info("  [universe] %s: %d daily bars (%s → %s)", symbol, len(ts),
                    data[gecko_id]["dates"][0], data[gecko_id]["dates"][-1])
    return data


# -----------------------------------------------------------------------------
# Point-in-time universe (STRATEGY.md gate G1 — kills survivorship bias)
#
# Membership: data/pit_universe/membership.json (pit_universe.py; CMC monthly
# snapshots — top-N by mcap AS OF each month, incl. coins that later died).
# Tradability: Coinbase Exchange products endpoint exposes DELISTED products
# (status='delisted') and still serves their full candle history, so the
# replay universe = PIT top-N ∩ Coinbase-listed-at-date — the live constraint,
# with the death spirals included instead of erased.
# Gaps/halts (e.g. XRP's 2021-23 US suspension): data is split into contiguous
# segments; a segment that ends before today is treated as a tradability
# terminal — positions still open there are force-exited at the last close
# minus DELIST_HAIRCUT_PCT (G1 delisting-return rule).
# -----------------------------------------------------------------------------

MEMBERSHIP_FILE = os.path.join(DATA_DIR, "pit_universe", "membership.json")
PIT_GATE_FILE = os.path.join(DATA_DIR, "stage1c_pit_gate.json")
DELIST_HAIRCUT_PCT = 20.0   # forced-exit haircut on delist/halt (G1)
MIN_SEGMENT_DAYS = SPARKLINE_DAYS + 40  # need indicator warmup inside a segment


def _fetch_daily_candles_raw(ex, product_id: str, since_iso: str = "2018-01-01T00:00:00Z") -> List[list]:
    """Daily candles via the raw products endpoint — works for DELISTED products,
    which ccxt's load_markets/fetch_ohlcv cannot see. Coinbase returns
    [time_s, low, high, open, close, volume]; normalize to [ts_ms, o, h, l, c, v]."""
    day = 86400
    start = int(datetime.fromisoformat(since_iso.replace("Z", "+00:00")).timestamp())
    now_s = int(time.time())
    out: Dict[int, list] = {}
    while start < now_s - day:
        end = min(start + 299 * day, now_s)
        iso = lambda s: datetime.fromtimestamp(s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            batch = ex.publicGetProductsIdCandles({
                "id": product_id, "granularity": 86400,
                "start": iso(start), "end": iso(end),
            })
        except Exception as e:
            msg = str(e)
            if "NotFound" in msg or "not found" in msg.lower():
                break  # product has no candle history at all
            logger.warning("  [pit-fetch] %s %s→%s: %s", product_id, iso(start), iso(end), e)
            batch = []
        for c in batch or []:
            ts = int(float(c[0]))
            out[ts] = [ts * 1000, float(c[3]), float(c[2]), float(c[1]), float(c[4]), float(c[5])]
        start = end + day  # always advance (empty window = pre-listing or halt)
        time.sleep(0.35)
    return [out[k] for k in sorted(out)]


def _segments(ohlcv: List[list], max_gap_days: int = 7) -> List[List[list]]:
    """Split rows into contiguous runs; a >max_gap_days hole (halt/suspension) splits."""
    segs: List[List[list]] = []
    cur: List[list] = []
    prev_ts = None
    for r in ohlcv:
        ts = int(r[0])
        if prev_ts is not None and (ts - prev_ts) > max_gap_days * 86400_000:
            if cur:
                segs.append(cur)
            cur = []
        cur.append(r)
        prev_ts = ts
    if cur:
        segs.append(cur)
    return segs


def _info_from_rows(symbol: str, rows: List[list], terminal: str) -> Dict[str, Any]:
    """Build the universe info dict (same shape as load_universe_daily) from candle rows.
    terminal: 'live_edge' (data ends because today does) or 'delisted'/'halt'."""
    ts = [int(r[0]) for r in rows]
    return {
        "symbol": symbol,
        "ts": ts,
        "highs": [float(r[2]) for r in rows],
        "lows": [float(r[3]) for r in rows],
        "closes": [float(r[4]) for r in rows],
        "vols": [float(r[5]) for r in rows],
        "dates": [datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d") for t in ts],
        "by_date": {datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d"): i
                    for i, t in enumerate(ts)},
        "terminal": terminal,
    }


def load_membership() -> Dict[str, Dict[str, int]]:
    """{'YYYY-MM': {SYMBOL: pit_rank}} from pit_universe.py output."""
    with open(MEMBERSHIP_FILE, encoding="utf-8") as f:
        m = json.load(f)
    return {month: {r["symbol"]: int(r["rank"]) for r in rows}
            for month, rows in m["membership"].items()}


def load_universe_pit(refresh: bool = False) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """PIT universe: every symbol ever in monthly top-N membership that has a Coinbase
    USD product (online OR delisted). Returns (universe infos keyed by 'SYM|seg', coverage)."""
    membership = load_membership()
    member_syms = sorted({s for mm in membership.values() for s in mm})

    import ccxt
    ex = ccxt.coinbaseexchange({"enableRateLimit": True})
    products = ex.publicGetProducts()
    prod_by_base: Dict[str, Dict[str, Any]] = {}
    for p in products:
        if p.get("quote_currency") != "USD":
            continue
        base = (p.get("base_currency") or "").upper()
        # Prefer online over delisted if both exist for a base
        if base not in prod_by_base or p.get("status") == "online":
            prod_by_base[base] = p

    universe: Dict[str, Dict[str, Any]] = {}
    no_product: List[str] = []
    n_delisted_used = 0
    for sym in member_syms:
        prod = prod_by_base.get(sym)
        if prod is None:
            no_product.append(sym)   # never tradable on Coinbase → not entry-eligible (live constraint)
            continue
        ohlcv = None if refresh else _load_daily_cache(sym, max_age_h=24 * 14)
        if ohlcv is None:
            ohlcv = _fetch_daily_candles_raw(ex, prod["id"])
            if ohlcv:
                _save_daily_cache(sym, ohlcv)
        if not ohlcv:
            no_product.append(sym)
            continue
        ohlcv = [r for r in ohlcv if r and len(r) >= 6 and r[4] and float(r[4]) > 0]
        delisted = prod.get("status") == "delisted"
        if delisted:
            n_delisted_used += 1
        segs = _segments(ohlcv)
        last_global_ts = int(ohlcv[-1][0]) if ohlcv else 0
        now_ms = time.time() * 1000
        for i, seg in enumerate(segs):
            if len(seg) < MIN_SEGMENT_DAYS:
                continue
            seg_end_ts = int(seg[-1][0])
            is_last_seg = seg_end_ts == last_global_ts
            recent = (now_ms - seg_end_ts) < 7 * 86400_000
            if is_last_seg and recent and not delisted:
                terminal = "live_edge"
            elif is_last_seg and delisted:
                terminal = "delisted"
            else:
                terminal = "halt"   # mid-history gap (suspension) or stale tail
            key = "bitcoin" if sym == "BTC" else f"{sym}|{i}"
            universe[key] = _info_from_rows(sym, seg, terminal)
    coverage = {
        "member_symbols": len(member_syms),
        "with_coinbase_data": len({v["symbol"] for v in universe.values()}),
        "delisted_products_used": n_delisted_used,
        "segments": len(universe),
        "never_or_no_data_on_coinbase": no_product,
    }
    logger.info("[pit] universe: %d member symbols → %d with Coinbase data "
                "(%d delisted products incl.), %d segments; %d never tradable: %s…",
                coverage["member_symbols"], coverage["with_coinbase_data"],
                n_delisted_used, coverage["segments"], len(no_product), ", ".join(no_product[:12]))
    return universe, coverage


def fetch_fear_greed_all() -> Dict[str, int]:
    from historical_replay import fetch_historical_fear_greed
    try:
        return fetch_historical_fear_greed(limit=0) or {}
    except Exception as e:
        logger.warning("  [fng] fetch failed (%s); defaulting to 50", e)
        return {}


# -----------------------------------------------------------------------------
# Coin dict (daily) — mirrors historical_replay.build_coin_dict contract
# -----------------------------------------------------------------------------

def _pct(a: float, b: float) -> float:
    return ((a - b) / b) * 100.0 if b else 0.0


def build_daily_coin_dict(info: Dict[str, Any], d: int, rank: int) -> Optional[Dict[str, Any]]:
    closes = info["closes"]
    if d < SPARKLINE_DAYS or d >= len(closes):
        return None
    price = closes[d]
    if price <= 0:
        return None
    spark = closes[d - SPARKLINE_DAYS + 1: d + 1]
    vols = info["vols"]
    avg_vol_usd = (sum(vols[max(0, d - 6): d + 1]) / max(1, len(vols[max(0, d - 6): d + 1]))) * price
    return {
        "id": info["symbol"].lower() if info["symbol"] != "BTC" else "bitcoin",
        "symbol": info["symbol"],
        "name": info["symbol"],
        "current_price": price,
        "total_volume": avg_vol_usd,
        "market_cap": 0.0,
        "sparkline_in_7d": {"price": spark},
        "price_change_percentage_1h_in_currency": 0.0,  # no intraday at daily res
        "price_change_percentage_24h": _pct(price, closes[d - 1]),
        "price_change_percentage_24h_in_currency": _pct(price, closes[d - 1]),
        "price_change_percentage_7d_in_currency": _pct(price, closes[d - 7]),
        "price_change_percentage_30d_in_currency": _pct(price, closes[d - 30]),
        "_is_top_3": rank < 3,
        "_is_top_10": rank < 10,
        "enhanced": {},
    }


def compute_btc_risk_on_daily(btc: Dict[str, Any], d: int, band_pct: float) -> Dict[str, Any]:
    """True trailing 200d-MA risk-on at BTC day index d (matches risk_regime logic)."""
    closes = btc["closes"]
    if d < MA200_DAYS:
        return {"risk_on": False, "btc_vs_ma200_pct": None, "btc_30d_change": None, "source": "insufficient"}
    ma200 = sum(closes[d - MA200_DAYS + 1: d + 1]) / MA200_DAYS
    close = closes[d]
    ch30 = _pct(close, closes[d - 30]) if d >= 30 else 0.0
    risk_on = (close > ma200 * (1 + band_pct / 100.0)) and (ch30 > 0)
    return {
        "risk_on": bool(risk_on),
        "btc_vs_ma200_pct": round(_pct(close, ma200), 2),
        "btc_30d_change": round(ch30, 2),
        "source": "ma200",
    }


# -----------------------------------------------------------------------------
# Daily trade simulation (uses daily H/L; conservative stop-before-target)
# -----------------------------------------------------------------------------

def simulate_daily(info: Dict[str, Any], d: int, stop_pct: float, target_pct: float,
                   max_hold: int = MAX_HOLD_DAYS) -> Dict[str, Any]:
    closes, highs, lows = info["closes"], info["highs"], info["lows"]
    eff_entry = closes[d] * (1 + SLIPPAGE_PCT / 100)
    stop_price = eff_entry * (1 - stop_pct / 100)
    target_price = eff_entry * (1 + target_pct / 100)
    max_i = min(d + max_hold, len(closes) - 1)
    exit_price, exit_reason, hold = None, "time_exit", 0
    for i in range(d + 1, max_i + 1):
        hold = i - d
        if lows[i] <= stop_price:
            exit_price, exit_reason = stop_price, "stop_loss"
            break
        if highs[i] >= target_price:
            exit_price, exit_reason = target_price, "take_profit"
            break
    if exit_price is None:
        exit_price = closes[max_i] if max_i > d else eff_entry
        # G1 delisting-return rule: if the hold window runs past the end of a segment
        # that ended for real (delist/halt) — not because today does — the position is
        # force-exited at the last close minus a liquidation haircut. This is the part
        # survivor-only backtests erase: you do NOT get to time-exit a dying coin cleanly.
        if (d + max_hold) > (len(closes) - 1) and info.get("terminal") in ("delisted", "halt"):
            exit_price = closes[-1] * (1 - DELIST_HAIRCUT_PCT / 100)
            exit_reason = "delisted"
            hold = (len(closes) - 1) - d
    eff_exit = exit_price * (1 - SLIPPAGE_PCT / 100)
    gross = _pct(eff_exit, eff_entry)
    net = gross - FEE_PCT * 2
    return {"outcome": "win" if net > 0 else "loss", "pnl_pct": round(net, 4),
            "gross_pnl_pct": round(gross, 4), "exit_reason": exit_reason, "hold_days": hold}


# -----------------------------------------------------------------------------
# Engine
# -----------------------------------------------------------------------------

def run_pipeline(universe: Dict[str, Any], fg: Dict[str, int], *, stage1b_active: bool,
                 verbose: bool = True,
                 membership: Optional[Dict[str, Dict[str, int]]] = None,
                 episode_mode: bool = False,
                 max_concurrent: int = 8,
                 entry_gate=None,
                 eligible_out: Optional[Dict[str, List[Tuple[str, int]]]] = None) -> List[Dict[str, Any]]:
    """Slide day-by-day along the BTC timeline; return entered trades w/ entry-date.

    entry_gate (H-2026Q4-2): optional callable(date_str) -> bool; when False, no new
    entries that day (existing holds unaffected). eligible_out: if a dict is passed, it
    receives {date: [(universe_key, day_index), ...]} — the coins that COULD have been
    entered that day, for the G7 random-entry baseline.

    membership (PIT mode): {'YYYY-MM': {SYMBOL: pit_rank}} — a coin is screened on a
    given day only if it was in the point-in-time top-N that month; rank comes from
    the snapshot (not list order). Without membership, legacy fixed-universe behavior.

    episode_mode (G4): max one concurrent position per coin AND a portfolio cap of
    max_concurrent (live parity: MAX_OPEN_TOTAL=8) — n becomes episode-level, not
    overlapping signal-level; slots fill in PIT-rank order."""
    from indicators import calculate_indicators
    from scorer import score_coin
    from quant_signal import generate_quant_signal
    from market_regime import MarketRegimeDetector
    from dynamic_targets import DynamicTargetCalculator
    from thresholds import get_threshold

    band = float(get_threshold("RISK_OFF_MA_BAND_PCT", 1.5))
    # Lean exit lengthens the max-hold (time exit); honor it for both the simulate
    # window and the day-range boundaries so we never index past available bars.
    _lean = float(get_threshold("LEAN_ENTRY_MODE", 0.0) or 0.0) >= 1.0
    max_hold = int(get_threshold("LEAN_MAX_HOLD_DAYS", 30)) if _lean else MAX_HOLD_DAYS
    regime_detector = MarketRegimeDetector()
    target_calc = DynamicTargetCalculator()

    btc = universe.get("bitcoin")
    if not btc:
        raise RuntimeError("BTC required for regime + 200d-MA")
    btc_dates = btc["dates"]
    ids = list(universe.keys())
    trades: List[Dict[str, Any]] = []

    start_d = max(SPARKLINE_DAYS, MA200_DAYS)
    end_d = len(btc_dates) - max_hold
    screened = 0
    blocked_until: Dict[str, int] = {}   # episode_mode: symbol → last btc-day index in position
    for d in range(start_d, end_d):
        date_str = btc_dates[d]
        fg_value = fg.get(date_str, 50)
        risk_info = compute_btc_risk_on_daily(btc, d, band)

        month_members = membership.get(date_str[:7]) if membership is not None else None
        coin_dicts = []
        for rank, gid in enumerate(ids):
            info = universe[gid]
            sym = info["symbol"]
            if episode_mode and blocked_until.get(sym, -1) >= d:
                continue   # G4: one concurrent position per coin (episode-level n)
            if membership is not None:
                if month_members is None:
                    continue
                if sym != "BTC" and sym not in month_members:
                    continue   # not in the point-in-time top-N that month
                rank = month_members.get(sym, 999) - 1  # PIT rank, 0-based
            di = info["by_date"].get(date_str)
            if di is None or di < SPARKLINE_DAYS:
                continue
            # Entries near the data edge: blocked when the edge is just "today"
            # (live_edge — outcome unknowable), but ALLOWED into a real terminal
            # (delist/halt) — the forced exit there is the honest outcome.
            if di >= len(info["closes"]) - max_hold and info.get("terminal", "live_edge") == "live_edge":
                continue
            if di >= len(info["closes"]) - 1:
                continue   # need at least one forward bar
            cd = build_daily_coin_dict(info, di, rank)
            if cd:
                cd["_pit_rank"] = rank
                coin_dicts.append((gid, info, di, cd))
        if not coin_dicts:
            continue
        if eligible_out is not None:
            eligible_out[date_str] = [(gid, di) for gid, _, di, _ in coin_dicts]
        if entry_gate is not None and not entry_gate(date_str):
            continue

        regime_input = {
            "coins": [cd for _, _, _, cd in coin_dicts],
            "fear_greed": {"value": fg_value},
            "sentiment": {"score": 0.0}, "macro": {},
        }
        calculate_indicators(regime_input)
        btc_cd = next((cd for gid, _, _, cd in coin_dicts if gid == "bitcoin"), None)
        market_24h = btc_cd.get("price_change_percentage_24h", 0) if btc_cd else 0
        regime_input["market_24h_pct"] = market_24h
        regime_input["market_7d_pct"] = btc_cd.get("price_change_percentage_7d_in_currency", 0) if btc_cd else 0
        try:
            regime_result = regime_detector.detect_regime(regime_input)
        except Exception:
            regime_result = {"regime": "SIDEWAYS", "characteristics": {}}
        regime = regime_result.get("regime", "SIDEWAYS")
        chars = dict(regime_result.get("characteristics", {}) or {})
        # A/B switch: Stage 1b sets the TRUE 200d-MA risk_on; baseline leaves it inactive (None).
        if stage1b_active:
            chars["risk_on"] = bool(risk_info["risk_on"])
            chars["risk_regime"] = "RISK_ON" if risk_info["risk_on"] else "RISK_OFF"
        else:
            chars.pop("risk_on", None)
            chars["risk_regime"] = None
        regime_data = {
            "regime": regime,
            "structural_regime": regime_result.get("structural_regime", regime),
            "market_24h_pct": market_24h, "fear_greed": fg_value,
            "volume_median": 0, "m2_yoy": regime_result.get("m2_yoy", 0),
            "characteristics": chars,
        }

        # Episode mode fills portfolio slots in PIT-rank order (live parity: best-ranked first)
        if episode_mode:
            coin_dicts.sort(key=lambda t: (t[3].get("_pit_rank", 999)))
        for gid, info, di, cd in coin_dicts:
            if episode_mode:
                open_now = sum(1 for v in blocked_until.values() if v >= d)
                if open_now >= max_concurrent:
                    break   # G4: portfolio cap (MAX_OPEN_TOTAL live parity)
                if blocked_until.get(info["symbol"], -1) >= d:
                    continue
            screened += 1
            try:
                sr = score_coin(cd, fg_value, 0.0, market_24h)
                cd["score"] = sr["score"]
                cd["risk"] = sr.get("risk", {})
            except Exception:
                continue
            try:
                quant = generate_quant_signal(cd, regime_data, fear_greed_value=fg_value)
            except Exception:
                continue
            if quant.get("quant_signal") != "BUY":
                continue
            tier = quant.get("quant_tier", "HOLD")
            vol = cd.get("indicators", {}).get("volatility", 50)
            try:
                tg = target_calc.calculate_targets(entry_price=cd["current_price"], volatility_pct=vol,
                                                    regime=regime, consensus_signal=tier)
                stop_pct, target_pct = tg["stop_loss_pct"], tg["take_profit_pct"]
            except Exception:
                stop_pct, target_pct = 4.0, 12.0
            res = simulate_daily(info, di, stop_pct, target_pct, max_hold=max_hold)
            if episode_mode:
                blocked_until[info["symbol"]] = d + max(1, int(res["hold_days"]))
            trades.append({
                "date": date_str, "year": int(date_str[:4]), "symbol": info["symbol"],
                "regime": regime, "quant_path": quant.get("quant_path", "default"),
                "quant_tier": tier, "risk_on": (bool(risk_info["risk_on"]) if stage1b_active else None),
                "pnl_pct": res["pnl_pct"], "gross_pnl_pct": res["gross_pnl_pct"],
                "outcome": res["outcome"], "exit_reason": res["exit_reason"], "hold_days": res["hold_days"],
                # identical-exit replay (G7 baseline) and daily mark-to-market (G8 curve)
                "gid": gid, "di": di, "stop_pct": stop_pct, "target_pct": target_pct,
            })
        if verbose and d % 200 == 0:
            logger.info("  [%s] day %d/%d (%s): %d trades so far",
                        "S1b" if stage1b_active else "base", d, end_d, date_str, len(trades))
    if verbose:
        logger.info("  [%s] done: %d screenings → %d trades",
                    "S1b" if stage1b_active else "base", screened, len(trades))
    return trades


# -----------------------------------------------------------------------------
# Stats + gate
# -----------------------------------------------------------------------------

def _expectancy(trades: List[Dict[str, Any]], extra_cost: float = 0.0) -> float:
    if not trades:
        return 0.0
    return sum(t["pnl_pct"] - extra_cost for t in trades) / len(trades)


def _profit_factor(trades: List[Dict[str, Any]], extra_cost: float = 0.0) -> float:
    adj = [t["pnl_pct"] - extra_cost for t in trades]
    g = sum(p for p in adj if p > 0)
    l = sum(-p for p in adj if p < 0)
    return (g / l) if l > 0 else (float("inf") if g > 0 else 0.0)


def _half_stats(trades: List[Dict[str, Any]], extra_cost: float = 0.0) -> Dict[str, Any]:
    """Win rate, expectancy AND profit factor all on cost-adjusted P&L — STRATEGY.md G2
    requires the stress columns to be fully recomputed under stress, not expectancy-only
    (audit 2026-06-11 H1: stressed PFs were previously reported at base cost)."""
    n = len(trades)
    wins = sum(1 for t in trades if (t["pnl_pct"] - extra_cost) > 0)
    pf = _profit_factor(trades, extra_cost)
    return {
        "n": n, "win_rate_pct": round(wins / n * 100, 1) if n else 0.0,
        "expectancy_pct": round(_expectancy(trades, extra_cost), 3),
        "profit_factor": (round(pf, 2) if pf != float("inf") else "inf"),
    }


# -----------------------------------------------------------------------------
# G4/G5/G6 statistics (STRATEGY.md): MDE, bootstrap bounds, deflated hurdle, folds
# -----------------------------------------------------------------------------

def _rich_stats(trades: List[Dict[str, Any]], extra_cost: float = 0.0,
                n_boot: int = 10_000) -> Dict[str, Any]:
    """Point estimates + the honesty layer: stdev, MDE (G4), bootstrap 5th-pct
    expectancy/PF (G6), and expectancy with the top-3 winners removed (G6)."""
    import numpy as np
    base = _half_stats(trades, extra_cost)
    if not trades:
        return base
    pnl = np.array([t["pnl_pct"] - extra_cost for t in trades], dtype=float)
    n = len(pnl)
    sd = float(np.std(pnl, ddof=1)) if n > 1 else 0.0
    base["stdev_pct"] = round(sd, 3)
    base["mde_pct"] = round(2.5 * sd / (n ** 0.5), 3) if n else None  # one-sided 95%, ~80% power
    if n >= 10:
        boots = min(n_boot, 2_000) if n > 2_000 else n_boot
        rng = np.random.default_rng(42)   # fixed seed: artifacts must be reproducible
        idx = rng.integers(0, n, size=(boots, n))
        samples = pnl[idx]
        exps = samples.mean(axis=1)
        pos = np.where(samples > 0, samples, 0).sum(axis=1)
        neg = np.where(samples < 0, -samples, 0).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            pfs = np.where(neg > 0, pos / neg, np.inf)
        base["boot5_expectancy_pct"] = round(float(np.percentile(exps, 5)), 3)
        base["boot5_profit_factor"] = round(float(np.percentile(pfs, 5)), 3)
        top3_removed = np.sort(pnl)[:-3] if n > 3 else pnl
        base["top3_removed_expectancy_pct"] = round(float(top3_removed.mean()), 3)
    return base


def _fold_stats(trades: List[Dict[str, Any]], fold_years=(2022, 2023, 2024, 2025),
                purge_days: int = 30, extra_cost: float = 0.0) -> Dict[str, Any]:
    """G3: rolling yearly OOS folds with a purge embargo — trades entered within
    purge_days before the NEXT fold boundary are dropped from their fold, because
    their holds leak across it. (Audit 2026-06-11 H2: the old predicate embargoed
    dates in the PRIOR December while also requiring year == y — unsatisfiable, so
    the purge never dropped a single trade.)"""
    from datetime import date as _date, timedelta as _td
    folds = {}
    positive = 0
    for y in fold_years:
        next_boundary = _date(y + 1, 1, 1)
        embargo_start = (next_boundary - _td(days=purge_days)).isoformat()
        fold_trades = [t for t in trades if t["year"] == y and t["date"] < embargo_start]
        st = _half_stats(fold_trades, extra_cost)
        folds[str(y)] = st
        if st["n"] > 0 and st["expectancy_pct"] > 0:
            positive += 1
    pooled = [t for t in trades if t["year"] in fold_years]
    return {"folds": folds, "folds_positive": positive, "folds_total": len(fold_years),
            "pooled": _half_stats(pooled, extra_cost),
            "passes_3_of_4": positive >= 3 and _expectancy(pooled, extra_cost) > 0}


def _registered_trial_count() -> int:
    """N for the G5 deflated hurdle = distinct config hashes ever registered."""
    hashes = set()
    try:
        with open(TRIALS_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    hashes.add(json.loads(line).get("config_hash"))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return max(1, len(hashes))


def _deflated_hurdle_pct(sd: float, n: int, n_trials: int) -> float:
    """False Strategy theorem: best of N zero-skill trials ≈ √(2·ln N) SEs above zero."""
    import math
    if not n:
        return float("inf")
    return math.sqrt(2 * math.log(max(2, n_trials))) * sd / math.sqrt(n)


def summarize(trades: List[Dict[str, Any]], label: str) -> Dict[str, Any]:
    train = [t for t in trades if t["year"] < WF_SPLIT_YEAR]
    test = [t for t in trades if t["year"] >= WF_SPLIT_YEAR]
    contrarian_in_risk_on = sum(
        1 for t in trades
        if t.get("risk_on") is True and (t.get("quant_path") or "").lower() in CONTRARIAN_PATHS
    )
    return {
        "label": label,
        "all": _half_stats(trades),
        "train_pre2023": _half_stats(train),
        "test_2023plus": _half_stats(test),
        "test_2023plus_stress_1p5": _half_stats(test, extra_cost=(COST_STRESS_PCT - ROUND_TRIP_COST_PCT)),
        "contrarian_in_risk_on": contrarian_in_risk_on,
    }


def evaluate_gate(s1b: Dict[str, Any], base: Dict[str, Any], *, target_delta_e: float = 1.5) -> Dict[str, Any]:
    crit = []

    def add(name, passed, detail):
        crit.append({"name": name, "passed": bool(passed), "detail": detail})

    tr = s1b["train_pre2023"]["expectancy_pct"]
    te = s1b["test_2023plus"]["expectancy_pct"]
    te_pf = s1b["test_2023plus"]["profit_factor"]
    te_stress = s1b["test_2023plus_stress_1p5"]["expectancy_pct"]
    base_te = base["test_2023plus"]["expectancy_pct"]
    delta_e = round(te - base_te, 3)
    pf_ok = (te_pf == "inf") or (isinstance(te_pf, (int, float)) and te_pf > 1.3)

    add("expectancy_both_halves_pos", tr > 0 and te > 0, f"train {tr}% / test {te}%")
    add("beats_baseline_by_target", delta_e >= target_delta_e,
        f"ΔE(test) {delta_e}% vs target ≥+{target_delta_e}% (S1b {te}% − base {base_te}%)")
    add("profit_factor_gt_1_3", pf_ok, f"test PF {te_pf}")
    add("contrarian_leak_eliminated", s1b["contrarian_in_risk_on"] == 0,
        f"{s1b['contrarian_in_risk_on']} contrarian-in-risk-on entries")
    add("robust_to_1p5_cost", te_stress > 0, f"test expectancy {te_stress}% @1.5% round-trip")

    failing = [c["name"] for c in crit if not c["passed"]]
    return {"verdict": "PASS" if not failing else "FAIL", "failing": failing,
            "criteria": crit, "delta_e_test": delta_e}


def run_gate(refresh: bool = False, verbose: bool = True) -> Dict[str, Any]:
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Backtest always lacks funding/order-book/on-chain feeds, so these per-coin
    # "DATA_QUALITY downgraded" warnings are expected noise — silence to keep the
    # run fast and the log readable.
    for noisy in ("quant_signal", "scorer", "indicators", "market_regime", "dynamic_targets"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    logger.info("[stage1c] loading multi-year daily universe …")
    universe = load_universe_daily(refresh=refresh)
    if "bitcoin" not in universe:
        raise RuntimeError("BTC daily data unavailable — cannot run gate")
    fg = fetch_fear_greed_all()
    logger.info("[stage1c] universe=%d coins, F&G days=%d", len(universe), len(fg))

    logger.info("[stage1c] running Stage 1b pipeline (risk-on gate ACTIVE) …")
    s1b_trades = run_pipeline(universe, fg, stage1b_active=True, verbose=verbose)
    logger.info("[stage1c] running baseline pipeline (gate DISABLED) …")
    base_trades = run_pipeline(universe, fg, stage1b_active=False, verbose=verbose)

    s1b = summarize(s1b_trades, "stage1b")
    base = summarize(base_trades, "baseline")
    gate = evaluate_gate(s1b, base)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(universe), "wf_split_year": WF_SPLIT_YEAR,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT, "cost_stress_pct": COST_STRESS_PCT,
        "stage1b": s1b, "baseline": base, "gate": gate,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SUMMARY_FILE, "w") as f:
        json.dump(out, f, indent=2, default=str)
    return out


def _print_report(out: Dict[str, Any]) -> None:
    g = out["gate"]
    s, b = out["stage1b"], out["baseline"]
    print("=" * 70)
    print(f"STAGE 1C WALK-FORWARD GATE — VERDICT: {g['verdict']}")
    print("=" * 70)
    print(f"Universe: {out['universe_size']} coins | WF split: <{out['wf_split_year']} vs ≥{out['wf_split_year']} "
          f"| cost {out['round_trip_cost_pct']}% (stress {out['cost_stress_pct']}%)")
    for label, st in (("STAGE 1B (gate ON)", s), ("BASELINE (gate OFF)", b)):
        print(f"\n{label}")
        for half in ("all", "train_pre2023", "test_2023plus", "test_2023plus_stress_1p5"):
            h = st[half]
            print(f"  {half:>26}: n={h['n']:>5}  WR={h['win_rate_pct']:>5}%  "
                  f"exp={h['expectancy_pct']:>7}%  PF={h['profit_factor']}")
        print(f"  {'contrarian_in_risk_on':>26}: {st['contrarian_in_risk_on']}")
    print("\nCRITERIA:")
    for c in g["criteria"]:
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['name']}: {_ascii(c['detail'])}")
    if g["failing"]:
        print(f"\nFAILING: {', '.join(g['failing'])}")
    print("=" * 70)


# -----------------------------------------------------------------------------
# LEAN gate (STRATEGY.md §5 item 1 — reproducible driver)
#
# Reconstructs the legacy-vs-lean_full A/B that produced data/stage1c_lean_full_gate.json
# on 2026-06-01 from an uncommitted script (red-team finding A3). Every lean parameter is
# pinned explicitly via threshold_override so the run never depends on live_thresholds.json
# state, and the artifact embeds git SHA + the full config snapshot. Each run also appends
# to the data/trials.jsonl registry (STRATEGY.md gate G5).
# -----------------------------------------------------------------------------

LEAN_GATE_FILE = os.path.join(DATA_DIR, "stage1c_lean_full_gate.json")
TRIALS_FILE = os.path.join(DATA_DIR, "trials.jsonl")

# The lean strategy under test, pinned (NOT read from live_thresholds.json):
LEAN_GATE_CONFIG: Dict[str, Any] = {
    "LEAN_ENTRY_MODE": 1.0,
    "LEAN_FIXED_STOP_PCT": 24.0,
    "LEAN_TAKE_PROFIT_PCT": 150.0,   # effectively no TP
    "LEAN_MAX_HOLD_DAYS": 30,        # time exit
    "LEAN_ENTRY_MIN_MOM30": 15.0,
    "RISK_OFF_MA_BAND_PCT": 1.5,
}
LEGACY_GATE_CONFIG: Dict[str, Any] = {"LEAN_ENTRY_MODE": 0.0}


def _git_provenance() -> Dict[str, Any]:
    import subprocess
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_DIR,
                             capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=_DIR,
                                    capture_output=True, text=True, timeout=10).stdout.strip())
        return {"git_sha": sha or None, "working_tree_dirty": dirty}
    except Exception:
        return {"git_sha": None, "working_tree_dirty": None}


def _config_hash(config: Dict[str, Any]) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]


def _append_trial(entry: Dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TRIALS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def run_lean_gate(refresh: bool = False, verbose: bool = True) -> Dict[str, Any]:
    """Legacy (full multi-path pipeline) vs lean_full (risk-on prune + wide-stop/no-TP/30d
    exits) over the multi-year daily universe. Writes LEAN_GATE_FILE with provenance."""
    from thresholds import threshold_override

    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("quant_signal", "scorer", "indicators", "market_regime", "dynamic_targets"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    logger.info("[stage1c --lean] loading multi-year daily universe …")
    universe = load_universe_daily(refresh=refresh)
    if "bitcoin" not in universe:
        raise RuntimeError("BTC daily data unavailable — cannot run lean gate")
    fg = fetch_fear_greed_all()
    logger.info("[stage1c --lean] universe=%d coins, F&G days=%d", len(universe), len(fg))

    logger.info("[stage1c --lean] running LEGACY pipeline (lean OFF, risk-on gate OFF) …")
    with threshold_override(LEGACY_GATE_CONFIG):
        legacy_trades = run_pipeline(universe, fg, stage1b_active=False, verbose=verbose)
    logger.info("[stage1c --lean] running LEAN_FULL pipeline (lean ON, risk-on gate ON) …")
    with threshold_override(LEAN_GATE_CONFIG):
        lean_trades = run_pipeline(universe, fg, stage1b_active=True, verbose=verbose)

    legacy = summarize(legacy_trades, "legacy")
    lean = summarize(lean_trades, "lean_full")
    gate = evaluate_gate(lean, legacy)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **_git_provenance(),
        "driver": "stage1c_replay.py --lean",
        "config": {"lean_full": LEAN_GATE_CONFIG, "legacy": LEGACY_GATE_CONFIG,
                   "config_hash": _config_hash(LEAN_GATE_CONFIG)},
        "universe_size": len(universe),
        "universe_symbols": sorted(info["symbol"] for info in universe.values()),
        "universe_caveat": "30 currently-listed Coinbase coins — SURVIVORSHIP-BIASED; "
                           "results are an upper bound until the point-in-time rebuild (STRATEGY.md G1)",
        "wf_split_year": WF_SPLIT_YEAR,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT, "cost_stress_pct": COST_STRESS_PCT,
        "legacy": legacy, "lean_full": lean, "gate": gate,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(LEAN_GATE_FILE):  # data-safety: never overwrite an artifact silently
        bak = f"{LEAN_GATE_FILE}.bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        os.replace(LEAN_GATE_FILE, bak)
        logger.info("[stage1c --lean] previous artifact preserved at %s", bak)
    with open(LEAN_GATE_FILE, "w") as f:
        json.dump(out, f, indent=2, default=str)
    _append_trial({
        "ts": out["generated_at"], "driver": out["driver"],
        "config_hash": out["config"]["config_hash"], "config": LEAN_GATE_CONFIG,
        "universe": "30-survivor (biased)", "n_test": lean["test_2023plus"]["n"],
        "test_expectancy_pct": lean["test_2023plus"]["expectancy_pct"],
        "test_pf": lean["test_2023plus"]["profit_factor"], "verdict": gate["verdict"],
        **{k: out[k] for k in ("git_sha", "working_tree_dirty")},
    })
    return out


def _ascii(s: Any) -> str:
    """Windows cp1252 consoles cannot print Δ/→ etc. — sanitize report text only
    (artifact JSON keeps real unicode)."""
    return str(s).encode("ascii", "replace").decode()


def _print_lean_report(out: Dict[str, Any]) -> None:
    g = out["gate"]
    print("=" * 70)
    print(f"STAGE 1C LEAN GATE (reproducible) — VERDICT: {g['verdict']}")
    print(f"git {out.get('git_sha')}{' (dirty)' if out.get('working_tree_dirty') else ''}"
          f" | config {out['config']['config_hash']}")
    print(f"CAVEAT: {out['universe_caveat']}")
    print("=" * 70)
    for label_key in ("lean_full", "legacy"):
        st = out[label_key]
        print(f"\n{label_key.upper()}")
        for half in ("all", "train_pre2023", "test_2023plus", "test_2023plus_stress_1p5"):
            h = st[half]
            print(f"  {half:>26}: n={h['n']:>5}  WR={h['win_rate_pct']:>5}%  "
                  f"exp={h['expectancy_pct']:>7}%  PF={h['profit_factor']}")
    print("\nCRITERIA:")
    for c in g["criteria"]:
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['name']}: {_ascii(c['detail'])}")


def run_pit_gate(refresh: bool = False, verbose: bool = True) -> Dict[str, Any]:
    """The honest run (STRATEGY.md G1): legacy vs lean_full A/B on the POINT-IN-TIME
    universe — monthly top-N by mcap as of each date, Coinbase-tradable-at-date,
    including delisted coins with forced-exit haircuts. Same gate criteria as the
    survivor-universe run so the two artifacts are directly comparable.
    (Interim: G2-G9 amendments — folds, bootstrap, deflated hurdle, random-entry
    baseline — land on top of this harness next.)"""
    from thresholds import threshold_override

    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("quant_signal", "scorer", "indicators", "market_regime", "dynamic_targets"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    logger.info("[stage1c --pit] loading point-in-time universe …")
    universe, coverage = load_universe_pit(refresh=refresh)
    if "bitcoin" not in universe:
        raise RuntimeError("BTC daily data unavailable — cannot run PIT gate")
    membership = load_membership()
    fg = fetch_fear_greed_all()
    logger.info("[stage1c --pit] universe segments=%d, months=%d, F&G days=%d",
                len(universe), len(membership), len(fg))

    logger.info("[stage1c --pit] running LEGACY pipeline (lean OFF, risk-on OFF) …")
    with threshold_override(LEGACY_GATE_CONFIG):
        legacy_trades = run_pipeline(universe, fg, stage1b_active=False, verbose=verbose,
                                     membership=membership)
    logger.info("[stage1c --pit] running LEAN_FULL pipeline (lean ON, risk-on ON) …")
    with threshold_override(LEAN_GATE_CONFIG):
        lean_trades = run_pipeline(universe, fg, stage1b_active=True, verbose=verbose,
                                   membership=membership)

    legacy = summarize(legacy_trades, "legacy_pit")
    lean = summarize(lean_trades, "lean_full_pit")
    gate = evaluate_gate(lean, legacy)
    delist_exits = sum(1 for t in lean_trades if t["exit_reason"] == "delisted")

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **_git_provenance(),
        "driver": "stage1c_replay.py --pit",
        "config": {"lean_full": LEAN_GATE_CONFIG, "legacy": LEGACY_GATE_CONFIG,
                   "config_hash": _config_hash(LEAN_GATE_CONFIG),
                   "delist_haircut_pct": DELIST_HAIRCUT_PCT,
                   "membership": "monthly PIT top-50 by mcap (CMC snapshots) ∩ Coinbase-listed-at-date"},
        "coverage": coverage,
        "lean_delisted_forced_exits": delist_exits,
        "wf_split_year": WF_SPLIT_YEAR,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT, "cost_stress_pct": COST_STRESS_PCT,
        "legacy": legacy, "lean_full": lean, "gate": gate,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(PIT_GATE_FILE):
        bak = f"{PIT_GATE_FILE}.bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        os.replace(PIT_GATE_FILE, bak)
    with open(PIT_GATE_FILE, "w") as f:
        json.dump(out, f, indent=2, default=str)
    _append_trial({
        "ts": out["generated_at"], "driver": out["driver"],
        "config_hash": out["config"]["config_hash"], "config": LEAN_GATE_CONFIG,
        "universe": "PIT top-50 ∩ Coinbase (G1)", "n_test": lean["test_2023plus"]["n"],
        "test_expectancy_pct": lean["test_2023plus"]["expectancy_pct"],
        "test_pf": lean["test_2023plus"]["profit_factor"], "verdict": gate["verdict"],
        **{k: out[k] for k in ("git_sha", "working_tree_dirty")},
    })
    return out


def _print_pit_report(out: Dict[str, Any]) -> None:
    g = out["gate"]
    cov = out["coverage"]
    print("=" * 70)
    print(f"STAGE 1C POINT-IN-TIME GATE (G1, honest universe) — VERDICT: {g['verdict']}")
    print(f"git {out.get('git_sha')}{' (dirty)' if out.get('working_tree_dirty') else ''}"
          f" | config {out['config']['config_hash']}")
    print(f"Universe: {cov['member_symbols']} PIT member symbols, {cov['with_coinbase_data']} with "
          f"Coinbase data ({cov['delisted_products_used']} delisted), {cov['segments']} segments")
    print(f"Lean delist forced exits: {out['lean_delisted_forced_exits']}")
    print("=" * 70)
    for label_key in ("lean_full", "legacy"):
        st = out[label_key]
        print(f"\n{st['label'].upper()}")
        for half in ("all", "train_pre2023", "test_2023plus", "test_2023plus_stress_1p5"):
            h = st[half]
            print(f"  {half:>26}: n={h['n']:>5}  WR={h['win_rate_pct']:>5}%  "
                  f"exp={h['expectancy_pct']:>7}%  PF={h['profit_factor']}")
    print("\nCRITERIA:")
    for c in g["criteria"]:
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['name']}: {_ascii(c['detail'])}")


# -----------------------------------------------------------------------------
# Candidate #1 (STRATEGY.md D4): BTC/ETH risk-on rotation — the null-hypothesis
# strategy every pipeline candidate must beat (gate G8). Long the major while the
# existing risk-on rule holds (close > MA200×(1+band) AND 30d change > 0), flat
# otherwise. Entries/exits at the close of the day AFTER the flip (no lookahead).
# Two fills per episode → fee drag is per-episode, not per-trade.
# -----------------------------------------------------------------------------

ROTATION_GATE_FILE = os.path.join(DATA_DIR, "stage1c_rotation_gate.json")


def _rotation_episodes(info: Dict[str, Any], band_pct: float) -> List[Dict[str, Any]]:
    """Risk-on episodes for one asset using its own MA200/30d rule. Entry/exit at
    next-day close after the signal flips; round-trip cost applied per episode."""
    closes, dates = info["closes"], info["dates"]
    episodes: List[Dict[str, Any]] = []
    in_pos = False
    entry_i = None
    for d in range(MA200_DAYS, len(closes) - 1):
        ma200 = sum(closes[d - MA200_DAYS + 1: d + 1]) / MA200_DAYS
        ch30 = _pct(closes[d], closes[d - 30]) if d >= 30 else 0.0
        risk_on = (closes[d] > ma200 * (1 + band_pct / 100.0)) and (ch30 > 0)
        if risk_on and not in_pos:
            in_pos, entry_i = True, d + 1   # next-day close
        elif not risk_on and in_pos:
            exit_i = d + 1
            gross = _pct(closes[exit_i], closes[entry_i])
            net = gross - ROUND_TRIP_COST_PCT
            episodes.append({
                "date": dates[entry_i], "year": int(dates[entry_i][:4]),
                "symbol": info["symbol"], "regime": "ROTATION",
                "quant_path": "rotation", "quant_tier": "ROTATION", "risk_on": True,
                "pnl_pct": round(net, 4), "gross_pnl_pct": round(gross, 4),
                "outcome": "win" if net > 0 else "loss",
                "exit_reason": "risk_off_flip", "hold_days": exit_i - entry_i,
            })
            in_pos = False
    # open episode at data edge: mark-to-last-close (flagged, not a closed trade)
    if in_pos and entry_i is not None and entry_i < len(closes) - 1:
        gross = _pct(closes[-1], closes[entry_i])
        episodes.append({
            "date": dates[entry_i], "year": int(dates[entry_i][:4]),
            "symbol": info["symbol"], "regime": "ROTATION",
            "quant_path": "rotation", "quant_tier": "ROTATION", "risk_on": True,
            "pnl_pct": round(gross - ROUND_TRIP_COST_PCT, 4), "gross_pnl_pct": round(gross, 4),
            "outcome": "win" if gross - ROUND_TRIP_COST_PCT > 0 else "loss",
            "exit_reason": "open_at_data_edge", "hold_days": (len(closes) - 1) - entry_i,
        })
    return episodes


def run_rotation_gate(verbose: bool = True) -> Dict[str, Any]:
    """BTC and ETH risk-on rotation benchmark (G8 reference). Uses cached daily data."""
    from thresholds import get_threshold
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    band = float(get_threshold("RISK_OFF_MA_BAND_PCT", 1.5))
    universe = load_universe_daily(refresh=False)
    out_assets: Dict[str, Any] = {}
    for gid, sym in (("bitcoin", "BTC"), ("ethereum", "ETH")):
        info = universe.get(gid)
        if not info:
            continue
        eps = _rotation_episodes(info, band)
        s = summarize(eps, f"{sym.lower()}_rotation")
        s["episodes"] = len(eps)
        s["total_net_pct"] = round(sum(e["pnl_pct"] for e in eps), 2)
        s["avg_hold_days"] = round(sum(e["hold_days"] for e in eps) / len(eps), 1) if eps else 0
        s["open_at_edge"] = sum(1 for e in eps if e["exit_reason"] == "open_at_data_edge")
        out_assets[sym] = s
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **_git_provenance(),
        "driver": "stage1c_replay.py --rotation",
        "config": {"rule": "long while close>MA200*(1+band) AND ch30d>0; next-day-close fills",
                   "band_pct": band, "round_trip_cost_pct": ROUND_TRIP_COST_PCT},
        "note": "G8 benchmark: per-EPISODE stats (one episode = weeks-months in market; "
                "not comparable 1:1 with per-trade expectancy — compare capital growth per year)",
        "assets": out_assets,
    }
    if os.path.exists(ROTATION_GATE_FILE):
        os.replace(ROTATION_GATE_FILE,
                   f"{ROTATION_GATE_FILE}.bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    with open(ROTATION_GATE_FILE, "w") as f:
        json.dump(out, f, indent=2, default=str)
    for sym, s in out_assets.items():
        _append_trial({
            "ts": out["generated_at"], "driver": out["driver"],
            "config_hash": _config_hash({**out["config"], "asset": sym}),
            "config": {**out["config"], "asset": sym},
            "universe": "single major (no selection)", "n_test": s["test_2023plus"]["n"],
            "test_expectancy_pct": s["test_2023plus"]["expectancy_pct"],
            "test_pf": s["test_2023plus"]["profit_factor"], "verdict": "BENCHMARK",
            **{k: out[k] for k in ("git_sha", "working_tree_dirty")},
        })
    return out


def _print_rotation_report(out: Dict[str, Any]) -> None:
    print("=" * 70)
    print("RISK-ON ROTATION BENCHMARK (G8 null-hypothesis strategy)")
    print(f"git {out.get('git_sha')} | rule: {_ascii(out['config']['rule'])} | cost {out['config']['round_trip_cost_pct']}%/episode")
    print("=" * 70)
    for sym, s in out["assets"].items():
        print(f"\n{sym}: {s['episodes']} episodes, avg hold {s['avg_hold_days']}d, "
              f"total net {s['total_net_pct']}% ({s['open_at_edge']} open at edge)")
        for half in ("all", "train_pre2023", "test_2023plus"):
            h = s[half]
            print(f"  {half:>26}: n={h['n']:>4}  WR={h['win_rate_pct']:>5}%  "
                  f"exp/episode={h['expectancy_pct']:>8}%  PF={h['profit_factor']}")


PIT_HOLDS_FILE = os.path.join(DATA_DIR, "stage1c_pit_holds.json")


def run_pit_hold_arms(holds=(7, 14, 21), verbose: bool = True) -> Dict[str, Any]:
    """Registered hold-period arms (STRATEGY.md D4 #2): lean on the PIT universe with
    LEAN_MAX_HOLD_DAYS swept. Literature (Dobrynskaya; Liu-Tsyvinski) says crypto
    momentum decays into reversal beyond ~1 month — these arms test shorter exits.
    Each arm is one registered trial in data/trials.jsonl (raises the G5 hurdle)."""
    from thresholds import threshold_override
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("quant_signal", "scorer", "indicators", "market_regime", "dynamic_targets"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    universe, coverage = load_universe_pit(refresh=False)
    membership = load_membership()
    fg = fetch_fear_greed_all()
    arms: Dict[str, Any] = {}
    ts = datetime.now(timezone.utc).isoformat()
    for hold in holds:
        cfg = {**LEAN_GATE_CONFIG, "LEAN_MAX_HOLD_DAYS": int(hold)}
        logger.info("[pit-holds] lean arm, hold=%dd …", hold)
        with threshold_override(cfg):
            trades = run_pipeline(universe, fg, stage1b_active=True, verbose=verbose,
                                  membership=membership)
        s = summarize(trades, f"lean_pit_hold{hold}")
        arms[f"hold_{hold}d"] = s
        _append_trial({
            "ts": ts, "driver": "stage1c_replay.py --pit-holds",
            "config_hash": _config_hash(cfg), "config": cfg,
            "universe": "PIT top-50 ∩ Coinbase (G1)", "n_test": s["test_2023plus"]["n"],
            "test_expectancy_pct": s["test_2023plus"]["expectancy_pct"],
            "test_pf": s["test_2023plus"]["profit_factor"], "verdict": "ARM",
            **_git_provenance(),
        })
    out = {"generated_at": ts, **_git_provenance(),
           "driver": "stage1c_replay.py --pit-holds", "coverage": coverage,
           "base_config": LEAN_GATE_CONFIG, "arms": arms}
    if os.path.exists(PIT_HOLDS_FILE):
        os.replace(PIT_HOLDS_FILE,
                   f"{PIT_HOLDS_FILE}.bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    with open(PIT_HOLDS_FILE, "w") as f:
        json.dump(out, f, indent=2, default=str)
    return out


def _print_holds_report(out: Dict[str, Any]) -> None:
    print("=" * 70)
    print("PIT HOLD-PERIOD ARMS (lean, honest universe) — registered trials")
    print("=" * 70)
    for arm, s in out["arms"].items():
        h = s["test_2023plus"]
        hs = s["test_2023plus_stress_1p5"]
        print(f"  {arm:>9}: test n={h['n']:>4} WR={h['win_rate_pct']:>5}% "
              f"exp={h['expectancy_pct']:>7}% PF={h['profit_factor']}  "
              f"(stress exp={hs['expectancy_pct']}%)")


# -----------------------------------------------------------------------------
# ADJUDICATION (STRATEGY.md v1.3 ranking): lean-7d vs rotation, full G-gate set
# -----------------------------------------------------------------------------

ADJUDICATION_FILE = os.path.join(DATA_DIR, "stage1c_adjudication.json")
# G2: measured base cost = the account's live taker tier both ways + slippage, from
# fill_cost_model (single source). Since 2026-10-11: 0.90%/side + 0.05% slip → 1.9% RT.
# Before: 2.4% (the retired 1.20%/side tier) — every artifact records the cost it used,
# so earlier verdicts stay on their own basis. The replay already charges
# ROUND_TRIP_COST_PCT, so the extra layered on = measured − modeled. Stress = 2× measured.
from fill_cost_model import TAKER_FEE_PCT_PER_SIDE as _TAKER, SLIPPAGE_PCT_PER_SIDE as _SLIP
MEASURED_RT_COST_PCT = round(2 * (_TAKER + _SLIP), 4)
STRESS_RT_COST_PCT = 2 * MEASURED_RT_COST_PCT


def run_adjudication(verbose: bool = True) -> Dict[str, Any]:
    """Episode-level lean-7d on the PIT universe vs the BTC rotation benchmark,
    judged against gates G2-G9. This is the registered decision run for whether
    the pipeline candidate earns an allocation or dies (STRATEGY.md v1.3)."""
    from thresholds import threshold_override

    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("quant_signal", "scorer", "indicators", "market_regime", "dynamic_targets"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    universe, coverage = load_universe_pit(refresh=False)
    membership = load_membership()
    fg = fetch_fear_greed_all()
    lean7_cfg = {**LEAN_GATE_CONFIG, "LEAN_MAX_HOLD_DAYS": 7}

    logger.info("[adjudicate] lean-7d EPISODE-LEVEL arm on PIT universe …")
    with threshold_override(lean7_cfg):
        lean_trades = run_pipeline(universe, fg, stage1b_active=True, verbose=verbose,
                                   membership=membership, episode_mode=True)
    lean_test = [t for t in lean_trades if t["year"] >= WF_SPLIT_YEAR]
    extra_meas = MEASURED_RT_COST_PCT - ROUND_TRIP_COST_PCT
    extra_stress = STRESS_RT_COST_PCT - ROUND_TRIP_COST_PCT
    lean_stats = {
        "all": _rich_stats(lean_trades),
        "test_modeled_cost": _rich_stats(lean_test),
        "test_measured_cost": _rich_stats(lean_test, extra_cost=extra_meas),
        "test_stress_cost": _rich_stats(lean_test, extra_cost=extra_stress),
        "folds_measured_cost": _fold_stats(lean_trades, extra_cost=extra_meas),
    }
    lean_day = (sum(t["pnl_pct"] - extra_meas for t in lean_test)
                / max(1, sum(t["hold_days"] for t in lean_test)))

    logger.info("[adjudicate] rotation benchmark (BTC) at measured cost …")
    rot_universe = load_universe_daily(refresh=False)
    rot_eps = _rotation_episodes(rot_universe["bitcoin"], 1.5)
    rot_test = [e for e in rot_eps if e["year"] >= WF_SPLIT_YEAR]
    rot_extra = MEASURED_RT_COST_PCT - ROUND_TRIP_COST_PCT
    rot_stats = {
        "all": _rich_stats(rot_eps),
        "test_measured_cost": _rich_stats(rot_test, extra_cost=rot_extra),
        "folds_measured_cost": _fold_stats(rot_eps, extra_cost=rot_extra),
    }
    rot_day = (sum(e["pnl_pct"] - rot_extra for e in rot_test)
               / max(1, sum(e["hold_days"] for e in rot_test)))

    n_trials = _registered_trial_count()
    lt = lean_stats["test_measured_cost"]
    hurdle = _deflated_hurdle_pct(lt.get("stdev_pct") or 0.0, lt["n"], n_trials)

    crit = []
    def add(name, passed, detail):
        crit.append({"name": name, "passed": bool(passed), "detail": detail})
    add("G4_n_episodes_100", lt["n"] >= 100, f"episode-level test n={lt['n']}")
    add("G4_exceeds_mde", lt["expectancy_pct"] > (lt.get("mde_pct") or 9e9),
        f"exp {lt['expectancy_pct']}% vs MDE {lt.get('mde_pct')}%")
    add("G5_deflated_hurdle", lt["expectancy_pct"] > hurdle,
        f"exp {lt['expectancy_pct']}% vs hurdle {round(hurdle, 3)}% (N={n_trials} trials)")
    add("G6_boot5_expectancy_pos", (lt.get("boot5_expectancy_pct") or -1) > 0,
        f"boot 5th-pct exp {lt.get('boot5_expectancy_pct')}%")
    add("G6_boot5_pf_gt_1", (lt.get("boot5_profit_factor") or 0) > 1.0,
        f"boot 5th-pct PF {lt.get('boot5_profit_factor')}")
    add("G6_survives_top3_removal", (lt.get("top3_removed_expectancy_pct") or -1) > 0,
        f"top3-removed exp {lt.get('top3_removed_expectancy_pct')}%")
    add("G3_folds_3_of_4", lean_stats["folds_measured_cost"]["passes_3_of_4"],
        f"{lean_stats['folds_measured_cost']['folds_positive']}/4 folds positive at measured cost")
    add("G2_stress_positive", lean_stats["test_stress_cost"]["expectancy_pct"] > 0,
        f"exp {lean_stats['test_stress_cost']['expectancy_pct']}% @ {STRESS_RT_COST_PCT}% RT stress")
    add("G9_edge_clears_cost_plus_2pp", lt["expectancy_pct"] >= 2.0,
        f"net exp at measured cost {lt['expectancy_pct']}% vs required ≥2pp")
    add("G8_beats_rotation_per_day", lean_day > rot_day,
        f"lean {round(lean_day, 4)}%/day-in-market vs rotation {round(rot_day, 4)}%/day (measured cost)")
    failing = [c["name"] for c in crit if not c["passed"]]
    verdict = "PASS" if not failing else "FAIL"

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **_git_provenance(),
        "driver": "stage1c_replay.py --adjudicate",
        "config": {"lean_7d": lean7_cfg, "config_hash": _config_hash({**lean7_cfg, "episode_mode": True}),
                   "measured_rt_cost_pct": MEASURED_RT_COST_PCT, "stress_rt_cost_pct": STRESS_RT_COST_PCT,
                   "episode_mode": True, "max_concurrent": 8, "n_registered_trials": n_trials},
        "coverage": coverage,
        "lean_7d": lean_stats,
        "rotation_btc": rot_stats,
        "per_day_in_market_measured": {"lean_7d": round(lean_day, 4), "rotation_btc": round(rot_day, 4)},
        "gate": {"verdict": verdict, "failing": failing, "criteria": crit},
    }
    if os.path.exists(ADJUDICATION_FILE):
        os.replace(ADJUDICATION_FILE,
                   f"{ADJUDICATION_FILE}.bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    with open(ADJUDICATION_FILE, "w") as f:
        json.dump(out, f, indent=2, default=str)
    _append_trial({
        "ts": out["generated_at"], "driver": out["driver"],
        "config_hash": out["config"]["config_hash"], "config": lean7_cfg,
        "universe": "PIT top-50 ∩ Coinbase, episode-level (G4)", "n_test": lt["n"],
        "test_expectancy_pct": lt["expectancy_pct"], "test_pf": lt["profit_factor"],
        "verdict": verdict, **{k: out[k] for k in ("git_sha", "working_tree_dirty")},
    })
    return out


def _print_adjudication(out: Dict[str, Any]) -> None:
    g = out["gate"]
    print("=" * 70)
    print(f"ADJUDICATION — lean-7d (episode-level, PIT) vs rotation: {g['verdict']}")
    print(f"git {out.get('git_sha')} | N registered trials {out['config']['n_registered_trials']} "
          f"| measured cost {out['config']['measured_rt_cost_pct']}% RT")
    print("=" * 70)
    lt = out["lean_7d"]["test_measured_cost"]
    print(f"\nlean-7d test @measured: n={lt['n']} WR={lt['win_rate_pct']}% exp={lt['expectancy_pct']}% "
          f"PF={lt['profit_factor']} | MDE={lt.get('mde_pct')} boot5exp={lt.get('boot5_expectancy_pct')} "
          f"boot5PF={lt.get('boot5_profit_factor')} top3rm={lt.get('top3_removed_expectancy_pct')}")
    rt = out["rotation_btc"]["test_measured_cost"]
    print(f"rotation test @measured: n={rt['n']} WR={rt['win_rate_pct']}% exp={rt['expectancy_pct']}% "
          f"PF={rt['profit_factor']}")
    pd_ = out["per_day_in_market_measured"]
    print(f"per day-in-market: lean {pd_['lean_7d']}%/d vs rotation {pd_['rotation_btc']}%/d")
    print("\nCRITERIA:")
    for c in g["criteria"]:
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['name']}: {_ascii(c['detail'])}")


# -----------------------------------------------------------------------------
# Q3 2026 registered rotation trials (STRATEGY.md v1.6) — daily-equity engine.
# One engine for incumbent + variants so comparisons are apples-to-apples:
# signals from each asset's own MA/band/30d rule, next-day-close fills, round-trip
# cost charged on each leg's entry, daily mark-to-market for true max drawdown.
# -----------------------------------------------------------------------------

Q3_TRIALS_OUT = os.path.join(DATA_DIR, "stage1c_q3_rotation_trials.json")


def _asset_signals(info: Dict[str, Any], ma_days: int, band_pct: float) -> List[bool]:
    """risk_on[d] per day index (same rule as live risk_regime, parameterized)."""
    closes = info["closes"]
    out = [False] * len(closes)
    for d in range(ma_days, len(closes)):
        ma = sum(closes[d - ma_days + 1: d + 1]) / ma_days
        ch30 = _pct(closes[d], closes[d - 30]) if d >= 30 else 0.0
        out[d] = closes[d] > ma * (1 + band_pct / 100.0) and ch30 > 0
    return out


def _realized_vol_ann(closes: List[float], d: int, window: int = 30) -> Optional[float]:
    """Annualized stdev of daily returns over the trailing window, at day d."""
    if d < window + 1:
        return None
    rets = [(closes[i] / closes[i - 1] - 1.0) for i in range(d - window + 1, d + 1)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return (var ** 0.5) * (365 ** 0.5) * 100.0


def _daily_rotation_sim(assets: List[Dict[str, Any]], weights: List[float], *,
                        ma_days: int = 200, band_pct: float = 1.5,
                        vol_target_pct: Optional[float] = None,
                        cost_rt_pct: float = ROUND_TRIP_COST_PCT) -> Dict[str, Any]:
    """Multi-asset rotation equity sim. Each asset is long its weight while its own
    rule is risk-on (scaled by vol targeting at entry when vol_target_pct is set),
    else that sleeve sits in cash. Full round-trip cost charged at each sleeve entry."""
    spine = assets[0]["dates"]  # BTC timeline
    sigs = [_asset_signals(a, ma_days, band_pct) for a in assets]
    eq, peak, maxdd = 1.0, 1.0, 0.0
    halves: Dict[str, Dict[str, float]] = {}
    # per-sleeve state: None when flat, else (entry_price, scale)
    pos: List[Optional[Tuple[float, float]]] = [None] * len(assets)
    n_flips = 0
    cur_half = None

    def _half_of(date_str: str) -> str:
        return "train_pre2023" if int(date_str[:4]) < WF_SPLIT_YEAR else "test_2023plus"

    for d0 in range(max(ma_days, 31), len(spine) - 1):
        date_str = spine[d0]
        hkey = _half_of(date_str)
        if hkey != cur_half:
            halves[hkey] = {"eq": 1.0, "peak": 1.0, "maxdd": 0.0}
            cur_half = hkey
        day_ret = 0.0
        for i, a in enumerate(assets):
            di = a["by_date"].get(date_str)
            if di is None or di + 1 >= len(a["closes"]):
                continue
            sig = sigs[i][di] if di < len(sigs[i]) else False
            px_now, px_next = a["closes"][di], a["closes"][di + 1]
            if pos[i] is not None:
                entry, scale = pos[i]
                day_ret += weights[i] * scale * (px_next / px_now - 1.0)
                if not sig:  # exit at next-day close
                    pos[i] = None
                    n_flips += 1
            elif sig:  # enter at next-day close; charge full RT cost on the sleeve now
                scale = 1.0
                if vol_target_pct is not None:
                    rv = _realized_vol_ann(a["closes"], di)
                    if rv and rv > 0:
                        scale = min(1.0, vol_target_pct / rv)
                pos[i] = (px_next, scale)
                day_ret -= weights[i] * scale * (cost_rt_pct / 100.0)
                n_flips += 1
        eq *= (1.0 + day_ret)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak * 100.0)
        h = halves[cur_half]
        h["eq"] *= (1.0 + day_ret)
        h["peak"] = max(h["peak"], h["eq"])
        h["maxdd"] = max(h["maxdd"], (h["peak"] - h["eq"]) / h["peak"] * 100.0)

    return {
        "total_net_pct": round((eq - 1.0) * 100.0, 1),
        "max_drawdown_pct": round(maxdd, 1),
        "n_leg_flips": n_flips,
        "halves": {k: {"net_pct": round((v["eq"] - 1.0) * 100.0, 1),
                       "maxdd_pct": round(v["maxdd"], 1)} for k, v in halves.items()},
    }


def run_q3_rotation_trials(verbose: bool = True) -> Dict[str, Any]:
    """Run H-2026Q3-1, H-2026Q3-2 and AUDIT-2026Q3-3 against their pre-registered
    criteria (STRATEGY.md v1.6). Uses cached daily data; nothing touches live state."""
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    universe = load_universe_daily(refresh=False)
    btc, eth = universe.get("bitcoin"), universe.get("ethereum")
    if not btc or not eth:
        raise RuntimeError("BTC/ETH daily data required")
    extra_meas = MEASURED_RT_COST_PCT  # measured-cost arm charges full measured RT

    def both_costs(fn):
        return {"modeled_cost": fn(ROUND_TRIP_COST_PCT), "measured_cost": fn(extra_meas)}

    plain = both_costs(lambda c: _daily_rotation_sim([btc], [1.0], cost_rt_pct=c))
    volm = both_costs(lambda c: _daily_rotation_sim([btc], [1.0], vol_target_pct=60.0, cost_rt_pct=c))
    dual = both_costs(lambda c: _daily_rotation_sim([btc, eth], [0.5, 0.5], cost_rt_pct=c))

    def _crit_h1() -> Tuple[bool, List[str]]:
        details = []
        ok = True
        for cost in ("modeled_cost", "measured_cost"):
            for half in ("train_pre2023", "test_2023plus"):
                p, v = plain[cost]["halves"][half], volm[cost]["halves"][half]
                dd_ok = v["maxdd_pct"] <= 0.75 * p["maxdd_pct"]
                ret_ok = v["net_pct"] >= 0.8 * p["net_pct"]
                ok = ok and dd_ok and ret_ok
                details.append(f"{cost}/{half}: DD {v['maxdd_pct']} vs cap {round(0.75*p['maxdd_pct'],1)} "
                               f"[{'ok' if dd_ok else 'FAIL'}]; net {v['net_pct']} vs floor "
                               f"{round(0.8*p['net_pct'],1)} [{'ok' if ret_ok else 'FAIL'}]")
        return ok, details

    def _crit_h2() -> Tuple[bool, List[str]]:
        details = []
        ok = True
        for cost in ("modeled_cost", "measured_cost"):
            for half in ("train_pre2023", "test_2023plus"):
                p, v = plain[cost]["halves"][half], dual[cost]["halves"][half]
                ret_ok = v["net_pct"] >= p["net_pct"]
                dd_ok = v["maxdd_pct"] <= 1.1 * p["maxdd_pct"]
                ok = ok and dd_ok and ret_ok
                details.append(f"{cost}/{half}: net {v['net_pct']} vs {p['net_pct']} "
                               f"[{'ok' if ret_ok else 'FAIL'}]; DD {v['maxdd_pct']} vs cap "
                               f"{round(1.1*p['maxdd_pct'],1)} [{'ok' if dd_ok else 'FAIL'}]")
        return ok, details

    h1_pass, h1_det = _crit_h1()
    h2_pass, h2_det = _crit_h2()

    # AUDIT-2026Q3-3: robustness grid at modeled cost
    grid = {}
    incumbent_net = None
    for ma in (180, 200, 220):
        for band in (1.0, 1.5, 2.0):
            r = _daily_rotation_sim([btc], [1.0], ma_days=ma, band_pct=band)
            grid[f"ma{ma}_band{band}"] = {"net_pct": r["total_net_pct"], "maxdd_pct": r["max_drawdown_pct"]}
            if ma == 200 and band == 1.5:
                incumbent_net = r["total_net_pct"]
    plateau = all(
        (v["net_pct"] > 0) == (incumbent_net > 0)
        and abs(v["net_pct"] - incumbent_net) <= 0.25 * abs(incumbent_net)
        for v in grid.values()
    )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **_git_provenance(),
        "driver": "stage1c_replay.py --q3-trials",
        "costs": {"modeled_rt_pct": ROUND_TRIP_COST_PCT, "measured_rt_pct": MEASURED_RT_COST_PCT},
        "plain_rotation_incumbent": plain,
        "H-2026Q3-1_vol_managed": {"result": volm, "verdict": "PASS" if h1_pass else "FAIL", "criteria_detail": h1_det},
        "H-2026Q3-2_dual_btc_eth": {"result": dual, "verdict": "PASS" if h2_pass else "FAIL", "criteria_detail": h2_det},
        "AUDIT-2026Q3-3_robustness": {"grid": grid, "incumbent_net_pct": incumbent_net,
                                      "verdict": "PLATEAU" if plateau else "CLIFF"},
    }
    if os.path.exists(Q3_TRIALS_OUT):
        os.replace(Q3_TRIALS_OUT, f"{Q3_TRIALS_OUT}.bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    with open(Q3_TRIALS_OUT, "w") as f:
        json.dump(out, f, indent=2, default=str)
    ts = out["generated_at"]
    for tid, verdict, cfg in (
        ("H-2026Q3-1", out["H-2026Q3-1_vol_managed"]["verdict"],
         {"strategy": "rotation_btc", "vol_target_pct": 60, "vol_window_d": 30, "ma_days": 200, "band_pct": 1.5}),
        ("H-2026Q3-2", out["H-2026Q3-2_dual_btc_eth"]["verdict"],
         {"strategy": "rotation_dual_btc_eth", "weights": [0.5, 0.5], "ma_days": 200, "band_pct": 1.5}),
        ("AUDIT-2026Q3-3", out["AUDIT-2026Q3-3_robustness"]["verdict"],
         {"strategy": "rotation_btc_grid", "ma_days": [180, 200, 220], "band_pct": [1.0, 1.5, 2.0]}),
    ):
        _append_trial({"ts": ts, "kind": "result", "trial_id": tid, "driver": out["driver"],
                       "config_hash": _config_hash(cfg), "verdict": verdict,
                       **{k: out[k] for k in ("git_sha", "working_tree_dirty")}})
    return out


def _print_q3_report(out: Dict[str, Any]) -> None:
    print("=" * 70)
    print("Q3 2026 REGISTERED ROTATION TRIALS (criteria fixed 2026-07-02, pre-run)")
    print(f"git {out.get('git_sha')} | costs modeled {out['costs']['modeled_rt_pct']}% / "
          f"measured {out['costs']['measured_rt_pct']}%")
    print("=" * 70)
    for label, key in (("PLAIN (incumbent)", "plain_rotation_incumbent"),
                       ("H1 VOL-MANAGED", "H-2026Q3-1_vol_managed"),
                       ("H2 DUAL BTC+ETH", "H-2026Q3-2_dual_btc_eth")):
        node = out[key]
        res = node.get("result", node)
        m = res["measured_cost"]
        v = f" — VERDICT: {node['verdict']}" if "verdict" in node else ""
        print(f"\n{label}{v}")
        print(f"  total(meas): net {m['total_net_pct']}%  maxDD {m['max_drawdown_pct']}%  flips {m['n_leg_flips']}")
        for half, hv in m["halves"].items():
            print(f"  {half:>15}: net {hv['net_pct']}%  maxDD {hv['maxdd_pct']}%")
    aud = out["AUDIT-2026Q3-3_robustness"]
    print(f"\nAUDIT robustness grid — VERDICT: {aud['verdict']} (incumbent net {aud['incumbent_net_pct']}%)")
    for cell, v in aud["grid"].items():
        print(f"  {cell:>16}: net {v['net_pct']:>8}%  maxDD {v['maxdd_pct']}%")


# -----------------------------------------------------------------------------
# H-2026Q3-4 — activation-gated trailing stop on BTC rotation (registered 2026-08-27).
#
# Origin: the owner observed that the pre-cohort system armed a trailing stop once a
# trade was ~5-10% in profit, and asked why rotation has none. Audit found the "wide
# fixed stop beats trailing" conclusion was established for the LEAN ALT strategy and
# then inherited by rotation WITHOUT being re-tested — an unexamined assumption.
#
# Params pinned a priori to the legacy config (activate +8%, trail 13%). No sweep:
# a 12-combo exploratory scan motivated the trial, so selecting the best cell here
# would be double-dipping. This tests one pre-declared rule.
# -----------------------------------------------------------------------------

TRAIL_TRIAL_OUT = os.path.join(DATA_DIR, "stage1c_h2026q3_4_trailing.json")


def _rotation_episodes_trailing(info: Dict[str, Any], ma_days: int, band_pct: float,
                                activate_pct: Optional[float], trail_pct: float,
                                cost_rt_pct: float, lo: int, hi: int) -> Dict[str, Any]:
    """Rotation over [lo,hi) with an optional activation-gated trailing stop.
    activate_pct=None disables the trail entirely (the incumbent rule).
    Returns compounded net return %, max drawdown %, and per-episode net returns."""
    closes = info["closes"]
    sig = _asset_signals(info, ma_days, band_pct)
    eq, peak, maxdd = 1.0, 1.0, 0.0
    pos = hwm = None
    armed = False
    rets: List[float] = []
    n_trail_exits = 0
    hi = min(hi, len(closes) - 1)
    for d in range(max(ma_days, lo), hi):
        px, nxt = closes[d], closes[d + 1]
        if pos is not None:
            hwm = max(hwm, px)
            if activate_pct is not None and (px / pos - 1.0) * 100 >= activate_pct:
                armed = True
            trail_hit = armed and px <= hwm * (1 - trail_pct / 100.0)
            if trail_hit or not sig[d]:
                r = (nxt / pos) * (1 - cost_rt_pct / 100.0) - 1.0
                rets.append(r * 100)
                eq *= (1 + r)
                pos = hwm = None
                armed = False
                if trail_hit:
                    n_trail_exits += 1
        elif sig[d]:
            pos, hwm, armed = nxt, nxt, False
        cur = eq * (px / pos) if pos else eq
        peak = max(peak, cur)
        maxdd = max(maxdd, (peak - cur) / peak * 100)
    if pos is not None:
        r = (closes[hi] / pos) * (1 - cost_rt_pct / 100.0) - 1.0
        rets.append(r * 100)
        eq *= (1 + r)
    return {"net_pct": round((eq - 1) * 100, 1), "max_dd_pct": round(maxdd, 1),
            "episode_returns": rets, "n_episodes": len(rets), "n_trail_exits": n_trail_exits}


def run_trailing_stop_trial(verbose: bool = True) -> Dict[str, Any]:
    """H-2026Q3-4 against its pre-registered criteria. Measured cost, 4 purged folds,
    bootstrap, deflated hurdle. Observational — changes no live behavior."""
    import numpy as np
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    universe = load_universe_daily(refresh=False)
    btc = universe["bitcoin"]
    dates = btc["dates"]
    ACT, TRAIL = 8.0, 13.0                 # pinned a priori
    COST = MEASURED_RT_COST_PCT            # 2.4% measured, NOT the 0.66% model
    PURGE_D = 30

    def _idx(datestr: str) -> int:
        return next((i for i, d in enumerate(dates) if d >= datestr), len(dates) - 1)

    # --- (a) 4 rolling yearly OOS folds, 30-day purge at each fold start ---
    folds: Dict[str, Any] = {}
    wins = 0
    for y in (2022, 2023, 2024, 2025):
        lo, hi = _idx(f"{y}-01-01") + PURGE_D, _idx(f"{y + 1}-01-01")
        base = _rotation_episodes_trailing(btc, 200, 1.5, None, TRAIL, COST, lo, hi)
        test = _rotation_episodes_trailing(btc, 200, 1.5, ACT, TRAIL, COST, lo, hi)
        beat = test["net_pct"] > base["net_pct"]
        wins += 1 if beat else 0
        folds[str(y)] = {"incumbent_net_pct": base["net_pct"], "trailing_net_pct": test["net_pct"],
                         "delta_pp": round(test["net_pct"] - base["net_pct"], 1), "beat": beat}

    # --- (b)(c) pooled OOS at measured cost ---
    lo_p, hi_p = _idx("2022-01-01") + PURGE_D, len(dates) - 1
    base_p = _rotation_episodes_trailing(btc, 200, 1.5, None, TRAIL, COST, lo_p, hi_p)
    test_p = _rotation_episodes_trailing(btc, 200, 1.5, ACT, TRAIL, COST, lo_p, hi_p)

    # --- (d) bootstrap on per-episode net returns ---
    rng = np.random.default_rng(42)
    tr = np.array(test_p["episode_returns"], dtype=float)
    boot5 = None
    if len(tr) >= 10:
        idx = rng.integers(0, len(tr), size=(10000, len(tr)))
        boot5 = float(np.percentile(tr[idx].mean(axis=1), 5))

    # --- (e) deflated hurdle on the mean-return improvement ---
    import math
    bs = np.array(base_p["episode_returns"], dtype=float)
    n_trials = _registered_trial_count()
    sd = float(np.std(tr, ddof=1)) if len(tr) > 1 else 0.0
    hurdle = math.sqrt(2 * math.log(max(2, n_trials))) * sd / math.sqrt(max(1, len(tr)))
    improvement = (float(tr.mean()) - float(bs.mean())) if len(tr) and len(bs) else 0.0

    crit = []
    def add(name, passed, detail):
        crit.append({"name": name, "passed": bool(passed), "detail": detail})
    add("a_folds_3_of_4", wins >= 3, f"{wins}/4 folds beat incumbent")
    add("b_pooled_oos_at_measured_cost", test_p["net_pct"] >= base_p["net_pct"],
        f"trailing {test_p['net_pct']}% vs incumbent {base_p['net_pct']}% @ {COST}% RT")
    add("c_drawdown_not_worse", test_p["max_dd_pct"] <= base_p["max_dd_pct"],
        f"maxDD {test_p['max_dd_pct']}% vs {base_p['max_dd_pct']}%")
    add("d_bootstrap_5pct_beats_incumbent",
        boot5 is not None and boot5 > float(bs.mean()) if len(bs) else False,
        f"boot 5th-pct mean {boot5 if boot5 is None else round(boot5,3)}% vs incumbent mean "
        f"{round(float(bs.mean()),3) if len(bs) else 'n/a'}%")
    add("e_exceeds_deflated_hurdle", improvement > hurdle,
        f"improvement {improvement:+.3f}pp/episode vs hurdle {hurdle:.3f}pp (N={n_trials} trials)")

    failing = [c["name"] for c in crit if not c["passed"]]
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(), **_git_provenance(),
        "trial_id": "H-2026Q3-4", "driver": "stage1c_replay.py --trailing-trial",
        "config": {"activate_pct": ACT, "trail_pct": TRAIL, "cost_rt_pct": COST,
                   "purge_days": PURGE_D, "params": "pinned a priori (legacy config), no sweep"},
        "folds": folds, "pooled": {"incumbent": base_p, "trailing": test_p},
        "verdict": "PASS" if not failing else "FAIL", "failing": failing, "criteria": crit,
    }
    for k in ("incumbent", "trailing"):   # keep artifact small
        out["pooled"][k].pop("episode_returns", None)
    if os.path.exists(TRAIL_TRIAL_OUT):
        os.replace(TRAIL_TRIAL_OUT,
                   f"{TRAIL_TRIAL_OUT}.bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    with open(TRAIL_TRIAL_OUT, "w") as f:
        json.dump(out, f, indent=2, default=str)
    _append_trial({"ts": out["generated_at"], "kind": "result", "trial_id": "H-2026Q3-4",
                   "driver": out["driver"], "verdict": out["verdict"],
                   "config_hash": _config_hash(out["config"]),
                   **{k: out[k] for k in ("git_sha", "working_tree_dirty")}})
    return out


# -----------------------------------------------------------------------------
# Q4 2026 registered trials (STRATEGY.md v2.0) — VENUE-2026Q4-1, H-2026Q4-2
#
# Interpretation choices for wording the registration left open, written into the code
# (and the artifacts) BEFORE the first run, 2026-10-11:
#   * "return/maxDD" = CAGR / |max drawdown| (Calmar); total-return ratio reported too.
#   * VENUE (c) bootstrap uses CLOSED 2023+ OOS episodes (same sample as every G6 so far);
#     the open-at-data-edge episode counts in the equity curve (marked, like HODL) but
#     not in episode statistics.
#   * H2 contrast "rejected entries" = 2023+ entries of the SAME rule run WITHOUT the
#     ETH/BTC condition whose entry date fails that condition.
#   * H2 G8 uses the v2.0 wording: compounded return OR Calmar vs the better of HODL BTC
#     and rotation (measured cost), 2023-01-01 → data end; the alt book is marked to
#     market daily with 8 fixed 1/8 slots (live MAX_OPEN_TOTAL parity).
#   * Folds use the existing _fold_stats rule: a fold with zero trades is NOT positive
#     (applied as registered, even though it penalises rotation for sitting out 2022).
# One shot: each trial refuses to run if a result row already exists for its id.
# -----------------------------------------------------------------------------

VENUE_TRIAL_OUT = os.path.join(DATA_DIR, "stage1c_venue_2026q4_1.json")
ALT_REGIME_TRIAL_OUT = os.path.join(DATA_DIR, "stage1c_alt_regime_2026q4_2.json")
LEAN7_REGISTERED_HASH = "41c90fbe47eb2ab8"     # adjudication config, 2026-06-11
ETH_BTC_SMA_DAYS = 50                          # pinned by H-2026Q4-2
TIER_REQUIREMENTS = {
    "coinbase_base_taker": "Coinbase Advanced base tier (<$1K 30-day volume) — today's measured cost",
    "kraken_t1_taker": "Kraken Pro tier 1 ($0+), market orders",
    "kraken_t3_taker": "Kraken Pro tier 3: $10K 30-day volume OR $20K assets on platform; market orders",
    "kraken_t3_maker": "Kraken Pro tier 3 AND resting limit (maker) fills — fill rate unproven",
}


def _load_registration(trial_id: str) -> Dict[str, Any]:
    """The registered row, or raise. Enforces run_not_before and one-shot execution."""
    reg, has_result = None, False
    with open(TRIALS_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("trial_id") != trial_id:
                continue
            if r.get("kind") == "registered":
                reg = r
            elif r.get("kind") == "result" or r.get("verdict"):
                has_result = True
    if reg is None:
        raise RuntimeError(f"{trial_id} is not registered — register criteria BEFORE running")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today < reg.get("run_not_before", "0000"):
        raise RuntimeError(f"{trial_id} may not run before {reg['run_not_before']}")
    if has_result:
        raise RuntimeError(f"{trial_id} already has a recorded result — trials run once")
    return reg


def _curve_stats(curve: List[float], n_days: int) -> Dict[str, Any]:
    """Total return, CAGR, max drawdown, Calmar from an equity curve starting at 1.0."""
    if not curve:
        return {"total_pct": 0.0, "cagr_pct": 0.0, "max_dd_pct": 0.0, "calmar": None}
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    years = max(n_days, 1) / 365.0
    total = curve[-1] / curve[0] - 1
    cagr = (curve[-1] / curve[0]) ** (1 / years) - 1 if curve[-1] > 0 else -1.0
    return {"total_pct": round(total * 100, 2), "cagr_pct": round(cagr * 100, 2),
            "max_dd_pct": round(mdd * 100, 2),
            "calmar": round(cagr / abs(mdd), 3) if mdd < 0 else None,
            "total_over_dd": round(total / abs(mdd), 3) if mdd < 0 else None}


def _hodl_curve(info: Dict[str, Any], i0: int, i1: int) -> List[float]:
    c = info["closes"]
    return [c[t] / c[i0] for t in range(i0, i1 + 1)]


def _rotation_daily(info: Dict[str, Any], band_pct: float, cost_side_pct: float,
                    i0: int, i1: int) -> Tuple[List[float], List[Dict[str, Any]]]:
    """Daily equity of the rotation rule over [i0, i1] (same signal and next-close fill
    as _rotation_episodes) with cost_side_pct charged on each leg. Returns (curve,
    episodes); an episode still open at i1 is marked but flagged open."""
    closes, dates = info["closes"], info["dates"]
    eq, units, in_pos = 1.0, 0.0, False
    entry_eq, entry_i = 1.0, None
    curve = [1.0]
    eps: List[Dict[str, Any]] = []
    for d in range(i0, i1):
        if d >= MA200_DAYS - 1 and d >= 30:
            ma = sum(closes[d - MA200_DAYS + 1: d + 1]) / MA200_DAYS
            on = closes[d] > ma * (1 + band_pct / 100.0) and _pct(closes[d], closes[d - 30]) > 0
        else:
            on = False
        nxt = d + 1
        if on and not in_pos:
            entry_eq, entry_i = eq, nxt
            units = eq * (1 - cost_side_pct / 100.0) / closes[nxt]
            in_pos = True
        elif not on and in_pos:
            eq = units * closes[nxt] * (1 - cost_side_pct / 100.0)
            eps.append({"date": dates[entry_i], "year": int(dates[entry_i][:4]),
                        "pnl_pct": round((eq / entry_eq - 1) * 100, 4), "open": False})
            in_pos, units = False, 0.0
        curve.append(units * closes[nxt] if in_pos else eq)
    if in_pos and entry_i is not None:
        eps.append({"date": dates[entry_i], "year": int(dates[entry_i][:4]),
                    "pnl_pct": round((curve[-1] / entry_eq - 1) * 100, 4), "open": True})
    return curve, eps


def _eth_btc_gate(universe: Dict[str, Any]):
    """H-2026Q4-2 condition: ETH/BTC daily close > its SMA50 computed through t-1.
    Built from every ETH segment; a date without 50 prior aligned closes is False
    (fail-closed: no evidence of an alt-favorable regime, no entry)."""
    btc = universe["bitcoin"]
    eth_close: Dict[str, float] = {}
    for info in universe.values():
        if info.get("symbol") == "ETH":
            for ds, i in info["by_date"].items():
                eth_close[ds] = info["closes"][i]
    dates = [ds for ds in btc["dates"] if ds in eth_close]
    ratio = [eth_close[ds] / btc["closes"][btc["by_date"][ds]] for ds in dates]
    ok: Dict[str, bool] = {}
    for k in range(ETH_BTC_SMA_DAYS, len(dates)):
        sma_prev = sum(ratio[k - ETH_BTC_SMA_DAYS: k]) / ETH_BTC_SMA_DAYS
        ok[dates[k]] = ratio[k] > sma_prev
    return lambda ds: ok.get(ds, False), ok


def _portfolio_curve(trades: List[Dict[str, Any]], universe: Dict[str, Any],
                     btc_dates: List[str], start: str, extra_cost: float,
                     slot_frac: float = 1 / 8) -> List[float]:
    """Alt book marked to market daily: each trade is a fixed 1/8 slot; its daily path
    follows closes and its final day reproduces the trade's gross P&L exactly; the
    trade's cost (modeled fee + extra) lands on its exit day."""
    daily: Dict[str, float] = {}
    for t in trades:
        info = universe[t["gid"]]
        closes, dates = info["closes"], info["dates"]
        di, hold = int(t["di"]), max(1, int(t["hold_days"]))
        path_prev = 1.0
        for k in range(1, hold + 1):
            if di + k >= len(closes):
                break
            p_k = (1 + t["gross_pnl_pct"] / 100.0) if k == hold else closes[di + k] / closes[di]
            r = p_k / path_prev - 1
            if k == hold:
                r -= (FEE_PCT * 2 + extra_cost) / 100.0
            daily[dates[di + k]] = daily.get(dates[di + k], 0.0) + slot_frac * r
            path_prev = p_k
    curve, eq = [1.0], 1.0
    for ds in btc_dates:
        if ds <= start:
            continue
        eq *= 1 + daily.get(ds, 0.0)
        curve.append(eq)
    return curve


def _random_entry_baseline(trades: List[Dict[str, Any]], universe: Dict[str, Any],
                           eligible: Dict[str, List[Tuple[str, int]]], max_hold: int,
                           extra_cost: float, n_resamples: int = 1000) -> Dict[str, Any]:
    """G7: same entry dates, a coin drawn uniformly from that day's eligible universe,
    IDENTICAL exits (the trade's own stop/target/max-hold). Strategy must beat the 95th
    percentile of the baseline mean."""
    import numpy as np
    per_episode = []
    for t in trades:
        pool = eligible.get(t["date"], [])
        vals = [simulate_daily(universe[g], di, t["stop_pct"], t["target_pct"], max_hold=max_hold)["pnl_pct"]
                - extra_cost for g, di in pool]
        if vals:
            per_episode.append(np.array(vals, dtype=float))
    if not per_episode:
        return {"n": 0, "p95_mean_pct": None, "strategy_mean_pct": None}
    rng = np.random.default_rng(42)
    means = np.empty(n_resamples)
    for r in range(n_resamples):
        means[r] = float(np.mean([v[rng.integers(0, len(v))] for v in per_episode]))
    strat = float(np.mean([t["pnl_pct"] - extra_cost for t in trades]))
    return {"n": len(per_episode), "resamples": n_resamples,
            "baseline_median_pct": round(float(np.median(means)), 3),
            "p95_mean_pct": round(float(np.percentile(means, 95)), 3),
            "strategy_mean_pct": round(strat, 3),
            "strategy_percentile": round(float((means < strat).mean() * 100), 1)}


def _boot_diff_p5(a: List[float], b: List[float], n_boot: int = 10_000) -> Optional[float]:
    import numpy as np
    if len(a) < 2 or len(b) < 2:
        return None
    rng = np.random.default_rng(42)
    A, B = np.array(a, dtype=float), np.array(b, dtype=float)
    da = A[rng.integers(0, len(A), size=(n_boot, len(A)))].mean(axis=1)
    db = B[rng.integers(0, len(B), size=(n_boot, len(B)))].mean(axis=1)
    return round(float(np.percentile(da - db, 5)), 3)


def _write_artifact(path: str, out: Dict[str, Any]) -> None:
    if os.path.exists(path):
        os.replace(path, f"{path}.bak.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)


def run_q4_trials(verbose: bool = True) -> Dict[str, Any]:
    """Run VENUE-2026Q4-1 and H-2026Q4-2 against their registered criteria, once."""
    import numpy as np
    from thresholds import threshold_override
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("quant_signal", "scorer", "indicators", "market_regime", "dynamic_targets"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    reg_v = _load_registration("VENUE-2026Q4-1")
    reg_h = _load_registration("H-2026Q4-2")
    lean7_cfg = {**LEAN_GATE_CONFIG, "LEAN_MAX_HOLD_DAYS": 7}
    got = _config_hash({**lean7_cfg, "episode_mode": True})
    if got != LEAN7_REGISTERED_HASH:
        raise RuntimeError(f"lean-7d config drifted: {got} != registered {LEAN7_REGISTERED_HASH}")
    prov = _git_provenance()
    ts = datetime.now(timezone.utc).isoformat()

    universe, coverage = load_universe_pit(refresh=False)
    membership = load_membership()
    fg = fetch_fear_greed_all()
    btc = universe["bitcoin"]
    bdates = btc["dates"]
    i_end = len(bdates) - 1
    i_2023 = btc["by_date"].get("2023-01-01") or next(i for i, d in enumerate(bdates) if d >= "2023-01-01")

    gate, gate_map = _eth_btc_gate(universe)
    eligible: Dict[str, List[Tuple[str, int]]] = {}
    logger.info("[q4] lean-7d episode run, UNCONDITIONAL (contrast + venue context) …")
    with threshold_override(lean7_cfg):
        uncond = run_pipeline(universe, fg, stage1b_active=True, verbose=verbose,
                              membership=membership, episode_mode=True)
        logger.info("[q4] lean-7d episode run, CONDITIONAL on ETH/BTC > SMA50(t-1) …")
        cond = run_pipeline(universe, fg, stage1b_active=True, verbose=verbose,
                            membership=membership, episode_mode=True,
                            entry_gate=gate, eligible_out=eligible)

    # ============================ VENUE-2026Q4-1 ============================
    tiers = reg_v["config"]["per_side_cost_pct"]
    i0 = MA200_DAYS
    hodl_full = _curve_stats(_hodl_curve(btc, i0, i_end), i_end - i0)
    tier_out: Dict[str, Any] = {}
    for name, side in sorted(tiers.items(), key=lambda kv: -kv[1]):
        curve, eps = _rotation_daily(btc, 1.5, float(side), i0, i_end)
        full = _curve_stats(curve, i_end - i0)
        closed = [e for e in eps if not e["open"]]
        folds = _fold_stats(closed)
        test = _rich_stats([e for e in closed if e["year"] >= WF_SPLIT_YEAR])
        a_ret = full["total_pct"] >= hodl_full["total_pct"]
        a_cal = (full["calmar"] or 0) >= 1.25 * (hodl_full["calmar"] or 9e9)
        crit = [
            {"name": "a_return_or_calmar_vs_hodl", "passed": a_ret or a_cal,
             "detail": f"total {full['total_pct']}% vs HODL {hodl_full['total_pct']}% | Calmar "
                       f"{full['calmar']} vs 1.25x HODL {round(1.25 * (hodl_full['calmar'] or 0), 3)} "
                       f"(total/DD {full['total_over_dd']} vs HODL {hodl_full['total_over_dd']})"},
            {"name": "b_folds_3_of_4", "passed": folds["passes_3_of_4"],
             "detail": f"{folds['folds_positive']}/4 folds positive: "
                       + ", ".join(f"{y}: n={s['n']} exp={s['expectancy_pct']}%" for y, s in folds["folds"].items())},
            {"name": "c_boot5_oos_episode_exp_pos", "passed": (test.get("boot5_expectancy_pct") or -1) > 0,
             "detail": f"2023+ closed episodes n={test['n']}: exp {test['expectancy_pct']}%, "
                       f"boot 5th-pct {test.get('boot5_expectancy_pct')}%"},
        ]
        extra = 2 * float(side) - ROUND_TRIP_COST_PCT
        lean_test = [t for t in uncond if t["year"] >= WF_SPLIT_YEAR]
        tier_out[name] = {
            "per_side_cost_pct": side, "requires": TIER_REQUIREMENTS.get(name, ""),
            "full_window": full, "passes": all(c["passed"] for c in crit), "criteria": crit,
            "lean7_context_2023plus": _half_stats(lean_test, extra_cost=extra),
        }
    passing = [n for n, v in tier_out.items() if v["passes"]]
    venue = {
        "generated_at": ts, **prov, "trial_id": "VENUE-2026Q4-1",
        "driver": "stage1c_replay.py --q4-trials", "registered": reg_v,
        "window": f"{bdates[i0]} -> {bdates[i_end]}", "hodl_btc_full_window": hodl_full,
        "tiers": tier_out,
        "cheapest_passing_tier": passing[0] if passing else None,   # tiers ordered costliest-first
        "verdict": "PASS" if passing else "FAIL",
    }

    # ============================== H-2026Q4-2 ==============================
    extra_meas = MEASURED_RT_COST_PCT - ROUND_TRIP_COST_PCT
    cond_test = [t for t in cond if t["year"] >= WF_SPLIT_YEAR]
    st = _rich_stats(cond_test, extra_cost=extra_meas)
    folds = _fold_stats(cond, extra_cost=extra_meas)
    n_trials = _registered_trial_count()
    hurdle = _deflated_hurdle_pct(st.get("stdev_pct") or 0.0, st["n"], n_trials)
    g7 = _random_entry_baseline(cond_test, universe, eligible, 7, extra_meas)

    start = bdates[i_2023 - 1]
    strat_c = _curve_stats(_portfolio_curve(cond_test, universe, bdates, start, extra_meas), i_end - i_2023 + 1)
    hodl_c = _curve_stats(_hodl_curve(btc, i_2023 - 1, i_end), i_end - i_2023 + 1)
    rot_curve, _ = _rotation_daily(btc, 1.5, MEASURED_RT_COST_PCT / 2, i_2023 - 1, i_end)
    rot_c = _curve_stats(rot_curve, i_end - i_2023 + 1)
    best_total = max(hodl_c["total_pct"], rot_c["total_pct"])
    best_cal = max(hodl_c["calmar"] or -9e9, rot_c["calmar"] or -9e9)

    rejected = [t["pnl_pct"] - extra_meas for t in uncond
                if t["year"] >= WF_SPLIT_YEAR and not gate(t["date"])]
    accepted = [t["pnl_pct"] - extra_meas for t in cond_test]
    contrast = (float(np.mean(accepted)) - float(np.mean(rejected))) if accepted and rejected else None
    contrast_p5 = _boot_diff_p5(accepted, rejected)

    crit = []
    def add(name, passed, detail, group):
        crit.append({"name": name, "passed": bool(passed), "detail": detail, "group": group})
    add("G4_n_episodes_100", st["n"] >= 100, f"conditional 2023+ episodes n={st['n']}", "G4")
    add("G4_exceeds_mde", st["expectancy_pct"] > (st.get("mde_pct") or 9e9),
        f"exp {st['expectancy_pct']}% vs MDE {st.get('mde_pct')}%", "G4")
    add("G3_folds_3_of_4", folds["passes_3_of_4"], f"{folds['folds_positive']}/4 folds positive at measured cost", "core")
    add("G5_deflated_hurdle", st["expectancy_pct"] > hurdle,
        f"exp {st['expectancy_pct']}% vs hurdle {round(hurdle, 3)}% (N={n_trials})", "core")
    add("G6_boot5_expectancy_pos", (st.get("boot5_expectancy_pct") or -1) > 0,
        f"boot 5th-pct exp {st.get('boot5_expectancy_pct')}%", "core")
    add("G6_boot5_pf_gt_1", (st.get("boot5_profit_factor") or 0) > 1.0,
        f"boot 5th-pct PF {st.get('boot5_profit_factor')}", "core")
    add("G6_survives_top3_removal", (st.get("top3_removed_expectancy_pct") or -1) > 0,
        f"top3-removed exp {st.get('top3_removed_expectancy_pct')}%", "core")
    add("G7_beats_random_entry_p95", g7["strategy_mean_pct"] is not None and g7["strategy_mean_pct"] > (g7["p95_mean_pct"] or 9e9),
        f"strategy {g7['strategy_mean_pct']}% vs random-entry p95 {g7['p95_mean_pct']}% "
        f"(percentile {g7.get('strategy_percentile')})", "core")
    add("G9_net_exp_ge_2pp", st["expectancy_pct"] >= 2.0, f"net exp {st['expectancy_pct']}% vs >=2pp", "core")
    add("G8_vs_best_of_hodl_rotation", strat_c["total_pct"] >= best_total or (strat_c["calmar"] or -9e9) >= best_cal,
        f"alt book total {strat_c['total_pct']}% / Calmar {strat_c['calmar']} vs best total {best_total}% / "
        f"best Calmar {round(best_cal, 3)} (HODL {hodl_c['total_pct']}%/{hodl_c['calmar']}, "
        f"rotation {rot_c['total_pct']}%/{rot_c['calmar']})", "core")
    add("contrast_cond_minus_rejected", contrast is not None and contrast > 0 and (contrast_p5 or -1) > 0,
        f"accepted {round(float(np.mean(accepted)), 3) if accepted else None}% (n={len(accepted)}) - rejected "
        f"{round(float(np.mean(rejected)), 3) if rejected else None}% (n={len(rejected)}) = "
        f"{round(contrast, 3) if contrast is not None else None}pp, boot 5th-pct {contrast_p5}pp", "core")
    core_fail = [c["name"] for c in crit if c["group"] == "core" and not c["passed"]]
    g4_fail = [c["name"] for c in crit if c["group"] == "G4" and not c["passed"]]
    verdict = "FAIL" if core_fail else ("INSUFFICIENT_N" if g4_fail else "PASS")
    stress = _half_stats(cond_test, extra_cost=STRESS_RT_COST_PCT - ROUND_TRIP_COST_PCT)
    regime_days = sum(1 for d, v in gate_map.items() if v and d >= "2023-01-01")
    total_days = sum(1 for d in gate_map if d >= "2023-01-01")
    alt = {
        "generated_at": ts, **prov, "trial_id": "H-2026Q4-2",
        "driver": "stage1c_replay.py --q4-trials", "registered": reg_h, "coverage": coverage,
        "conditional_test_measured": st, "folds_measured": folds, "random_entry_g7": g7,
        "portfolio_2023plus": {"alt_book": strat_c, "hodl_btc": hodl_c, "rotation_measured": rot_c},
        "unconditional_test_measured": _half_stats([t for t in uncond if t["year"] >= WF_SPLIT_YEAR],
                                                   extra_cost=extra_meas),
        "context_only_G2_stress": stress,
        "alt_favorable_days_2023plus": f"{regime_days}/{total_days}",
        "criteria": crit, "core_failing": core_fail, "g4_failing": g4_fail, "verdict": verdict,
    }
    # Record only after BOTH trials computed: a crash midway must not leave one trial
    # "used" (one-shot guard) with the other unrun.
    _write_artifact(VENUE_TRIAL_OUT, venue)
    _write_artifact(ALT_REGIME_TRIAL_OUT, alt)
    _append_trial({"ts": ts, "kind": "result", "trial_id": "VENUE-2026Q4-1", "driver": venue["driver"],
                   "verdict": venue["verdict"], "cheapest_passing_tier": venue["cheapest_passing_tier"],
                   "config_hash": reg_v["config_hash"], **prov})
    _append_trial({"ts": ts, "kind": "result", "trial_id": "H-2026Q4-2", "driver": alt["driver"],
                   "verdict": verdict, "config_hash": reg_h["config_hash"], **prov})
    return {"venue": venue, "alt_regime": alt}


def _print_q4_report(out: Dict[str, Any]) -> None:
    v, a = out["venue"], out["alt_regime"]
    print("=" * 70)
    print(f"VENUE-2026Q4-1 (rotation vs HODL by venue cost): {v['verdict']} | window {v['window']}")
    h = v["hodl_btc_full_window"]
    print(f"  HODL BTC: total {h['total_pct']}%  CAGR {h['cagr_pct']}%  maxDD {h['max_dd_pct']}%  Calmar {h['calmar']}")
    for name, t in v["tiers"].items():
        f = t["full_window"]
        print(f"  [{'PASS' if t['passes'] else 'FAIL'}] {name} ({t['per_side_cost_pct']}%/side): total {f['total_pct']}%"
              f"  CAGR {f['cagr_pct']}%  maxDD {f['max_dd_pct']}%  Calmar {f['calmar']}")
        for c in t["criteria"]:
            print(f"       [{'ok' if c['passed'] else 'X '}] {c['name']}: {_ascii(c['detail'])}")
        lc = t["lean7_context_2023plus"]
        print(f"       lean-7d context 2023+: n={lc['n']} exp {lc['expectancy_pct']}%/trade")
    print(f"  cheapest passing tier: {v['cheapest_passing_tier']}")
    print("=" * 70)
    print(f"H-2026Q4-2 (alts conditional on ETH/BTC > SMA50): {a['verdict']}")
    print(f"  alt-favorable days 2023+: {a['alt_favorable_days_2023plus']}")
    for c in a["criteria"]:
        print(f"    [{'PASS' if c['passed'] else 'FAIL'}] ({c['group']}) {c['name']}: {_ascii(c['detail'])}")
    print(f"  unconditional 2023+ (context): {a['unconditional_test_measured']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 1c multi-year daily walk-forward gate")
    ap.add_argument("--refresh", action="store_true", help="re-fetch daily OHLCV (ignore cache)")
    ap.add_argument("--quiet", action="store_true", help="less logging")
    ap.add_argument("--lean", action="store_true",
                    help="run the legacy-vs-lean_full A/B gate (reproduces stage1c_lean_full_gate.json)")
    ap.add_argument("--pit", action="store_true",
                    help="run the legacy-vs-lean A/B on the POINT-IN-TIME universe (G1 honest run)")
    ap.add_argument("--rotation", action="store_true",
                    help="run the BTC/ETH risk-on rotation benchmark (G8 null strategy)")
    ap.add_argument("--pit-holds", action="store_true",
                    help="run lean hold-period arms 7/14/21d on the PIT universe (registered trials)")
    ap.add_argument("--adjudicate", action="store_true",
                    help="episode-level lean-7d vs rotation, full G-gate verdict (STRATEGY.md v1.3)")
    ap.add_argument("--q3-trials", action="store_true",
                    help="run the Q3 2026 registered rotation trials (STRATEGY.md v1.6)")
    ap.add_argument("--trailing-trial", action="store_true",
                    help="run H-2026Q3-4: activation-gated trailing stop on rotation")
    ap.add_argument("--q4-trials", action="store_true",
                    help="run VENUE-2026Q4-1 + H-2026Q4-2 once, against their registered criteria")
    args = ap.parse_args()
    if args.q4_trials:
        _print_q4_report(run_q4_trials(verbose=not args.quiet))
    elif args.trailing_trial:
        out = run_trailing_stop_trial(verbose=not args.quiet)
        print("=" * 70)
        print(f"H-2026Q3-4 activation-gated trailing stop ({out['config']['activate_pct']}% / "
              f"{out['config']['trail_pct']}%) @ {out['config']['cost_rt_pct']}% cost: {out['verdict']}")
        print("=" * 70)
        for y, f in out["folds"].items():
            print(f"  fold {y}: incumbent {f['incumbent_net_pct']:>7}%  trailing {f['trailing_net_pct']:>7}%"
                  f"  {f['delta_pp']:+7}pp  {'BEAT' if f['beat'] else 'lost'}")
        p = out["pooled"]
        print(f"  pooled : incumbent {p['incumbent']['net_pct']}% (DD {p['incumbent']['max_dd_pct']}%) | "
              f"trailing {p['trailing']['net_pct']}% (DD {p['trailing']['max_dd_pct']}%, "
              f"{p['trailing']['n_trail_exits']} trail exits)")
        print("\n  CRITERIA:")
        for c in out["criteria"]:
            print(f"    [{'PASS' if c['passed'] else 'FAIL'}] {c['name']}: {_ascii(c['detail'])}")
    elif args.q3_trials:
        out = run_q3_rotation_trials(verbose=not args.quiet)
        _print_q3_report(out)
    elif args.adjudicate:
        out = run_adjudication(verbose=not args.quiet)
        _print_adjudication(out)
    elif args.pit_holds:
        out = run_pit_hold_arms(verbose=not args.quiet)
        _print_holds_report(out)
    elif args.rotation:
        out = run_rotation_gate(verbose=not args.quiet)
        _print_rotation_report(out)
    elif args.pit:
        out = run_pit_gate(refresh=args.refresh, verbose=not args.quiet)
        _print_pit_report(out)
    elif args.lean:
        out = run_lean_gate(refresh=args.refresh, verbose=not args.quiet)
        _print_lean_report(out)
    else:
        out = run_gate(refresh=args.refresh, verbose=not args.quiet)
        _print_report(out)


if __name__ == "__main__":
    main()
