# tests/test_sell_softening_regime.py — Stress tests for regime/sentiment SELL→HOLD softening
# Ensures we balance regime + technicals correctly and do not miss BUY opportunities.
# Run: pytest tests/test_sell_softening_regime.py -v

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _scored_with_coins(coins):
    """Build scored dict with coins and summary from signals."""
    signals = [c.get('signal', 'HOLD') for c in coins]
    return {
        'coins': coins,
        'summary': {
            'total': len(coins),
            'buy': signals.count('BUY'),
            'hold': signals.count('HOLD'),
            'sell': signals.count('SELL'),
        },
        'fear_greed': {'value': 55},
        'sentiment': {'score': 0.2},
    }


def _coin(signal, rsi=50, momentum=0, id_='c1'):
    return {
        'id': id_,
        'signal': signal,
        'indicators': {'rsi': rsi, 'momentum': momentum},
        'reason': f'Score/RSI {rsi}',
    }


class TestRegimeSentimentSellSoftening:
    """Stress test: apply_regime_sentiment_sell_softening balances regime and technicals."""

    def test_bull_bullish_sentiment_softens_marginal_sell(self):
        """BULL + non-bearish sentiment: marginal SELL (RSI 75, momentum -10) → HOLD."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [
            _coin('BUY', rsi=45, momentum=5, id_='b1'),
            _coin('SELL', rsi=75, momentum=-10, id_='s1'),
        ]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 1
        assert scored['coins'][0]['signal'] == 'BUY'
        assert scored['coins'][1]['signal'] == 'HOLD'
        assert '[SELL→HOLD' in (scored['coins'][1].get('reason') or '')
        assert scored['summary']['sell'] == 0
        assert scored['summary']['hold'] == 1
        assert scored['summary']['buy'] == 1

    def test_bull_extreme_rsi_keeps_sell(self):
        """BULL: extreme overbought (RSI 85) stays SELL — do not hide real danger."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=85, momentum=-5, id_='s1')]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_bull_extreme_momentum_keeps_sell(self):
        """BULL: severe negative momentum (-30) stays SELL."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=72, momentum=-30, id_='s1')]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_bear_regime_no_softening(self):
        """BEAR: no softening — keep all SELLs for capital preservation."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=75, momentum=-10, id_='s1')]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BEAR', 'confidence': 70}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_volatile_regime_no_softening(self):
        """VOLATILE: no softening."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=75, momentum=-10, id_='s1')]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'VOLATILE', 'confidence': 75}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_bull_bearish_sentiment_no_softening(self):
        """BULL but bearish sentiment (low F&G + negative sentiment): do not soften."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=75, momentum=-10, id_='s1')]
        scored = _scored_with_coins(coins)
        scored['fear_greed'] = {'value': 20}
        scored['sentiment'] = {'score': -0.5}
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_sideways_neutral_sentiment_softens(self):
        """SIDEWAYS + neutral sentiment (F&G 40): marginal SELL → HOLD."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=73, momentum=-5, id_='s1')]
        scored = _scored_with_coins(coins)
        scored['fear_greed'] = {'value': 40}
        scored['sentiment'] = {'score': -0.2}
        regime = {'regime': 'SIDEWAYS', 'confidence': 65}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 1
        assert scored['coins'][0]['signal'] == 'HOLD'

    def test_buy_and_hold_never_touched(self):
        """Mission-critical: BUY and HOLD signals are never changed."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [
            _coin('BUY', rsi=40, momentum=10, id_='b1'),
            _coin('HOLD', rsi=55, momentum=0, id_='h1'),
            _coin('SELL', rsi=75, momentum=-10, id_='s1'),
        ]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        apply_regime_sentiment_sell_softening(scored, regime)
        assert scored['coins'][0]['signal'] == 'BUY'
        assert scored['coins'][1]['signal'] == 'HOLD'
        assert scored['coins'][2]['signal'] == 'HOLD'

    def test_summary_totals_consistent_after_softening(self):
        """Summary stays consistent: buy + hold + sell = total."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [
            _coin('BUY', id_='b1'),
            _coin('SELL', rsi=75, momentum=-10, id_='s1'),
            _coin('SELL', rsi=78, momentum=-8, id_='s2'),
        ]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        apply_regime_sentiment_sell_softening(scored, regime)
        s = scored['summary']
        assert s['buy'] + s['hold'] + s['sell'] == s['total']
        assert s['total'] == 3
        assert s['buy'] == 1
        assert s['sell'] == 0
        assert s['hold'] == 2

    def test_boundary_rsi_80_softened(self):
        """Boundary: RSI exactly 80 is softened (<= 80)."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=80, momentum=0, id_='s1')]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 1
        assert scored['coins'][0]['signal'] == 'HOLD'

    def test_boundary_momentum_minus_25_softened(self):
        """Boundary: momentum exactly -25 is softened (>= -25)."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=70, momentum=-25, id_='s1')]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 1
        assert scored['coins'][0]['signal'] == 'HOLD'

    def test_rsi_81_keeps_sell(self):
        """RSI 81 (just above 80) keeps SELL."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=81, momentum=-5, id_='s1')]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_momentum_minus_26_keeps_sell(self):
        """Momentum -26 (just below -25) keeps SELL."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=70, momentum=-26, id_='s1')]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_missing_indicators_treated_as_marginal(self):
        """Missing indicators (rsi/momentum) default to 50/0 → marginal → softened."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [{'id': 's1', 'signal': 'SELL', 'indicators': {}, 'reason': 'Score low'}]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 1
        assert scored['coins'][0]['signal'] == 'HOLD'

    def test_no_softening_when_sentiment_and_fg_both_bearish(self):
        """When both sentiment < -0.3 and F&G < 35, no softening."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=75, momentum=-10, id_='s1')]
        scored = _scored_with_coins(coins)
        scored['fear_greed'] = {'value': 30}
        scored['sentiment'] = {'score': -0.4}
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_softening_with_fg_only(self):
        """F&G >= 35 alone (sentiment missing) still allows softening."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=75, momentum=-10, id_='s1')]
        scored = _scored_with_coins(coins)
        scored['fear_greed'] = {'value': 40}
        scored['sentiment'] = {}
        regime = {'regime': 'BULL', 'confidence': 80}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 1
        assert scored['coins'][0]['signal'] == 'HOLD'


