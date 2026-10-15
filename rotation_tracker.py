# =============================================================================
# rotation_tracker.py — paper cohort for STRATEGY.md candidate #1 (v1.3 ranking).
#
# Strategy: long BTC while risk-on (close > MA200×(1+band) AND 30d change > 0),
# flat otherwise. This is the G8 null-hypothesis benchmark that beat the whole
# pipeline on the honest universe (+1288% vs +365% buy-hold since 2018, max DD
# 48.6% vs 81.4%). The paper cohort measures it at live fidelity — it is the
# track record the owner funds against (D5).
#
# Design constraints (deliberate):
#   - Journal is the ONLY state: the open cohort position is found by
#     source == 'paper_rotation'. No sidecar files to drift.
#   - The tracker exclusively owns its position lifecycle. Cohort positions are
#     invisible to every other exit path: the paper stop/trailing loop filters
#     source == 'paper_trade', and stale/trailing real-money paths skip is_test.
#     Exits happen HERE, only on a risk-off flip.
#   - lean_override=True at open: this is a registered benchmark cohort
#     (data/trials.jsonl), paper-only; the lean fill guard protects real-money
#     entries, which this is not.
#   - Fail-closed: unknown/unavailable risk_on (None/False) opens nothing;
#     on risk-off it always tries to close.
# =============================================================================

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROTATION_SOURCE = 'paper_rotation'
ROTATION_COIN_ID = 'bitcoin'
ROTATION_SYMBOL = 'BTC'


def _open_cohort_positions() -> List[Dict[str, Any]]:
    from trade_journal import get_open_positions
    return [p for p in get_open_positions(exclude_test=False)
            if (p.get('source') or '').strip() == ROTATION_SOURCE]


def _milestone_email(subject: str, body: str) -> None:
    """Best-effort owner email for a cohort entry/exit (STRATEGY.md v1.7c).

    These are the track-record milestones the owner asked to be pinged on — the
    first entry is day 1 of the fundable record. Uses send_critical_alert (bypasses
    routine-alert toggles, respects the master kill-switch) so it lands even though
    routine trade-alert emails are off. Never raises into the refresh loop."""
    try:
        from alert_system import AlertSystem
        AlertSystem().send_critical_alert(subject=subject, body=body,
                                          alert_type='rotation_milestone')
    except Exception as e:
        logger.debug("[rotation] milestone email skipped: %s", e)


def track_rotation(btc_price: Optional[float], notional_usd: Optional[float] = None) -> Optional[str]:
    """Maintain the single BTC rotation paper position from the risk-on signal.
    Called once per refresh cycle. Returns 'opened' | 'closed' | None (no action)."""
    try:
        price = float(btc_price or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return None

    from risk_regime import compute_btc_risk_on
    info = compute_btc_risk_on()
    risk_on = info.get('risk_on')
    open_pos = _open_cohort_positions()

    if risk_on is True and not open_pos:
        from trade_journal import open_position
        from test_mode import get_paper_trade_notional_usd
        from fill_cost_model import simulated_entry_fill
        notional = float(notional_usd or get_paper_trade_notional_usd())
        # MEASURED fill costs (STRATEGY.md §4.2): the track record must be net of the
        # fees/slippage a live fill would pay, or it is not fundable evidence.
        eff_price, quantity, entry_fee_usd = simulated_entry_fill(price, notional)
        pos = open_position({
            'coin_id': ROTATION_COIN_ID, 'coin_name': 'Bitcoin', 'coin_symbol': ROTATION_SYMBOL,
            'signal': 'BUY', 'entry_price': eff_price,
            'quantity': quantity, 'capital_usd': notional,
            'entry_fee_usd': entry_fee_usd,
            'is_test': True, 'source': ROTATION_SOURCE,
            'quant_path': 'rotation', 'risk_regime': 'RISK_ON', 'market_regime': 'ROTATION',
            # Rotation is not a quant-gate signal, so it has no BUY tier. Label it
            # explicitly rather than leaving None: attribution/analytics treat missing
            # metadata as a data-integrity fault, and a self-describing value is safer
            # than a null for the many `.strip().upper()` readers of this field.
            'quant_tier': 'ROTATION',
            # No stop / no TP: the only exit is the risk-off flip (tracker-owned).
            'ai_stop_pct': 0, 'ai_target_pct': 0,
            'lean_override': True,
            'notes': 'STRATEGY.md candidate #1 paper cohort — exits only on risk-off flip',
        })
        if pos:
            logger.info("[rotation] opened paper BTC @ $%.2f eff (mkt $%.2f, fee $%.2f, vs MA200 %+.1f%%)",
                        eff_price, price, entry_fee_usd, info.get('btc_vs_ma200_pct') or 0)
            _milestone_email(
                subject="Rotation cohort ENTERED BTC — track record started",
                body=(f"The BTC risk-on rotation paper cohort opened its first/next position.\n\n"
                      f"Entry (effective, net slippage): ${eff_price:,.2f}\n"
                      f"Market price: ${price:,.2f}\n"
                      f"Entry fee modeled: ${entry_fee_usd:,.2f}\n"
                      f"BTC vs 200-day MA: {info.get('btc_vs_ma200_pct') or 0:+.1f}%\n\n"
                      f"This is a PAPER entry (no real money). Next step: have Claude verify "
                      f"the entry looks right. The position exits automatically on the next "
                      f"risk-off flip."),
            )
            return 'opened'
        logger.warning("[rotation] open_position rejected the cohort entry — check journal log")
        return None

    if risk_on is not True and open_pos:
        from trade_journal import close_position
        from fill_cost_model import simulated_exit_fill
        closed_any = False
        for p in open_pos:
            eff_exit, exit_fee_usd = simulated_exit_fill(price, float(p.get('quantity') or 0))
            closed, err = close_position(p['id'], exit_price=eff_exit,
                                         exit_reason='risk_off_flip',
                                         exit_fee_usd=exit_fee_usd,
                                         notes='rotation paper exit (risk-off flip)')
            if err:
                logger.warning("[rotation] close failed for #%s: %s", p.get('id'), err)
            else:
                closed_any = True
                pnl = (closed or {}).get('pnl_pct')
                logger.info("[rotation] closed paper BTC @ $%.2f (pnl %s%%)", price, pnl)
                try:
                    _pnl_f = float(pnl) if pnl is not None else 0.0
                except (TypeError, ValueError):
                    _pnl_f = 0.0
                _milestone_email(
                    subject=f"Rotation cohort EXITED BTC — {_pnl_f:+.1f}% (risk-off flip)",
                    body=(f"The BTC risk-on rotation paper cohort closed on a risk-off flip.\n\n"
                          f"Exit (effective, net slippage): ${eff_exit:,.2f}\n"
                          f"Net P&L (incl. fees): {_pnl_f:+.2f}%\n\n"
                          f"This completes one paper trade — a data point toward the track "
                          f"record. Ask Claude for the running cohort stats."),
                )
        return 'closed' if closed_any else None

    return None
