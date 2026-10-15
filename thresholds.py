# thresholds.py — FIX: Single source of truth for live thresholds (eliminate duplication drift)
# All consumers (quant_signal, veto_specialist, model_attribution) read from here.
# Daily retrain writes back to live_thresholds.json.

import os
import json
import logging
import time
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)
_thresholds_lock = threading.Lock()

LIVE_THRESHOLDS_PATH = os.path.join(os.path.dirname(__file__), 'live_thresholds.json')

# Defaults (single source when JSON missing or key missing)
DEFAULT_THRESHOLDS: Dict[str, Any] = {
    # Quant / shared
    'BLOOD_STREET_FG': 30,
    'DIP_MIN_SCORE': 65,
    'FUNDING_VETO_BEAR_PCT': 0.08,
    'OB_IMBALANCE_MAX_ADJ': 4,
    'HIGH_ATR_BEAR_MIN_SCORE': 80,
    'HIGH_ATR_PCT': 4.0,
    'EDGE_BOOST_THRESHOLD': 0.5,
    'MTF_ALIGNMENT_BONUS': 3,
    'MTF_MEDIUM_BONUS': 2,
    'SUPPORT_NEAR_BONUS': 2,
    'DIP_24H_PCT': -6.0,
    'DIP_1H_STABILIZING': -3.0,
    'DIP_7D_MAX': -15.0,
    'PUMP_24H_PCT': 14.0,
    'PUMP_7D_PCT': 20.0,
    'PUMP_30D_PCT': 35.0,
    'PULLBACK_24H_MAX': -2.0,
    'PULLBACK_24H_MIN': -8.0,
    'PULLBACK_MIN_SCORE': 66,
    'VOL_HARVEST_ATR_PCT': 3.5,
    'VOL_HARVEST_MIN_SCORE': 68,
    'VOL_HARVEST_7D_MAX': -12.0,
    'BLOOD_FG_MAX': 45,
    'PULLBACK_FG_MAX': 50,
    'VOL_HARVEST_FG_MAX': 48,
    'BLOOD_STREET_24H_MAX': -5.0,
    'BLOOD_STREET_MIN_SCORE': 65,
    'BLOOD_STREET_1H_MIN': -4.0,
    'VOL_HARVEST_1H_MIN': -4.0,
    'CAPITULATION_FG_MAX': 15,
    'CAPITULATION_MIN_SCORE': 68,
    'VOL_CONTRACTION_FUNDING_MAX': 0.05,
    'BEAR_REGIME_MIN_SCORE': 72,  # BEAR/VOLATILE floor when not in rally; rally (market_24h > 5%) uses 72
    # Momentum detection (two-tier: early + confirmed)
    'EARLY_MOMENTUM_MIN_SCORE': 48,
    'EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE': 58,  # +10 in SIDEWAYS: 0/5 wins, -2.85%/trade — no trend to ride
    'EARLY_MOMENTUM_MIN_EARLY_MOM': 3.0,
    'EARLY_MOMENTUM_MAX_RSI': 85,      # sparkline RSI runs hot for trending coins
    'EARLY_MOMENTUM_MAX_24H': 15.0,
    'EARLY_MOMENTUM_MAX_7D': 30.0,
    'EARLY_MOMENTUM_MIN_QUALITY': 8,    # low bar — early detection accepts more noise; confirmed tier has quality > 40
    'MOMENTUM_BREAKOUT_MIN_SCORE': 52,
    'MOMENTUM_BREAKOUT_MIN_MOM': 8,
    'MOMENTUM_BREAKOUT_MIN_QUALITY': 40,
    'MOMENTUM_BREAKOUT_STRONG_QUALITY': 60,
    'MOMENTUM_BREAKOUT_MAX_24H': 15.0,
    'MOMENTUM_BREAKOUT_MAX_7D': 25.0,
    'MOMENTUM_BREAKOUT_MAX_RSI': 85,   # sparkline RSI runs hot for trending coins
    # Veto specialist
    'FUNDING_VETO_BEAR_RELAXED_PCT': 0.12,
    'EXCHANGE_NETFLOW_VETO': 1500,
    'LTH_DISTRIBUTION_SOPR_MIN': 1.02,
    'LTH_DISTRIBUTION_SUPPLY_PCT': 0.3,
    'NARRATIVE_EXHAUSTION_FG': 83,
    'NARRATIVE_EXHAUSTION_7D_PCT': 25,
    'SHORT_TERM_PUMP_FG': 78,
    'SHORT_TERM_PUMP_24H_PCT': 15,
    'EXTENDED_RALLY_30D_PCT': 50,
    'EXTENDED_RALLY_FG': 80,
    # BULL-specific veto (audit: BULL had 0% win rate with zero protection)
    'BULL_EUPHORIA_FG': 90,         # F&G threshold for BULL euphoria veto
    'BULL_EUPHORIA_7D_PCT': 30,     # 7d price change for BULL euphoria veto
    'BULL_EXTENDED_FG': 85,         # F&G threshold for BULL extended move veto
    'BULL_EXTENDED_30D_PCT': 60,    # 30d price change for BULL parabolic veto
    'VETO_TEMPERATURE': 0.1,
    'VETO_MAX_TOKENS': 1024,
    'VETO_CONFIDENCE_THRESHOLD': 70,
    # Tactical regime overlay — short-term divergence detection
    'TACTICAL_SEVERE_24H_PCT': -4.0,          # BTC 24h drop for severe downgrade
    'TACTICAL_SEVERE_ALT_BREADTH_PCT': 25.0,  # Alt 24h breadth % below which = severe
    'TACTICAL_MODERATE_24H_PCT': -2.0,        # BTC 24h drop for moderate downgrade
    'TACTICAL_MODERATE_7D_PCT': -5.0,         # BTC 7d drop for moderate (BULL path)
    'TACTICAL_MODERATE_RSI': 35.0,            # RSI below which = moderate (SIDEWAYS path)
    'TACTICAL_SEVERE_1H_PCT': -3.0,           # BTC 1h crash fast-trigger
    'TACTICAL_UPGRADE_24H_PCT': 5.0,          # BTC 24h gain for rally upgrade
    'TACTICAL_UPGRADE_ALT_BREADTH_PCT': 75.0, # Alt 24h breadth % above which = rally
    'TACTICAL_UPGRADE_RSI': 55.0,             # RSI above which = rally confirmation
    # SIDEWAYS regime protection (audit: 11.1% win rate in SIDEWAYS)
    'SIDEWAYS_REGIME_MIN_SCORE': 68,  # Score floor for SIDEWAYS regime
    'BULL_REGIME_MIN_SCORE': 72,      # Score floor for BULL regime (locked ≥72: 0% win rate below this in all observed data)
    'BULL_POSITION_SIZE_MULT': 1.0,   # Normalized from 1.2 until BULL WR improves (Cycle 17)
    'BULL_STOP_FLOOR_PCT': 5.0,       # Widened from 3.0% to match BEAR/VOLATILE; momentum entries need room for retracements
    'BULL_CHASE_24H_MAX_PCT': 5.0,    # Max coin 24h change before BULL momentum tier is capped (don't chase extended moves)
    # Dynamic stop policy (minimal global parameter surface; avoid per-coin overfit)
    'DYNAMIC_STOP_HIGH_BETA_FLOOR_PCT': 4.5,
    'DYNAMIC_STOP_HIGH_BETA_VOL_THRESHOLD_PCT': 85.0,
    'DYNAMIC_STOP_HIGH_BETA_ATR_THRESHOLD_PCT': 2.5,
    'DYNAMIC_STOP_HIGH_BETA_MOM_7D_THRESHOLD_PCT': 20.0,
    'DYNAMIC_STOP_REPRICE_DRIFT_PCT': 0.05,
    # Legacy aliases kept for backward compatibility with older call sites/configs
    'HIGH_BETA_VOL_THRESHOLD_PCT': 85.0,
    'HIGH_BETA_STOP_FLOOR_PCT': 4.5,
    'HIGH_BETA_ENTRY_STOP_FLOOR_PCT': 4.5,
    'HIGH_BETA_ENTRY_ATR_THRESHOLD_PCT': 2.5,
    'HIGH_BETA_ENTRY_7D_THRESHOLD_PCT': 20.0,
    # Volume gate (Cycle 17: no volume filtering in pipeline)
    'VOLUME_GATE_RATIO': 0.5,        # Coins below median*ratio get penalized (0.5 = below 50% of median)
    'VOLUME_GATE_PENALTY': 5,        # Score penalty points for low-volume coins
    # ATH proximity (coins near all-time high = elevated reversion risk)
    'ATH_PROXIMITY_VETO_PCT': -5.0,  # ath_change_percentage >= this → near ATH (CoinGecko: -5.0 = within 5% of ATH)
    'ATH_PROXIMITY_PENALTY': 3,      # Score penalty for coins near ATH
    # FDV dilution risk (fully diluted valuation >> market cap = future sell pressure)
    'FDV_DILUTION_RATIO_MAX': 3.0,   # FDV/market_cap above this → dilution risk flag
    'FDV_DILUTION_PENALTY': 4,       # Score penalty for high-dilution tokens
    # Path kill switch (auto-disable underperforming paths)
    'PATH_KILL_MIN_TRADES': 10,       # Min trades to evaluate a path
    'PATH_KILL_WIN_RATE': 0.40,       # Win rate below this → path disabled
    'PATH_REVIVE_WIN_RATE': 0.55,     # 7d win rate above this → re-enable
    # Portfolio-level risk gates
    'MAX_OPEN_PER_REGIME': 4,         # Max simultaneous open positions per regime
    'MAX_OPEN_TOTAL': 8,              # Max total open positions
    # Trailing stop (used by app.py and exit_manager.py)
    # Phase 1 (2026-05-30) "let winners run" overhaul: replay-validated on the 168-entry backtest.
    # A ~13% trail + ~8% activation flips the same entries from -1.7% to +0.7..+1.2% expectancy
    # (positive plateau trail 12-17%, OOS-positive in both time halves). Old 3%/3% clipped the
    # fat tail: winners realized +7% while the universe produced +50-80% moves.
    'TRAILING_STOP_MIN_PROFIT_PCT': 8,       # Min profit % before trailing activates (was 3: strangled winners)
    'TRAILING_STOP_TRAIL_PCT': 13,           # Trail % below high-water mark (was 3: noise-tight; 13 = center of validated 12-17% plateau)
    # Breakeven stop — DISABLED by setting trigger == activation so the empty band never fires.
    # The validated replay policy used only the initial regime-floor stop until trailing activates
    # at +8%; premature tightening to breakeven re-introduced the strangling we just removed.
    'BREAKEVEN_STOP_TRIGGER_PCT': 8,         # was 1.5; == TRAILING_STOP_MIN_PROFIT_PCT => breakeven band empty (disabled)
    'BREAKEVEN_STOP_BUFFER_PCT': 0.3,        # Small buffer below entry (0.3%) to avoid noise (retained for safety paths)
    # Stale position exit (sell at market after N days to free capital)
    'STALE_POSITION_MAX_DAYS': 21,           # was 14: extended to match the 21-day hold the replay edge requires
    # Top-N gate: minimum score to enter quant screening (per regime)
    'GATE_SCORE_BULL': 52,
    'GATE_SCORE_SIDEWAYS': 52,
    'GATE_SCORE_BEAR': 46,
    'GATE_SCORE_VOLATILE': 52,
    # Data quality gate for top tiers (funding_ok, order_book_ok, onchain_ok)
    'REQUIRED_SOURCES_FOR_STRONG_BUY': 2,
    'REQUIRED_SOURCES_FOR_MODERATE_BUY': 1,  # MODERATE_BUY needs at least 1 source (0 = no data)
    'DATA_QUALITY_MOMENTUM_BYPASS_PCT': 10.0,  # 24h gain % for relaxed STRONG_BUY source requirement
    # Historical win prob scope: compute path-specific win prob for top-N coins
    'WIN_PROB_TOP_N': 10,
    # Order book sniper ratio: minimum bid/ask ratio for sniper entry badge
    'OB_RATIO_SNIPER_MIN': 1.08,
    # Score inflation guard (cap total additive boosts from overlays)
    'MAX_CUMULATIVE_ADJ': 15,
    # Confirmation score weights
    'CONF_SCORE_BASE_WEIGHT': 0.70,
    'CONF_SNIPER_BONUS': 10,
    'CONF_NARRATIVE_BONUS': 5,
    'CONF_WHALE_BONUS': 5,
    'CONF_MAX_BONUS': 15,
    'CONF_PROMOTE_MIN_SCORE': 70,
    # Fractional Kelly multiplier for edge calculation (0.5 = half-Kelly)
    'KELLY_FRACTION': 0.5,
    # Missed-opportunity analysis (recursive optimizer)
    'MISSED_EDGE_WEIGHT': 0.25,
    'PAPER_TRADE_WEIGHT': 0.75,
    'PAPER_PROMO_MIN_CLOSED': 20,
    'PAPER_PROMO_MIN_WR': 45.0,
    'PAPER_PROMO_WILSON_LB': 40.0,
    'TOP_MISSED_OPPS_TO_REPORT': 10,
    # --- Stage 1b paper→live promotion gate (stage1b_promotion.py) ---
    # GO requires ALL criteria. Conservative; tune only with evidence.
    'STAGE1B_PROMO_MIN_CLOSED': 30,        # min closed paper trades (sample size)
    'STAGE1B_PROMO_MIN_DAYS': 21,          # min calendar span of paper evidence
    'STAGE1B_PROMO_MIN_EXPECTANCY': 0.30,  # min net (after-fee) expectancy %/trade
    'STAGE1B_PROMO_MIN_PF': 1.30,          # min profit factor
    'STAGE1B_PROMO_WILSON_LB': 45.0,       # min Wilson lower-bound win-rate %
    'STAGE1B_PROMO_COST_STRESS_MULT': 0.5, # extra round-trip cost (x) for cost-robustness stress
    'STAGE1B_PROMO_BOOTSTRAP_FLOOR': 0.0,  # 10th-pct bootstrap expectancy must exceed this %
    # Post-promotion kill-switch: auto-revert to paper if live degrades.
    'STAGE1B_KILL_LIVE_MIN_CLOSED': 10,    # min live closes before kill-switch can fire
    'STAGE1B_KILL_DD_PCT': 8.0,            # live drawdown %% from promotion that triggers revert
    'STAGE1B_KILL_EXPECTANCY': -0.50,      # live expectancy %% below which we revert
    # Optimizer execution and maturity
    'MATURITY_TRADE_TARGET': 100,  # 100 trades before confirming a threshold change
    'MIN_TRADES_FOR_REC': 15,      # Min closed trades to recommend a threshold change (regime-grouped)
    'MIN_TRADES_FOR_AUTO': 10,     # Min evidence trades for high-confidence auto-apply eligibility
    'SLIPPAGE_PCT': 0.08,          # typical crypto taker fee + slippage
    'FEE_PCT': 0.075,              # exchange fee
    'MIN_WALK_FORWARD_TRADES': 15,
    # Regression scorer (weekly logistic regression on attribution trades)
    'REGRESSION_SCORE_WEIGHT': 0.3,   # Weight on regression prob when blending (conservative: model first deployed 2026-05-30 on 71 samples)
    'REGRESSION_MAX_DRAG_PCT': 0.15,  # Max regression-induced score reduction (floor=score*0.85; keeps scores in tier for entries >=72)
    'MIN_TRADES_FOR_REGRESSION': 50,  # Below this, use additive score only (lowered 2026-05-30: 71 live trades available)
    # Position sizer Kelly cap
    'KELLY_CAP_PCT': 5.0,          # Max % of account per position from Kelly
    # --- Stage 1b: momentum/trend structural gate (evidence: blueprint Sec 8-9) ---
    # risk_on = BTC > 200d-MA (with band) AND BTC 30d-change > 0. Master entry gate.
    'RISK_OFF_MA_BAND_PCT': 1.5,   # Whipsaw band around BTC 200d-MA (R2b: 7.8->5 flips/yr). Structural, not optimized.
    'MOM30_MIN_CH30D': 15.0,       # Min coin 30d momentum (%) for risk-on momentum entries (R2a plateau +10..+40).
    'RISK_OFF_SIZE_MULT': 0.5,     # Size haircut for (momentum-only) entries when NOT risk-on. Sizing param, not optimized.
    'MIN_24H_VOLUME_USD': 10000000.0,  # Liquidity floor for the top-100 scan expansion tier. Operational, not optimized.
    # --- Stage 1c: LEAN ENTRY MODE (prune) ---
    # 0=off (legacy), 1=on. When on, only momentum/trend-family BUYs fire AND only while
    # BTC is risk-on; mean-reversion / contrarian / catch-all paths are suppressed. Evidence:
    # multi-year daily walk-forward (stage1c_replay) — those paths are net-negative and the
    # full pipeline only turns positive (both WF halves) once pruned to rally/pullback/momentum
    # in risk-on. Structural switch, NOT optimizer-tunable. Enable in live_thresholds.json for paper.
    'LEAN_ENTRY_MODE': 0.0,
    # --- Stage 1c: LEAN EXIT mechanics (active only while LEAN_ENTRY_MODE on) ---
    # Evidence (stage1c_replay exit research): the pipeline's tight ATR stops (~4-6%)
    # over-stopped trend trades (only 6.7% survived to a profitable exit). A WIDE FIXED
    # stop with no take-profit and NO trailing matches the no-stop edge (test ~+9.5%/trade,
    # PF ~2.1-2.6 OOS, cost-robust) while structurally capping every loss. Trailing is
    # disabled under lean (exit_manager) because any trail tight enough to protect clips
    # the right-tail winners. Structural switches, NOT optimizer-tunable.
    'LEAN_FIXED_STOP_PCT': 24.0,     # Wide fixed disaster stop (caps each loss ~-24%).
    'LEAN_TAKE_PROFIT_PCT': 150.0,   # Effectively "no TP" — let winners run to stop/time-exit.
    'LEAN_MAX_HOLD_DAYS': 30.0,      # Time exit (research sweet spot; 21 leaves edge, 45 adds tail).
    'LEAN_ENTRY_MIN_MOM30': 15.0,    # 30d-momentum entry floor for lean rally/pullback entries.
}