class TestSellSofteningDoesNotAffectBuyOpportunities:
    """Mission: softening must not remove or downgrade BUY signals."""

    def test_many_buys_unchanged_while_sells_softened(self):
        """Mix of BUYs and SELLs: only SELLs are softened; BUY count unchanged."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [
            _coin('BUY', rsi=35, momentum=15, id_='b1'),
            _coin('BUY', rsi=42, momentum=8, id_='b2'),
            _coin('SELL', rsi=76, momentum=-12, id_='s1'),
            _coin('SELL', rsi=74, momentum=-8, id_='s2'),
        ]
        scored = _scored_with_coins(coins)
        regime = {'regime': 'BULL', 'confidence': 80}
        apply_regime_sentiment_sell_softening(scored, regime)
        assert scored['summary']['buy'] == 2
        assert scored['summary']['sell'] == 0
        assert scored['summary']['hold'] == 2
        for c in scored['coins']:
            if c['id'].startswith('b'):
                assert c['signal'] == 'BUY'

    def test_empty_coins_safe(self):
        """Empty coins list: no error, returns 0."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        scored = {'coins': [], 'summary': {'total': 0, 'buy': 0, 'hold': 0, 'sell': 0}, 'fear_greed': {'value': 50}, 'sentiment': {'score': 0}}
        regime = {'regime': 'BULL'}
        n = apply_regime_sentiment_sell_softening(scored, regime)
        assert n == 0
        assert scored['summary']['total'] == 0

    def test_none_regime_data_safe(self):
        """None regime_data: treated as SIDEWAYS, no crash."""
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        coins = [_coin('SELL', rsi=75, momentum=-10, id_='s1')]
        scored = _scored_with_coins(coins)
        n = apply_regime_sentiment_sell_softening(scored, None)
        assert n == 1
        assert scored['coins'][0]['signal'] == 'HOLD'


