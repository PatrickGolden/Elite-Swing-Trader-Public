# =============================================================================
# dynamic_targets.py — Volatility-Adjusted Stop Loss & Take Profit Calculator
# Replaces fixed percentages with smart, adaptive targets
#
# FEATURES:
#   - Volatility-based stop loss width
#   - Regime-aware adjustments (BULL/BEAR/SIDEWAYS/VOLATILE)
#   - Confidence-level scaling
#   - Risk:Reward ratio optimization
#   - Safety bounds (min/max limits)
#   - Detailed reasoning for each calculation
#
# INPUTS (units): entry_price (USD), volatility_pct (annualized %, e.g. 50–150 from
#   indicators.volatility), regime ('BULL'|'BEAR'|'SIDEWAYS'|'VOLATILE'),
#   consensus_signal ('ULTRA_BUY'|'STRONG_BUY'|'MODERATE_BUY'|'WEAK_BUY').
# ORDER OF OPERATIONS: raw stop → bounds [min_stop_pct, max_stop_pct] → raw target →
#   bounds [min_target_pct, max_target_pct] → prices.
#   raw_stop = base × vol_mult × regime_mult × conf_mult;
#   raw_target = stop_loss_pct × rr_ratio;
#   stop_loss_price = entry × (1 - stop_pct/100), take_profit_price = entry × (1 + target_pct/100).
# =============================================================================

import math


def get_regime_stop_floor(regime: str, volatility_pct=None) -> float:
    """
    Return the regime-aware minimum stop-loss floor percentage.

    Single source of truth used by initial stop calculation (calculate_targets),
    breakeven stops, and any other layer that must respect regime noise-floors.
    Never let a stop be tighter than this value for the given regime.
    """
    try:
        from thresholds import get_threshold
        bull_floor = float(get_threshold('BULL_STOP_FLOOR_PCT', 5.0))
        hb_threshold = float(get_threshold('DYNAMIC_STOP_HIGH_BETA_VOL_THRESHOLD_PCT', 85.0))
        hb_floor = float(get_threshold('DYNAMIC_STOP_HIGH_BETA_FLOOR_PCT', 4.5))
    except Exception:
        bull_floor, hb_threshold, hb_floor = 5.0, 85.0, 4.5

    floors = {
        'BULL': bull_floor,
        'SIDEWAYS': 3.5,
        'BEAR': 5.0,
        'VOLATILE': 5.0,
    }
    floor = floors.get(regime, 3.0)

    # High-beta coins in trending regimes need a wider floor to avoid trend-clip
    if (volatility_pct is not None
            and float(volatility_pct) >= hb_threshold
            and regime in ('BULL', 'SIDEWAYS')):
        floor = max(floor, hb_floor)

    return floor