OPTIMIZER_BOUNDS: Dict[str, tuple] = {
    'DIP_MIN_SCORE':              (58, 72),
    'BLOOD_STREET_FG':            (20, 40),
    'BLOOD_STREET_MIN_SCORE':     (55, 70),
    'BEAR_REGIME_MIN_SCORE':      (65, 80),
    'PULLBACK_MIN_SCORE':         (60, 74),
    'VOL_HARVEST_MIN_SCORE':      (62, 76),
    'CAPITULATION_MIN_SCORE':     (62, 76),
    'BLOOD_FG_MAX':               (35, 55),
    'PULLBACK_FG_MAX':            (40, 60),
    'VOL_HARVEST_FG_MAX':         (38, 58),
    'FUNDING_VETO_BEAR_PCT':      (0.04, 0.14),
    'VETO_CONFIDENCE_THRESHOLD':  (60, 85),
    'EDGE_BOOST_THRESHOLD':       (0.3, 0.7),
    'PUMP_24H_PCT':               (13.0, 20.0),
    'SHORT_TERM_PUMP_24H_PCT':    (10.0, 20.0),
    # Momentum (early + breakout)
    'EARLY_MOMENTUM_MIN_SCORE':   (44, 56),
    'EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE': (52, 66),
    'EARLY_MOMENTUM_MIN_QUALITY': (5, 25),
    'MOMENTUM_BREAKOUT_MIN_SCORE': (48, 60),
    'MOMENTUM_BREAKOUT_MIN_QUALITY': (32, 52),
    'MOM30_MIN_CH30D':            (10.0, 40.0),  # Stage 1b: risk-on 30d-momentum entry floor (R2a plateau)
    # BULL veto
    'BULL_EUPHORIA_FG':           (85, 95),
    'BULL_EUPHORIA_7D_PCT':       (20.0, 40.0),
    'BULL_EXTENDED_FG':           (80, 92),
    'BULL_EXTENDED_30D_PCT':      (45.0, 75.0),
    # SIDEWAYS regime
    'SIDEWAYS_REGIME_MIN_SCORE':  (60, 78),
    'BULL_REGIME_MIN_SCORE':      (72, 80),  # Floor locked at 72 — score below this has never been profitable in BULL
    'BULL_CHASE_24H_MAX_PCT':     (3.0, 8.0),
    # BULL_POSITION_SIZE_MULT and BULL_STOP_FLOOR_PCT excluded from optimizer:
    # Sizing/stop params need a sizing-aware counterfactual, not entry blocking logic.
    # TODO: Add sizing counterfactual when n>100 trades.
    'VOLUME_GATE_RATIO':          (0.2, 1.0),
    'VOLUME_GATE_PENALTY':        (2, 10),
    'ATH_PROXIMITY_VETO_PCT':     (-15.0, -2.0),
    'ATH_PROXIMITY_PENALTY':      (1, 8),
    'FDV_DILUTION_RATIO_MAX':     (1.5, 5.0),
    'FDV_DILUTION_PENALTY':       (1, 8),
    'OB_RATIO_SNIPER_MIN':        (1.02, 1.15),
    # Veto (FG and price-change gates)
    'NARRATIVE_EXHAUSTION_FG':    (78, 88),
    'NARRATIVE_EXHAUSTION_7D_PCT': (20.0, 32.0),
    'SHORT_TERM_PUMP_FG':         (72, 85),
    'EXTENDED_RALLY_FG':          (75, 88),
    'EXTENDED_RALLY_30D_PCT':     (42.0, 58.0),
    # Score inflation guard + confirmation score + Kelly
    'MAX_CUMULATIVE_ADJ':         (10, 25),
    'CONF_SCORE_BASE_WEIGHT':     (0.55, 0.85),
    'CONF_SNIPER_BONUS':          (5, 20),
    # CONF_NARRATIVE_BONUS removed from optimizer — narrative_spike badge removed (phantom field).
    'CONF_WHALE_BONUS':           (2, 10),
    'CONF_MAX_BONUS':             (10, 25),
    'CONF_PROMOTE_MIN_SCORE':     (65, 80),
    'KELLY_FRACTION':             (0.25, 0.75),
    # Tactical regime overlay
    'TACTICAL_SEVERE_24H_PCT':          (-6.0, -2.0),
    'TACTICAL_SEVERE_ALT_BREADTH_PCT':  (15.0, 35.0),
    'TACTICAL_MODERATE_24H_PCT':        (-4.0, -1.0),
    'TACTICAL_MODERATE_7D_PCT':         (-8.0, -3.0),
    'TACTICAL_MODERATE_RSI':            (30.0, 42.0),
    'TACTICAL_SEVERE_1H_PCT':          (-5.0, -1.5),
    'TACTICAL_UPGRADE_24H_PCT':         (5.0, 8.0),
    'TACTICAL_UPGRADE_ALT_BREADTH_PCT': (65.0, 85.0),
    'TACTICAL_UPGRADE_RSI':             (50.0, 65.0),
}

