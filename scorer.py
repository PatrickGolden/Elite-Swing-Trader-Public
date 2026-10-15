# =============================================================================
# scorer.py — Swing Trader Dashboard
# Multi-factor scoring → BUY / HOLD / SELL signals + dynamic risk parameters
#
# Score breakdown (0–100 total):
#   RSI          25 pts  — oversold/overbought positioning
#   Momentum     25 pts  — multi-timeframe price momentum
#   Volume       15 pts  — turnover ratio (liquidity/interest)
#   Sentiment    20 pts  — Fear & Greed + news sentiment combined
#   Bollinger    15 pts  — band position (near lower = opportunity)
# =============================================================================

import logging

logger = logging.getLogger(__name__)


def _rsi_score(rsi):
    """25 pts — lower RSI = higher score (oversold = buy opportunity)"""
    if rsi < 25:   return 25    # Extreme oversold
    elif rsi < 35: return 22
    elif rsi < 45: return 17
    elif rsi < 55: return 12    # Neutral zone
    elif rsi < 65: return 7
    elif rsi < 75: return 3
    else:          return 0     # Overbought


def _rsi_score_momentum(rsi):
    """25 pts — trend-following: RSI 45-65 is the sweet spot (healthy trend, confirmed strength)."""
    if rsi < 30:   return 5     # Oversold in a trend = potentially weakening
    elif rsi < 40: return 10
    elif rsi < 50: return 16
    elif rsi < 60: return 22    # Healthy uptrend zone
    elif rsi < 65: return 18
    elif rsi < 70: return 12
    elif rsi < 78: return 6     # Getting extended but still ok
    else:          return 0     # Extreme overbought, avoid


def _momentum_score(momentum):
    """25 pts — positive momentum = higher score"""
    if momentum > 20:    return 25
    elif momentum > 12:  return 21
    elif momentum > 6:   return 17
    elif momentum > 2:   return 13
    elif momentum > 0:   return 9
    elif momentum > -5:  return 5
    elif momentum > -15: return 2
    else:                return 0


def _volume_score(volume, market_cap):
    """15 pts — volume/market cap turnover ratio"""
    if not market_cap or market_cap <= 0:
        return 5
    turnover = (volume / market_cap) * 100
    if turnover > 20:   return 15
    elif turnover > 12: return 12
    elif turnover > 6:  return 9
    elif turnover > 3:  return 6
    elif turnover > 1:  return 3
    else:               return 0


def _sentiment_score(fg_value, news_score):
    """
    20 pts — combines Fear & Greed (0-100) + Alpha Vantage news score (-1 to +1)
    Strategy: Contrarian for extremes (buy extreme fear), trend-following in middle
    """
    # Fear & Greed: extreme fear can be a buy opportunity (contrarian)
    # But combined with news, we balance contrarian vs trend signals
    fg_norm   = fg_value / 100.0                          # 0.0–1.0
    news_norm = min(1.0, max(-1.0, news_score * 3.0))     # -1 to +1

    # Weight 60% F&G, 40% news sentiment
    combined = (fg_norm * 0.6) + ((news_norm + 1.0) / 2.0 * 0.4)  # 0–1
    return round(combined * 20)


def _bb_score(bb_pos):
    """15 pts — near lower Bollinger Band = potential buy (mean reversion)"""
    if bb_pos < 10:    return 15   # Well below lower band
    elif bb_pos < 25:  return 13
    elif bb_pos < 40:  return 10
    elif bb_pos < 60:  return 7    # Middle of bands
    elif bb_pos < 75:  return 4
    elif bb_pos < 90:  return 1
    else:              return 0    # Above upper band


def _bb_score_momentum(bb_pos):
    """15 pts — trend-following: price above mean = strength; upper band = extended."""
    if bb_pos < 20:    return 3    # Below mean in a trend = weakening
    elif bb_pos < 40:  return 7
    elif bb_pos < 55:  return 11
    elif bb_pos < 70:  return 14   # Above mean, strong trend
    elif bb_pos < 80:  return 11   # Getting extended
    elif bb_pos < 90:  return 5
    else:              return 0    # Above upper band, overextended


def _macd_bonus(macd_signal):
    """Small bonus/penalty for MACD confirmation"""
    if macd_signal == 'bullish': return 3
    if macd_signal == 'bearish': return -3
    return 0


def _trending_bonus(is_trending: bool) -> int:
    """6 pts — coin is on CoinGecko's trending list (unusual social attention signal)."""
    return 6 if is_trending else 0


def _volume_spike_score(volume_spike_ratio: float) -> int:
    """0-7 pts — 24h volume relative to 7-day rolling average. Spike = unusual buying interest."""
    if volume_spike_ratio >= 4.0:   return 7
    elif volume_spike_ratio >= 3.0: return 6
    elif volume_spike_ratio >= 2.0: return 5
    elif volume_spike_ratio >= 1.5: return 3
    elif volume_spike_ratio >= 1.2: return 1
    else:                           return 0


