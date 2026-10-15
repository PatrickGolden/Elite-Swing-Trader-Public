# regime_sell_softening.py — Regime/sentiment-aware SELL→HOLD softening (no app deps)
# Used after scoring: in BULL/SIDEWAYS with non-bearish sentiment, marginal SELLs become HOLD
# so the dashboard summary is not dominated by SELLs. Extreme SELLs (RSI > 80 or momentum < -25) stay.


def apply_regime_sentiment_sell_softening(scored, regime_data):
    """
    In BULL/SIDEWAYS with non-bearish sentiment, soften marginal SELL → HOLD so the summary
    isn't dominated by SELLs (per-coin technical SELL can contradict regime/sentiment).
    Keeps SELL when extreme (RSI > 80 or momentum < -25). Mutates scored and its summary.
    Returns number of coins softened (for logging). Never touches BUY or HOLD.
    """
    regime = (regime_data or {}).get('regime', 'SIDEWAYS')
    sentiment_score = float((scored.get('sentiment') or {}).get('score', 0) or 0)
    fear_greed = float((scored.get('fear_greed') or {}).get('value', 50) or 50)
    if regime not in ('BULL', 'SIDEWAYS'):
        return 0
    if sentiment_score < -0.3 and fear_greed < 35:
        return 0
    softened = 0
    for coin in scored.get('coins', []):
        if coin.get('signal') != 'SELL':
            continue
        ind = coin.get('indicators') or {}
        rsi = float(ind.get('rsi', 50) or 50)
        momentum = float(ind.get('momentum', 0) or 0)
        if rsi <= 80 and momentum >= -25:
            coin['signal'] = 'HOLD'
            coin['reason'] = (coin.get('reason') or '') + ' [SELL→HOLD: bull/sentiment]'
            softened += 1
    if softened:
        signals = [c.get('signal', 'HOLD') for c in scored['coins']]
        scored['summary'] = {
            'total': len(scored['coins']),
            'buy': signals.count('BUY'),
            'hold': signals.count('HOLD'),
            'sell': signals.count('SELL'),
        }
    return softened
