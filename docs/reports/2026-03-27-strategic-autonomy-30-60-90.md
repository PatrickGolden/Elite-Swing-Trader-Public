# Strategic Autonomy Plan (30/60/90) - 2026-03-27

## Objective

Grow toward a data-driven, self-improving trading engine while preserving capital through explicit safety gates, auditability, and rollback discipline.

## Operating Principle

- No autonomy expansion without passing deterministic controls first.
- Live-trade quality outranks backtest-only upside.
- Every promotion must have a measurable gate and a reversible rollback path.

## Current Starting State

- Quant gate has 2 failing core tests and must be treated as unstable for promotion decisions.
- Optimizer safety tests are green and controls are largely healthy.
- Roadmap framework is active, but rollback trigger enforcement parity is incomplete.

## 0-30 Days: Stabilize and Fail-Closed

### Goals

- Restore quant gate determinism.
- Close roadmap policy-to-code drift.
- Lock governance cadence before any autonomy expansion.

### Required Deliverables

1. Quant gate regression fix for:
   - bull participant BUY path behavior
   - regression floor anti-drag behavior
2. Roadmap rollback trigger parity implementation against `config/roadmap_framework.json`.
3. Portfolio gate exception handling decision:
   - fail-closed by default, or
   - explicit degraded mode with hard alerting and no auto-exec.
4. Weekly audit packet generation:
   - test results
   - optimizer drift report
   - roadmap transitions/pending approvals snapshot

### Promotion Gates (must all pass)

- `tests/unit/test_quant_signal.py` = 100% pass.
- `tests/stress/test_recursive_optimizer.py` = 100% pass.
- `tests/unit/test_roadmap_automation.py` + `tests/unit/test_roadmap_dispatcher.py` = 100% pass.
- No unresolved critical findings in the latest audit report.

### Rollback Triggers

- Any reappearance of quant deterministic test failures.
- Any roadmap high-risk feature activated without approval+rationale.
- Any unresolved threshold-write rollback failure.

## 31-60 Days: Controlled Learning Expansion

### Goals

- Improve edge capture without sacrificing safety.
- Turn optimizer/live drift into intentional governance decisions.

### Required Deliverables

1. Governance policy for optimizer recommendations:
   - apply/defer/reject with reasons persisted.
2. Quant sensitivity experiments (bounded):
   - one parameter family at a time,
   - fixed observation window,
   - pre-declared success and failure criteria.
3. Extended traceability dashboard/report:
   - policy -> code -> test -> runtime evidence.

### Promotion Gates

- 2 consecutive weekly cycles with all critical suites green.
- Explicit resolution (apply or defer with rationale) for every nontrivial optimizer/live threshold delta.
- Attribution completeness remains >= 95% on closed trades.
- Rolling 30d expectancy >= 0.0% on live trades.

### Rollback Triggers

- Expectancy turns negative for 3 consecutive daily checks.
- Win-rate drop exceeds configured weekly threshold.
- Missed-opportunity reductions come with degraded drawdown/quality.

## 61-90 Days: Bounded Autonomy

### Goals

- Enable narrowly scoped, reversible self-improvement loops.
- Keep human approval for high-blast-radius changes.

### Required Deliverables

1. Tiered autonomy matrix:
   - Tier 0: diagnostics-only auto actions
   - Tier 1: low-risk bounded parameter updates
   - Tier 2: recommendation-only for high-risk changes
2. Auto-change budget policy:
   - max changes/cycle,
   - max absolute threshold movement,
   - mandatory cool-down periods.
3. Incident-response playbook for roadmap/optimizer/quant anomalies.

### Promotion Gates

- 4 consecutive weekly cycles with no critical or high severity audit regressions.
- All roadmap rollback triggers enforced in code and validated by tests.
- Stable execution quality metrics (no material safety incident spikes).

### Rollback Triggers

- Any high-risk automation path bypassing manual approval.
- Any unresolved discrepancy between roadmap policy and runtime behavior.
- Any evidence of optimizer overfitting leaking into auto-apply decisions.

## Autonomy Matrix (Initial)

| Change Class | Examples | Default Mode | Approval | Rollback SLA |
| --- | --- | --- | --- | --- |
| Diagnostics | SHAP reports, telemetry enrichment | Auto | Not required | Same day |
| Low-risk threshold refinement | Small bounded gate shifts | Conditional auto | Optional (weekly review) | Same cycle |
| Regime floor or veto policy changes | BEAR/SIDEWAYS floors, veto confidence | Manual decision | Required | Immediate |
| High-risk optimizer modules | Optuna/advanced tuning activation | Recommendation only | Required with rationale | Immediate |

## Weekly Governance Cadence

1. Run full re-audit command set.
2. Review quant failures first, then roadmap approvals, then optimizer deltas.
3. Decide keep/loosen/rollback with explicit rationale.
4. Store artifacts and decision notes in weekly report.

## KPI Guardrails for "Money-Printer, Responsibly"

- Primary: live expectancy, drawdown, and execution safety incidents.
- Secondary: missed-opportunity rate and optimizer recommendation quality.
- Hard rule: if primary KPIs degrade, autonomy contracts immediately regardless of secondary gains.
