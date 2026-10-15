# =============================================================================
# market_regime.py — Market Regime Detection & Strategy Adjustment
# Classifies market into: Bull / Bear / Sideways / Volatile
# Two-layer system: structural (30d/7d) + tactical (1h/24h) overlay
# Adjusts AI voting weights and position sizing based on regime
# =============================================================================

import logging
import numpy as np
from datetime import datetime

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """
    Detects current market regime and adjusts trading strategy accordingly.

    REGIMES:
    - BULL: Strong uptrend, low volatility, greed dominant
    - BEAR: Strong downtrend, high volatility, fear dominant
    - SIDEWAYS: Range-bound, medium volatility, neutral sentiment
    - VOLATILE: High volatility regardless of direction, uncertain

    LAYERS:
    - Structural: 30d/7d macro backdrop (existing point system)
    - Tactical: 1h/24h short-term reality check (can adjust up or down)
      - Downgrades: easier to trigger (capital protection)
      - Upgrades: harder to trigger, max one level (opportunity capture)

    STRATEGY ADJUSTMENTS:
    - Bull: More aggressive, accept MODERATE_BUY signals
    - Bear: Conservative, require STRONG_BUY + extra confirmation
    - Sideways: Range trading, focus on mean reversion
    - Volatile: Reduce position sizes, widen stops
    """

    def __init__(self):
        self.current_regime = 'UNKNOWN'
        self.confidence = 0.0
        self.last_check = None

    def detect_regime(self, data):
        """
        Analyze market data and classify regime.

        Parameters:
        - data: dict with coins, fear_greed, macro, sentiment

        Returns:
        {
            'regime': 'BULL' | 'BEAR' | 'SIDEWAYS' | 'VOLATILE',
            'structural_regime': same as above (before tactical overlay),
            'confidence': 0-100,
            'characteristics': {...},
            'strategy_adjustment': {...},
        }
        """

        # Extract BTC data (market leader)
        coins = data.get('coins', [])
        btc = next((c for c in coins if c.get('id') in ('bitcoin', 'btc') or c.get('symbol') == 'BTC'), None)

        if not btc:
            logger.warning("[market_regime] BTC data not found in coins list (%d coins) — falling back to UNKNOWN regime",
                           len(coins))
            return self._fallback_regime()

        # Get key metrics
        btc_change_7d = btc.get('price_change_percentage_7d_in_currency', 0) or 0
        btc_change_30d = btc.get('price_change_percentage_30d_in_currency', 0) or 0

        btc_indicators = btc.get('indicators', {})
        btc_volatility = btc_indicators.get('volatility', 50) or 50
        btc_rsi = btc_indicators.get('rsi', 50) or 50

        fear_greed_value = data.get('fear_greed', {}).get('value', 50)

        # Calculate altcoin momentum (how many are up vs down)
        alt_slice = coins[1:20]
        alt_count = max(1, len(alt_slice))
        altcoin_gains = sum(1 for c in alt_slice if (c.get('price_change_percentage_7d_in_currency') or 0) > 0)
        altcoin_momentum = altcoin_gains / float(alt_count)  # 0 to 1

        # =====================================================================
        # STRUCTURAL REGIME CLASSIFICATION
        # Score axes: trend (BTC 7d/30d), volatility, sentiment (fear_greed),
        # RSI, altcoin (participation). Each axis adds points to BULL/BEAR/
        # SIDEWAYS/VOLATILE. Regime = argmax(scores); confidence = min(100,
        # scores[regime]).
        # =====================================================================

        scores = {
            'BULL': 0,
            'BEAR': 0,
            'SIDEWAYS': 0,
            'VOLATILE': 0,
        }

        # --- TREND ANALYSIS ---
        if btc_change_30d > 10 and btc_change_7d > 0:
            scores['BULL'] += 30
        elif btc_change_30d < -10 and btc_change_7d < 0:
            scores['BEAR'] += 30
        elif abs(btc_change_30d) < 5:
            scores['SIDEWAYS'] += 30

        # --- VOLATILITY ANALYSIS ---
        if btc_volatility > 80:
            scores['VOLATILE'] += 25
        elif btc_volatility < 40:
            scores['BULL'] += 15  # Low vol = stability
            scores['SIDEWAYS'] += 10
        else:
            scores['SIDEWAYS'] += 15

        # --- SENTIMENT ANALYSIS ---
        if fear_greed_value > 70:  # Greed
            scores['BULL'] += 20
        elif fear_greed_value < 30:  # Fear
            scores['BEAR'] += 20
        else:  # Neutral
            scores['SIDEWAYS'] += 15

        # --- RSI ANALYSIS ---
        if btc_rsi > 60:
            scores['BULL'] += 10
        elif btc_rsi < 40:
            scores['BEAR'] += 10
        else:
            scores['SIDEWAYS'] += 10

        # --- ALTCOIN PARTICIPATION ---
        if altcoin_momentum > 0.7:  # 70% of alts up
            scores['BULL'] += 15
        elif altcoin_momentum < 0.3:  # 70% of alts down
            scores['BEAR'] += 15
        else:
            scores['SIDEWAYS'] += 10

        # --- VOLATILITY OVERRIDE ---
        if btc_volatility > 100:
            scores['VOLATILE'] += 30  # Extra weight to volatile

        # --- CYCLE EXTENSION (Bittel/Pal) — macro ---
        if (data.get('macro', {}).get('fed_rate', {}).get('value') or 0) < 4.5:
            scores['BULL'] += 8
            scores['SIDEWAYS'] += 5

        # M2 LIQUIDITY INTEGRATION — liquidity expansion tilts regime toward BULL/SIDEWAYS
        macro = data.get('macro', {})
        m2_yoy = macro.get('m2', {}).get('value', 0) or 0
        if m2_yoy > 5.0:
            scores['BULL'] += 10
            scores['SIDEWAYS'] += 6

        # =====================================================================
        # DETERMINE STRUCTURAL WINNER
        # =====================================================================

        structural_regime = max(scores, key=scores.get)

        # =====================================================================
        # TACTICAL OVERLAY — short-term divergence check
        # =====================================================================

        regime, tactical_info = self._apply_tactical_overlay(structural_regime, coins)
        confidence = min(100, scores[structural_regime])

        # =====================================================================
        # CHARACTERISTICS
        # =====================================================================

        characteristics = {
            'btc_trend_30d': f"{btc_change_30d:+.1f}%",
            'btc_trend_7d': f"{btc_change_7d:+.1f}%",
            'volatility': btc_volatility,
            'fear_greed': fear_greed_value,
            'rsi': btc_rsi,
            'altcoin_participation': f"{altcoin_momentum * 100:.0f}%",
            'm2_yoy': round(m2_yoy, 2),  # M2 LIQUIDITY INTEGRATION
        }

        # --- Stage 1b: market-trend (risk-on/off) master gate ---
        # Prefer the BTC>200d-MA computation injected by the caller (network lives in
        # risk_regime.py, keeping this detector network-free + testable). Fail-closed
        # fallback: derive from BTC 30d-change only when no 200d-MA value is supplied.
        risk_info = data.get('btc_risk_on')
        if not isinstance(risk_info, dict):
            risk_info = {
                'risk_on': bool(btc_change_30d > 0),
                'btc_vs_ma200_pct': None,
                'btc_30d_change': round(float(btc_change_30d), 2),
                'source': 'regime_30d_fallback',
            }
        characteristics['risk_on'] = bool(risk_info.get('risk_on', False))
        characteristics['risk_regime'] = 'RISK_ON' if characteristics['risk_on'] else 'RISK_OFF'
        characteristics['btc_vs_ma200_pct'] = risk_info.get('btc_vs_ma200_pct')
        characteristics['risk_on_source'] = risk_info.get('source')

        # Tactical overlay info (always present for dashboard)
        if tactical_info:
            characteristics['tactical_override'] = True
            characteristics['structural_regime'] = tactical_info['structural_regime']
            characteristics['tactical_reason'] = tactical_info['reason']
            characteristics['alt_breadth_24h_pct'] = tactical_info['alt_breadth_24h_pct']
            characteristics['btc_24h'] = tactical_info['btc_24h']
            characteristics['btc_1h'] = tactical_info['btc_1h']
        else:
            characteristics['tactical_override'] = False

        # =====================================================================
        # STRATEGY ADJUSTMENTS (based on final resolved regime)
        # =====================================================================

        if regime == 'BULL':
            strategy = {
                'position_size_multiplier': self._get_bull_size_mult(),  # Normalized until BULL WR improves
                'required_confidence': 'Medium',  # Accept MODERATE_BUY
                'stop_loss_multiplier': 0.9,  # Tighter stops
                'take_profit_multiplier': 1.2,  # Higher targets
                'ai_agreement_required': 2,  # 2 of 3 AIs
                'recommendation': 'Aggressive stance - ride the trend',
            }

        elif regime == 'BEAR':
            strategy = {
                'position_size_multiplier': 0.6,  # Smaller positions
                'required_confidence': 'Very High',  # Only STRONG_BUY
                'stop_loss_multiplier': 1.3,  # Wider stops (volatility)
                'take_profit_multiplier': 0.8,  # Lower targets
                'ai_agreement_required': 3,  # All 3 AIs must agree
                'recommendation': 'Conservative - capital preservation priority',
            }

        elif regime == 'SIDEWAYS':
            strategy = {
                'position_size_multiplier': 1.0,  # Normal sizing
                'required_confidence': 'High',  # STRONG_BUY preferred
                'stop_loss_multiplier': 1.0,  # Normal stops
                'take_profit_multiplier': 0.9,  # Slightly lower targets
                'ai_agreement_required': 3,  # All 3 AIs
                'recommendation': 'Mean reversion - buy dips, sell rips',
            }

        else:  # VOLATILE
            strategy = {
                'position_size_multiplier': 0.5,  # Much smaller
                'required_confidence': 'Very High',  # Only best setups
                'stop_loss_multiplier': 1.5,  # Much wider stops
                'take_profit_multiplier': 1.5,  # Higher targets to compensate risk
                'ai_agreement_required': 3,  # All 3 AIs must agree
                'recommendation': 'High risk environment - trade cautiously',
                'contraction_breakout': bool(btc_volatility > 70),  # High vol for vol_contraction path in quant
            }

        # Sentiment-aware label: in BULL/SIDEWAYS, if sentiment is bearish we don't soften
        # SELLs — reflect that in the strategy text so it matches behavior.
        if regime in ('BULL', 'SIDEWAYS'):
            sentiment_score = float((data.get('sentiment') or {}).get('score', 0) or 0)
            fear_greed = float((data.get('fear_greed') or {}).get('value', 50) or 50)
            if sentiment_score < -0.3 and fear_greed < 35:
                strategy['recommendation'] = strategy['recommendation'] + ' (sentiment cautious - SELLs not softened)'

        # Tactical override label
        if tactical_info:
            strategy['recommendation'] = (
                f"[tactical: {tactical_info['structural_regime']}→{regime}] "
                + strategy['recommendation']
            )

        # =====================================================================
        # STORE AND RETURN
        # =====================================================================

        self.current_regime = regime
        self.confidence = confidence
        self.last_check = datetime.now()

        return {
            'regime': regime,
            'structural_regime': structural_regime,
            'confidence': confidence,
            'characteristics': characteristics,
            'strategy_adjustment': strategy,
            'scores': scores,  # For debugging
            'timestamp': self.last_check.strftime('%Y-%m-%d %H:%M:%S'),
            'm2_yoy': round(m2_yoy, 2),  # M2 LIQUIDITY INTEGRATION — for quant_signal
        }

    def _apply_tactical_overlay(self, structural_regime, coins):
        """
        Short-term divergence check on 1h/24h data.

        Downgrades: BULL/SIDEWAYS can be downgraded when short-term action diverges.
        Upgrades: BEAR/SIDEWAYS can be upgraded (one level max) on strong rallies.
        VOLATILE/UNKNOWN pass through unchanged.

        Returns (final_regime, tactical_info_dict_or_None).
        """
        if structural_regime in ('VOLATILE', 'UNKNOWN'):
            return structural_regime, None

        try:
            from thresholds import get_threshold as _t
        except ImportError:
            return structural_regime, None

        # --- Extract short-term BTC data ---
        btc = next((c for c in coins if c.get('id') in ('bitcoin', 'btc') or c.get('symbol') == 'BTC'), None)
        if not btc:
            return structural_regime, None

        btc_24h = float(btc.get('price_change_percentage_24h_in_currency')
                        or btc.get('price_change_percentage_24h') or 0)
        btc_1h = float(btc.get('price_change_percentage_1h_in_currency') or 0)
        btc_7d = float(btc.get('price_change_percentage_7d_in_currency') or 0)
        btc_rsi = float((btc.get('indicators') or {}).get('rsi', 50) or 50)

        # --- Altcoin 24h breadth (fraction of top-19 alts with positive 24h change) ---
        alt_slice = coins[1:20]
        alt_count = max(1, len(alt_slice))
        alt_up_24h = sum(
            1 for c in alt_slice
            if float(c.get('price_change_percentage_24h_in_currency')
                     or c.get('price_change_percentage_24h') or 0) > 0
        )
        alt_breadth_pct = (alt_up_24h / alt_count) * 100

        # --- Load thresholds ---
        severe_24h = float(_t('TACTICAL_SEVERE_24H_PCT', -4.0))
        severe_alt = float(_t('TACTICAL_SEVERE_ALT_BREADTH_PCT', 25.0))
        moderate_24h = float(_t('TACTICAL_MODERATE_24H_PCT', -2.0))
        moderate_7d = float(_t('TACTICAL_MODERATE_7D_PCT', -5.0))
        moderate_rsi = float(_t('TACTICAL_MODERATE_RSI', 35.0))
        severe_1h = float(_t('TACTICAL_SEVERE_1H_PCT', -3.0))
        upgrade_24h = float(_t('TACTICAL_UPGRADE_24H_PCT', 5.0))
        upgrade_alt = float(_t('TACTICAL_UPGRADE_ALT_BREADTH_PCT', 75.0))
        upgrade_rsi = float(_t('TACTICAL_UPGRADE_RSI', 55.0))

        downgrade = None
        reason = None

        # =================================================================
        # DOWNGRADE PATH (BULL or SIDEWAYS structural)
        # =================================================================
        if structural_regime in ('BULL', 'SIDEWAYS'):

            # Acute 1h crash (fast-trigger, both BULL and SIDEWAYS)
            if btc_1h <= severe_1h and alt_breadth_pct < severe_alt:
                downgrade = 'BEAR'
                reason = f'acute_crash: 1h={btc_1h:+.1f}%, alt_breadth={alt_breadth_pct:.0f}%'

            elif structural_regime == 'BULL':
                if btc_24h <= severe_24h and alt_breadth_pct < severe_alt:
                    downgrade = 'BEAR'
                    reason = f'severe_divergence: 24h={btc_24h:+.1f}%, alt_breadth={alt_breadth_pct:.0f}%'
                elif btc_24h <= moderate_24h and btc_7d <= moderate_7d:
                    downgrade = 'SIDEWAYS'
                    reason = f'moderate_divergence: 24h={btc_24h:+.1f}%, 7d={btc_7d:+.1f}%'

            elif structural_regime == 'SIDEWAYS':
                if btc_24h <= severe_24h and alt_breadth_pct < severe_alt:
                    downgrade = 'BEAR'
                    reason = f'severe_divergence: 24h={btc_24h:+.1f}%, alt_breadth={alt_breadth_pct:.0f}%'
                elif btc_24h <= moderate_24h and btc_rsi < moderate_rsi:
                    downgrade = 'VOLATILE'
                    reason = f'moderate_divergence: 24h={btc_24h:+.1f}%, rsi={btc_rsi:.0f}'

        # =================================================================
        # UPGRADE PATH (BEAR or SIDEWAYS structural)
        # Higher bar than downgrades — must meet ALL three conditions
        # Max one level: BEAR→SIDEWAYS, SIDEWAYS→BULL (never BEAR→BULL)
        # =================================================================
        if downgrade is None and structural_regime in ('BEAR', 'SIDEWAYS'):
            if (btc_24h >= upgrade_24h
                    and alt_breadth_pct > upgrade_alt
                    and btc_rsi > upgrade_rsi):
                if structural_regime == 'BEAR':
                    downgrade = 'SIDEWAYS'  # one level up
                    reason = f'rally_upgrade: 24h={btc_24h:+.1f}%, alt_breadth={alt_breadth_pct:.0f}%, rsi={btc_rsi:.0f}'
                else:  # SIDEWAYS → BULL
                    downgrade = 'BULL'
                    reason = f'rally_upgrade: 24h={btc_24h:+.1f}%, alt_breadth={alt_breadth_pct:.0f}%, rsi={btc_rsi:.0f}'

        if downgrade is None:
            return structural_regime, None

        tactical_info = {
            'structural_regime': structural_regime,
            'tactical_regime': downgrade,
            'reason': reason,
            'btc_1h': round(btc_1h, 2),
            'btc_24h': round(btc_24h, 2),
            'btc_7d': round(btc_7d, 2),
            'btc_rsi': round(btc_rsi, 1),
            'alt_breadth_24h_pct': round(alt_breadth_pct, 1),
        }
        logger.info("[market_regime] Tactical override: %s → %s (%s)",
                    structural_regime, downgrade, reason)
        return downgrade, tactical_info

    def _get_bull_size_mult(self):
        """Get BULL position size multiplier from live thresholds (default 1.0)."""
        try:
            from thresholds import get_threshold
            return get_threshold('BULL_POSITION_SIZE_MULT', 1.0)
        except Exception:
            return 1.0

    def _fallback_regime(self):
        """Return safe defaults if BTC data unavailable"""
        logger.warning("[market_regime] Using fallback UNKNOWN regime — all trading will use conservative settings")
        return {
            'regime': 'UNKNOWN',
            'structural_regime': 'UNKNOWN',
            'confidence': 0,
            # Fail-closed: no BTC data => not risk-on (suppresses fear-dip buys + halves momentum size)
            'characteristics': {'risk_on': False, 'risk_regime': 'RISK_OFF', 'risk_on_source': 'fallback'},
            'strategy_adjustment': {
                'position_size_multiplier': 0.5,
                'required_confidence': 'Very High',
                'stop_loss_multiplier': 1.5,
                'take_profit_multiplier': 1.0,
                'ai_agreement_required': 3,
                'recommendation': 'Insufficient data - trade cautiously',
            },
            'scores': {},
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

    def get_regime_summary(self):
        """Quick summary for display"""
        return {
            'current_regime': self.current_regime,
            'confidence': self.confidence,
            'last_check': self.last_check.strftime('%H:%M:%S') if self.last_check else 'Never',
        }