PARAM_DESCRIPTIONS: Dict[str, str] = {
    'DIP_MIN_SCORE':              'Minimum quant score for dip-buying path',
    'BLOOD_STREET_FG':            'Max Fear & Greed for blood-in-the-street path',
    'BLOOD_STREET_MIN_SCORE':     'Minimum quant score for blood-in-the-street path',
    'BEAR_REGIME_MIN_SCORE':      'Score floor for BEAR/VOLATILE regimes',
    'PULLBACK_MIN_SCORE':         'Minimum quant score for pullback-in-trend path',
    'VOL_HARVEST_MIN_SCORE':      'Minimum quant score for volatility harvest path',
    'CAPITULATION_MIN_SCORE':     'Minimum quant score for capitulation path',
    'BLOOD_FG_MAX':               'Max Fear & Greed for blood-in-the-street entry',
    'PULLBACK_FG_MAX':            'Max Fear & Greed for pullback path entry',
    'VOL_HARVEST_FG_MAX':         'Max Fear & Greed for volatility harvest entry',
    'FUNDING_VETO_BEAR_PCT':      'Funding rate veto threshold in BEAR regime',
    'VETO_CONFIDENCE_THRESHOLD':  'Base confidence threshold for veto specialist',
    'EDGE_BOOST_THRESHOLD':       'Edge score needed for tier upgrade',
    'PUMP_24H_PCT':               '24h pump cap — above this, tier is capped',
    'SHORT_TERM_PUMP_24H_PCT':    '24h % for short-term pump veto trigger',
    'EARLY_MOMENTUM_MIN_SCORE':   'Minimum score for early momentum path',
    'EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE': 'Higher score floor for early momentum in SIDEWAYS regime',
    'EARLY_MOMENTUM_MIN_QUALITY': 'Minimum momentum quality for early momentum',
    'MOMENTUM_BREAKOUT_MIN_SCORE': 'Minimum score for momentum breakout path',
    'MOMENTUM_BREAKOUT_MIN_QUALITY': 'Minimum momentum quality for confirmed breakout',
    'BULL_EUPHORIA_FG':           'Fear & Greed threshold for BULL euphoria veto',
    'BULL_EUPHORIA_7D_PCT':       '7d price change threshold for BULL euphoria veto',
    'BULL_EXTENDED_FG':           'Fear & Greed threshold for BULL extended move veto',
    'BULL_EXTENDED_30D_PCT':      '30d price change threshold for BULL parabolic veto',
    'SIDEWAYS_REGIME_MIN_SCORE':  'Score floor for SIDEWAYS regime',
    'BULL_REGIME_MIN_SCORE':      'Score floor for BULL regime',
    'BULL_POSITION_SIZE_MULT':    'Position size multiplier for BULL regime',
    'BULL_STOP_FLOOR_PCT':        'Minimum stop-loss floor for BULL regime',
    'BULL_CHASE_24H_MAX_PCT':     'Max 24h price change in BULL before momentum tier is capped to MODERATE_BUY',
    'DATA_QUALITY_MOMENTUM_BYPASS_PCT': '24h gain % threshold for relaxed data-quality source requirement on STRONG_BUY',
    'DYNAMIC_STOP_HIGH_BETA_FLOOR_PCT': 'Global stop-loss floor for dynamic-stop eligible high-beta names',
    'DYNAMIC_STOP_HIGH_BETA_VOL_THRESHOLD_PCT': 'Volatility threshold (%) for dynamic-target high-beta eligibility',
    'DYNAMIC_STOP_HIGH_BETA_ATR_THRESHOLD_PCT': 'ATR threshold for dynamic-stop eligibility',
    'DYNAMIC_STOP_HIGH_BETA_MOM_7D_THRESHOLD_PCT': '7d momentum threshold for dynamic-stop eligibility',
    'DYNAMIC_STOP_REPRICE_DRIFT_PCT': 'Minimum stop drift (%) before live SL reprice',
    'HIGH_BETA_VOL_THRESHOLD_PCT': 'Legacy volatility threshold alias for older dynamic-stop code paths',
    'HIGH_BETA_STOP_FLOOR_PCT':    'Legacy stop floor alias for older dynamic-stop code paths',
    'HIGH_BETA_ENTRY_STOP_FLOOR_PCT': 'Legacy entry stop floor alias',
    'HIGH_BETA_ENTRY_ATR_THRESHOLD_PCT': 'Legacy entry ATR threshold alias',
    'HIGH_BETA_ENTRY_7D_THRESHOLD_PCT': 'Legacy entry 7d threshold alias',
    'VOLUME_GATE_RATIO':          'Volume-to-median ratio below which penalty applies',
    'VOLUME_GATE_PENALTY':        'Score penalty for low-volume coins',
    'ATH_PROXIMITY_VETO_PCT':     'ATH distance threshold — coins above this (closer to ATH) get penalized',
    'ATH_PROXIMITY_PENALTY':      'Score penalty for coins near all-time high',
    'FDV_DILUTION_RATIO_MAX':     'Max FDV/market_cap ratio before dilution penalty applies',
    'FDV_DILUTION_PENALTY':       'Score penalty for tokens with high FDV dilution risk',
    'OB_RATIO_SNIPER_MIN':        'Minimum bid/ask ratio for order book sniper entry badge',
    'NARRATIVE_EXHAUSTION_FG':    'Fear & Greed threshold for narrative exhaustion veto',
    'NARRATIVE_EXHAUSTION_7D_PCT': '7d price change threshold for narrative exhaustion veto',
    'SHORT_TERM_PUMP_FG':         'Fear & Greed threshold for short-term pump veto',
    'EXTENDED_RALLY_FG':          'Fear & Greed threshold for extended rally veto',
    'EXTENDED_RALLY_30D_PCT':    '30d price change threshold for extended rally veto',
    'MAX_CUMULATIVE_ADJ':        'Max total additive score boost from all overlays',
    'CONF_SCORE_BASE_WEIGHT':    'Weight on raw quant score in confirmation_score',
    'CONF_SNIPER_BONUS':         'Confirmation bonus when light sniper entry active',
    'CONF_NARRATIVE_BONUS':      'Confirmation bonus for narrative spike',
    'CONF_WHALE_BONUS':          'Confirmation bonus for whale accumulation',
    'CONF_MAX_BONUS':            'Cap on total confirmation bonuses',
    'CONF_PROMOTE_MIN_SCORE':    'Min raw score for confirmation-based tier promotion',
    'KELLY_FRACTION':            'Fractional Kelly multiplier for edge calculation',
    # Tactical regime overlay
    'TACTICAL_SEVERE_24H_PCT':          'BTC 24h % drop threshold for severe tactical downgrade',
    'TACTICAL_SEVERE_ALT_BREADTH_PCT':  'Alt 24h breadth % below which severe divergence triggers',
    'TACTICAL_MODERATE_24H_PCT':        'BTC 24h % drop threshold for moderate tactical downgrade',
    'TACTICAL_MODERATE_7D_PCT':         'BTC 7d % drop threshold for moderate downgrade (BULL path)',
    'TACTICAL_MODERATE_RSI':            'RSI below which moderate tactical downgrade triggers (SIDEWAYS path)',
    'TACTICAL_SEVERE_1H_PCT':          'BTC 1h % crash threshold for acute sell-off fast-trigger',
    'TACTICAL_UPGRADE_24H_PCT':         'BTC 24h % gain threshold for rally upgrade',
    'TACTICAL_UPGRADE_ALT_BREADTH_PCT': 'Alt 24h breadth % above which rally upgrade triggers',
    'TACTICAL_UPGRADE_RSI':             'RSI above which rally upgrade triggers',
}

