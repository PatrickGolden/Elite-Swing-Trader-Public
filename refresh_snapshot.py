# refresh_snapshot.py — Log per-refresh snapshots for future analysis: open positions + per-coin signals (incl. HOLD).
# One JSON line per refresh appended to data/refresh_snapshots.jsonl. Use for analyzing all decisions over time.

import os
from datetime import datetime, timezone
from typing import Dict, Any, List

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SNAPSHOTS_FILE = os.path.join(DATA_DIR, "refresh_snapshots.jsonl")


def _snapshot_open_position(position: Dict[str, Any], symbol_to_price: Dict[str, float]) -> Dict[str, Any]:
    """Reduce one open position to a small, serializable dict for the snapshot."""
    symbol = (position.get("coin_symbol") or "").strip().upper()
    entry = float(position.get("entry_price") or 0)
    qty = float(position.get("quantity") or 0)
    current_price = symbol_to_price.get(symbol) if symbol_to_price else None
    pnl_pct = None
    if current_price is not None and entry and entry > 0:
        pnl_pct = round((current_price - entry) / entry * 100, 2)

    return {
        "symbol": symbol,
        "coin_id": position.get("coin_id") or "",
        "position_id": position.get("id"),
        "quantity": round(qty, 8) if qty else 0,
        "entry_price": round(entry, 8) if entry else 0,
        "current_stop_price": round(float(position.get("current_stop_price") or 0), 8) or None,
        "current_target_price": round(float(position.get("current_target_price") or 0), 8) or None,
        "entry_time": position.get("entry_time"),
        "current_price": round(current_price, 8) if current_price is not None else None,
        "pnl_pct": pnl_pct,
    }


def _snapshot_coin(coin: Dict[str, Any], *, lean_active: bool = False) -> Dict[str, Any]:
    """Reduce one coin to a small, serializable dict for the snapshot (score + consensus/final/veto)."""
    rec = coin.get("ai_recommendation") or {}
    consensus = rec.get("consensus") or {}
    quant = rec.get("quant") or {}
    badges = quant.get("signal_badges") or []
    dt = coin.get("dynamic_targets") or {}
    risk = coin.get("risk") or {}
    stop_pct = dt.get("stop_loss_pct") or risk.get("stop_loss_pct")

    return {
        "symbol": (coin.get("symbol") or "").upper(),
        "coin_id": coin.get("id") or "",
        "score": round(float(coin.get("score") or 0), 2),
        "quant_signal": (coin.get("signal") or "HOLD").strip() or "HOLD",
        "quant_tier": quant.get("quant_tier") or "",
        "consensus_signal": (consensus.get("consensus_signal") or "").strip() or None,
        "final_signal": rec.get("final_signal"),
        "veto_reason": (consensus.get("veto_reason") or "").strip() or None,
        # Lean-mode observability (Stage 1c): enough per-coin context to diagnose a
        # leaked fill from snapshots alone (which fields were missing on 2026-06-02).
        "quant_path": quant.get("quant_path"),
        "risk_on": quant.get("risk_on"),
        "lean_active": bool(lean_active),
        "lean_pruned": "lean_pruned" in badges,
        "stop_loss_pct": round(float(stop_pct), 2) if stop_pct is not None else None,
    }


def log_refresh_snapshot(
    open_positions: List[Dict[str, Any]],
    coins: List[Dict[str, Any]],
    summary: Dict[str, Any],
    regime_data: Dict[str, Any],
) -> None:
    """
    Append one refresh snapshot for future analysis: open positions + per-coin signals (including HOLD).
    Call after each refresh with current open positions, scored coins, summary, and regime_data.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    regime = (regime_data or {}).get("regime") or "UNKNOWN"

    # Build symbol -> current_price from coins for PnL
    symbol_to_price = {}
    for c in (coins or []):
        sym = (c.get("symbol") or "").strip().upper()
        if sym:
            p = c.get("current_price")
            if p is not None:
                symbol_to_price[sym] = float(p)

    lean_active = False
    try:
        import lean_policy
        lean_active = lean_policy.is_lean_active()
    except Exception:
        pass

    positions_payload = [
        _snapshot_open_position(p, symbol_to_price)
        for p in (open_positions or [])
    ]
    coins_payload = [_snapshot_coin(c, lean_active=lean_active) for c in (coins or [])]
    n_eligible = sum(
        1 for c in (coins or [])
        if (c.get("ai_recommendation") or {}).get("final_signal")
        in ("ULTRA_BUY", "STRONG_BUY", "MODERATE_BUY")
    )
    n_lean_pruned = sum(1 for c in coins_payload if c.get("lean_pruned"))

    entry = {
        "ts": ts,
        "regime": regime,
        "lean": {
            "lean_active": lean_active,
            "risk_on": ((regime_data or {}).get("characteristics") or {}).get("risk_on"),
            "auto_exec_eligible": n_eligible,
            "lean_pruned": n_lean_pruned,
        },
        "summary": {
            "total": int(summary.get("total") or 0),
            "buy": int(summary.get("buy") or 0),
            "hold": int(summary.get("hold") or 0),
            "sell": int(summary.get("sell") or 0),
        },
        "open_positions": positions_payload,
        "coins": coins_payload,
    }

    import safe_json
    safe_json.append_jsonl(SNAPSHOTS_FILE, entry, max_lines=5_000, default=str)
