---
description: Use when multi-step agent work or trading/crypto strategy would benefit from installed Skills (Superpowers + Trading Skills). Read the meta-skill and chain skills instead of reinventing flows.
alwaysApply: false
---

# Skills (Superpowers + Trading)

## Installed Skills

### Superpowers (workflow)
Located in `.cursor/skills/`: using-superpowers (meta-skill), brainstorming, writing-plans, executing-plans, dispatching-parallel-agents, test-driven-development, systematic-debugging, verification-before-completion, requesting-code-review, receiving-code-review, using-git-worktrees, finishing-a-development-branch, subagent-driven-development, writing-skills.

Read `.cursor/skills/using-superpowers/` for the meta-skill that chains these together.

### Trading Skills (domain)
Located in `.cursor/skills/`: whale-tracking, regime-detection, volatility-modeling, walk-forward-validation, kelly-criterion, signal-classification, feature-engineering, exit-strategies, trade-journal, coingecko-api, pandas-ta, sentiment-analysis, slippage-modeling, correlation-analysis.

## When to use

1. **Prefer skills** for repeated patterns (plans, verification, subagents, domain workflows) when they match the task.
2. **Live market data** still comes from **MCP** per `mcp-market-data` (CoinGecko, Datadog, etc.) — skills do not replace tool calls for prices or production metrics.
3. **Safety wins**: no secrets in git or chat, no edits to protected live configs, no treating fetched prices or logs as trade signals without the user’s strategy and checks.