# One-line "how it improves signals" for optimizer email report (reused by recursive_optimizer format_report_html)
PARAM_IMPACT: Dict[str, str] = {
    'DIP_MIN_SCORE':              'Raises the bar for dip entries so marginal setups are filtered; expected to reduce losses in BEAR.',
    'BLOOD_STREET_FG':            'Tightens/loosens blood-in-the-street entry by Fear & Greed; improves timing in extreme fear.',
    'BLOOD_STREET_MIN_SCORE':     'Filters weak blood-in-the-street setups; fewer low-conviction entries.',
    'BEAR_REGIME_MIN_SCORE':      'Score floor in BEAR/VOLATILE; higher = fewer marginal trades when regime is hostile.',
    'PULLBACK_MIN_SCORE':         'Minimum score for pullback-in-trend; reduces bad pullback entries.',
    'VOL_HARVEST_MIN_SCORE':      'Volatility harvest path gate; improves selectivity in choppy regimes.',
    'CAPITULATION_MIN_SCORE':     'Capitulation path gate; filters weak panic entries.',
    'BLOOD_FG_MAX':               'Max F&G for blood-in-the-street; avoids buying when fear is not extreme enough.',
    'PULLBACK_FG_MAX':            'Max F&G for pullback path; keeps entries aligned with regime.',
    'VOL_HARVEST_FG_MAX':         'Max F&G for volatility harvest; improves path selectivity.',
    'FUNDING_VETO_BEAR_PCT':      'Veto threshold in BEAR; blocks high-funding longs to cut funding bleed.',
    'VETO_CONFIDENCE_THRESHOLD':  'Veto specialist sensitivity; higher = fewer vetoes, lower = more protection.',
    'EDGE_BOOST_THRESHOLD':       'Edge score for tier upgrade; improves R:R alignment of promoted signals.',
    'PUMP_24H_PCT':               '24h pump cap; above this, tier is capped to avoid chasing pumps.',
    'SHORT_TERM_PUMP_24H_PCT':    'Short-term pump veto; filters overheated momentum.',
    'EARLY_MOMENTUM_MIN_SCORE':   'Early momentum path gate; improves momentum entry quality.',
    'EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE': 'SIDEWAYS floor for early momentum; higher bar when no trend to ride.',
    'EARLY_MOMENTUM_MIN_QUALITY': 'Momentum quality filter; reduces noise in early momentum.',
    'MOMENTUM_BREAKOUT_MIN_SCORE': 'Breakout path score floor; fewer weak breakouts.',
    'MOMENTUM_BREAKOUT_MIN_QUALITY': 'Breakout quality filter; confirms stronger breakouts only.',
    'BULL_EUPHORIA_FG':           'BULL euphoria veto; protects against buying tops in euphoric bull runs.',
    'BULL_EUPHORIA_7D_PCT':       '7d cap for BULL euphoria; blocks late entries after weekly spikes.',
    'BULL_EXTENDED_FG':           'BULL extended veto; prevents parabolic late entries.',
    'BULL_EXTENDED_30D_PCT':      '30d cap for BULL extended; filters blow-off top entries.',
    'SIDEWAYS_REGIME_MIN_SCORE':  'SIDEWAYS score floor; higher = fewer marginal trades in directionless market.',
    'BULL_REGIME_MIN_SCORE':      'BULL score floor; higher = fewer weak entries when regime is bullish.',
    'BULL_POSITION_SIZE_MULT':    'BULL position sizing; lower = less exposure when BULL classification uncertain.',
    'BULL_STOP_FLOOR_PCT':        'BULL stop floor; wider = more room for noise in misclassified BULL.',
    'BULL_CHASE_24H_MAX_PCT':     'BULL chase cap; lower = stricter filter against chasing extended moves in BULL.',
    'VOLUME_GATE_RATIO':          'Volume gate ratio; higher = more coins penalized, fewer thin-market entries.',
    'VOLUME_GATE_PENALTY':        'Volume gate penalty; higher = stronger filter on low-volume coins.',
    'ATH_PROXIMITY_VETO_PCT':     'ATH proximity gate; tighter (closer to 0) = more coins flagged near ATH, reduces top-buying.',
    'ATH_PROXIMITY_PENALTY':      'ATH penalty; higher = stronger score drag on near-ATH entries.',
    'FDV_DILUTION_RATIO_MAX':     'FDV dilution gate; lower ratio = more tokens flagged for dilution risk.',
    'FDV_DILUTION_PENALTY':       'FDV dilution penalty; higher = stronger score drag on high-dilution tokens.',
    'OB_RATIO_SNIPER_MIN':        'OB sniper minimum ratio; lower = more sniper entries, higher = stricter bid-ask filter.',
    'NARRATIVE_EXHAUSTION_FG':    'Narrative exhaustion veto; avoids buying when narrative is stretched.',
    'NARRATIVE_EXHAUSTION_7D_PCT': '7d price cap for narrative exhaustion; blocks extended runs.',
    'SHORT_TERM_PUMP_FG':         'Short-term pump F&G gate; vetoes overheated sentiment.',
    'EXTENDED_RALLY_FG':          'Extended rally veto; reduces FOMO entries after big moves.',
    'EXTENDED_RALLY_30D_PCT':     '30d rally cap; filters late participation in extended rallies.',
    'MAX_CUMULATIVE_ADJ':         'Cap on score inflation; keeps tiers from drifting too high.',
    'CONF_SCORE_BASE_WEIGHT':     'Weight on raw quant in confirmation; tunes final tier sensitivity.',
    'CONF_SNIPER_BONUS':          'Sniper entry bonus; rewards high-conviction setups.',
    'CONF_NARRATIVE_BONUS':      'Narrative bonus; aligns with sentiment spikes.',
    'CONF_WHALE_BONUS':           'Whale accumulation bonus; rewards on-chain signal.',
    'CONF_MAX_BONUS':             'Cap on confirmation bonuses; prevents over-promotion.',
    'CONF_PROMOTE_MIN_SCORE':     'Min score for confirmation-based promotion; gates tier upgrade.',
    'KELLY_FRACTION':             'Kelly fraction for edge; tunes position sizing from edge score.',
    # Tactical regime overlay
    'TACTICAL_SEVERE_24H_PCT':          'Severe downgrade trigger; catches sharp selloffs structural regime misses.',
    'TACTICAL_SEVERE_ALT_BREADTH_PCT':  'Alt breadth gate for severe downgrade; confirms broad weakness.',
    'TACTICAL_MODERATE_24H_PCT':        'Moderate downgrade trigger; catches developing selloffs before they worsen.',
    'TACTICAL_MODERATE_7D_PCT':         'Weekly trend divergence; catches BULL structural lag on multi-day drops.',
    'TACTICAL_MODERATE_RSI':            'RSI-based downgrade in SIDEWAYS; catches momentum exhaustion.',
    'TACTICAL_SEVERE_1H_PCT':          'Acute crash trigger; immediate protective downgrade on 1h panic.',
    'TACTICAL_UPGRADE_24H_PCT':         'Rally upgrade trigger; catches sharp recoveries structural regime misses.',
    'TACTICAL_UPGRADE_ALT_BREADTH_PCT': 'Alt breadth gate for rally upgrade; confirms broad strength.',
    'TACTICAL_UPGRADE_RSI':             'RSI confirmation for rally upgrade; validates momentum behind rally.',
}

