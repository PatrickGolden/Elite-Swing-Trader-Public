# Optimal Path: 6-Model Brain + New Option Improvements

> Status: Legacy architecture note. Current production flow is quant-first and the 6-model brain path is removed.
> Keep this file as historical context only.

This doc describes how the 6-model brain is wired in and how the "New Option" improvements (strict gates, trusted-model filter) are applied.

## Summary

- **Default:** App still uses the 3-model pipeline (Haiku + Grok + Sonnet) for screening and consensus.
- **Optional:** Set `USE_6MODEL_BRAIN=1` to use the **6-model brain** for the **top 3** BUY candidates (after Haiku + Grok agree). Those 3 coins get a full 6-model consensus; the rest of the agreed list still use 3-model consensus (Haiku + Grok, no Sonnet).
- **New Option logic** is always applied inside the 6-model brain when it runs:
  - **Stricter gates:** Only "strong" BUY when agreement ≥70% and average confidence ≥80% (configurable in `brain/models.py`).
  - **Trusted-model filter:** BUY only counts if at least one of **Claude Sonnet 4.6**, **DeepSeek R1**, or **Grok-4** voted BUY; otherwise the result is downgraded to HOLD.
  - **Quality tier:** `consensus_quality` (ultra / strong / moderate / hold) is computed so the app can map to ULTRA_BUY / STRONG_BUY / MODERATE_BUY.

No debate round or Judge is implemented yet; that can be added later if you want more refinement.

---

## How to Use the 6-Model Brain

1. **Environment**
  - Set `USE_6MODEL_BRAIN=1` (or `true` / `yes`).
  - Ensure API keys for all 6 models are set (see `brain/models.py` and `.env`):
    - `ANTHROPIC_API_KEY` (Claude Sonnet, Haiku)
    - `XAI_API_KEY` (Grok-4)
    - `GEMINI_API_KEY` (Gemini 2.5 Flash)
    - `GROQ_API_KEY` (Llama 3.3 70B)
    - `DEEPSEEK_API_KEY` (DeepSeek R1)
2. **Refresh flow**
  - Top 10 BUY coins are still screened with **Haiku + Grok**.
  - Coins where both agree on BUY form the `agreed` list.
  - **Top 3** of `agreed` (by score):
    - If `USE_6MODEL_BRAIN=1`: each gets **6-model consensus** (unified prompt → all 6 models → aggregate with New Option rules).
    - If not set: each gets **Sonnet** + 3-model consensus as before.
  - All other agreed coins use 3-model consensus (Haiku + Grok, no Sonnet).
3. **Output**
  - 6-model results are mapped to the same tiers as the 3-model engine: **ULTRA_BUY**, **STRONG_BUY**, **MODERATE_BUY**, **HOLD**, **SELL**.
  - Dynamic targets, position sizing, and alerts use the same logic; recommendations are recorded for performance tracking.

---

## New Option Logic (in `brain/`)


| Idea from "New Option"                                  | Implementation                                                                                                                    |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| ≥70% agreement on direction                             | `CONSENSUS_STRONG_AGREEMENT_PCT = 70` in `brain/models.py`; used for `consensus_quality` "ultra" vs "strong".                     |
| Avg confidence ≥80%                                     | `CONSENSUS_STRONG_CONFIDENCE = 80`; "ultra" only when both strong agreement and strong confidence.                                |
| At least one of Sonnet / DeepSeek R1 / Grok in majority | `TRUSTED_MODEL_NAMES` in `brain/models.py`; in `aggregate_consensus()` a BUY is downgraded to HOLD if no trusted model voted BUY. |
| Debate round                                            | Not implemented (would double API cost; add later if needed).                                                                     |
| Judge on split                                          | Not implemented (can add a single Sonnet call when consensus_reached is False).                                                   |


---

## Files Touched

- `**brain/models.py`** – `TRUSTED_MODEL_NAMES`, `CONSENSUS_STRONG_AGREEMENT_PCT`, `CONSENSUS_STRONG_CONFIDENCE`.
- `**brain/consensus.py**` – Trusted-model check; `consensus_quality`; stricter reasoning when downgrading to HOLD.
- `**consensus_engine.py**` – `record_and_format_6model()` to record 6-model results and return the same shape as `get_consensus()`.
- `**app.py**` – `_build_coin_data_for_brain()`, `_run_6model_consensus_sync()`, and refresh branch when `USE_6MODEL_BRAIN=1` for top 3.

---

## Possible Next Steps (Without Over-Engineering)

1. **Tune thresholds** – Adjust 70% / 80% (or 78% in base consensus) from backtest or live results.
2. **Judge on marginal cases** – When `consensus_reached` is False or agreement is 50–70%, call Sonnet once with all 6 responses and use that as the final BUY/HOLD/SELL.
3. **Debate round (optional)** – For top 1 coin only, run a second round where each model sees others’ answers and can revise; re-aggregate. Enable via a flag if you want to test cost/benefit.