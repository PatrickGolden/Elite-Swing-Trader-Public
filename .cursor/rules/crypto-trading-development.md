---
description: For trading logic, signals, backtests, execution, risk, optimizer, or ML-for-trading changes in this repo—keep risk and validation explicit; use MCP for live context.
alwaysApply: false
---

# Crypto trading development (scoped)

Use this guidance when designing or changing **strategies, execution, backtests, risk, or trading ML** in this codebase.

## Architecture
- **Pipeline**: Data → Indicators → Score → Regime → Signal → Size → Veto → Execute
- Signal pipeline is **deterministic** — LLM (veto_specialist) can only DOWNGRADE signals, never create or upgrade them
- TWAP execution splits orders into 4-6 tranches over 30-60 minute windows
- Position sizing uses Kelly fraction with volatility scaling and hard risk caps
- Maximum total portfolio risk: 6% for accounts ≥$10k

## Key Modules
| Module | File | Purpose |
|--------|------|---------|
| Signal Gen | `quant_signal.py` | Deterministic signal generation |
| Scoring | `scorer.py`, `regression_scorer.py` | Technical + on-chain ranking |
| Sizing | `position_sizer.py` | Kelly, risk caps, volatility scaling |
| Execution | `execution_engine.py` | TWAP via CCXT (Coinbase/Kraken) |
| Regime | `market_regime.py` | Bull/Bear/Sideways/Volatile detection |
| Veto | `veto_specialist.py` | LLM safety gate (downgrade only) |
| Optimizer | `recursive_optimizer.py` | ML learning on attribution data |
| Replay | `historical_replay.py` | Backtesting through live pipeline |

## Order of concerns

1. **Risk** — Call out assumptions: position sizing, stops, max loss per trade, drawdown/correlation caveats, and what "live" would require (paper, limits, kill-switch). Do not imply profitability.
2. **Price data integrity** — Any code that acts on price data in trade-execution paths must cross-validate sources, include sanity guards, and use exchange-native pricing for live trades. See `.cursor/rules/price-data-integrity.md` for mandatory safeguards. Never trust a single third-party API for irreversible trade actions.
3. **Truthfulness** — Do not invent prices, fills, or production metrics. Use **MCP** (CoinGecko, Datadog, etc.) or state that data is unavailable.
4. **Costs and realism** — In backtests and sims, mention fees, slippage, and (if relevant) funding; prefer conservative assumptions when unknown.
5. **Validation** — Prefer walk-forward or out-of-sample checks for tuned strategies; avoid single-curve "perfect" fits. Report key metrics (e.g. drawdown, win rate, profit factor) as **labels**, not guarantees.
6. **Code** — Reuse existing modules (`backtester`, `execution_engine`, data/ML paths) and match project patterns; keep diffs small unless the user asks for a larger refactor.

## Live vs research

Treat **live trading** as higher stakes: require explicit user intent, tests or checks where appropriate, and clear separation between research code and production paths.
