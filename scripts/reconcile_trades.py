"""
Phase 1: Exchange Reconciliation
Cross-check live closed trades and open SL orders against Coinbase.
Run once to verify no hidden damage from price data issues.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    from trade_journal import get_all_positions
    from execution_engine import fetch_order_status, get_coinbase_exchange, fetch_order_with_fee

    positions = get_all_positions(exclude_test=False)
    live_closed = [
        p for p in positions
        if (p.get('status') or '').upper() == 'CLOSED'
        and not p.get('is_test')
        and p.get('source') != 'paper_trade'
        and p.get('source') != 'duplicate_cleanup'
    ]
    live_open = [
        p for p in positions
        if (p.get('status') or '').upper() == 'OPEN'
        and not p.get('is_test')
        and p.get('source') != 'paper_trade'
    ]

    issues = []
    print("=" * 70)
    print("PHASE 1: EXCHANGE RECONCILIATION")
    print("=" * 70)

    # 1. Check gap-down fallback trades specifically
    print("\n--- 1. Gap-down fallback trades (positions 93, 94, 96) ---")
    gap_ids = [93, 94, 96]
    for p in live_closed:
        if p.get('id') not in gap_ids:
            continue
        notes = p.get('notes', '')
        exit_price = float(p.get('exit_price', 0) or 0)
        entry_price = float(p.get('entry_price', 0) or 0)
        pnl = float(p.get('pnl_usd', 0) or 0)
        ratio = exit_price / entry_price if entry_price > 0 else 0
        print(f"  #{p['id']} {p.get('coin_symbol','?'):6s} entry=${entry_price:.4f} exit=${exit_price:.4f} "
              f"ratio={ratio:.4f}x pnl=${pnl:+,.2f}")
        print(f"    notes: {notes[:100]}")
        if 'gap fallback' in notes.lower():
            if ratio > 0.5 and ratio < 2.0:
                print(f"    OK - exit price within normal range of entry")
            else:
                issues.append(f"#{p['id']} {p.get('coin_symbol')}: gap fallback exit ratio {ratio:.4f}x is suspicious")
                print(f"    *** SUSPICIOUS ratio ***")

    # 2. Check all live closed for price anomalies
    print("\n--- 2. All live closed trades - price ratio check ---")
    anomalies = 0
    for p in live_closed:
        entry = float(p.get('entry_price', 0) or 0)
        exit_p = float(p.get('exit_price', 0) or 0)
        if entry > 0 and exit_p > 0:
            ratio = exit_p / entry
            if ratio > 5 or ratio < 0.1:
                anomalies += 1
                issues.append(f"#{p['id']} {p.get('coin_symbol')}: exit/entry ratio {ratio:.4f}x")
                print(f"  *** #{p['id']} {p.get('coin_symbol'):6s} ratio={ratio:.4f}x entry=${entry} exit=${exit_p}")
    if anomalies == 0:
        print(f"  OK - all {len(live_closed)} live closed trades have normal price ratios")

    # 3. Verify open positions have valid SL orders on Coinbase
    print("\n--- 3. Open live positions - SL order verification ---")
    positions_with_sl = [p for p in live_open if p.get('sl_order_id')]
    positions_without_sl = [p for p in live_open if not p.get('sl_order_id')]

    if positions_without_sl:
        print(f"  WARNING: {len(positions_without_sl)} open positions without SL order:")
        for p in positions_without_sl:
            print(f"    #{p['id']} {p.get('coin_symbol','?')} - no sl_order_id")

    for p in positions_with_sl:
        sl_id = p.get('sl_order_id')
        symbol = p.get('coin_symbol', '?')
        try:
            status = fetch_order_status(sl_id)
            if status == 'open':
                print(f"  OK #{p['id']} {symbol:6s} SL order {sl_id[:12]}... is OPEN on Coinbase")
            elif status == 'filled':
                issues.append(f"#{p['id']} {symbol}: SL order filled but position still OPEN in journal")
                print(f"  *** #{p['id']} {symbol:6s} SL order FILLED but position OPEN ***")
            elif status == 'canceled':
                issues.append(f"#{p['id']} {symbol}: SL order canceled on exchange")
                print(f"  *** #{p['id']} {symbol:6s} SL order CANCELED ***")
            else:
                print(f"  ? #{p['id']} {symbol:6s} SL order status: {status}")
            time.sleep(0.3)
        except Exception as e:
            print(f"  ERROR #{p['id']} {symbol:6s} checking SL: {e}")

    if not positions_with_sl and not positions_without_sl:
        print("  No open live positions")

    # 4. Cross-check execution log gap fallback entries
    print("\n--- 4. Execution log gap-fallback telemetry ---")
    log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'execution_log.jsonl')
    gap_entries = []
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get('action') == 'stop_loss_gap_fallback_telemetry':
                        gap_entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        print("  execution_log.jsonl not found")

    for g in gap_entries:
        fill = float(g.get('fill_price', 0) or 0)
        stop = float(g.get('intended_stop', 0) or 0)
        slippage = float(g.get('slippage_to_stop', 0) or 0)
        symbol = g.get('symbol', '?')
        pid = g.get('position_id', '?')
        # Positive slippage means fill was ABOVE stop (favorable for sells)
        if slippage > 0:
            print(f"  #{pid} {symbol:6s} fill=${fill:.4f} stop=${stop:.4f} slippage={slippage:+.2f}% (filled ABOVE stop - favorable)")
        elif slippage > -5:
            print(f"  #{pid} {symbol:6s} fill=${fill:.4f} stop=${stop:.4f} slippage={slippage:+.2f}% (minor slippage)")
        else:
            issues.append(f"#{pid} {symbol}: gap fallback slippage {slippage:.2f}% is concerning")
            print(f"  *** #{pid} {symbol:6s} fill=${fill:.4f} stop=${stop:.4f} slippage={slippage:+.2f}% ***")

    if not gap_entries:
        print("  No gap-fallback telemetry entries found")

    # Summary
    print("\n" + "=" * 70)
    print(f"RECONCILIATION COMPLETE")
    print(f"  Live closed trades checked: {len(live_closed)}")
    print(f"  Open positions checked: {len(live_open)}")
    print(f"  Gap-fallback entries: {len(gap_entries)}")
    print(f"  ISSUES: {len(issues)}")
    for i, issue in enumerate(issues, 1):
        print(f"    {i}. {issue}")
    if not issues:
        print("  ALL CLEAR - no exchange reconciliation issues found")
    print("=" * 70)


if __name__ == '__main__':
    main()
