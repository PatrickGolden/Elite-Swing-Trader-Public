# =============================================================================
# rotation_dryrun.py — prove the rotation cohort's full lifecycle BEFORE it matters.
#
# WHY (June red team A9; Aug 8 review): the cohort has never opened a position in
# production. Without this, the first risk-on flip would be simultaneously the
# first live exercise of open -> journal -> exit -> email AND day 1 of the fundable
# track record. Those must not be the same day.
#
# This runs the REAL track_rotation() against the REAL risk-on rule and the REAL
# alert path — but redirects the journal to a scratch file, so the production
# positions.json is never touched and the track record is not polluted. The only
# thing forced is the risk_on verdict (both True and False, to drive entry+exit).
#
# Run:  python scripts/rotation_dryrun.py            (email suppressed by default)
#       python scripts/rotation_dryrun.py --send-email   (also proves delivery)
# =============================================================================

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tls_setup  # noqa: F401

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD_POSITIONS = os.path.join(PROJECT, 'positions.json')


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotation cohort end-to-end dry run (staging journal)")
    ap.add_argument("--send-email", action="store_true",
                    help="actually send the milestone emails (default: capture only)")
    args = ap.parse_args()

    import trade_journal
    import rotation_tracker
    from rotation_tracker import track_rotation, ROTATION_SOURCE

    prod_before = None
    if os.path.exists(PROD_POSITIONS):
        with open(PROD_POSITIONS, encoding='utf-8') as f:
            prod_before = f.read()

    scratch_dir = tempfile.mkdtemp(prefix='rotation_dryrun_')
    scratch = os.path.join(scratch_dir, 'positions.json')
    with open(scratch, 'w', encoding='utf-8') as f:
        json.dump([], f)

    emails = []
    failures = []

    def _check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    def _fake_email(subject, body, alert_type=None):
        emails.append({'subject': subject, 'body': body, 'alert_type': alert_type})
        return True

    risk_state = {'risk_on': True, 'btc_vs_ma200_pct': 2.0, 'btc_30d_change': 5.0, 'source': 'dryrun'}

    print("=" * 70)
    print("ROTATION COHORT DRY RUN — staging journal, production code paths")
    print(f"scratch journal: {scratch}")
    print("=" * 70)

    email_patch = (patch('alert_system.AlertSystem.send_critical_alert', side_effect=_fake_email)
                   if not args.send_email else patch.object(rotation_tracker, '_noop_never', create=True))

    with patch.object(trade_journal, 'POSITIONS_FILE', scratch), \
         patch('risk_regime.compute_btc_risk_on', side_effect=lambda *a, **k: dict(risk_state)), \
         email_patch:

        # --- 1. entry on risk-on ---
        print("\n1) risk_on=True -> expect ENTRY")
        r1 = track_rotation(65000.0)
        _check("track_rotation returned 'opened'", r1 == 'opened', f"got {r1!r}")
        with open(scratch, encoding='utf-8') as f:
            rows = json.load(f)
        open_rows = [p for p in rows if (p.get('source') or '') == ROTATION_SOURCE
                     and p.get('status') == 'OPEN']
        _check("exactly one cohort position OPEN", len(open_rows) == 1, f"{len(open_rows)} open")
        if open_rows:
            p = open_rows[0]
            _check("tagged source=paper_rotation", (p.get('source') or '') == ROTATION_SOURCE)
            _check("is_test=True (paper, not real money)", p.get('is_test') is True)
            _check("entry price includes slippage", float(p.get('entry_price', 0)) > 65000.0,
                   f"${float(p.get('entry_price', 0)):,.2f} vs mkt $65,000")
            _check("entry fee recorded (cost-honest)", float(p.get('entry_fee_usd') or 0) > 0,
                   f"${float(p.get('entry_fee_usd') or 0):,.2f}")

        # --- 2. idempotency ---
        print("\n2) second refresh while in position -> expect NO duplicate")
        r2 = track_rotation(66000.0)
        _check("track_rotation returned None", r2 is None, f"got {r2!r}")
        with open(scratch, encoding='utf-8') as f:
            rows = json.load(f)
        still_open = [p for p in rows if (p.get('source') or '') == ROTATION_SOURCE
                      and p.get('status') == 'OPEN']
        _check("still exactly one position", len(still_open) == 1, f"{len(still_open)} open")

        # --- 3. exit on risk-off ---
        print("\n3) risk_on=False -> expect EXIT")
        risk_state['risk_on'] = False
        r3 = track_rotation(71500.0)   # +10% move
        _check("track_rotation returned 'closed'", r3 == 'closed', f"got {r3!r}")
        with open(scratch, encoding='utf-8') as f:
            rows = json.load(f)
        closed = [p for p in rows if (p.get('source') or '') == ROTATION_SOURCE
                  and p.get('status') == 'CLOSED']
        _check("position CLOSED", len(closed) == 1, f"{len(closed)} closed")
        if closed:
            c = closed[0]
            _check("exit_reason=risk_off_flip", c.get('exit_reason') == 'risk_off_flip',
                   str(c.get('exit_reason')))
            _check("exit fee recorded", float(c.get('exit_fee_usd') or 0) > 0,
                   f"${float(c.get('exit_fee_usd') or 0):,.2f}")
            pnl = float(c.get('pnl_pct') or 0)
            # +10% gross, minus ~2.4% fees and ~0.1% slippage
            _check("net P&L is cost-honest (~+7.4% on a +10% move)", 6.5 < pnl < 8.5, f"{pnl:+.2f}%")

        # --- 4. flat stays flat ---
        print("\n4) risk_on=False with no position -> expect NO action")
        r4 = track_rotation(60000.0)
        _check("track_rotation returned None", r4 is None, f"got {r4!r}")

        # --- 5. fail-closed on unknown regime ---
        print("\n5) risk_on=None (data unavailable) -> expect NO entry (fail-closed)")
        risk_state['risk_on'] = None
        r5 = track_rotation(65000.0)
        _check("track_rotation returned None", r5 is None, f"got {r5!r}")
        with open(scratch, encoding='utf-8') as f:
            rows = json.load(f)
        _check("no new position opened", not [p for p in rows
                                              if (p.get('source') or '') == ROTATION_SOURCE
                                              and p.get('status') == 'OPEN'])

    # --- 6. alerts ---
    print("\n6) milestone alerts")
    if args.send_email:
        print("  (--send-email: emails were sent for real; check your inbox)")
    else:
        _check("two milestone emails fired (entry + exit)", len(emails) == 2, f"{len(emails)} captured")
        if len(emails) == 2:
            _check("entry email subject says ENTERED", 'ENTERED' in emails[0]['subject'],
                   emails[0]['subject'])
            _check("exit email subject says EXITED", 'EXITED' in emails[1]['subject'],
                   emails[1]['subject'])
            _check("alert_type routes as rotation_milestone",
                   all(e['alert_type'] == 'rotation_milestone' for e in emails))

    # --- 7. production journal untouched ---
    print("\n7) production safety")
    prod_after = None
    if os.path.exists(PROD_POSITIONS):
        with open(PROD_POSITIONS, encoding='utf-8') as f:
            prod_after = f.read()
    _check("production positions.json byte-identical", prod_before == prod_after)
    if prod_after is not None and prod_before is not None:
        # Count-based, not absolute-zero: the live system legitimately opened its first
        # real rotation position on 2026-08-20, so "zero rotation rows in production"
        # stopped being a valid invariant. What must hold is that the DRY RUN adds none.
        def _n_rot(blob: str) -> int:
            return sum(1 for p in json.loads(blob) if (p.get('source') or '') == ROTATION_SOURCE)
        before_n, after_n = _n_rot(prod_before), _n_rot(prod_after)
        _check("dry run added no rotation rows to production", before_n == after_n,
               f"{before_n} before / {after_n} after (real live positions are expected)")

    print("\n" + "=" * 70)
    if failures:
        print(f"DRY RUN FAILED — {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("DRY RUN PASSED — the cohort lifecycle works end to end.")
    print("The first real risk-on flip will be day 1 of the record, not day 1 of testing.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
