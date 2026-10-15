# Why We Had No Trades During a +9% Crypto Rally — Assessment & Fixes

## Executive summary

The system is built to **avoid losers** (quant-first, AI veto, specialist veto, regime filters). On a strong up day, several gates act together so that **no coin ever reaches "take the trade"** — so we also **avoid winners**. This document traces the full pipeline, identifies root causes, and proposes concrete changes so we participate in rallies while keeping risk controls.

---

## 1. Pipeline recap (quant → brain → sentiment → veto)

1. **Scorer** (`scorer.py`): Builds a 0–100 **base score** from RSI (25), momentum (25), volume (15), sentiment (20), Bollinger (15), MACD (±3). BUY requires score ≥ 62 **and** at least one of: RSI &lt; 50, momentum &gt; 0, MACD bullish.
2. **Pre-AI gate** (`app.py`): Only coins with **base score &gt; score_threshold** get sent to Haiku + Grok. Threshold is regime-dependent: **BULL 75, SIDEWAYS 70, BEAR 46, VOLATILE 80.** In VOLATILE, almost no one passes.
3. **Quant** (`quant_signal.py`): Takes base score, adds order-book, MTF alignment, support bonuses, then:
   - **Regime safety**: In **BEAR** or **VOLATILE**, if **adjusted score &lt; 78** → forced to **HOLD** (no BUY at all).
   - Funding veto in BEAR if funding &gt; 0.08%.
   - High ATR in BEAR: score &lt; 80 → capped below STRONG.
4. **Screening**: **Both** Haiku and Grok must say **BUY** or the coin is discarded (“screening_failed”). No consensus is computed for that coin.
5. **Consensus** (`consensus_engine.py`): For 2-model, STRONG_BUY = both BUY + High confidence. In **BEAR/VOLATILE**, consensus is **downgraded**: STRONG_BUY → MODERATE_BUY, MODERATE_BUY → **WEAK_BUY**. Trade recommendation for WEAK_BUY is **“NO — Insufficient conviction. Skip or wait.”**
6. **apply_full_veto_logic** (`app.py`): **Quant is primary.** If quant says HOLD, final_signal stays HOLD. If quant says BUY, final_signal = consensus_signal (e.g. ULTRA/STRONG/MODERATE/WEAK). Only **ULTRA_BUY, STRONG_BUY, MODERATE_BUY** get position sizing; **WEAK_BUY does not**.
7. **Specialist veto** (`veto_specialist.py`): Runs when BEAR+BUY, high vol+BUY, or models disagree. Can override to HOLD. Rules: funding in BEAR, whale dump, LTH distribution, **narrative exhaustion** (F&amp;G ≥ 78 and 7d ≥ 25%).

---

## 2. Root causes: why zero trades on a +9% day

### A. Regime lag and VOLATILE gate

- **Regime** is from BTC 7d/30d, volatility, Fear &amp; Greed, altcoin participation. A **single strong up day** can leave 7d/30d still negative or flat, so regime stays **BEAR** or **SIDEWAYS**. A big up move also **increases volatility** → regime can be **VOLATILE**.
- **VOLATILE** sets **score_threshold = 80**. Very few coins have base score &gt; 80, so **almost no coins reach AI screening** on high-volatility rally days.
- **BEAR** sets quant **regime safety: score &lt; 78 → HOLD**. So even coins that pass the pre-AI gate (score &gt; 46) get **no BUY from quant** unless adjusted score ≥ 78.

**Conclusion:** On the day of the rally, we’re likely in BEAR or VOLATILE with lagging 7d data. That combination either blocks coins from AI (VOLATILE 80) or blocks quant BUY (BEAR 78).

### B. Scorer is “buy the dip” biased

- **RSI**: Lower RSI = higher score (oversold = opportunity). After a +9% day, RSI is often **elevated** (e.g. 55–70) → **fewer RSI points**.
- **Bollinger**: Score is highest **near the lower band**. After a rally, price is often **mid or upper band** → **fewer BB points**.
- **Momentum** is weighted 1h/24h/7d/30d — so a strong 24h can help, but RSI and BB can still keep total score from reaching 78 in BEAR.