def _generate_signal(score, rsi, momentum, macd_signal, atr_pct,
                     is_momentum_context=False):
    """
    Determine BUY / HOLD / SELL with multiple confirmation gates.
    A high score alone isn't enough — key indicators must align.
    """
    # Hard SELL conditions (override score)
    # In momentum context, raise the RSI hard-sell threshold because
    # sparkline RSI can stay elevated (70-85) for days in a real trend.
    rsi_hard_sell = 88 if is_momentum_context else 80
    if rsi > rsi_hard_sell:
        return 'SELL', f'RSI extreme overbought (>{rsi_hard_sell})'
    if momentum < -25:
        return 'SELL', 'Severe negative momentum'

    # BUY conditions — score threshold + confirmations
    if score >= 62:
        reasons = []
        if is_momentum_context:
            reasons.append('momentum context (trend-following)')
        if rsi < 50:       reasons.append(f'RSI {rsi} (not overbought)')
        if momentum > 0:   reasons.append('positive momentum')
        if macd_signal == 'bullish': reasons.append('MACD bullish')

        # Need at least 1 confirmation besides score
        if reasons:
            return 'BUY', ' | '.join(reasons) if reasons else 'multi-factor score'

    # SELL conditions
    if score <= 28 or (rsi > 72 and momentum < 0):
        return 'SELL', f'Score {score}, RSI {rsi}'

    return 'HOLD', f'Score {score} — await stronger signal'


def score_coin(coin, fg_value, news_score, market_24h_pct=None, is_trending=False, volume_spike_ratio=1.0):
    """
    Score a single coin across all factors.
    market_24h_pct: BTC (market) 24h move for bull participation / outperforming bonuses.
    Returns enriched dict with signal, score, breakdown, and risk params.
    """
    ind      = coin.get('indicators', {})
    if not ind:
        logger.warning("[scorer] No indicators for %s — scoring with defaults (RSI=50, momentum=0)",
                       coin.get('name', coin.get('id', '?')))
    rsi      = ind.get('rsi',       50.0)
    bb_pos   = ind.get('bb_pos',    50.0)
    atr_pct  = ind.get('atr_pct',   2.0)
    momentum = ind.get('momentum',   0.0)
    macd_sig = ind.get('macd',  'neutral')

    volume     = coin.get('total_volume',  0) or 0
    market_cap = coin.get('market_cap',    1) or 1

    # Extract price changes once for entire scoring function
    from coin_utils import get_price_changes
    pc = get_price_changes(coin)

    # Momentum context: when strong multi-TF momentum with acceleration,
    # use trend-following RSI/BB scoring instead of mean-reversion.
    # Gate uses price changes and acceleration (not RSI — sparkline RSI
    # can be artificially high for trending coins and would block this).
    mom_accel = ind.get('momentum_accel') or {}
    early_mom = float(ind.get('early_momentum', 0) or 0)
    is_momentum_context = (
        (momentum > 5 or early_mom > 3)
        and mom_accel.get('is_accelerating', False)
        and pc['ch24h'] > 0
        and pc['ch7d'] > 0
        and pc['ch24h'] < 15  # not a pump
    )

    # --- Score each factor ---
    s_rsi       = _rsi_score_momentum(rsi) if is_momentum_context else _rsi_score(rsi)
    s_mom       = _momentum_score(momentum)
    s_vol       = _volume_score(volume, market_cap)
    s_sent      = _sentiment_score(fg_value, news_score)
    s_bb        = _bb_score_momentum(bb_pos) if is_momentum_context else _bb_score(bb_pos)
    bonus       = _macd_bonus(macd_sig)
    s_trending  = _trending_bonus(bool(is_trending))
    s_vol_spike = _volume_spike_score(float(volume_spike_ratio) if volume_spike_ratio else 1.0)

    total = max(0, min(100, s_rsi + s_mom + s_vol + s_sent + s_bb + bonus + s_trending + s_vol_spike))
    ch1h, ch24, ch7d, ch30d = pc['ch1h'], pc['ch24h'], pc['ch7d'], pc['ch30d']
    # Rally participation: when momentum and 24h are strongly positive, add small bonus so breakouts can pass BEAR/VOLATILE quant gate (see docs/RALLY_PARTICIPATION_ASSESSMENT.md)
    if momentum > 15 and ch24 > 5.0:
        total = min(100, total + 4)
    # Blood in the street: only reward dips when there is fear (sniper — buy when others are fearful)
    if fg_value < 40 and ch24 < -5.0:
        total = min(100, total + 5)   # strongest bonus: fear + dip = asymmetric opportunity
    # Short-term: stabilizing dip = opportunity only when F&G not greedy (sniper)
    if ch24 < -5.0 and ch1h > -4.0 and ch1h > ch24 and fg_value < 55:
        total = min(100, total + 3)   # stabilizing dip bonus
    if ch24 > 12.0:
        total = max(0, total - 2)     # short-term overbought penalty
    # 1d/1w/1m: trend alignment (24h, 7d, 30d same sign) = bonus; extended overbought (7d and 30d both high) = penalty
    if ch24 > 0 and ch7d > 0 and ch30d > 0:
        total = min(100, total + 2)   # 1d/1w/1m trend aligned
    if ch24 < 0 and ch7d < 0 and ch30d < 0 and fg_value < 50:
        total = min(100, total + 1)   # 1d/1w/1m downtrend + fear (capitulation context, sniper)
    if ch7d > 25.0 and ch30d > 30.0:
        total = max(0, total - 2)     # extended overbought (1w + 1m)
    # Pullback-in-trend: only when some fear (sniper — not buying pullback in euphoria)
    if ch7d > 0 and ch30d > 0 and ch24 < 0 and ch24 > -8.0 and fg_value < 55:
        total = min(100, total + 2)
    # Resilience: 24h down but RSI holding — only when fear (sniper)
    if ch24 < 0 and rsi > 38 and fg_value < 52:
        total = min(100, total + 2)
    # Bull market: tokens contributing to the move — outperform market (relative strength) and momentum patterns
    if ch24 > 0 and ch7d > 0 and ch30d > 0:
        if momentum > 10:
            total = min(100, total + 2)   # strong trend participation (bull participant)
        if market_24h_pct is not None and ch24 > float(market_24h_pct):
            total = min(100, total + 2)   # outperforming BTC 24h (leading the rally)
        if ch7d and ch24 > (float(ch7d) / 4.0):
            total = min(100, total + 1)   # momentum acceleration (24h pace stronger than weekly)

    # --- Signal ---
    signal, reason = _generate_signal(total, rsi, momentum, macd_sig, atr_pct,
                                      is_momentum_context=is_momentum_context)

    # --- Dynamic Risk Parameters (ATR-based) ---
    # Stop loss: 1.0× ATR (minimum 1.5%, maximum 8%)
    stop_pct   = round(max(1.5, min(8.0, atr_pct * 1.0)), 2)
    target_pct = round(stop_pct * 3.0, 2)   # 3:1 R:R
    rr_ratio   = 3.0

    # Swing hypothesis text
    if signal == 'BUY':
        hypothesis = (
            f"Momentum={momentum:+.1f}% | RSI={rsi} (not overbought) | "
            f"Score={total}/100 | Stop={stop_pct}% | Target={target_pct}%"
        )
    elif signal == 'SELL':
        hypothesis = f"RSI={rsi} | Momentum={momentum:+.1f}% | Score={total}/100 — reduce/exit"
    else:
        hypothesis = f"Score={total}/100 — monitor for breakout above resistance"

    return {
        'signal':     signal,
        'score':      total,
        'hypothesis': hypothesis,
        'reason':     reason,
        'breakdown': {
            'rsi':          s_rsi,
            'momentum':     s_mom,
            'volume':       s_vol,
            'sentiment':    s_sent,
            'bb':           s_bb,
            'trending':     s_trending,
            'volume_spike': s_vol_spike,
        },
        'risk': {
            'stop_loss_pct':  stop_pct,
            'target_pct':     target_pct,
            'rr_ratio':       rr_ratio,
            'position_size':  '<1% of capital',
            'dynamic_stop':   f'{stop_pct}% (1×ATR)',
        },
    }