# One-line "why this param relates to missed opportunities" for optimizer email (regret-minimizing learning)
PARAM_REGRET_REASON: Dict[str, str] = {
    'DIP_MIN_SCORE':              'A higher bar may have filtered dip setups that later rallied; relaxing can capture more dip entries.',
    'BLOOD_STREET_FG':            'Tight F&G may have skipped blood-in-the-street entries that then ran; loosening can reduce missed fear rallies.',
    'BLOOD_STREET_MIN_SCORE':     'High score floor may have excluded marginal blood setups that worked; lowering captures more.',
    'BEAR_REGIME_MIN_SCORE':      'Bear floor may have blocked good entries in volatile regimes; small relax can reduce missed reversals.',
    'PULLBACK_MIN_SCORE':         'Strict pullback gate may have missed pullbacks that resumed; slight lower captures more pullback wins.',
    'VOL_HARVEST_MIN_SCORE':      'Vol harvest bar may have filtered choppy entries that paid; relax to reduce missed vol harvest.',
    'CAPITULATION_MIN_SCORE':     'Capitulation gate may have skipped panic buys that bounced; lower bar captures more capitulation wins.',
    'BLOOD_FG_MAX':               'Low F&G max may have blocked blood entries that later rallied; raise to capture more.',
    'PULLBACK_FG_MAX':            'Low F&G max for pullback may have missed entries; raise to reduce missed pullbacks.',
    'VOL_HARVEST_FG_MAX':         'F&G cap may have blocked vol harvest entries; raise to capture more.',
    'FUNDING_VETO_BEAR_PCT':      'Aggressive funding veto may have blocked good bear longs; relax to reduce missed bear entries.',
    'VETO_CONFIDENCE_THRESHOLD':  'High veto threshold may have over-blocked; lower to allow more marginal signals.',
    'EDGE_BOOST_THRESHOLD':      'High edge bar may have blocked tier upgrades that would have taken good trades; lower to capture more.',
    'PUMP_24H_PCT':               'Low pump cap may have capped tier on strong momentum; raise to participate in more runners.',
    'SHORT_TERM_PUMP_24H_PCT':    'Pump veto may have blocked short-term momentum; relax to reduce missed pumps.',
    'EARLY_MOMENTUM_MIN_SCORE':   'High early-momentum bar may have filtered runners; lower to capture more early momentum.',
    'EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE': 'SIDEWAYS floor may have blocked momentum that worked; lower to capture more.',
    'EARLY_MOMENTUM_MIN_QUALITY': 'Quality gate may have skipped early momentum; relax to reduce missed.',
    'MOMENTUM_BREAKOUT_MIN_SCORE': 'Breakout score floor may have missed breakouts; lower to capture more.',
    'MOMENTUM_BREAKOUT_MIN_QUALITY': 'Breakout quality bar may have filtered good breakouts; relax to reduce missed.',
    'BULL_EUPHORIA_FG':           'Tight BULL euphoria veto may have blocked good momentum; relax to capture more BULL entries.',
    'BULL_EUPHORIA_7D_PCT':       '7d cap may have vetoed strong BULL momentum; raise to capture more.',
    'BULL_EXTENDED_FG':           'BULL extended veto may have blocked extended bull entries; relax to capture more.',
    'BULL_EXTENDED_30D_PCT':      '30d cap may have blocked strong BULL cycles; raise to capture more.',
    'SIDEWAYS_REGIME_MIN_SCORE':  'High SIDEWAYS floor may have blocked entries that worked; lower to capture more.',
    'BULL_REGIME_MIN_SCORE':      'Floor locked ≥72 (optimizer range 72–80); raise to tighten further, cannot lower — zero win rate observed below 72.',
    'BULL_POSITION_SIZE_MULT':    'Low BULL sizing may have reduced profits on correct BULL calls; raise to capture more.',
    'BULL_STOP_FLOOR_PCT':        'Wide BULL stop floor may have survived noise but reduced R:R; tighten if BULL accuracy improves.',
    'BULL_CHASE_24H_MAX_PCT':     'Tight BULL chase cap may have blocked strong momentum entries; raise to capture more.',
    'VOLUME_GATE_RATIO':          'Low ratio may have penalized coins that later pumped on volume; lower to reduce missed.',
    'VOLUME_GATE_PENALTY':        'High penalty may have pushed good low-vol setups below floor; lower to capture more.',
    'ATH_PROXIMITY_VETO_PCT':     'Tight ATH gate may have blocked coins breaking to new ATH; relax to capture breakout runners.',
    'ATH_PROXIMITY_PENALTY':      'High ATH penalty may have suppressed valid breakout entries; lower to capture more.',
    'FDV_DILUTION_RATIO_MAX':     'Low FDV ratio may have filtered tokens with locked supply that later pumped; raise to capture more.',
    'FDV_DILUTION_PENALTY':       'High FDV penalty may have blocked good setups in high-dilution tokens; lower to capture more.',
    'OB_RATIO_SNIPER_MIN':        'High OB ratio may have blocked sniper entries on mild bid-skew; lower to capture more.',
    'NARRATIVE_EXHAUSTION_FG':    'Narrative exhaustion veto may have blocked good narrative runs; relax to capture more.',
    'NARRATIVE_EXHAUSTION_7D_PCT': '7d cap may have vetoed extended narrative; raise to reduce missed.',
    'SHORT_TERM_PUMP_FG':         'Pump F&G veto may have blocked momentum; relax to reduce missed pumps.',
    'EXTENDED_RALLY_FG':          'Extended rally veto may have blocked participation; relax to capture more rally.',
    'EXTENDED_RALLY_30D_PCT':     '30d rally cap may have blocked late rally entries; raise to reduce missed.',
    'MAX_CUMULATIVE_ADJ':         'Low cap may have limited score and blocked tier upgrades; raise to capture more.',
    'CONF_SCORE_BASE_WEIGHT':     'Weight affects final tier; tuning can reduce missed promotions.',
    'CONF_SNIPER_BONUS':          'Low sniper bonus may have kept good setups from promoting; raise to capture more.',
    'CONF_NARRATIVE_BONUS':       'Narrative bonus affects tier; raise to capture narrative-driven moves.',
    'CONF_WHALE_BONUS':           'Whale bonus affects tier; raise to capture on-chain-driven entries.',
    'CONF_MAX_BONUS':             'Cap on bonuses may have limited promotions; raise to capture more.',
    'CONF_PROMOTE_MIN_SCORE':     'High promote bar may have blocked tier upgrades; lower to capture more.',
    'KELLY_FRACTION':             'Edge sizing affects position; tuning can improve capture of edge trades.',
    # Tactical regime overlay
    'TACTICAL_SEVERE_24H_PCT':          'Tight severe trigger may have downgraded regime during normal dips; relax to reduce false downgrades.',
    'TACTICAL_SEVERE_ALT_BREADTH_PCT':  'Low breadth bar may have triggered severe on normal rotation; raise to reduce false downgrades.',
    'TACTICAL_MODERATE_24H_PCT':        'Tight moderate trigger may have downgraded regime on normal pullbacks; relax to reduce missed entries.',
    'TACTICAL_MODERATE_7D_PCT':         'Tight 7d bar may have forced SIDEWAYS too often; relax to stay in BULL longer.',
    'TACTICAL_MODERATE_RSI':            'High RSI bar may have triggered VOLATILE too often; lower to reduce false downgrades.',
    'TACTICAL_SEVERE_1H_PCT':          'Tight 1h trigger may have over-reacted to flash crashes; relax to reduce false alarms.',
    'TACTICAL_UPGRADE_24H_PCT':         'High upgrade bar may have missed rally participation; lower to capture more rallies.',
    'TACTICAL_UPGRADE_ALT_BREADTH_PCT': 'High breadth bar may have blocked upgrades during strong BTC moves; lower to upgrade earlier.',
    'TACTICAL_UPGRADE_RSI':             'High RSI bar may have delayed upgrades; lower to catch earlier recoveries.',
}

