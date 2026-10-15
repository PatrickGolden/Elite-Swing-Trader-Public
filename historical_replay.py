# =============================================================================
# historical_replay.py — Historical Replay Through Live Signal Pipeline
#
# Feeds historical OHLCV data through the EXACT same pipeline that makes
# live trading decisions: calculate_indicators() → score_coin() →
# generate_quant_signal() → simulate trade outcome.
#
# Output: optimizer-compatible attribution records for accelerated learning.
#
# Data sources (all free):
#   - CoinGecko /coins/{id}/market_chart?days=90 (hourly granularity)
#   - Alternative.me /fng/?limit=100 (historical Fear & Greed)
#
# Usage:
#   from historical_replay import run_historical_replay
#   results = run_historical_replay()         # Returns dict with trades, summary
#   results = run_historical_replay(days=60)  # Custom simulation window
# =============================================================================

import json
import logging
import math
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

# Wire OS trust store into Python's SSL stack (must come before any
# import that creates an SSLContext, e.g. requests/urllib3/ccxt).
import tls_setup  # noqa: F401

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
CACHE_DIR = os.path.join(DATA_DIR, 'historical_cache')
OUTPUT_FILE = os.path.join(DATA_DIR, 'backtest_trades.json')
MISSED_OUTPUT_FILE = os.path.join(DATA_DIR, 'backtest_missed.json')

# Top coins for replay (CoinGecko IDs) — liquid, on-exchange, US-accessible
TOP_COINS = [
    ('bitcoin', 'BTC'), ('ethereum', 'ETH'), ('solana', 'SOL'),
    ('ripple', 'XRP'), ('cardano', 'ADA'), ('dogecoin', 'DOGE'),
    ('chainlink', 'LINK'), ('avalanche-2', 'AVAX'), ('polkadot', 'DOT'),
    ('litecoin', 'LTC'), ('uniswap', 'UNI'), ('near', 'NEAR'),
    ('cosmos', 'ATOM'), ('sui', 'SUI'), ('aptos', 'APT'),
]

# Execution assumptions (conservative per project rules)
SLIPPAGE_PCT = 0.30        # 0.3% market order slippage
FEE_PCT = 0.18             # 0.18% per side (Coinbase Advanced)
ROUND_TRIP_COST_PCT = SLIPPAGE_PCT + (FEE_PCT * 2)  # ~0.66% total drag

# Simulation params
# NOTE: two distinct windows — do not conflate them.
#   MAX_HOLD_HOURS        = forward window for HOLD/SELL outcome labeling (outcome_7d). MUST stay 7d
#                           or the `outcome_7d` field (used by the optimizer + Step 0) becomes a lie.
#   TRADE_MAX_HOLD_HOURS  = max hold for an *entered* trade simulation. Phase 1 (2026-05-30) raised
#                           this 7d -> 21d so the backtest matches the live "let winners run" policy
#                           (trail 13% + 8% activation needs room to run; validated in replay).
MAX_HOLD_HOURS = 7 * 24     # 7-day forward window for outcome_7d labeling (do NOT change)
TRADE_MAX_HOLD_HOURS = 21 * 24  # 21-day max hold for entered-trade simulation (matches live STALE_POSITION_MAX_DAYS)
HOURLY_CHECK = True         # Check stop/target every hour (realistic)
MIN_SPARKLINE_HOURS = 120   # Need at least 120 of 168 hours for indicator quality


# ---------------------------------------------------------------------------
# Data Fetching — CoinGecko + Fear & Greed
# ---------------------------------------------------------------------------
def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f'{name}.json')


def _cache_is_fresh(name: str, max_age_hours: int = 12) -> bool:
    path = _cache_path(name)
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < max_age_hours * 3600


def _load_cache(name: str) -> Any:
    path = _cache_path(name)
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return None


def _save_cache(name: str, data: Any):
    _ensure_cache_dir()
    with open(_cache_path(name), 'w') as f:
        json.dump(data, f)


def fetch_historical_prices(coin_id: str, days: int = 90) -> List[Tuple[int, float, float]]:
    """
    Fetch hourly prices + volumes from CoinGecko.
    Returns: [(timestamp_ms, price_usd, volume_usd), ...]
    Cached for 12 hours to avoid rate limits.
    """
    from resilient_http import resilient_get

    cache_name = f'prices_{coin_id}_{days}d'
    if _cache_is_fresh(cache_name):
        cached = _load_cache(cache_name)
        if cached:
            return [tuple(x) for x in cached]

    url = f'https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart'
    cg_key = os.getenv('COINGECKO_API_KEY', '')
    headers = {}
    if cg_key:
        headers['x-cg-demo-api-key'] = cg_key

    resp = resilient_get(
        url, params={'vs_currency': 'usd', 'days': str(days)},
        headers=headers, timeout=30, source='coingecko', max_retries=3,
    )
    resp.raise_for_status()
    data = resp.json()

    prices_raw = data.get('prices', [])
    volumes_raw = data.get('total_volumes', [])

    # Align prices and volumes by timestamp
    vol_map = {int(v[0]): float(v[1]) for v in volumes_raw if len(v) >= 2}
    result = []
    for p in prices_raw:
        if len(p) < 2:
            continue
        ts = int(p[0])
        price = float(p[1])
        vol = vol_map.get(ts, 0.0)
        result.append((ts, price, vol))

    _save_cache(cache_name, result)
    logger.info(f"  [HIST] Fetched {len(result)} hourly points for {coin_id} ({days}d)")
    return result