def score_tokens(data):
    """Score all 50 coins and attach summary stats to the data object."""
    fg_value   = data.get('fear_greed', {}).get('value', 50)
    news_score = data.get('sentiment',  {}).get('score',  0.0)

    coins = data.get('coins', [])
    market_24h_pct = data.get('market_24h_pct')
    trending_ids = {str(cid).lower() for cid in (data.get('trending_coin_ids') or [])}
    for coin in coins:
        is_trending = coin.get('id', '').lower() in trending_ids
        vol_spike = float(coin.get('volume_spike_ratio') or 1.0)
        result = score_coin(
            coin, fg_value, news_score,
            market_24h_pct=market_24h_pct,
            is_trending=is_trending,
            volume_spike_ratio=vol_spike,
        )
        coin.update({
            'signal':     result['signal'],
            'score':      result['score'],
            'hypothesis': result['hypothesis'],
            'reason':     result['reason'],
            'breakdown':  result['breakdown'],
            'risk':       result['risk'],
        })

    # Sort: BUY first (by score desc), then HOLD, then SELL
    order = {'BUY': 0, 'HOLD': 1, 'SELL': 2}
    data['coins'] = sorted(
        coins,
        key=lambda c: (order.get(c.get('signal', 'HOLD'), 1), -c.get('score', 0))
    )

    # Summary counts
    signals = [c.get('signal', 'HOLD') for c in coins]
    data['summary'] = {
        'total': len(coins),
        'buy':   signals.count('BUY'),
        'hold':  signals.count('HOLD'),
        'sell':  signals.count('SELL'),
    }

    return data