So the system is optimized for **dips and mean reversion**, not for **breakouts and trend continuation**. On the day of the rally, scores may not be high enough to pass the BEAR 78 gate.

### C. Both AIs must say BUY

- Screening log shows many **HOLD/HOLD** or **HOLD/BUY** (e.g. Grok BUY, Haiku HOLD). Any disagreement → **screening_failed** → no consensus, no trade.
- So we require **perfect agreement** just to consider a trade. One cautious model (e.g. “overbought, wait for pullback”) kills the signal.

### D. BEAR downgrades and WEAK_BUY = no trade

- In BEAR, consensus **STRONG_BUY → MODERATE_BUY**, **MODERATE_BUY → WEAK_BUY**. So even when both models say BUY with good confidence, we end up with **WEAK_BUY**.
- **WEAK_BUY** is explicitly **“NO — Insufficient conviction. Skip or wait.”** So we **never take** WEAK_BUY trades, and in BEAR we push many setups into WEAK_BUY.

### E. Specialist veto on every BEAR+BUY

- In BEAR, **every** BUY goes through the specialist veto. Narrative-exhaustion rule: F&amp;G ≥ 78 and 7d ≥ 25% → VETO. After a big up day, F&amp;G and 7d can trip this and **veto** otherwise valid setups.

### F. Evidence from your screening log

- **market_regime: BEAR**, **quant_tier: HOLD** for all logged coins. So **quant never emitted BUY** for the coins that were screened. That implies either (1) no coin had adjusted score ≥ 78 in BEAR, or (2) funding/other veto forced HOLD. Combined with Haiku/Grok often disagreeing (HOLD vs BUY), the pipeline never produced a trade.

---

## 3. Design tension: “no bad trades” vs “some good trades”

- **Current design**: Many independent gates (regime, quant 78, both AIs BUY, no WEAK_BUY, specialist veto). Result: **very few false positives**, but **many false negatives** on strong up days.
- **Goal**: Keep risk controls (veto, funding, sizing) but **allow participation when the market is actually rallying** (e.g. 24h momentum positive, BTC leading).

---

## 4. Recommended changes (prioritized)

### 4.1 Rally-aware regime softening (high impact)

When the **market is already up** over a short window (e.g. BTC 24h &gt; 5% or top coins’ average 24h &gt; 5%), treat the environment as **“rally in progress”** and slightly relax BEAR/VOLATILE so we can participate:

- **Pre-AI gate**: In VOLATILE, if 24h rally &gt; threshold, **lower score_threshold** from 80 to e.g. **68** so more coins get to AI.
- **Quant**: In BEAR, if 24h rally &gt; threshold, **temporarily lower** the regime-safety score floor from 78 to e.g. **72** so STRONG/MODERATE quant BUY is possible.

Implementation: add a **“rally” flag** (e.g. from BTC 24h or average 24h of top 5 coins) and use it in `app.py` (score_threshold) and `quant_signal.py` (regime safety floor). Use constants (e.g. `RALLY_24H_PCT = 5.0`, `BEAR_RALLY_MIN_SCORE = 72`) so behavior is tunable.

### 4.2 Allow MODERATE_BUY in BEAR when both AIs say BUY (medium impact)

Today in BEAR we downgrade STRONG_BUY → MODERATE_BUY and MODERATE_BUY → WEAK_BUY. So we **never** take MODERATE_BUY in BEAR (we turn it into WEAK_BUY). Change:

- In BEAR, **do not downgrade STRONG_BUY to MODERATE_BUY** when both Haiku and Grok say BUY with High confidence. Only downgrade one tier (STRONG_BUY stays STRONG_BUY, MODERATE_BUY stays MODERATE_BUY) so that **MODERATE_BUY in BEAR** still gets position sizing (with smaller size already implied by regime).

Alternative: keep downgrade but **treat WEAK_BUY as tradeable in BEAR with reduced size** (e.g. 0.5× MODERATE size). That would require a clear “WEAK_BUY = smaller size” path in position_sizer and UI.

