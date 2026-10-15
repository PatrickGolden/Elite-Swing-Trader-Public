# coin_utils.py — Shared coin data helpers (price changes, float coercion)

from typing import Any, Dict, Optional


def safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce value to float, returning default on None/failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_price_changes(coin: Dict[str, Any]) -> Dict[str, float]:
    """Extract price change percentages from a coin dict.

    Checks indicators shorthand keys (ch1h, ch24h, ch7d, ch30d) first,
    then falls back to top-level CoinGecko keys. Returns floats with 0.0 default.

    Returns:
        {'ch1h': float, 'ch24h': float, 'ch7d': float, 'ch30d': float}
    """
    ind = coin.get('indicators') or {}
    return {
        'ch1h': safe_float(
            ind.get('ch1h') if ind.get('ch1h') is not None
            else coin.get('price_change_percentage_1h_in_currency')
        ),
        'ch24h': safe_float(
            ind.get('ch24h') if ind.get('ch24h') is not None
            else coin.get('price_change_percentage_24h')
            or coin.get('price_change_percentage_24h_in_currency')
        ),
        'ch7d': safe_float(
            ind.get('ch7d') if ind.get('ch7d') is not None
            else coin.get('price_change_percentage_7d_in_currency')
        ),
        'ch30d': safe_float(
            ind.get('ch30d') if ind.get('ch30d') is not None
            else coin.get('price_change_percentage_30d_in_currency')
        ),
    }
