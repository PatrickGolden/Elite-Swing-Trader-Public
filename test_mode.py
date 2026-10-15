# test_mode.py — Shared test mode + paper trading flags (no app dependency)
# Used by app to avoid circular imports and enable Light Test Mode (caching).

import os
import safe_json

TEST_MODE_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'test_mode_config.json')
DEFAULT_PAPER_TRADE_NOTIONAL_USD = 100.0


def _load_config() -> dict:
    data = safe_json.load(TEST_MODE_CONFIG_FILE, default=dict)
    return data if isinstance(data, dict) else {}


def _save_config(updates: dict) -> bool:
    cur = _load_config()
    cur.update(updates)
    return safe_json.save(TEST_MODE_CONFIG_FILE, cur)


def get_test_mode() -> bool:
    """Return True if Test Mode is on. Default True when file missing (legacy)."""
    data = _load_config()
    return bool(data.get('test_mode', True))


def set_test_mode(on: bool) -> bool:
    """Set Test Mode on/off. Preserves paper_trading keys. Returns True if saved."""
    return _save_config({'test_mode': bool(on)})


def get_paper_trading() -> bool:
    """When True, simulate journal fills for paper trades (no exchange orders). Default False."""
    return bool(_load_config().get('paper_trading', False))


def set_paper_trading(on: bool) -> bool:
    """Enable/disable paper trading simulation."""
    return _save_config({'paper_trading': bool(on)})


def get_paper_trade_notional_usd() -> float:
    """Fixed USD notional per simulated paper trade."""
    v = _load_config().get('paper_trade_notional_usd', DEFAULT_PAPER_TRADE_NOTIONAL_USD)
    try:
        f = float(v)
        return f if f > 0 else DEFAULT_PAPER_TRADE_NOTIONAL_USD
    except (TypeError, ValueError):
        return DEFAULT_PAPER_TRADE_NOTIONAL_USD


def set_paper_trade_notional_usd(usd: float) -> bool:
    """Set default notional per paper trade (must be positive)."""
    try:
        u = float(usd)
    except (TypeError, ValueError):
        return False
    if u <= 0:
        return False
    return _save_config({'paper_trade_notional_usd': round(u, 2)})