class DynamicTargetCalculator:
    """
    Calculates optimal stop loss and take profit levels based on:
    - Coin volatility (ATR-based)
    - Market regime
    - Signal confidence
    - Risk:Reward ratios
    """
    
    def __init__(self, 
                 base_stop_pct=2.5,
                 min_stop_pct=1.5,
                 max_stop_pct=15.0,
                 min_target_pct=3.0,
                 max_target_pct=50.0):
        """
        Initialize dynamic target calculator.
        
        Args:
            base_stop_pct: Base stop loss percentage (default: 2.5%)
            min_stop_pct: Minimum stop loss allowed (default: 1.5%)
            max_stop_pct: Maximum stop loss allowed (default: 15.0%)
            min_target_pct: Minimum take profit (default: 3.0%)
            max_target_pct: Maximum take profit (default: 50.0%)
        """
        self.base_stop_pct = base_stop_pct
        self.min_stop_pct = min_stop_pct
        self.max_stop_pct = max_stop_pct
        self.min_target_pct = min_target_pct
        self.max_target_pct = max_target_pct
    
    
    # =========================================================================
    # VOLATILITY MULTIPLIERS
    # =========================================================================
    
    def get_volatility_multiplier(self, volatility_pct):
        """
        Calculate stop loss multiplier based on coin volatility.
        
        Low volatility coins (BTC, ETH) get tighter stops.
        High volatility coins (small caps) get wider stops.
        
        Args:
            volatility_pct: Annualized volatility percentage (e.g., 75.0)
        
        Returns:
            float: Multiplier (0.7 to 2.5)
        """
        if volatility_pct is None:
            return 1.0  # Default if no volatility data
        
        # Volatility bands
        if volatility_pct < 30:
            # Very low volatility (stable coins, BTC in calm markets)
            return 0.7
        elif volatility_pct < 45:
            # Low volatility (BTC, ETH typically)
            return 0.85
        elif volatility_pct < 60:
            # Medium-low volatility (large caps)
            return 1.0
        elif volatility_pct < 80:
            # Medium volatility (most alts)
            return 1.2
        elif volatility_pct < 100:
            # High volatility (mid caps)
            return 1.5
        elif volatility_pct < 150:
            # Very high volatility (small caps)
            return 2.0
        else:
            # Extreme volatility (micro caps, new tokens)
            return 2.5
    
    
    # =========================================================================
    # REGIME MULTIPLIERS
    # =========================================================================
    
    def get_regime_multiplier(self, regime):
        """
        Calculate stop loss multiplier based on market regime.
        
        BULL markets: Tighter stops (cleaner trends)
        BEAR/VOLATILE: Wider stops (more chop)
        
        Args:
            regime: Market regime string ('BULL', 'BEAR', 'SIDEWAYS', 'VOLATILE')
        
        Returns:
            float: Multiplier (1.0 to 1.5)
        """
        regime_map = {
            'BULL': 1.1,      # Momentum entries need room for retracements
            'SIDEWAYS': 1.0,  # Ranging = normal stops
            'BEAR': 1.3,      # Choppy downtrends = wider stops
            'VOLATILE': 1.5,  # Extreme volatility = much wider stops
        }
        return regime_map.get(regime, 1.0)
    
    
    # =========================================================================
    # CONFIDENCE MULTIPLIERS
    # =========================================================================
    
    def get_confidence_multiplier(self, consensus_signal):
        """
        Calculate stop loss multiplier based on signal confidence.
        
        High confidence = tighter stops (we're more certain)
        Low confidence = wider stops (need more room for uncertainty)
        
        Args:
            consensus_signal: Signal tier ('ULTRA_BUY', 'STRONG_BUY', etc.)
        
        Returns:
            float: Multiplier (0.9 to 1.2)
        """
        confidence_map = {
            'ULTRA_BUY': 0.9,      # Very high confidence = tight stops
            'STRONG_BUY': 1.0,     # High confidence = normal stops
            'MODERATE_BUY': 1.1,   # Medium confidence = slightly wider
            'WEAK_BUY': 1.2,       # Low confidence = wider stops
        }
        return confidence_map.get(consensus_signal, 1.0)
    
    
    # =========================================================================
    # RISK:REWARD RATIOS
    # =========================================================================
    
    def get_risk_reward_ratio(self, regime, consensus_signal):
        """
        Determine appropriate Risk:Reward ratio based on regime and confidence.
        
        BULL/SIDEWAYS/BEAR: R:R 2.0 (take profits on consistent moves)
        VOLATILE: Higher R:R (2.5:1)
        
        Args:
            regime: Market regime
            consensus_signal: Signal confidence tier
        
        Returns:
            float: Risk:Reward ratio (e.g., 2.5 means target is 2.5x the stop)
        """
        # Base R:R by regime
        regime_rr = {
            'BULL': 2.0,      # Bull produces consistent smaller gains — take them
            'SIDEWAYS': 2.0,  # Mean reversion = take profits faster
            'BEAR': 2.0,      # Choppy = take what you can get
            'VOLATILE': 2.5,  # High risk = need high reward
        }
        
        base_rr = regime_rr.get(regime, 2.5)
        
        # Adjust for confidence
        if consensus_signal == 'ULTRA_BUY':
            return base_rr * 1.1  # Push for bigger targets on best setups
        elif consensus_signal == 'WEAK_BUY':
            return base_rr * 0.9  # Take profits faster on weak signals
        
        return base_rr
    
    
    # =========================================================================
    # MAIN CALCULATION
    # =========================================================================
    
    def calculate_targets(self, 
                         entry_price,
                         volatility_pct,
                         regime,
                         consensus_signal):
        """
        Calculate dynamic stop loss and take profit levels.
        
        Args:
            entry_price: Coin entry price (e.g., 45000)
            volatility_pct: Coin volatility (e.g., 75.0)
            regime: Market regime ('BULL', 'BEAR', etc.)
            consensus_signal: Signal tier ('ULTRA_BUY', etc.)
        
        Returns:
            dict: {
                'stop_loss_pct': float,
                'stop_loss_price': float,
                'take_profit_pct': float,
                'take_profit_price': float,
                'risk_reward_ratio': float,
                'reasoning': {
                    'volatility_mult': float,
                    'regime_mult': float,
                    'confidence_mult': float,
                    'base_stop': float,
                    'final_stop': float,
                    'bounded_stop': float
                }
            }
        """
        # LEAN EXIT (Stage 1c): when lean mode is on, override with a WIDE FIXED stop,
        # effectively-no take-profit, and NO trailing. Research showed the tight ATR
        # stops over-stopped trend trades; a wide fixed stop matches the no-stop edge
        # while structurally capping each loss. (exit_manager disables trailing/stale-21
        # under the same flag.) Applies to every entry because lean changes the whole regime.
        try:
            from thresholds import get_threshold as _gt
            if float(_gt('LEAN_ENTRY_MODE', 0.0) or 0.0) >= 1.0:
                stop_pct = float(_gt('LEAN_FIXED_STOP_PCT', 24.0))
                tp_pct = float(_gt('LEAN_TAKE_PROFIT_PCT', 150.0))
                return {
                    'stop_loss_pct': round(stop_pct, 2),
                    'stop_loss_price': round(entry_price * (1 - stop_pct / 100), 2),
                    'take_profit_pct': round(tp_pct, 2),
                    'take_profit_price': round(entry_price * (1 + tp_pct / 100), 2),
                    'trailing_stop_pct': None,
                    'trailing_stop_price': None,
                    'trailing_trigger_pct': None,
                    'risk_reward_ratio': round(tp_pct / stop_pct, 2) if stop_pct else 0.0,
                    'reasoning': {
                        'lean_exit': True, 'stop_loss_pct': stop_pct, 'take_profit_pct': tp_pct,
                        'regime': regime,
                        'note': 'LEAN_ENTRY_MODE: wide fixed stop, no TP, no trailing',
                    },
                }
        except Exception:
            pass

        # Get multipliers
        vol_mult = self.get_volatility_multiplier(volatility_pct)
        regime_mult = self.get_regime_multiplier(regime)
        conf_mult = self.get_confidence_multiplier(consensus_signal)
        
        # Calculate raw stop loss
        raw_stop_pct = self.base_stop_pct * vol_mult * regime_mult * conf_mult
        
        # Apply bounds
        stop_loss_pct = max(self.min_stop_pct, min(self.max_stop_pct, raw_stop_pct))

        # Regime-based minimum floor — single source of truth via module-level function
        regime_floor = get_regime_stop_floor(regime, volatility_pct)

        # High-beta flag for reasoning dict (re-derive from thresholds for transparency)
        try:
            from thresholds import get_threshold
            high_beta_vol_threshold = float(get_threshold('DYNAMIC_STOP_HIGH_BETA_VOL_THRESHOLD_PCT', 85.0))
        except Exception:
            high_beta_vol_threshold = 85.0
        high_beta_floor_applied = (
            volatility_pct is not None
            and float(volatility_pct) >= high_beta_vol_threshold
            and regime in ('BULL', 'SIDEWAYS')
        )

        stop_loss_pct = max(stop_loss_pct, regime_floor)

        # Calculate take profit using R:R ratio
        rr_ratio = self.get_risk_reward_ratio(regime, consensus_signal)
        raw_target_pct = stop_loss_pct * rr_ratio
        
        # Apply bounds to target
        take_profit_pct = max(self.min_target_pct, min(self.max_target_pct, raw_target_pct))
        
        # Calculate actual price levels
        stop_loss_price = entry_price * (1 - stop_loss_pct / 100)
        take_profit_price = entry_price * (1 + take_profit_pct / 100)
        
        # Calculate actual R:R achieved (after bounding)
        actual_rr = (take_profit_pct / stop_loss_pct) if stop_loss_pct > 0 else 3.0
        
        # Trailing stop levels (informational for UI/alerts). Phase 1: derive from the SAME
        # thresholds execution uses (exit_manager.py / app.py) so the displayed trail/activation
        # are never stale relative to live behavior. Previously hardcoded 5%/3% — misleading.
        try:
            from thresholds import get_threshold
            trail_activate_pct = float(get_threshold('TRAILING_STOP_MIN_PROFIT_PCT', 8))
            trail_width_pct = float(get_threshold('TRAILING_STOP_TRAIL_PCT', 13))
        except Exception:
            trail_activate_pct, trail_width_pct = 8.0, 13.0
        trailing_stop_pct = trail_width_pct
        trailing_stop_price = entry_price * (1 - trailing_stop_pct / 100)
        trailing_trigger_pct = trail_activate_pct

        return {
            'stop_loss_pct': round(stop_loss_pct, 2),
            'stop_loss_price': round(stop_loss_price, 2),
            'take_profit_pct': round(take_profit_pct, 2),
            'take_profit_price': round(take_profit_price, 2),
            'trailing_stop_pct': round(trailing_stop_pct, 2) if trailing_stop_pct else None,
            'trailing_stop_price': round(trailing_stop_price, 2) if trailing_stop_price else None,
            'trailing_trigger_pct': round(trailing_trigger_pct, 2) if trailing_trigger_pct else None,
            'risk_reward_ratio': round(actual_rr, 2),
            'reasoning': {
                'volatility_pct': volatility_pct,
                'volatility_mult': round(vol_mult, 2),
                'regime': regime,
                'regime_mult': round(regime_mult, 2),
                'confidence': consensus_signal,
                'confidence_mult': round(conf_mult, 2),
                'base_stop_pct': self.base_stop_pct,
                'raw_stop_pct': round(raw_stop_pct, 2),
                'bounded_stop_pct': stop_loss_pct,
                'regime_floor_pct': regime_floor,
                'high_beta_floor_applied': high_beta_floor_applied,
                'high_beta_vol_threshold_pct': high_beta_vol_threshold,
                'raw_target_pct': round(raw_target_pct, 2),
                'bounded_target_pct': take_profit_pct,
                'bounded_reason': self._get_bounding_reason(raw_stop_pct, stop_loss_pct, 
                                                            raw_target_pct, take_profit_pct)
            }
        }
    
    
    def _get_bounding_reason(self, raw_stop, bounded_stop, raw_target, bounded_target):
        """Generate explanation if bounds were applied"""
        reasons = []
        
        if raw_stop < self.min_stop_pct:
            reasons.append(f"Stop widened from {raw_stop:.2f}% to minimum {self.min_stop_pct}%")
        elif raw_stop > self.max_stop_pct:
            reasons.append(f"Stop capped from {raw_stop:.2f}% to maximum {self.max_stop_pct}%")
        
        if raw_target < self.min_target_pct:
            reasons.append(f"Target raised from {raw_target:.2f}% to minimum {self.min_target_pct}%")
        elif raw_target > self.max_target_pct:
            reasons.append(f"Target capped from {raw_target:.2f}% to maximum {self.max_target_pct}%")
        
        return reasons if reasons else ["No bounds applied"]
    
    
    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================
    
    def get_stop_for_position(self, coin_data, regime_data, consensus_signal):
        """
        Calculate stop loss for a specific coin and market conditions.
        Convenience wrapper for calculate_targets().
        
        Args:
            coin_data: Dict with coin info (must have 'current_price', 'indicators')
            regime_data: Dict with regime info (must have 'regime')
            consensus_signal: Signal tier string
        
        Returns:
            dict: Same as calculate_targets()
        """
        entry_price = coin_data.get('current_price', 0)
        volatility = coin_data.get('indicators', {}).get('volatility', 50)
        regime = regime_data.get('regime', 'SIDEWAYS')
        
        return self.calculate_targets(
            entry_price=entry_price,
            volatility_pct=volatility,
            regime=regime,
            consensus_signal=consensus_signal
        )
    
    
    def format_reasoning(self, targets):
        """
        Format the reasoning into a human-readable explanation.
        
        Args:
            targets: Result from calculate_targets()
        
        Returns:
            str: Formatted explanation
        """
        r = targets['reasoning']
        
        explanation = f"""
DYNAMIC TARGET CALCULATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Base Stop Loss: {r['base_stop_pct']}%

Adjustments Applied:
  • Volatility ({r['volatility_pct']:.1f}%): ×{r['volatility_mult']} multiplier
  • Market Regime ({r['regime']}): ×{r['regime_mult']} multiplier
  • Signal Confidence ({r['confidence']}): ×{r['confidence_mult']} multiplier

Calculation:
  {r['base_stop_pct']}% × {r['volatility_mult']} × {r['regime_mult']} × {r['confidence_mult']} = {r['raw_stop_pct']}%

Final Stop Loss: {targets['stop_loss_pct']}% (${targets['stop_loss_price']:,.2f})
Final Take Profit: {targets['take_profit_pct']}% (${targets['take_profit_price']:,.2f})
Risk:Reward Ratio: 1:{targets['risk_reward_ratio']}

Bounds Applied: {', '.join(r['bounded_reason'])}
        """.strip()
        
        return explanation


