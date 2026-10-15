# =============================================================================
# fill_cost_model.py — single source of truth for simulated fill costs.
#
# STRATEGY.md §4.2 (paper-fill calibration): the paper cohort is only fundable
# evidence if paper fills carry the costs live fills actually pay. The old paper
# constants modeled 0.1%/side fees vs the MEASURED 1.20%/side taker on every
# logged Coinbase fill (June 2026, base tier <$1k 30d volume) — a 12x understatement
# and the root cause of the historical "paper 7x better than live" gap.
#
# Values below are MEASURED or account-reported, not aspirational:
#   TAKER_FEE_PCT_PER_SIDE  0.90  — this account's LIVE fee tier ("Intro"), read from
#                                   Coinbase's read-only transaction_summary endpoint
#                                   2026-10-11 (taker_fee_rate 0.009). Coinbase cut US
#                                   fees 2026-09-16.
#   MAKER_FEE_PCT_PER_SIDE  0.50  — same source (maker_fee_rate 0.005); still UNPROVEN
#                                   as achievable until real maker fills exist (do not
#                                   simulate maker fills as achieved until then)
#   SLIPPAGE_PCT_PER_SIDE   0.05  — conservative vs measured true price slip
#                                   (median −0.015%, worst −0.096%, n=20)
#
# History: 1.20% taker / 0.60% maker until 2026-10-11 — measured on every June 2026 fill,
# and exactly the old base tier Coinbase retired on 2026-09-16. Paper rows journaled
# before 2026-10-11 carry the 1.20% basis in their recorded fees; they are not rewritten.
#
# RE-MEASURE when the fee tier changes (next tier: $10K 30-day volume OR $500K assets on
# platform) or the exchange changes.
# The promotion ladder (STRATEGY.md §4) blocks paper→live until paper fill costs
# match measured live within 0.5pp/trade — this module is how paper stays honest.
# =============================================================================

from typing import Tuple

TAKER_FEE_PCT_PER_SIDE = 0.90
MAKER_FEE_PCT_PER_SIDE = 0.50   # account schedule — no measured maker fills yet
SLIPPAGE_PCT_PER_SIDE = 0.05


def simulated_entry_fill(market_price: float, notional_usd: float) -> Tuple[float, float, float]:
    """Taker entry at market: effective price (slippage against you), quantity,
    and entry fee in USD. Returns (effective_price, quantity, entry_fee_usd)."""
    eff_price = float(market_price) * (1 + SLIPPAGE_PCT_PER_SIDE / 100.0)
    quantity = float(notional_usd) / eff_price if eff_price > 0 else 0.0
    entry_fee_usd = round(float(notional_usd) * (TAKER_FEE_PCT_PER_SIDE / 100.0), 6)
    return eff_price, quantity, entry_fee_usd


def simulated_exit_fill(market_price: float, quantity: float) -> Tuple[float, float]:
    """Taker exit at market: effective price (slippage against you) and exit fee
    in USD. Returns (effective_price, exit_fee_usd)."""
    eff_price = float(market_price) * (1 - SLIPPAGE_PCT_PER_SIDE / 100.0)
    exit_fee_usd = round(float(quantity) * eff_price * (TAKER_FEE_PCT_PER_SIDE / 100.0), 6)
    return eff_price, exit_fee_usd
