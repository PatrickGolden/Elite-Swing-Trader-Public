# =============================================================================
# indicators.py — Swing Trader Dashboard
# Calculates technical indicators from price sparkline (168 hourly prices; CoinGecko sparkline, CoinCap fallback)
# =============================================================================

import numpy as np


# -----------------------------------------------------------------------------
# RSI — Relative Strength Index
# <30 = oversold (potential BUY), >70 = overbought (potential SELL)
# -----------------------------------------------------------------------------
def calculate_rsi(prices, period=14):
    if not prices or len(prices) < period + 1:
        return 50.0  # neutral default

    prices = np.array(prices, dtype=float)
    deltas = np.diff(prices)

    gains  = np.where(deltas > 0,  deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Seed with simple averages
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    # Smooth with Wilder's method
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs  = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(float(rsi), 2)


# FULL EXPERT INTEGRATION: BCBacker / Pal / Bittel / TXMC / Credible
# -----------------------------------------------------------------------------
# Weekly RSI — approximate from hourly sparkline by resampling to daily closes
# then applying standard RSI. Used for deep-cycle oversold gates.
# -----------------------------------------------------------------------------
def calculate_weekly_rsi(prices, period=14):
    if not prices:
        return 50.0

    arr = np.array(prices, dtype=float)
    hours_per_day = 24
    if arr.size < hours_per_day * (period + 1):
        # Not enough history to compute a stable weekly-style RSI; stay neutral.
        return 50.0

    days = arr.size // hours_per_day
    trimmed = arr[: days * hours_per_day]
    # Use daily closes (last hour of each day) as resampled series
    daily_closes = trimmed[hours_per_day - 1 :: hours_per_day]
    return calculate_rsi(daily_closes.tolist(), period=period)


# -----------------------------------------------------------------------------
# Bollinger Band Position
# 0 = at/below lower band (oversold), 100 = at/above upper band (overbought)
# -----------------------------------------------------------------------------
def calculate_bb_position(prices, period=20, std_dev=2.0):
    if not prices or len(prices) < period:
        return 50.0

    arr     = np.array(prices[-period:], dtype=float)
    sma     = float(np.mean(arr))
    std     = float(np.std(arr))

    if std == 0:
        return 50.0

    upper   = sma + std_dev * std
    lower   = sma - std_dev * std
    current = float(arr[-1])

    if upper == lower:
        return 50.0

    pos = (current - lower) / (upper - lower) * 100.0
    return round(float(np.clip(pos, 0, 100)), 2)


# -----------------------------------------------------------------------------
# ATR Estimate — Average True Range as % of current price
# Used for dynamic stop-loss sizing
# -----------------------------------------------------------------------------
def calculate_atr_pct(prices, period=14):
    if not prices or len(prices) < period + 1:
        return 2.0  # default 2%

    arr    = np.array(prices[-(period + 1):], dtype=float)
    ranges = np.abs(np.diff(arr))
    atr    = float(np.mean(ranges))

    if arr[-1] == 0:
        return 2.0

    atr_pct = (atr / arr[-1]) * 100.0
    return round(float(atr_pct), 4)


# -----------------------------------------------------------------------------
# Annualised Volatility (from hourly prices → annualised)
# -----------------------------------------------------------------------------
def calculate_volatility(prices):
    if not prices or len(prices) < 2:
        return 0.0

    arr     = np.array(prices, dtype=float)
    returns = np.diff(arr) / arr[:-1]
    returns = returns[np.isfinite(returns)]   # remove inf/nan

    if len(returns) == 0:
        return 0.0

    hourly_vol  = float(np.std(returns))
    annual_vol  = hourly_vol * np.sqrt(8760)  # 8760 hours/year
    return round(annual_vol * 100.0, 2)


# -----------------------------------------------------------------------------
# MACD Signal (simplified — uses price EMA crossover from sparkline)
# Returns: 'bullish', 'bearish', 'neutral'
# -----------------------------------------------------------------------------
def calculate_macd_signal(prices):
    if not prices or len(prices) < 26:
        return 'neutral'

    def ema(data, span):
        arr = np.array(data, dtype=float)
        k = 2.0 / (span + 1)
        e = arr[0]
        for v in arr[1:]:
            e = v * k + e * (1 - k)
        return e

    ema12 = ema(prices, 12)
    ema26 = ema(prices, 26)
    macd  = ema12 - ema26

    # Compare current MACD to a few periods ago
    if len(prices) >= 30:
        ema12_prev = ema(prices[:-4], 12)
        ema26_prev = ema(prices[:-4], 26)
        macd_prev  = ema12_prev - ema26_prev

        if macd > 0 and macd > macd_prev:
            return 'bullish'
        elif macd < 0 and macd < macd_prev:
            return 'bearish'

    return 'neutral'


# -----------------------------------------------------------------------------
# Weighted Momentum Score
# Combines 1h / 24h / 7d / 30d changes into a single momentum number
# -----------------------------------------------------------------------------
def calculate_momentum(c1h, c24h, c7d, c30d):
    w1h, w24h, w7d, w30d = 0.05, 0.25, 0.45, 0.25
    c1h  = c1h  or 0.0
    c24h = c24h or 0.0
    c7d  = c7d  or 0.0
    c30d = c30d or 0.0
    score = w1h * c1h + w24h * c24h + w7d * c7d + w30d * c30d
    return round(float(score), 3)


# -----------------------------------------------------------------------------
# Early Momentum Score — weights shorter timeframes heavily for fast detection
# Fires hours into a move instead of days.
# -----------------------------------------------------------------------------
def calculate_early_momentum(c1h, c24h, c7d, c30d):
    c1h  = c1h  or 0.0
    c24h = c24h or 0.0
    c7d  = c7d  or 0.0
    c30d = c30d or 0.0
    score = 0.20 * c1h + 0.40 * c24h + 0.30 * c7d + 0.10 * c30d
    return round(float(score), 3)


# -----------------------------------------------------------------------------
# Momentum Acceleration — detects if price movement is speeding up.
# Annualizes each timeframe to a daily rate and compares short vs long.
# Positive acceleration = the move is getting stronger, not fading.
# -----------------------------------------------------------------------------
def calculate_momentum_acceleration(c1h, c24h, c7d, c30d):
    c1h  = float(c1h  or 0)
    c24h = float(c24h or 0)
    c7d  = float(c7d  or 0)
    c30d = float(c30d or 0)

    daily_from_1h  = c1h * 24.0
    daily_from_24h = c24h
    daily_from_7d  = c7d / 7.0
    daily_from_30d = c30d / 30.0

    short_accel  = daily_from_1h  - daily_from_24h
    medium_accel = daily_from_24h - daily_from_7d
    long_accel   = daily_from_7d  - daily_from_30d

    is_accelerating = (short_accel > 0 and medium_accel > 0)

    return {
        'short_accel':     round(short_accel, 3),
        'medium_accel':    round(medium_accel, 3),
        'long_accel':      round(long_accel, 3),
        'is_accelerating': is_accelerating,
    }


# -----------------------------------------------------------------------------
# Momentum Quality — Sharpe-like ratio: return / volatility.
# Smooth uptrends score high; choppy pumps score low.
# Institutional factor: low-vol momentum is more persistent (Moskowitz 2012).
# -----------------------------------------------------------------------------
def calculate_momentum_quality(prices, c7d):
    c7d = float(c7d or 0)
    if not prices or len(prices) < 48 or c7d <= 0:
        return 0.0

    arr = np.array(prices[-168:], dtype=float)
    returns = np.diff(arr) / arr[:-1]
    returns = returns[np.isfinite(returns)]
    if len(returns) < 24:
        return 0.0

    vol = float(np.std(returns))
    if vol == 0:
        return 0.0

    daily_vol = vol * np.sqrt(24)
    weekly_return = c7d / 100.0
    quality = weekly_return / (daily_vol * 7.0)
    normalized = min(100.0, max(0.0, quality * 40.0))
    return round(float(normalized), 2)


# -----------------------------------------------------------------------------
# Relative Strength — how much a coin outperforms the broader market.
# Cross-sectional momentum: the key Citadel/Renaissance factor.
# -----------------------------------------------------------------------------
def calculate_relative_strength(ch24h, ch7d, market_24h_pct, market_7d_pct):
    ch24h = float(ch24h or 0)
    ch7d  = float(ch7d or 0)
    m24   = float(market_24h_pct or 0)
    m7    = float(market_7d_pct or 0)

    rs_24h = ch24h - m24
    rs_7d  = ch7d  - m7

    strong = (rs_24h > 0 and rs_7d > 2.0)

    return {
        'rs_24h': round(rs_24h, 3),
        'rs_7d':  round(rs_7d, 3),
        'strong': strong,
    }


# -----------------------------------------------------------------------------
# Main entry — attach all indicators to each coin
# -----------------------------------------------------------------------------
def calculate_indicators(data):
    coins = data.get('coins', [])
    market_24h_pct = float(data.get('market_24h_pct') or 0)
    market_7d_pct  = float(data.get('market_7d_pct') or 0)

    for coin in coins:
        sparkline = coin.get('sparkline_in_7d') or {}
        prices    = sparkline.get('price') or []

        rsi        = calculate_rsi(prices)
        weekly_rsi = calculate_weekly_rsi(prices)
        bb_pos     = calculate_bb_position(prices)
        atr_pct    = calculate_atr_pct(prices)
        vol        = calculate_volatility(prices)
        macd_sig   = calculate_macd_signal(prices)
        # 24h fallback: API may expose only price_change_percentage_24h_in_currency
        c1h = coin.get('price_change_percentage_1h_in_currency')
        c24h = coin.get('price_change_percentage_24h') or coin.get('price_change_percentage_24h_in_currency')
        c7d = coin.get('price_change_percentage_7d_in_currency')
        c30d = coin.get('price_change_percentage_30d_in_currency')

        momentum = calculate_momentum(c1h, c24h, c7d, c30d)
        early_momentum = calculate_early_momentum(c1h, c24h, c7d, c30d)
        momentum_accel = calculate_momentum_acceleration(c1h, c24h, c7d, c30d)
        momentum_quality = calculate_momentum_quality(prices, c7d)
        relative_strength = calculate_relative_strength(
            c24h, c7d, market_24h_pct, market_7d_pct,
        )

        # Multi-timeframe context: 1h, 1d (24h), 1w (7d), ~1m (30d) for overlay (scorer, quant, veto)
        ch1h = float(c1h or 0)
        ch24h_f = float(c24h or 0)
        ch7d = float(c7d or 0)
        ch30d = float(c30d or 0)

        coin['indicators'] = {
            'rsi':               rsi,
            'weekly_rsi':        weekly_rsi,
            'bb_pos':            bb_pos,
            'atr_pct':           atr_pct,
            'volatility':        vol,
            'macd':              macd_sig,
            'momentum':          momentum,
            'early_momentum':    early_momentum,
            'momentum_accel':    momentum_accel,
            'momentum_quality':  momentum_quality,
            'relative_strength': relative_strength,
            'ch1h':              ch1h,
            'ch24h':             ch24h_f,
            'ch7d':              ch7d,
            'ch30d':             ch30d,
        }

    data['coins'] = coins
    return data