PARAM_PATH_FILTER: Dict[str, str] = {
    'DIP_MIN_SCORE':              'dip',
    'BLOOD_STREET_FG':            'blood_in_the_street',
    'BLOOD_STREET_MIN_SCORE':     'blood_in_the_street',
    'BEAR_REGIME_MIN_SCORE':      '*',
    'PULLBACK_MIN_SCORE':         'pullback_in_trend',
    'VOL_HARVEST_MIN_SCORE':      'volatility_harvest',
    'CAPITULATION_MIN_SCORE':     'capitulation',
    'BLOOD_FG_MAX':               'blood_in_the_street',
    'PULLBACK_FG_MAX':            'pullback_in_trend',
    'VOL_HARVEST_FG_MAX':         'volatility_harvest',
    'FUNDING_VETO_BEAR_PCT':      '*',
    'VETO_CONFIDENCE_THRESHOLD':  '*',
    'EDGE_BOOST_THRESHOLD':       '*',
    'PUMP_24H_PCT':               '*',
    'SHORT_TERM_PUMP_24H_PCT':    '*',
    'EARLY_MOMENTUM_MIN_SCORE':   'early_momentum',
    'EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE': 'early_momentum',
    'EARLY_MOMENTUM_MIN_QUALITY': 'early_momentum',
    'MOMENTUM_BREAKOUT_MIN_SCORE': 'momentum_breakout',
    'MOMENTUM_BREAKOUT_MIN_QUALITY': 'momentum_breakout',
    'BULL_EUPHORIA_FG':           '*',
    'BULL_EUPHORIA_7D_PCT':       '*',
    'BULL_EXTENDED_FG':           '*',
    'BULL_EXTENDED_30D_PCT':      '*',
    'SIDEWAYS_REGIME_MIN_SCORE':  '*',
    'BULL_REGIME_MIN_SCORE':      '*',
    'BULL_POSITION_SIZE_MULT':    '*',
    'BULL_STOP_FLOOR_PCT':        '*',
    'BULL_CHASE_24H_MAX_PCT':     'early_momentum,momentum_breakout',
    'VOLUME_GATE_RATIO':          '*',
    'VOLUME_GATE_PENALTY':        '*',
    'ATH_PROXIMITY_VETO_PCT':     '*',
    'ATH_PROXIMITY_PENALTY':      '*',
    'FDV_DILUTION_RATIO_MAX':     '*',
    'FDV_DILUTION_PENALTY':       '*',
    'OB_RATIO_SNIPER_MIN':        '*',
    'NARRATIVE_EXHAUSTION_FG':    '*',
    'NARRATIVE_EXHAUSTION_7D_PCT': '*',
    'SHORT_TERM_PUMP_FG':         '*',
    'EXTENDED_RALLY_FG':          '*',
    'EXTENDED_RALLY_30D_PCT':     '*',
    'MAX_CUMULATIVE_ADJ':         '*',
    'CONF_SCORE_BASE_WEIGHT':     '*',
    'CONF_SNIPER_BONUS':          '*',
    'CONF_NARRATIVE_BONUS':       '*',
    'CONF_WHALE_BONUS':           '*',
    'CONF_MAX_BONUS':             '*',
    'CONF_PROMOTE_MIN_SCORE':     '*',
    'KELLY_FRACTION':             '*',
    # Tactical regime overlay
    'TACTICAL_SEVERE_24H_PCT':          '*',
    'TACTICAL_SEVERE_ALT_BREADTH_PCT':  '*',
    'TACTICAL_MODERATE_24H_PCT':        '*',
    'TACTICAL_MODERATE_7D_PCT':         '*',
    'TACTICAL_MODERATE_RSI':            '*',
    'TACTICAL_SEVERE_1H_PCT':          '*',
    'TACTICAL_UPGRADE_24H_PCT':         '*',
    'TACTICAL_UPGRADE_ALT_BREADTH_PCT': '*',
    'TACTICAL_UPGRADE_RSI':             '*',
}