def fetch_historical_market_caps(coin_id: str, days: int = 90) -> Dict[int, float]:
    """Fetch historical market caps. Returns {timestamp_ms: market_cap_usd}."""
    from resilient_http import resilient_get

    cache_name = f'mcap_{coin_id}_{days}d'
    if _cache_is_fresh(cache_name):
        cached = _load_cache(cache_name)
        if cached:
            return {int(k): v for k, v in cached.items()}

    url = f'https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart'
    cg_key = os.getenv('COINGECKO_API_KEY', '')
    headers = {}
    if cg_key:
        headers['x-cg-demo-api-key'] = cg_key

    resp = resilient_get(
        url, params={'vs_currency': 'usd', 'days': str(days)},
        headers=headers, timeout=30, source='coingecko', max_retries=3,
    )
    resp.raise_for_status()
    data = resp.json()

    mcaps = {}
    for m in data.get('market_caps', []):
        if len(m) >= 2 and m[1]:
            mcaps[int(m[0])] = float(m[1])

    _save_cache(cache_name, mcaps)
    return mcaps


def fetch_historical_fear_greed(limit: int = 100) -> Dict[str, int]:
    """
    Fetch historical Fear & Greed index from Alternative.me.
    Returns: {'YYYY-MM-DD': value, ...}
    """
    from resilient_http import resilient_get

    cache_name = f'fear_greed_{limit}d'
    if _cache_is_fresh(cache_name):
        cached = _load_cache(cache_name)
        if cached:
            return cached

    resp = resilient_get(
        f'https://api.alternative.me/fng/?limit={limit}',
        timeout=15, source='alternative_me', max_retries=3,
    )
    resp.raise_for_status()
    data = resp.json().get('data', [])

    result = {}
    for d in data:
        ts = int(d.get('timestamp', 0))
        if ts > 0:
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
            result[date_str] = int(d.get('value', 50))

    _save_cache(cache_name, result)
    logger.info(f"  [HIST] Fetched {len(result)} days of Fear & Greed")
    return result