Recommendation: **allow STRONG_BUY to remain STRONG_BUY in BEAR** when both AIs agree BUY with High confidence; keep MODERATE_BUY → WEAK_BUY for lower confidence. That increases participation without going to full WEAK_BUY trading.

### 4.3 Scorer: add a “momentum breakout” bonus (medium impact)

When **momentum is strongly positive** (e.g. composite momentum &gt; 15) **and** 24h change &gt; 5%, add a **small score bonus** (e.g. +3–5 points) so that strong trend days can push scores over the BEAR 78 (or 72) gate. This keeps the “buy the dip” core but adds “participate in breakout” when the move is already confirmed.

### 4.4 Regime detection: shorten reaction to strong 24h (lower impact)

In `market_regime.py`, give **more weight to 24h** (and optionally 1h) when they are strongly positive, so that after a +9% day we shift toward BULL or at least SIDEWAYS sooner, instead of staying BEAR/VOLATILE for days. This reduces regime lag and naturally relaxes BEAR/VOLATILE gates over time.

### 4.5 Narrative exhaustion: exempt “early” rally (lower impact)

Narrative exhaustion (F&amp;G ≥ 78, 7d ≥ 25%) is meant to avoid chasing. On the **first** big up day, 7d might still be &lt; 25%. So this rule may not be the main blocker on day one. If we see vetoes in logs with reason “narrative exhaustion” on strong up days, consider requiring **both** 7d ≥ 25% **and** 24h ≥ 10% (or similar) so we don’t veto the first leg of a new trend.

### 4.6 Screening: optional “one BUY one HOLD” path (experimental)

Allow a **smaller-size** path when **one** model says BUY and the other HOLD (e.g. consensus = MODERATE_BUY, position size 0.5×). This would increase signals but also noise; best as an **opt-in** (e.g. config flag) and with clear logging so you can measure hit rate before enabling by default.

---

## 5. Summary table

| Layer        | What blocks rallies                     | Suggested change                                      |
|-------------|------------------------------------------|-------------------------------------------------------|
| Pre-AI gate | VOLATILE threshold 80                    | Rally mode: lower to ~68 when 24h &gt; 5%            |
| Quant       | BEAR score &lt; 78 → HOLD                 | Rally mode: lower floor to ~72 when 24h &gt; 5%       |
| Consensus   | BEAR downgrades STRONG→MOD, MOD→WEAK    | In BEAR, keep STRONG_BUY when both AIs High confidence |
| Trade take  | WEAK_BUY = no position                  | No change to WEAK; fix by not over-downgrading       |
| Scorer      | Dip bias (RSI/BB)                       | Momentum breakout bonus when 24h strong              |
| Regime      | Lag (7d/30d)                            | Weight 24h more when strongly positive              |
| Veto        | Narrative exhaustion                    | Optional: require 24h very high to veto “first leg”  |

---

## 6. Next steps

1. **Implement rally-aware softening** (pre-AI threshold + quant BEAR floor) with constants in one place (e.g. `config/rally_config.json` or top of `quant_signal.py` and `app.py`).
2. **Stop over-downgrading in BEAR** when both AIs agree BUY with High confidence (consensus_engine.py).
3. **Add momentum breakout bonus** in scorer when 24h and momentum are strong.
4. **Run learning pipeline** (missed-opportunity analysis) after implementing; use `data/learning_report.json` and screening log to verify more opportunities are captured and outcomes are acceptable.
5. **Update `system_manual.py`** to describe “rally participation” and any new constants so behavior is documented for operators.

This keeps the quant-first, veto-safe design while allowing the system to **participate in winning trades** when the market is already moving up.

---

## 7. Short-term volatility handling (dips = opportunity, pumps = don’t chase)

To stay **one step ahead** of short-term volatility without changing core logic:

