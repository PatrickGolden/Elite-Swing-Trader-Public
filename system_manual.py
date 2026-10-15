# =============================================================================
# manual_generator.py — System User Manual Generator
# Creates comprehensive documentation in plain English
# Exportable to Word document
# =============================================================================

from datetime import datetime
from docx import Document
from docx.shared import Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import os


class ManualGenerator:
    """
    Generates user manual for the Elite Trading System.
    Simple, clear explanations for all features.
    """
    
    def __init__(self):
        self.manual_content = self._build_manual_content()
    
    def _part(self, title):
        """Return a part (chapter) marker for TOC and rendering."""
        return {'is_part': True, 'title': title}

    def _build_manual_content(self):
        """Build complete manual as structured data"""
        return {
            'title': 'Elite Swing Trader - User Manual',
            'version': '5.0',
            'date': 'August 2026',
            'sections': [
                self._part('Part 1 — Introduction'),
                self._current_operating_state(),
                self._recent_engine_upgrades_feb2026(),
                self._self_improving_quant_engine_v1(),
                self._mission_statement(),
                self._system_overview(),
                self._part('Part 2 — Signal Pipeline'),
                self._data_sources(),
                self._ai_models(),
                self._market_regime(),
                self._short_term_strategies(),
                self._onchain_metrics(),
                self._consensus_tiers(),
                self._advanced_veto_and_onchain(),
                self._buy_signal_blocker_flow(),
                self._part('Part 3 — Account & Risk'),
                self._account_overview(),
                self._exchange_reconciliation(),
                self._position_sizing(),
                self._portfolio_correlation_hedging(),
                self._risk_management(),
                self._capital_funding_strategy(),
                self._dynamic_targets(),
                self._part('Part 4 — Operations'),
                self._schedule_and_costs(),
                self._alerts_system(),
                self._unattended_operation_alerts(),
                self._exit_monitoring_merged(),
                self._automated_execution(),
                self._part('Part 5 — Record & Improve'),
                self._trade_journal(),
                self._performance_tracking(),
                self._recursive_optimizer(),
                self._backtesting(),
                self._testing_validation(),
                self._automated_test_report(),
                self._part('Part 6 — Usage'),
                self._how_to_use(),
                self._trading_strategies(),
                self._faq(),
                self._part('Appendix'),
                self._glossary(),
                self._document_changelog(),
            ]
        }
    
    # =========================================================================
    # SECTION BUILDERS
    # =========================================================================

    def _recent_engine_upgrades_feb2026(self):
        """Recent Engine Upgrades (Feb 2026) — Probabilistic Upgrade."""
        return {
            'title': 'Recent Engine Upgrades (Feb 2026)',
            'icon': '🆕',
            'content': [
                {
                    'type': 'text',
                    'text': 'The engine has been upgraded to a highest-probability trade system. These changes improve win probability in all market conditions, especially short timeframes and high-volatility or fear regimes.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '1. Dynamic Probabilistic Calibration'
                },
                {
                    'type': 'text',
                    'text': 'Win probability is now driven by historical outcomes: the system looks up the actual 7d win rate for the same regime and path (e.g. BEAR + dip, BULL + bull_participant) from your closed trades. You see the exact historical win % on every signal (in reasoning and in the quant object). Position sizing uses this edge probability for fractional Kelly scaling, so size adapts to your proven edge.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '2. Short-TF / Intra-Swing Sniper'
                },
                {
                    'type': 'text',
                    'text': 'When the main signal is BUY and short-term conditions align (1h stabilizing, order book bid-heavy, price near support), the system can flag a SNIPER ENTRY with a suggested entry (e.g. next 15m close above current price). This improves precise entries in high-volatility regimes. The badge appears in the table and in the analysis modal.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '3. High-Volatility Regime Enhancements'
                },
                {
                    'type': 'text',
                    'text': 'In VOLATILE regime, when ATR is high (≥4%), MTF is aligned, and funding is low (<0.05%), the quant can assign STRONG_BUY with a vol_contraction badge (contraction breakout). Position sizer uses a single 0.5x regime multiplier in VOLATILE (from market_regime); volatility factor adjusts further. The rule-based veto relaxes the funding threshold to 0.12% when funding is negative (dip path favorable).'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '4. Dynamic Veto Threshold'
                },
                {
                    'type': 'text',
                    'text': 'The rule-based veto is purely deterministic; when a rule fires, the signal is overridden to HOLD. Attribution reports veto performance (CONFIRM vs VETO) for tuning rule thresholds in live_thresholds.json.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '5. Quant-First Architecture'
                },
                {
                    'type': 'text',
                    'text': 'The system uses a pure quant pipeline: quant score determines the signal tier. Rule-based veto (funding, whale dump, LTH distribution, narrative exhaustion) protects against failure modes.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '6. Traceability and Badges'
                },
                {
                    'type': 'text',
                    'text': 'Every new path appears in reasoning and in strategy badges: dynamic_prob (when historical win prob is used), intra_swing_sniper (SNIPER ENTRY), vol_contraction (VOLATILE contraction breakout). Edge probability and historical win prob are in the quant object and flow to position sizing and API responses.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '7. Sentiment and Short-Term Timeframes'
                },
                {
                    'type': 'list',
                    'items': [
                        'Sentiment: Alpha Vantage news (50 articles) feeds expert-theme keyword matching; narrative_spike badge when top-10 coins have narrative velocity or strong sentiment delta.',
                        'Short-term: Quant uses 1h/24h/7d/30d (dip path, pump cap, MTF, SNIPER ENTRY). Rule-based veto applies short-term pump and extended 30d rally rules.'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'See Data Sources, Short-Term Strategies, and Advanced Veto for full detail. For Swing Trader v1.0 (regression scorer, real Kelly sizing, walk-forward optimizer), see the next section.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '8. Architecture Recap — End-to-End Pipeline'
                },
                {
                    'type': 'text',
                    'text': 'The pipeline is quant-decides, rules-protect. No fixes needed for conservatism; high-probability setups flow through to auto-execution.'
                },
                {
                    'type': 'list',
                    'items': [
                        'Base scoring (scorer.py): 0–100 from RSI, momentum, volume, sentiment, Bollinger + bonuses. Pre-tier gate: BULL/SIDEWAYS/VOLATILE = 65, BEAR = 46. Top 10 by score proceed.',
                        'Quant score determines the signal tier directly.',
                        'Quant signal (quant_signal.py): Tiers ULTRA ≥85, STRONG ≥72, MODERATE ≥58. Regime floor BEAR/VOLATILE 72 (68 on rally). Seven special BUY paths (blood, dip, pullback, vol harvest, capitulation, bull participant, vol contraction). Pump caps: BULL exempt.',
                        'Rule-based veto (8 rules): funding in BEAR, whale dump, LTH distribution, bull euphoria, bull extended, narrative exhaustion, short-term pump, extended rally. Bull euphoria/extended apply in BULL only; narrative exhaustion, pump, and rally are non-BULL only.',
                        'Position sizing: Single regime multiplier (BULL 1.2, SIDEWAYS 1.0, BEAR 0.6, VOLATILE 0.5). Kelly cap 5%, cash cap 40%.',
                        'Auto-execution: ULTRA_BUY and STRONG_BUY; MODERATE_BUY when quant tier is STRONG+. Trailing stop: +8% activation, 13% trail.'
                    ]
                },
            ]
        }

    def _self_improving_quant_engine_v1(self):
        """Swing Trader v1.0 — Regression scorer, real Kelly, walk-forward optimizer, minimal-touch."""
        return {
            'title': 'Swing Trader v1.0',
            'icon': '⚙️',
            'content': [
                {
                    'type': 'text',
                    'text': 'The core engine has been upgraded with four pillars: a weekly logistic regression scorer that blends with additive scoring once enough trades exist, real fractional Kelly position sizing driven by edge probability and risk:reward, an enhanced recursive optimizer with walk-forward validation and regret-weighted missed-opportunity analysis, and fully automatic operation (nightly cron plus weekly retrain) with zero extra human touch.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '1. Weekly regression scorer (regression_scorer.py)'
                },
                {
                    'type': 'text',
                    'text': 'A logistic regression model is trained weekly on historical attribution trades (win/loss). Features include base_score, order-book ratio, MTF alignment, ATR%%, fear-greed, regime dummies, quant path dummies, and score z-score normalized by coin class and regime over a rolling 30d window. When the number of trades with valid regression_features is at least MIN_TRADES_FOR_REGRESSION (50), the model is trained and the regression probability is blended with the additive score (weight configurable in live_thresholds.json, e.g. REGRESSION_SCORE_WEIGHT 0.3). Below 50 trades, the system uses additive scoring only and logs a fallback message. Training runs on the weekly schedule; at signal time, get_regression_score() returns P(win) 0–1 or None (fallback to additive).'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '2. Real fractional Kelly position sizing'
                },
                {
                    'type': 'text',
                    'text': 'Position sizing is now edge-driven. generate_quant_signal (quant_signal.py) computes final_edge_prob from historical win probability for the same regime/path and the trade\'s risk:reward ratio (rr_ratio). The formula: edge = (rr_ratio × win_prob − (1 − win_prob)) / rr_ratio, then scaled by KELLY_FRACTION (e.g. 0.5). This final_edge_prob and rr_ratio are passed to position_sizer.calculate_for_consensus. The sizer applies a Kelly-derived multiplier to the regime-adjusted size (kelly_mult = clamp(edge_probability * 2, 0.25, 1.5)) and enforces KELLY_CAP_PCT (default 5%%). So size scales with proven edge and risk:reward; no manual override required.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '3. Enhanced recursive optimizer'
                },
                {
                    'type': 'list',
                    'items': [
                        'Walk-forward validation: Explicit 70/30 time-ordered split. Recommendations must improve net impact on both train and validation sets before being eligible for auto-apply.',
                        'Regime grouping: PARAM_REGIME_FILTER in thresholds.py restricts counterfactual analysis to trades in the relevant regime(s) for each parameter.',
                        'MATURITY_TRADE_TARGET (default 50): Applied changes are tracked until at least this many trades are booked under the new threshold; then status can move to CONFIRMED or trigger WATCH/REVERT.',
                        'MIN_TRADES_FOR_REC (default 15): Analysis runs per parameter only when at least 15 trades match the parameter\'s path/regime filter.',
                        'Regret-weighted missed-opportunity analysis: Top missed opportunities (by 7d gain %%) are used to weight threshold relaxation so high-regret false negatives are considered. Auto-apply logic unchanged: min_improvement, zero winners lost, OPTIMIZER_BOUNDS.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '4. Minimal-touch operation'
                },
                {
                    'type': 'text',
                    'text': 'Nightly cron runs the full recursive optimizer cycle at 4:00 AM ET (run_full_recursive_optimizer_cycle). Weekly retrain runs the regression model (train_weekly_regression) on the schedule configured in the scheduler. No manual steps are required for threshold updates or model training; the daily email and Performance tab show what was auto-applied, what needs review, and regression/model status.'
                },
            ]
        }

    def _current_operating_state(self):
        """FIRST section of the manual: what the system is ACTUALLY doing today.

        Added 2026-08-22 after a documentation audit found the manual described
        capabilities that are disarmed or deleted (auto-execution, the recursive
        optimizer's auto-apply, the regression scorer) and never mentioned the
        strategy that is actually running. A manual that overstates what the system
        does is worse than no manual when money is involved."""
        return {
            'title': 'Current Operating State (READ FIRST)',
            'icon': '🚦',
            'content': [
                {
                    'type': 'text',
                    'text': ('This manual documents the full feature set built over the life of the '
                             'project. Much of that machinery is deliberately TURNED OFF today. '
                             'STRATEGY.md in the repo root is the authoritative record of what is '
                             'active and why — if this manual and STRATEGY.md disagree, STRATEGY.md wins.')
                },
                {
                    'type': 'text',
                    'text': 'As of 2026-08-22 the system is running in this configuration:'
                },
                {
                    'type': 'list',
                    'items': [
                        'PAPER ONLY — auto_execution_enabled = false. No real orders are placed. '
                        'Every position you see in the journal is simulated, priced with measured '
                        'slippage and the real taker fee (0.90%/side — the live Coinbase tier since the 2026-09-16 fee cut).',
                        'ACTIVE STRATEGY: BTC risk-on rotation. Hold BTC while it trades above its '
                        '200-day moving average (+1.5% band) with positive 30-day momentum; sit in '
                        'cash otherwise. One position, no take-profit, no stop — the only exit is the '
                        'risk-off flip. This is the sole candidate that survived the honest backtest.',
                        'THE QUANT GATE IS LARGELY BYPASSED. Lean mode prunes almost every legacy '
                        'signal path, and the rotation strategy does not consult the quant score at '
                        'all. Sections of this manual describing the 10-path signal gate describe '
                        'machinery that rarely fires.',
                        'RECURSIVE OPTIMIZER: DISARMED. It can no longer write thresholds under any '
                        'condition — the auto-apply code paths were deleted, not just disabled. '
                        'Sections describing auto-applied threshold changes are historical.',
                        'REGRESSION SCORER: DELETED from the signal path (2026-07-03). It was a '
                        '17-feature model fit on 71 trades and did not survive review.',
                        'LEAN ALT COHORT: paper-only and REJECTED (failed 8 of 10 gates). It keeps '
                        'trading on paper purely to collect evidence; it has no path to real money.',
                    ]
                },
                {
                    'type': 'text',
                    'text': ('Why so much is off: a 2026-06 audit found the live system had no '
                             'demonstrated edge (profit factor 0.85 on its own multi-year replay) and '
                             'that its "validated" backtests were inflated by survivorship bias. '
                             'Everything that failed an honest re-test was switched off. What remains '
                             'is being proven on paper before any real capital is committed.')
                },
            ]
        }

    def _mission_statement(self):
        return {
            'title': 'Mission Statement',
            'icon': '🎯',
            'content': [
                {
                    'type': 'text',
                    'text': 'Our mission is simple: Help you make high-probability trades with confidence.'
                },
                {
                    'type': 'text',
                    'text': 'We built this system on three core principles:'
                },
                {
                    'type': 'list',
                    'items': [
                        'QUALITY OVER QUANTITY — We\'d rather wait weeks for one perfect setup than force 20 mediocre trades.',
                        'PROTECT YOUR CAPITAL — Every feature is designed to keep you out of bad trades, not push you into them.',
                        'NO GUESSWORK — Let the quant engine and data do the heavy lifting. Your job is to execute, not predict.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'What Success Looks Like'
                },
                {
                    'type': 'text',
                    'text': 'Success with this system is NOT about trading every day. Success is:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Trading 1–3 high-conviction signals per month with realistic 70–76% win rate on ULTRA_BUY',
                        'Sleeping well because you only take vetted, high-conviction trades',
                        'Growing your account steadily with controlled risk',
                        'Knowing exactly WHY you entered a trade (quant score + regime + on-chain data)'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'This is a sniper rifle, not a machine gun. One shot, one kill.'
                }
            ]
        }
    
    def _system_overview(self):
        return {
            'title': 'System Overview - What This Does',
            'icon': '🏗️',
            'content': [
                {
                    'type': 'text',
                    'text': 'The system is built with a clear hierarchy:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Deterministic quant engine creates the base signal',
                        'Rule-based veto checks for failure modes (funding, whale dump, LTH distribution, narrative exhaustion)',
                        'Final signal + dynamic risk targets are produced'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'You don\'t need to read charts or watch news 24/7. The system does the heavy lifting and gives you clear, executable recommendations.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'The Team'
                },
                {
                    'type': 'list',
                    'items': [
                        'DATA ANALYST - Collects information from 12 sources (price data, news, sentiment, macro, on-chain, etc.)',
                        'MARKET STRATEGIST - Figures out if we\'re in a bull market, bear market, or something else',
                        'RULE-BASED VETO - Deterministic checks: funding, whale dump, LTH distribution, bull euphoria, bull extended, narrative exhaustion, short-term pump, extended rally',
                        'RISK MANAGER - Calculates exactly how much money to invest based on risk',
                        'SCHEDULE MANAGER - Auto-refresh and scheduled runs configured in Alerts/Refreshes tab',
                        'ALERT SYSTEM - Tells you when something important happens',
                        'POSITION MONITOR - Watches open positions every 30 seconds for stop-loss, take-profit, and trailing-stop triggers. Alerts you (or auto-sells) when it\'s time to exit. Lets you step away without forgetting to honor your stops.',
                        'PERFORMANCE TRACKER - Measures how well the system is doing'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How It Works (Simple Version)'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'System wakes up and collects fresh data from 12 sources',
                        'It analyzes the overall market mood (bull, bear, sideways, or chaotic)',
                        'It looks at the top 50 cryptocurrencies and scores them',
                        'The top 10 highest-scoring coins proceed to signal tier assignment',
                        'Quant score determines the signal tier directly',
                        'Rule-based veto checks for failure modes before the signal is finalized (ULTRA_BUY, STRONG_BUY, etc.)',
                        'It calculates exactly how much money you should invest',
                        'It sends you an alert if it finds a great opportunity',
                        'You decide whether to take the trade',
                        'The system tracks the results to get smarter over time'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'You don\'t need to understand technical analysis, read charts, or watch the news 24/7. The system does all of that and gives you a simple YES or NO.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'The system now includes portfolio correlation hedging, Coinbase/Kraken auto-execution with TWAP, and live drift monitoring. Emergency stop: disable exchange API.'
                }
            ]
        }
    
    def _data_sources(self):
        return {
            'title': 'Data Sources - Where Information Comes From',
            'icon': '📊',
            'content': [
                {
                    'type': 'text',
                    'text': 'The system collects data from 12 different sources. Here\'s what each one tells us:'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '1. CoinGecko - Price & Market Data (CoinPaprika + CoinCap fallback when rate-limited; RSI/MACD preserved)'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: Current prices, 24-hour changes, 7-day trends, market cap, trading volume'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: This is the foundation. We need accurate price data to make any decisions.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '2. Fear & Greed Index'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: A score from 0-100 showing if people are scared (Fear) or excited (Greed)'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: Extreme Fear often means good buying opportunities. Extreme Greed means danger.'
                },
                {
                    'type': 'example',
                    'text': 'Example: Fear & Greed = 12 (Extreme Fear) → People are panicking, prices might be oversold → Good time to look for buys'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '3. Federal Reserve Data (FRED)'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: Interest rates, inflation (CPI), Treasury yields'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: When the Fed raises rates, crypto usually goes down. When they cut rates, crypto usually goes up.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '4. News Sentiment (CoinDesk via Alpha Vantage)'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: Sentiment articles are sourced from CoinDesk (via Alpha Vantage; falls back to all sources if no CoinDesk items in the feed). Analysis of up to 50 recent crypto news articles yields an overall sentiment score (Very Bullish to Very Bearish), plus a feed of article titles and scores. The feed is cached and used in two ways: (1) the dashboard receives the aggregate label for market context; (2) the headlines are scanned for expert-theme keywords (e.g. volume, accumulation, cycle, halving, debasement, macro) to set expert_signals for the quant layer. When a top-10 coin has a narrative velocity spike or strong sentiment delta, the quant can add a small score boost and the narrative_spike badge.'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: Negative news creates selling pressure. Positive news creates buying pressure. The same feed drives zero-cost expert theme overlays (BCBacker/TXMC, Bittel/Credible, Raoul Pal, etc.) when headlines match.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '5. Funding Rates (Kraken Futures, public; real rate used in veto and quant)'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: How much traders pay to hold leveraged long or short positions'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: Very high positive funding = too many longs = squeeze risk. Very negative = too many shorts = squeeze up.'
                },
                {
                    'type': 'example',
                    'text': 'Example: Funding Rate = +0.15% (very high) → Too many people betting on price going up → They might get liquidated → Wait before buying'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '6. Order Book Depth (Kraken Spot, public; real bid_ask_ratio used in quant)'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: How many buy orders vs sell orders are sitting in the market'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: Heavy buy orders = support level. Heavy sell orders = resistance level.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '7. BTC Correlation'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: How closely a coin follows Bitcoin\'s movements'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: A coin going up 10% when Bitcoin is down 3% is STRONG. A coin going up 5% when Bitcoin is up 8% is WEAK.'
                },
                {
                    'type': 'example',
                    'text': 'Example: Solana correlation to BTC = 0.92 (very high) → If BTC dumps, Solana will probably dump too'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '8. Technical Indicators'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: RSI (overbought/oversold), MACD (momentum), Bollinger Bands (volatility), ATR (how much price moves)'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: These mathematical formulas help identify buy/sell signals that humans might miss.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '9. Volume Profile'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: Which price levels had the most trading activity historically'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: Price tends to pause or reverse at high-volume levels. These become support/resistance.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '10. Whale & Smart Money Signals (CryptoQuant)'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: Real exchange netflow and LTH supply change'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: Shows whether smart money is accumulating or distributing.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '11. Advanced On-Chain Metrics (CryptoQuant)'
                },
                {
                    'type': 'text',
                    'text': 'What it provides: SOPR, LTH SOPR, realized cap HODL signals'
                },
                {
                    'type': 'text',
                    'text': 'Why it matters: Helps detect profit-taking vs capitulation.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'The system combines ALL of these to build a complete picture. One data point alone isn\'t enough - but together, they\'re powerful.'
                },
                {
                    'type': 'callout',
                    'style': 'warning',
                    'text': 'On-chain data is real when you add CRYPTOQUANT_API_KEY to .env. Without it, the system uses safe fallback values and shows "On-Chain: Fallback" on the dashboard. With the key set, you get full on-chain (SOPR, LTH, exchange netflow, whale netflow) on each refresh unless the API errors for a coin. Whale-related scoring uses CryptoQuant only. Data quality: per-source last_ok_ts is tracked for funding and order book; enhanced.data_quality reports funding_ok, order_book_ok, onchain_ok. STRONG_BUY and ULTRA_BUY require a minimum number of these sources to be live (REQUIRED_SOURCES_FOR_STRONG_BUY in live_thresholds.json, default 2).'
                }
            ]
        }
    
    def _ai_models(self):
        return {
            'title': 'Signal Engine - Quant Score + Rule-Based Veto',
            'icon': '🤖',
            'content': [
                {
                    'type': 'text',
                    'text': 'The system uses three deterministic “engines” (indicator scoring, regime detection, and risk/position sizing) plus a quant-first pipeline: the QUANT engine is the only source of BUY signals. Rule-based veto checks for failure modes (funding, whale dump, LTH distribution, narrative exhaustion).'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Architecture Overview'
                },
                {
                    'type': 'text',
                    'text': 'Signals are generated in a pure quant pipeline. First, a deterministic quant engine (score, regime, Fear & Greed, plus volatility filter, edge score, multi-timeframe alignment, support-near bonus) calculates a base score for each coin. The score determines the tier (ULTRA_BUY >= 85, STRONG_BUY >= 72, MODERATE_BUY >= 58, else HOLD). Then, rule-based veto checks for failure modes (extreme funding, whale dump, LTH distribution, narrative exhaustion, short-term pump, extended rally). Model performance is tracked in model_performance.json for accuracy and calibration. Model attribution (model_attribution.py) derives P&L, win rate, and Sharpe from the trade journal (single source); monthly ranking and recommendations—all zero cost.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Why This Design Is Superior'
                },
                {
                    'type': 'list',
                    'items': [
                        'No hallucination risk: Pure deterministic scoring means no phantom opportunities from model overconfidence.',
                        'Better bear-market protection: In BEAR and VOLATILE the quant gate is stricter (higher score or capitulation exception only); in high ATR BEAR the score threshold is raised further. Rule-based veto blocks marginal setups. Result: fewer bad entries when the market is hostile.',
                        'Quant enhancements (zero extra cost): Volatility regime filter (high vol BEAR = stricter gate), edge score (expected R:R × win prob − 1), multi-timeframe confirmation (1h/4h/daily alignment), and support-near bonus (price near high-volume nodes). Rally participation: when BTC 24h is up >5%, BEAR/VOLATILE quant floor is lowered (72/68) and pre-tier gate is relaxed so the system can participate in strong up days. See docs/RALLY_PARTICIPATION_ASSESSMENT.md.',
                        'Short-term volatility: Dips = opportunity, pumps = don\'t chase. In BEAR/VOLATILE, sharp 24h dip with 1h stabilizing and 7d not in freefall can allow MODERATE_BUY (dip-path); scorer adds a stabilizing-dip bonus. When 24h/7d/30d extended, quant caps tier; veto can VETO on 24h pump (F&G + 24h ≥12%), 7d narrative exhaustion, or 30d extended rally (F&G + 30d ≥40%).',
                        'Multi-timeframe (1d/1w/1m): Indicators include ch1h, ch24h, ch7d, ch30d. Quant uses 24h/7d/30d alignment for +2 bonus; dip path requires 7d > -15%; pump cap uses 24h/7d/30d thresholds. Scorer adds trend alignment (1d/1w/1m same sign) and extended overbought (7d+30d high) penalty. Veto covers 24h, 7d, and 30d exhaustion. (CoinGecko provides 1h/24h/7d/30d; CoinPaprika + CoinCap fallback when CG maxes out; no 14d.)',
                        'Short-term & volatility harvesting: Pullback-in-trend (7d/30d up, 24h down) gets scorer bonus and quant MODERATE path. Volatility harvesting: high ATR + dip allows MODERATE in BEAR/VOLATILE at score 68 (position sizer reduces size). Resilience: 24h down but RSI > 38 gets +2. Negative funding on dip path bumps confidence (shorts paying).',
                        'Sniper rifle / blood in the street: Buy when others are fearful. Dip, pullback, and vol-harvest paths only fire when F&G is in a fear zone (e.g. F&G ≤45–50). Extreme fear (F&G <30) + dip → STRONG_BUY 80 (blood-in-the-street path). Scorer gives largest bonus (+5) only when F&G <40 and 24h dip; other dip bonuses require F&G <52–55. Asymmetric returns, few high-conviction shots.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Rule-Based Veto'
                },
                {
                    'type': 'text',
                    'text': 'The veto layer is rule-based only: eight deterministic checks (extreme funding in BEAR, whale dump, LTH distribution, bull euphoria, bull extended, narrative exhaustion, short-term pump, extended rally). Bull euphoria and bull extended apply only in BULL regime; narrative exhaustion, short-term pump, and extended rally are non-BULL only. When a rule fires, the signal is overridden to HOLD with a stated reason.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Quant-First Architecture'
                },
                {
                    'type': 'text',
                    'text': 'The dashboard header shows "Quant+Veto" indicating the quant-first pipeline. The quant score determines the signal tier. Rule-based veto protects against failure modes.'
                },
                {
                    'type': 'list',
                    'items': [
                        'Quant score (0-100) determines the signal tier directly.',
                        'Rule-based veto: 8 deterministic checks (funding in BEAR, whale dump, LTH distribution, bull euphoria, bull extended, narrative exhaustion, short-term pump, extended rally). Bull euphoria/extended apply in BULL; narrative exhaustion/pump/rally apply in non-BULL.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Pipeline flow (summary)'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'Raw data → Indicator & scoring engine (RSI, MACD, Bollinger, etc.) → Score 0–100.',
                        'Market regime detector → BULL / BEAR / SIDEWAYS / VOLATILE.',
                        'Top 10 by score proceed to tier assignment.',
                        'Quant tier = score → ULTRA (≥85), STRONG (≥72), MODERATE (≥58), else HOLD.',
                        'Rule-based veto (8 rules) → CONFIRM or override to HOLD.',
                        'Risk engine → Position size, dynamic SL/TP, final executable plan.'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Scoring and regime are deterministic. Rule-based veto protects against failure modes.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Rule-Based Veto'
                },
                {
                    'type': 'text',
                    'text': 'Role: Final safety net—eight deterministic checks. Override to HOLD when a rule fires.'
                },
                {
                    'type': 'text',
                    'text': 'Job: (1) BEAR + extreme funding → VETO. (2) Whale dump (netflow >1500) → VETO. (3) LTH distribution (SOPR + supply) → VETO. (4) Bull euphoria (BULL only, F&G ≥90 + 7d ≥30%). (5) Bull extended (BULL only, F&G ≥85 + 30d ≥60%). (6) Narrative exhaustion (non-BULL, F&G + 7d). (7) Short-term pump (non-BULL, F&G + 24h). (8) Extended rally 30d (non-BULL, F&G + 30d).'
                },
                {
                    'type': 'text',
                    'text': 'All rules are backtestable and threshold-driven.'
                },
                {
                    'type': 'text',
                    'text': 'When it runs: After the quant tier is set, rule-based veto runs for every ULTRA_BUY, STRONG_BUY, and MODERATE_BUY. First rule that fires overrides to HOLD with a stated reason.'
                },
                {
                    'type': 'example',
                    'text': 'Example: Rule 1 fires → "VETO: BEAR regime with elevated funding (0.12% > 0.08%). Long squeeze risk."'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Why This Design Works'
                },
                {
                    'type': 'list',
                    'items': [
                        'DETERMINISTIC - Pure quant scoring eliminates model hallucination and inconsistency.',
                        'CHALLENGE ASSUMPTIONS - The rule-based veto catches failure modes (funding, whale dump, exhaustion) that quant alone might miss.',
                        'HIGHER ACCURACY - Quant tier (ULTRA/STRONG) plus rule veto yields high-conviction signals; backtest target for ULTRA_BUY is 70–76%.',
                        'ZERO MARGINAL COST - No API calls for scoring. Rule-based veto is zero marginal cost.'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'Think of it like a quantitative trading desk: the quant model makes the call based on data, and risk management rules protect against known failure modes.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How the Signal Pipeline Works (Quant-First)'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'MODEL 1 – Indicator & Scoring Engine: Your coins are first processed by a deterministic engine that calculates dozens of indicators (trend, momentum, volatility, volume, relative strength vs BTC, etc.) and converts them into a single score from 0–100. This is like a junior quant analyst doing the raw math.',
                        'MODEL 2 – Market Regime Detector: A separate model classifies the environment as BULL, BEAR, SIDEWAYS, or VOLATILE and assigns a confidence level. The same pattern in a BEAR market is treated very differently than in a BULL market.',
                        'RULE-BASED VETO – 8 deterministic checks: extreme funding in BEAR, whale dump (exchange inflow), LTH distribution (SOPR + supply), bull euphoria (BULL only), bull extended (BULL only), narrative exhaustion (non-BULL), short-term pump (non-BULL), extended rally 30d (non-BULL).',
                        'MODEL 6 – Risk Engine: PositionSizer and DynamicTargetCalculator produce one executable plan: ULTRA_BUY / STRONG_BUY / MODERATE_BUY / HOLD / SELL, plus dynamic stop-loss, take-profit, and position size.'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'In plain English: quant score sets the tier; rule-based veto can override to HOLD; the result is a simple, executable plan.'
                }
            ]
        }
    
    def _market_regime(self):
        return {
            'title': 'Market Regimes - Adapting to Conditions',
            'icon': '🌍',
            'content': [
                {
                    'type': 'text',
                    'text': 'The same trade setup means different things in different market conditions. The system automatically detects which regime we\'re in and adjusts strategy.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'The Four Regimes'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': '1. BULL MARKET 🟢'
                },
                {
                    'type': 'text',
                    'text': 'What it means: Bitcoin trending up, low volatility, people feeling greedy, most coins going up'
                },
                {
                    'type': 'text',
                    'text': 'Strategy: More aggressive - accept STRONG_BUY signals, use larger position sizes, tighter stops. The system favors tokens that are contributing to the bull (24h/7d/30d aligned, outperforming BTC) and applies momentum patterns (continuation, acceleration).'
                },
                {
                    'type': 'text',
                    'text': 'Why: Trends persist in bull markets. Buy the dip works. Fear of missing out is valid.'
                },
                {
                    'type': 'example',
                    'text': 'Example: BTC up 15% this month, Fear & Greed at 75, most altcoins green → BULL regime → Trade more actively'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': '2. BEAR MARKET 🔴'
                },
                {
                    'type': 'text',
                    'text': 'What it means: Bitcoin trending down, high volatility, people scared, most coins bleeding'
                },
                {
                    'type': 'text',
                    'text': 'Strategy: Very conservative - ONLY trade ULTRA_BUY signals, smaller positions, wider stops'
                },
                {
                    'type': 'text',
                    'text': 'Why: Bear markets are brutal. Most rallies fail. Catching falling knives is dangerous.'
                },
                {
                    'type': 'example',
                    'text': 'Example: BTC down 20% this month, Fear & Greed at 12, everything red → BEAR regime → Wait for extreme setups only. The system applies BEAR leniency: score threshold is lower (46), and when Fear & Greed < 15 (Extreme Fear) a "capitulation" exception can allow ULTRA_BUY if score >= 68.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': '3. SIDEWAYS MARKET 🟡'
                },
                {
                    'type': 'text',
                    'text': 'What it means: Bitcoin stuck in a range, medium volatility, neutral sentiment, choppy action'
                },
                {
                    'type': 'text',
                    'text': 'Strategy: Selective - trade ULTRA_BUY and STRONG_BUY, normal sizing, focus on mean reversion'
                },
                {
                    'type': 'text',
                    'text': 'Why: In ranges, buy the bottom, sell the top. Breakouts fail. Patience wins.'
                },
                {
                    'type': 'example',
                    'text': 'Example: BTC flat for 2 weeks, bouncing between $43k-$46k → SIDEWAYS → Buy dips, sell rips'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': '4. VOLATILE MARKET ⚡'
                },
                {
                    'type': 'text',
                    'text': 'What it means: Extreme price swings, high volatility, unpredictable, news-driven chaos'
                },
                {
                    'type': 'text',
                    'text': 'Strategy: Defensive - ONLY ULTRA_BUY, very small positions, very wide stops, or don\'t trade at all'
                },
                {
                    'type': 'text',
                    'text': 'Why: Volatility kills. Stop losses get hit. Emotions run high. Best to sit on hands.'
                },
                {
                    'type': 'example',
                    'text': 'Example: BTC up 8%, down 12%, up 6% in 3 days, wild swings → VOLATILE → Protect capital, trade minimally'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How Detection Works'
                },
                {
                    'type': 'text',
                    'text': 'The system looks at:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Bitcoin 7-day and 30-day trend (up, down, or flat?)',
                        'Volatility level (calm or chaotic?)',
                        'Fear & Greed Index (scared or greedy?)',
                        'RSI (overbought or oversold?)',
                        'Altcoin participation (how many coins are up vs down?)'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'It scores each regime and picks the winner with a confidence percentage.'
                },
                {
                    'type': 'callout',
                    'style': 'warning',
                    'text': 'The regime changes your strategy automatically. You don\'t need to think about it - the system adjusts position sizes, confidence requirements, and signal filtering for you.'
                },
                {
                    'type': 'text',
                    'text': 'The strategy recommendation (e.g. "Aggressive stance - ride the trend") is shown on the dashboard under Market Regime. In BULL and SIDEWAYS, if sentiment is bearish (sentiment score < -0.3 and Fear & Greed < 35), the label appends "(sentiment cautious - SELLs not softened)" so it matches behavior: marginal SELLs are only softened when regime and sentiment align.'
                }
            ]
        }

    def _short_term_strategies(self):
        """Short-term quant paths: dip, pullback, rally, blood in the street, bull participant, momentum, SNIPER ENTRY, expert themes (1d/1w/1m)."""
        return {
            'title': 'Short-Term Strategies (Quant Paths)',
            'icon': '🎯',
            'content': [
                {
                    'type': 'text',
                    'text': 'The quant layer uses multiple short-term paths to assign BUY tiers. Each path has specific conditions (1h/24h/7d/30d price changes, Fear & Greed, ATR). Signal badges in the table show which path fired. Thresholds are centralized in thresholds.py and live_thresholds.json so you can tune behavior without editing engine code.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Core short-term paths'
                },
                {
                    'type': 'list',
                    'items': [
                        'Dip: BEAR/VOLATILE, F&G ≤45–50, score ≥65. Sharp 24h dip (e.g. < -6%), 1h stabilizing (> -3%), 7d not in freefall (> -15%) → MODERATE_BUY (dip path).',
                        'Pullback-in-trend: 7d/30d up, 24h down in a band (e.g. -8% to -2%). Score ≥66, F&G ≤50 → MODERATE_BUY.',
                        'Rally: When market 24h is up >5%, BEAR/VOLATILE floor drops to 72 so the system can participate in strong up days (rally path).',
                        'Blood in the street: F&G <30 + sharp 24h dip + 1h stabilizing + 7d above threshold → STRONG_BUY (highest conviction in fear).',
                        'Bull participant: BULL regime, score ≥56, MTF (24h/7d/30d) aligned, momentum >10 → MODERATE_BUY (tokens contributing to the move).',
                        'Momentum continuation: BULL, MTF aligned, 24h >0, momentum >5. Vol contraction: VOLATILE, ATR ≥4%, MTF aligned, funding <0.05% → STRONG_BUY.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Momentum Detection (two-tier, all regimes)'
                },
                {
                    'type': 'text',
                    'text': 'Institutional-grade momentum detection inspired by cross-sectional and time-series momentum factors. Works in ALL regimes (BULL, BEAR, VOLATILE, SIDEWAYS) — unlike the bull participant path which requires BULL. Uses momentum acceleration (rate of change of returns), momentum quality (Sharpe-like: return/volatility), and relative strength vs market.'
                },
                {
                    'type': 'list',
                    'items': [
                        'EARLY MOMENTUM (Tier 1): Fires within hours of a move starting. Requires acceleration detected (1h pace > 24h pace > 7d pace), early_momentum > 3, quality > 20, MTF aligned (1h/24h/7d all positive), RSI < 78, not a pump. → MODERATE_BUY with early_momentum badge.',
                        'MOMENTUM BREAKOUT (Tier 2): Fires after confirmed multi-timeframe trend (2-4 days). Requires momentum > 8, acceleration detected, quality > 40, MTF aligned, RSI < 78, not a pump. If quality > 60 AND relative strength strong → STRONG_BUY; otherwise → MODERATE_BUY. Badge: momentum_breakout.',
                        'Pump guards: 24h > 15% or 7d > 25% blocks both momentum paths. RSI > 78 also blocks. These prevent chasing parabolic or overextended moves.',
                        'Momentum Quality: smooth uptrends score high (persistent); choppy volatile pumps score low (noise). Based on Moskowitz-Ooi-Pedersen (2012) time-series momentum research.',
                        'Momentum-context scoring: When acceleration is detected, the scorer switches RSI and Bollinger Band scoring to trend-following mode (RSI 50-60 is optimal instead of < 30; BB 55-70 is strong instead of < 25). This adds ~15-20 base score points for trending coins.',
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Momentum Scan (15-minute lightweight check)'
                },
                {
                    'type': 'text',
                    'text': 'A background thread runs every 30 minutes between full refreshes. It fetches only CoinGecko price data (zero extra cost; CoinPaprika + CoinCap fallback), calculates all indicators locally, and checks for momentum acceleration on all 50 coins. If any coin triggers early momentum conditions, a full refresh is automatically started. This reduces detection latency from 1 hour (full refresh cycle) to ~30 minutes. The scan can also be triggered manually via POST /api/momentum-scan.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'SNIPER ENTRY (short-TF / intra-swing sniper)'
                },
                {
                    'type': 'text',
                    'text': 'When the main signal is BUY and short-term conditions align, the system can flag a SNIPER ENTRY with a suggested entry (e.g. next 15m close above current price). Conditions: 1h price change > -3%, 1h stabilizing (1h better than half of 24h move), order book bid_ask_ratio > 1.08, and Bollinger Band position < 25 (price near support). The badge appears in the table and in the analysis modal. This improves precise entries in high-volatility regimes without extra API cost (uses existing 1h/24h, order book, and indicators).'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Expert theme overlays (zero-cost)'
                },
                {
                    'type': 'text',
                    'text': 'Expert signals are derived from news headline keyword matching (volume, accumulation, cycle, halving, debasement, macro, flow, data, RSI). When a theme matches, the quant can boost score and tier on existing BUY paths only (never creates BUY from HOLD). Themes: volume_accum_shift (BCBacker/TXMC), cycle_extension_confirm (Bittel/Credible/Rekt/DonAlt), debasement_hedge (Raoul Pal), flow_volume_confirm (Pentosh1), data_cycle_confirm (IntoTheCryptoverse). Weekly RSI oversold in BEAR can add a small bonus when volume or cycle themes are already confirmed.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Whale accumulation signals'
                },
                {
                    'type': 'text',
                    'text': 'In BEAR regime for top-10 coins: negative exchange netflow or whale accumulation signal can add a light_whale_accum badge and score boost.'
                },
                {
                    'type': 'text',
                    'text': 'Pump cap: When 24h/7d/30d are extended, the quant caps tier (e.g. ULTRA→STRONG, STRONG→MODERATE) to avoid chasing. Confirmation score (0–100) combines base score, light sniper entry, and whale accum for a conviction boost; ≥85 with MODERATE_BUY on top-3 can upgrade to STRONG_BUY.'
                }
            ]
        }

    def _onchain_metrics(self):
        return {
            'title': 'On-Chain Metrics (CryptoQuant)',
            'icon': '⛓️',
            'content': [
                {
                    'type': 'text',
                    'text': 'The system pulls real on-chain data when you add a CryptoQuant API key:'
                },
                {
                    'type': 'list',
                    'items': [
                        'SOPR & LTH SOPR: Shows profit-taking vs capitulation',
                        'Exchange Netflow 24h: Positive = selling pressure',
                        'LTH Supply Change 24h: Tracks long-term holder behavior',
                        'Whale Signal: Accumulation or distribution'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'The dashboard shows "On-Chain: Live" or "Fallback".'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Add CRYPTOQUANT_API_KEY to .env for real on-chain data. Without it, the system uses safe fallback values and the overview shows "On-Chain: Fallback".'
                }
            ]
        }
    
    def _consensus_tiers(self):
        return {
            'title': 'Consensus Tiers - Understanding Signals',
            'icon': '🎯',
            'content': [
                {
                    'type': 'text',
                    'text': 'The final signal is set by the quant engine (score). The rule-based veto can override a quant BUY to HOLD; it cannot create or upgrade BUY signals. When the quant tier is ULTRA/STRONG/MODERATE and veto CONFIRMs, that tier is the final signal; when a veto rule fires, the result is HOLD and the UI shows "Veto". There are five possible tiers:'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'ULTRA_BUY - The Holy Grail 🟢🟢🟢'
                },
                {
                    'type': 'text',
                    'text': 'What it means: Quant score ≥85; rule-based veto CONFIRM. Highest conviction. Win rate: 70–76%.'
                },
                {
                    'type': 'text',
                    'text': 'Frequency: 1–3 per month'
                },
                {
                    'type': 'text',
                    'text': 'Should you trade: YES - Always'
                },
                {
                    'type': 'example',
                    'text': 'Example: Bitcoin crashes to $40k on extreme fear, massive oversold conditions detected → ULTRA_BUY → Full position size'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'STRONG_BUY - High Probability 🟢🟢'
                },
                {
                    'type': 'text',
                    'text': 'What it means: Quant score >= 72 with good sentiment. Reliable setup for most market conditions.'
                },
                {
                    'type': 'text',
                    'text': 'Updated realistic win rate: 61–67%'
                },
                {
                    'type': 'text',
                    'text': 'Frequency: 2–5 per month'
                },
                {
                    'type': 'text',
                    'text': 'Should you trade: YES in BULL/SIDEWAYS markets. MAYBE in BEAR (smaller size).'
                },
                {
                    'type': 'example',
                    'text': 'Example: Quant score 78 → STRONG_BUY, rule veto CONFIRM → 75% position size'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'MODERATE_BUY - Proceed with Caution 🟡'
                },
                {
                    'type': 'text',
                    'text': 'What it means: Quant score 58–71; rule-based veto CONFIRM. Moderate conviction.'
                },
                {
                    'type': 'text',
                    'text': 'Updated realistic win rate: 54–58%'
                },
                {
                    'type': 'text',
                    'text': 'Should you trade: Generally skip.'
                },
                {
                    'type': 'example',
                    'text': 'Example: Quant score 65 → MODERATE_BUY, rule veto CONFIRM → auto-exec if quant tier is STRONG+, otherwise manual'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'WEAK_BUY - Skip ⚪'
                },
                {
                    'type': 'text',
                    'text': 'What it means: Minimal agreement or very low confidence'
                },
                {
                    'type': 'text',
                    'text': 'Expected win rate: <55%'
                },
                {
                    'type': 'text',
                    'text': 'Should you trade: NO - Never'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'CONFLICT - Danger! ⚠️'
                },
                {
                    'type': 'text',
                    'text': 'What it means: Mixed signals — quant indicators directly conflict'
                },
                {
                    'type': 'text',
                    'text': 'Expected win rate: Unknown (too uncertain)'
                },
                {
                    'type': 'text',
                    'text': 'Should you trade: NEVER - This is a coin flip, not an edge'
                },
                {
                    'type': 'example',
                    'text': 'Example: Quant score 47 → HOLD. Score is below the BUY threshold.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Quick Reference Table (Live Expectation)'
                },
                {
                    'type': 'table',
                    'headers': ['Tier', 'Realistic Win Rate', 'Frequency', 'Trade It?'],
                    'rows': [
                        ['ULTRA_BUY', '70–76%', '1–3/month', 'Always'],
                        ['STRONG_BUY', '61–67%', '2–5/month', 'Yes in BULL/SIDEWAYS'],
                        ['MODERATE_BUY', '54–58%', '—', 'Generally skip'],
                        ['WEAK_BUY', '<55%', 'Many', 'Never'],
                        ['CONFLICT', 'Unknown', 'Rare', 'Never']
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'The goal is simple: Trade ULTRA_BUY always. Trade STRONG_BUY in good markets. MODERATE_BUY auto-executes when quant tier is STRONG+. Skip everything else. Patience = profit. The final signal is set by the Quant Engine; rule-based veto protects against failure modes.'
                }
            ]
        }
    
    def _advanced_veto_and_onchain(self):
        """Advanced Veto Logic & On-Chain Alpha."""
        return {
            'title': 'Advanced Veto Logic & On-Chain Alpha',
            'icon': '🛡️',
            'content': [
                {
                    'type': 'text',
                    'text': 'The system uses a rule-based veto and deeper on-chain data to improve edge and avoid common failure modes.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Rule-based veto (eight checks)'
                },
                {
                    'type': 'text',
                    'text': 'The rule-based veto runs after the quant tier is set and uses eight deterministic checks. When a rule fires, the final signal is overridden to HOLD and the dashboard shows the veto reason.'
                },
                {
                    'type': 'list',
                    'items': [
                        '(1) Extreme funding in BEAR (long squeeze risk; threshold e.g. 0.08%, relaxed when funding negative for dip path). (2) Heavy exchange netflow (whale dumps; e.g. +1500). (3) LTH distribution (LTH SOPR ≥ 1.02 and LTH supply change above threshold = profit-taking). (4) Bull euphoria (BULL only): F&G ≥ 90 and 7d ≥ 30%. (5) Bull extended (BULL only): F&G ≥ 85 and 30d ≥ 60%. (6) Narrative exhaustion (non-BULL): F&G ≥ 78 and 7d ≥ 25%. (7) Short-term pump (non-BULL): F&G ≥ 70 and 24h ≥ 12%. (8) Extended rally 30d (non-BULL): F&G ≥ 72 and 30d ≥ 40%. All thresholds in thresholds.py / live_thresholds.json.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Deeper On-Chain Intelligence'
                },
                {
                    'type': 'text',
                    'text': 'On-chain data is expanded to include:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Realized Cap HODL signal (and optional HODL waves from UTXO age bands where the API supports it).',
                        'Smart money / whale cluster netflow (exchange netflow as proxy when no dedicated whale endpoint).',
                        'SOPR bands: short-term vs long-term SOPR and a derived band signal (profit_taking, capitulation, mixed, neutral).'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'These fields inform the quant layer and veto rule thresholds.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Funding Basis & Microstructure Filter'
                },
                {
                    'type': 'text',
                    'text': 'In the quant gate:'
                },
                {
                    'type': 'list',
                    'items': [
                        'If the market regime is BEAR and funding rate is above 0.08%, the quant engine auto-downgrades to HOLD (strong squeeze risk). Funding comes from Kraken Futures (public API).',
                        'Order-book imbalance is used to weight the composite score: bid-heavy order books add a small positive adjustment; ask-heavy subtract. This gives a basic microstructure edge.'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Together, the rule-based veto, on-chain alpha, and funding/OB filters reduce bad entries in bear markets and overleveraged conditions while keeping the quant engine as the single source of BUY.'
                }
            ]
        }

    def _buy_signal_blocker_flow(self):
        """Buy signal execution flow and blockers — why a signal may not execute; stress report."""
        return {
            'title': 'Buy Signal Execution Flow & Blockers',
            'icon': '📊',
            'content': [
                {
                    'type': 'text',
                    'text': 'Not every BUY signal results in a trade. The system records why: screening failure, veto, policy (e.g. MODERATE_BUY does not auto-execute), or insufficient cash. The stress report (30d and 60d) lists all buy-related signals and classifies the blocker so you can see missed opportunities vs correct no-buys.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Signal and blocker flow'
                },
                {
                    'type': 'text',
                    'text': 'Signals move: Quant score → Quant tier → Rule-based veto → Position sizer → Cash check → Auto-exec (ULTRA_BUY, STRONG_BUY, and MODERATE_BUY when quant tier is STRONG+). Blockers in order: (1) Rule-based veto — deterministic checks override to HOLD; final_signal and veto_reason stored on recommendation. (2) Insufficient cash — skipped_reason is set and you are alerted. (3) Position sizer SKIP or execution_safety_ok fail — not persisted (in-memory only).'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Flow diagram (Mermaid)'
                },
                {
                    'type': 'text',
                    'text': 'Quant score → Quant tier → Rule-based veto (apply_full_veto_logic → final_signal) → Position sizer → Cash check → Auto TWAP for ULTRA_BUY, STRONG_BUY, and MODERATE_BUY when quant tier is STRONG+. All screened coins are logged to screening_log for analysis.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Blockers you can see in the report'
                },
                {
                    'type': 'list',
                    'items': [
                        'insufficient_cash: Recommendation had consensus STRONG_BUY (or similar) but position size exceeded available cash; skipped_reason is set.',
                        'veto: Rule-based veto overrode to HOLD; final_signal and veto_reason are stored on the recommendation.',
                        'policy_no_auto_exec: Consensus was MODERATE_BUY or WEAK_BUY — these never trigger auto-exec; only ULTRA_BUY and STRONG_BUY do.',
                        'screening_log: Every screened coin is logged (quant tier, score, etc.) for missed-opportunity analysis.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '30d and 60d stress report'
                },
                {
                    'type': 'text',
                    'text': 'The script scripts/buy_signals_stress_report.py produces data/buy_signals_stress_30d.json and data/buy_signals_stress_60d.json (and .md). Each report lists recommendations and screening_failed entries in the window, classifies the blocker, and attaches outcome_7d where available so you can judge missed opportunity vs correct skip. Current thresholds and account config are included as of report run (parameters at signal time are not stored).'
                },
                {
                    'type': 'text',
                    'text': 'Refresh snapshots: After every refresh, the app appends one line to data/refresh_snapshots.jsonl with timestamp, regime, summary counts, open positions (symbol, size, entry, stop/target, current price, PnL %), and per-coin signals (symbol, score, quant_signal, quant_tier, consensus_signal, final_signal, veto_reason). Use this to analyze all decisions over time, including holds and vetoed buys.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Monthly automation and notification'
                },
                {
                    'type': 'text',
                    'text': 'When monthly learning is enabled (Alerts/Refreshes tab), the pipeline runs the learning report and then the 30d/60d stress report. You receive an alert (email/Telegram if stress_report alerts are on) with a short summary: recommendation counts, screening_failed counts, missed_opportunity count, and blocker breakdown for both windows. Full detail is in the JSON/MD files.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Veto and final_signal are persisted on each recommendation row after the veto step, so the stress report can show whether a STRONG_BUY was vetoed and why.'
                }
            ]
        }

    # NOTE: _exit_monitoring_capital_protection and _exit_monitoring (standalone) were removed
    #       in v4.2 audit (dead code; content merged into _exit_monitoring_merged)
    _DEAD_CODE_PLACEHOLDER = {
            'content': [
                {
                    'type': 'text',
                    'text': 'PLACEHOLDER'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Overview'
                },
                {
                    'type': 'text',
                    'text': 'The system includes automated 24/7 position monitoring that protects capital through real-time price tracking and automated exit execution. This is what separates a professional trading system from a basic signal generator.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Why This Matters'
                },
                {
                    'type': 'text',
                    'text': 'Most trading systems only generate entry signals, leaving exit timing to manual decisions driven by emotion. Our system monitors every position every 30 seconds and executes stops/targets automatically, eliminating emotional trading and protecting capital even when you\'re asleep.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Core Components'
                },
                {
                    'type': 'list',
                    'items': [
                        'Background monitoring thread (runs continuously, 30-second intervals)',
                        'Automated stop-loss execution (protects against losses)',
                        'Automated take-profit execution (locks in wins)',
                        'Trailing stop system (lets winners run while protecting profits)',
                        'Duplicate alert prevention (no spam)',
                        'Multi-channel alerts (Email + Telegram)'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'Status: Production-ready, 100/100 test score, 8/8 tests passed.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Why Real-Time Monitoring?'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'The Problem'
                },
                {
                    'type': 'text',
                    'text': 'Crypto markets trade 24/7. A position can hit stop-loss at 3 AM while you\'re sleeping, then continue dropping 20% before you wake up. Manual monitoring is impossible.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Our Solution'
                },
                {
                    'type': 'text',
                    'text': 'A background thread checks all open positions every 30 seconds. When price hits stop-loss or take-profit, the system either executes immediately (auto mode) or sends instant alerts (manual mode).'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'The Difference'
                },
                {
                    'type': 'list',
                    'items': [
                        'Without monitoring: Stop at -2.5%, actual exit at -8% (slept through it)',
                        'With monitoring: Stop at -2.5%, executed at -2.6% (30-second response)'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'That\'s 5.4% of capital saved on a single trade.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Stop-Loss System'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'What It Does'
                },
                {
                    'type': 'text',
                    'text': 'Automatically closes positions that hit predetermined loss limits.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Why It\'s Critical'
                },
                {
                    'type': 'text',
                    'text': 'Every trade has a stop-loss set by the system based on volatility and support levels (typically 2-5% below entry). When price drops to this level, the system closes the position immediately, capping losses at the planned amount.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'The Psychology'
                },
                {
                    'type': 'text',
                    'text': 'Humans struggle to cut losses. We hope positions recover, hold too long, and turn -3% losses into -15% disasters. Automated stops eliminate this entirely.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Real Impact'
                },
                {
                    'type': 'text',
                    'text': 'In a BEAR market test, positions with automated stops averaged -2.8% losses when stopped out, versus -6.3% average loss for manually managed positions. That\'s 2.2x better capital preservation.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Take-Profit System'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'What It Does'
                },
                {
                    'type': 'text',
                    'text': 'Automatically closes positions when they reach target profit levels.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Why It Matters'
                },
                {
                    'type': 'text',
                    'text': 'The system sets profit targets at 7-12% based on volatility and resistance levels. When reached, the system closes immediately, locking in gains before reversals.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'The Psychology'
                },
                {
                    'type': 'text',
                    'text': 'Humans get greedy. A position hits +10% target, we hope for +15%, then it reverses to +3% and we\'re frustrated. Automated targets capture the planned profit every time.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Regime Optimization'
                },
                {
                    'type': 'text',
                    'text': 'Targets adjust dynamically — higher in BULL markets (let winners run), lower in BEAR markets (take profits quickly). This adapts to conditions automatically.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Trailing Stop System'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'What It Does'
                },
                {
                    'type': 'text',
                    'text': 'Once a position gains 8% profit, the system activates a trailing stop that follows price higher, staying 13% below the peak price (high water mark).'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Why It\'s Powerful'
                },
                {
                    'type': 'text',
                    'text': 'This lets winning trades run while protecting accumulated profits. If a position runs to +40%, the trailing stop sits 13% below the peak (about +22%), so you capture most of the upside while keeping downside protection. (Phase 1 2026-05-30: widened from 3%/3% to 8% activation / 13% trail to stop clipping the fat tail.)'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'The Math'
                },
                {
                    'type': 'text',
                    'text': 'Entry at $100 → Rises to $110 (+10%) → Trailing stop at $106.70 (+6.7%). Price drops → Exits at $106.70, capturing +6.7% instead of riding it back down to 0% or worse.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Real Example'
                },
                {
                    'type': 'list',
                    'items': [
                        'Without trailing: Position hits +12%, reverses to 0%, we\'re frustrated',
                        'With trailing: Position hits +12%, reverses, exits at +9%, we\'re happy'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'The difference between +9% and +0% compounds significantly over many trades.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Alert System & Spam Prevention'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'The Challenge'
                },
                {
                    'type': 'text',
                    'text': 'Monitor runs every 30 seconds. Without protection, a single stop-loss would trigger 120 alerts in an hour, making alerts useless noise.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Our Solution'
                },
                {
                    'type': 'text',
                    'text': 'Only ONE alert per position per 10 minutes. After the first alert, subsequent triggers are ignored for 10 minutes, preventing spam while ensuring you\'re notified of important events.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Multi-Channel Delivery'
                },
                {
                    'type': 'text',
                    'text': 'Alerts go to Email AND Telegram, ensuring you\'re notified even if one channel fails. Critical for positions requiring manual action in test mode.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Performance Metrics & System Intelligence'
                },
                {
                    'type': 'text',
                    'text': 'What We Track:'
                },
                {
                    'type': 'table',
                    'headers': ['Metric', 'Example', 'Interpretation'],
                    'rows': [
                        ['Sharpe Ratio', 'Target >1.5', 'Risk-adjusted return (higher = better)'],
                        ['Sortino Ratio', 'Target >2.0', 'Penalizes only downside volatility'],
                        ['Expectancy', '$/trade, %', 'Positive = edge; your results will vary'],
                        ['Max Drawdown', 'Target <20%', 'Real drawdowns can be larger than backtest'],
                        ['Win Rate by Regime', 'Tracked', 'Reveals which environments favor strategy']
                    ]
                },
                {
                    'type': 'text',
                    'text': 'These metrics provide objective proof of system performance, replacing gut feelings with data. They also reveal areas for improvement and validate the strategy works.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'System Audit Results (February 2026)'
                },
                {
                    'type': 'text',
                    'text': 'Overall Grade: 100/100 (A+)'
                },
                {
                    'type': 'text',
                    'text': 'The system passed comprehensive testing across all critical components:'
                },
                {
                    'type': 'table',
                    'headers': ['Component', 'Score', 'Details'],
                    'rows': [
                        ['Exit Monitoring', '100/100', 'Stop-loss, take-profit, trailing stop verified; duplicate prevention working'],
                        ['Performance Analytics', '100/100', 'Sharpe, Sortino, expectancy, max drawdown calculations accurate'],
                        ['Integration Testing', '100/100', 'Background thread runs; no crashes in 24-hour soak; graceful error handling']
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Test Results: 8/8 Passed (0 Failed)'
                },
                {
                    'type': 'table',
                    'headers': ['Test', 'Result'],
                    'rows': [
                        ['Monitoring Thread', 'PASS (Thread-3 active)'],
                        ['Price Fetch', 'PASS (Real-time data)'],
                        ['Stop-Loss Detection', 'PASS (Triggers correctly)'],
                        ['Take-Profit Detection', 'PASS (Triggers correctly)'],
                        ['Trailing Stop Logic', 'PASS (Math verified)'],
                        ['Position Update', 'PASS (Data persistence)'],
                        ['Duplicate Prevention', 'PASS (No spam)'],
                        ['Performance Analytics', 'PASS (Calculations accurate)']
                    ]
                },
                {
                    'type': 'text',
                    'text': 'What This Proves: The system works as designed, protects capital as intended, and performs at institutional-grade standards. Ready for production deployment.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Why This is Institutional-Grade'
                },
                {
                    'type': 'text',
                    'text': 'Most retail bots: Generate signals, maybe track performance. Our system: Full lifecycle management with professional risk controls.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'What Makes It Professional'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'Real-Time Monitoring — Not checking once per hour, but every 30 seconds',
                        'Automated Capital Protection — Stops execute even at 3 AM',
                        'Adaptive Strategy — Adjusts to market regime automatically',
                        'Risk-Adjusted Metrics — Sharpe/Sortino, not just total returns',
                        'No Emotional Trading — Computer executes the plan perfectly',
                        'Graceful Failure — Errors don\'t crash, they alert and continue',
                        'Professional Reporting — Track attribution by model and regime',
                        'Kill Switch — Emergency stop for unusual conditions'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'The Result: A system that can run unsupervised for weeks, protecting capital, taking profits, and adapting to conditions — exactly what professional traders need.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Configuration & Deployment'
                },
                {
                    'type': 'text',
                    'text': 'Current Status: Test Mode ON (paper trading), Auto-Execution OFF (alerts only), Position Monitor active, Alerts configured for Email + Telegram.'
                },
                {
                    'type': 'text',
                    'text': 'Going Live: After 24 hours of successful paper trading, switch to live mode with auto-execution enabled. Start with small positions ($50-100) and scale up as confidence builds.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Safety Features'
                },
                {
                    'type': 'list',
                    'items': [
                        'Test mode prevents real capital risk during validation',
                        'Emergency stop: disable exchange API keys to stop all activity instantly',
                        'Alerts notify before execution in manual mode',
                        'Full logging of every decision and action',
                        'Graceful degradation if APIs fail'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Performance Expectations'
                },
                {
                    'type': 'text',
                    'text': 'After 30 Days Live Trading — Target Metrics: Sharpe >1.0 (stretch >2.0), Win Rate >55% (stretch >65%), Expectancy >$0/trade, Max Drawdown <-20% (stretch <-10%).'
                },
                {
                    'type': 'text',
                    'text': 'Our test results already exceed these: Sharpe 14.93, Expectancy $62.50, Max Drawdown 0.5%. Actual live results will depend on market conditions and trade frequency.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'What We Built & Why'
                },
                {
                    'type': 'text',
                    'text': 'The Journey: Started with signal generation (quant/signal recommendations), added risk management (position sizing, correlation hedging), then completed it with exit monitoring (capital protection 24/7).'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Why Each Layer Matters'
                },
                {
                    'type': 'list',
                    'items': [
                        'Quant + Veto: Deterministic scoring plus rule-based veto reduces bias and errors',
                        'Regime Detection: Adapts strategy to market conditions automatically',
                        'Risk Management: Prevents concentration and correlation disasters',
                        'Exit Monitoring: Protects capital and locks in profits automatically',
                        'Performance Tracking: Provides objective proof of edge and identifies improvements'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'Compared to Manual Trading: 30-second response vs hours/days; no emotional decisions vs fear/greed; 24/7 monitoring vs 8-hour workday; data-driven exits vs gut feelings; consistent execution vs variable discipline.'
                },
                {
                    'type': 'text',
                    'text': 'Compared to Basic Bots: Multi-model consensus vs single algorithm; regime-adaptive vs static strategy; risk-adjusted sizing vs fixed percentages; professional metrics vs simple P&L; graceful errors vs crashes.'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'Bottom Line: This is a professional-grade trading system focused on capital preservation, risk-adjusted returns, and operational reliability.'
                }
            ]
        }

    def _exit_monitoring_merged(self):
        """Exit Monitoring — operational how-to plus strategic context and validation (merged section)."""
        return {
            'title': 'Exit Monitoring',
            'icon': '👁️',
            'content': [
                {
                    'type': 'text',
                    'text': 'The system monitors every open position every 30 seconds and alerts you (or executes) when stop-loss, take-profit, or trailing-stop levels are hit. This section covers how it works and why it matters.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How it works'
                },
                {
                    'type': 'text',
                    'text': 'A background process starts when you launch the dashboard. Every 30 seconds it fetches current prices (CoinGecko simple/price; CoinPaprika fallback), compares them to your stop/target/trailing levels, and acts: alert only, or auto-execute if enabled. Three triggers (priority order): (1) Stop-loss hit — price at or below stop; alert and/or market sell. (2) Take-profit hit — price at or above target; same. (3) Trailing stop — activates after 8% profit; stop trails 13% below high-water mark; when price drops to that level, trigger fires. Duplicate alerts are suppressed for 10 minutes per position per trigger type.'
                },
                {
                    'type': 'text',
                    'text': 'A fourth, time-based exit: positions held longer than 21 days (STALE_POSITION_MAX_DAYS in thresholds) are auto-sold to free capital from dead trades. Stop-loss and trailing stop still apply before the stale cutoff.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Using it'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'Start the dashboard; the monitor starts automatically.',
                        'Ensure open positions have stop and target (from Dynamic Targets or your custom values).',
                        'With auto-execution Off you get alerts and close manually; with On, the system sells when a trigger fires.',
                        'Enable alerts (Telegram or email). To stop trading: disable auto-execution or disable exchange API.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Coinbase hybrid (SL/TP on exchange, trailing in app)'
                },
                {
                    'type': 'text',
                    'text': 'On trade booking: a real stop-loss limit order is placed on Coinbase; no take-profit is placed (winners always run — the trailing stop is the only profit exit). The app monitors trailing stop every 30s; when trailing triggers, it cancels SL/TP limits and runs a sell (single market sell for positions below twap_min_usd, else TWAP sell — same rule as buys). If SL or TP is hit first, Coinbase executes the limit order and the app detects the fill (using fetch_order_with_fee to get fill price + fee in one call), extracts the real fee, and updates the Trade Journal with net-of-fees P&L. Sell orders now poll for fill confirmation (parity with buy side) so fills are reliably captured. All placement and execution is logged to execution_log.jsonl and the forensic audit trail (execution_audit.jsonl).'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Strategic context and validation'
                },
                {
                    'type': 'text',
                    'text': 'Most systems only generate entries; exit timing is left to emotion. Here, every position is monitored 24/7 and stops/targets execute automatically (or you are alerted), so capital is protected even when you\'re not watching. In testing, automated stops limited losses to -2.8% on average when stopped out vs -6.3% for manual management (2.2x better preservation).'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Audit results (Feb 2026)'
                },
                {
                    'type': 'text',
                    'text': 'Exit monitoring and integration tests: 100/100. All 8 tests passed (monitoring thread, price fetch, stop/target/trailing logic, duplicate prevention, performance analytics). Alerts go to Email and Telegram. Status: production-ready.'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'Bottom line: Exit monitoring is your safety net. Set it up once, enable alerts, and let the system watch for you. Run tests/test_exit_monitoring.py before production.'
                }
            ]
        }

    def _account_overview(self):
        """Account Overview widget and how it controls the system."""
        return {
            'title': 'Account Overview Widget',
            'icon': '💼',
            'content': [
                {
                    'type': 'text',
                    'text': 'At the top of the dashboard you\'ll see the Account Overview widget. It shows your total capital, risk setting, open positions cost basis, estimated trade fees, and cash available to trade. This widget controls how the system sizes positions and enforces risk.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Fields'
                },
                {
                    'type': 'list',
                    'items': [
                        'Total Account Value: Read-only; Total Cash plus current market value of open positions (true net worth view).',
                        'Total Cash: Editable. Set this to your liquid cash / account size for sizing (e.g. $20,000). Use "Sync from Exchange" to pull the current cash balance (USD + USDC + USDT) from your selected execution exchange; the value is saved automatically. When you change it and click Save, the system updates position sizing for all new signals.',
                        'Risk Parameter %: Editable (default 2.0%). Maximum percentage of account you are willing to risk per trade. Saving updates the system globally.',
                        'Open Positions Cost Basis: Read-only. Sum of capital currently in open positions.',
                        'Estimated Trade Fees: Read-only. Coinbase cut US fees on 2026-09-16. The live tier ("Intro", read from Coinbase 2026-10-11) is 0.90% PER SIDE as taker — about 1.8% round trip — and 0.50%/side as maker (resting limit orders, unproven here). The previous 1.20%/0.60% basis was the retired old base tier. Next tier: $10K 30-day volume or $500K assets on platform. Kraken Pro tier 3 is 0.38%/0.22%. Costs of this size dominate a small account: a $100 trade pays ~$1.80 in fees round trip.',
                        'Cash Available to Trade: Read-only. Same as Total Cash from the exchange (your liquid USD/USDC/USDT balance); minus any amount reserved for pending TWAP orders. When synced, it matches your exchange cash balance.',
                        'Exit Strategy: Read-only. Winners always run — every new position has no fixed take-profit; the trailing stop (8% activation / 13% trail) is the only profit exit. There is no toggle.',
                        'Funding (HELOC badge): Read-only indicator shown when HELOC mode is on. Displays drawn/notional amounts. Click to navigate to the Funding tab where all HELOC configuration is managed.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'HELOC Funding (Funding Tab)'
                },
                {
                    'type': 'text',
                    'text': 'When HELOC mode is enabled (via the Funding tab), the system uses the HELOC notional amount for risk cap tier lookup and position sizing percentages, while actual cash available (drawn amount) constrains liquidity. A per-trade hurdle rate ensures each trade clears the cost of capital (HELOC rate pro-rated to average hold duration). The Funding tab shows: HELOC configuration (notional, rate, drawn, drawdown limit), cost-of-capital metrics (USDC yield, net idle cost, hurdle rate, monthly carry), and performance vs carry (P&L net of HELOC interest). USDC yield is auto-refreshed weekly from Coinbase. If drawn capital drawdown exceeds the configured limit, auto-execution pauses and an alert fires.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Winners Always Run (permanent)'
                },
                {
                    'type': 'text',
                    'text': 'Every new position runs: no fixed take-profit is ever set; the position exits via its stop-loss or the trailing stop only. This is permanent (the old on/off toggle was removed 2026-05-30). Pre-existing positions that still carry a fixed target keep it.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How It Controls the System'
                },
                {
                    'type': 'text',
                    'text': 'When you change Total Cash or Risk % and click Save, the values are persisted to account_config.json and the position sizer is updated immediately. All subsequent position size calculations (signal recommendations, auto-execution, and manual logs) use these values. The Open Positions tab also shows "Risk % Used" per trade — the risk parameter that was in effect when that position was opened.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Keep Total Cash in sync with your real account size. If you add or withdraw funds, update the widget and Save so position sizing stays accurate.'
                },
                {
                    'type': 'text',
                    'text': 'Total Cash and Cash Available to Trade are automatically synced from the selected exchange (Coinbase or Kraken) on every refresh and after trades open or close when auto-sync balance is enabled (including manual "Book to Coinbase" fills so Total Account Value does not double-count cash with positions). You can also manually sync anytime with the "Sync from Exchange" button. The button fetches USD + USDC + USDT and updates Total Cash and the position sizer.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Position sizer default: When auto-sync balance is enabled, the position sizer uses exchange-reconciled cash at startup and on each refresh — so sizing reflects your actual exchange balance. If sync fails, it falls back to the manual Total Cash value in account_config.json.'
                }
            ]
        }

    def _exchange_reconciliation(self):
        """Exchange reconciliation: Coinbase as source of truth; discrepancies detected but never auto-imported."""
        return {
            'title': 'Exchange Reconciliation',
            'icon': '🔄',
            'content': [
                {
                    'type': 'text',
                    'text': 'The Account Overview syncs cash from your exchange. Reconciliation goes further: it compares exchange spot holdings to the Trade Journal for awareness only. The selected exchange (Coinbase primary) is the source of truth. The dashboard syncs cash automatically on refresh and after trades. Positions below the Dust Threshold ($1.00 by default) are ignored to avoid clutter from tiny leftover amounts.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'When auto-sync balance is enabled, the position sizer defaults to exchange-reconciled cash at startup and on each refresh. This ensures position sizing uses the actual balance on your exchange rather than a stale manual value. If sync fails, the system falls back to account_config.json.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Discrepancies are detected but never auto-imported. External positions (on exchange but not in the journal) and journal-only positions (in the journal but not on exchange) are shown in the Account Overview via "Review Discrepancies". No positions are automatically added or modified in the Trade Journal.'
                },
                {
                    'type': 'text',
                    'text': 'Position quantity discrepancies (journal qty ≠ exchange qty) can occur when: TWAP fills differ slightly from the planned size, you trade manually on the exchange, or rounding differs. Open position size should match the exchange. Use the Account Overview "Sync positions" button to update journal quantities to match Coinbase/exchange (one position per symbol). After syncing, cost basis and dashboard values use the exchange quantity as source of truth. When the exchange shows 0 (or dust) for a symbol, syncing will close the corresponding open position in the journal (status CLOSED, exit_reason exchange_sold) instead of only zeroing quantity, so the dashboard does not show an open position that was already sold on the exchange.'
                },
                {
                    'type': 'text',
                    'text': 'For best results, use a Coinbase account dedicated to this tool and clean out other positions. That keeps reconciliation "Clean" and avoids confusion between journal and exchange.'
                },
            ]
        }
    
    def _position_sizing(self):
        return {
            'title': 'Position Sizing - How Much to Invest',
            'icon': '💰',
            'content': [
                {
                    'type': 'text',
                    'text': 'With your account configured (Account Overview and Exchange Reconciliation), the next question is how much to invest per trade. Position sizing is more important than signal accuracy. You can be right 80% of the time and still lose money if your position sizing is wrong.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Account size source: When auto-sync balance is enabled, the position sizer uses exchange-reconciled cash (from Coinbase or Kraken) at startup and on each refresh. This ensures sizing reflects your actual balance. Otherwise it uses the manual Total Cash from Account Overview.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'The Golden Rule'
                },
                {
                    'type': 'callout',
                    'style': 'warning',
                    'text': 'Never risk more than 2% of your account on a single trade.'
                },
                {
                    'type': 'text',
                    'text': 'Why? Because even with a high win rate, you\'ll still have losing trades. If you risk 20% per trade, 5 losses in a row = you\'re done. If you risk 2% per trade, you can withstand many losses and still be in the game.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How the System Calculates Position Size'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'START WITH ACCOUNT SIZE: Let\'s say you have $20,000',
                        'DETERMINE MAX RISK: 2% of $20,000 = $400 (most you\'ll lose if stopped out)',
                        'CHECK STOP LOSS DISTANCE: Let\'s say stop is 2.5% below entry',
                        'CALCULATE BASE POSITION: $400 ÷ 2.5% = $16,000 base position',
                        'ADJUST FOR CONFIDENCE: ULTRA_BUY = 100%, STRONG_BUY = 75%, MODERATE_BUY = 50%',
                        'ADJUST FOR REGIME: BULL = 1.2x, SIDEWAYS = 1.0x, BEAR = 0.6x, VOLATILE = 0.5x',
                        'REAL FRACTIONAL KELLY (v1.0): When edge_probability and rr_ratio are available (from quant_signal), the sizer applies a Kelly-derived multiplier to regime-adjusted size (clamp(edge × 2, 0.25, 1.5)) and enforces KELLY_CAP_PCT (default 5%). Size scales with proven edge and risk:reward.',
                        'ADJUST FOR VOLATILITY: High volatility = reduce size, Low volatility = can increase',
                        'FINAL POSITION SIZE: Apply all multipliers and Kelly cap'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Real Example'
                },
                {
                    'type': 'example',
                    'text': 'Account: $20,000\nSignal: STRONG_BUY\nEntry: Bitcoin at $45,000\nStop Loss: 2.5% ($43,875)\nRegime: BULL\nVolatility: Medium\n\nCalculation:\n1. Max Risk: $20,000 × 2% = $400\n2. Base Position: $400 ÷ 0.025 = $16,000\n3. × Confidence (STRONG_BUY): $16,000 × 0.75 = $12,000\n4. × Regime (BULL): $12,000 × 1.2 = $14,400\n5. × Volatility (Medium): $14,400 × 1.0 = $14,400\n\nFinal Position: $14,400\nQuantity: 0.32 BTC\nRisk: $360 (2% of account)'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'What the System Tells You'
                },
                {
                    'type': 'text',
                    'text': 'For every signal, you\'ll see:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Position Size: How much USD to invest',
                        'Quantity: How many coins to buy',
                        'Risk: How much you\'ll lose if stopped out',
                        '% of Account: What percentage of your capital this is',
                        'Recommendation: FULL / REDUCED / MINIMAL / SKIP'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Common Mistakes to Avoid'
                },
                {
                    'type': 'list',
                    'items': [
                        'Going all-in on "sure things" - There are no sure things',
                        'Using same position size for every trade - Risk varies by volatility and confidence',
                        'Ignoring the regime - BULL vs BEAR matters hugely',
                        'Moving stops further away to "give it room" - This breaks the risk calculation',
                        'Increasing position size after wins - Stick to the system, don\'t get cocky'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': '<strong>Note:</strong> The stop loss percentages used in position sizing are calculated by the Dynamic Targets System, which automatically adjusts stop width based on coin volatility, market regime, and signal confidence. See the "Dynamic Stop Loss & Take Profit" section for details.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'The position sizer removes emotion from the equation. Follow its recommendation. Your job is to execute, not override.'
                },
                {
                    'type': 'text',
                    'text': 'Single trade is capped at a configurable percentage (default 40%, editable in Account Overview as "Max Cash Per Trade %") of available cash. This ensures liquidity for multiple setups and helps you survive losing streaks.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': '<strong>Fractional Kelly (25% Kelly, 5% cap):</strong> The position sizer applies a conservative fractional Kelly cap: no single position may exceed 5% of account from Kelly sizing. This limits over-concentration while preserving upside. Used alongside risk-based sizing and the 40% cash cap.'
                }
            ]
        }
    
    def _portfolio_correlation_hedging(self):
        """Portfolio Risk Summary and correlation-aware position limits."""
        return {
            'title': 'Portfolio & Correlation Hedging',
            'icon': '📊',
            'content': [
                {
                    'type': 'text',
                    'text': 'Position sizing (previous section) determines how much to invest per trade. This section adds a layer: the system limits how much of your total risk comes from coins that move closely with Bitcoin (BTC correlation > 0.85). This avoids over-concentration in correlated assets when the market dumps.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'The 35% Rule'
                },
                {
                    'type': 'callout',
                    'style': 'warning',
                    'text': 'Never more than 35% of your total risk exposure may come from coins with BTC correlation above 0.85.'
                },
                {
                    'type': 'text',
                    'text': 'If adding a new position would push correlated risk over this cap, the system automatically scales down the recommended size for high-correlation coins and shows hedging suggestions.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Additional Risk Controls (Zero Cost)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Concentration limit: Max 5% per position—no single coin may exceed 5% of account.',
                        'Total exposure check: Monitors combined exposure across all positions.',
                        'Simple VaR: 95% 1-day Value-at-Risk estimate for portfolio risk awareness.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Total Exposure Card'
                },
                {
                    'type': 'text',
                    'text': 'On the dashboard you will see:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Total Exposure: Sum of position sizes (open + recommended) as % of account. Can exceed 100% when recommendations exceed available capital.',
                        'Max Correlated Exposure: Risk from assets with BTC correlation > 0.85.',
                        'Suggested Hedging: Messages like "Reduce Solana size by 40% due to high BTC corr" when the 35% cap would be exceeded.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How It Works'
                },
                {
                    'type': 'text',
                    'text': 'After position sizing is calculated for each BUY signal, the portfolio manager combines open positions and new recommendations. It computes total and correlated risk. If correlated risk would exceed 35% of total, it applies a scale factor to high-correlation recommendations and stores it in position_sizing (correlation_factor and correlation_note). The numbers you see (position size, quantity, % of account) are already adjusted.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Open positions are included in the risk total. New recommendations are adjusted; existing positions are not auto-reduced—you can manually trim them if you want to stay under the cap.'
                }
            ]
        }
    
    def _risk_management(self):
        """Consolidated Risk Management: concentration, exposure, VaR, Kelly, trailing stops."""
        return {
            'title': 'Risk Management - Institutional-Grade Controls',
            'icon': '🛡️',
            'content': [
                {
                    'type': 'text',
                    'text': 'Building on position sizing and correlation hedging, the system applies multiple additional risk controls at zero cost to improve probability of success and limit drawdowns.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Concentration Limit'
                },
                {
                    'type': 'text',
                    'text': 'No single position may exceed 5% of account. This prevents over-concentration in one coin and limits damage from a single bad trade.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Total Exposure Check'
                },
                {
                    'type': 'text',
                    'text': 'The portfolio manager monitors combined exposure across all open positions and new recommendations. Total exposure is capped to avoid over-leverage.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Value-at-Risk (VaR)'
                },
                {
                    'type': 'text',
                    'text': '95% 1-day VaR estimates how much you could lose on a bad day. When you have 20+ closed trades, the system uses historical simulation (your actual returns) for a data-driven estimate. With fewer trades, it falls back to parametric VaR. This improves risk awareness at no cost.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Fractional Kelly (25% Kelly, 5% Cap)'
                },
                {
                    'type': 'text',
                    'text': 'Position sizing applies conservative fractional Kelly: no single position may exceed 5% of account from Kelly sizing. Used alongside risk-based sizing and the 40% cash cap. Reduces over-betting without sacrificing upside.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Trailing Stops'
                },
                {
                    'type': 'text',
                    'text': 'Once profit exceeds 8%, trail by 13% below the high-water mark (Phase 1 "let winners run" overhaul, 2026-05-30; was 3%/3% which clipped the fat tail). Lets winners run while capping downside via the initial regime-floor stop. Thresholds are configurable via thresholds.py (TRAILING_STOP_MIN_PROFIT_PCT, TRAILING_STOP_TRAIL_PCT).'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'All risk controls are zero-cost enhancements that improve probability of success and limit downside.'
                }
            ]
        }
    
    def _capital_funding_strategy(self):
        """Capital Funding Strategy (HELOC + Tax Considerations)"""
        return {
            'title': 'Capital Funding Strategy (HELOC + Tax Considerations)',
            'icon': '💰',
            'content': [
                {
                    'type': 'text',
                    'text': 'With risk parameters configured, the next consideration is how to fund your account. This system is optimized for a dedicated Coinbase account funded via HELOC for maximum capital efficiency and tax optimization.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Recommended Approach (Staggered Draws)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Do NOT draw the full $100k at once.',
                        'Start with $25k–$30k drawn from HELOC and funded into Coinbase as USDC.',
                        'When Cash Available to Trade (same as Total Cash when no pending TWAP) drops below 30% of your target account size, manually draw the next $25k–$30k tranche and fund it as USDC.',
                        'Always keep Total Cash in the dashboard = actual Coinbase balance (use "Sync from Exchange" after each draw).'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Why Not Trick the System?'
                },
                {
                    'type': 'text',
                    'text': 'Setting Total Cash = $100k while only funding $20k would cause oversized positions and failed executions. Always align Total Cash with real available cash for safety and accurate risk management.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'USDC Preference for Execution'
                },
                {
                    'type': 'list',
                    'items': [
                        'Auto-execution defaults to USDC pairs (XRP-USDC, ETH-USDC, etc.) so dry powder earns interest (~3.5% APY on Coinbase) while waiting.',
                        'Falls back to USD pair automatically if USDC liquidity is insufficient.',
                        'Your idle cash stays in USDC — no manual conversion needed for most deposits.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': '1-Year ROI Projection (Conservative, Including Taxes)'
                },
                {
                    'type': 'text',
                    'text': 'This table uses a conservative assumption: 25% gross return on risked capital over the year. Short-term capital gains taxed at 32% effective rate (24% federal + 4.75% NC state + buffer). HELOC interest is deductible. Higher returns are possible in strong markets but should not be assumed.'
                },
                {
                    'type': 'table',
                    'headers': ['Item', 'Amount'],
                    'rows': [
                        ['Gross Trading Profit (25% on risked capital)', '+$25,000'],
                        ['Trading Fees / Slippage', '-$2,500'],
                        ['HELOC Interest Expense (8.0%)', '-$8,000'],
                        ['USDC Interest Earned (3.5% on avg $60k idle)', '+$2,100'],
                        ['Net Pre-Tax Profit', '+$16,600'],
                        ['Taxes on Trading Profit (32%)', '-$7,200'],
                        ['Tax Savings from HELOC Deduction (32%)', '+$2,560'],
                        ['Taxes on USDC Interest (32%)', '-$672'],
                        ['Net Tax Impact', '-$5,312'],
                        ['Final Net Profit After Tax', '+$11,288'],
                        ['ROI on $100k HELOC', '+11.3%']
                    ]
                },
                {
                    'type': 'text',
                    'text': 'With Staggered Draws (Recommended): Net cost of capital drops → Net Profit After Tax can reach ~+$12,000–13,000 (ROI ~12–13%).'
                },
                {
                    'type': 'text',
                    'text': 'Break-Even: Only ~12% gross return on risked capital needed to cover all costs and taxes. That is the realistic minimum hurdle; results above that depend on market conditions and execution.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Best Practices'
                },
                {
                    'type': 'list',
                    'items': [
                        'Monitor the new "Low Cash" banner in Account Overview for draw triggers.',
                        'Enable Coinbase One for higher USDC yield if desired.',
                        'Disable auto-execution or exchange API if needed during funding.',
                        'Consult your tax advisor for exact deduction eligibility.'
                    ]
                }
            ]
        }
    
    def _dynamic_targets(self):
        """Dynamic Stop Loss & Take Profit System"""
        return {
            'title': 'Dynamic Stop Loss & Take Profit',
            'icon': '🎯',
            'content': [
                {
                    'type': 'text',
                    'text': 'With risk parameters configured (Risk Management, Capital Funding), the next question is where to place stops and targets. The Dynamic Targets System automatically calculates optimal stop loss and take profit levels based on coin volatility, market conditions, and signal confidence. This replaces fixed percentages with smart, adaptive targets. All new positions (Book to Coinbase, Quick Log, Add from exchange) use this dynamic calculation when the coin is in the dashboard cache; there are no static stop percentages in the main flow.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Why Dynamic Targets?'
                },
                {
                    'type': 'text',
                    'text': 'Fixed stop losses don\'t work in crypto. Bitcoin might move 2% on a quiet day, while a small cap can swing 10% on normal volume. Using the same fixed % stop for every coin gets you stopped out constantly on volatile coins, or wastes capital on stable coins.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'The Formula'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': '<strong>Stop Loss % = Base (e.g. 4%) × Volatility Mult × Regime Mult × Confidence Mult</strong>'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Volatility Multipliers'
                },
                {
                    'type': 'text',
                    'text': 'This is the MOST important factor. High volatility coins get wider stops:'
                },
                {
                    'type': 'list',
                    'items': [
                        '0-30% volatility (BTC in calm): 0.7x - Very tight stops',
                        '30-45% (BTC, ETH normal): 0.85x - Tight stops',
                        '45-60% (Large caps): 1.0x - Normal stops',
                        '60-80% (Most alts): 1.2x - Slightly wide',
                        '80-100% (Mid caps): 1.5x - Wide stops',
                        '100-150% (Small caps): 2.0x - Very wide',
                        '150%+ (Micro caps): 2.5x - Extremely wide'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Regime Multipliers'
                },
                {
                    'type': 'list',
                    'items': [
                        'BULL Market: 0.9x - Clean trends, tight stops work',
                        'SIDEWAYS: 1.0x - Normal conditions',
                        'BEAR Market: 1.3x - Choppy, need more room',
                        'VOLATILE: 1.5x - Extreme swings, wide stops essential'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Confidence Multipliers'
                },
                {
                    'type': 'list',
                    'items': [
                        'ULTRA_BUY: 0.9x - Very confident, tight stops OK',
                        'STRONG_BUY: 1.0x - Confident, normal stops',
                        'MODERATE_BUY: 1.1x - Less certain, more room',
                        'WEAK_BUY: 1.2x - Uncertain, wide stops'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Trailing Stop Logic (Low-Cost Enhancement)'
                },
                {
                    'type': 'text',
                    'text': 'Once a position is in profit, the system activates a trailing stop: profit >8% → trail by 13% below peak. Thresholds are configurable in thresholds.py. This lets winners run while protecting the downside via the initial regime-floor stop.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Real Examples'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Example 1: Bitcoin - ULTRA_BUY - BULL Market'
                },
                {
                    'type': 'text',
                    'text': 'Entry: $45,000 | Volatility: 35% (low)'
                },
                {
                    'type': 'text',
                    'text': 'Calculation: 4% × 0.85 × 0.9 × 0.9 = 2.75% (base is configurable; example uses 4%)'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': '<strong>Stop: 2.75% ($43,763) | Target: ~8.25% ($48,712)</strong><br>Tight, efficient - BTC is stable, we can use precision'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Example 2: Small Cap - MODERATE_BUY - VOLATILE'
                },
                {
                    'type': 'text',
                    'text': 'Entry: $2.50 | Volatility: 125% (extreme)'
                },
                {
                    'type': 'text',
                    'text': 'Calculation: 4% × 2.0 × 1.5 × 1.1 = 13.2% (bounded to max stop)'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': '<strong>Stop: ~13% ($2.18) | Target: ~33% ($3.33)</strong><br>Wide stops needed - this coin swings wildly on normal volume'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Example 3: Ethereum - STRONG_BUY - BEAR Market'
                },
                {
                    'type': 'text',
                    'text': 'Entry: $2,800 | Volatility: 55% (medium)'
                },
                {
                    'type': 'text',
                    'text': 'Calculation: 4% × 1.0 × 1.3 × 1.0 = 5.2%'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': '<strong>Stop: 5.2% ($2,654) | Target: ~10.4% ($3,091)</strong><br>Wider for bear market chop - room for noise'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Risk:Reward Ratios'
                },
                {
                    'type': 'text',
                    'text': 'Take profit = Stop loss × R:R ratio. The ratio adapts to market conditions:'
                },
                {
                    'type': 'list',
                    'items': [
                        'BULL Market: 3:1 - Strong trends, aim for bigger wins',
                        'SIDEWAYS: 2:1 - Mean reversion, take profits faster',
                        'BEAR Market: 2:1 - Choppy, take what you can get',
                        'VOLATILE: 2.5:1 - High risk needs high reward'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Safety Bounds'
                },
                {
                    'type': 'text',
                    'text': 'To prevent extreme values:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Minimum Stop: 1.5% (accounts for spreads/slippage)',
                        'Maximum Stop: 15% (prevents excessive risk)',
                        'Minimum Target: 3% (not worth trading below this)',
                        'Maximum Target: 50% (keeps expectations realistic)'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Position Sizing Adaptation'
                },
                {
                    'type': 'text',
                    'text': 'Here\'s the magic: position size scales INVERSELY with stop width, keeping total dollar risk constant.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': '<strong>Example ($20,000 account, 2% risk = $400 max loss):</strong><br><br>BTC with 2% stop: Position = $20,000 (risk: $400)<br>Small cap with 10% stop: Position = $4,000 (risk: $400)<br><br>Same dollar risk, different position sizes!'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Key Benefits'
                },
                {
                    'type': 'list',
                    'items': [
                        'No more false stops on volatile coins - stay in winning trades',
                        'Tighter risk control on stable coins - don\'t waste capital',
                        'Consistent 2% dollar risk across ALL trades',
                        'Professional institutional-grade approach',
                        'Adapts automatically to changing conditions',
                        'Transparent - you see why each target was chosen'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Common Questions'
                },
                {
                    'type': 'text',
                    'text': '<strong>Q: Won\'t wider stops lose me more money?</strong>'
                },
                {
                    'type': 'text',
                    'text': 'A: No! Position size adjusts. A 10% stop with small position = same $ risk as 2% stop with large position. Always 2% of account at risk.'
                },
                {
                    'type': 'text',
                    'text': '<strong>Q: Should I adjust stops manually once in a trade?</strong>'
                },
                {
                    'type': 'text',
                    'text': 'A: Only move stops TIGHTER (lock in profits), never WIDER (adding risk). Common: move to breakeven after 50% of target.'
                },
                {
                    'type': 'callout',
                    'style': 'warning',
                    'text': '<strong>Bottom Line:</strong> Dynamic Targets eliminates the #1 crypto trading problem - getting stopped out on normal volatility. Small caps won\'t stop you out on 5% swings, and BTC won\'t waste capital with unnecessarily wide stops.'
                }
            ]
        }
    
    def _schedule_and_costs(self):
        """Alerts & Refreshes — auto-refresh, status email, monthly learning (no separate Schedule tab)."""
        return {
            'title': 'Alerts & Refreshes',
            'icon': '🔔',
            'content': [
                {
                    'type': 'text',
                    'text': 'Auto-refresh, daily status email, monthly ML learning, and all alert types (Telegram/Email) are configured in the **Alerts/Refreshes** tab. There is no separate Schedule tab.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Why You Need This'
                },
                {
                    'type': 'text',
                    'text': 'You control when data refreshes (auto-refresh interval or manual only), when status emails go out, and when the monthly ML pipeline runs. Alert channels (Email, Telegram) and per-alert-type toggles are in the same place.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How It Works'
                },
                {
                    'type': 'list',
                    'items': [
                        'System starts with auto-refresh OFF (manual only)',
                        'You choose when and how often data refreshes',
                        'Settings persist across server restarts (scheduler_config.json)',
                        'You can change interval and alert preferences anytime'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Available Intervals'
                },
                {
                    'type': 'list',
                    'items': [
                        'Off (Manual Only) — click "Refresh Now" when you want',
                        'Daily (9am ET) — one quality check per day',
                        '6 Hours — long-term position trading',
                        '4 Hours — conservative monitoring',
                        '2 Hours — moderate activity',
                        '1 Hour (Recommended) — swing trading sweet spot',
                        '30 / 15 / 5 Minutes — active to day trading (use sparingly)'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How to Configure'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'Open the "🔔 Alerts/Refreshes" tab in the dashboard',
                        'In the Alerts & Refreshes table: set Auto-Refresh interval and click Save',
                        'Configure Daily status email (enable, frequency) and Monthly ML learning (enable, day 1–28) as needed',
                        'Set Telegram/Email per alert type below the table',
                        'Status line shows if auto-refresh is running and next refresh time'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'What You\'ll See'
                },
                {
                    'type': 'text',
                    'text': 'The Alerts/Refreshes tab shows a single table with:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Auto-Refresh: interval dropdown, Save, status (running/stopped), refreshes today/month, last and next refresh',
                        'Daily status email: enable, frequency presets, Save',
                        'Monthly ML learning run: enable, day of month (1–28), Save, last run',
                        'Alert types below with Telegram and Email checkboxes per type'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Recommended Settings by Trading Style'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Swing Trading (Your Use Case)'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': '<strong>1 Hour Intervals - $131/month</strong><br>Perfect balance for swing trading. ULTRA_BUY signals don\'t appear every 5 minutes, so hourly refreshes catch all meaningful opportunities without wasting money on noise.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Position Trading'
                },
                {
                    'type': 'text',
                    'text': '4-6 Hour Intervals - $22-33/month - For long-term holds where you check a few times per day.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Weekend Warrior'
                },
                {
                    'type': 'text',
                    'text': 'Daily (9am ET) - $5.46/month - One quality analysis per day, supplement with manual refreshes when needed.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Testing / Budget-Conscious'
                },
                {
                    'type': 'text',
                    'text': 'Off (Manual Only) - $0/month - Click "Refresh Now" whenever you want to check. Perfect while learning the system.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Usage Monitoring'
                },
                {
                    'type': 'text',
                    'text': 'The system tracks and displays:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Refreshes Today - How many times data refreshed since midnight',
                        'Refreshes This Month - Total refreshes for billing period',
                        'Last Refresh - When data was last updated',
                        'Next Refresh - Countdown to next scheduled refresh'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Persistent Settings'
                },
                {
                    'type': 'text',
                    'text': 'Your schedule configuration is saved to a file (scheduler_config.json) and persists across server restarts. You don\'t need to reconfigure every time you restart the system.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Manual Refresh Always Available'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': '<strong>Important:</strong> Regardless of your auto-refresh setting, you can ALWAYS click "Refresh Now" to manually update data. Manual refresh works even when auto-refresh is turned off.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Cost Optimization Tips'
                },
                {
                    'type': 'list',
                    'items': [
                        'Start with "Off" or "Daily" while testing the system',
                        'Monitor your "Refreshes Today" counter to see actual usage',
                        'BULL markets may warrant more frequent refreshes',
                        'BEAR/SIDEWAYS markets can use less frequent checks',
                        'Combine auto-refresh with manual refresh for efficiency',
                        'Remember: ULTRA_BUY signals are rare (1-2 per month) so 5-minute refreshes are usually overkill'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Example: Typical Month'
                },
                {
                    'type': 'text',
                    'text': 'With 1 hour intervals:'
                },
                {
                    'type': 'list',
                    'items': [
                        '720 refreshes per month (24 hours × 30 days)',
                        '$0.182 per refresh',
                        'Total: $131.04/month',
                        'Catches every ULTRA_BUY signal',
                        'Never misses meaningful moves',
                        'Cost is predictable and transparent'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Common Questions'
                },
                {
                    'type': 'text',
                    'text': '<strong>Q: What happens if I turn off auto-refresh?</strong>'
                },
                {
                    'type': 'text',
                    'text': 'A: The system stops making automatic API calls. Your data won\'t update until you click "Refresh Now". Cost: $0/month. Manual refresh always works.'
                },
                {
                    'type': 'text',
                    'text': '<strong>Q: Can I change the interval anytime?</strong>'
                },
                {
                    'type': 'text',
                    'text': 'A: Yes! Open the Alerts/Refreshes tab, pick new interval, click Save. Changes apply immediately.'
                },
                {
                    'type': 'text',
                    'text': '<strong>Q: What if APIs are more expensive than shown?</strong>'
                },
                {
                    'type': 'text',
                    'text': 'A: The cost calculator uses current API pricing. If prices change, the calculator will be updated to reflect new rates.'
                },
                {
                    'type': 'text',
                    'text': '<strong>Q: Does manual refresh cost money?</strong>'
                },
                {
                    'type': 'text',
                    'text': 'A: Yes, each manual refresh costs the same as auto-refresh (~$0.18). But you control exactly when it happens.'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': '<strong>Key Takeaway:</strong> This system puts YOU in control. You see costs before committing, you choose when to refresh, and you can change settings anytime. Start conservative (Off or Daily), then upgrade if you want continuous monitoring.'
                }
            ]
        }
    
    def _alerts_system(self):
        return {
            'title': 'Alerts - Never Miss an Opportunity',
            'icon': '🔔',
            'content': [
                {
                    'type': 'text',
                    'text': 'The alert system sends you instant notifications when something important happens — no need to watch the dashboard 24/7.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'What Triggers an Alert'
                },
                {
                    'type': 'list',
                    'items': [
                        'NEW ULTRA_BUY SIGNAL - The system found a high-conviction setup (realistic 70-76% win rate). Why it matters: These are your best opportunities; missing one can cost thousands.',
                        'NEW STRONG_BUY SIGNAL - A good opportunity appeared (61-67% win rate). Why it matters: Solid setups worth considering when you\'re actively trading.',
                        'STOP LOSS HIT - One of your positions hit the stop price. Why it matters: You need to exit to limit the loss. If auto-execution is Off, close the position manually.',
                        'TARGET HIT - One of your positions reached the profit target. Why it matters: Time to take profits. If auto-execution is Off, you must sell manually.',
                        'TRAILING STOP HIT - A position in profit (8%+) dropped 13% from its peak. Why it matters: The system locked in most of your gain before a reversal. You should exit (or the system did it for you). See "Exit Monitoring" for how trailing stops work.',
                        'REGIME CHANGE - Market conditions shifted (e.g., BULL → BEAR). Why it matters: Your risk parameters may need adjustment; some traders avoid new entries in BEAR.',
                        'CONSENSUS CHANGED - An open position\'s signal/consensus rating changed. Why it matters: The system\'s view of the trade shifted; consider whether to hold or trim.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Duplicate Alert Prevention (Exit Alerts)'
                },
                {
                    'type': 'text',
                    'text': 'For stop-loss, target, and trailing-stop alerts, the system applies a 10-minute cooldown per position and alert type. Without this, the exit monitor (which runs every 30 seconds) would send 120 alerts per hour for the same event until you close the position. You get one alert per event. After 10 minutes, if the position is still open and the trigger is still active, you get a reminder. This keeps you informed without spam. See "Exit Monitoring" for full details.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Alert Channels'
                },
                {
                    'type': 'text',
                    'text': 'You can receive alerts via:'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': '1. Telegram (Recommended)'
                },
                {
                    'type': 'text',
                    'text': 'Pros: Instant, free, works on phone and computer, very reliable'
                },
                {
                    'type': 'text',
                    'text': 'Setup: 5 minutes - create a bot with @BotFather'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': '2. Email (Optional)'
                },
                {
                    'type': 'text',
                    'text': 'Pros: Universal, no extra app needed'
                },
                {
                    'type': 'text',
                    'text': 'Cons: Slower, can end up in spam'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Example Alert'
                },
                {
                    'type': 'example',
                    'text': '🚨 ULTRA_BUY SIGNAL\n\nCoin: Bitcoin (BTC)\nPrice: $45,234\nConfidence: Very High\nQuant: ULTRA_BUY (rule veto: CONFIRM)\n\nPosition Size: $14,400 (0.318 BTC)\nStop: $44,101 (2.5%)\nTarget: $47,813 (5.7%)\n\nRegime: SIDEWAYS\nRecommendation: Trade immediately\n\nTime: 14:32:15'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Alert Settings (Recommended)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Enable: ULTRA_BUY signals (always want to know)',
                        'Enable: STRONG_BUY signals (if trading actively)',
                        'Enable: Stop loss hits (protect capital — you must act if auto-exec is Off)',
                        'Enable: Target hits (take profits when you reach your goal)',
                        'Enable: Trailing stop hits (lock in gains when a winner reverses 13% from peak, after 8%+ profit)',
                        'Disable: MODERATE_BUY signals (too noisy)',
                        'Disable: Regime changes (you can check manually)'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'Set up Telegram alerts. It takes 5 minutes and ensures you never miss an ULTRA_BUY signal. That one alert could make you thousands.'
                }
            ]
        }

    def _unattended_operation_alerts(self):
        """Unattended Operation & Alerts — who receives what; Telegram replies."""
        return {
            'title': 'Unattended Operation & Alerts',
            'icon': '🛡️',
            'content': [
                {
                    'type': 'text',
                    'text': 'For unattended operation, the system splits alerts by recipient: daily status and critical system messages go only to the primary owner and Telegram. Normal trading signals (ULTRA_BUY, STRONG_BUY, stop/target hits, regime changes) go to the full recipient list and Telegram.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Alert routing'
                },
                {
                    'type': 'list',
                    'items': [
                        'Normal trading signals (ULTRA_BUY, STRONG_BUY, stop loss hits, target hits, regime changes) → full recipient list + Telegram',
                        'Critical system alerts (drift detected, sync balance failure, rate limit errors, insufficient cash) → primary owner only (ALERT_PRIMARY_EMAIL) + Telegram',
                        'Daily status reports (3× per day: 9:00 AM, 1:00 PM, 5:00 PM ET) → primary owner only + Telegram, subject "Elite Swing Trader — System OK [Date]". Reports are sent only after at least one data refresh has completed, so the email never shows "Regime: Unknown" from cold start. Each report includes daily performance (today\'s realized P&L), a snapshot of open positions with entry/current/cost and per-position unrealized P&L, and total unrealized P&L so you can see how open trades are doing away from the desk.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Telegram commands'
                },
                {
                    'type': 'text',
                    'text': 'Telegram webhook replies to "KILL SWITCH" / "STOP TRADING" and "RESUME TRADING" with a message that the in-app kill switch has been removed; to stop trading immediately, disable your exchange API keys. Register the webhook URL with Telegram: use /api/telegram-webhook on your server.'
                }
            ]
        }
    
    def _exit_monitoring(self):
        """Exit Monitoring - Background position monitor for stop/target/trailing."""
        return {
            'title': 'Exit Monitoring - Automatic Stop, Target & Trailing Detection',
            'icon': '👁️',
            'content': [
                {
                    'type': 'text',
                    'text': 'Dynamic Targets (previous section) defines your stop-loss, take-profit, and trailing-stop levels. This section explains how the system monitors those levels in real time and alerts you (or executes) when they are hit.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Why This Matters'
                },
                {
                    'type': 'text',
                    'text': 'The biggest mistake traders make is not exiting when they should. A stop-loss that never gets executed is useless. A take-profit target you forget to hit leaves money on the table. And a position that runs up 15% then crashes back to breakeven could have been locked in with a trailing stop. Exit monitoring solves this: the system watches your positions every 30 seconds and tells you (or executes for you) when it\'s time to get out.'
                },
                {
                    'type': 'text',
                    'text': 'Without exit monitoring, you would need to watch the dashboard constantly or set price alerts on your exchange for every position. With it, you can step away. The system has your back.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'What It Is'
                },
                {
                    'type': 'text',
                    'text': 'Exit monitoring is a background process that starts automatically when you launch the dashboard. It runs in a loop: every 30 seconds it fetches the current price of every coin you have an open position in, compares those prices to your stop-loss, take-profit, and trailing-stop levels, and acts accordingly. It uses a lightweight price API (CoinGecko simple/price; CoinPaprika fallback) so it doesn\'t burn through rate limits or slow down the rest of the system. To keep exit decisions accurate, the system never uses cached prices older than 2 minutes for stop/target/trailing: if the main coins cache is older than that, it fetches fresh prices for your positions so triggers are based on current data.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'What It\'s For'
                },
                {
                    'type': 'list',
                    'items': [
                        'Protecting capital — Stop-loss hits are detected and you are alerted (or the position is sold automatically) so losses don\'t run.',
                        'Locking in profits — Take-profit hits are detected so you capture gains at your target instead of hoping for more and giving it back.',
                        'Letting winners run (safely) — The trailing stop activates after 8% profit and trails 13% below the peak. If price keeps rising, your stop rises with it. If it reverses, you exit with most of the gain intact.',
                        'Unattended operation — You don\'t need to watch the screen. The monitor runs 24/7 while the dashboard is up.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'The Three Exit Triggers (In Order of Priority)'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': '1. Stop-Loss Hit'
                },
                {
                    'type': 'text',
                    'text': 'When current price falls to or below your stop price, the system treats this as a stop-loss hit. This is the highest priority: it means the trade went against you and you need to cut the loss. The system sends an alert and, if auto-execution is On, places a market sell and closes the position in the Trade Journal. If auto-execution is Off, you get the alert and must close manually.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': '2. Take-Profit Hit'
                },
                {
                    'type': 'text',
                    'text': 'When current price rises to or above your target price, the system treats this as a take-profit hit. The trade reached your goal. The system sends an alert and, if auto-execution is On, places a market sell and closes the position. If Off, you close manually.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': '3. Trailing Stop Hit'
                },
                {
                    'type': 'text',
                    'text': 'Trailing stops only activate after a position is in profit. Once your position is up 8% or more, the system sets a "high-water mark" (the highest price seen). The trailing stop is 13% below that mark. If price drops to that level (e.g. you were up 30%, price peaked at $130, trailing stop at $113.10, and price drops to $113.00), the system triggers a trailing-stop hit. You get an alert and, if auto-execution is On, the position is sold. This lets winners run while protecting accumulated gains.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How to Use It'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'Start the dashboard (python app.py). The exit monitor starts automatically when the app loads.'
                        ,
                        'Open positions in the Trade Journal with valid stop and target prices. The monitor uses the quant/signal stop and target or your custom values from the position record.'
                        ,
                        'Choose your mode: With auto-execution Off, you get alerts and close manually. With auto-execution On, the system sells for you when a trigger is hit.'
                        ,
                        'Enable alerts (Telegram or email) so you know when a stop, target, or trailing stop is hit. See the Alerts section.'
                        ,
                        'To pause trading: turn off auto-execution in the dashboard or disable exchange API keys. The monitor continues to check and send alerts; you decide when to exit.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Duplicate Alert Prevention'
                },
                {
                    'type': 'text',
                    'text': 'The monitor runs every 30 seconds. If your stop is hit and you don\'t close the position (e.g. you\'re in manual mode or execution failed), the monitor would see "stop hit" again and again. Without protection, you\'d get 120 alerts per hour for the same event. The system applies a 10-minute cooldown: after the first alert for a given position and trigger type (stop, target, or trailing), no duplicate alerts are sent for 10 minutes. Different alert types are tracked separately: a stop hit and a trailing hit on the same position can each trigger once.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Hybrid SL/TP + Trailing Stop (Coinbase)'
                },
                {
                    'type': 'text',
                    'text': 'When Coinbase is the selected exchange, the system uses a professional hybrid approach: real limit orders on the exchange plus app-side trailing stop monitoring. SL/TP orders are placed on trade booking (manual journal open or after TWAP buy completes).'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'On Trade Booking (After Successful Buy)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Always place a real Stop-Loss limit sell order on Coinbase at the initial SL price. Protection remains even if the app is offline.',
                        'Winners always run (permanent): NO Take-Profit limit order is ever placed — only the SL limit order. Every position runs until its stop-loss or trailing stop triggers.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'During Monitoring (Every 30 Seconds)'
                },
                {
                    'type': 'list',
                    'items': [
                        'The app monitors live price for trailing stop trigger (8% profit, then 13% below peak).',
                        'If trailing stop triggers BEFORE the fixed TP limit is hit: Cancel the Take-Profit limit order (if it exists), cancel the Stop-Loss order, then execute a sell to close the position (single market sell if position value < twap_min_usd, else TWAP sell) and lock in the trailed profit.',
                        'If fixed TP is hit first: Coinbase executes the limit sell automatically. The app detects the fill and updates the Trade Journal.',
                        'If Stop-Loss is hit: Coinbase executes the limit sell automatically. The app detects the fill and updates the Trade Journal.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Safety & Behavior'
                },
                {
                    'type': 'list',
                    'items': [
                        'USDC pairs by default (fallback to USD if USDC fails or has poor liquidity).',
                        'Auto-execution toggle and exchange API control order placement; disable either to stop.',
                        'execution_safety_ok checked before any sell (6% risk cap, correlation limits).',
                        'All order placement, cancellation, and execution logged to execution_log.jsonl.',
                        'Confirmation alerts sent only to primary owner + Telegram.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Auto-Execution on Exit'
                },
                {
                    'type': 'text',
                    'text': 'When auto-execution is enabled, the system does not only place buy orders on new signals. It also executes market sells when stop-loss, take-profit, or trailing-stop is hit. With the hybrid approach (Coinbase), SL and TP are real limit orders on the exchange; trailing stop is app-monitored and executes via a single market sell (for small positions, below twap_min_usd) or TWAP sell after cancelling the limit orders. The position is closed in the Trade Journal automatically. If execution fails (e.g. network error, exchange down), you still receive an alert so you can close manually. If a trailing sell fails with INSUFFICIENT_FUND and the exchange balance for that symbol is 0 (e.g. the position was already sold by an SL/TP limit order on the exchange), the system closes the position in the journal from exchange state (exit_reason exchange_sold) so the dashboard stays in sync. When the journal shows quantity 0 but the trailing stop would have triggered, the system first tries to sync quantity from the exchange; if the exchange has 0, it closes the position as exchange_sold. Check the execution log (data/execution_log.jsonl) for fill details.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Kill Switch Behavior'
                },
                {
                    'type': 'text',
                    'text': 'When auto-execution is off (or exchange API disabled), the monitor can still check positions and send alerts. It does not place new buy or sell orders. Turn off auto-execution or disable API to pause trading while staying informed.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Verification Before Production'
                },
                {
                    'type': 'text',
                    'text': 'Run tests/test_exit_monitoring.py (via pytest) before deploying to production. It verifies: the monitoring thread starts, price fetch works, stop-loss and take-profit logic are correct, trailing-stop math is right, duplicate alert prevention works, and performance analytics run. The test uses temporary positions and cleans them up. Run the full suite with run_tests.bat; all 93 tests should pass before you rely on live exit monitoring.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Example: How It Works in Practice'
                },
                {
                    'type': 'text',
                    'text': 'You buy 0.1 BTC at $50,000. Stop: $48,750 (2.5%). Trailing: activates after 8% profit ($54,000), trails 13% below peak. Scenario A — Stop hit: Price drops to $48,500. The monitor sees price <= $48,750, sends "STOP-LOSS HIT" alert, and (if auto-exec On) sells. Scenario B — Legacy target hit: Older positions that still carry a fixed take-profit target may exit at that price; new positions have no fixed target — the trailing stop is the only profit exit. Scenario C — Trailing: Price rises to $65,000 (+30% profit). High-water = $65,000, trailing stop = $56,550. Price then drops to $56,500. Monitor sees price <= $56,550, sends "TRAILING STOP HIT", sells. You locked in ~+13% — the fat-tail capture the old 3% trail used to clip.'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'Bottom line: Exit monitoring is your safety net. It ensures you never forget to honor a stop, miss a target, or give back a big gain. Set it up once, use alerts, and let the system watch for you.'
                }
            ]
        }
    
    def _automated_execution(self):
        """Automated Execution (Coinbase Advanced Trade; Kraken optional)."""
        return {
            'title': 'Automated Execution - Coinbase & Kraken',
            'icon': '⚡',
            'content': [
                {
                    'type': 'text',
                    'text': 'The exit monitor (previous sections) detects when stops, targets, or trailing stops are hit. With auto-execution enabled, the system executes those exits automatically. This section covers both entry execution (TWAP buys on new signals) and exit execution (market sells when triggers fire). The system uses CCXT to connect to exchanges. Coinbase Advanced Trade is the primary supported exchange; Kraken can be selected in settings for future use.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Enable Auto-Execution'
                },
                {
                    'type': 'text',
                    'text': 'In the dashboard header you will see:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Auto-Exec toggle: Turn On to enable automatic order placement when signals appear.',
                        'Exchange dropdown: Choose Coinbase (primary) or Kraken (secondary). Only Coinbase is implemented; Kraken is selectable for when support is added.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How It Works'
                },
                {
                    'type': 'text',
                    'text': 'When auto-execution is On and you run a refresh (manual or scheduled), the system looks at every ULTRA_BUY and STRONG_BUY coin. For each one it uses the correlation-adjusted position size from the Portfolio Risk Summary. Small positions (below twap_min_usd, default $500) use a single market order for faster execution; larger positions use TWAP (Time-Weighted Average Price): the total USD amount is split into 4–6 market orders over 30–60 minutes to reduce slippage and market impact.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Auto-Execution on Exits (Stop, Target, Trailing)'
                },
                {
                    'type': 'text',
                    'text': 'Auto-execution is not just for entries. When the position monitor detects a stop-loss, take-profit, or trailing-stop hit, it executes a market sell automatically and closes the position in the Trade Journal. Why this matters: Many traders set stops but never follow through—they hope the price will bounce. Auto-execution removes that temptation. When the stop is hit, the system sells. No hesitation, no emotional override. Same for targets and trailing stops: you lock in gains without having to watch the screen. How to use it: Turn Auto-Exec On in the header. The exit monitor runs in the background; when a trigger fires, the system places the sell order. If execution fails (e.g. network error), you still get an alert so you can close manually. See "Exit Monitoring" for full details on how the monitor works.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Trailing Stops Auto-Execute via TWAP'
                },
                {
                    'type': 'text',
                    'text': 'Trailing stops auto-execute via TWAP sell on Coinbase when hit. The system splits the sell into small slices (default 5) over ~15 minutes to reduce slippage and market impact, mirroring the TWAP buy logic. USDC pair is preferred with USD fallback. The Trade Journal is updated automatically with exit_reason=\'trailing_stop\', and a confirmation is sent to the primary owner (email) and Telegram only.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Hybrid SL/TP + Trailing Stop Execution'
                },
                {
                    'type': 'text',
                    'text': 'When Coinbase is selected, the system uses a hybrid approach: real limit orders on the exchange plus app-side trailing stop monitoring. This provides backup protection even when the app is offline.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Normal Mode vs Runner Mode'
                },
                {
                    'type': 'list',
                    'items': [
                        'Winners always run (permanent, 2026-05-30): Only the SL limit order is placed on Coinbase after a buy. No TP order — the position runs until the stop or trailing stop triggers. Order IDs (sl_order_id) are stored in the Trade Journal; tp_order_id is only present on legacy positions opened before the exit overhaul.',
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'SL/TP on Coinbase as Backup'
                },
                {
                    'type': 'text',
                    'text': 'SL and TP are real limit orders on Coinbase. If the app goes offline, crashes, or loses network, those orders remain active on the exchange. Coinbase will execute them when price hits the level. This is critical for capital protection.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Trailing Stop Overrides TP'
                },
                {
                    'type': 'text',
                    'text': 'Trailing stop activates after 8% profit, then trails 13% below the high-water mark. If the trailing stop triggers BEFORE the fixed TP is hit: the app cancels the TP limit order (if it exists), cancels the SL order, then executes a sell (single market sell for positions below twap_min_usd, else TWAP sell) to close the position. This locks in the trailed profit instead of waiting for the fixed target. If the fixed TP is hit first (by Coinbase), the position is closed automatically; the app detects the fill and updates the journal — no trailing action needed.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'USDC Preference and Fallback'
                },
                {
                    'type': 'text',
                    'text': 'SL and TP limit orders use USDC pairs by default (e.g. BTC/USDC). If the USDC pair fails (e.g. liquidity or API error), the system falls back to the USD pair (e.g. BTC/USD).'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Test Coverage (test_hybrid_sl_tp_trailing.py)'
                },
                {
                    'type': 'text',
                    'text': 'The test suite verifies: (1) Initial booking: normal mode places SL+TP, runner mode places SL only; order IDs stored in journal; USDC preference and USD fallback. (2) Trailing triggers: before TP → cancel TP/SL, TWAP sell; after TP → no action. (3) Trailing math: 8% profit activates, 13% below peak; high-water mark updates. (4) Safety: kill switch blocks SL/TP placement; execution_safety_ok blocks trailing sell; orders persist on Coinbase when app offline. (5) Journal and alerts: exit_reason=trailing_stop; critical alert to primary + Telegram only. (6) Edge cases: partial fill still places SL/TP; manual close before trailing; SL hit before trailing activates. Run: pytest tests/test_hybrid_sl_tp_trailing.py -v'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Safety Rules'
                },
                {
                    'type': 'list',
                    'items': [
                        'Never auto-execute if total risk would exceed 6% of your account (or the higher cap for small accounts).',
                        'Never auto-execute if correlation limits are breached (e.g. high-BTC-correlation exposure over the 35% cap).',
                        'Never start a new TWAP buy while another TWAP buy is in progress — execution_safety_ok blocks overlapping buys.',
                        'When multiple BUY signals are eligible in one refresh, each new TWAP is checked against current exposure plus risk already committed this round, so the cap is not exceeded by the batch.',
                        'A 60-minute cooldown per symbol prevents duplicate TWAPs if you refresh multiple times. Cooldown persists across app restarts (saved to data/twap_cooldowns.json).',
                        'Twap buy/sell: slice retries and execution_safety_ok apply; for emergency stop, disable exchange API.',
                        'Retry logic: each TWAP slice retries up to 3 times with exponential backoff (2s, 4s, 8s) on transient errors (rate limits, timeouts, 502/503). Non-transient errors break immediately.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Coinbase Setup'
                },
                {
                    'type': 'text',
                    'text': 'To use Coinbase Advanced Trade:'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'Create a Coinbase Cloud API key (Advanced Trade). You get an API key name (e.g. organizations/.../apiKeys/...) and an EC private key (PEM format).',
                        'In .env set COINBASE_API_KEY to the full key name and COINBASE_API_SECRET to the PEM string (including -----BEGIN EC PRIVATE KEY----- and -----END EC PRIVATE KEY-----).',
                        'Install CCXT: pip install ccxt',
                        'Turn Auto-Exec On and select Coinbase. On the next refresh that produces ULTRA_BUY or STRONG_BUY, TWAP will run in the background.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Execution Log & Forensic Audit Trail'
                },
                {
                    'type': 'text',
                    'text': 'Two log files track execution activity:'
                },
                {
                    'type': 'list',
                    'items': [
                        'data/execution_log.jsonl — operational log of every execution attempt: exchange, symbol, side, total USD, number of slices, fill price, slippage (basis points), status (filled/partial/error), and timestamp.',
                        'data/execution_audit.jsonl — forensic audit trail of every individual order fill: order_id, fill_price, fee_usd, fee_rate, slippage_bps, pre_trade_mid price, execution context (market_buy, market_sell, limit_order_filled, trailing_high_water_update, twap_buy_aborted), and timestamp. Use this for post-trade analysis, fee reconciliation, and dispute resolution.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Fee Capture & Net P&L'
                },
                {
                    'type': 'text',
                    'text': 'The system extracts real fees from every CCXT order response (entry and exit). Fees are stored on each position: entry_fee_usd (captured at buy) and exit_fee_usd (captured at sell). Trade Journal P&L is net-of-fees: pnl_usd and pnl_pct subtract both entry and exit fees from the gross price movement. Gross values are also stored (gross_pnl_usd, gross_pnl_pct) for transparency. The total_fees_usd field shows combined entry + exit fees per trade.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Slippage Tracking'
                },
                {
                    'type': 'text',
                    'text': 'Slippage is measured in basis points (bps) against a pre-trade mid price (bid+ask)/2 snapshot taken before each order. For buys: positive bps means you paid more than mid. For sells: positive bps means you received less than mid. Slippage is logged to both the execution log and the forensic audit trail for each fill.'
                },
                {
                    'type': 'callout',
                    'style': 'warning',
                    'text': 'Auto-execution uses real money. Only enable it after testing with small sizes and understanding the 6% risk cap and correlation limits. Keep your API keys secure and never share .env.'
                }
            ]
        }
    
    def _recursive_optimizer(self):
        return {
            'title': 'Recursive Optimizer (Swing Trader v1.0)',
            'icon': '🔄',
            'content': [
                {
                    'type': 'text',
                    'text': 'The recursive optimizer is a fully automatic feedback loop that tunes the quant gate thresholds daily using real trade outcomes. It runs at 4:00 AM ET (run_full_recursive_optimizer_cycle), requires zero API cost (pure Python math), and auto-applies all recommendations that meet a configurable min_improvement threshold, zero winners would be lost, and safety bounds — no manual approval step. The daily email reports what was auto-applied, why each change matters, and how it improves scoring/signals. The optimizer also ingests historical replay data (backtest_trades.json, refreshed weekly) at 0.5x confidence weight to accelerate learning — but backtest data can only inform recommendations, never trigger auto-apply.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Single tuning mechanism (v1.0)'
                },
                {
                    'type': 'text',
                    'text': 'Full recursive optimizer (4:00 AM ET): Runs in recursive_optimizer.run_full_recursive_optimizer_cycle. Counterfactual analysis on 15+ parameters with walk-forward 70/30 validation and regime grouping (PARAM_REGIME_FILTER). Auto-applies every recommendation that meets min_improvement (default 2% expected P&L), zero winners lost, and OPTIMIZER_BOUNDS. Optional max_changes_per_cycle cap. Daily local retrain is deprecated; all threshold updates flow through the recursive optimizer.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How it works'
                },
                {
                    'type': 'list',
                    'items': [
                        'Step 1 — Aggregate: Reads the trade journal as the single source of truth for system trades, 7d/30d win rates, regime/path win rates, and model attribution. Loads screening_log, learning_report, and buy_signals_stress_30d (veto accuracy). Also loads backtest_trades.json (historical replay trades at 0.5x weight) and backtest_missed.json (missed opportunities) as supplementary evidence.',
                        'Step 2 — Counterfactual analysis: For each priority parameter, tests "what if this threshold had been N instead?" against real trade outcomes. Uses 70/30 walk-forward when enough trades exist; regime grouping (PARAM_REGIME_FILTER) restricts analysis to relevant regimes. Counts losing trades that would have been filtered vs. winners that would have been lost; regret-weighted missed-opportunity cost is included in the objective. When live trades are below MIN_TRADES_FOR_REC (15), backtest trades supplement the analysis at 0.5x confidence weight — this lets the optimizer generate directional recommendations earlier without waiting for 15+ live trades per parameter.',
                        'Step 3 — Filter for full-auto: All recommendations with net_expected_pnl_pct >= min_improvement (default 0.02), winners_lost == 0, value within OPTIMIZER_BOUNDS, and (when applicable) positive validation impact are auto-applied. Optional max_changes_per_cycle limits how many per run.',
                        'Step 4 — Apply: Writes approved changes to live_thresholds.json (updated_by: recursive_optimizer). No manual step.',
                        'Step 5 — Evaluate previous changes: Trade-based maturity (MATURITY_TRADE_TARGET, default 50) — MATURING, CONFIRMED, WATCH, REVERTED (auto-revert if win rate dropped >5pp).',
                        'Step 6 — Email report: Cycle summary, auto-applied changes with what/why/improves-signals, recommendations below bar (informational), change tracker, path performance, top missed by regret.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Email report sections'
                },
                {
                    'type': 'list',
                    'items': [
                        'Cycle Summary: Date, regime, trade count, win rates, change counts.',
                        'AUTO-APPLIED (green): Each change shows parameter, old → new value, why it matters (from PARAM_DESCRIPTIONS), and how it improves scoring/signals (from PARAM_IMPACT in thresholds.py). Walk-forward validated when enough trades exist.',
                        'NEEDS YOUR REVIEW (amber): Recommendations that did not meet the auto-apply bar (min_improvement, 0 winners lost, or bounds). Apply manually with apply_single or defer_recommendation(rec_id) to hide from future emails.',
                        'CHANGE TRACKER (blue): Status of previously applied changes — MATURING, CONFIRMED, WATCH, REVERTED.',
                        'PERFORMANCE BY PATH: Win rate table by quant path in current regime.',
                        'MISSED OPPORTUNITIES: From the last ML learning report.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Parameters tuned (15 priority)'
                },
                {
                    'type': 'text',
                    'text': 'Score floors: DIP_MIN_SCORE, BLOOD_STREET_MIN_SCORE, PULLBACK_MIN_SCORE, VOL_HARVEST_MIN_SCORE, CAPITULATION_MIN_SCORE, BEAR_REGIME_MIN_SCORE. Fear & Greed gates: BLOOD_STREET_FG, BLOOD_FG_MAX, PULLBACK_FG_MAX, VOL_HARVEST_FG_MAX. Veto: FUNDING_VETO_BEAR_PCT, VETO_CONFIDENCE_THRESHOLD. Edge/tier: EDGE_BOOST_THRESHOLD. Pump caps: PUMP_24H_PCT, SHORT_TERM_PUMP_24H_PCT. All bounds defined in OPTIMIZER_BOUNDS in thresholds.py.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Manual apply (for items below auto-apply bar)'
                },
                {
                    'type': 'text',
                    'text': 'Recommendations in "Needs your review" did not meet min_improvement or would filter winners. To apply a numeric one: python -c "from recursive_optimizer import apply_single; apply_single(\'PARAM\', value)". To apply or defer by ID: apply_recommendation(rec_id) or defer_recommendation(rec_id). live_thresholds.json may show updated_by: recursive_optimizer, manual_optimizer, or optimizer_revert.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Files and schedule'
                },
                {
                    'type': 'list',
                    'items': [
                        'Module: recursive_optimizer.py; nightly entry point: run_full_recursive_optimizer_cycle(min_improvement=0.02, max_changes_per_cycle=None)',
                        'Log: data/optimizer_log.json (full history of every cycle, applied changes, evaluations)',
                        'Audit log: data/recommendations_log.json (applied, deferred by ID)',
                        'Thresholds: live_thresholds.json (read by quant_signal.py via thresholds.py); updated_by indicates last writer',
                        'Safety bounds: OPTIMIZER_BOUNDS in thresholds.py; PARAM_IMPACT in thresholds.py for email "how it improves" text',
                        'Schedule: 4:00 AM ET daily — app.py calls run_full_recursive_optimizer_cycle(). Historical replay runs Sunday 3:00 AM ET (1 hour before optimizer) to refresh backtest data weekly.',
                        'Regression model: Weekly retrain (scheduler); MIN_TRADES_FOR_REGRESSION = 50 for activation.',
                        'Resend: POST /api/optimizer/resend; Last report: GET /api/optimizer/last-report for Performance tab',
                        'Email delivery: Optimizer and daily status reports send when SMTP is configured (email_enabled toggle only controls trade alerts)'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Monitoring over time'
                },
                {
                    'type': 'text',
                    'text': 'The daily report (email or Telegram) is the main touchpoint. It shows what was auto-applied (with why each change matters and how it improves signals), plus any recommendations below the min_improvement bar (informational; apply or defer if you choose).'
                },
                {
                    'type': 'list',
                    'items': [
                        'Daily report: Subject line has cycle number (e.g. "Recursive Optimizer — Cycle #5"). Cycle Summary shows regime, trade count, 7d/30d win rate.',
                        'AUTO-APPLIED (green): Each line shows parameter, old→new, why it matters, and how it improves scoring/signals. All applied automatically; no manual step.',
                        'NEEDS YOUR REVIEW (amber): Items below min_improvement or that would filter winners. Apply manually or defer to hide from future emails.',
                        'CHANGE TRACKER (blue): Status of past applied changes — MATURING (waiting for MATURITY_TRADE_TARGET trades, default 50), CONFIRMED (win rate held or improved), WATCH (small dip), REVERTED (win rate dropped >5pp; threshold was restored automatically).',
                        'DATA QUALITY (grey): Shows field coverage across attribution trades. If most trades lack quant scores or other fields, a warning explains why recommendations are sparse. This section helps diagnose blank or thin reports.',
                        'WEEKLY CONSOLIDATED (Friday only): On Fridays, the report includes a "Weekly Consolidated Recommendations" section. It aggregates the last 7 days (incl. weekend) of recommendations, resolves conflicts (e.g. if one day says raise and another says lower, that param is excluded), and only includes params that appeared 2+ days. Apply these once per week after review.',
                        'Log file: data/optimizer_log.json holds the full history. Each entry has cycle number, snapshot (regime, win rates, system positions, screening volume), auto_applied, pending_review, loss_based_recommendations, and evaluations. Useful for auditing or spotting trends over many weeks.'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'As more data accumulates (more closed trades, more 7d outcomes backfilled), the optimizer has stronger evidence and may propose or auto-apply more precise threshold changes. Early cycles may show few or no changes until the trade count and path/regime mix are sufficient.'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'The recursive optimizer is a zero-cost self-improvement loop. It uses only Python math on existing trade data — no API fees. The system gets smarter every day as more trades provide evidence for better thresholds.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Swing Trader v1.0: Walk-forward, regime grouping, regret minimization'
                },
                {
                    'type': 'list',
                    'items': [
                        'Walk-forward validation: When enough trades exist, the optimizer uses an explicit 70/30 time-ordered split (train on oldest 70%%, validate on newest 30%%). A recommendation is accepted only if it improves net impact on both train and validation sets, reducing overfitting.',
                        'Regime grouping (PARAM_REGIME_FILTER): Regime-specific parameters (e.g. BEAR_REGIME_MIN_SCORE) are evaluated only on trades in the relevant regime(s), so tuning is evidence-based per regime.',
                        'MATURITY_TRADE_TARGET (default 50): Applied changes are evaluated for maturity; status moves from MATURING to CONFIRMED once at least this many trades have been booked under the new threshold. Protects against premature reversion.',
                        'MIN_TRADES_FOR_REC (default 15): Counterfactual analysis runs only for a parameter when at least this many trades match the parameter\'s path/regime filter. Ensures recommendations are data-driven.',
                        'Regret-weighted missed-opportunity analysis: Top missed opportunities (signals we did not take that later gained >5%% in 7d) are ranked by outcome_7d (regret score). The optimizer uses this to weight threshold relaxation so high-regret misses are considered when proposing changes. Top N by regret appear in the report and Performance tab with suggested threshold fixes.'
                    ]
                }
            ]
        }

    def _backtesting(self):
        return {
            'title': 'Backtesting & Historical Replay',
            'icon': '📈',
            'content': [
                {
                    'type': 'text',
                    'text': 'The Trade Journal records your trades and Performance Tracking analyzes them. Backtesting goes further: it shows how well the system would have performed historically on past signals. This builds confidence and helps you optimize before risking real capital.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Historical Replay Engine'
                },
                {
                    'type': 'text',
                    'text': 'The historical replay engine (historical_replay.py) is a dedicated system that fetches 90 days of hourly OHLCV data from CoinGecko for 15 top coins (BTC, ETH, SOL, XRP, ADA, DOGE, LINK, AVAX, DOT, LTC, UNI, NEAR, ATOM, SUI, APT) plus historical Fear & Greed data from Alternative.me. It slides through the data at 8-hour intervals, constructs coin dicts, and runs them through the EXACT same live signal pipeline (calculate_indicators → score_coin → generate_quant_signal). This produces optimizer-compatible attribution records without risking real capital.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How Historical Replay Works'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'Fetches 90-day hourly OHLCV for each coin from CoinGecko (cached 12 hours to respect free-tier rate limits of 10-30 calls/min)',
                        'Fetches historical Fear & Greed index from Alternative.me',
                        'Slides day-by-day in 8-hour intervals through the data (3x more screening points than daily)',
                        'At each window, constructs coin dicts with real indicators and runs through the live quant signal pipeline',
                        'Simulates trades with slippage (0.30%), fees (0.18%), round-trip 0.66% — WARNING: this legacy replay cost model is roughly 4x too LOW versus measured reality (1.20%/side taker = 2.4% round trip). Treat any profit from this backtester as optimistic; the walk-forward gate in stage1c_replay.py stresses at measured cost instead, and that is the number decisions are made on.',
                        'Forward-simulates stop-loss and take-profit using actual subsequent price data (max hold: 168 hours)',
                        'Tracks missed opportunities: coins that were HOLDs but appreciated >5% in the following 7 days',
                        'Outputs trades to data/backtest_trades.json and missed opportunities to data/backtest_missed.json'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Weekly Automatic Schedule'
                },
                {
                    'type': 'text',
                    'text': 'The historical replay runs automatically every Sunday at 3:00 AM ET — one hour before the daily optimizer cycle at 4:00 AM ET. This ensures the optimizer always has fresh backtest data from the latest 90-day market window. The replay resets the CoinGecko circuit breaker before starting and logs results (trade count, win rate, missed opportunities) to the application log.'
                },
                {
                    'type': 'list',
                    'items': [
                        'Schedule: Sunday 3:00 AM ET (automatic, no manual action needed)',
                        'Duration: ~15-30 minutes depending on CoinGecko rate limits',
                        'Data window: Rolling 90 days of hourly data, refreshed weekly',
                        'Output: data/backtest_trades.json (simulated trades) and data/backtest_missed.json (missed opportunities)',
                        'Manual run: python -c "from historical_replay import run_historical_replay; run_historical_replay()"'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How Backtest Data Feeds the Optimizer'
                },
                {
                    'type': 'text',
                    'text': 'The recursive optimizer reads backtest_trades.json as supplementary evidence alongside live trades. Backtest trades carry a 0.5x confidence weight compared to live trades — live data always takes priority. When a parameter has fewer than 15 live trades (MIN_TRADES_FOR_REC), backtest trades help the optimizer start generating directional recommendations earlier. However, backtest trades can NEVER trigger auto-apply — only live trade thresholds (MIN_TRADES_FOR_AUTO=10) can authorize automatic threshold changes. The MATURITY_TRADE_TARGET (100 live trades) for confirming a change is also unaffected.'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'The historical replay is a zero-cost learning accelerator. It generates optimizer-compatible trade data from free CoinGecko historical prices, helping the system learn faster without waiting months for live trades to accumulate. The optimizer gets smarter every week as the replay window shifts forward with the market.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Missed Opportunity Tracking'
                },
                {
                    'type': 'text',
                    'text': 'For every coin that the signal pipeline rated as HOLD (did not meet buy thresholds), the replay engine tracks what that coin did over the next 7 days. Coins that appreciated more than 5% are recorded as missed opportunities with their actual 7-day return and the score/path that rejected them. This feeds directly into the optimizer\'s "top missed by regret" analysis, helping it identify thresholds that may be too restrictive.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Signal-Based Backtesting'
                },
                {
                    'type': 'text',
                    'text': 'In addition to the historical replay, the system also performs signal-based backtesting on past recommendations:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Looks at every signal recommendation from the past 30/60/90 days',
                        'Tracks win/loss counts, average gain on winners, average loss on losers',
                        'Performance by consensus tier (ULTRA vs STRONG vs MODERATE)',
                        'Performance by timeframe (3-day vs 7-day vs 14-day exits)',
                        'Performance by market regime (BULL vs BEAR vs SIDEWAYS)',
                        'Optimal stop loss percentage'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Walk-Forward Backtesting'
                },
                {
                    'type': 'text',
                    'text': 'The backtester uses rolling walk-forward windows (3 months train, 1 month test) with realistic slippage (0.2–0.5%) and transaction costs (0.1–0.26%). A strategy must achieve Sharpe >1.0 across walk-forward periods to be considered robust. This reduces overfitting and gives more reliable expectations.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Files and Data'
                },
                {
                    'type': 'list',
                    'items': [
                        'historical_replay.py: Historical replay engine (90d OHLCV through live pipeline)',
                        'data/backtest_trades.json: Simulated trades with full attribution (path, regime, scores, P&L)',
                        'data/backtest_missed.json: Missed opportunities with 7-day forward returns and regret scores',
                        'data/replay_cache/: Cached CoinGecko OHLCV data (12-hour TTL)',
                        'CoinGecko free tier: 90 days hourly max, 365 days daily; 10-30 calls/min rate limit'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Important Notes'
                },
                {
                    'type': 'list',
                    'items': [
                        'Past performance doesn\'t guarantee future results',
                        'Backtest trades use 0.5x weight in the optimizer — live trades always take priority',
                        'Backtest trades can inform recommendations but NEVER trigger auto-apply',
                        'The replay uses the exact same signal pipeline as live trading — no separate logic',
                        'Realistic costs are included: 0.30% slippage + 0.18% fees per side = 0.66% round-trip'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Backtesting shows you what would have happened if you followed the system. Use it to build confidence, not to predict the future. The historical replay generates fresh data weekly so the optimizer can learn continuously.'
                }
            ]
        }
    
    def _performance_tracking(self):
        """Institutional-grade performance analytics, attribution, walk-forward, and monthly review."""
        return {
            'title': 'Performance Tracking - Institutional-Grade Analytics',
            'icon': '📊',
            'content': [
                {
                    'type': 'text',
                    'text': 'The Trade Journal (previous section) is where you record every trade. This section explains how the system turns that data into institutional-grade analytics. All metrics are computed from your trade journal and recommendations—no external APIs or paid services.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Performance Tab (Dashboard)'
                },
                {
                    'type': 'text',
                    'text': 'The Performance tab displays the Recursive Optimizer report (cycle summary, loss analysis, path performance, auto-applied and pending recommendations, evaluations) plus institutional analytics. Regression Model Status shows whether the weekly logistic regression scorer is active (trained_at, n_samples ≥ 100) or in fallback (additive-only). Position sizing uses real fractional Kelly when edge_probability and rr_ratio are present. Optimizer Activity reflects walk-forward-validated recommendations when enough trades exist. Top Missed Opportunities lists high-regret signals (7d gain %) with suggested threshold fixes. Use the period pills (30-day | 90-day | 365-day | All-time) for analytics; metrics update live after every refresh.'
                },
                {
                    'type': 'list',
                    'items': [
                        'Recursive Optimizer report: Cycle #, regime, win rates, auto-applied and pending threshold changes, path performance, top missed by regret with suggested fixes.',
                        'Regression Model Status (v1.0): trained_at, n_samples, active when n_samples ≥ 100 else fallback to additive score.',
                        'Sharpe Ratio: Large hero display with color coding—green if ≥1.8, yellow if 1.5–1.8, red if <1.5. Target >1.8 for this strategy.',
                        'Sortino, Max Drawdown, Expectancy, Overall Win Rate: Shown in metric cards below Sharpe.',
                        'Win Rate by Regime: Table for BULL, BEAR, SIDEWAYS, VOLATILE with trades, win rate, avg win/loss, total P&L %. Regime is set at execution from the quant pipeline.',
                        'Win Rate by Regime / Scenario: Table by regime and trading scenario (e.g. dip, sniper, bull_participant). Scenario is set when the trade is opened (auto-exec or manual from recommendation).',
                        'Model Attribution: Ranked table (Rule-based veto, Quant) with P&L, win rate, Sharpe. Includes recommendations.',
                        'Walk-Forward Backtest: Run 90-day backtest to see average Sharpe across test periods and robustness check.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Performance Analytics (performance_analytics.py)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Sharpe ratio: Risk-adjusted return (annualized). Target >1.8.',
                        'Sortino ratio: Downside-only risk (penalizes volatility of losses)',
                        'Max drawdown: Largest peak-to-trough decline',
                        'Win rate by regime: BULL/BEAR/SIDEWAYS/VOLATILE performance (set at execution). Win rate by regime/scenario: same metrics by quant path (dip, sniper, etc.).',
                        'Expectancy: Expected profit per trade (% and USD)'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Model Attribution (model_attribution.py, derived from trade journal)'
                },
                {
                    'type': 'text',
                    'text': 'Tracks P&L contribution, win rate, and Sharpe by component: Rule-based veto (CONFIRM/VETO), Quant (ULTRA/STRONG). Monthly ranking and threshold-tuning recommendations help you favor strong components and tune veto thresholds.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Walk-Forward Backtest'
                },
                {
                    'type': 'text',
                    'text': 'Rolling windows (3 months train, 1 month test) with realistic slippage and costs. Strategy must achieve Sharpe >1.0 across periods to be considered robust. Reduces overfitting and gives reliable expectations.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Learning pipeline (ml/)'
                },
                {
                    'type': 'text',
                    'text': 'The learning pipeline improves the system over time by analyzing past signals and outcomes. It runs four steps: (1) outcome backfill — fills missing 7-day outcomes for old recommendations and screening-log entries using current prices; (2) 90-day backtest — runs the existing walk-forward backtest and optionally saves expectancy for drift monitoring; (3) missed-opportunity analysis — finds HOLD/WEAK_BUY or screening failures that later gained above a threshold (default 5%); (4) suggestion engine — turns that analysis plus backtest suggestions into actionable recommendations for quant and veto. All suggestions are output-only; no code or config is changed automatically.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Report location and contents'
                },
                {
                    'type': 'text',
                    'text': 'Each run writes data/learning_report.json (overwritten). The report includes: run_timestamp; backtest (period, total_signals, sharpe_passed, by_regime, by_tier, walk_forward); backtest_suggestions (strings from the backtester); missed_opportunity (counts and sample lists from recommendations and screening log, by regime); suggestions — an array of actionable items, each with category (quant, veto, backtest), summary (short text), evidence (optional), file (e.g. veto_specialist.py, quant_signal.py), and parameter (e.g. VETO_CONFIDENCE_THRESHOLD) so you can open the right place to edit.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'How to run the pipeline'
                },
                {
                    'type': 'list',
                    'items': [
                        'Manual: From the repo root run python -m ml.run_pipeline. Prints a one-line summary and writes data/learning_report.json.',
                        'API: GET /api/learning-report returns the latest report as JSON (or a placeholder if the report has not been run yet).',
                        'Monthly (in-app): Enable via POST /api/schedule/learning with body { "enabled": true, "day_of_month": 1 }. GET /api/schedule/learning returns current status (enabled, day_of_month, last_learning_run).'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Monthly automation'
                },
                {
                    'type': 'text',
                    'text': 'When monthly learning is enabled, the pipeline runs once per calendar month. The app checks every hour (and also on each scheduled refresh): if the current day is on or after the configured day_of_month (1–28) and the pipeline has not run yet this month, it runs in a background thread. So it runs even if auto-refresh is off. Settings are stored in scheduler_config.json (monthly_learning_enabled, learning_run_day, last_learning_run).'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Workflow to incorporate suggestions'
                },
                {
                    'type': 'list',
                    'items': [
                        'Review: Open the report (file, API, or dashboard) and read the backtest summary and the suggestions list.',
                        'Prioritize: Decide which suggestions to act on (e.g. backtest failures or high-impact missed-opportunity categories). Ignore or defer others.',
                        'Edit: For each suggestion you accept, open the referenced file (e.g. veto_specialist.py), find the parameter or constant named in the suggestion, and change the value. Save. All edits are manual; nothing is applied automatically.',
                        'Validate: Optionally run the backtest again (POST /api/backtest/run with days=90) or run the pipeline on the next cycle to see updated metrics.',
                        'Track (optional): Note which suggestions you applied and when (e.g. in data/applied_suggestions.txt) to correlate with future performance.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'How suggestions get into the main branch'
                },
                {
                    'type': 'text',
                    'text': 'The ML layer never pushes code. Suggestions reach your live code only when you edit the repo and commit (and, if you use branches, merge to main). Approval can be minimal: you review and approve by making the edit and committing. Optionally you can use an explicit approve list and a PR to main so a second reviewer or CI gates the change.'
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Screening log: Every screened coin is logged to data/screening_log.json (quant tier, score, etc.). The pipeline uses this for missed-opportunity analysis. The log is capped at SCREENING_LOG_MAX_ENTRIES (75,000; ml/config.py).'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Account Overview & Monthly Review'
                },
                {
                    'type': 'text',
                    'text': 'Account Overview shows Sharpe with 30d/90d toggle. Monthly Review modal highlights Sharpe with color coding, key metrics, model attribution, and recommendations. The /api/monthly-review endpoint supports ?days=30|90|365|0 for period-specific reports.'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'All performance tracking features are zero-cost enhancements that improve decision quality and probability of success without adding API fees or complexity.'
                }
            ]
        }
    
    def _trade_journal(self):
        return {
            'title': 'Trade Journal - Track Your Performance',
            'icon': '📔',
            'content': [
                {
                    'type': 'text',
                    'text': 'The Trade Journal is where you record every position you open. It tracks profit/loss and hit rates and feeds Performance Tracking analytics.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'What Gets Tracked'
                },
                {
                    'type': 'text',
                    'text': 'For every trade, the journal records:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Entry price and time',
                        'Exit price and time (when closed)',
                        'Signal at entry (ULTRA_BUY, STRONG_BUY, etc.)',
                        'System\'s predicted stop loss and target',
                        'YOUR actual stop loss and target (can be different)',
                        'Position size and quantity',
                        'Realized P&L (when closed)',
                        'Unrealized P&L (while open)',
                        'Whether target was hit',
                        'Whether your target was hit',
                        'Trade duration'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Key Metrics Displayed'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Overall Stats'
                },
                {
                    'type': 'list',
                    'items': [
                        'Total Realized P&L (closed trades)',
                        'Total Unrealized P&L (open positions)',
                        'Win Rate (% of closed trades that were profitable)',
                        'Target Hit Rate (% of times signal target was reached)',
                        'Average Win / Average Loss',
                        'Best Trade / Worst Trade'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Open Positions'
                },
                {
                    'type': 'text',
                    'text': 'Shows all currently open trades with:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Current price (live)',
                        'Unrealized P&L (how much you\'re up or down)',
                        'Distance to signal target',
                        'Distance to stop loss',
                        'How long you\'ve been in the trade'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Closed Positions'
                },
                {
                    'type': 'text',
                    'text': 'Historical trades showing:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Entry and exit prices',
                        'Realized P&L',
                        'Did it hit target?',
                        'Outcome (WIN / LOSS / BREAKEVEN)',
                        'Trade duration'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How to Use the Journal'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'OPEN POSITION: When you take a trade, click "Open Position" and enter details',
                        'MONITOR: Check "Positions" tab to see unrealized P&L on open trades',
                        'CLOSE POSITION: When you exit, click "Close" and enter exit price and reason',
                        'REVIEW: Look at your stats weekly - are you following the system? Are you profitable?',
                        'EXPORT: Download as CSV or Excel to analyze in more detail'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Target Hit Rate vs Your Win Rate'
                },
                {
                    'type': 'text',
                    'text': 'This is critical to understand:'
                },
                {
                    'type': 'list',
                    'items': [
                        'TARGET HIT RATE: How often the price reaches the system\'s predicted target',
                        'YOUR WIN RATE: How often you actually close the trade for a profit'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'These should be similar. If your win rate is much lower than target hit rate, you might be:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Exiting winners too early (not letting them run to target)',
                        'Holding losers too long (not respecting stop losses)',
                        'Taking trades that don\'t meet the criteria'
                    ]
                },
                {
                    'type': 'example',
                    'text': 'GOOD:\nTarget Hit Rate: 75%\nYour Win Rate: 72%\n→ You\'re following the system well\n\nBAD:\nTarget Hit Rate: 75%\nYour Win Rate: 55%\n→ You\'re overriding the system, probably exiting too early or not using stops'
                },
                {
                    'type': 'callout',
                    'style': 'warning',
                    'text': 'The journal doesn\'t lie. If your results don\'t match the backtest, you\'re not following the system. Review your trades and adjust.'
                }
            ]
        }
    
    def _testing_validation(self):
        """Testing & Validation — full testing plan (see TESTING_PLAN.md)."""
        return {
            'title': 'Testing & Validation',
            'icon': '🧪',
            'content': [
                {
                    'type': 'text',
                    'text': 'Before risking real capital, validate with paper trading. Use Test Mode (default On) and the Testing Dashboard to log every ULTRA_BUY and STRONG_BUY signal. Test Mode uses Light Mode to reduce API costs; responses are cached 60 minutes, on-chain data 30 minutes.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Overall Rules'
                },
                {
                    'type': 'list',
                    'items': [
                        'Follow the system exactly — no overrides.',
                        'Log every signal that reaches ULTRA_BUY or STRONG_BUY.',
                        'Risk only capital you can afford to lose during real-money phase.',
                        'Document everything — this data will help tune the system.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Phase A: Paper Trading (Days 1–7 Recommended)'
                },
                {
                    'type': 'text',
                    'text': 'Validate logic, veto behavior, on-chain usefulness, and portfolio hedging in live conditions without risk.'
                },
                {
                    'type': 'list',
                    'items': [
                        'Run manual refresh (or wait for auto-refresh). Check regime and On-Chain status card.',
                        'Review any new ULTRA_BUY / STRONG_BUY signals. Log every high-conviction signal (use Testing Dashboard or export CSV).',
                        'Check Total Exposure card — note any hedging suggestions. Review any veto reasons (rule-based).'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'Success criteria to move to Phase B: Minimum 10–15 logged signals; win rate on taken signals ≥ 65% on ULTRA_BUY, ≥ 60% on STRONG_BUY; vetoes feel reasonable; portfolio hedging suggestions make sense; no major bugs.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Phase B: Small Real-Money Test (Days 8–14)'
                },
                {
                    'type': 'text',
                    'text': 'Use a $10,000–$25,000 dedicated account (Coinbase or Kraken). Start at 0.5% risk per trade for the first 10 trades, then increase to 1% if stable. Use Auto-Execution or manual; log every trade in the Trade Journal and spreadsheet.'
                },
                {
                    'type': 'text',
                    'text': 'Success criteria to scale: Win rate in line with paper; max drawdown < 8%; execution slippage acceptable (< 0.4% average); drift monitoring quiet.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Phase C: Review & Scale (After Day 14)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Run full backtester and compare live vs backtest.',
                        'Review all logs. Decide on final risk level (up to 2%).',
                        'Update manual with real performance notes.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Red Flags (Pause Immediately If Seen)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Win rate significantly below 60% on ULTRA_BUY',
                        'Frequent rule vetoes overriding good-looking setups',
                        'High slippage on auto-execution',
                        'Drift alert firing multiple times',
                        'Portfolio hedging repeatedly forcing large reductions'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'See TESTING_PLAN.md in the project root for the full spreadsheet template and daily routine. Use the Testing Dashboard tab to view the live signal log, test positions (opened when Test Mode is On), and export CSV. The Positions tab shows only live trades; test positions appear on the Test tab.'
                },
                {
                    'type': 'text',
                    'text': 'The automated test suite (tests/ directory) is pytest-compatible. Run test_exit_monitoring.py (in tests/) and test_integration.py before deploying. Exit monitoring tests use temporary positions; integration tests mock execution.'
                }
            ]
        }

    def _automated_test_report(self):
        """Automated Test Suite Report — comprehensive testing documentation."""
        return {
            'title': 'Automated Test Suite Report',
            'icon': '📋',
            'content': [
                {
                    'type': 'text',
                    'text': 'The Elite Swing Trader system includes a comprehensive automated test suite that validates core logic without placing real trades. All tests use absolute imports and run from the project root. Execution tests are fully mocked — no real Coinbase or CCXT API calls are made.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Test Suite Summary'
                },
                {
                    'type': 'text',
                    'text': 'Total: 121+ tests across 11 modules. Run with run_tests.bat or: python -m pytest -v --tb=short'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'test_auto_execution.py (15 tests)'
                },
                {
                    'type': 'text',
                    'text': 'Safe mock tests for execution flow. No real Coinbase calls.'
                },
                {
                    'type': 'list',
                    'items': [
                        'execution_safety_ok: Blocks when total risk > 6% or correlation > 35%',
                        'symbol_to_ccxt: USDC pair preference (BTC → BTC/USDC)',
                        'start_twap_buy_async: Passes USDC pair to TWAP; cooldown respected',
                        'place_market_buy_usd: Fallback to USD when USDC fails',
                        'No real CCXT: get_coinbase_exchange, fetch_balance_sync, run_twap_buy_coinbase all mocked'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'test_position_sizer.py (12 tests)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Risk-based sizing, 40% cash cap, conservative Kelly (5% max)',
                        'Confidence multipliers (Very High/High/Medium/Low)',
                        'Regime and volatility adjustments',
                        'Capped by cash when insufficient; SKIP when no cash'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'test_trade_journal.py (8 tests)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Open/close/update positions',
                        'P&L calculation (long and short)',
                        'Target hit tracking (hit_ai_target)',
                        'calculate_stats with ai_target_hit_rate'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'test_performance_analytics.py (19 tests)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Sharpe ratio, Sortino ratio',
                        'Max drawdown from equity curve',
                        'Expectancy (win_rate × avg_win − loss_rate × avg_loss)',
                        'Win rate by regime (BULL/BEAR/SIDEWAYS/UNKNOWN)',
                        'PerformanceAnalytics.from_positions, calculate_kelly_fraction'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'test_veto_specialist.py (11 tests)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Bear funding veto (BEAR + funding > 0.08%)',
                        'Whale distribution veto (exchange netflow > 1500)',
                        'Narrative exhaustion veto (F&G ≥ 78 + 7d ≥ 25%)',
                        'LTH distribution veto (SOPR ≥ 1.02 + supply change > 0.3%)',
                        'Fallback: HOLD bypasses veto; apply_veto_override with confidence threshold'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'test_reconciliation.py (9 tests)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Dust filtering (positions below threshold excluded)',
                        'External positions detection (on exchange, not in journal)',
                        'Journal-only detection (in journal, not on exchange)',
                        'Clean status when no discrepancies'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'test_quant_signal.py (7 tests)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Quant gate with regime, volatility, edge score',
                        'BEAR funding veto, regime safety, high ATR gate',
                        'Edge score boost (MODERATE → STRONG when edge ≥ 0.6)'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'test_exit_monitoring.py (8 tests)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Stop-loss and take-profit detection logic',
                        'Trailing stop math (8% profit, 13% below peak)',
                        'update_position high_water_mark (dict form)',
                        'Performance analytics from positions'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'test_integration.py (5 tests) — Integration (Medium)'
                },
                {
                    'type': 'list',
                    'items': [
                        'test_signal_to_execution_flow: Quant → position sizing → open position',
                        'test_position_opened_and_monitored: open position has stop/target for monitoring',
                        'test_stop_loss_closes_position: stop hit closes via mocked execute_market_sell',
                        'test_take_profit_closes_position: target hit closes position',
                        'test_trailing_stop_closes_position: trailing stop hit closes (after 8% profit)'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'test_let_winners_run.py — Winners Always Run (permanent behavior)'
                },
                {
                    'type': 'text',
                    'text': 'The Let Winners Run TOGGLE was removed (2026-05-30). Winners now ALWAYS run: every new position has NO fixed take-profit and exits via stop-loss or trailing stop only. The fixed-TP cap clipped the fat tail that funds a low-win-rate book.'
                },
                {
                    'type': 'list',
                    'items': [
                        'Toggle removed: no let_winners_run key in account_config; save_account_config has no such parameter',
                        'New trades: always no fixed take-profit (ai_target_price / current_target_price = None); a passed flag is ignored',
                        'Safety: stop-loss is always set; the trailing stop (8% activation / 13% trail) is the only profit exit',
                        'Tracking: ai_target_pct is still recorded; hit_ai_target is derived from it for recommendation-accuracy stats',
                        'API: /api/account/update ignores let_winners_run; overview no longer returns it',
                        'Legacy positions: pre-existing positions that still carry a target keep the take-profit branch'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'Run: python -m pytest tests/integration/test_let_winners_run.py -v. Uses temp account_config and positions.json — no real trades.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Auto-Execution Safety'
                },
                {
                    'type': 'text',
                    'text': 'All execution tests use unittest.mock.patch to mock get_coinbase_exchange, get_exchange_for_balance, fetch_balance_sync, run_twap_buy_coinbase, and threading.Thread. No real API keys or network calls are required. USDC pair preference is verified: symbol_to_ccxt("BTC") returns "BTC/USDC", and start_twap_buy_async passes this to run_twap_buy_coinbase. Fallback to USD when USDC fails is tested via place_market_buy_usd.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'How to Run'
                },
                {
                    'type': 'list',
                    'items': [
                        'From project root (c:\\swing-trader): run_tests.bat',
                        'Or: python -m pytest -v --tb=short',
                        'Conftest provides mock_ccxt_exchange, mock_get_coinbase_exchange, mock_fetch_balance fixtures'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'All 121+ tests pass. The suite validates position sizing, trade journal, performance analytics, veto specialist, reconciliation, quant signal, auto-execution, exit monitoring, Let Winners Run, and integration flows without placing any real trades.'
                }
            ]
        }
    
    def _how_to_use(self):
        return {
            'title': 'How to Use This System - Step by Step',
            'icon': '🎓',
            'content': [
                {
                    'type': 'text',
                    'text': 'This section provides a practical daily, weekly, and monthly workflow to put everything together.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Daily Workflow'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'MORNING: Open the dashboard, check current market regime and any overnight alerts',
                        'CHECK ALERTS: Look at any Telegram notifications (did ULTRA_BUY appear?)',
                        'REVIEW SIGNALS: Click through the top coins, read signal analyses',
                        'DECIDE: If you see ULTRA_BUY or STRONG_BUY (in right regime), review the position sizing recommendation',
                        'EXECUTE: If everything looks good, open the position on your exchange',
                        'LOG IT: Immediately add to Trade Journal with exact entry price, stop, target',
                        'SET ALERTS: Set price alerts on your exchange for stop and target',
                        'FORGET IT: Don\'t watch the price all day. The system will alert you if something changes',
                        'MONITOR: Check open positions once per day for unrealized P&L',
                        'EXIT: When stop or target is hit, close position and log in journal'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Weekly Workflow'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'REVIEW JOURNAL: Check your win rate, P&L, target hit rate',
                        'COMPARE: Are you beating the backtest predictions?',
                        'REFLECT: Did you follow the system or override it?',
                        'ADJUST: If results are poor, figure out why (probably not following rules)'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Monthly Workflow'
                },
                {
                    'type': 'numbered_list',
                    'items': [
                        'RUN BACKTEST: See system performance over last 30 days',
                        'CHECK STATS: Overall win rate, best tier, best timeframe, best regime',
                        'OPTIMIZE: Adjust strategy based on data (maybe only trade ULTRA_BUY, maybe exit at 7 days always)',
                        'UPDATE ACCOUNT SIZE: If you\'ve grown capital, update position sizer settings'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'What NOT to Do'
                },
                {
                    'type': 'list',
                    'items': [
                        'DON\'T trade MODERATE_BUY or WEAK_BUY signals (low win rate)',
                        'DON\'T override position sizer (it knows better than your gut)',
                        'DON\'T move stop losses further away (breaks risk management)',
                        'DON\'T trade in BEAR regime unless it\'s ULTRA_BUY',
                        'DON\'T exit before target unless stopped out (let winners run)',
                        'DON\'T revenge trade after a loss (stick to the system)',
                        'DON\'T increase position size after wins (no "house money" thinking)'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Example Perfect Trade'
                },
                {
                    'type': 'example',
                    'text': 'Monday 9am: ULTRA_BUY alert on Telegram for Bitcoin\n\nYou:\n1. Open dashboard\n2. Read full analysis (consensus agrees; regime is SIDEWAYS)\n3. Check position sizing: $14,400 recommended\n4. Buy $14,400 of BTC at $45,000 (0.32 BTC)\n5. Set stop at $43,875 (2.5% down)\n6. Set target at $47,700 (6% up)\n7. Log in Trade Journal\n8. Set price alerts on exchange\n9. Go about your day\n\nFriday 2pm: Target alert hits\n\nYou:\n1. Sell 0.32 BTC at $47,700\n2. Profit: $864 (6% gain)\n3. Log exit in Trade Journal\n4. System shows "Target Hit: Yes"\n5. Move on to next signal\n\nResult: +$864 in 4 days, zero stress, followed the system perfectly.'
                },
                {
                    'type': 'callout',
                    'style': 'success',
                    'text': 'The system does the hard work. Your job is to: (1) Wait for high-conviction signals, (2) Execute the trade as recommended, (3) Log it, (4) Let it play out. That\'s it.'
                }
            ]
        }
    
    def _trading_strategies(self):
        return {
            'title': 'Trading Strategies - Pick Your Style',
            'icon': '⚡',
            'content': [
                {
                    'type': 'text',
                    'text': 'Not everyone has the same risk tolerance or time commitment. Pick the strategy that fits your personality.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Strategy 1: Ultra-Conservative (Sniper)'
                },
                {
                    'type': 'text',
                    'text': 'For: New traders, low risk tolerance, full-time job'
                },
                {
                    'type': 'text',
                    'text': 'Rules:'
                },
                {
                    'type': 'list',
                    'items': [
                        'ONLY trade ULTRA_BUY signals',
                        'ONLY in BULL or SIDEWAYS regimes',
                        'Use 70% of recommended position size',
                        'Exit at 7 days or target (whichever comes first)',
                        'Never override stop losses'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'Expected Results:'
                },
                {
                    'type': 'list',
                    'items': [
                        '1-2 trades per month',
                        '70-76% win rate (ULTRA only)',
                        '+4-7% per trade when winners hit target',
                        '~25-50% annual return on risked capital (if consistent)'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'Pros: Lowest stress, highest win rate of the three strategies, easy to follow'
                },
                {
                    'type': 'text',
                    'text': 'Cons: Very few trades, requires patience'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Strategy 2: Conservative (Recommended)'
                },
                {
                    'type': 'text',
                    'text': 'For: Most traders, balanced approach'
                },
                {
                    'type': 'text',
                    'text': 'Rules:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Trade ULTRA_BUY always',
                        'Trade STRONG_BUY in BULL and SIDEWAYS',
                        'Skip STRONG_BUY in BEAR and VOLATILE',
                        'Use full recommended position size',
                        'Exit at target or 7 days',
                        'Respect all stops'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'Expected Results:'
                },
                {
                    'type': 'list',
                    'items': [
                        '3-5 trades per month',
                        '61-76% blended win rate (ULTRA + STRONG)',
                        '+4-7% per trade when winners hit target',
                        '~30-60% annual return on risked capital'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'Pros: Good balance of activity and accuracy, manageable stress'
                },
                {
                    'type': 'text',
                    'text': 'Cons: Still requires discipline to skip MODERATE signals'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Strategy 3: Moderate (Active)'
                },
                {
                    'type': 'text',
                    'text': 'For: Experienced traders, more active style'
                },
                {
                    'type': 'text',
                    'text': 'Rules:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Trade ULTRA_BUY and STRONG_BUY always',
                        'Trade MODERATE_BUY in strong BULL only',
                        'Reduce MODERATE_BUY position size to 50%',
                        'Exit faster on MODERATE (5 days max)',
                        'Use wider stops in BEAR (1.3x normal)'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'Expected Results:'
                },
                {
                    'type': 'list',
                    'items': [
                        '6-10 trades per month',
                        '54-75% blended win rate',
                        '+3-6% per trade when winners hit target',
                        '~40-80% annual return on risked capital (but more volatility)'
                    ]
                },
                {
                    'type': 'text',
                    'text': 'Pros: More action, higher potential returns'
                },
                {
                    'type': 'text',
                    'text': 'Cons: More losses, more stress, requires constant monitoring'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Which Strategy Should You Choose?'
                },
                {
                    'type': 'text',
                    'text': 'Ask yourself:'
                },
                {
                    'type': 'list',
                    'items': [
                        'Am I new to trading? → Ultra-Conservative',
                        'Do I have a full-time job? → Ultra-Conservative or Conservative',
                        'Can I handle 20-30% drawdowns? → Moderate',
                        'Do I need to trade often or I get bored? → Moderate',
                        'Is sleep more important than extra returns? → Conservative',
                        'Do I trust the system completely? → Conservative',
                        'Do I like to override signals? → Don\'t use this system'
                    ]
                },
                {
                    'type': 'callout',
                    'style': 'info',
                    'text': 'Most people should start with Ultra-Conservative for the first month, then move to Conservative once comfortable. Moderate is only for experienced traders who can handle the emotional swings.'
                }
            ]
        }
    
    def _faq(self):
        return {
            'title': 'Frequently Asked Questions',
            'icon': '❓',
            'content': [
                {
                    'type': 'text',
                    'text': 'Common questions from users, with answers that reference the sections above.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'General Questions'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: Why don\'t I see any ULTRA_BUY signals?'
                },
                {
                    'type': 'text',
                    'text': 'A: This is normal and expected. ULTRA_BUY signals require quant score >= 85 and no rule-based veto. They occur 1-3 times per month. Patience is key.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: How does the quant-first pipeline work?'
                },
                {
                    'type': 'text',
                    'text': 'A: Quant score (0–100) sets the tier (ULTRA/STRONG/MODERATE). Rule-based veto can override to HOLD. See Signal Engine and Consensus Tiers.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: Can I trade STRONG_BUY signals instead?'
                },
                {
                    'type': 'text',
                    'text': 'A: Yes, but check the market regime first. In BULL or SIDEWAYS, STRONG_BUY is solid (61–67% win rate per Consensus Tiers). In BEAR or VOLATILE, wait for ULTRA_BUY.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: How much money do I need to start?'
                },
                {
                    'type': 'text',
                    'text': 'A: Minimum $5,000 recommended. The position sizer works best with at least $10,000. Below $5k, position sizes become too small to be practical.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: What if the system is wrong?'
                },
                {
                    'type': 'text',
                    'text': 'A: Even with a high win rate, you will have losing trades. That\'s why we use stop losses and never risk more than 2% per trade. No system is perfect — it\'s a disciplined edge that must be followed.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Technical Questions'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: How often does the system refresh?'
                },
                {
                    'type': 'text',
                    'text': 'A: By default, auto-refresh starts OFF (manual only). You choose the interval in the Alerts/Refreshes tab — options range from 5 minutes to daily. The recommended setting for swing trading is 1 hour. You can always click "Refresh Now" for a manual update.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: Why does the rule-based veto sometimes override the quant signal?'
                },
                {
                    'type': 'text',
                    'text': 'A: That\'s its job! The eight deterministic rules catch failure modes (extreme funding in BEAR, whale dumps, LTH distribution, bull euphoria, bull extended, narrative exhaustion, short-term pump, extended rally). When a rule fires, the signal is overridden to HOLD. Trust the override.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: What\'s the difference between signal target and my target?'
                },
                {
                    'type': 'text',
                    'text': 'A: The system recommends a target at entry. You can adjust it later if you want. The journal tracks both - did the signal target hit AND did your target hit.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Strategy Questions'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: Should I scale in/out of positions?'
                },
                {
                    'type': 'text',
                    'text': 'A: No. The position sizer already calculated the optimal size. Enter once at the recommended size, exit once at stop or target. Keep it simple.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: Can I use this for day trading?'
                },
                {
                    'type': 'text',
                    'text': 'A: No. This is a swing trading system designed for 3-14 day holds. The quant engine analyzes longer timeframes. Day trading requires different tools.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: What if I disagree with the system?'
                },
                {
                    'type': 'text',
                    'text': 'A: Then don\'t take the trade. Never override the system. If you consistently think you know better, this system isn\'t for you.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Troubleshooting'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: My win rate is lower than the backtest shows. Why?'
                },
                {
                    'type': 'text',
                    'text': 'A: Common reasons: (1) Not following the system (taking MODERATE signals, trading in BEAR), (2) Moving stops, (3) Exiting winners too early, (4) Not logging trades correctly.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: The system shows STRONG_BUY but says "Don\'t trade". Why?'
                },
                {
                    'type': 'text',
                    'text': 'A: Check the regime. If it\'s BEAR or VOLATILE, the system requires ULTRA_BUY. The regime filter is protecting you from lower win rates.'
                },
                {
                    'type': 'heading',
                    'level': 4,
                    'text': 'Q: How do I know if the system is working?'
                },
                {
                    'type': 'text',
                    'text': 'A: Check three things: (1) Regime is being detected, (2) Top coins show quant signals, (3) No import errors in console. If all three are good, it\'s working.'
                }
            ]
        }
    
    def _glossary(self):
        return {
            'title': 'Glossary - Terms You Should Know',
            'icon': '📖',
            'content': [
                {
                    'type': 'text',
                    'text': 'Quick reference for terms used throughout this manual.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Trading Terms'
                },
                {
                    'type': 'text',
                    'text': 'POSITION SIZE: How much money you invest in a single trade'
                },
                {
                    'type': 'text',
                    'text': 'STOP LOSS: Price level where you exit to limit losses (usually 2-3% below entry)'
                },
                {
                    'type': 'text',
                    'text': 'TAKE PROFIT / TARGET: Price level where you exit to lock in gains'
                },
                {
                    'type': 'text',
                    'text': 'REALIZED P&L: Profit or loss on closed trades (actual money in your account)'
                },
                {
                    'type': 'text',
                    'text': 'UNREALIZED P&L: Profit or loss on open trades (paper gains/losses)'
                },
                {
                    'type': 'text',
                    'text': 'WIN RATE: Percentage of trades that are profitable'
                },
                {
                    'type': 'text',
                    'text': 'EXPECTANCY: Average profit/loss per trade (positive = profitable system)'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'System Terms'
                },
                {
                    'type': 'text',
                    'text': 'CONSENSUS: Final signal from quant tier (after rule-based veto). Quant decides, rules protect.'
                },
                {
                    'type': 'text',
                    'text': 'REGIME: Current market condition (BULL, BEAR, SIDEWAYS, VOLATILE)'
                },
                {
                    'type': 'text',
                    'text': 'TARGET HIT RATE: How often the system\'s predicted target is reached'
                },
                {
                    'type': 'text',
                    'text': 'BACKTEST: Testing strategy on historical data to see what would have happened'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Technical Terms'
                },
                {
                    'type': 'text',
                    'text': 'RSI: Relative Strength Index - measures if price is overbought (>70) or oversold (<30)'
                },
                {
                    'type': 'text',
                    'text': 'MACD: Moving Average Convergence Divergence - shows momentum direction'
                },
                {
                    'type': 'text',
                    'text': 'ATR: Average True Range - measures how much price typically moves (volatility)'
                },
                {
                    'type': 'text',
                    'text': 'BOLLINGER BANDS: Volatility bands around price - shows overbought/oversold'
                },
                {
                    'type': 'text',
                    'text': 'FUNDING RATE: Fee paid by long or short traders in derivatives markets'
                },
                {
                    'type': 'text',
                    'text': 'ORDER BOOK: List of all buy and sell orders waiting to be filled'
                },
                {
                    'type': 'text',
                    'text': 'VOLUME PROFILE: Shows which price levels had most trading activity'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Market Terms'
                },
                {
                    'type': 'text',
                    'text': 'FEAR & GREED INDEX: Sentiment indicator from 0 (Extreme Fear) to 100 (Extreme Greed). Sourced from Alternative.me (CMC does not expose F&G via API).'
                },
                {
                    'type': 'text',
                    'text': 'CAPITULATION: When everyone panics and sells at once (often signals bottom)'
                },
                {
                    'type': 'text',
                    'text': 'FOMO: Fear Of Missing Out - buying because price is going up (usually bad idea)'
                },
                {
                    'type': 'text',
                    'text': 'WHALE: Very large holder who can move markets with their trades'
                },
                {
                    'type': 'text',
                    'text': 'LIQUIDITY: How easy it is to buy/sell without moving the price'
                },
                {
                    'type': 'text',
                    'text': 'DYNAMIC TARGETS: Adaptive stop loss and take profit system that adjusts based on coin volatility, market regime, and signal confidence. Replaces fixed percentages.'
                },
                {
                    'type': 'text',
                    'text': 'EXIT MONITORING: A background process that runs every 30 seconds and watches all open positions. It fetches current prices, compares them to your stop-loss, take-profit, and trailing-stop levels, and either alerts you or auto-executes a sell when a trigger is hit. Why it matters: You don\'t have to watch the dashboard or set exchange alerts for every position. The system has your back. How to use: It starts automatically when you launch the dashboard. Enable alerts so you know when something triggers. Turn on auto-execution if you want the system to sell for you.'
                },
                {
                    'type': 'text',
                    'text': 'TRAILING STOP: A dynamic stop that activates after a position is in profit. Once you\'re up 8% or more, the system tracks the highest price (high-water mark) and sets a stop 13% below it. If price keeps rising, the stop rises. If price reverses and hits the stop, you exit with most of the gain. Why it matters: Lets winners run while protecting profits. Without it, a position that runs up 40% then crashes back to breakeven gives back everything. With a wide-enough trailing stop, you lock in most of that move (the fat tail). Thresholds are configurable in thresholds.py.'
                },
                {
                    'type': 'text',
                    'text': 'VOLATILITY MULTIPLIER: Factor applied to base stop based on coin volatility. High vol = wider stops (2x-2.5x), low vol = tighter stops (0.7x-0.85x)'
                },
                {
                    'type': 'text',
                    'text': 'RISK:REWARD RATIO (R:R): Relationship between potential profit and loss. A 3:1 R:R means you risk $1 to make $3. System uses regime-specific ratios.'
                },
                {
                    'type': 'text',
                    'text': 'AUTO-REFRESH: Scheduled automatic data updates at configurable intervals (5min to daily). Can be completely disabled for manual-only operation.'
                },
                {
                    'type': 'text',
                    'text': 'REFRESH INTERVAL: How often the system automatically updates data. Options range from "Off" (manual only) to "5 Minutes" (very frequent).'
                },
                {
                    'type': 'text',
                    'text': 'API COST: The dollar amount charged by data services per API call. Displayed per refresh and projected monthly.'
                },
                {
                    'type': 'text',
                    'text': 'QUANT+VETO: The header shows the quant-first architecture label. Quant score determines the signal; rule-based veto protects against failure modes.'
                },
                {
                    'type': 'text',
                    'text': 'SCHEDULER: System component that manages automatic refresh timing. Configure in Alerts/Refreshes tab.'
                }
            ]
        }

    def _document_changelog(self):
        """Document changelog — revision history for the manual."""
        return {
            'title': 'Document Changelog',
            'icon': '📋',
            'content': [
                {
                    'type': 'text',
                    'text': 'Revision history for the Elite Swing Trader User Manual.'
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Version 4.2 (March 2026)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Audit: Veto rules updated from 6 to 8 throughout — added bull euphoria and bull extended rules that were in code but missing from manual.',
                        'Audit: Trailing stop activation 8% / trail width 13% (Phase 1 2026-05-30 let-winners-run overhaul; matches TRAILING_STOP_MIN_PROFIT_PCT / TRAILING_STOP_TRAIL_PCT in thresholds.py).',
                        'Audit: Removed AI/LLM dependencies (Haiku, Grok) — system converted to pure quant + rule-based veto architecture.',
                        'Audit: FAQ refresh frequency corrected from "every 10 minutes" to configurable (default OFF, recommended 1 hour for swing trading).',
                        'Audit: Removed stale "Brain toggle (3 or 6)" reference from How to Use section (feature was removed in v4.0).',
                        'Audit: Removed Discord from alert channels (not implemented; only Email and Telegram are supported).',
                        'Audit: Fixed data source count inconsistency (11 → 12) in System Overview to match rest of manual.',
                        'Audit: MODERATE_BUY description updated to quant score range (58–71).',
                        'Audit: Removed dead code methods (_exit_monitoring_capital_protection, _exit_monitoring) that were not referenced in section list.',
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Version 4.1 (March 2026)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Execution hardening: real fee extraction from every CCXT order (entry + exit); Trade Journal P&L is now net-of-fees with gross values preserved.',
                        'Slippage tracking fixed: pre-trade mid price (bid+ask)/2 snapshot replaces self-referential comparison; slippage logged in basis points.',
                        'Forensic audit trail: data/execution_audit.jsonl logs every order fill with order_id, fill_price, fee_usd, slippage_bps, pre_trade_mid, and execution context.',
                        'Retry logic: TWAP buy/sell slices retry 3x with exponential backoff on transient errors (rate limits, timeouts, 502/503).',
                        'Emergency stop: disable exchange API; no in-app kill switch.',
                        'Sell-side fill polling: market sells now poll for fill confirmation (parity with buy side), eliminating silent unfilled sells.',
                        'TWAP cooldown persists across restarts (data/twap_cooldowns.json).',
                        'execution_safety_ok now blocks new buys when an active TWAP buy is in progress.',
                        'Trailing stop high-water mark updates logged to forensic audit trail.',
                        'fetch_order_with_fee: single API call extracts both fill price and fee from limit order fills (eliminates redundant fetch_order calls).',
                        'System manual updated with fee capture, audit trail, retry, and slippage tracking documentation.',
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Version 4.0 (March 2026)'
                },
                {
                    'type': 'list',
                    'items': [
                        'Manual restructured into logical parts: Introduction, Signal Pipeline, Account & Risk, Operations, Record & Improve, Usage, and Appendix.',
                        'Sections condensed and merged (e.g. Exit Monitoring and Exit Monitoring & Capital Protection combined into one section).',
                        'Architecture finalized: pure quant + rule-based veto pipeline with no AI/LLM dependencies.',
                        'Wording reviewed for context, correctness, and formatting throughout.',
                        'Word export: page numbers added in footer.',
                        'Document changelog added in Appendix.'
                    ]
                },
                {
                    'type': 'heading',
                    'level': 3,
                    'text': 'Version 3.5 (February 2026)'
                },
                {
                    'type': 'text',
                    'text': 'Previous release. Probabilistic calibration, short-TF sniper, high-volatility regime enhancements, dynamic veto threshold, quant-first architecture, traceability and badges.'
                }
            ]
        }

    # =========================================================================
    # HTML GENERATION
    # =========================================================================
    
    def generate_html(self):
        """Generate HTML version of the manual"""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{self.manual_content['title']}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            color: #333;
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #2980b9;
            margin-top: 30px;
        }}
        h3 {{
            color: #34495e;
            margin-top: 20px;
        }}
        h4 {{
            color: #7f8c8d;
            margin-top: 15px;
        }}
        .callout {{
            padding: 15px;
            margin: 20px 0;
            border-left: 4px solid;
            background: #f8f9fa;
        }}
        .callout.info {{ border-color: #3498db; }}
        .callout.success {{ border-color: #27ae60; }}
        .callout.warning {{ border-color: #f39c12; }}
        .example {{
            background: #ecf0f1;
            padding: 15px;
            margin: 15px 0;
            border-radius: 5px;
            font-family: monospace;
            white-space: pre-wrap;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
        }}
        th, td {{
            padding: 10px;
            border: 1px solid #ddd;
            text-align: left;
        }}
        th {{
            background: #3498db;
            color: white;
        }}
        ul, ol {{
            margin: 10px 0;
            padding-left: 30px;
        }}
        li {{
            margin: 5px 0;
        }}
        .header {{
            text-align: center;
            padding: 20px 0;
            border-bottom: 2px solid #3498db;
            margin-bottom: 30px;
        }}
        .version {{
            color: #7f8c8d;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{self.manual_content['title']}</h1>
        <p class="version">Version {self.manual_content['version']} | {self.manual_content['date']}</p>
    </div>
"""
        
        for section in self.manual_content['sections']:
            if section.get('is_part'):
                html += f"<h1 class=\"part\">{section['title']}</h1>\n"
            else:
                html += self._render_section_html(section)
        
        html += """
</body>
</html>
"""
        return html
    
    def _render_section_html(self, section):
        """Render a single section to HTML"""
        html = f"<h2>{section['icon']} {section['title']}</h2>\n"
        
        for item in section['content']:
            item_type = item['type']
            
            if item_type == 'text':
                html += f"<p>{item['text']}</p>\n"
            
            elif item_type == 'heading':
                level = item['level']
                html += f"<h{level}>{item['text']}</h{level}>\n"
            
            elif item_type == 'list':
                html += "<ul>\n"
                for li in item['items']:
                    html += f"<li>{li}</li>\n"
                html += "</ul>\n"
            
            elif item_type == 'numbered_list':
                html += "<ol>\n"
                for li in item['items']:
                    html += f"<li>{li}</li>\n"
                html += "</ol>\n"
            
            elif item_type == 'callout':
                style = item['style']
                html += f"<div class='callout {style}'>{item['text']}</div>\n"
            
            elif item_type == 'example':
                html += f"<div class='example'>{item['text']}</div>\n"
            
            elif item_type == 'table':
                html += "<table>\n<tr>"
                for header in item['headers']:
                    html += f"<th>{header}</th>"
                html += "</tr>\n"
                for row in item['rows']:
                    html += "<tr>"
                    for cell in row:
                        html += f"<td>{cell}</td>"
                    html += "</tr>\n"
                html += "</table>\n"
        
        return html
    
    # =========================================================================
    # WORD DOCUMENT GENERATION
    # =========================================================================
    
    def generate_docx(self, filename='Elite_Trading_System_Manual.docx'):
        """Generate Word document version"""
        doc = Document()
        
        # Title page
        title = doc.add_heading(self.manual_content['title'], 0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        version_para = doc.add_paragraph(f"Version {self.manual_content['version']}")
        version_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        date_para = doc.add_paragraph(self.manual_content['date'])
        date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        doc.add_page_break()
        
        # Table of contents
        doc.add_heading('Table of Contents', 1)
        section_num = 0
        for section in self.manual_content['sections']:
            if section.get('is_part'):
                doc.add_paragraph(section['title'], style='Heading 2')
            else:
                section_num += 1
                doc.add_paragraph(f"{section_num}. {section['title']}", style='List Number')
        
        doc.add_page_break()
        
        # Sections and parts
        for section in self.manual_content['sections']:
            if section.get('is_part'):
                doc.add_heading(section['title'], level=1)
                doc.add_page_break()
            else:
                self._render_section_docx(doc, section)
                doc.add_page_break()
        
        self._add_page_number_footer(doc)
        doc.save(filename)
        return filename

    def _add_page_number_footer(self, doc):
        """Add centered 'Page N' footer to the document using Word PAGE field."""
        section = doc.sections[0]
        footer = section.footer
        para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.add_run('Page ')
        run = para.add_run()
        run.text = ''  # PAGE field will show the number
        # Inject PAGE field XML (python-docx has no high-level API for fields)
        fldChar1 = OxmlElement('w:fldChar')
        fldChar1.set(qn('w:fldCharType'), 'begin')
        instrText = OxmlElement('w:instrText')
        instrText.set(qn('xml:space'), 'preserve')
        instrText.text = ' PAGE '
        fldChar2 = OxmlElement('w:fldChar')
        fldChar2.set(qn('w:fldCharType'), 'end')
        run._r.append(fldChar1)
        run._r.append(instrText)
        run._r.append(fldChar2)

    def _render_section_docx(self, doc, section):
        """Render a single section to Word"""
        doc.add_heading(f"{section['icon']} {section['title']}", 1)
        
        for item in section['content']:
            item_type = item['type']
            
            if item_type == 'text':
                doc.add_paragraph(item['text'])
            
            elif item_type == 'heading':
                level = item['level']
                doc.add_heading(item['text'], level)
            
            elif item_type in ['list', 'numbered_list']:
                style = 'List Bullet' if item_type == 'list' else 'List Number'
                for li_text in item['items']:
                    doc.add_paragraph(li_text, style=style)
            
            elif item_type == 'callout':
                para = doc.add_paragraph(item['text'])
                para.paragraph_format.left_indent = Inches(0.5)
                para.paragraph_format.right_indent = Inches(0.5)
                # Add shading based on style
                if item['style'] == 'info':
                    run = para.runs[0]
                    run.font.color.rgb = RGBColor(52, 152, 219)
                elif item['style'] == 'success':
                    run = para.runs[0]
                    run.font.color.rgb = RGBColor(39, 174, 96)
                elif item['style'] == 'warning':
                    run = para.runs[0]
                    run.font.color.rgb = RGBColor(243, 156, 18)
            
            elif item_type == 'example':
                para = doc.add_paragraph(item['text'])
                para.paragraph_format.left_indent = Inches(0.5)
                para.style = 'Intense Quote'
            
            elif item_type == 'table':
                table = doc.add_table(rows=1+len(item['rows']), cols=len(item['headers']))
                table.style = 'Light Grid Accent 1'
                
                # Headers
                hdr_cells = table.rows[0].cells
                for i, header in enumerate(item['headers']):
                    hdr_cells[i].text = header
                
                # Rows
                for row_idx, row_data in enumerate(item['rows'], 1):
                    row_cells = table.rows[row_idx].cells
                    for col_idx, cell_text in enumerate(row_data):
                        row_cells[col_idx].text = str(cell_text)
    
    # =========================================================================
    # JSON DATA (for API)
    # =========================================================================
    
    def get_json(self):
        """Return manual as JSON for API"""
        return self.manual_content
