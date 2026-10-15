# =============================================================================
# protected_assets.py — coins the system must NEVER trade on a real exchange.
#
# Owner instruction (2026-09-27, chat): "I have a big XRP position sitting in my
# coinbase account. Do not trade unless i approve."
#
# Why a code constant and not a config key: execution_config.json is writable through
# the dashboard API, so a config flag could be flipped by a click or a sync. Removing a
# coin from this set requires a code change and a commit — an explicit, reviewable act
# that IS the owner's approval. There is deliberately no runtime override.
#
# What it blocks (every real order path funnels through these; see execution_engine):
#   - market buys, market sells, stop-limit sells, limit (take-profit) sells
#   - quantity sync adopting the exchange balance into a journal position
#   - the pre-flight "orphan sweep" that cancels open sell orders on a coin
# What it does NOT block: paper/simulated trades (no exchange contact), read-only
# balance and price fetches.
#
# The hazard it closes: owner holdings and system positions share one Coinbase balance.
# If the system ever held its own XRP position, sync_position_quantities treats the
# exchange balance as truth, would set that position's quantity to the ENTIRE XRP
# balance, and then place a stop-loss sell on all of it.
# =============================================================================
from __future__ import annotations

PROTECTED_ASSETS = frozenset({"XRP"})

_QUOTES = ("USDC", "USDT", "USD", "EUR", "GBP", "BTC")


def base_asset(symbol) -> str:
    """'XRP', 'xrp', 'XRP/USD', 'XRP-USDC', 'XRPUSD' -> 'XRP'. Unknown -> upper string."""
    s = str(symbol or "").strip().upper()
    for sep in ("/", "-", ":"):
        if sep in s:
            return s.split(sep, 1)[0]
    for q in _QUOTES:
        if s.endswith(q) and len(s) - len(q) >= 2:   # 'XRPUSD' -> 'XRP'; 'WBTC' stays 'WBTC'
            return s[: -len(q)]
    return s


def is_protected(symbol) -> bool:
    return base_asset(symbol) in PROTECTED_ASSETS


def refusal(symbol, action: str) -> dict:
    """Uniform refusal result: carries both conventions used by the order functions
    ({success: False} and {status/error}) so every caller treats it as a failure."""
    import logging
    msg = (f"REFUSED {action} on {base_asset(symbol)}: protected owner-held asset "
           f"(protected_assets.py — owner approval required, 2026-09-27)")
    logging.getLogger(__name__).critical(msg)
    return {"success": False, "status": "refused_protected_asset", "error": msg,
            "symbol": str(symbol), "protected": True}
