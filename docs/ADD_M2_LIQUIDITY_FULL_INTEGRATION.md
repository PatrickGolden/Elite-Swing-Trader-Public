# ADD_M2_LIQUIDITY_FULL_INTEGRATION

You are an elite Python/Flask/crypto-trading engineer working on the Elite Swing Trader Dashboard.

SINGLE FINAL PROMPT: Add M2 liquidity indicator + fully integrate into quant logic to drive the best BUY signals in all market conditions (BEAR, SIDEWAYS, BULL, VOLATILE). Zero extra cost, purely in quant/macro sources.

## 1. Fetch M2SL YoY in enhanced_data_fetcher.py

- In get_macro_data(), after the CPI block, add:
  - M2 Money Supply YoY % — key liquidity indicator for debasement and cycle extension
  - FRED series M2SL, limit 25, compute YoY from latest vs 12 months ago
  - result['m2'] = {'value': yoy, 'date': ..., 'label': 'M2 YoY %'}

## 2. Pass M2 to regime and quant

- **market_regime.py** detect_regime():
  - m2_yoy = macro.get('m2', {}).get('value', 0)
  - if m2_yoy > 5.0: scores['BULL'] += 10, scores['SIDEWAYS'] += 6
  - Add to characteristics: 'm2_yoy': round(m2_yoy, 2)
  - Return m2_yoy at top level for quant_signal

- **quant_signal.py** (right after debasement_hedge and cycle_extension blocks):
  - m2_yoy = regime_data.get('m2_yoy', 0) or from characteristics
  - Liquidity-driven BUY logic — strongest asymmetric edge when liquidity expands in fear
  - if m2_yoy > 5.0:
    - if regime in ('BEAR', 'SIDEWAYS') and (debasement_hedge or cycle_extension_confirm): score +6, liquidity_expansion badge, expert reasoning
    - elif regime == 'BULL': score +3, liquidity_expansion badge

## 3. Expert + Liquidity Synergy

- Debasement_hedge and cycle_extension_confirm get extra lift when M2 is expanding (covered above).
- Add liquidity_expansion badge to signal_badges list.

## 4. TEST SCRIPTS (MANDATORY)

- Update tests/test_expert_consolidated.py with at least 4 new tests:
  - M2 fetched and present in macro data
  - M2 > 5% boosts score and adds liquidity_expansion badge in BEAR/SIDEWAYS
  - Synergy with debasement_hedge and cycle_extension_confirm
  - No boost when M2 low

- Run: pytest tests/test_expert_consolidated.py -v --tb=short
- Print: "✅ M2 LIQUIDITY FULLY INTEGRATED — Drives strongest BUY signals in liquidity expansion across all regimes."

## Rules (strict)

- Use ONLY existing FRED code pattern.
- Do NOT touch social parsing (removed; expert_signals from news only).
- Preserve deterministic quant primacy — M2 only boosts existing paths.
- Add "# M2 LIQUIDITY INTEGRATION" comments everywhere.
- Keep everything zero extra cost.

## Verification

- Refresh dashboard, check macro data for M2.
- Test a BEAR coin with high M2 for liquidity_expansion badge and strong reasoning boost.
