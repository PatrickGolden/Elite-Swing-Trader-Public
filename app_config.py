"""
Pure config-loading and signal-pipeline functions extracted from app.py.

These have no Flask or app-global dependencies, so they can be imported by
data/persistence modules (enhanced_data_fetcher, trade_journal, exit_manager)
without creating circular imports through the Flask hub.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File paths shared with app.py (app.py re-imports these to keep one source of truth)
# ---------------------------------------------------------------------------
EXECUTION_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'execution_config.json')
LAST_AUTO_SYNC_FILE   = os.path.join(os.path.dirname(__file__), 'data', 'last_auto_sync_at.json')
ACCOUNT_CONFIG_FILE   = os.path.join(os.path.dirname(__file__), 'account_config.json')


# ---------------------------------------------------------------------------
# Execution config
# ---------------------------------------------------------------------------

def _resolve_last_auto_sync_at(main_data):
    """Authoritative source: sidecar file (data/last_auto_sync_at.json).
    Legacy fallback: 'last_auto_sync_at' key inside execution_config.json (read-only, not written to anymore)."""
    import safe_json

    main_data = main_data if isinstance(main_data, dict) else {}
    side = safe_json.load(LAST_AUTO_SYNC_FILE)
    if side and isinstance(side, dict):
        sv = side.get('last_auto_sync_at')
        if sv:
            return sv
    mv = main_data.get('last_auto_sync_at')
    return mv if mv else None


def get_execution_config():
    """Return { auto_execution_enabled, auto_execution_exchange, auto_sync_balance, last_auto_sync_at, twap_min_usd, stale_position_rule_enabled }."""
    import safe_json

    defaults = {
        # FAIL-CLOSED: a missing/corrupt config file must never arm live trading.
        # Real-money execution requires an explicit `"auto_execution_enabled": true` on disk.
        'auto_execution_enabled': False,
        'auto_execution_exchange': 'coinbase',
        'auto_sync_balance': True,
        'last_auto_sync_at': None,
        'twap_min_usd': 500.0,
        'stale_position_rule_enabled': True,
    }
    data = safe_json.load(EXECUTION_CONFIG_FILE)
    if not data:
        return {**defaults, 'last_auto_sync_at': _resolve_last_auto_sync_at({})}
    twap_min = data.get('twap_min_usd')
    twap_min = float(twap_min) if twap_min is not None else 500.0
    # FAIL-CLOSED: only explicit JSON true → on; missing/null/malformed → off.
    _ae = data.get('auto_execution_enabled', False)
    _auto_exec_on = True if _ae is True else False
    return {
        'auto_execution_enabled': _auto_exec_on,
        'auto_execution_exchange': (data.get('auto_execution_exchange') or 'coinbase').lower(),
        'auto_sync_balance': bool(data.get('auto_sync_balance', True)),
        'last_auto_sync_at': _resolve_last_auto_sync_at(data),
        'twap_min_usd': twap_min,
        'stale_position_rule_enabled': bool(data.get('stale_position_rule_enabled', True)),
    }


# ---------------------------------------------------------------------------
# Account config
# ---------------------------------------------------------------------------

def load_account_config():
    """Load account config. Returns dict with defaults if missing."""
    import safe_json
    defaults = {
        'total_cash': 0,
        'risk_pct': float(os.getenv('MAX_RISK_PCT', 2.0)),
        'max_cash_usage_pct': 40.0,
        'dust_threshold_usd': 1.0,
        'heloc_mode': False,
        'heloc_notional': 0.0,
        'heloc_rate_pct': 8.0,
        'usdc_yield_pct': 4.1,
        'usdc_yield_auto_refresh': True,
        'usdc_yield_last_updated': None,
        'heloc_drawn': 0.0,
        'heloc_drawn_peak': 0.0,
        'max_drawn_drawdown_pct': 30.0,
        'hurdle_mode': 'per_trade',
    }
    data = safe_json.load(ACCOUNT_CONFIG_FILE)
    if not data:
        return defaults
    # Migration: heloc_target_account_size → heloc_notional
    if 'heloc_target_account_size' in data and 'heloc_notional' not in data:
        data['heloc_notional'] = data.pop('heloc_target_account_size', 0)
    elif 'heloc_target_account_size' in data:
        data.pop('heloc_target_account_size', None)
    return {
        'total_cash': float(data.get('total_cash', 0) or 0),
        'risk_pct': float(data.get('risk_pct', 2.0) or 2.0),
        'max_cash_usage_pct': float(data.get('max_cash_usage_pct', 40.0) or 40.0),
        'dust_threshold_usd': float(data.get('dust_threshold_usd', 1.0) or 1.0),
        'heloc_mode': bool(data.get('heloc_mode', False)),
        'heloc_notional': float(data.get('heloc_notional', 0) or 0),
        'heloc_rate_pct': float(data.get('heloc_rate_pct', 8.0) or 8.0),
        'usdc_yield_pct': float(data.get('usdc_yield_pct', 4.1) or 4.1),
        'usdc_yield_auto_refresh': bool(data.get('usdc_yield_auto_refresh', True)),
        'usdc_yield_last_updated': data.get('usdc_yield_last_updated'),
        'heloc_drawn': float(data.get('heloc_drawn', 0) or 0),
        'heloc_drawn_peak': float(data.get('heloc_drawn_peak', 0) or 0),
        'max_drawn_drawdown_pct': float(data.get('max_drawn_drawdown_pct', 30.0) or 30.0),
        'hurdle_mode': str(data.get('hurdle_mode', 'per_trade') or 'per_trade'),
    }


# ---------------------------------------------------------------------------
# Signal pipeline
# ---------------------------------------------------------------------------

def apply_full_veto_logic(coin, regime_data, consensus_result, fear_greed_value, position_sizer=None, cash_available=None):
    """
    Quant-first veto pipeline: quant tier IS the signal, rule-based veto gates.
    No LLM override — quant decides, rules protect.
    Mutates consensus_result with final_signal, quant_signal, quant_tier, veto_reason, reasoning.
    Returns (final_signal, quant) for caller use.
    Pass position_sizer and cash_available so quant can attach position_sizing.
    """
    from quant_signal import generate_quant_signal
    from veto_specialist import evaluate as veto_specialist_evaluate, apply_veto_override

    quant = generate_quant_signal(coin, regime_data, fear_greed_value, position_sizer=position_sizer, cash_available=cash_available)
    # Quant tier is the signal — no LLM gate
    final_signal = quant['quant_tier'] if quant['quant_signal'] == 'BUY' else 'HOLD'
    consensus_result['consensus_signal'] = final_signal
    consensus_result['quant_signal'] = quant['quant_signal']
    consensus_result['quant_tier'] = quant['quant_tier']
    consensus_result['veto_reason'] = None
    # Rule-based veto only (no LLM); runs for all BUY tiers in all modes.
    # Keep behavior parity between test mode and production decisioning.
    if final_signal in ('ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY'):
        try:
            veto_result = veto_specialist_evaluate(
                coin, regime_data, consensus_result,
                enhanced=coin.get('enhanced'),
                fear_greed_value=fear_greed_value,
            )
            apply_veto_override(consensus_result, veto_result, regime_data)
        except Exception as e:
            if coin:
                logger.warning(f"  ⚠ Veto specialist error for {coin.get('name', '')}: {e}")
    final_signal = consensus_result.get('consensus_signal', final_signal)
    consensus_result['consensus_quality'] = (
        'ultra' if quant['quant_confidence'] >= 85 else 'strong'
    ) if final_signal in ['ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY'] else 'hold'
    return final_signal, quant
