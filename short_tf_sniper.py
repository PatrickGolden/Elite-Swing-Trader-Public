# short_tf_sniper.py — Short-TF / Intra-Swing Sniper Module
# Lightweight 1h (and 15m if available) checks for precise entries in high-vol.
# Returns SNIPER_ENTRY sub-signal when conditions align; used as final boost in quant.

from typing import Dict, Any, Optional

# 1h must be above this (stabilizing, not still crashing)
SNIPER_1H_MIN = -3.0
# 1h should be improving vs 24h: ch1h > ch24h/2 (stabilizing)
# Order book: bid-heavy for sniper entry
SNIPER_OB_RATIO_MIN = 1.08
# BB position: near support (low = buy zone)
SNIPER_BB_POS_MAX = 25


def get_short_tf_sniper(coin: Dict[str, Any], quant_signal: str) -> Dict[str, Any]:
    """
    Intra-swing sniper: triggered when main signal is BUY and short-TF conditions align.
    Conditions: 1h > -3%, 1h stabilizing (ch1h > ch24h/2), OB bid_ask_ratio > 1.08, BB_pos < 25.
    Returns dict with active (bool), sub_signal ('SNIPER_ENTRY' or None), confidence (0-100), entry_suggestion (str).
    """
    out = {
        'active': False,
        'sub_signal': None,
        'confidence': 0,
        'entry_suggestion': None,
    }
    if quant_signal != 'BUY':
        return out

    ind = coin.get('indicators') or {}
    ch1h = ind.get('ch1h')
    ch24h = ind.get('ch24h')
    if ch1h is None and ch24h is None:
        ch24h = coin.get('price_change_percentage_24h') or coin.get('price_change_percentage_24h_in_currency')
    ch1h = float(ch1h if ch1h is not None else 0)
    ch24h = float(ch24h if ch24h is not None else 0)

    # 1h > -3% and stabilizing (1h better than half of 24h move)
    if ch1h <= SNIPER_1H_MIN:
        return out
    try:
        if ch24h != 0 and ch1h <= (ch24h / 2.0):
            return out
    except (TypeError, ValueError):
        pass

    # Order book: bid_ask_ratio > 1.08
    try:
        enh = coin.get('enhanced') or {}
        ob = enh.get('order_book') or {}
        ob_ratio = ob.get('bid_ask_ratio')
        if ob_ratio is not None:
            if float(ob_ratio) < SNIPER_OB_RATIO_MIN:
                return out
        else:
            # No OB data: allow but lower confidence
            pass
    except (TypeError, ValueError):
        pass

    # BB position < 25 (near support)
    bb_pos = ind.get('bb_pos')
    if bb_pos is not None:
        try:
            if float(bb_pos) > SNIPER_BB_POS_MAX:
                return out
        except (TypeError, ValueError):
            pass

    # All conditions met
    out['active'] = True
    out['sub_signal'] = 'SNIPER_ENTRY'
    out['confidence'] = 72  # fixed confidence for sniper entry
    price = coin.get('current_price')
    if price is not None and float(price) > 0:
        out['entry_suggestion'] = f"Next 15m close above ${float(price):,.2f}"
    else:
        out['entry_suggestion'] = "Next 15m close above current price"
    return out