class TestFullRegimeSentimentTechnicalsMatrix:
    """
    Full permutation matrix: Regime × Sentiment × Technicals.
    Ensures no good BUY opportunities are missed and SELL handling is correct in every combination.
    """

    def _run(self, regime, sentiment_score, fear_greed, coins):
        from regime_sell_softening import apply_regime_sentiment_sell_softening
        scored = _scored_with_coins(coins)
        scored['sentiment'] = {'score': sentiment_score}
        scored['fear_greed'] = {'value': fear_greed}
        return apply_regime_sentiment_sell_softening(scored, {'regime': regime}), scored

    # ---------- REGIME: only BULL and SIDEWAYS can soften ----------
    def test_sideways_bearish_sentiment_no_soften(self):
        """SIDEWAYS + bearish sentiment (F&G<35, score<-0.3): no softening."""
        n, scored = self._run('SIDEWAYS', -0.5, 30, [_coin('SELL', rsi=74, momentum=-8, id_='s1')])
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_bear_with_bullish_sentiment_no_soften(self):
        """BEAR + bullish sentiment: regime wins, no softening (capital preservation)."""
        n, scored = self._run('BEAR', 0.5, 60, [_coin('SELL', rsi=74, momentum=-8, id_='s1')])
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_volatile_with_bullish_sentiment_no_soften(self):
        """VOLATILE + bullish sentiment: no softening."""
        n, scored = self._run('VOLATILE', 0.3, 55, [_coin('SELL', rsi=74, momentum=-8, id_='s1')])
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    def test_unknown_regime_no_soften(self):
        """Unknown/other regime string: not in (BULL, SIDEWAYS) → no soften."""
        n, scored = self._run('UNKNOWN', 0.2, 55, [_coin('SELL', rsi=74, momentum=-8, id_='s1')])
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    # ---------- SENTIMENT BOUNDARIES (aligned = score >= -0.3 OR F&G >= 35) ----------
    def test_sentiment_boundary_score_minus_0_3_fg_low_still_softens(self):
        """Score exactly -0.3 (>= -0.3) with F&G 34: aligned → soften."""
        n, scored = self._run('BULL', -0.3, 34, [_coin('SELL', rsi=75, momentum=-10, id_='s1')])
        assert n == 1
        assert scored['coins'][0]['signal'] == 'HOLD'

    def test_sentiment_boundary_fg_35_score_bearish_still_softens(self):
        """F&G exactly 35 (>= 35) with score -0.5: aligned → soften."""
        n, scored = self._run('BULL', -0.5, 35, [_coin('SELL', rsi=75, momentum=-10, id_='s1')])
        assert n == 1
        assert scored['coins'][0]['signal'] == 'HOLD'

    def test_sentiment_both_just_bearish_no_soften(self):
        """Score -0.31 and F&G 34: both below threshold → no soften."""
        n, scored = self._run('BULL', -0.31, 34, [_coin('SELL', rsi=75, momentum=-10, id_='s1')])
        assert n == 0
        assert scored['coins'][0]['signal'] == 'SELL'

    # ---------- TECHNICALS: both boundaries at once ----------
    def test_technical_both_boundaries_rsi_80_momentum_minus_25_softened(self):
        """SELL with RSI=80 and momentum=-25 (both at boundary): still marginal → softened."""
        n, scored = self._run('BULL', 0.2, 50, [_coin('SELL', rsi=80, momentum=-25, id_='s1')])
        assert n == 1
        assert scored['coins'][0]['signal'] == 'HOLD'

    # ---------- BUY OPPORTUNITIES: never touched in any regime/sentiment ----------
    def test_buy_high_rsi_never_touched_bull(self):
        """BUY with overbought RSI 78 in BULL: never touched (good opportunity preserved)."""
        coins = [_coin('BUY', rsi=78, momentum=15, id_='b1')]
        n, scored = self._run('BULL', 0.2, 55, coins)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'BUY'

    def test_buy_never_touched_bear(self):
        """BUY in BEAR regime: never touched."""
        coins = [_coin('BUY', rsi=40, momentum=5, id_='b1')]
        n, scored = self._run('BEAR', -0.2, 30, coins)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'BUY'

    def test_buy_never_touched_volatile(self):
        """BUY in VOLATILE: never touched."""
        coins = [_coin('BUY', rsi=45, momentum=10, id_='b1')]
        n, scored = self._run('VOLATILE', 0.1, 50, coins)
        assert n == 0
        assert scored['coins'][0]['signal'] == 'BUY'

    # ---------- MIXED BATCH: only marginal SELLs softened ----------
    def test_mixed_one_extreme_one_marginal_only_marginal_softened(self):
        """One SELL RSI 85, one SELL RSI 75: only the marginal one softened."""
        coins = [
            _coin('SELL', rsi=85, momentum=-5, id_='s1'),
            _coin('SELL', rsi=75, momentum=-10, id_='s2'),
        ]
        n, scored = self._run('BULL', 0.2, 55, coins)
        assert n == 1
        assert scored['coins'][0]['signal'] == 'SELL'
        assert scored['coins'][1]['signal'] == 'HOLD'
        assert scored['summary']['sell'] == 1
        assert scored['summary']['hold'] == 1

    def test_mixed_one_extreme_momentum_one_marginal(self):
        """One SELL momentum -30, one SELL momentum -20: only marginal softened."""
        coins = [
            _coin('SELL', rsi=72, momentum=-30, id_='s1'),
            _coin('SELL', rsi=72, momentum=-20, id_='s2'),
        ]
        n, scored = self._run('BULL', 0.2, 55, coins)
        assert n == 1
        assert scored['coins'][0]['signal'] == 'SELL'
        assert scored['coins'][1]['signal'] == 'HOLD'

    # ---------- ALL FOUR REGIMES × marginal SELL (sentiment aligned) ----------
    def test_matrix_bull_aligned_softens(self):
        """BULL + aligned sentiment + marginal SELL → soften."""
        n, _ = self._run('BULL', 0.1, 50, [_coin('SELL', rsi=75, momentum=-10, id_='s1')])
        assert n == 1

    def test_matrix_sideways_aligned_softens(self):
        """SIDEWAYS + aligned sentiment + marginal SELL → soften."""
        n, _ = self._run('SIDEWAYS', 0.0, 40, [_coin('SELL', rsi=75, momentum=-10, id_='s1')])
        assert n == 1

    def test_matrix_bear_aligned_no_soften(self):
        """BEAR + aligned sentiment + marginal SELL → no soften."""
        n, _ = self._run('BEAR', 0.2, 55, [_coin('SELL', rsi=75, momentum=-10, id_='s1')])
        assert n == 0

    def test_matrix_volatile_aligned_no_soften(self):
        """VOLATILE + aligned sentiment + marginal SELL → no soften."""
        n, _ = self._run('VOLATILE', 0.2, 55, [_coin('SELL', rsi=75, momentum=-10, id_='s1')])
        assert n == 0