# Regime filter for counterfactual — only use trades in these regimes when evaluating the param
# Params not listed: no regime filter (use all trades that pass path filter)
PARAM_REGIME_FILTER: Dict[str, list] = {
    'BEAR_REGIME_MIN_SCORE':      ['BEAR', 'VOLATILE'],
    'SIDEWAYS_REGIME_MIN_SCORE':  ['SIDEWAYS'],
    'EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE': ['SIDEWAYS'],
    'BULL_REGIME_MIN_SCORE':      ['BULL'],
    'BULL_POSITION_SIZE_MULT':    ['BULL'],
    'BULL_STOP_FLOOR_PCT':        ['BULL'],
    'BULL_CHASE_24H_MAX_PCT':     ['BULL'],
}

# Maps each optimizer param to the trade field it should be compared against.
# Score floors use 'confirmation_score', FG gates use 'fear_greed', etc.
PARAM_FIELD_MAP: Dict[str, str] = {
    'DIP_MIN_SCORE':              'confirmation_score',
    'BLOOD_STREET_FG':            'fear_greed',
    'BLOOD_STREET_MIN_SCORE':     'confirmation_score',
    'BEAR_REGIME_MIN_SCORE':      'confirmation_score',
    'PULLBACK_MIN_SCORE':         'confirmation_score',
    'VOL_HARVEST_MIN_SCORE':      'confirmation_score',
    'CAPITULATION_MIN_SCORE':     'confirmation_score',
    'BLOOD_FG_MAX':               'fear_greed',
    'PULLBACK_FG_MAX':            'fear_greed',
    'VOL_HARVEST_FG_MAX':         'fear_greed',
    'FUNDING_VETO_BEAR_PCT':      'funding_rate',
    'VETO_CONFIDENCE_THRESHOLD':  'confirmation_score',
    'EDGE_BOOST_THRESHOLD':       'edge_score',
    'PUMP_24H_PCT':               'price_change_24h',
    'SHORT_TERM_PUMP_24H_PCT':    'price_change_24h',
    'EARLY_MOMENTUM_MIN_SCORE':   'confirmation_score',
    'EARLY_MOMENTUM_SIDEWAYS_MIN_SCORE': 'confirmation_score',
    'EARLY_MOMENTUM_MIN_QUALITY': 'confirmation_score',
    'MOMENTUM_BREAKOUT_MIN_SCORE': 'confirmation_score',
    'MOMENTUM_BREAKOUT_MIN_QUALITY': 'confirmation_score',
    'MOM30_MIN_CH30D':            'price_change_30d',  # Stage 1b: gates on stored 30d momentum
    'BULL_EUPHORIA_FG':           'fear_greed',
    'BULL_EUPHORIA_7D_PCT':       'price_change_7d',
    'BULL_EXTENDED_FG':           'fear_greed',
    'BULL_EXTENDED_30D_PCT':      'price_change_30d',
    'SIDEWAYS_REGIME_MIN_SCORE':  'confirmation_score',
    'BULL_REGIME_MIN_SCORE':      'confirmation_score',
    'BULL_CHASE_24H_MAX_PCT':     'price_change_24h',
    # BULL_POSITION_SIZE_MULT and BULL_STOP_FLOOR_PCT removed from PARAM_FIELD_MAP:
    # They affect position SIZING and stop PLACEMENT, not entry gating.
    # Counterfactual "would_be_blocked" is the wrong question for sizing params.
    # TODO: Add a sizing-aware counterfactual evaluation when n>100 trades.
    'VOLUME_GATE_RATIO':          'confirmation_score',  # volume/mcap not in attribution; confirmation_score is proxy
    'VOLUME_GATE_PENALTY':        'confirmation_score',  # same
    'ATH_PROXIMITY_VETO_PCT':     'ath_change_pct',      # actual CoinGecko ath_change_percentage stored on trade
    'ATH_PROXIMITY_PENALTY':      'ath_change_pct',      # same
    'FDV_DILUTION_RATIO_MAX':     'fdv_ratio',           # fdv/market_cap ratio stored on trade at entry
    'FDV_DILUTION_PENALTY':       'fdv_ratio',           # same
    'OB_RATIO_SNIPER_MIN':        'confirmation_score',  # order book ratio not in attribution; proxy
    'NARRATIVE_EXHAUSTION_FG':    'fear_greed',
    'NARRATIVE_EXHAUSTION_7D_PCT': 'price_change_7d',
    'SHORT_TERM_PUMP_FG':         'fear_greed',
    'EXTENDED_RALLY_FG':          'fear_greed',
    'EXTENDED_RALLY_30D_PCT':     'price_change_30d',
    'MAX_CUMULATIVE_ADJ':         'confirmation_score',
    'CONF_SCORE_BASE_WEIGHT':     'confirmation_score',
    'CONF_SNIPER_BONUS':          'confirmation_score',
    'CONF_NARRATIVE_BONUS':       'confirmation_score',
    'CONF_WHALE_BONUS':           'confirmation_score',
    'CONF_MAX_BONUS':             'confirmation_score',
    'CONF_PROMOTE_MIN_SCORE':     'confirmation_score',
    'KELLY_FRACTION':             'edge_score',
    # Tactical regime overlay — best available trade-field proxies
    # (BTC-specific fields not stored per-trade; coin's own change is the closest proxy)
    'TACTICAL_SEVERE_24H_PCT':          'price_change_24h',
    'TACTICAL_SEVERE_ALT_BREADTH_PCT':  'confirmation_score',   # alt breadth not stored per-trade
    'TACTICAL_MODERATE_24H_PCT':        'price_change_24h',
    'TACTICAL_MODERATE_7D_PCT':         'price_change_7d',
    'TACTICAL_MODERATE_RSI':            'confirmation_score',   # RSI not stored per-trade
    'TACTICAL_SEVERE_1H_PCT':           'price_change_24h',     # 1h not stored; 24h is best proxy
    'TACTICAL_UPGRADE_24H_PCT':         'price_change_24h',
    'TACTICAL_UPGRADE_ALT_BREADTH_PCT': 'confirmation_score',
    'TACTICAL_UPGRADE_RSI':             'confirmation_score',
}

