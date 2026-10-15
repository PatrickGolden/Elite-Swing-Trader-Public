# =============================================================================
# position_sizer.py — Risk-Based Position Sizing Calculator
# Calculates optimal position size based on:
#   - Account size
#   - Maximum risk per trade (% of capital)
#   - Volatility (ATR)
#   - AI confidence level
#   - Market regime
#   - Stop loss distance
#
# ORDER OF CAPS (calculate_for_consensus):
#   1. Base size = max_risk_usd / (stop_loss_pct/100)
#   2. Confidence × regime × volatility
#   3. Cap at 80% of account (never all-in)
#   4. Cap at cash_available if provided
#   5. Kelly cap: min(position, 5% of account)
#   6. Liquidity cap: min(position, max_cash_usage_pct % of cash_available)
# All inputs: entry_price (USD), stop_loss_pct (e.g. 2.5 for 2.5%), coin_volatility (annualized %).
# =============================================================================

import logging

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Professional position sizing for risk management.
    
    RULES:
    1. Never risk more than max_risk_pct per trade (default 2%)
    2. Adjust size based on confidence (STRONG = larger, WEAK = smaller)
    3. Adjust size based on regime (BULL = larger, BEAR/VOLATILE = smaller)
    4. Account for volatility (high vol = smaller size)
    """
    
    KELLY_FRACTION = 0.25   # 25% Kelly (conservative)
    KELLY_CAP_PCT = 5.0     # Hard cap: max 5% of account per position from Kelly

    def __init__(self, account_size=0, max_risk_pct=2.0, max_cash_usage_pct=40.0):
        """
        Parameters:
        - account_size: Total capital in USD
        - max_risk_pct: Maximum % of capital to risk per trade (1-3% recommended)
        - max_cash_usage_pct: Max % of available cash any single trade can use (liquidity cap, default 40%)
        """
        self.account_size = account_size
        self.max_risk_pct = max_risk_pct
        self.max_risk_usd = account_size * (max_risk_pct / 100)
        self.max_cash_usage_pct = max_cash_usage_pct
    
    def calculate_position_size(self, entry_price, stop_loss_pct, confidence_level, regime_adjustment=1.0, volatility_factor=1.0, cash_available=None):
        """
        Calculate optimal position size.
        
        Parameters:
        - entry_price: Entry price in USD
        - stop_loss_pct: Stop loss as % (e.g., 2.5 for 2.5%)
        - confidence_level: 'Very High' | 'High' | 'Medium' | 'Low'
        - regime_adjustment: Multiplier from market regime (0.5 - 1.5)
        - volatility_factor: Multiplier based on volatility (0.5 - 1.5)
        - cash_available: Optional. If set, position is capped at this (total cash minus open positions).
        
        Returns:
        {
            'position_size_usd': How much USD to invest,
            'quantity': How many coins to buy,
            'risk_usd': How much you'll lose if stopped out,
            'pct_of_account': What % of capital this position is,
            'max_loss_pct': Maximum % loss on account if stopped out,
            'recommendation': 'FULL' | 'REDUCED' | 'MINIMAL' | 'SKIP',
            'capped_by_cash': True if size was reduced to fit cash_available,
        }
        """
        
        # Guard against invalid stop loss (prevents ZeroDivisionError)
        if not stop_loss_pct or stop_loss_pct <= 0:
            logger.warning("stop_loss_pct=%.4f invalid; returning SKIP", stop_loss_pct or 0)
            return {
                'position_size_usd': 0, 'quantity': 0, 'risk_usd': 0,
                'pct_of_account': 0, 'max_loss_pct': 0,
                'recommendation': 'SKIP',
                'message': 'Invalid stop loss percentage — cannot size position',
                'capped_by_cash': False,
                'breakdown': {
                    'base_size': 0, 'confidence_mult': 0,
                    'regime_mult': regime_adjustment, 'volatility_mult': volatility_factor,
                    'final_mult': 0,
                },
            }

        # Confidence multipliers
        confidence_multipliers = {
            'Very High': 1.0,   # Full size
            'High': 0.75,       # 75% size
            'Medium': 0.5,      # 50% size
            'Low': 0.25,        # 25% size
        }
        
        confidence_mult = confidence_multipliers.get(confidence_level, 0.25)
        
        # Calculate base position size (risk-based)
        # Formula: Position Size = Risk Amount / (Stop Loss %)
        base_position_usd = self.max_risk_usd / (stop_loss_pct / 100)
        
        # Apply confidence adjustment
        adjusted_position_usd = base_position_usd * confidence_mult
        
        # Apply regime adjustment
        adjusted_position_usd *= regime_adjustment
        
        # Apply volatility adjustment
        adjusted_position_usd *= volatility_factor
        
        # Cap at account size (never all-in)
        max_position_usd = self.account_size * 0.8  # Max 80% of account
        final_position_usd = min(adjusted_position_usd, max_position_usd)
        capped_by_cash = False
        if cash_available is not None and cash_available >= 0 and final_position_usd > cash_available:
            final_position_usd = max(0.0, cash_available)
            capped_by_cash = True

        # Minimum position size for exchange compatibility (most exchanges require $10+)
        MIN_POSITION_USD = 10.0
        if 0 < final_position_usd < MIN_POSITION_USD:
            logger.info("Position size $%.2f below exchange minimum $%.2f — skipping", final_position_usd, MIN_POSITION_USD)
            final_position_usd = 0.0
            capped_by_cash = True

        # Calculate quantity
        quantity = final_position_usd / entry_price if entry_price and entry_price > 0 else 0
        
        # Calculate actual risk
        actual_risk_usd = final_position_usd * (stop_loss_pct / 100)
        pct_of_account = (final_position_usd / self.account_size * 100) if self.account_size > 0 else 0
        max_loss_pct = (actual_risk_usd / self.account_size * 100) if self.account_size > 0 else 0
        
        # Recommendation (SKIP if no cash left)
        if final_position_usd <= 0:
            recommendation = 'SKIP'
            message = 'No cash available for new trades'
        elif final_position_usd >= base_position_usd * 0.8:
            recommendation = 'FULL'
            message = 'High confidence - trade full calculated size' + (' (capped by available cash)' if capped_by_cash else '')
        elif final_position_usd >= base_position_usd * 0.5:
            recommendation = 'REDUCED'
            message = 'Medium confidence - reduced position size' + (' (capped by available cash)' if capped_by_cash else '')
        elif final_position_usd >= base_position_usd * 0.25:
            recommendation = 'MINIMAL'
            message = 'Low confidence - minimal position size' + (' (capped by available cash)' if capped_by_cash else '')
        else:
            recommendation = 'SKIP'
            message = 'Confidence too low - skip this trade' + (' (or no cash available)' if capped_by_cash else '')
        
        return {
            'position_size_usd': round(final_position_usd, 2),
            'quantity': round(quantity, 8),
            'risk_usd': round(actual_risk_usd, 2),
            'pct_of_account': round(pct_of_account, 1),
            'max_loss_pct': round(max_loss_pct, 2),
            'recommendation': recommendation,
            'message': message,
            'capped_by_cash': capped_by_cash,
            'breakdown': {
                'base_size': round(base_position_usd, 2),
                'confidence_mult': confidence_mult,
                'regime_mult': regime_adjustment,
                'volatility_mult': volatility_factor,
                'final_mult': round(confidence_mult * regime_adjustment * volatility_factor, 2),
            }
        }
    
    def calculate_for_consensus(self, entry_price, stop_loss_pct, consensus_signal, regime_data=None, coin_volatility=50, cash_available=None, max_cash_usage_pct=None, edge_probability=None, rr_ratio=None):
        """
        Convenience method that auto-determines confidence from consensus signal.
        
        Parameters:
        - entry_price: Entry price
        - stop_loss_pct: Stop loss %
        - consensus_signal: 'ULTRA_BUY' | 'STRONG_BUY' | 'MODERATE_BUY' | 'WEAK_BUY'
        - regime_data: Output from MarketRegimeDetector (optional)
        - coin_volatility: Annualized volatility (0-200)
        - cash_available: Optional. Cap position at available cash (total minus open positions).
        - max_cash_usage_pct: Max % of available cash per trade (liquidity cap). Default 40.
        - edge_probability: Optional. From quant (rr*win_prob - 1). Fractional Kelly scaling.
        - rr_ratio: Optional. Risk:reward ratio (e.g. 3.0). Use with edge_probability.
        
        Returns: Same as calculate_position_size() plus cash_usage_pct, cash_cap_applied, max_cash_usage_pct
        """
        if max_cash_usage_pct is None:
            max_cash_usage_pct = getattr(self, 'max_cash_usage_pct', 40.0)
        
        # Map consensus to confidence
        consensus_to_confidence = {
            'ULTRA_BUY': 'Very High',
            'STRONG_BUY': 'High',
            'MODERATE_BUY': 'Medium',
            'WEAK_BUY': 'Low',
        }
        
        confidence_level = consensus_to_confidence.get(consensus_signal, 'Low')
        
        # Get regime adjustment
        regime_adjustment = 1.0
        if regime_data:
            regime_adjustment = regime_data.get('strategy_adjustment', {}).get('position_size_multiplier', 1.0)
            # Stage 1b: risk-off (BTC < 200d-MA) momentum entries are allowed but sized down
            # (risk-off momentum is weak/flat in the evidence). risk_on None => no change.
            _risk_on = (regime_data.get('characteristics') or {}).get('risk_on', None)
            if _risk_on is False:
                try:
                    from thresholds import get_threshold as _gt
                    regime_adjustment *= float(_gt('RISK_OFF_SIZE_MULT', 0.5))
                except Exception:
                    regime_adjustment *= 0.5
        # VOLATILE sizing handled by market_regime.py position_size_multiplier (0.5).
        # No additional penalty here — the 0.5 multiplier from regime + volatility_factor is sufficient.
        
        # Real Kelly sizing from edge_probability (fractional Kelly from quant).
        # kelly_mult = max(0.25, min(1.5, edge_probability * 2)) so edge 0.5 → full size, edge 0.25 → half.
        if edge_probability is not None:
            try:
                ep = float(edge_probability)
                kelly_mult = max(0.25, min(1.5, ep * 2.0)) if ep > 0 else 0.25
                regime_adjustment *= kelly_mult
            except (TypeError, ValueError):
                pass
        
        # Calculate volatility adjustment
        # High volatility = reduce size
        if coin_volatility > 100:
            volatility_factor = 0.6
        elif coin_volatility > 75:
            volatility_factor = 0.8
        elif coin_volatility > 50:
            volatility_factor = 1.0
        else:
            volatility_factor = 1.2  # Low vol = can size up slightly
        
        result = self.calculate_position_size(
            entry_price=entry_price,
            stop_loss_pct=stop_loss_pct,
            confidence_level=confidence_level,
            regime_adjustment=regime_adjustment,
            volatility_factor=volatility_factor,
            cash_available=cash_available,
        )

        # Fractional Kelly cap (from thresholds or default 5% of account)
        try:
            from thresholds import get_threshold
            kelly_cap_pct = float(get_threshold('KELLY_CAP_PCT', 5.0) or 5.0)
        except Exception:
            kelly_cap_pct = getattr(self, 'KELLY_CAP_PCT', 5.0)
        kelly_cap_usd = self.account_size * (kelly_cap_pct / 100)
        position_size_usd = result['position_size_usd']
        if position_size_usd > kelly_cap_usd:
            position_size_usd = kelly_cap_usd
            result['kelly_cap_applied'] = True
        else:
            result['kelly_cap_applied'] = False
        result['kelly_cap_usd'] = round(kelly_cap_usd, 2)
        
        # Apply hard max cash usage per trade (liquidity cap)
        total_cash = self.account_size
        if cash_available is not None and cash_available >= 0 and total_cash > 0:
            max_by_cash = cash_available * (max_cash_usage_pct / 100.0)
            final_position_size_usd = min(position_size_usd, max_by_cash)
        else:
            final_position_size_usd = position_size_usd
        
        # Recompute quantity and risk metrics from capped size
        entry = float(entry_price) if entry_price and entry_price > 0 else 0
        quantity = (final_position_size_usd / entry) if entry else 0
        actual_risk_usd = final_position_size_usd * (stop_loss_pct / 100)
        pct_of_account = (final_position_size_usd / total_cash) * 100 if total_cash else 0
        max_loss_pct = (actual_risk_usd / total_cash) * 100 if total_cash else 0
        
        result['position_size_usd'] = round(final_position_size_usd, 2)
        result['quantity'] = round(quantity, 8)
        result['risk_usd'] = round(actual_risk_usd, 2)
        result['pct_of_account'] = round(pct_of_account, 1)
        result['max_loss_pct'] = round(max_loss_pct, 2)
        result['cash_usage_pct'] = round((final_position_size_usd / total_cash) * 100, 1) if total_cash > 0 else 0
        result['cash_cap_applied'] = final_position_size_usd < position_size_usd
        result['max_cash_usage_pct'] = max_cash_usage_pct
        
        return result

    def update_account_size(self, new_size):
        """Update account size (e.g., after profits/losses)"""
        self.account_size = new_size
        self.max_risk_usd = new_size * (self.max_risk_pct / 100)
    
    def get_settings(self):
        """Get current position sizer settings"""
        return {
            'account_size': self.account_size,
            'max_risk_pct': self.max_risk_pct,
            'max_risk_usd': self.max_risk_usd,
            'max_cash_usage_pct': getattr(self, 'max_cash_usage_pct', 40.0),
        }

    def update_max_cash_usage_pct(self, pct: float) -> None:
        """Set max % of available cash any single trade can use (liquidity cap)."""
        self.max_cash_usage_pct = max(20.0, min(65.0, float(pct)))


# =============================================================================
# Example usage
# =============================================================================

if __name__ == '__main__':
    # Initialize for $20k account with 2% max risk
    sizer = PositionSizer(account_size=20000, max_risk_pct=2.0)
    
    # Example: BTC at $45,000, 2.5% stop, STRONG_BUY signal
    result = sizer.calculate_for_consensus(
        entry_price=45000,
        stop_loss_pct=2.5,
        consensus_signal='STRONG_BUY',
        coin_volatility=60,
    )
    
    print("Position Size Calculation:")
    print(f"  Entry: $45,000")
    print(f"  Stop: 2.5% ($43,875)")
    print(f"  Signal: STRONG_BUY")
    print(f"\nRecommendation: {result['recommendation']}")
    print(f"  Position Size: ${result['position_size_usd']:,.2f}")
    print(f"  Quantity: {result['quantity']:.8f} BTC")
    print(f"  Risk if Stopped: ${result['risk_usd']:,.2f}")
    print(f"  % of Account: {result['pct_of_account']}%")
    print(f"  Max Loss: {result['max_loss_pct']}%")
    print(f"\n{result['message']}")
