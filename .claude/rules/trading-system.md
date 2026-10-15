---
description: Domain rules for the crypto swing trading system
globs: ["*.py", "*.json"]
---

# Trading System Rules

## Architecture
- Signal pipeline is deterministic: Data → Indicators → Score → Regime → Signal → Size → Veto → Execute
- LLM (veto_specialist) can only DOWNGRADE signals, never create or upgrade them
- TWAP execution splits orders into 4-6 tranches over 30-60 minute windows
- Position sizing uses Kelly fraction with volatility scaling and hard risk caps

## Critical Invariants
- Maximum total portfolio risk: 6% for accounts ≥$10k (higher caps for smaller accounts)
- Regime detection (Bull/Bear/Sideways/Volatile) drives threshold adjustments
- The optimizer learns from attribution data — never hardcode what it should learn
- Historical replay feeds backtests through the LIVE signal pipeline, not a separate path

## Key Modules
- `quant_signal.py` — Deterministic signal generation (never add randomness)
- `scorer.py` / `regression_scorer.py` — Ranking by technical + on-chain signals
- `position_sizer.py` — Kelly fraction, risk caps, volatility scaling
- `execution_engine.py` — TWAP order execution via CCXT
- `recursive_optimizer.py` — ML learning on trade attribution
- `historical_replay.py` — Accelerated backtesting through live pipeline

## Data Flow
- Exchange: Coinbase Advanced Trade (primary), Kraken (fallback) via CCXT
- Data sources: CoinGecko, Alpha Vantage, Alternative.me, on-chain (12 total)
- All state persisted as JSON files (positions.json, execution_config.json, etc.)