- **Dips as opportunity**: In BEAR/VOLATILE, when a coin has a **sharp 24h dip** (e.g. 24h &lt; -6%) and **1h is stabilizing** (1h &gt; -3%), the quant can allow **MODERATE_BUY** at score ≥ 65 (dip-path). Scorer adds a small **stabilizing-dip bonus** when 24h &lt; -5%, 1h &gt; -4%, and 1h &gt; 24h. So we can catch bounces without relaxing the core regime floor for every setup.
- **Pumps = don’t chase**: Quant **caps tier at STRONG_BUY** when coin 24h &gt; 14% (no ULTRA on a just-pumped coin). Scorer applies a small **short-term overbought penalty** when 24h &gt; 12%. **Veto** adds a **short-term pump** rule: F&amp;G ≥ 70 and coin 24h ≥ 12% → VETO (don’t chase). Narrative exhaustion (7d) remains for extended moves.
- **Indicators**: Each coin has **ch1h** and **ch24h** in `indicators` so scorer, quant, and veto use the same short-term context.

Tunable constants: `quant_signal.py` (DIP_24H_PCT, DIP_1H_STABILIZING, DIP_MIN_SCORE, PUMP_24H_PCT), `veto_specialist.py` (SHORT_TERM_PUMP_FG, SHORT_TERM_PUMP_24H_PCT).

---

## 8. Multi-timeframe overlay: 1d, 1w, 2w, up to 1 month

Logic and short-term overlay now account for **1h, 1d (24h), 1w (7d), and ~1m (30d)**. (CoinGecko does not provide 14d/2w; we use 7d and 30d as the medium-term anchors.)

- **Indicators** (`indicators.py`): Each coin has `ch1h`, `ch24h`, `ch7d`, `ch30d` so scorer, quant, and veto share the same 1d/1w/1m context.
- **Quant** (`quant_signal.py`):
  - **MTF medium**: When 24h, 7d, 30d are same sign (1d/1w/1m trend aligned), add **+2** score (`MTF_MEDIUM_BONUS`).
  - **Dip path**: Require **7d &gt; -15%** (`DIP_7D_MAX`) so we don’t buy a falling knife; dip-path BUY still requires 24h dip + 1h stabilizing.
  - **Pump cap**: Cap tier when **24h &gt; 14%** or **7d &gt; 20%** or **30d &gt; 35%** (ULTRA→STRONG; STRONG→MODERATE when 7d/30d extended).
- **Veto** (`veto_specialist.py`): **Extended rally (1m)**: F&amp;G ≥ 72 and **30d ≥ 40%** → VETO (don’t chase monthly extended). Rules 4 (7d narrative), 5 (24h short-term), 6 (30d extended) cover 1d/1w/1m.
- **Scorer** (`scorer.py`): **Trend alignment**: 24h, 7d, 30d all positive → +2; all negative → +1 (capitulation context). **Extended overbought**: 7d &gt; 25% and 30d &gt; 30% → -2.

---

## 9. Additional short-term and volatility-harvesting strategies

These keep the core logic intact while adding more ways to capture short-term and volatile setups (position sizer already reduces size in high vol).

1. **Pullback-in-trend (buy the pullback)**  
   - **Idea**: 7d and 30d up, 24h down = pullback in an uptrend; often a good entry.  
   - **Scorer**: When 7d &gt; 0, 30d &gt; 0, 24h &lt; 0 and 24h &gt; -8%, add **+2**.  
   - **Quant**: In BEAR/VOLATILE, when 7d &gt; 0, 30d &gt; 0, and 24h in (-8%, -2%], score ≥ 66 → **MODERATE_BUY** (pullback path). Constants: `PULLBACK_24H_MAX`, `PULLBACK_24H_MIN`, `PULLBACK_MIN_SCORE`.

2. **Volatility harvesting (high ATR + dip)**  
   - **Idea**: In high-volatility regimes, participate in dips with **reduced size** (position sizer already scales down by volatility) instead of sitting out.  
   - **Quant**: In BEAR/VOLATILE, when **ATR% ≥ 3.5**, score ≥ 68, 24h &lt; 0, 1h &gt; -4%, 7d &gt; -12% → **MODERATE_BUY** (vol-harvest path). Constants: `VOL_HARVEST_ATR_PCT`, `VOL_HARVEST_MIN_SCORE`, `VOL_HARVEST_7D_MAX`.

