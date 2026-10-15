# reconciliation.py — Exchange vs Journal reconciliation logic
# Pure functions for dust filtering, external positions detection, quantity mismatch, clean status.
# Used by app.py /api/account/reconcile and tests.

import logging
from typing import Dict, List, Any, Set, Optional

logger = logging.getLogger(__name__)


def reconcile_holdings(
    exchange_holdings: Dict[str, float],
    journal_holdings: Dict[str, float],
    dust_threshold_usd: float,
    price_by_symbol: Dict[str, float],
    exclude_symbols: Optional[Set[str]] = None,
    qty_tolerance: float = 1e-4,
) -> Dict[str, Any]:
    """
    Compare exchange holdings vs journal holdings. Read-only detection.

    - Positions below dust_threshold_usd (by quantity * price) are filtered out.
    - External: on exchange but not in journal (above dust). Symbols in exclude_symbols (e.g. USDC, USDT) are skipped.
    - In journal only: in journal but not on exchange (above dust)
    - Quantity mismatch: same symbol on both sides but different quantities; suppressed if delta USD value is below dust.
    - status: 'clean' if no discrepancies, else 'discrepancy_detected'

    Returns:
        {
            'external': list of {type, symbol, exchange_quantity},
            'in_journal_only': list of {type, symbol, journal_quantity},
            'quantity_mismatch': list of {type, symbol, exchange_quantity, journal_quantity, delta},
            'discrepancies': external + in_journal_only + quantity_mismatch,
            'status': 'clean' | 'discrepancy_detected',
        }
    """
    exclude_symbols = exclude_symbols or set()
    norm_exclude = { (s or '').strip().upper() for s in exclude_symbols }

    def value_usd(sym: str, qty: float) -> float | None:
        p = price_by_symbol.get((sym or '').strip().upper())
        if p is None:
            return None
        return float(qty) * float(p)

    def above_dust(sym: str, qty: float) -> bool:
        v = value_usd(sym, qty)
        if v is None:
            # No price available — conservatively treat as above dust to avoid silently hiding positions
            logger.warning("[reconciliation] No price for %s (qty=%s) — treating as above dust to avoid silent exclusion", sym, qty)
            return True
        return v >= dust_threshold_usd

    def qty_matched(jq: float, eq: float) -> bool:
        if jq <= 0 and eq <= 0:
            return True
        if jq <= 0 or eq <= 0:
            return False
        return abs(jq - eq) <= max(1e-6, qty_tolerance * max(jq, eq, 1.0))

    external = []
    in_journal_only = []
    quantity_mismatch = []

    for sym, qty in exchange_holdings.items():
        if qty <= 0:
            continue
        if (sym or '').strip().upper() in norm_exclude:
            continue
        jq = journal_holdings.get(sym, 0)
        if jq <= 0:
            if not above_dust(sym, qty):
                continue
            external.append({"type": "external", "symbol": sym, "exchange_quantity": round(qty, 8)})
        else:
            if qty_matched(jq, qty):
                continue
            delta_qty = abs(float(jq) - float(qty))
            delta_usd = value_usd(sym, delta_qty)
            if delta_usd is None:
                # Price unavailable — cannot evaluate dust threshold.
                # Include with a flag so operators see it rather than silently
                # dropping a potentially large discrepancy.
                delta = round(float(jq) - float(qty), 8)
                quantity_mismatch.append({
                    "type": "quantity_mismatch",
                    "symbol": sym,
                    "exchange_quantity": round(qty, 8),
                    "journal_quantity": round(jq, 8),
                    "delta": delta,
                    "price_unavailable": True,
                })
                continue
            if not above_dust(sym, qty) and not above_dust(sym, jq):
                continue
            if delta_usd < dust_threshold_usd:
                continue  # rounding artifact below dust
            delta = round(float(jq) - float(qty), 8)
            quantity_mismatch.append({
                "type": "quantity_mismatch",
                "symbol": sym,
                "exchange_quantity": round(qty, 8),
                "journal_quantity": round(jq, 8),
                "delta": delta,
            })

    for sym, qty in journal_holdings.items():
        if qty <= 0:
            continue
        eq = exchange_holdings.get(sym, 0)
        if eq <= 0:
            if not above_dust(sym, qty):
                continue
            in_journal_only.append({
                "type": "in_journal_not_on_exchange",
                "symbol": sym,
                "journal_quantity": round(qty, 8),
            })

    discrepancies = external + in_journal_only + quantity_mismatch
    status = "clean" if not discrepancies else "discrepancy_detected"

    return {
        "external": external,
        "in_journal_only": in_journal_only,
        "quantity_mismatch": quantity_mismatch,
        "discrepancies": discrepancies,
        "status": status,
    }