# =============================================================================
# EXAMPLES & TESTING
# =============================================================================

if __name__ == '__main__':
    calculator = DynamicTargetCalculator()
    
    print("=" * 70)
    print("DYNAMIC TARGETS - EXAMPLE CALCULATIONS")
    print("=" * 70)
    
    # Example 1: Bitcoin - ULTRA_BUY - BULL Market
    print("\n📊 EXAMPLE 1: Bitcoin (Low Volatility)")
    print("-" * 70)
    btc_targets = calculator.calculate_targets(
        entry_price=45000,
        volatility_pct=35,
        regime='BULL',
        consensus_signal='ULTRA_BUY'
    )
    print(calculator.format_reasoning(btc_targets))
    
    # Example 2: Small Cap - MODERATE_BUY - VOLATILE Market
    print("\n📊 EXAMPLE 2: Small Cap Altcoin (High Volatility)")
    print("-" * 70)
    alt_targets = calculator.calculate_targets(
        entry_price=2.50,
        volatility_pct=125,
        regime='VOLATILE',
        consensus_signal='MODERATE_BUY'
    )
    print(calculator.format_reasoning(alt_targets))
    
    # Example 3: Ethereum - STRONG_BUY - BEAR Market
    print("\n📊 EXAMPLE 3: Ethereum (Medium Volatility, Bear Market)")
    print("-" * 70)
    eth_targets = calculator.calculate_targets(
        entry_price=2800,
        volatility_pct=55,
        regime='BEAR',
        consensus_signal='STRONG_BUY'
    )
    print(calculator.format_reasoning(eth_targets))
    
    print("\n" + "=" * 70)
    print("KEY INSIGHTS:")
    print("=" * 70)
    print("• BTC (low vol): Tight 2.0% stop, 6.0% target")
    print("• Small cap (high vol): Wide 9.9% stop, 24.8% target")
    print("• ETH in bear: Moderate 3.7% stop, 7.4% target")
    print("\nAll targets adapt to actual market conditions! ✅")
    print("=" * 70)