# ---------------------------------------------------------------------------
# Coin Dict Builder — construct what the pipeline expects
# ---------------------------------------------------------------------------
def _find_closest_idx(timestamps: List[int], target_ts: int) -> int:
    """Binary search for closest timestamp index."""
    lo, hi = 0, len(timestamps) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if timestamps[mid] < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def build_coin_dict(
    coin_id: str,
    symbol: str,
    hourly_data: List[Tuple[int, float, float]],
    mcap_data: Dict[int, float],
    sim_hour_idx: int,
    is_top_3: bool = False,
    is_top_10: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Build a coin dict at a specific hour index, using lookback data for sparkline.
    Returns None if insufficient data.
    """
    if sim_hour_idx < 168:
        return None  # Need 7 days of lookback for sparkline
    if sim_hour_idx >= len(hourly_data):
        return None

    # Current price
    _, current_price, current_vol = hourly_data[sim_hour_idx]
    if current_price <= 0:
        return None

    # 7-day sparkline (168 hourly prices)
    sparkline_start = max(0, sim_hour_idx - 167)
    sparkline = [hourly_data[i][1] for i in range(sparkline_start, sim_hour_idx + 1)]
    if len(sparkline) < MIN_SPARKLINE_HOURS:
        return None

    # Price changes
    def _pct_change(hours_ago: int) -> float:
        idx = sim_hour_idx - hours_ago
        if idx < 0 or idx >= len(hourly_data):
            return 0.0
        old_price = hourly_data[idx][1]
        if old_price <= 0:
            return 0.0
        return ((current_price - old_price) / old_price) * 100

    ch1h = _pct_change(1)
    ch24h = _pct_change(24)
    ch7d = _pct_change(168)
    ch30d = _pct_change(720) if sim_hour_idx >= 720 else _pct_change(sim_hour_idx)

    # Volume: average of last 24 hours
    vol_start = max(0, sim_hour_idx - 23)
    avg_vol = sum(hourly_data[i][2] for i in range(vol_start, sim_hour_idx + 1)) / max(1, sim_hour_idx + 1 - vol_start)

    # Market cap: find closest
    current_ts = hourly_data[sim_hour_idx][0]
    mcap = 0.0
    if mcap_data:
        closest_ts = min(mcap_data.keys(), key=lambda t: abs(t - current_ts), default=0)
        mcap = mcap_data.get(closest_ts, 0.0)

    return {
        'id': coin_id,
        'symbol': symbol,
        'name': symbol,
        'current_price': current_price,
        'total_volume': avg_vol,
        'market_cap': mcap,
        'sparkline_in_7d': {'price': sparkline},
        'price_change_percentage_1h_in_currency': ch1h,
        'price_change_percentage_24h': ch24h,
        'price_change_percentage_24h_in_currency': ch24h,
        'price_change_percentage_7d_in_currency': ch7d,
        'price_change_percentage_30d_in_currency': ch30d,
        '_is_top_3': is_top_3,
        '_is_top_10': is_top_10,
        'enhanced': {},  # No on-chain data for historical replay — honest
    }


# ---------------------------------------------------------------------------
# Trade Simulator — forward simulation with stop/target
# ---------------------------------------------------------------------------
def simulate_trade(
    hourly_data: List[Tuple[int, float, float]],
    entry_idx: int,
    entry_price: float,
    stop_loss_pct: float,
    take_profit_pct: float,
) -> Dict[str, Any]:
    """
    Simulate a trade forward from entry_idx.
    Checks stop-loss and take-profit every hour.
    Applies slippage + fees.

    Returns: {outcome, pnl_pct, pnl_usd_per_unit, exit_reason, hold_hours, exit_price}
    """
    # Entry with slippage
    effective_entry = entry_price * (1 + SLIPPAGE_PCT / 100)
    stop_price = effective_entry * (1 - stop_loss_pct / 100)
    target_price = effective_entry * (1 + take_profit_pct / 100)

    max_idx = min(entry_idx + TRADE_MAX_HOLD_HOURS, len(hourly_data) - 1)
    exit_price = None
    exit_reason = 'time_exit'
    hold_hours = 0

    for i in range(entry_idx + 1, max_idx + 1):
        _, price, _ = hourly_data[i]
        hold_hours = i - entry_idx

        # Check stop-loss first (conservative: stop triggers before target on same candle)
        if price <= stop_price:
            exit_price = stop_price
            exit_reason = 'stop_loss'
            break

        # Check take-profit
        if price >= target_price:
            exit_price = target_price
            exit_reason = 'take_profit'
            break

    # Time exit: use last available price
    if exit_price is None:
        if max_idx > entry_idx:
            exit_price = hourly_data[max_idx][1]
        else:
            exit_price = effective_entry  # No forward data

    # Apply exit slippage + round-trip fees
    effective_exit = exit_price * (1 - SLIPPAGE_PCT / 100)
    gross_pnl_pct = ((effective_exit - effective_entry) / effective_entry) * 100
    net_pnl_pct = gross_pnl_pct - (FEE_PCT * 2)  # Round-trip fees

    return {
        'outcome': 'win' if net_pnl_pct > 0 else 'loss',
        'pnl_pct': round(net_pnl_pct, 4),
        'exit_reason': exit_reason,
        'hold_hours': hold_hours,
        'entry_price': round(effective_entry, 6),
        'exit_price': round(effective_exit, 6),
        'stop_loss_pct': stop_loss_pct,
        'take_profit_pct': take_profit_pct,
        'gross_pnl_pct': round(gross_pnl_pct, 4),
        'fees_pct': round(FEE_PCT * 2, 4),
        'slippage_pct': round(SLIPPAGE_PCT * 2, 4),
    }


# ---------------------------------------------------------------------------
# Attribution Record Builder — optimizer-compatible output
# ---------------------------------------------------------------------------
def build_attribution_record(
    coin_id: str,
    symbol: str,
    sim_timestamp: str,
    quant_output: Dict[str, Any],
    trade_result: Dict[str, Any],
    regime: str,
    fear_greed: int,
    coin_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """Build an attribution record identical to what the optimizer consumes."""
    indicators = coin_dict.get('indicators', {})
    return {
        # Required by optimizer
        'pnl_pct': trade_result['pnl_pct'],
        'pnl_usd': 0.0,  # No dollar amount for backtested trades
        'outcome': trade_result['outcome'],

        # Signal attribution
        'quant_tier': quant_output.get('quant_tier', ''),
        'quant_path': quant_output.get('quant_path', 'default'),
        'regime': regime,
        'specialist_veto': 'CONFIRM',  # No veto in backtest — signal was BUY
        'confirmation_score': quant_output.get('confirmation_score', 0),
        'edge_score': quant_output.get('edge_score', 0),

        # Context fields for counterfactual
        'fear_greed': fear_greed,
        'price_change_24h': coin_dict.get('price_change_percentage_24h', 0),
        'price_change_7d': coin_dict.get('price_change_percentage_7d_in_currency', 0),
        'price_change_30d': coin_dict.get('price_change_percentage_30d_in_currency', 0),
        'funding_rate': 0.0,  # Not available historically — honest

        # Metadata
        'coin_symbol': symbol,
        'coin_id': coin_id,
        'ts': sim_timestamp,
        'exit_time': sim_timestamp,  # Approximate
        'source': 'historical_replay',
        'data_quality_ok_count': quant_output.get('data_quality_ok_count', 0),

        # Trade simulation details
        'exit_reason': trade_result['exit_reason'],
        'hold_hours': trade_result['hold_hours'],
        'entry_price': trade_result['entry_price'],
        'exit_price': trade_result['exit_price'],
        'stop_loss_pct': trade_result['stop_loss_pct'],
        'take_profit_pct': trade_result['take_profit_pct'],
        'score': quant_output.get('score', 0),
        'signal_badges': quant_output.get('signal_badges', []),
    }


# ---------------------------------------------------------------------------
# Missed Opportunity Tracker — forward price for HOLD/SELL signals
# ---------------------------------------------------------------------------
def _track_missed_opportunity(
    missed_list: list,
    coin_id: str,
    symbol: str,
    coin_dict: Dict[str, Any],
    hourly: List[Tuple[int, float, float]],
    coin_idx: int,
    quant: Dict[str, Any],
    regime: str,
    fg_value: int,
    date_str: str,
    signal_type: str = 'HOLD',
) -> None:
    """Track what a HOLD/SELL coin did over the next 7 days."""
    entry_price = coin_dict['current_price']
    if entry_price <= 0 or coin_idx >= len(hourly) - 24:
        return

    # Forward 7d price (or max available)
    fwd_idx = min(coin_idx + MAX_HOLD_HOURS, len(hourly) - 1)
    if fwd_idx <= coin_idx:
        return
    fwd_price = hourly[fwd_idx][1]
    outcome_7d = ((fwd_price - entry_price) / entry_price) * 100

    # Forward max price (best possible exit in 7d window)
    max_price = max(hourly[i][1] for i in range(coin_idx + 1, fwd_idx + 1))
    max_gain_7d = ((max_price - entry_price) / entry_price) * 100

    missed_list.append({
        'coin_id': coin_id,
        'symbol': symbol,
        'date': date_str,
        'signal_type': signal_type,
        'score': quant.get('score', 0),
        'quant_tier': quant.get('quant_tier', 'HOLD'),
        'quant_path': quant.get('quant_path', 'default'),
        'regime': regime,
        'fear_greed': fg_value,
        'entry_price': round(entry_price, 6),
        'outcome_7d': round(outcome_7d, 2),
        'max_gain_7d': round(max_gain_7d, 2),
        'source': 'historical_replay',
    })


# ---------------------------------------------------------------------------
# Main Replay Engine
# ---------------------------------------------------------------------------
def _truncate_coin_data_for_end_offset(
    coin_data: Dict[str, Any],
    coin_mcaps: Dict[str, Dict[int, float]],
    end_offset_days: int,
) -> None:
    """Drop trailing hours so the replay window ends end_offset_days before the data's last timestamp."""
    if end_offset_days <= 0:
        return
    offset_ms = int(timedelta(days=end_offset_days).total_seconds() * 1000)
    for coin_id, info in list(coin_data.items()):
        hourly = info.get('hourly') or []
        if not hourly:
            continue
        last_ts = hourly[-1][0]
        cutoff = last_ts - offset_ms
        info['hourly'] = [p for p in hourly if p[0] <= cutoff]
        mcaps = coin_mcaps.get(coin_id, {})
        if isinstance(mcaps, dict):
            coin_mcaps[coin_id] = {ts: v for ts, v in mcaps.items() if ts <= cutoff}


def run_historical_replay(
    coins: Optional[List[Tuple[str, str]]] = None,
    days: int = 90,
    sim_interval_hours: int = 8,
    min_score_for_trade: int = 0,
    verbose: bool = True,
    threshold_overrides: Optional[Dict[str, Any]] = None,
    end_offset_days: int = 0,
) -> Dict[str, Any]:
    """
    Run the full historical replay.

    Args:
        coins: List of (coin_id, symbol) tuples. Defaults to TOP_COINS.
        days: Days of historical data to fetch (max 90 for hourly on free tier).
        sim_interval_hours: How often to simulate a screening (default: 8h, ~3x daily).
        min_score_for_trade: Minimum quant score to simulate a trade (default: 0, use signal).
        verbose: Log progress.
        threshold_overrides: Dict of threshold overrides for Optuna trials.
            Applied via thread-local context manager — never modifies live_thresholds.json.
        end_offset_days: If > 0, truncate fetched series so the replay ends this many
            calendar days before the last available timestamp (train vs holdout split).

    Returns:
        {
            'trades': [...],           # Attribution records
            'summary': {...},          # Win rate, expectancy, etc.
            'signals_generated': int,  # Total BUY signals
            'coins_screened': int,     # Total screenings
            'simulation_days': int,
        }
    """
    # If threshold overrides provided, run inside context manager
    if threshold_overrides:
        from thresholds import threshold_override
        with threshold_override(threshold_overrides):
            return run_historical_replay(
                coins=coins, days=days, sim_interval_hours=sim_interval_hours,
                min_score_for_trade=min_score_for_trade, verbose=verbose,
                threshold_overrides=None,  # Prevent infinite recursion
                end_offset_days=end_offset_days,
            )

    # Late imports to avoid circular dependencies
    from indicators import calculate_indicators
    from scorer import score_coin
    from quant_signal import generate_quant_signal
    from dynamic_targets import DynamicTargetCalculator
    from market_regime import MarketRegimeDetector

    target_calc = DynamicTargetCalculator()
    regime_detector = MarketRegimeDetector()
    coins = coins or TOP_COINS

    if verbose:
        logger.info(f"[REPLAY] Starting historical replay: {len(coins)} coins, {days} days")

    # -----------------------------------------------------------------------
    # Phase 1: Fetch all data
    # -----------------------------------------------------------------------
    coin_data = {}
    coin_mcaps = {}
    for i, (coin_id, symbol) in enumerate(coins):
        try:
            hourly = fetch_historical_prices(coin_id, days)
            mcaps = fetch_historical_market_caps(coin_id, days)
            if len(hourly) < 168 + TRADE_MAX_HOLD_HOURS:
                logger.warning(f"  [SKIP] {symbol}: only {len(hourly)} hourly points (need {168 + TRADE_MAX_HOLD_HOURS})")
                continue
            # Data quality: filter zero/negative prices and detect gaps
            valid_hourly = [p for p in hourly if isinstance(p, (list, tuple)) and len(p) >= 2 and p[1] and float(p[1]) > 0]
            dropped = len(hourly) - len(valid_hourly)
            if dropped > 0:
                logger.warning(f"  [QUALITY] {symbol}: dropped {dropped} invalid price points (zero/null)")
            if len(valid_hourly) < 168 + TRADE_MAX_HOLD_HOURS:
                logger.warning(f"  [SKIP] {symbol}: only {len(valid_hourly)} valid points after quality filter")
                continue
            # Gap detection: warn if >2h gaps exist (may distort indicators)
            for j in range(1, min(len(valid_hourly), 500)):
                ts_gap = valid_hourly[j][0] - valid_hourly[j - 1][0]
                if ts_gap > 7200_000:  # >2h in ms
                    logger.debug(f"  [GAP] {symbol}: {ts_gap / 3600_000:.1f}h gap at index {j}")
                    break
            hourly = valid_hourly
            coin_data[coin_id] = {'hourly': hourly, 'symbol': symbol}
            coin_mcaps[coin_id] = mcaps
            if verbose:
                logger.info(f"  [HIST] {symbol}: {len(hourly)} hourly points loaded")
        except Exception as e:
            logger.warning(f"  [SKIP] {symbol}: fetch error: {e}")

        # Rate limit: ~10 calls/min for free tier (2 calls per coin: prices + mcaps)
        # Use 8s gap to stay well within limits (7.5 calls/min)
        if i < len(coins) - 1:
            time.sleep(8)

    if end_offset_days > 0:
        _truncate_coin_data_for_end_offset(coin_data, coin_mcaps, end_offset_days)
        for cid in list(coin_data.keys()):
            hourly = coin_data[cid]['hourly']
            sym = coin_data[cid]['symbol']
            if len(hourly) < 168 + TRADE_MAX_HOLD_HOURS:
                logger.warning(
                    f"  [SKIP] {sym}: only {len(hourly)} hourly points after end_offset_days (need {168 + TRADE_MAX_HOLD_HOURS})"
                )
                del coin_data[cid]
                coin_mcaps.pop(cid, None)

    if not coin_data:
        logger.error("[REPLAY] No coin data fetched — aborting")
        return {'trades': [], 'summary': {}, 'signals_generated': 0, 'coins_screened': 0}

    # Fear & Greed
    fg_history = fetch_historical_fear_greed(limit=days + 10)

    # -----------------------------------------------------------------------
    # Phase 2: Find simulation window
    # -----------------------------------------------------------------------
    # We need: 30 days (720h) lookback for ch30d + 7 days (168h) forward for outcome
    # So simulate from hour 720 to len(data) - 168
    btc_data = coin_data.get('bitcoin', {}).get('hourly', [])
    if not btc_data:
        logger.error("[REPLAY] No BTC data — cannot determine regime")
        return {'trades': [], 'summary': {}, 'signals_generated': 0, 'coins_screened': 0}

    min_start = 720  # 30d lookback
    max_end = len(btc_data) - TRADE_MAX_HOLD_HOURS  # need full trade-hold forward data for simulation

    if max_end <= min_start:
        logger.error(f"[REPLAY] Insufficient data: {len(btc_data)} hours (need {min_start + TRADE_MAX_HOLD_HOURS})")
        return {'trades': [], 'summary': {}, 'signals_generated': 0, 'coins_screened': 0}

    if verbose:
        sim_days = (max_end - min_start) // sim_interval_hours
        logger.info(f"[REPLAY] Simulation window: hours {min_start}-{max_end} ({sim_days} screening days)")

    # -----------------------------------------------------------------------
    # Phase 3: Slide through time
    # -----------------------------------------------------------------------
    all_trades = []
    all_signals = []
    missed_opportunities = []
    coins_screened = 0
    skipped_hold = 0
    skipped_sell = 0

    for sim_hour in range(min_start, max_end, sim_interval_hours):
        # Determine date for Fear & Greed lookup
        btc_ts = btc_data[sim_hour][0]
        sim_date = datetime.fromtimestamp(btc_ts / 1000, tz=timezone.utc)
        date_str = sim_date.strftime('%Y-%m-%d')
        fg_value = fg_history.get(date_str, 50)

        # Build BTC coin dict first (needed for regime)
        btc_hourly = coin_data.get('bitcoin', {}).get('hourly', [])
        btc_coin = build_coin_dict(
            'bitcoin', 'BTC', btc_hourly, coin_mcaps.get('bitcoin', {}),
            sim_hour, is_top_3=True, is_top_10=True,
        )
        if not btc_coin:
            continue

        # Build all coin dicts for this hour
        coin_dicts = []
        for coin_id, info in coin_data.items():
            hourly = info['hourly']
            symbol = info['symbol']

            # Map sim_hour from BTC timeline to this coin's timeline
            # (timestamps should be aligned but may have slight offsets)
            target_ts = btc_data[sim_hour][0]
            coin_idx = _find_closest_idx(
                [h[0] for h in hourly], target_ts
            )

            rank = list(coin_data.keys()).index(coin_id)
            cd = build_coin_dict(
                coin_id, symbol, hourly, coin_mcaps.get(coin_id, {}),
                coin_idx,
                is_top_3=(rank < 3),
                is_top_10=(rank < 10),
            )
            if cd:
                coin_dicts.append((coin_id, symbol, cd, coin_idx, hourly))

        if not coin_dicts:
            continue

        # Detect market regime from BTC + all coins
        regime_input = {
            'coins': [cd for _, _, cd, _, _ in coin_dicts],
            'fear_greed': {'value': fg_value},
            'sentiment': {'score': 0.0},
            'macro': {},
        }

        # Run indicators on all coins (calculate_indicators mutates in place)
        calculate_indicators(regime_input)

        # Market-wide stats for scorer
        btc_entry = next((cd for cid, _, cd, _, _ in coin_dicts if cid == 'bitcoin'), None)
        market_24h = btc_entry.get('price_change_percentage_24h', 0) if btc_entry else 0
        market_7d = btc_entry.get('price_change_percentage_7d_in_currency', 0) if btc_entry else 0
        regime_input['market_24h_pct'] = market_24h
        regime_input['market_7d_pct'] = market_7d

        # Detect regime
        try:
            regime_result = regime_detector.detect_regime(regime_input)
        except Exception:
            regime_result = {'regime': 'SIDEWAYS', 'confidence': 50, 'm2_yoy': 0}

        regime = regime_result.get('regime', 'SIDEWAYS')
        regime_data = {
            'regime': regime,
            'structural_regime': regime_result.get('structural_regime', regime),
            'market_24h_pct': market_24h,
            'fear_greed': fg_value,
            'volume_median': 0,  # Not available historically
            'm2_yoy': regime_result.get('m2_yoy', 0),
            # Stage 1c gate: carry characteristics (incl. risk_on) so the quant gate's
            # risk-on/off logic is exercised. The 90d-hourly replay lacks 200d history, so
            # risk_on here is detect_regime's BTC 30d-change>0 proxy (R1: +5.14% OOS) — a
            # conservative stand-in for the live 200d-MA gate.
            'characteristics': regime_result.get('characteristics', {}) or {},
        }

        # Screen each coin
        for coin_id, symbol, coin_dict, coin_idx, hourly in coin_dicts:
            coins_screened += 1

            # Score
            try:
                score_result = score_coin(coin_dict, fg_value, 0.0, market_24h)
                coin_dict['score'] = score_result['score']
                coin_dict['risk'] = score_result.get('risk', {})
                coin_dict['indicators'] = coin_dict.get('indicators', {})
            except Exception as e:
                logger.debug(f"  Score error {symbol}: {e}")
                continue

            # Generate quant signal
            try:
                quant = generate_quant_signal(coin_dict, regime_data, fear_greed_value=fg_value)
            except Exception as e:
                logger.debug(f"  Quant signal error {symbol}: {e}")
                continue

            signal = quant.get('quant_signal', 'HOLD')
            tier = quant.get('quant_tier', 'HOLD')

            # Record all signals for analysis
            all_signals.append({
                'date': date_str,
                'coin': symbol,
                'signal': signal,
                'tier': tier,
                'score': quant.get('score', 0),
                'regime': regime,
            })

            if signal == 'SELL':
                skipped_sell += 1
                # Track forward price for SELL signals too (did we miss a reversal?)
                _track_missed_opportunity(
                    missed_opportunities, coin_id, symbol, coin_dict, hourly, coin_idx,
                    quant, regime, fg_value, date_str, signal_type='SELL',
                )
                continue
            if signal != 'BUY':
                skipped_hold += 1
                # Track forward price for HOLD signals (what did we miss?)
                if quant.get('score', 0) >= 40:  # Only track near-misses, not garbage
                    _track_missed_opportunity(
                        missed_opportunities, coin_id, symbol, coin_dict, hourly, coin_idx,
                        quant, regime, fg_value, date_str, signal_type='HOLD',
                    )
                continue

            # BUY signal — simulate trade
            # Get dynamic stop/target
            entry_price = coin_dict['current_price']
            volatility = coin_dict.get('indicators', {}).get('volatility', 50)
            try:
                targets = target_calc.calculate_targets(
                    entry_price=entry_price,
                    volatility_pct=volatility,
                    regime=regime,
                    consensus_signal=tier,
                )
                stop_pct = targets['stop_loss_pct']
                target_pct = targets['take_profit_pct']
            except Exception:
                stop_pct = 4.0
                target_pct = 12.0

            # Simulate forward
            trade = simulate_trade(
                hourly, coin_idx, entry_price, stop_pct, target_pct,
            )

            # Build attribution record
            sim_ts = sim_date.isoformat().replace('+00:00', 'Z')
            record = build_attribution_record(
                coin_id, symbol, sim_ts, quant, trade,
                regime, fg_value, coin_dict,
            )
            all_trades.append(record)

            if verbose and len(all_trades) % 10 == 0:
                logger.info(f"  [REPLAY] {len(all_trades)} trades simulated so far...")

    # -----------------------------------------------------------------------
    # Phase 4: Analyze and save
    # -----------------------------------------------------------------------
    summary = _compute_summary(all_trades, all_signals, skipped_hold, skipped_sell)

    # Save results
    output = {
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'config': {
            'coins': len(coins),
            'days': days,
            'end_offset_days': end_offset_days,
            'sim_interval_hours': sim_interval_hours,
            'slippage_pct': SLIPPAGE_PCT,
            'fee_pct': FEE_PCT,
            'max_hold_hours': TRADE_MAX_HOLD_HOURS,
            'outcome_label_hours': MAX_HOLD_HOURS,
        },
        'summary': summary,
        'trades': all_trades,
        'signals': all_signals,
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    # Save missed opportunities (sorted by regret — largest missed gains first)
    if missed_opportunities:
        missed_opportunities.sort(key=lambda m: m.get('outcome_7d', 0), reverse=True)
        missed_output = {
            'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'total_missed': len(missed_opportunities),
            'avg_outcome_7d': round(sum(m['outcome_7d'] for m in missed_opportunities) / len(missed_opportunities), 2),
            'top_regrets': missed_opportunities[:50],  # Top 50 by outcome
            'all': missed_opportunities,
        }
        with open(MISSED_OUTPUT_FILE, 'w') as f:
            json.dump(missed_output, f, indent=2, default=str)
        if verbose:
            top_5 = missed_opportunities[:5]
            logger.info(f"  Missed opportunities tracked: {len(missed_opportunities)}")
            for m in top_5:
                logger.info(f"    {m['symbol']} {m['date']}: +{m['outcome_7d']:.1f}% (max +{m['max_gain_7d']:.1f}%) [{m['signal_type']}]")

    if verbose:
        logger.info(f"\n{'='*60}")
        logger.info(f"[REPLAY] COMPLETE")
        logger.info(f"  Coins screened: {coins_screened}")
        logger.info(f"  BUY signals:    {len(all_trades)}")
        logger.info(f"  HOLD skipped:   {skipped_hold}")
        logger.info(f"  SELL skipped:   {skipped_sell}")
        logger.info(f"  Win rate:       {summary.get('win_rate_pct', 0):.1f}%")
        logger.info(f"  Expectancy:     {summary.get('expectancy_pct', 0):.3f}%/trade")
        logger.info(f"  Profit factor:  {summary.get('profit_factor', 0):.2f}")
        logger.info(f"  Saved to:       {OUTPUT_FILE}")
        logger.info(f"{'='*60}")

    return {
        'trades': all_trades,
        'missed_opportunities': missed_opportunities,
        'summary': summary,
        'signals_generated': len(all_trades),
        'coins_screened': coins_screened,
        'simulation_days': (max_end - min_start) // sim_interval_hours,
    }


def _compute_summary(trades: list, signals: list, skipped_hold: int, skipped_sell: int) -> dict:
    """Compute comprehensive summary stats from backtested trades."""
    if not trades:
        return {'win_rate_pct': 0, 'expectancy_pct': 0, 'total_trades': 0}

    wins = [t for t in trades if t['outcome'] == 'win']
    losses = [t for t in trades if t['outcome'] == 'loss']
    n = len(trades)
    n_wins = len(wins)
    n_losses = len(losses)

    win_rate = (n_wins / n * 100) if n > 0 else 0
    avg_win = sum(t['pnl_pct'] for t in wins) / n_wins if n_wins else 0
    avg_loss = sum(t['pnl_pct'] for t in losses) / n_losses if n_losses else 0
    total_pnl = sum(t['pnl_pct'] for t in trades)
    expectancy = total_pnl / n if n > 0 else 0

    gross_wins = sum(t['pnl_pct'] for t in wins)
    gross_losses = abs(sum(t['pnl_pct'] for t in losses))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float('inf')

    # By regime
    by_regime = {}
    for t in trades:
        r = t.get('regime', 'UNKNOWN')
        if r not in by_regime:
            by_regime[r] = {'wins': 0, 'losses': 0, 'total_pnl': 0}
        by_regime[r]['total_pnl'] += t['pnl_pct']
        if t['outcome'] == 'win':
            by_regime[r]['wins'] += 1
        else:
            by_regime[r]['losses'] += 1
    for r, stats in by_regime.items():
        total = stats['wins'] + stats['losses']
        stats['win_rate_pct'] = round(stats['wins'] / total * 100, 1) if total > 0 else 0
        stats['expectancy_pct'] = round(stats['total_pnl'] / total, 3) if total > 0 else 0

    # By tier
    by_tier = {}
    for t in trades:
        tier = t.get('quant_tier', 'UNKNOWN')
        if tier not in by_tier:
            by_tier[tier] = {'wins': 0, 'losses': 0, 'total_pnl': 0}
        by_tier[tier]['total_pnl'] += t['pnl_pct']
        if t['outcome'] == 'win':
            by_tier[tier]['wins'] += 1
        else:
            by_tier[tier]['losses'] += 1
    for tier, stats in by_tier.items():
        total = stats['wins'] + stats['losses']
        stats['win_rate_pct'] = round(stats['wins'] / total * 100, 1) if total > 0 else 0
        stats['expectancy_pct'] = round(stats['total_pnl'] / total, 3) if total > 0 else 0

    # By regime × tier cross-tab (lets optimizer observe STRONG_BUY vs MODERATE_BUY per regime)
    by_regime_and_tier: dict = {}
    for t in trades:
        regime = (t.get('regime') or 'UNKNOWN').upper()
        tier = t.get('quant_tier', 'UNKNOWN')
        key = f"{regime}:{tier}"
        if key not in by_regime_and_tier:
            by_regime_and_tier[key] = {'wins': 0, 'losses': 0, 'total_pnl': 0}
        by_regime_and_tier[key]['total_pnl'] += t['pnl_pct']
        if t['outcome'] == 'win':
            by_regime_and_tier[key]['wins'] += 1
        else:
            by_regime_and_tier[key]['losses'] += 1
    for key, stats in by_regime_and_tier.items():
        total = stats['wins'] + stats['losses']
        stats['win_rate_pct'] = round(stats['wins'] / total * 100, 1) if total > 0 else 0
        stats['expectancy_pct'] = round(stats['total_pnl'] / total, 3) if total > 0 else 0

    # By path
    by_path = {}
    for t in trades:
        path = t.get('quant_path', 'default')
        if path not in by_path:
            by_path[path] = {'wins': 0, 'losses': 0, 'total_pnl': 0}
        by_path[path]['total_pnl'] += t['pnl_pct']
        if t['outcome'] == 'win':
            by_path[path]['wins'] += 1
        else:
            by_path[path]['losses'] += 1
    for path, stats in by_path.items():
        total = stats['wins'] + stats['losses']
        stats['win_rate_pct'] = round(stats['wins'] / total * 100, 1) if total > 0 else 0
        stats['expectancy_pct'] = round(stats['total_pnl'] / total, 3) if total > 0 else 0

    # By exit reason
    by_exit = {}
    for t in trades:
        reason = t.get('exit_reason', 'unknown')
        by_exit[reason] = by_exit.get(reason, 0) + 1

    # Hold time stats
    hold_hours = [t.get('hold_hours', 0) for t in trades]
    avg_hold = sum(hold_hours) / len(hold_hours) if hold_hours else 0

    # Max drawdown (sequential)
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cumulative += t['pnl_pct']
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Signal selectivity
    total_signals = len(signals)
    buy_rate = (len(trades) / total_signals * 100) if total_signals > 0 else 0

    return {
        'total_trades': n,
        'wins': n_wins,
        'losses': n_losses,
        'win_rate_pct': round(win_rate, 1),
        'avg_win_pct': round(avg_win, 3),
        'avg_loss_pct': round(avg_loss, 3),
        'expectancy_pct': round(expectancy, 3),
        'total_pnl_pct': round(total_pnl, 3),
        'profit_factor': round(profit_factor, 2),
        'max_drawdown_pct': round(max_dd, 2),
        'avg_hold_hours': round(avg_hold, 1),
        'by_regime': by_regime,
        'by_tier': by_tier,
        'by_regime_and_tier': by_regime_and_tier,
        'by_path': by_path,
        'by_exit_reason': by_exit,
        'signal_selectivity': {
            'total_screenings': total_signals,
            'buy_signals': len(trades),
            'hold_signals': skipped_hold,
            'sell_signals': skipped_sell,
            'buy_rate_pct': round(buy_rate, 1),
        },
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-7s %(message)s',
        datefmt='%H:%M:%S',
    )
    results = run_historical_replay()
    print(json.dumps(results['summary'], indent=2))