_loaded_once: bool = False
_loaded_values: Dict[str, Any] = {}
_loaded_ts: float = 0
_THRESHOLD_TTL: float = 300.0  # 5 min TTL to prevent permanent staleness


def _load_live_thresholds() -> Dict[str, Any]:
    """Load live_thresholds.json and merge with defaults. FIX: try/except with fallback + print once.

    Thread-local overrides (threshold_override) are merged LAST, into a copy — so callers
    that read the whole dict (quant_signal's `live = _load_live_thresholds()`) honor trial
    overrides exactly like get_threshold() does. Previously overrides only reached
    get_threshold(), so Optuna/gate-driver overrides silently never affected the quant gate."""
    global _loaded_once, _loaded_values, _loaded_ts
    now = time.time()
    with _thresholds_lock:
        if _loaded_once and (now - _loaded_ts) < _THRESHOLD_TTL:
            return _merge_overrides(_loaded_values)
        try:
            import safe_json
            data = safe_json.load(LIVE_THRESHOLDS_PATH, default=None)
            if data is not None and isinstance(data, dict):
                # Merge: file overrides defaults (only scalar values used as thresholds)
                out = dict(DEFAULT_THRESHOLDS)
                for k, v in data.items():
                    if k in out and isinstance(v, (int, float, str, bool)):
                        out[k] = v
                _loaded_values = out
            else:
                _loaded_values = dict(DEFAULT_THRESHOLDS)
        except Exception as e:
            if not _loaded_once:
                logger.warning("[thresholds] Fallback to defaults: %s", e)
            _loaded_values = dict(DEFAULT_THRESHOLDS)
        _loaded_once = True
        _loaded_ts = now
        return _merge_overrides(_loaded_values)


def _merge_overrides(values: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay thread-local overrides onto a COPY; never mutate the shared cache."""
    overrides = getattr(_threshold_overrides, 'values', None)
    if not overrides:
        return values
    merged = dict(values)
    merged.update(overrides)
    return merged


# Thread-local override support for Optuna trials (never touches live_thresholds.json)
_threshold_overrides = threading.local()


@contextmanager
def threshold_override(overrides: Dict[str, Any]):
    """Context manager for temporary threshold overrides (Optuna trials).

    Thread-safe: uses threading.local so concurrent trials don't interfere.
    """
    _threshold_overrides.values = overrides
    try:
        yield
    finally:
        _threshold_overrides.values = None


def get_threshold(key: str, default: Any = None) -> Any:
    """Get a single threshold by key. Checks thread-local overrides first (for Optuna)."""
    overrides = getattr(_threshold_overrides, 'values', None)
    if overrides and key in overrides:
        return overrides[key]
    t = _load_live_thresholds()
    return t.get(key, default if default is not None else DEFAULT_THRESHOLDS.get(key))


def reload_live_thresholds() -> None:
    """Call after daily retrain writes live_thresholds.json so next read gets fresh values.
    Thread-safe: sets _loaded_once=False under lock, then immediately reloads so there's
    no window where another thread reads stale values."""
    global _loaded_once, _loaded_values
    with _thresholds_lock:
        _loaded_once = False
    # Force immediate reload so no caller sees stale data between reset and next get_threshold()
    _load_live_thresholds()
