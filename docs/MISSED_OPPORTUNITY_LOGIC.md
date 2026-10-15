# Missed-opportunity logic and data requirements

## How “zero missed opportunities” is computed

The learning report’s `missed_opportunity` section does **not** scan the market for all possible trades. It only looks at two data sources that the system already recorded:

1. **From recommendations** (`ai_recommendations_v2.json`)  
   A row counts as a “missed opportunity” only if **all** of these are true:
   - `outcome_7d` is **not** null (must be backfilled).
   - `outcome_7d >= threshold_pct` (default 5%).
   - `consensus_signal` is **HOLD** or **WEAK_BUY** (we passed on it; STRONG_BUY doesn’t count as “missed”).

2. **From screening** (`data/screening_log.json`)  
   A row counts as a “missed opportunity” only if **all** of these are true:
   - `outcome_7d` is **not** null (must be backfilled).
   - `outcome_7d >= threshold_pct` (default 5%).
   - `note == "screening_failed"`.
   - At least one of `haiku_signal` or `grok_signal` is **BUY** (one model wanted to buy but we didn’t).

So “zero missed” can mean either:

- **A)** We had rows with `outcome_7d` and none of them met the “passed on it” + “gained ≥5%” criteria, or  
- **B)** We had **no** rows with `outcome_7d` at all, so the code never evaluated any passed-up trade.

Only in case **A** does “zero missed” actually **prove** we didn’t pass up a trade that did well (within this definition). In case **B**, “zero missed” just means “no data to check.”

---

## Why your current report shows zero (and does **not** prove no missed trades)

### 1. Outcome data comes only from backfill

- `outcome_7d` is set by **backfill** in `ml/outcome_backfill.py`.
- Backfill runs only for entries **at least 7 days old** (`min_age_days = 7`):
  - `cutoff = now - 7 days`
  - If `timestamp >= cutoff`, the entry is skipped and `outcome_7d` is never set.

So any recommendation or screening entry **newer than 7 days** will always have `outcome_7d == null` and will **never** be considered in missed-opportunity.

### 2. Your current data is too new

- **Recommendations:** All entries in `ai_recommendations_v2.json` have timestamps on **2026-02-26**. As of 2026-03-02 that is **4–5 days** ago, so **none** are 7+ days old → backfill never runs for them → **all** have `outcome_7d: null`.
- **Screening log:** Sampled entries have timestamps like **2026-02-25**. That is **5–6 days** ago, so again **none** are 7+ days old at run time → **all** have `outcome_7d: null`.

Verified in the repo:

- No recommendation has `"outcome_7d": <number>` (all are `null`).
- No screening log entry has `"outcome_7d": <number>` (all are `null`).

So the missed-opportunity step **skips every row** on the first check: `if r.get("outcome_7d") is None: continue`. The result is:

- `from_recommendations.total == 0` and `from_screening.total == 0` **because there is no outcome data**, not because we checked and found no missed opportunities.

### 3. Conclusion

- The report **does not** and **cannot** currently prove that “we did not pass up a trade that did well.”
- It can only say: among the **passed-up** recommendations and screening failures we have in our logs, **we have not yet computed any 7-day outcome**, so the missed-opportunity count is zero by construction.

Once you have recommendations and/or screening_failed entries that are **7+ days old**, backfill will start filling `outcome_7d`. Then:

- If those rows include HOLD/WEAK_BUY or screening_failed with a BUY, and `outcome_7d >= 5%`, they will show up as missed opportunities.
- If none of them do, then “zero missed” would be a **verified** zero for that data set (still only for trades we actually logged).

---

## What would be needed to “prove” we didn’t pass up a good trade

The current design **does not** attempt to:

- Scan the whole market for coins that went up 5%+ in the last 7 days.
- Compare that universe to “what we could have bought but didn’t.”

It only asks: among the **specific** recommendations we stored (HOLD/WEAK_BUY) and screening_failed rows we stored, did any have a 7-day gain above the threshold? So “proof” is limited to:

- **Recommendations:** We did not pass up a **logged** HOLD/WEAK_BUY that later gained ≥5% (once those rows are old enough to have `outcome_7d`).
- **Screening:** We did not pass up a **logged** screening_failed with Haiku or Grok BUY that later gained ≥5% (same condition).

To go further (e.g. “no coin we could have seen went up 5% without us”) would require different logic and data (e.g. a full universe of screened symbols and their 7d returns), which is not implemented here.

---

## 30-day buy signal stress report

The script `scripts/buy_signals_stress_report.py` produces `data/buy_signals_stress_30d.json` and `data/buy_signals_stress_30d.md`: it lists buy-related signals from the last 30 days, classifies why each did or did not execute (blocker), and attaches outcomes and verdicts (e.g. missed opportunity vs correct skip).

**Parameters at signal time are not stored.** The report includes a “current params” section (thresholds from `live_thresholds.json`, account from `account_config.json`) **as of report run only** — for reference. You cannot compare “thresholds at the time of the signal” to “today” unless the system is extended to snapshot key parameters when each recommendation or screening entry is written (e.g. `thresholds_snapshot`, `cash_available`, `account_size` on the recommendation record).

---

## Note on how `outcome_7d` is computed in backfill

Backfill currently sets `outcome_7d` as:

```text
((current_price - entry_price) / entry_price) * 100
```

So it uses **today’s price** vs **entry price**, not the price exactly 7 days after the recommendation. So it’s “return from entry to now” for entries older than 7 days, not a strict “7-day forward return.” For “did this trade do well over 7 days?” a stricter definition would use price at `timestamp + 7 days`; that would require historical price data at that date.
