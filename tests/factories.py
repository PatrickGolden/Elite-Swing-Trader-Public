"""
Shared test data factories — reduce duplicate large dicts and keep Coinbase v3 mocks finite.

Heavy tests can request bounded synthetic datasets (deterministic seed) instead of ad-hoc
mega-lists. Prefer **smaller counts** in CI; use larger counts only in SoakLane / local probes.

Import from tests: `from tests.factories import coinbase_balance_v3_single_page, ...`
"""
from __future__ import annotations

import random
from typing import Any, Literal


def coinbase_balance_v3_single_page(
    *,
    usd_free: float = 5000.0,
    usdc_free: float = 3000.0,
    btc_free: float = 0.01,
) -> dict:
    """
    CCXT-style balance dict with v3 pagination terminated (avoids infinite loop in
    execution_engine.fetch_balance_sync when fetch_balance is a MagicMock).
    """
    return {
        "USD": {"free": usd_free, "used": 0.0, "total": usd_free},
        "USDC": {"free": usdc_free, "used": 0.0, "total": usdc_free},
        "BTC": {"free": btc_free, "used": 0.0, "total": btc_free},
        "info": {"has_next": False, "cursor": ""},
    }


def create_synthetic_closed_trades(
    *,
    count: int = 500,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """
    Deterministic closed-position-shaped dicts for analytics / journal stress helpers.

    Default *count* is modest for CI; pass count=5000 only when profiling memory or SoakLane.
    """
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    outcomes: tuple[Literal["win", "loss"], ...] = ("win", "loss")
    symbols = ("BTC", "ETH", "SOL", "XRP", "DOGE")
    for i in range(count):
        sym = symbols[i % len(symbols)]
        entry = 10.0 + rng.random() * 500.0
        exit_p = entry * (1.0 + rng.uniform(-0.08, 0.12))
        qty = round(rng.uniform(0.001, 2.0), 6)
        pnl_usd = round((exit_p - entry) * qty, 4)
        pnl_pct = round(100.0 * (exit_p - entry) / entry, 4) if entry else 0.0
        rows.append(
            {
                "id": i + 1,
                "status": "CLOSED",
                "coin_symbol": sym,
                "entry_price": round(entry, 4),
                "exit_price": round(exit_p, 4),
                "quantity": qty,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "outcome": outcomes[i % 2],
                "hit_ai_target": bool(i % 3 == 0),
                "market_regime": rng.choice(("BULL", "BEAR", "CHOP", "UNKNOWN")),
            }
        )
    return rows


def generate_stress_market_regimes(
    *,
    days: int = 90,
    volatility: bool = True,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """
    Compact per-day regime rows for stress / regime logic (no giant OHLC matrices).
    """
    rng = random.Random(seed)
    regimes = ("BULL", "BEAR", "CHOP", "HIGH_VOL", "LOW_VOL")
    rows: list[dict[str, Any]] = []
    base_vol = 0.02 if volatility else 0.005
    for d in range(days):
        regime = rng.choice(regimes)
        vol_mult = rng.uniform(0.5, 2.5) if volatility else rng.uniform(0.8, 1.2)
        rows.append(
            {
                "day_index": d,
                "regime": regime,
                "daily_return_pct": round(rng.gauss(0.0, base_vol * vol_mult) * 100.0, 6),
                "realized_vol": round(base_vol * vol_mult, 6),
            }
        )
    return rows
