# Watch-phase schedule (from 2026-07-03)

What happens when, what reaches you, and what to actually do. The system is built
so your default action is nothing — every item below says when that changes.

## Automatic — no action unless something's off

| When | What | Reaches you? |
|---|---|---|
| Every hour | Refresh + signal scan; cohorts checked against risk-on | No |
| Every 30 min | Watchdog restarts the app if it died | No |
| Daily 07:00 | Health check | **Yes — 1 email/day.** Subject says HEALTHY or CRITICAL. |
| Daily 18:00 | Google Drive backup | No |
| Sunday 08:00 | Falsifier cycle → decision memo (`data/decision_memos/`) | No email — read it or ask Claude |
| Monday 08:00 | Claude security audit | Only if findings |

## Your routine

- **Daily (~5 seconds):** glance at the 7 AM health email. HEALTHY subject = delete it.
  CRITICAL subject = read it. **No email at all = the machine or app is down — check.**
- **Weekly (~5 minutes):** read Sunday's decision memo (or ask Claude "how's the
  cohort" — same content, with commentary). Look for the "Attention needed" section.
- **Monthly:** skim the memos as a set — is the gate-watch verdict still "flat was
  right"? Are suppressed signals telling us anything?

## Event triggers — the things to actually look out for

1. **BTC closes above its 200-day MA × 1.015 with positive 30-day momentum**
   → rotation cohort opens its first paper BTC position within the hour.
   *Your move:* confirm the entry appears (ask Claude to verify entry price/fees
   look right). This is day 1 of the fundable track record.
   *Early warning:* the daily health email won't tell you this — but BTC within
   ~5% of its 200-day MA is the "get ready" zone (it was −17% on 07-02).
2. **First risk-OFF flip after that** → cohort closes; first completed track-record
   trade. Sanity-check the exit + net P&L (fees now included by design).
3. **~30 rotation paper closes** (likely several months) → promotion-ladder
   decision: min-size live + the funding conversation. Criteria already written
   in STRATEGY.md §4.
4. **A Sunday memo flags a contradiction** (guards changed, gate verdict flips to
   "COST us upside", cohort expectancy breaches its MDE) → talk it through before
   changing anything. One flag ≠ act; the memo will say what evidence is worth.
5. **2026-09-30 — pre-registered kill date.** If nothing has passed the gates by
   then, the honest conclusion gets written (STRATEGY.md D6). Not extendable
   without documented near-miss evidence.
6. **Q3 trial slots (2 remaining)** — spent only when evidence suggests a
   hypothesis worth testing. Idle slots are discipline, not waste.

## Alert posture (set 2026-07-03)

- Master email kill-switch: OFF (lifted)
- Daily 7 AM health report: ON (doubles as dead-man signal)
- Critical alerts (drift, sync failure, cash issues): ON
- Routine trade-alert emails (signals, stops, fills): OFF
- 3×-daily status emails: OFF
- Telegram: configured but off

## Standing rules (learned the hard way)

- Restart the app only via `Start-ScheduledTask -TaskName SwingTrader-Dashboard`
  (never from an editor shell — June outage root cause).
- Config edits to scheduler/execution JSON: stop app → edit → start (the app
  overwrites file edits made while it runs).
- Machine plugged in = app runs 24/7. On battery it sleeps after 3 min idle —
  fine for a daily-MA strategy as long as it wakes most days.
