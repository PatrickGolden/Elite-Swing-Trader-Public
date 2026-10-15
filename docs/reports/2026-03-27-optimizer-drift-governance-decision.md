# Optimizer Drift Governance Decision - 2026-03-27

## Context

The optimizer-vs-live diff identified two threshold drifts:

- `BEAR_REGIME_MIN_SCORE`: live `78` vs recommended `81`
- `SIDEWAYS_REGIME_MIN_SCORE`: live `68` vs recommended `78`

Source command: `python scripts/diff_optimizer_vs_live.py`

## Decision

- **Decision type:** Defer both recommendations for now (manual governance hold).
- **Why:** These are high-impact gating thresholds with direct trade-frequency and opportunity-cost effects. They require explicit validation against live-trade quality, not only recommendation logic.

## Rationale

1. Current strategic mode is capital-preservation-first.
2. Quant policy recently tightened and tests were aligned to strict behavior.
3. Additional floor tightening could over-constrain entries before full post-fix observation window is complete.
4. Drift should be revisited after one full weekly review cycle with fresh evidence.

## Review Gate (next cycle)

Before applying either recommendation, require:

- Quant deterministic suite remains green.
- Roadmap rollback parity remains green.
- 30-day live expectancy is non-negative.
- Missed-opportunity profile does not worsen materially.

## Operator Note

This record is the governance artifact for explicit apply/defer tracking until automated decision logging is expanded.