3. **Resilience bonus (24h down, RSI holding)**  
   - **Idea**: 24h negative but RSI still &gt; 38 = price held better than momentum suggests; potential support.  
   - **Scorer**: When 24h &lt; 0 and RSI &gt; 38, add **+2**.

4. **Negative funding on dip**  
   - **Idea**: When funding is negative (shorts paying), dip longs are structurally favored (squeeze risk is lower).  
   - **Quant**: When the **dip path** is taken and **funding &lt; 0%**, set quant confidence to **68** (from 65).

**Tuning**: All of the above use named constants in `quant_signal.py` and `scorer.py` so thresholds can be adjusted without changing core logic.

---

## 10. Sniper rifle / blood in the street (asymmetric returns)

Philosophy: *Be greedy when others are fearful.* We are a sniper rifle even on short timeframes—few shots, high conviction. Dip-style paths fire only when there is **fear** (Fear & Greed in a fear zone), so we buy panic and aim for asymmetric payoff.

- **F&G gates (quant)**  
  - **Dip path**: only when **F&G ≤ 45** (`BLOOD_FG_MAX`).  
  - **Pullback path**: only when **F&G ≤ 50** (`PULLBACK_FG_MAX`).  
  - **Vol harvest path**: only when **F&G ≤ 48** (`VOL_HARVEST_FG_MAX`).  

- **Blood-in-the-street path (quant)**  
  When **F&G &lt; 30** (`BLOOD_STREET_FG`), **24h &lt; -5%**, **1h &gt; -4%**, **7d &gt; -15%**, **score ≥ 62** → **STRONG_BUY** with confidence **80** (high-conviction asymmetric). This is the “buy when there is blood in the street” path; it runs after the other dip paths so it can override to STRONG.

- **Scorer (sniper)**  
  - **Blood-in-the-street bonus**: **F&G &lt; 40** and **24h &lt; -5%** → **+5** (so these setups rank at the top).  
  - **Stabilizing dip bonus**: only when **F&G &lt; 55**.  
  - **Pullback bonus**: only when **F&G &lt; 55**.  
  - **Resilience bonus**: only when **F&G &lt; 52**.  
  - **1d/1w/1m downtrend (capitulation) bonus**: only when **F&G &lt; 50**.

Result: we do not buy every dip or every pullback; we concentrate on fear-driven setups for asymmetric returns. Constants: `BLOOD_FG_MAX`, `PULLBACK_FG_MAX`, `VOL_HARVEST_FG_MAX`, `BLOOD_STREET_FG`, `BLOOD_STREET_24H_MAX`, `BLOOD_STREET_MIN_SCORE` in `quant_signal.py`.

---

## 11. Bull market: tokens contributing to the move + momentum patterns

When the regime is **BULL**, we focus on tokens that are **contributing to** the bull move (leading or confirming the trend), not just any BUY. We also apply explicit **momentum patterns and strategies**.

- **Market context for scoring**  
  Before scoring, the app attaches **BTC 24h and 7d** to the data (`market_24h_pct`, `market_7d_pct`) so the scorer can reward **relative strength** (outperforming the market).

- **Scorer (bull participation and momentum)**  
  When **24h, 7d, 30d are all positive** (trend participation):  
  - **Strong trend participation**: momentum &gt; 10 → **+2** (bull participant).  
  - **Outperforming**: coin 24h &gt; BTC 24h → **+2** (leading the rally).  
  - **Momentum acceleration**: 24h move &gt; 7d/4 (short-term pace stronger than weekly) → **+1**.

- **Quant (BULL regime)**  
  - **Bull participant path**: When regime is **BULL**, score ≥ **56** (below normal 58 MODERATE_BUY floor), **24h/7d/30d aligned positive**, and **momentum &gt; 10** → **MODERATE_BUY** (confidence 62). This surfaces tokens contributing to the bull.  
  - **Badges**: In BULL, **bull_participant** when MTF aligned, 24h &gt; 0, momentum &gt; 10; **momentum_continuation** when MTF aligned, 24h &gt; 0, momentum &gt; 5.

Result: In a bull market we favor coins that are participating in or leading the move and that show momentum continuation, with clear badges and a slightly lower score bar (56) for aligned, high-momentum setups.
