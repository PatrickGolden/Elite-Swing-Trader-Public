# =============================================================================
# app.py — ELITE SWING TRADER DASHBOARD
# Complete multi-AI trading system with regime detection and risk management
#
# FEATURES:
#   - Quant-first architecture: Quant score + Rule-based veto
#   - Market Regime Detection (Bull/Bear/Sideways/Volatile)
#   - 12 Data Sources (Enhanced)
#   - Real-Time Alerts (Telegram/Discord)
#   - Backtesting Engine
#   - Performance Tracking
#
# Run with: python app.py
# Then open: http://localhost:5000
# =============================================================================

import os
import sys
import json
import logging
import time
import traceback
import random

# Wire OS trust store into Python's SSL stack (must come before any
# import that creates an SSLContext, e.g. requests/urllib3/ccxt). Required
# on machines where AV / corporate proxy does HTTPS inspection.
import tls_setup  # noqa: F401

# Prevent double module initialization: when run as `python app.py`, this file
# loads as __main__.  Later `from app import X` would re-execute it as a second
# module with separate globals (locks, threads, schedulers).  Aliasing early
# ensures Python reuses the already-loaded module for any `import app`.
if __name__ == '__main__':
    sys.modules.setdefault('app', sys.modules[__name__])

from logging_config import setup_logging
setup_logging()
# Use fixed name so logs are consistent whether run as __main__ or imported (avoids duplicate __main__/app streams)
logger = logging.getLogger("app")

_TESTING = os.environ.get("PYTEST_CURRENT_TEST") is not None or os.environ.get("TESTING") == "1"
LEAN_PROFIT_FIRST_MODE = True
LEAN_MIN_CLOSED_TRADES_FOR_ADVANCED = 100
LEAN_MIN_ATTR_COMPLETENESS_PCT = 95.0
LEAN_MIN_EXPECTANCY_PCT = 0.0
ROADMAP_DOC_PATH = os.path.join(os.path.dirname(__file__), 'docs', 'LEAN_PROFIT_ROADMAP.md')

if not _TESTING:
    import socket as _sock
    _probe = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    _probe.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    try:
        _probe.bind(('127.0.0.1', 5099))
        _probe.listen(1)
    except OSError:
        print("[FATAL] Another instance of app.py is already running. Exiting.")
        raise SystemExit(1)

import threading
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify, request, Response, send_file, send_from_directory
from flask_cors import CORS

from dashboard_json import DashboardJSONProvider
from dotenv import load_dotenv
from system_manual import ManualGenerator
from dynamic_targets import DynamicTargetCalculator
from scheduler_manager import SchedulerManager
from regime_sell_softening import apply_regime_sentiment_sell_softening

# Base modules
from indicators import calculate_indicators
from scorer import score_tokens
from trade_journal import (
    open_position, close_position, update_targets, update_position, delete_position,
    get_all_positions, get_open_positions, get_closed_positions, get_test_positions, enrich_open_positions, calculate_stats, export_csv, export_xlsx,
    is_paper_trade,
)
from portfolio_manager import (
    get_portfolio_risk_summary,
    get_correlation_adjustment_factors,
    apply_correlation_adjustment,
)
from execution_engine import (
    execution_safety_ok,
    start_twap_buy_async,
    get_active_twap_orders,
    unregister_pending_order,
    log_skipped_insufficient_cash,
    fetch_balance_sync,
    get_coinbase_exchange,
    get_coinbase_tradeable_symbols,
    symbol_to_ccxt,
    execute_market_sell,
    place_limit_sell_order,
    place_stop_limit_sell_order,
    cancel_order,
    cancel_and_wait,
    execute_trailing_stop_sell,
    fetch_order_status,
    _drain_pending_journal_fills,
    AUCTION_MODE_RETRY_AFTER_SEC,
)
import execution_engine as _execution_engine_module
from veto_specialist import evaluate as veto_specialist_evaluate, apply_veto_override
from testing_log import log_signal as testing_log_signal, get_signal_log, get_testing_stats, export_signal_log_csv
from signal_history import log_snapshot as signal_history_log_snapshot
from refresh_snapshot import log_refresh_snapshot
from screening_log import append_screening_entry
# ELITE MODULES
from enhanced_data_fetcher import EnhancedDataFetcherV2 as DataFetcher
from market_regime import MarketRegimeDetector
from position_sizer import PositionSizer
from quant_signal import generate_quant_signal
from alert_system import AlertSystem
from backtester import Backtester
from monitoring import (
    load_backtest_expectancy,
    save_backtest_expectancy,
    get_live_stats_by_regime,
    check_for_drift,
)

load_dotenv()

from admin_api_guard import require_admin_write_access
from routes.alerts_bp import alerts_bp
from routes.optimizer_roadmap_bp import optimizer_roadmap_bp
from routes.schedule_bp import schedule_bp
from routes.journal_bp import journal_bp
from routes.account_bp import account_bp


# Execution config: auto-execution enabled + exchange (coinbase / kraken)
# last_auto_sync_at lives in a sidecar under data/ so balance sync does not rewrite the same file
# as the auto_execution toggle (avoids cross-process stale RMW clobbering the flag).
EXECUTION_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'execution_config.json')
LAST_AUTO_SYNC_FILE = os.path.join(os.path.dirname(__file__), 'data', 'last_auto_sync_at.json')
EXECUTION_CONFIG_AUDIT_FILE = os.path.join(os.path.dirname(__file__), 'data', 'execution_config_audit.jsonl')
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
_EXECUTION_CONFIG_DISK_KEYS = (
    'auto_execution_enabled',
    'auto_execution_exchange',
    'auto_sync_balance',
    'twap_min_usd',
    'stale_position_rule_enabled',
)
_exec_config_lock = threading.Lock()  # guards read-modify-write on execution_config.json (in-process)

# Per-position locks guarding the cancel→place→update_position sequence used by the
# stop-loss reprice flow. Without this, concurrent monitor passes can race and place
# duplicate stop orders that lock 2× the available base balance, leading to
# INSUFFICIENT_FUND on subsequent attempts. See plan: functional-conjuring-allen.md.
_sl_reprice_locks: dict[int, threading.Lock] = {}
_sl_reprice_locks_guard = threading.Lock()


def _get_sl_reprice_lock(position_id: int) -> threading.Lock:
    with _sl_reprice_locks_guard:
        lock = _sl_reprice_locks.get(position_id)
        if lock is None:
            lock = threading.Lock()
            _sl_reprice_locks[position_id] = lock
        return lock


def _append_execution_config_audit(before_disk: dict, after_disk: dict, audit_source: str | None, audit_extra: dict | None) -> None:
    """Append-only trail when execution_config.json is saved (data/ is gitignored). Skipped under tests."""
    if _TESTING:
        return
    try:
        from execution_config_file_lock import execution_config_cross_lock

        rec = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'pid': os.getpid(),
            'source': audit_source or 'set_execution_config',
            'before': before_disk,
            'after': after_disk,
            'extra': audit_extra or {},
        }
        line = json.dumps(rec, default=str, sort_keys=True) + '\n'
        ddir = os.path.dirname(os.path.abspath(EXECUTION_CONFIG_AUDIT_FILE))
        if ddir:
            os.makedirs(ddir, exist_ok=True)
        with execution_config_cross_lock(EXECUTION_CONFIG_AUDIT_FILE):
            with open(EXECUTION_CONFIG_AUDIT_FILE, 'a', encoding='utf-8') as af:
                af.write(line)
    except Exception as e:
        logger.error('CRITICAL: execution_config audit append failed — audit trail may be incomplete: %s', e)


def _exchange_keys_configured():
    """Return { coinbase: bool, kraken: bool } based on env (no keys exposed)."""
    return {
        'coinbase': bool((os.getenv('COINBASE_API_KEY') or '').strip() and (os.getenv('COINBASE_API_SECRET') or '').strip()),
        'kraken': bool((os.getenv('KRAKEN_API_KEY') or '').strip() and (os.getenv('KRAKEN_API_SECRET') or '').strip()),
    }


def _resolve_last_auto_sync_at(main_data):
    """Authoritative source: sidecar file (data/last_auto_sync_at.json).
    Legacy fallback: 'last_auto_sync_at' key inside execution_config.json (read-only, not written to anymore)."""
    import safe_json

    main_data = main_data if isinstance(main_data, dict) else {}
    side = safe_json.load(LAST_AUTO_SYNC_FILE)
    if side and isinstance(side, dict):
        sv = side.get('last_auto_sync_at')
        if sv:
            return sv
    mv = main_data.get('last_auto_sync_at')
    return mv if mv else None


def get_execution_config():
    """Return { auto_execution_enabled, auto_execution_exchange, auto_sync_balance, last_auto_sync_at, twap_min_usd, stale_position_rule_enabled }."""
    import safe_json

    defaults = {
        # FAIL-CLOSED: a missing/corrupt config file must never arm live trading.
        # Real-money execution requires an explicit `"auto_execution_enabled": true` on disk.
        'auto_execution_enabled': False,
        'auto_execution_exchange': 'coinbase',
        'auto_sync_balance': True,
        'last_auto_sync_at': None,
        'twap_min_usd': 500.0,
        'stale_position_rule_enabled': True,
    }
    data = safe_json.load(EXECUTION_CONFIG_FILE)
    if not data:
        return {**defaults, 'last_auto_sync_at': _resolve_last_auto_sync_at({})}
    twap_min = data.get('twap_min_usd')
    twap_min = float(twap_min) if twap_min is not None else 500.0
    # FAIL-CLOSED: only explicit JSON true → on; missing/null/malformed → off.
    _ae = data.get('auto_execution_enabled', False)
    _auto_exec_on = True if _ae is True else False
    return {
        'auto_execution_enabled': _auto_exec_on,
        'auto_execution_exchange': (data.get('auto_execution_exchange') or 'coinbase').lower(),
        'auto_sync_balance': bool(data.get('auto_sync_balance', True)),
        'last_auto_sync_at': _resolve_last_auto_sync_at(data),
        'twap_min_usd': twap_min,
        'stale_position_rule_enabled': bool(data.get('stale_position_rule_enabled', True)),
    }


def set_execution_config(
    auto_execution_enabled=None,
    auto_execution_exchange=None,
    auto_sync_balance=None,
    twap_min_usd=None,
    stale_position_rule_enabled=None,
    *,
    audit_source=None,
    audit_extra=None,
):
    """Update and persist execution config. Returns False if the main config file could not be saved."""
    import safe_json
    from execution_config_file_lock import execution_config_cross_lock

    with _exec_config_lock:
        with execution_config_cross_lock(EXECUTION_CONFIG_FILE):
            before = get_execution_config()
            cur = dict(before)
            if auto_execution_enabled is not None:
                cur['auto_execution_enabled'] = bool(auto_execution_enabled)
            if auto_execution_exchange is not None:
                cur['auto_execution_exchange'] = 'kraken' if str(auto_execution_exchange).lower() == 'kraken' else 'coinbase'
            if auto_sync_balance is not None:
                cur['auto_sync_balance'] = bool(auto_sync_balance)
            if twap_min_usd is not None:
                cur['twap_min_usd'] = max(0.0, float(twap_min_usd))
            if stale_position_rule_enabled is not None:
                cur['stale_position_rule_enabled'] = bool(stale_position_rule_enabled)
            # One-time migration: legacy last_auto_sync_at inside main JSON → sidecar (then stripped from main on save)
            raw_main = safe_json.load(EXECUTION_CONFIG_FILE) or {}
            legacy_las = raw_main.get('last_auto_sync_at')
            side_raw = safe_json.load(LAST_AUTO_SYNC_FILE) or {}
            if legacy_las and not side_raw.get('last_auto_sync_at'):
                with execution_config_cross_lock(LAST_AUTO_SYNC_FILE):
                    safe_json.save(LAST_AUTO_SYNC_FILE, {'last_auto_sync_at': legacy_las})
            payload = {k: cur[k] for k in _EXECUTION_CONFIG_DISK_KEYS}
            before_disk = {k: before.get(k) for k in _EXECUTION_CONFIG_DISK_KEYS}
            after_disk = {k: cur[k] for k in _EXECUTION_CONFIG_DISK_KEYS}
            ok = safe_json.save(EXECUTION_CONFIG_FILE, payload)
            if not ok:
                logger.warning("execution_config: safe_json.save failed for %s", EXECUTION_CONFIG_FILE)
            else:
                _append_execution_config_audit(before_disk, after_disk, audit_source, audit_extra)
                if before.get('auto_execution_enabled') != cur.get('auto_execution_enabled'):
                    logger.info(
                        "execution_config: auto_execution_enabled %s -> %s",
                        before.get('auto_execution_enabled'),
                        cur.get('auto_execution_enabled'),
                    )
            return ok


def _set_last_auto_sync_at(iso_str):
    """Persist last auto-sync timestamp (sidecar only — does not rewrite execution toggles)."""
    import safe_json
    from execution_config_file_lock import execution_config_cross_lock

    ddir = os.path.dirname(os.path.abspath(LAST_AUTO_SYNC_FILE))
    if ddir:
        os.makedirs(ddir, exist_ok=True)
    with execution_config_cross_lock(LAST_AUTO_SYNC_FILE):
        ok = safe_json.save(LAST_AUTO_SYNC_FILE, {'last_auto_sync_at': iso_str})
    if not ok:
        logger.warning("execution_config: failed to save last_auto_sync sidecar %s", LAST_AUTO_SYNC_FILE)


# Test Mode: paper trading only; when on, Light Mode reduces API usage (see test_mode.py)
from test_mode import (
    get_test_mode,
    set_test_mode,
    get_paper_trading,
    get_paper_trade_notional_usd,
    set_paper_trading,
    set_paper_trade_notional_usd,
)

# Simulated paper fills: MEASURED costs from fill_cost_model (STRATEGY.md §4.2).
# Previously 0.1%/side fee vs the measured 1.20%/side taker — a 12x understatement
# that made paper results uncomparable to live (the historical 7x paper/live gap).
from fill_cost_model import (
    TAKER_FEE_PCT_PER_SIDE as PAPER_TRADE_FEE_PCT,
    SLIPPAGE_PCT_PER_SIDE as PAPER_TRADE_BUY_SLIPPAGE_PCT,
    SLIPPAGE_PCT_PER_SIDE as PAPER_TRADE_SELL_SLIPPAGE_PCT,
)


def _light_test_pipeline() -> bool:
    """When True, use reduced top-N and cached enhanced data. Paper trading uses full production breadth."""
    return bool(get_test_mode() and not get_paper_trading())

# Light Test Mode: cache enhanced/on-chain data for 30 min when test mode (reduces API calls)
_enhanced_data_cache = {'raw': None, 'ts': 0}
_ENHANCED_CACHE_TTL = 30 * 60  # seconds

# Account Overview: total cash (account size), risk % — persisted for widget and position_sizer
ACCOUNT_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'account_config.json')
SYNC_FAILURE_COUNT_FILE = os.path.join(os.path.dirname(__file__), 'data', 'sync_failure_count.json')
SYNC_FAILURE_AUDIT_FILE = os.path.join(os.path.dirname(__file__), 'data', 'sync_failure_audit.jsonl')
REPEATED_SYNC_FAILURE_THRESHOLD = 3


def load_account_config():
    """Load account config. Returns dict with defaults if missing."""
    import safe_json
    defaults = {
        'total_cash': 0,
        'risk_pct': float(os.getenv('MAX_RISK_PCT', 2.0)),
        'max_cash_usage_pct': 40.0,
        'dust_threshold_usd': 1.0,
        'heloc_mode': False,
        'heloc_notional': 0.0,
        'heloc_rate_pct': 8.0,
        'usdc_yield_pct': 4.1,
        'usdc_yield_auto_refresh': True,
        'usdc_yield_last_updated': None,
        'heloc_drawn': 0.0,
        'heloc_drawn_peak': 0.0,
        'max_drawn_drawdown_pct': 30.0,
        'hurdle_mode': 'per_trade',
    }
    data = safe_json.load(ACCOUNT_CONFIG_FILE)
    if not data:
        return defaults
    # Migration: heloc_target_account_size → heloc_notional
    if 'heloc_target_account_size' in data and 'heloc_notional' not in data:
        data['heloc_notional'] = data.pop('heloc_target_account_size', 0)
    elif 'heloc_target_account_size' in data:
        data.pop('heloc_target_account_size', None)
    return {
        'total_cash': float(data.get('total_cash', 0) or 0),
        'risk_pct': float(data.get('risk_pct', 2.0) or 2.0),
        'max_cash_usage_pct': float(data.get('max_cash_usage_pct', 40.0) or 40.0),
        'dust_threshold_usd': float(data.get('dust_threshold_usd', 1.0) or 1.0),
        'heloc_mode': bool(data.get('heloc_mode', False)),
        'heloc_notional': float(data.get('heloc_notional', 0) or 0),
        'heloc_rate_pct': float(data.get('heloc_rate_pct', 8.0) or 8.0),
        'usdc_yield_pct': float(data.get('usdc_yield_pct', 4.1) or 4.1),
        'usdc_yield_auto_refresh': bool(data.get('usdc_yield_auto_refresh', True)),
        'usdc_yield_last_updated': data.get('usdc_yield_last_updated'),
        'heloc_drawn': float(data.get('heloc_drawn', 0) or 0),
        'heloc_drawn_peak': float(data.get('heloc_drawn_peak', 0) or 0),
        'max_drawn_drawdown_pct': float(data.get('max_drawn_drawdown_pct', 30.0) or 30.0),
        'hurdle_mode': str(data.get('hurdle_mode', 'per_trade') or 'per_trade'),
    }


def save_account_config(total_cash=None, risk_pct=None, max_cash_usage_pct=None, dust_threshold_usd=None,
                        heloc_mode=None, heloc_notional=None, heloc_rate_pct=None,
                        usdc_yield_pct=None, usdc_yield_auto_refresh=None, usdc_yield_last_updated=None,
                        heloc_drawn=None, heloc_drawn_peak=None, max_drawn_drawdown_pct=None, hurdle_mode=None):
    """Update and persist account config. None means leave unchanged."""
    cur = load_account_config()
    if total_cash is not None:
        cur['total_cash'] = float(total_cash)
    if risk_pct is not None:
        cur['risk_pct'] = float(risk_pct)
    if max_cash_usage_pct is not None:
        cur['max_cash_usage_pct'] = float(max_cash_usage_pct)
    if dust_threshold_usd is not None:
        cur['dust_threshold_usd'] = float(dust_threshold_usd)
    if heloc_mode is not None:
        cur['heloc_mode'] = bool(heloc_mode)
    if heloc_notional is not None:
        cur['heloc_notional'] = float(heloc_notional)
    if heloc_rate_pct is not None:
        cur['heloc_rate_pct'] = float(heloc_rate_pct)
    if usdc_yield_pct is not None:
        cur['usdc_yield_pct'] = float(usdc_yield_pct)
    if usdc_yield_auto_refresh is not None:
        cur['usdc_yield_auto_refresh'] = bool(usdc_yield_auto_refresh)
    if usdc_yield_last_updated is not None:
        cur['usdc_yield_last_updated'] = usdc_yield_last_updated
    if heloc_drawn is not None:
        cur['heloc_drawn'] = float(heloc_drawn)
        # Update peak if new drawn exceeds it
        if cur['heloc_drawn'] > cur.get('heloc_drawn_peak', 0):
            cur['heloc_drawn_peak'] = cur['heloc_drawn']
    if heloc_drawn_peak is not None:
        cur['heloc_drawn_peak'] = float(heloc_drawn_peak)
    if max_drawn_drawdown_pct is not None:
        cur['max_drawn_drawdown_pct'] = float(max_drawn_drawdown_pct)
    if hurdle_mode is not None:
        cur['hurdle_mode'] = str(hurdle_mode)
    # Remove legacy field if present
    cur.pop('heloc_target_account_size', None)
    import safe_json
    return safe_json.save(ACCOUNT_CONFIG_FILE, cur)


# =============================================================================
# Stablecoin Filter — Exclude from analysis
# =============================================================================

STABLECOINS = {
    # USD-pegged
    'usdt', 'tether',
    'usdc', 'usd-coin',
    'busd', 'binance-usd', 'binance-peg-busd',
    'dai',
    'tusd', 'trueusd',
    'usdp', 'paxos-standard',
    'gusd', 'gemini-dollar',
    'usdd',
    'frax',
    'lusd',
    'susd',
    'usdn', 'neutrino',
    'ust', 'terrausd',
    'cusd', 'celo-dollar',
    'mimatic',
    'fei',
    'vai',
    'eurs',
    'eurt',
    'gbpt',
    'fdusd', 'first-digital-usd',
    'usds',
    'ethena-usde',
    # Algorithmic stablecoins
    'dei',
    'mim', 'magic-internet-money',
    # Wrapped versions
    'wusdc',
    'wusdt',
    'wdai',
}

def is_stablecoin(coin):
    """
    Check if a coin is a stablecoin.
    
    Stablecoins don't swing trade - they're pegged to $1.
    No point analyzing USDT, USDC, DAI for buy signals.
    """
    coin_id = coin.get('id', '').lower()
    coin_symbol = coin.get('symbol', '').lower()
    coin_name = coin.get('name', '').lower()
    
    # Check against stablecoin list
    if coin_id in STABLECOINS:
        return True
    if coin_symbol in STABLECOINS:
        return True
    
    # Check if name contains stablecoin keywords AND price is near $1
    stablecoin_keywords = ['usd', 'dollar', 'euro', 'stablecoin']
    for keyword in stablecoin_keywords:
        if keyword in coin_name:
            # `or 0` guards a present-but-null current_price (CoinGecko returns null for
            # some coins); `.get(k, 0)` only covers a MISSING key, so a null value would
            # reach `0.95 <= None` and crash the entire refresh (seen 2026-06-23).
            price = coin.get('current_price') or 0
            # If price is near $1, it's likely a stablecoin
            if 0.95 <= price <= 1.05:
                return True
    
    return False


app = Flask(__name__)
app.json = DashboardJSONProvider(app)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB
CORS(app, resources={r"/api/*": {"origins": [
    "http://localhost:5000", "http://127.0.0.1:5000",
    "http://localhost:5099", "http://127.0.0.1:5099",
]}})


@app.route("/favicon.ico")
def _favicon():
    """Avoid 404 noise in access logs; browsers request /favicon.ico by default."""
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.svg",
        mimetype="image/svg+xml",
    )


# ---------------------------------------------------------------------------
# Structured error handlers — return JSON instead of raw tracebacks
# ---------------------------------------------------------------------------
@app.errorhandler(400)
def _handle_400(e):
    return jsonify({'error': 'Bad request', 'detail': str(e)}), 400

@app.errorhandler(404)
def _handle_404(e):
    return jsonify({'error': 'Not found', 'detail': str(e)}), 404

@app.errorhandler(500)
def _handle_500(e):
    logger.error("Unhandled 500: %s", e, exc_info=True)
    return jsonify({'error': 'Internal server error'}), 500


@app.after_request
def _add_no_cache_headers(response):
    """Prevent browser caching so reload always gets fresh data from current server instance."""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


# =============================================================================
# Initialize All Systems
# =============================================================================

# Data & Analysis
fetcher = DataFetcher()
regime_detector = MarketRegimeDetector()
# Initialize manual generator
manual_generator = ManualGenerator()

# Decision Systems
_account_cfg = load_account_config()
position_sizer = PositionSizer(
    account_size=_account_cfg['total_cash'],
    max_risk_pct=_account_cfg['risk_pct'],
    max_cash_usage_pct=_account_cfg.get('max_cash_usage_pct', 40.0),
)
# If HELOC mode on, set account_size to notional for risk % calcs
if _account_cfg.get('heloc_mode') and _account_cfg.get('heloc_notional', 0) > 0:
    position_sizer.update_account_size(_account_cfg['heloc_notional'])

# Dynamic Targets Calculator
target_calculator = DynamicTargetCalculator(base_stop_pct=6.0, min_stop_pct=4.0, max_stop_pct=15.0)

# Scheduler Manager
scheduler = SchedulerManager()

# Utilities
alerts = AlertSystem()
backtester = Backtester()

app.register_blueprint(alerts_bp)
app.register_blueprint(optimizer_roadmap_bp)
app.register_blueprint(schedule_bp)
app.register_blueprint(journal_bp)
app.register_blueprint(account_bp)

# Server start time for runtime counter (UTC ISO)
APP_STARTED_AT = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
_app_start_time = time.time()
# No exit/cash alerts for this many seconds after startup (avoids false alerts before data is ready)
ALERT_GRACE_SECONDS = 90

# Cache (thread-safe: use _cache_lock for all reads/writes)
_cache_lock = threading.Lock()
cached_data = {}
last_updated = None
refresh_count = 0
current_regime = None

# Reconcile cache (avoids hitting exchange API on every dashboard load)
_reconcile_cache = {'data': None, 'ts': 0}
_RECONCILE_CACHE_TTL = 300  # 5 minutes

# Data Source Health Tracking
data_source_status = {
    'coingecko': {'status': 'unknown', 'time': None, 'error': None},
    'fear_greed': {'status': 'unknown', 'time': None, 'error': None},
    'fred': {'status': 'unknown', 'time': None, 'error': None},
    'alpha_vantage': {'status': 'unknown', 'time': None, 'error': None},
    'btc_correlation': {'status': 'unknown', 'time': None, 'error': None},
    'onchain': {'status': 'unknown', 'time': None, 'error': None},   # CryptoQuant
    'indicators': {'status': 'unknown', 'time': None, 'error': None},
    'scoring': {'status': 'unknown', 'time': None, 'error': None},
    'dynamic_targets': {'status': 'unknown', 'time': None, 'error': None},
}

logger.info(f"\n{'=' * 80}")
logger.info(f"  🏆  ELITE SWING TRADER DASHBOARD")
logger.info(f"{'=' * 80}")
logger.info(f"  📊  Data Sources: 10 (per User Manual)")
logger.info(f"  🤖  Quant-First: Score + Rule Veto")
logger.info(f"  🎯  Regime Detection: Active")
logger.info(f"  💰  Position Sizing: Risk-Based")
logger.info(f"  🎯  Dynamic Targets: Volatility-Adjusted")
logger.info(f"  📈  Backtesting: Enabled")
logger.info(f"{'=' * 80}")
logger.info(f"  Account Size: ${position_sizer.account_size:,.2f}")
logger.info(f"  Max Risk: {position_sizer.max_risk_pct}% per trade")
logger.info(f"{'=' * 80}")
logger.info("  Swing Trader v1.0 — Sizing + regression + walk-forward active")
logger.info(f"{'=' * 80}\n")


# =============================================================================
# Helper Functions
# =============================================================================

def mark_source(name, status, error=None):
    """Mark data source as ok/error/warning"""
    global data_source_status
    data_source_status[name] = {
        'status': status,  # 'ok', 'error', 'warning'
        'time': datetime.now().strftime('%H:%M:%S'),
        'error': str(error)[:100] if error else None  # Truncate long errors
    }


def summarize_veto_log_30d(max_tail_bytes=524288):
    """Last-30d veto counts; tail-reads large veto_log.jsonl to avoid loading the entire file."""
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    veto_log = _Path(__file__).parent / 'data' / 'veto_log.jsonl'
    cutoff = (_dt.now(_tz.utc) - _td(days=30)).isoformat()
    veto_entries = []
    approximate = False
    if not veto_log.exists():
        return {'total_vetoes': 0, 'by_regime': {}}
    try:
        sz = veto_log.stat().st_size
        if sz <= max_tail_bytes:
            lines = veto_log.read_text(encoding='utf-8').strip().splitlines()
        else:
            approximate = True  # tail scan may miss older lines still within 30d if log is huge
            with open(veto_log, 'rb') as f:
                f.seek(-max_tail_bytes, os.SEEK_END)
                chunk = f.read().decode('utf-8', errors='replace')
            lines = chunk.splitlines()
            if lines:
                lines = lines[1:]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                if entry.get('ts', '') >= cutoff:
                    veto_entries.append(entry)
            except _json.JSONDecodeError:
                continue
    except Exception:
        return {'total_vetoes': 0, 'by_regime': {}}
    by_regime = {}
    for e in veto_entries:
        r = e.get('regime', 'UNKNOWN')
        by_regime[r] = by_regime.get(r, 0) + 1
    out = {'total_vetoes': len(veto_entries), 'by_regime': by_regime}
    if approximate:
        out['approximate_tail_scan'] = True
    return out


def _get_dynamic_stop_target_for_coin(coin_id: str, symbol: str, entry_price: float):
    """
    Compute dynamic stop/target for a coin using cached dashboard data (volatility, regime, consensus).
    Returns dict with stop_loss_pct, take_profit_pct or None if coin not in cache / calculation fails.
    Used so all position opens use dynamic stops when data is available instead of static %.
    """
    if not entry_price or entry_price <= 0:
        return None
    with _cache_lock:
        coins = (cached_data or {}).get('coins') or []
        regime_data = (cached_data or {}).get('regime') or {}
    regime = regime_data.get('regime', 'SIDEWAYS')
    coin = None
    for c in coins:
        cid = (c.get('id') or '').lower()
        sym = (c.get('symbol') or '').strip().upper()
        if cid == (coin_id or '').lower() or sym == (symbol or '').strip().upper():
            coin = c
            break
    if not coin:
        return None
    rec = coin.get('ai_recommendation') or {}
    consensus_signal = (rec.get('consensus') or {}).get('consensus_signal', 'MODERATE_BUY')
    if consensus_signal not in ('ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY', 'WEAK_BUY'):
        consensus_signal = 'MODERATE_BUY'
    volatility_pct = float((coin.get('indicators') or {}).get('volatility', 50) or 50)
    try:
        targets = target_calculator.calculate_targets(
            entry_price=entry_price,
            volatility_pct=volatility_pct,
            regime=regime,
            consensus_signal=consensus_signal,
        )
        return {
            'stop_loss_pct': targets['stop_loss_pct'],
            'take_profit_pct': targets['take_profit_pct'],
        }
    except Exception as e:
        logger.warning(f"  ⚠ Dynamic targets for {symbol or coin_id}: {e}")
        return None


def _apply_dynamic_targets_to_coin(coin, regime_data, consensus_signal, target_calc):
    """
    Set dynamic stop/take-profit targets on a coin. Single place for all target logic.
    On error, applies conservative defaults and still sets coin['dynamic_targets'] so
    downstream knows targets were applied. Returns True if calculation succeeded.
    """
    try:
        targets = target_calc.calculate_targets(
            entry_price=coin.get('current_price', 0),
            volatility_pct=coin.get('indicators', {}).get('volatility', 50),
            regime=regime_data.get('regime', 'SIDEWAYS'),
            consensus_signal=consensus_signal
        )
        coin['dynamic_targets'] = targets
        coin['stop_loss_pct'] = targets['stop_loss_pct']
        coin['take_profit_pct'] = targets['take_profit_pct']
        coin['stop_loss_price'] = targets['stop_loss_price']
        coin['take_profit_price'] = targets['take_profit_price']
        coin['risk_reward_ratio'] = targets['risk_reward_ratio']
        if 'risk' not in coin:
            coin['risk'] = {}
        coin['risk']['stop_loss_pct'] = targets['stop_loss_pct']
        coin['risk']['take_profit_pct'] = targets['take_profit_pct']
        return True
    except Exception as e:
        logger.warning(f"    ⚠ Error calculating dynamic targets for {coin.get('name')}: {e}")
        coin['stop_loss_pct'] = 2.5
        coin['take_profit_pct'] = 5.0
        entry = coin.get('current_price') or 0
        coin['dynamic_targets'] = {
            'stop_loss_pct': 2.5,
            'take_profit_pct': 5.0,
            'stop_loss_price': entry * 0.975 if entry else 0,
            'take_profit_price': entry * 1.05 if entry else 0,
            'risk_reward_ratio': 2.0,
        }
        if 'risk' not in coin:
            coin['risk'] = {}
        coin['risk']['stop_loss_pct'] = 2.5
        coin['risk']['take_profit_pct'] = 5.0
        return False




def apply_full_veto_logic(coin, regime_data, consensus_result, fear_greed_value, position_sizer=None, cash_available=None):
    """
    Quant-first veto pipeline: quant tier IS the signal, rule-based veto gates.
    No LLM override — quant decides, rules protect.
    Mutates consensus_result with final_signal, quant_signal, quant_tier, veto_reason, reasoning.
    Returns (final_signal, quant) for caller use.
    Pass position_sizer and cash_available so quant can attach position_sizing.
    """
    quant = generate_quant_signal(coin, regime_data, fear_greed_value, position_sizer=position_sizer, cash_available=cash_available)
    # Quant tier is the signal — no LLM gate
    final_signal = quant['quant_tier'] if quant['quant_signal'] == 'BUY' else 'HOLD'
    consensus_result['consensus_signal'] = final_signal
    consensus_result['quant_signal'] = quant['quant_signal']
    consensus_result['quant_tier'] = quant['quant_tier']
    consensus_result['veto_reason'] = None
    # Rule-based veto only (no LLM); runs for all BUY tiers in all modes.
    # Keep behavior parity between test mode and production decisioning.
    if final_signal in ('ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY'):
        try:
            veto_result = veto_specialist_evaluate(
                coin, regime_data, consensus_result,
                enhanced=coin.get('enhanced'),
                fear_greed_value=fear_greed_value,
            )
            apply_veto_override(consensus_result, veto_result, regime_data)
        except Exception as e:
            if coin:
                logger.warning(f"  ⚠ Veto specialist error for {coin.get('name', '')}: {e}")
    final_signal = consensus_result.get('consensus_signal', final_signal)
    consensus_result['consensus_quality'] = (
        'ultra' if quant['quant_confidence'] >= 85 else 'strong'
    ) if final_signal in ['ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY'] else 'hold'
    return final_signal, quant


# Gate-watch dedup: lean-pruned signals logged once per coin per UTC day (see refresh loop)
_lean_prune_logged_day: dict = {}


def _classify_live_auto_exec_skip(coin, portfolio_summary=None, account_size=None, regime_data=None):
    """Return live_skip_reason string if TWAP would not start, else None."""
    rec = coin.get('ai_recommendation') or {}
    pos = rec.get('position_sizing') or {}
    symbol = (coin.get('symbol') or '').strip().upper()
    fs = (rec.get('final_signal') or '').strip().upper()
    if fs not in ('ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY'):
        return None
    existing = [p for p in get_open_positions(exclude_test=True)
                if (p.get('coin_symbol') or '').strip().upper() == symbol]
    if existing:
        return 'already_open_live'
    quant_tier = (rec.get('quant_tier') or (rec.get('quant') or {}).get('quant_tier') or
                  (rec.get('consensus') or {}).get('quant_tier') or '').strip().upper()
    if quant_tier not in ('STRONG_BUY', 'ULTRA_BUY'):
        return 'policy_no_auto_exec'
    _quant_path = ((rec.get('quant') or {}).get('quant_path') or '').strip().lower()
    if _quant_path in ('rally',):
        return 'quant_path_blocked'
    position_usd = pos.get('position_size_usd')
    if position_usd is None or float(position_usd or 0) <= 0:
        return 'no_position_size'
    if float(position_usd) < 10.0:
        return 'below_exchange_minimum'
    sym_upper = (symbol or '').strip().upper()
    if sym_upper and _reconcile_cache.get('data'):
        for disc in (_reconcile_cache['data'].get('discrepancies') or []):
            if disc.get('type') == 'external' and (disc.get('symbol') or '').strip().upper() == sym_upper:
                return 'unjournaled_exchange'
    if portfolio_summary is not None and account_size is not None:
        exec_cfg = get_execution_config()
        if not exec_cfg.get('auto_execution_enabled', False):
            return 'auto_execution_disabled'
        if get_test_mode():
            return 'test_mode_on'
        overview = _build_account_overview()
        cash_available = overview.get('cash_available_to_trade', 0) or 0
        if float(position_usd) > cash_available:
            return 'insufficient_cash'
        new_risk_pct = float(pos.get('max_loss_pct', 0) or 0)
        ok, reason = execution_safety_ok(portfolio_summary, new_risk_pct, account_size)
        if not ok:
            return reason or 'execution_safety'
    return None


def _log_shadow_skip(coin, live_skip_reason, regime_data=None):
    """Append one shadow-skip row for optimizer / ML policy-skip analysis."""
    if not live_skip_reason:
        return
    try:
        from paper_evidence import append_shadow_skip
        rec = coin.get('ai_recommendation') or {}
        quant = rec.get('quant') or {}
        ind = coin.get('indicators') or {}
        entry_px = coin.get('current_price')
        append_shadow_skip({
            'coin_id': coin.get('id', ''),
            'symbol': (coin.get('symbol') or '').upper(),
            'entry_price': float(entry_px) if entry_px else None,
            'final_signal': rec.get('final_signal'),
            'quant_tier': quant.get('quant_tier') or rec.get('quant_tier'),
            'quant_path': quant.get('quant_path'),
            'market_regime': (regime_data or {}).get('regime') or rec.get('market_regime'),
            'live_skip_reason': live_skip_reason,
            'score': quant.get('score'),
            'ch24h': ind.get('ch24h'),
            'paper_would_fill': get_paper_trading() and live_skip_reason in (
                'policy_no_auto_exec', 'already_open_live', 'below_exchange_minimum',
            ),
        })
    except Exception as e:
        logger.debug("shadow skip log failed: %s", e)


def _start_twap_if_safe(coin, portfolio_summary, account_size, regime_data=None):
    """
    Start TWAP only if execution_safety_ok passes and cash is available. Single path for all TWAP execution.
    Returns True if TWAP was started, False if skipped (safety or missing data).
    regime_data: optional dict with 'regime' for tagging the journal position (quant gate).
    """
    rec = coin.get('ai_recommendation') or {}
    pos = rec.get('position_sizing') or {}
    symbol = coin.get('symbol', '')
    existing = [p for p in get_open_positions(exclude_test=True) if (p.get('coin_symbol') or '').strip().upper() == (symbol or '').strip().upper()]
    if existing:
        logger.info(f"  ⏭ Auto-exec skip {symbol}: already have {len(existing)} open position(s)")
        return False
    # Defense-in-depth: block if exchange already holds this symbol without a journal position.
    # Catches unjournaled fills from prior callback failures (prevents buying the same coin again).
    sym_upper = (symbol or '').strip().upper()
    if sym_upper and _reconcile_cache.get('data'):
        for disc in (_reconcile_cache['data'].get('discrepancies') or []):
            if disc.get('type') == 'external' and (disc.get('symbol') or '').strip().upper() == sym_upper:
                logger.warning(
                    f"  ⏭ Auto-exec skip {symbol}: exchange holds unjournaled {symbol} "
                    f"(qty={disc.get('exchange_quantity')}) — use 'Add from Coinbase' to import"
                )
                try:
                    if _alerts_ready():
                        alerts.send_critical_alert(
                            f"UNJOURNALED HOLDING BLOCKED AUTO-EXEC – {symbol}",
                            f"Exchange holds {disc.get('exchange_quantity')} {symbol} without a journal position. "
                            f"Auto-exec blocked to prevent duplicate buys. Use 'Add from Coinbase' to import.",
                            alert_type='unjournaled_holding_block',
                        )
                except Exception:
                    pass
                return False
    # Only auto-execute high-conviction signals (STRONG_BUY, ULTRA_BUY).
    # MODERATE_BUY (58-71 score, 26% WR historically) still shows in dashboard for manual review.
    ALLOWED_AUTO_TIERS = {'STRONG_BUY', 'ULTRA_BUY'}
    quant_tier = (rec.get('quant_tier') or (rec.get('quant') or {}).get('quant_tier') or
                  (rec.get('consensus') or {}).get('quant_tier') or rec.get('signal_tier') or rec.get('tier') or '').strip().upper()
    if quant_tier not in ALLOWED_AUTO_TIERS:
        logger.info(f"  ⏭ Auto-exec skip {symbol}: tier {quant_tier or 'UNKNOWN'} not in {ALLOWED_AUTO_TIERS}")
        return False
    # Block paths with statistically negative expectancy (below PATH_KILL threshold but empirically clear).
    _quant_dict = rec.get('quant') or {}  # quant lives under ai_recommendation['quant'], not top-level coin
    _quant_path = (_quant_dict.get('quant_path') or '').strip().lower()
    _BLOCKED_PATHS = {'rally'}  # 0/3 live wins, -8.54% combined — disable until PATH_KILL_MIN_TRADES met
    if _quant_path in _BLOCKED_PATHS:
        logger.info(f"  ⏭ Auto-exec skip {symbol}: quant_path='{_quant_path}' blocked (0% WR empirically)")
        return False
    position_usd = pos.get('position_size_usd')
    if position_usd is None or float(position_usd) <= 0:
        logger.info(f"  ⏭ Auto-exec skip {symbol}: no position size (position_size_usd={position_usd})")
        return False
    position_usd = float(position_usd)
    overview = _build_account_overview()
    cash_available = overview.get('cash_available_to_trade', 0) or 0
    if position_usd > cash_available:
        # Distinguish TWAP-blocked skips from genuine low-cash for measurement
        twap_orders = get_active_twap_orders()
        twap_usd = sum(float(o.get('total_usd', 0) or 0) for o in twap_orders if not o.get('is_test'))
        if twap_usd > 0 and (cash_available + twap_usd) >= position_usd:
            logger.warning(f"  ⏭ TWAP-BLOCKED skip {coin.get('symbol', '')}: cash ${cash_available:,.0f} (${twap_usd:,.0f} reserved by active TWAP), trade ${position_usd:,.0f}")
        else:
            logger.info(f"  ⏭ Auto-exec skip {coin.get('symbol', '')}: insufficient cash (available ${cash_available:,.0f}, trade ${position_usd:,.0f}) — alerting and logging")
        try:
            consensus_signal = (rec.get('consensus') or {}).get('consensus_signal', '')
            log_skipped_insufficient_cash(
                coin_id=coin.get('id', ''),
                coin_name=coin.get('name', ''),
                symbol=coin.get('symbol', ''),
                position_usd=position_usd,
                cash_available=cash_available,
                consensus_signal=consensus_signal,
            )
            logger.info(f"  ⚠ Skipped {coin.get('symbol', '?')}: insufficient cash")
            # Avoid startup spam: no alert when cash not loaded (0) or during grace period; at most one per refresh
            if cash_available > 0 and _alerts_ready():
                global _insufficient_cash_alert_refresh, _current_refresh_id
                _should_send_insufficient_cash = False
                with _cache_lock:
                    if _insufficient_cash_alert_refresh != _current_refresh_id:
                        _insufficient_cash_alert_refresh = _current_refresh_id
                        _should_send_insufficient_cash = True
                if _should_send_insufficient_cash:
                    alerts.send_insufficient_cash_alert(
                        coin_symbol=coin.get('symbol', ''),
                        position_usd=position_usd,
                        cash_available=cash_available,
                    )
        except Exception as e:
            logger.warning(f"  ⚠ Alert/log send failed: {e}")
        return False
    new_risk_pct = float(pos.get('max_loss_pct', 0) or 0)
    ok, reason = execution_safety_ok(portfolio_summary, new_risk_pct, account_size)
    if not ok:
        logger.info(f"  ⏭ Auto-exec skip {coin.get('symbol', '')}: {reason}")
        return False
    # Hurdle rate gate: trade must beat HELOC cost of capital
    heloc_cfg = load_account_config()
    if heloc_cfg.get('heloc_mode') and heloc_cfg.get('hurdle_mode') == 'per_trade':
        rr_ratio = float(pos.get('rr_ratio', 0) or 0)
        stop_loss_pct = float(pos.get('max_loss_pct', 0) or 0)
        expected_return_pct = stop_loss_pct * rr_ratio if rr_ratio > 0 else 0
        metrics = _compute_heloc_metrics(heloc_cfg)
        hurdle = metrics['hurdle_per_trade_pct']
        if hurdle > 0 and expected_return_pct < hurdle:
            logger.info(f"  ⏭ Auto-exec skip {symbol}: expected return {expected_return_pct:.3f}% < HELOC hurdle {hurdle:.3f}% (avg hold {metrics['avg_hold_days']:.1f}d)")
            return False
    cfg = get_execution_config()
    dt = coin.get('dynamic_targets') or {}
    risk = coin.get('risk') or {}
    stop_pct = float(dt.get('stop_loss_pct', 0) or 0) or float(risk.get('stop_loss_pct', 0) or 0)
    target_pct = float(dt.get('take_profit_pct', 0) or 0) or float(risk.get('take_profit_pct', 0) or 0)
    if not stop_pct or not target_pct:
        entry_for_dyn = float(coin.get('current_price', 0) or 0)
        if entry_for_dyn > 0:
            dyn = _get_dynamic_stop_target_for_coin(coin.get('id', ''), coin.get('symbol', ''), entry_for_dyn)
            if dyn:
                stop_pct = stop_pct or dyn['stop_loss_pct']
                target_pct = target_pct or dyn['take_profit_pct']
    stop_pct = stop_pct or cfg.get('default_stop_pct', 4.0)
    target_pct = target_pct or cfg.get('default_target_pct', 6.0)
    quant = rec.get('quant') or {}
    consensus_rec = rec.get('consensus') or {}
    regime = (regime_data or {}).get('regime') or consensus_rec.get('market_regime') or ''
    hb_floor = 4.5
    hb_atr = 2.5
    hb_7d = 20.0
    indicators = coin.get('indicators') or {}
    atr_pct = float(indicators.get('atr_pct', 0) or 0)
    ch7d = float(indicators.get('ch7d', 0) or 0)

    # Adaptive floor for high-beta trend names when scorer-provided stop is too tight.
    try:
        from thresholds import get_threshold
        hb_floor = float(get_threshold('DYNAMIC_STOP_HIGH_BETA_FLOOR_PCT', 4.5))
        hb_atr = float(get_threshold('DYNAMIC_STOP_HIGH_BETA_ATR_THRESHOLD_PCT', 2.5))
        hb_7d = float(get_threshold('DYNAMIC_STOP_HIGH_BETA_MOM_7D_THRESHOLD_PCT', 20.0))
        if regime in ('BULL', 'SIDEWAYS') and (atr_pct >= hb_atr or ch7d >= hb_7d):
            if stop_pct < hb_floor:
                logger.info(
                    f"  📈 High-beta stop floor applied for {symbol}: "
                    f"{stop_pct:.2f}% -> {hb_floor:.2f}% (atr={atr_pct:.2f}, 7d={ch7d:.2f}%)"
                )
            stop_pct = max(stop_pct, hb_floor)
    except Exception:
        pass
    # Execution context for journal position (regime + quant scenario + signal attribution)
    quant_tier = consensus_rec.get('quant_tier') or quant.get('quant_tier') or ''
    quant_path = quant.get('quant_path') or 'default'
    badges = quant.get('signal_badges') or []
    sniper = 'light_sniper_entry' in badges or 'intra_swing_sniper' in badges
    trading_scenario = 'sniper' if sniper else quant_path
    veto_reason = consensus_rec.get('veto_reason')
    execution_context = {
        'market_regime': regime,
        'quant_tier': quant_tier,
        'quant_path': quant_path,
        'trading_scenario': trading_scenario,
        'specialist_veto': 'VETO' if veto_reason else 'CONFIRM',
        'confirmation_score': quant.get('confirmation_score'),
        # Stage 1b: persist BTC risk-on/off at entry for the promotion gate-integrity audit.
        'risk_regime': quant.get('risk_regime') or ((regime_data or {}).get('characteristics') or {}).get('risk_regime'),
        # Persist attribution fields at entry-time so optimizer/counterfactual
        # analysis does not depend on later cache lookups.
        # fear_greed lives inside regime_data['characteristics'], not top-level.
        'fear_greed': ((regime_data or {}).get('characteristics') or {}).get('fear_greed'),
        'funding_rate': ((coin.get('enhanced') or {}).get('funding_rate') or {}).get('current')
            if isinstance((coin.get('enhanced') or {}).get('funding_rate'), dict)
            else (coin.get('enhanced') or {}).get('funding_rate'),
        'price_change_24h': (coin.get('indicators') or {}).get('ch24h'),
        'price_change_7d': (coin.get('indicators') or {}).get('ch7d'),
        'price_change_30d': (coin.get('indicators') or {}).get('ch30d'),
        'edge_score': quant.get('edge_score'),
        'historical_win_prob': quant.get('historical_win_prob'),
        'ath_change_pct': quant.get('ath_change_pct'),
        'fdv_ratio': quant.get('fdv_ratio'),
        'signal_badges': badges,
        # Dynamic stop policy attribution (for optimizer/roadmap analysis)
        'stop_policy_profile': 'dynamic_stop_v1',
        'stop_policy_version': 1,
        'dynamic_stop_eligible': bool(regime in ('BULL', 'SIDEWAYS') and (atr_pct >= hb_atr or ch7d >= hb_7d)),
        'dynamic_stop_floor_pct': hb_floor,
        'dynamic_stop_floor_applied': bool(stop_pct >= hb_floor and regime in ('BULL', 'SIDEWAYS') and (atr_pct >= hb_atr or ch7d >= hb_7d)),
    }
    # Enforce TWAP only for Tier 1-2 quant setups (ULTRA/STRONG). Lower tiers may still
    # execute via non-TWAP path when below twap_min_usd inside the execution engine.
    twap_min_usd = float(cfg.get('twap_min_usd', 500) or 500)
    if float(position_usd) >= twap_min_usd and quant_tier not in ('ULTRA_BUY', 'STRONG_BUY'):
        logger.info(
            f"  ⏭ Auto-exec skip {symbol}: TWAP restricted to Tier1-2 (tier={quant_tier or 'UNKNOWN'}, size=${float(position_usd):.0f})"
        )
        return False
    # regression_features for weekly logistic regression training
    try:
        from quant_signal import _order_book_ratio, _mtf_aligned
        s = (coin.get('symbol') or '').strip().upper()
        c = (coin.get('id') or '').strip().lower()
        coin_class = 'btc' if (s in ('BTC', 'BITCOIN') or 'bitcoin' in c) else 'eth' if (s in ('ETH', 'ETHEREUM') or 'ethereum' in c) else 'mid'
        ind = coin.get('indicators') or {}
        execution_context['regression_features'] = {
            'base_score': quant.get('score'),
            'ob_ratio': _order_book_ratio(coin),
            'mtf_aligned': _mtf_aligned(coin),
            'atr_pct': float(ind.get('atr_pct', 2.0) or 2.0),
            'fear_greed': ((regime_data or {}).get('characteristics') or {}).get('fear_greed') or 50,
            'regime': regime,
            'quant_path': quant_path,
            'coin_class': coin_class,
        }
    except Exception:
        pass
    # --- Stale price guard: force-refresh price if cache is >2 min old before sizing ---
    try:
        coin_id_for_price = coin.get('id', '')
        if coin_id_for_price and hasattr(fetcher, 'get_current_prices_fast'):
            fresh_prices = fetcher.get_current_prices_fast([coin_id_for_price])
            fresh_price = fresh_prices.get(coin_id_for_price)
            cached_price = coin.get('current_price', 0) or 0
            if fresh_price and cached_price and fresh_price > 0:
                drift_pct = abs(fresh_price - cached_price) / cached_price * 100
                if drift_pct > 2.0:
                    logger.warning(f"  ⚠ STALE PRICE GUARD: {coin.get('symbol', '')} cached ${cached_price:.2f} → live ${fresh_price:.2f} ({drift_pct:.1f}% drift) — using live price")
                    coin['current_price'] = fresh_price
                    # Recalculate position size with fresh price
                    position_calc = position_sizer.calculate_for_consensus(
                        entry_price=fresh_price,
                        stop_loss_pct=stop_pct,
                        consensus_signal=(rec.get('consensus') or {}).get('consensus_signal', 'MODERATE_BUY'),
                        regime_data=regime_data or {},
                        coin_volatility=coin.get('indicators', {}).get('volatility', 50),
                        cash_available=cash_available,
                    )
                    if position_calc:
                        new_usd = float(position_calc.get('position_size_usd', 0) or 0)
                        if new_usd > 0:
                            position_usd = new_usd
                            logger.info(f"  📐 Position resized to ${position_usd:.0f} after stale price correction")
    except Exception as e:
        logger.debug(f"  Stale price guard error (non-fatal): {e}")

    # Re-check cash after stale-price resize, because position_usd may have increased materially.
    overview_after_resize = _build_account_overview()
    cash_available_after_resize = overview_after_resize.get('cash_available_to_trade', 0) or 0
    if position_usd > cash_available_after_resize:
        logger.info(
            f"  ⏭ Auto-exec skip {coin.get('symbol', '')}: insufficient cash after resize "
            f"(available ${cash_available_after_resize:,.0f}, trade ${position_usd:,.0f})"
        )
        return False

    started = start_twap_buy_async(
        symbol=coin.get('symbol', ''),
        total_usd=position_usd,
        num_slices=5,
        duration_minutes=45,
        coin_name=coin.get('name', ''),
        coin_id=coin.get('id', ''),
        stop_pct=stop_pct,
        target_pct=target_pct,
        twap_min_usd=twap_min_usd,
        is_test=get_test_mode(),
        execution_context=execution_context,
    )
    if started:
        mode = "market buy" if position_usd < cfg.get('twap_min_usd', 500) else "TWAP"
        logger.info(f"  🚀 Auto-exec started {mode} for {coin.get('symbol', '')} (${float(position_usd):.0f}, SL={stop_pct:.1f}%, TP={target_pct:.1f}%)")
        return True
    logger.info(f"  ⏭ Auto-exec skip {coin.get('symbol', '')}: execution engine did not start order")
    return False


def _paper_symbol_already_open(symbol: str) -> bool:
    """Check if a paper/test position is already open for this symbol.
    Only checks paper/test positions -- live positions do not block paper opens."""
    sym = (symbol or '').strip().upper()
    if not sym:
        return True
    for p in get_open_positions(exclude_test=False):
        if not p.get('is_test'):
            continue
        if (p.get('coin_symbol') or '').strip().upper() == sym:
            return True
    return False


def _simulate_paper_fill(coin, regime_data=None):
    """
    Simulated journal entry for paper_trading: ULTRA/STRONG/MODERATE, fixed USD notional, no exchange.
    Returns True if a position was opened.
    """
    rec = coin.get('ai_recommendation') or {}
    fs = (rec.get('final_signal') or '').strip().upper()
    if fs not in ('ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY'):
        return False
    symbol = (coin.get('symbol') or '').strip().upper()
    if _paper_symbol_already_open(symbol):
        logger.info(f"  ⏭ Paper skip {symbol}: already have open position for symbol")
        return False

    cfg = get_execution_config()
    dt = coin.get('dynamic_targets') or {}
    risk = coin.get('risk') or {}
    stop_pct = float(dt.get('stop_loss_pct', 0) or 0) or float(risk.get('stop_loss_pct', 0) or 0)
    target_pct = float(dt.get('take_profit_pct', 0) or 0) or float(risk.get('take_profit_pct', 0) or 0)
    if not stop_pct or not target_pct:
        entry_for_dyn = float(coin.get('current_price', 0) or 0)
        if entry_for_dyn > 0:
            dyn = _get_dynamic_stop_target_for_coin(coin.get('id', ''), coin.get('symbol', ''), entry_for_dyn)
            if dyn:
                stop_pct = stop_pct or dyn['stop_loss_pct']
                target_pct = target_pct or dyn['take_profit_pct']
    stop_pct = stop_pct or cfg.get('default_stop_pct', 4.0)
    target_pct = target_pct or cfg.get('default_target_pct', 6.0)

    quant = rec.get('quant') or {}
    consensus_rec = rec.get('consensus') or {}
    regime = (regime_data or {}).get('regime') or consensus_rec.get('market_regime') or ''
    indicators = coin.get('indicators') or {}
    atr_pct = float(indicators.get('atr_pct', 0) or 0)
    ch7d = float(indicators.get('ch7d', 0) or 0)
    try:
        from thresholds import get_threshold
        hb_floor = float(get_threshold('DYNAMIC_STOP_HIGH_BETA_FLOOR_PCT', 4.5))
        hb_atr = float(get_threshold('DYNAMIC_STOP_HIGH_BETA_ATR_THRESHOLD_PCT', 2.5))
        hb_7d = float(get_threshold('DYNAMIC_STOP_HIGH_BETA_MOM_7D_THRESHOLD_PCT', 20.0))
        if regime in ('BULL', 'SIDEWAYS') and (atr_pct >= hb_atr or ch7d >= hb_7d):
            stop_pct = max(stop_pct, hb_floor)
    except Exception:
        hb_floor = 4.5
        hb_atr = 2.5
        hb_7d = 20.0

    quant_tier = consensus_rec.get('quant_tier') or quant.get('quant_tier') or ''
    quant_path = quant.get('quant_path') or 'default'
    badges = quant.get('signal_badges') or []
    sniper = 'light_sniper_entry' in badges or 'intra_swing_sniper' in badges
    trading_scenario = 'sniper' if sniper else quant_path
    veto_reason = consensus_rec.get('veto_reason')

    raw_price = float(coin.get('current_price', 0) or 0)
    if raw_price <= 0:
        logger.info(f"  ⏭ Paper skip {symbol}: no current price")
        return False
    entry_price = raw_price * (1 + PAPER_TRADE_BUY_SLIPPAGE_PCT / 100.0)
    notional = float(get_paper_trade_notional_usd())
    quantity = round(notional / entry_price, 8)
    if quantity <= 0:
        return False
    entry_fee_usd = round(notional * (PAPER_TRADE_FEE_PCT / 100.0), 6)

    execution_extra = {
        'market_regime': regime,
        'quant_tier': quant_tier,
        'quant_path': quant_path,
        'trading_scenario': trading_scenario,
        'specialist_veto': 'VETO' if veto_reason else 'CONFIRM',
        'confirmation_score': quant.get('confirmation_score'),
        # Stage 1b: persist BTC risk-on/off at entry for the promotion gate-integrity audit.
        'risk_regime': quant.get('risk_regime') or ((regime_data or {}).get('characteristics') or {}).get('risk_regime'),
        'fear_greed': ((regime_data or {}).get('characteristics') or {}).get('fear_greed'),
        'funding_rate': ((coin.get('enhanced') or {}).get('funding_rate') or {}).get('current')
            if isinstance((coin.get('enhanced') or {}).get('funding_rate'), dict)
            else (coin.get('enhanced') or {}).get('funding_rate'),
        'price_change_24h': indicators.get('ch24h'),
        'price_change_7d': indicators.get('ch7d'),
        'price_change_30d': indicators.get('ch30d'),
        'edge_score': quant.get('edge_score'),
        'ath_change_pct': quant.get('ath_change_pct'),
        'fdv_ratio': quant.get('fdv_ratio'),
        'stop_policy_profile': 'dynamic_stop_v1',
        'stop_policy_version': 1,
        'dynamic_stop_eligible': bool(regime in ('BULL', 'SIDEWAYS') and (atr_pct >= hb_atr or ch7d >= hb_7d)),
        'dynamic_stop_floor_pct': hb_floor,
        'dynamic_stop_floor_applied': bool(stop_pct >= hb_floor and regime in ('BULL', 'SIDEWAYS') and (atr_pct >= hb_atr or ch7d >= hb_7d)),
    }
    try:
        from quant_signal import _order_book_ratio, _mtf_aligned
        s = (coin.get('symbol') or '').strip().upper()
        c = (coin.get('id') or '').strip().lower()
        coin_class = 'btc' if (s in ('BTC', 'BITCOIN') or 'bitcoin' in c) else 'eth' if (s in ('ETH', 'ETHEREUM') or 'ethereum' in c) else 'mid'
        ind = coin.get('indicators') or {}
        execution_extra['regression_features'] = {
            'base_score': quant.get('score'),
            'ob_ratio': _order_book_ratio(coin),
            'mtf_aligned': _mtf_aligned(coin),
            'atr_pct': float(ind.get('atr_pct', 2.0) or 2.0),
            'fear_greed': ((regime_data or {}).get('characteristics') or {}).get('fear_greed') or 50,
            'regime': regime,
            'quant_path': quant_path,
            'coin_class': coin_class,
        }
    except Exception:
        pass

    pdata = {
        'coin_id': coin.get('id', ''),
        'coin_name': coin.get('name', ''),
        'coin_symbol': symbol,
        'signal': 'BUY',
        'entry_price': entry_price,
        'quantity': quantity,
        'ai_target_pct': float(target_pct),
        'ai_stop_pct': float(stop_pct),
        'is_test': True,
        'source': 'paper_trade',
        'capital_usd': round(notional, 2),
        'entry_fee_usd': entry_fee_usd,
        'notes': f'paper_trade simulation | final_signal={fs}',
        'ai_hypothesis': rec.get('ai_hypothesis', ''),
        'ai_confidence': rec.get('ai_confidence', ''),
        'live_skip_reason': 'policy_no_auto_exec' if fs == 'MODERATE_BUY' else 'paper_parallel',
    }
    pdata.update(execution_extra)

    pos = open_position(pdata)
    if pos:
        logger.info(
            f"  📄 Paper position opened #{pos.get('id')} {symbol} @ ${entry_price:.8f} "
            f"(${notional:.0f} notional, SL={stop_pct:.1f}%, TP={target_pct:.1f}%)"
        )
        return True
    return False


def _close_paper_position_simulated(position, market_price: float, exit_reason: str) -> bool:
    """Close paper trade at market with sell slippage + exit fee; no exchange."""
    if not is_paper_trade(position):
        return False
    exit_px = float(market_price) * (1 - PAPER_TRADE_SELL_SLIPPAGE_PCT / 100.0)
    qty = float(position.get('quantity', 0) or 0)
    exit_fee = round(qty * exit_px * (PAPER_TRADE_FEE_PCT / 100.0), 6)
    _, err = close_position(
        position.get('id'),
        exit_price=exit_px,
        exit_reason=exit_reason,
        notes='paper_trade simulated exit',
        exit_fee_usd=exit_fee,
    )
    if err:
        logger.warning(f"  Paper close error #{position.get('id')}: {err}")
        return False
    logger.info(f"  📄 Paper position closed #{position.get('id')} {position.get('coin_symbol')} reason={exit_reason}")
    return True


def _check_paper_position_exits(current_prices: dict) -> None:
    """Stop/target/trailing for paper positions only (simulated closes).

    Under LEAN the paper cohort must run the validated lean exit profile — wide fixed
    stop, NO trailing, time exit at LEAN_MAX_HOLD_DAYS — otherwise the cohort measures
    a different strategy than the one registered (the drift class that burned June 1-2).
    The real-money stale path (exit_manager.check_stale_positions) skips is_test, so
    the paper time exit lives here."""
    if not current_prices:
        return
    paper_open = [p for p in get_open_positions(exclude_test=False) if is_paper_trade(p)]
    if not paper_open:
        return
    from thresholds import get_threshold as _gt
    _lean = float(_gt('LEAN_ENTRY_MODE', 0.0) or 0.0) >= 1.0
    _lean_max_hold = float(_gt('LEAN_MAX_HOLD_DAYS', 30))
    _now_utc = datetime.now(timezone.utc)
    for position in paper_open:
        # Lean time exit (validated pillar): close at market once the hold expires.
        if _lean:
            _ets = (position.get('entry_time') or '')[:19]
            try:
                _entry_dt = datetime.strptime(_ets, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                _held_d = (_now_utc - _entry_dt).total_seconds() / 86400
            except (ValueError, TypeError):
                _held_d = 0.0
            if _held_d >= _lean_max_hold:
                _px = current_prices.get(position.get('coin_id'))
                if _px and _px > 0:
                    _close_paper_position_simulated(position, _px, 'time_exit')
                continue
        coin_id = position.get('coin_id')
        if not coin_id:
            continue
        current_price = current_prices.get(coin_id)
        if not current_price or current_price <= 0:
            continue
        entry_price = float(position.get('entry_price', 0))
        if entry_price <= 0:
            continue
        price_ratio = current_price / entry_price
        if price_ratio > 50 or price_ratio < 0.001:
            logger.warning(
                f"  Paper price sanity reject {position.get('coin_symbol')}: "
                f"entry=${entry_price} current=${current_price} ratio={price_ratio:.1f}x — likely bad data, skipping"
            )
            continue
        has_limit_orders = position.get('sl_order_id') or position.get('tp_order_id')
        if has_limit_orders:
            continue
        stop_price = float(position.get('current_stop_price') or position.get('ai_stop_price') or 0)
        target_price = float(position.get('current_target_price') or position.get('ai_target_price') or 0)
        if stop_price <= 0:
            continue
        # Winners always run: positions have no fixed target — managed by stop-loss + trailing.
        # (Legacy positions that still carry a target_price keep the take-profit branch below.)
        if current_price <= stop_price:
            _close_paper_position_simulated(position, current_price, 'stop_loss')
        elif target_price and current_price >= target_price:
            _close_paper_position_simulated(position, current_price, 'take_profit')
        elif _lean:
            continue  # lean profile has NO trailing — stop + time exit only
        else:
            profit_pct = ((current_price - entry_price) / entry_price) * 100
            from thresholds import get_threshold
            min_profit = get_threshold('TRAILING_STOP_MIN_PROFIT_PCT', 8)
            if profit_pct < min_profit:
                continue
            high_water = float(position.get('high_water_mark') or entry_price)
            if current_price > high_water:
                high_water = current_price
                update_position(position.get('id'), {'high_water_mark': high_water})
            trail_pct = get_threshold('TRAILING_STOP_TRAIL_PCT', 13)
            trailing_stop = high_water * (1 - trail_pct / 100)
            if current_price <= trailing_stop:
                _close_paper_position_simulated(position, current_price, 'trailing_stop')


# =============================================================================
# Hybrid SL/TP + Trailing Stop (real limit orders on Coinbase)
# =============================================================================

SL_AUCTION_RETRY_BASE_SECONDS = 90
SL_AUCTION_RETRY_MAX_ATTEMPTS = 6


def _schedule_sl_auction_retry(position, sl_result, context):
    """Persist bounded retry metadata for auction-mode stop-limit deferrals."""
    now = datetime.now(timezone.utc)
    retry_count = int(position.get('sl_retry_count') or 0) + 1
    retry_after_sec = int(
        sl_result.get('retry_after_sec')
        or position.get('sl_retry_after_sec')
        or AUCTION_MODE_RETRY_AFTER_SEC
        or SL_AUCTION_RETRY_BASE_SECONDS
    )
    jitter = random.uniform(0.9, 1.1)
    backoff_seconds = max(30, int(retry_after_sec * (2 ** max(retry_count - 1, 0)) * jitter))
    next_retry_at = now + timedelta(seconds=backoff_seconds)
    retry_payload = {
        'sl_retry_count': retry_count,
        'sl_last_error': sl_result.get('error') or 'auction_mode',
        'sl_retry_next_at': next_retry_at.isoformat().replace("+00:00", "Z"),
        'sl_retry_reason': 'auction_mode',
        'sl_retry_context': context,
        'sl_retry_after_sec': retry_after_sec,
    }
    update_position(position['id'], retry_payload)
    position.update(retry_payload)
    _execution_engine_module.log_execution({
        "ts": now.isoformat().replace("+00:00", "Z"),
        "action": "sl_retry_scheduled",
        "status": "deferred",
        "reason": "auction_mode",
        "symbol": position.get('coin_symbol', ''),
        "position_id": position.get('id'),
        "attempt": retry_count,
        "next_retry_at": retry_payload['sl_retry_next_at'],
        "context": context,
    })
    logger.warning(
        f"  ⏸ Deferred SL placement for {position.get('coin_symbol', '')} "
        f"(attempt {retry_count}/{SL_AUCTION_RETRY_MAX_ATTEMPTS}) "
        f"until {retry_payload['sl_retry_next_at']}"
    )
    if retry_count >= SL_AUCTION_RETRY_MAX_ATTEMPTS:
        logger.error(
            f"  🚨 SL retry attempts exhausted for {position.get('coin_symbol', '')} "
            f"(id={position.get('id')}); still unprotected while auction mode persists"
        )
        if not position.get('sl_retry_exhausted_alerted'):
            if _alerts_ready():
                alerts.send_critical_alert(
                    f"SL Protection Exhausted – {position.get('coin_symbol', '')}",
                    (
                        f"Stop-loss placement deferred due to auction mode reached max retries "
                        f"({SL_AUCTION_RETRY_MAX_ATTEMPTS}) for position #{position.get('id')}.\n\n"
                        f"Last error: {retry_payload.get('sl_last_error')}\n"
                        f"Position remains open without exchange SL protection. Review immediately."
                    ),
                    alert_type='stop_loss',
                )
            update_position(position['id'], {'sl_retry_exhausted_alerted': True})
            position['sl_retry_exhausted_alerted'] = True


def _clear_sl_retry_metadata(position):
    payload = {
        'sl_retry_count': 0,
        'sl_last_error': None,
        'sl_retry_next_at': None,
        'sl_retry_reason': None,
        'sl_retry_context': None,
        'sl_retry_after_sec': None,
        'sl_retry_exhausted_alerted': False,
    }
    update_position(position['id'], payload)
    position.update(payload)


def _alert_sl_retry_exhausted(position):
    if position.get('sl_retry_exhausted_alerted'):
        return
    if _alerts_ready():
        alerts.send_critical_alert(
            f"SL Protection Exhausted – {position.get('coin_symbol', '')}",
            (
                f"Stop-loss placement deferred due to auction mode reached max retries "
                f"({SL_AUCTION_RETRY_MAX_ATTEMPTS}) for position #{position.get('id')}.\n\n"
                f"Last error: {position.get('sl_last_error') or 'auction_mode'}\n"
                f"Position remains open without exchange SL protection. Review immediately."
            ),
            alert_type='stop_loss',
        )
    update_position(position['id'], {'sl_retry_exhausted_alerted': True})
    position['sl_retry_exhausted_alerted'] = True


def _place_sl_tp_orders_for_position(position):
    """
    Place real Stop-Loss order on Coinbase after position open.
    SL = stop-limit sell (trigger at stop_price, execute at limit ~0.995*stop).
    TP is tracked in-app and executed by monitoring flow; avoid placing full-size
    exchange TP concurrently with full-size SL to prevent reserve conflicts.
    Updates position with sl_order_id, tp_order_id. Returns (sl_ok, tp_ok, sl_error, tp_attempted, tp_error).
    """
    cfg = get_execution_config()
    if (cfg.get('auto_execution_exchange') or 'coinbase').lower() != 'coinbase':
        return False, False, None, False, None
    symbol = (position.get('coin_symbol') or '').strip().upper()
    quantity = float(position.get('quantity', 0) or 0)
    stop_price = float(position.get('current_stop_price') or position.get('ai_stop_price') or 0)
    entry_price = float(position.get('entry_price') or 0)
    # Winners always run: never place a take-profit. Trailing stop is the only profit exit.
    target_price = 0

    if not symbol or quantity <= 0 or stop_price <= 0:
        logger.warning(f"  ⚠ Skip SL/TP: invalid position data (symbol={symbol}, qty={quantity}, stop={stop_price})")
        return False, False, None, False, None

    sl_ok = False
    tp_ok = False
    sl_error = None
    tp_attempted = False
    tp_error = None
    # Winners always run: only a stop-limit SL is placed; no TP order.
    logger.info(f"  📤 Placing SL (stop trigger) ${stop_price:.8f} for {symbol} qty={quantity} (no TP — trailing only)")
    # Use stop-limit sell for SL: triggers when price FALLS to stop_price, then sells at limit ~0.995*stop (internal)
    sl_result = place_stop_limit_sell_order(symbol, quantity, stop_price)
    if sl_result.get('success'):
        sl_ok = True
        update_position(position['id'], {'sl_order_id': sl_result.get('order_id'), 'sl_stop_price': stop_price})
        position['sl_order_id'] = sl_result.get('order_id')
        position['sl_stop_price'] = stop_price
        _clear_sl_retry_metadata(position)
        logger.info(f"  ✓ Placed SL stop at ${stop_price:.8f} (order_id={sl_result.get('order_id')})")
    else:
        sl_error = sl_result.get('error') or "Unknown error"
        if sl_result.get('reason') == 'auction_mode':
            _schedule_sl_auction_retry(position, sl_result, context='initial_or_replace')
        logger.warning(f"  ✗ SL limit failed: {sl_error}")
    logger.info("  ⏭ Winners always run: no TP limit order placed (trailing stop is the only profit exit)")
    return sl_ok, tp_ok, sl_error, tp_attempted, tp_error


def _place_sl_tp_with_retry(position):
    """3-attempt retry for SL/TP placement."""
    for attempt in range(3):
        try:
            return _place_sl_tp_orders_for_position(position)
        except Exception as e:
            logger.warning(f"  SL/TP attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep((2 ** attempt) * 3)
    return False, False, "Max retries exceeded", False, None


def _check_sl_tp_order_status(current_prices=None):
    """
    Check SL/TP limit order status on Coinbase for all open positions.
    Handles filled (close position), canceled (re-place), and processes all positions per cycle.
    When current_prices is provided and SL order is still open but price is below stop (gap-down),
    cancels the SL and executes market sell (stop-limit gap fallback).
    """
    cfg = get_execution_config()
    if (cfg.get('auto_execution_exchange') or 'coinbase').lower() != 'coinbase':
        return
    open_positions = get_open_positions(exclude_test=True)
    for position in open_positions:
        sl_id = position.get('sl_order_id')
        tp_id = position.get('tp_order_id')
        retry_due = False
        next_retry_raw = position.get('sl_retry_next_at')
        retry_reason = position.get('sl_retry_reason')
        if next_retry_raw and retry_reason == 'auction_mode':
            try:
                retry_at = datetime.fromisoformat(str(next_retry_raw).replace('Z', '+00:00'))
                retry_due = datetime.now(timezone.utc) >= retry_at
            except Exception:
                retry_due = True
        if not sl_id and not tp_id and not retry_due:
            continue
        exit_reason = None
        fill_price = None
        sl_canceled = False
        tp_canceled = False
        sl_status = None  # 'filled' | 'canceled' | 'open'
        exit_fee_usd = 0.0
        if sl_id:
            from execution_engine import fetch_order_with_fee
            order_info = fetch_order_with_fee(
                sl_id,
                (cfg.get('auto_execution_exchange') or 'coinbase').lower(),
                position_id=position.get('id'),
                trade_id=position.get('trade_id'),
            )
            if order_info:
                status = order_info['status']
            else:
                status = fetch_order_status(sl_id)
            sl_status = status
            if status == 'filled':
                intended_stop = float(position.get('current_stop_price') or position.get('ai_stop_price') or 0)
                exchange_stop = float(position.get('sl_stop_price') or intended_stop or 0)
                if order_info and order_info.get('fill_price'):
                    fill_price = order_info['fill_price']
                    exit_fee_usd = float(order_info.get('fee_usd', 0) or 0)
                else:
                    fill_price = float(position.get('current_stop_price') or position.get('ai_stop_price', 0))
                exit_reason = 'stop_loss'
                if intended_stop > 0 and fill_price > 0:
                    _execution_engine_module.log_execution({
                        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "action": "stop_loss_fill_telemetry",
                        "symbol": position.get('coin_symbol', ''),
                        "position_id": position.get('id'),
                        "intended_stop": intended_stop,
                        "exchange_stop": exchange_stop,
                        "fill_price": fill_price,
                        "slippage_to_stop": round(((fill_price - intended_stop) / intended_stop) * 100, 6),
                    })
            elif status == 'canceled':
                sl_canceled = True
            elif status == 'open':
                desired_stop = float(position.get('current_stop_price') or position.get('ai_stop_price') or 0)
                placed_stop = float(position.get('sl_stop_price') or 0)
                if desired_stop > 0 and placed_stop > 0:
                    reprice_drift_pct = abs(desired_stop - placed_stop) / placed_stop * 100
                    drift_trigger = 0.05
                    try:
                        from thresholds import get_threshold
                        drift_trigger = float(get_threshold('DYNAMIC_STOP_REPRICE_DRIFT_PCT', 0.05))
                    except Exception:
                        pass
                    # Avoid churn: only reprice for meaningful stop drift.
                    if reprice_drift_pct >= drift_trigger:
                        # Per-position lock prevents concurrent monitor passes from racing
                        # cancel→place and producing duplicate stops that lock 2× the balance.
                        reprice_lock = _get_sl_reprice_lock(position['id'])
                        if not reprice_lock.acquire(blocking=False):
                            logger.info(
                                f"  ⏭ SL reprice for {position.get('coin_symbol', '')} already in progress — skipping"
                            )
                        else:
                            try:
                                logger.info(
                                    f"  🔄 Repricing SL for {position.get('coin_symbol', '')}: "
                                    f"exchange ${placed_stop:.8f} -> journal ${desired_stop:.8f} ({reprice_drift_pct:.3f}% drift)"
                                )
                                # Re-read journal in case another pass already advanced it.
                                if position.get('sl_order_id') != sl_id:
                                    logger.info(
                                        f"  ⏭ SL order id changed under us for {position.get('coin_symbol', '')}; aborting reprice"
                                    )
                                else:
                                    cancel_result = cancel_and_wait(sl_id)
                                    if cancel_result.get('success'):
                                        # Pre-flight orphan sweep: any other open sell orders for this base
                                        # symbol could be leftovers from a previous failed reprice that locked
                                        # base balance — cancel them before placing the new stop.
                                        try:
                                            from protected_assets import is_protected as _is_protected
                                            ex = get_coinbase_exchange()
                                            symbol_ccxt = symbol_to_ccxt(position.get('coin_symbol', ''))
                                            # Never sweep an owner-held coin: its open sell orders may be
                                            # the OWNER's, placed by hand on Coinbase (protected_assets.py).
                                            open_orders = [] if _is_protected(symbol_ccxt) else (ex.fetch_open_orders(symbol_ccxt) or [])
                                            for o in open_orders:
                                                oid = o.get('id')
                                                side = (o.get('side') or '').lower()
                                                if oid and side == 'sell':
                                                    logger.warning(
                                                        f"  🧹 Cancelling orphan sell order {oid} on {symbol_ccxt} before reprice"
                                                    )
                                                    cancel_and_wait(oid)
                                        except Exception as recon_e:
                                            logger.warning(f"  ⚠ Orphan sweep failed for {position.get('coin_symbol', '')}: {recon_e}")
                                        new_sl = place_stop_limit_sell_order(
                                            position.get('coin_symbol', ''),
                                            float(position.get('quantity', 0) or 0),
                                            desired_stop,
                                        )
                                        if new_sl.get('success'):
                                            new_sl_id = new_sl.get('order_id')
                                            update_position(position['id'], {'sl_order_id': new_sl_id, 'sl_stop_price': desired_stop})
                                            position['sl_order_id'] = new_sl_id
                                            position['sl_stop_price'] = desired_stop
                                            sl_id = new_sl_id
                                            _execution_engine_module.log_execution({
                                                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                                "action": "reprice_stop_limit_sell",
                                                "symbol": position.get('coin_symbol', ''),
                                                "position_id": position.get('id'),
                                                "new_order_id": new_sl_id,
                                                "exchange_stop": placed_stop,
                                                "intended_stop": desired_stop,
                                                "slippage_to_stop": round(((desired_stop - placed_stop) / placed_stop) * 100, 6),
                                            })
                                        else:
                                            # Don't clobber sl_order_id — old order is gone from exchange
                                            # but the journal will retry on next cycle. Loud warning so
                                            # operators see the gap.
                                            logger.warning(
                                                f"  ⚠ SL reprice failed to place new stop for {position.get('coin_symbol', '')}: {new_sl.get('error')}"
                                            )
                                            _execution_engine_module.log_execution({
                                                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                                "action": "reprice_stop_limit_sell",
                                                "status": "error",
                                                "symbol": position.get('coin_symbol', ''),
                                                "position_id": position.get('id'),
                                                "intended_stop": desired_stop,
                                                "error": new_sl.get('error'),
                                            })
                                            # balance_zero: position was closed externally (e.g. trailing
                                            # stop fired concurrently). Flag exit so monitor loop closes it.
                                            if new_sl.get('balance_zero'):
                                                logger.warning(
                                                    f"  ⚠ SL reprice balance_zero for {position.get('coin_symbol', '')} "
                                                    f"— flagging for external-close detection on next cycle"
                                                )
                                                exit_reason = 'external_close'
                                                fill_price = float(
                                                    current_prices.get(position.get('coin_id', ''), 0) or 0
                                                )
                                    else:
                                        logger.warning(
                                            f"  ⚠ SL reprice cancel failed for {position.get('coin_symbol', '')}: {cancel_result.get('error')}"
                                        )
                            except Exception as e:
                                logger.warning(f"  ⚠ SL reprice error for {position.get('coin_symbol', '')}: {e}")
                            finally:
                                reprice_lock.release()
        if not exit_reason and tp_id:
            from execution_engine import fetch_order_with_fee
            order_info = fetch_order_with_fee(
                tp_id,
                (cfg.get('auto_execution_exchange') or 'coinbase').lower(),
                position_id=position.get('id'),
                trade_id=position.get('trade_id'),
            )
            if order_info:
                status = order_info['status']
            else:
                status = fetch_order_status(tp_id)
            if status == 'filled':
                if order_info and order_info.get('fill_price'):
                    fill_price = order_info['fill_price']
                    exit_fee_usd = float(order_info.get('fee_usd', 0) or 0)
                else:
                    fill_price = float(position.get('current_target_price') or position.get('ai_target_price', 0))
                exit_reason = 'take_profit'
            elif status == 'canceled':
                tp_canceled = True
        if exit_reason and fill_price:
            if sl_id and exit_reason == 'take_profit':
                cancel_order(sl_id)
            if tp_id and exit_reason == 'stop_loss':
                cancel_order(tp_id)
            closed, err = close_position(
                position['id'],
                exit_price=fill_price,
                exit_reason=exit_reason,
                notes=f"Coinbase limit order filled at ${fill_price:.8f}",
                exit_fee_usd=exit_fee_usd,
            )
            if not err and not _skip_alerts_for_test_position(position):
                try:
                    if exit_reason == 'stop_loss':
                        alerts.send_stop_loss_executed_alert(position, {'fill_price': fill_price, 'success': True}, closed=closed)
                    else:
                        alerts.send_take_profit_executed_alert(position, {'fill_price': fill_price, 'success': True}, closed=closed)
                except Exception:
                    pass
            continue
        if sl_canceled or tp_canceled:
            try:
                if sl_canceled:
                    update_position(position['id'], {'sl_order_id': None, 'sl_stop_price': None})
                    position['sl_order_id'] = None
                    logger.warning(f"  ⚠ SL order canceled by exchange for {position.get('coin_symbol', '')} (id={position['id']}), re-placing...")
                    # If market is already through stop, skip stop-limit re-place and execute fallback sell.
                    current_price = (current_prices or {}).get(position.get('coin_id'))
                    stop_price = float(position.get('current_stop_price') or position.get('ai_stop_price') or 0)
                    _entry_px = float(position.get('entry_price', 0) or 0)
                    if current_price and _entry_px > 0:
                        _sl_ratio = current_price / _entry_px
                        if _sl_ratio > 20 or _sl_ratio < 0.02:
                            logger.warning(
                                f"  SL re-place gap sell BLOCKED {position.get('coin_symbol', '')}: "
                                f"price ratio={_sl_ratio:.2f}x — likely bad data, re-placing SL instead"
                            )
                            _place_sl_tp_with_retry(position)
                            continue
                    if current_price and stop_price and current_price < stop_price * 0.995:
                        _sl_cancel_confirmed = False
                        try:
                            _sl_spot = fetcher.get_coinbase_spot_prices([position.get('coin_symbol', '')])
                            _sl_spot_px = _sl_spot.get(position.get('coin_symbol', ''))
                            if _sl_spot_px and _sl_spot_px > 0 and _sl_spot_px < stop_price * 0.995:
                                _sl_cancel_confirmed = True
                            elif _sl_spot_px and _sl_spot_px > 0:
                                logger.warning(
                                    f"  SL-canceled sell BLOCKED {position.get('coin_symbol', '')}: "
                                    f"Coinbase spot ${_sl_spot_px:.4f} NOT below stop ${stop_price:.4f}"
                                )
                            else:
                                _sl_cancel_confirmed = True
                        except Exception:
                            _sl_cancel_confirmed = True
                        if not _sl_cancel_confirmed:
                            _place_sl_tp_with_retry(position)
                            continue
                        logger.info(
                            f"  SL canceled and price already below stop for {position.get('coin_symbol', '')}: "
                            f"${current_price:.4f} < ${stop_price:.4f}, using market-sell fallback"
                        )
                        result = execute_market_sell(
                            symbol=position.get('coin_symbol', ''),
                            quantity=float(position.get('quantity', 0)),
                            reason='stop_loss',
                            position_id=position.get('id'),
                            trade_id=position.get('trade_id'),
                        )
                        if result.get('success'):
                            fill_price = result.get('fill_price', current_price)
                            exit_fee_usd = float(result.get('fee_usd', 0) or 0)
                            closed, err = close_position(
                                position['id'],
                                exit_price=fill_price,
                                exit_reason='stop_loss',
                                notes=f"SL canceled + below stop: market sell fallback at ${fill_price:.8f}",
                                exit_fee_usd=exit_fee_usd,
                            )
                            if not err and not _skip_alerts_for_test_position(position):
                                alerts.send_stop_loss_executed_alert(position, {'fill_price': fill_price, 'success': True}, closed=closed)
                        else:
                            if not _skip_alerts_for_test_position(position):
                                alerts.send_stop_loss_alert(position, current_price)
                        continue
                if tp_canceled:
                    update_position(position['id'], {'tp_order_id': None})
                    position['tp_order_id'] = None
                    logger.warning(f"  ⚠ TP order canceled by exchange for {position.get('coin_symbol', '')} (id={position['id']}), re-placing...")
                _place_sl_tp_with_retry(position)
            except Exception as e:
                logger.warning(f"  ⚠ Failed to re-place SL/TP for {position.get('coin_symbol', '')}: {e}")
            continue
        if retry_due and not sl_id:
            retry_count = int(position.get('sl_retry_count') or 0)
            if retry_count >= SL_AUCTION_RETRY_MAX_ATTEMPTS:
                _alert_sl_retry_exhausted(position)
                continue
            logger.info(
                f"  🔁 Retrying deferred SL placement for {position.get('coin_symbol', '')} "
                f"(attempt {retry_count + 1}/{SL_AUCTION_RETRY_MAX_ATTEMPTS})"
            )
            sl_ok, _, sl_err, _, _ = _place_sl_tp_orders_for_position(position)
            if sl_ok:
                logger.info(f"  ✓ Deferred SL placement recovered for {position.get('coin_symbol', '')}")
            elif sl_err:
                logger.warning(
                    f"  ⚠ Deferred SL retry failed for {position.get('coin_symbol', '')}: {sl_err}"
                )
            continue
        # Stop-limit gap fallback: SL order still open but price below stop (limit didn't fill)
        if sl_id and sl_status == 'open' and current_prices and not exit_reason and cfg.get('auto_execution_enabled', True):
            current_price = current_prices.get(position.get('coin_id'))
            stop_price = float(position.get('current_stop_price') or position.get('ai_stop_price') or 0)
            entry_price_check = float(position.get('entry_price', 0) or 0)
            if current_price and entry_price_check > 0:
                _gap_ratio = current_price / entry_price_check
                if _gap_ratio > 20 or _gap_ratio < 0.02:
                    logger.warning(
                        f"  Gap fallback BLOCKED {position.get('coin_symbol', '')}: "
                        f"price ${current_price} vs entry ${entry_price_check} ratio={_gap_ratio:.2f}x — likely bad data"
                    )
                    continue
            if current_price and stop_price and current_price < stop_price * 0.995:
                # Cross-check with Coinbase spot before canceling SL and selling
                _gap_confirmed = False
                try:
                    _spot = fetcher.get_coinbase_spot_prices([position.get('coin_symbol', '')])
                    _spot_price = _spot.get(position.get('coin_symbol', ''))
                    if _spot_price and _spot_price > 0 and _spot_price < stop_price * 0.995:
                        _gap_confirmed = True
                    elif _spot_price and _spot_price > 0:
                        logger.warning(
                            f"  Gap fallback BLOCKED {position.get('coin_symbol', '')}: "
                            f"Coinbase spot ${_spot_price:.4f} NOT below stop ${stop_price:.4f} "
                            f"(fetched price ${current_price:.4f} was bad data)"
                        )
                    else:
                        _gap_confirmed = True  # Coinbase unavailable, trust fetched price
                except Exception:
                    _gap_confirmed = True  # Coinbase check failed, trust fetched price
                if not _gap_confirmed:
                    continue
                if _skip_alerts_for_test_position(position):
                    continue
                try:
                    logger.info(f"  Stop-limit gap fallback: {position.get('coin_symbol', '')} price ${current_price:.4f} < stop ${stop_price:.4f}, canceling SL and market selling")
                    cancel_order(sl_id)
                    update_position(position['id'], {'sl_order_id': None, 'sl_stop_price': None})
                    result = execute_market_sell(
                        symbol=position.get('coin_symbol', ''),
                        quantity=float(position.get('quantity', 0)),
                        reason='stop_loss',
                        position_id=position.get('id'),
                        trade_id=position.get('trade_id'),
                    )
                    if result.get('success'):
                        fill_price = result.get('fill_price', current_price)
                        exit_fee_usd = float(result.get('fee_usd', 0) or 0)
                        if stop_price > 0 and fill_price > 0:
                            _execution_engine_module.log_execution({
                                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                "action": "stop_loss_gap_fallback_telemetry",
                                "symbol": position.get('coin_symbol', ''),
                                "position_id": position.get('id'),
                                "intended_stop": stop_price,
                                "exchange_stop": float(position.get('sl_stop_price') or stop_price),
                                "fill_price": fill_price,
                                "slippage_to_stop": round(((fill_price - stop_price) / stop_price) * 100, 6),
                            })
                        closed, err = close_position(
                            position['id'],
                            exit_price=fill_price,
                            exit_reason='stop_loss',
                            notes=f"Stop-limit gap fallback: market sell at ${fill_price:.8f}",
                            exit_fee_usd=exit_fee_usd,
                        )
                        if not err:
                            alerts.send_stop_loss_executed_alert(position, {'fill_price': fill_price, 'success': True}, closed=closed)
                        else:
                            logger.warning(f"  Gap fallback: market sell ok but close_position failed: {err}")
                    else:
                        alerts.send_stop_loss_alert(position, current_price)
                except Exception as e:
                    logger.warning(f"  Stop-loss gap fallback failed for {position.get('coin_symbol', '')}: {e}")
                    alerts.send_stop_loss_alert(position, current_price)


# =============================================================================
# Exit Monitoring (with duplicate alert prevention and validation)
# =============================================================================

# Alert tracking to prevent duplicate alerts (spam prevention)
_recent_alerts = {}  # {(position_id, alert_type): last_alert_datetime}
_recent_alerts_lock = threading.Lock()
# When we first sent stop/target alert for a position still open → used for "missed exit" follow-up
_exit_alert_first_seen = {}  # (position_id, 'stop'|'target') -> timestamp
_exit_alert_first_seen_lock = threading.Lock()
_ALERT_PRUNE_AGE_DAYS = 30


def _prune_alert_dicts():
    """Remove stale entries from _recent_alerts and _exit_alert_first_seen (older than 30 days)."""
    cutoff_dt = datetime.now() - timedelta(days=_ALERT_PRUNE_AGE_DAYS)
    cutoff_ts = time.time() - (_ALERT_PRUNE_AGE_DAYS * 86400)
    with _recent_alerts_lock:
        stale = [k for k, v in _recent_alerts.items() if v < cutoff_dt]
        for k in stale:
            del _recent_alerts[k]
    with _exit_alert_first_seen_lock:
        stale = [k for k, v in _exit_alert_first_seen.items() if v < cutoff_ts]
        for k in stale:
            del _exit_alert_first_seen[k]
MISSED_EXIT_ALERT_MINUTES = 10

# Positions we sold (execute_market_sell succeeded) but close_position failed — retry close only, never sell again
_MAX_PENDING_CLOSE = 100
_SOLD_PENDING_CLOSE_FILE = os.path.join(os.path.dirname(__file__), 'data', 'sold_pending_close.json')
_RECONCILIATION_HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'data', 'reconciliation_history.jsonl')
_reconciliation_history_lock = threading.Lock()

def _append_reconciliation_history(entry: dict) -> None:
    """Append one JSON line to data/reconciliation_history.jsonl. Thread-safe."""
    try:
        os.makedirs(os.path.dirname(_RECONCILIATION_HISTORY_FILE), exist_ok=True)
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        line = json.dumps(entry, default=str) + "\n"
        with _reconciliation_history_lock:
            with open(_RECONCILIATION_HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as e:
        logger.warning("Reconciliation history write failed: %s", e)

def _load_sold_pending_close() -> set:
    import safe_json
    data = safe_json.load(_SOLD_PENDING_CLOSE_FILE, default=list)
    return set(data)

def _save_sold_pending_close():
    import safe_json
    os.makedirs(os.path.dirname(_SOLD_PENDING_CLOSE_FILE), exist_ok=True)
    safe_json.save(_SOLD_PENDING_CLOSE_FILE, list(_EXIT_SOLD_PENDING_CLOSE))

_EXIT_SOLD_PENDING_CLOSE: set = _load_sold_pending_close()

# At most one insufficient_cash alert per refresh cycle (avoids startup spam when cash not yet loaded)
_insufficient_cash_alert_refresh = None
_current_refresh_id = 0  # Set at start of each refresh_data()
_refresh_lock = threading.Lock()  # Prevents concurrent refresh_data() from scheduler/startup/manual overlap


def _skip_alerts_for_test_position(position):
    """True if position is test/paper — never send alerts for test trades (including old test positions)."""
    if not position:
        return True
    if position.get('is_test') is True:
        return True
    if position.get('exit_reason') == 'test_cleanup':
        return True
    return False


def _alerts_ready():
    """False during startup grace period to avoid false alerts before prices/cash are loaded."""
    return (time.time() - _app_start_time) >= ALERT_GRACE_SECONDS


def _should_alert(position_id, alert_type):
    """
    Prevent duplicate alerts within 10 minutes.
    Without this, the monitoring loop (runs every 30 seconds) would send
    120 alerts per hour for the same stop-loss hit until manually closed.
    Also suppresses all exit alerts during startup grace period (ALERT_GRACE_SECONDS).
    Lock ensures only one thread can grant an alert for the same key (avoids double-send).
    """
    if not _alerts_ready():
        return False
    from datetime import timedelta
    key = (position_id, alert_type)
    with _recent_alerts_lock:
        last_alert = _recent_alerts.get(key)
        if last_alert and (datetime.now() - last_alert) < timedelta(minutes=10):
            return False
        _recent_alerts[key] = datetime.now()
        return True


def check_position_exits(current_prices):
    """
    Check all open positions for stop/target hits. Run every 30 seconds.
    - Positions with sl_order_id/tp_order_id: check order status (exchange executes limit orders).
    - Positions without: price-based stop/target/trailing.
    Live positions use Coinbase spot as authoritative price; third-party for paper/display.
    """
    _prune_alert_dicts()
    open_positions = get_open_positions(exclude_test=True)
    paper_open = [p for p in get_open_positions(exclude_test=False) if is_paper_trade(p)]
    if not open_positions and not paper_open:
        return
    exec_cfg = get_execution_config()
    auto_exec = exec_cfg.get('auto_execution_enabled', True)

    # Phase 5: Overlay Coinbase spot prices for live positions (exchange-native > third-party)
    if open_positions:
        _live_symbols = list(set(p.get('coin_symbol', '') for p in open_positions if p.get('coin_symbol')))
        _live_sym_to_cid = {}
        for p in open_positions:
            s = p.get('coin_symbol', '').upper()
            cid = p.get('coin_id', '')
            if s and cid:
                _live_sym_to_cid[s] = cid
        if _live_symbols:
            try:
                _cb_spot = fetcher.get_coinbase_spot_prices(_live_symbols)
                _overrides = 0
                for sym, spot_px in _cb_spot.items():
                    cid = _live_sym_to_cid.get(sym)
                    if cid and spot_px and spot_px > 0:
                        old_px = current_prices.get(cid)
                        current_prices[cid] = spot_px
                        if old_px and old_px > 0 and abs(spot_px - old_px) / old_px > 0.01:
                            logger.debug(
                                f"  Coinbase spot override {sym}: ${old_px:.6g} -> ${spot_px:.6g}"
                            )
                        _overrides += 1
                if _overrides:
                    logger.debug(f"  Coinbase spot overlay: {_overrides}/{len(_live_symbols)} live positions")
            except Exception as e:
                logger.debug(f"  Coinbase spot overlay failed: {e}")

    # First: check if any SL/TP limit orders are filled (hybrid mode); pass prices for gap fallback
    _check_sl_tp_order_status(current_prices)
    open_positions = get_open_positions(exclude_test=True)
    for position in open_positions:
        has_limit_orders = position.get('sl_order_id') or position.get('tp_order_id')
        coin_id = position.get('coin_id')
        if not coin_id:
            continue
        current_price = current_prices.get(coin_id)
        if not current_price or current_price <= 0:
            continue
        entry_price = float(position.get('entry_price', 0))
        if entry_price <= 0:
            continue
        price_ratio = current_price / entry_price
        if price_ratio > 20 or price_ratio < 0.02:
            logger.warning(
                f"  LIVE price sanity reject {position.get('coin_symbol')}: "
                f"entry=${entry_price} current=${current_price} ratio={price_ratio:.2f}x — skipping exit check"
            )
            continue
        if has_limit_orders:
            has_sl = bool(position.get('sl_order_id'))
            has_tp_order = bool(position.get('tp_order_id'))
            if has_sl and has_tp_order:
                # Both SL and TP on exchange — fully handled there
                continue
            if has_sl and not has_tp_order:
                # SL on exchange, TP tracked in-app only (avoids Coinbase reserve conflicts).
                # Check if price has reached target — if so, cancel SL and market sell.
                target_price = float(position.get('current_target_price') or position.get('ai_target_price') or 0)
                if target_price > 0 and current_price >= target_price:
                    symbol = position.get('coin_symbol', '')
                    sl_order_id = position.get('sl_order_id')
                    logger.info(f"  🎯 In-app TP hit for {symbol} at ${current_price:.8f} (target ${target_price:.8f}) — cancelling SL order first")
                    try:
                        cancel_result = cancel_order(sl_order_id)
                        if cancel_result.get('success'):
                            logger.info(f"  ✓ Cancelled SL order {sl_order_id} for TP sell")
                        else:
                            # Check if SL already filled (race condition: SL filled between _check_sl_tp_order_status and now)
                            sl_status = fetch_order_status(sl_order_id)
                            if sl_status == 'filled':
                                logger.warning(f"  ⚠ SL order {sl_order_id} already filled — skipping TP sell to avoid double-sell")
                                continue
                            logger.warning(f"  ⚠ SL cancel failed for {sl_order_id} (status={sl_status}), proceeding with TP sell")
                    except Exception as e:
                        logger.warning(f"  ⚠ SL cancel error for {sl_order_id}: {e}")
                    _handle_take_profit_hit(position, current_price, auto_exec)
                # If TP not hit, SL is still on exchange — nothing else to do in-app
                continue
            # Only TP on exchange, no SL — fall through to legacy monitoring
        # No limit orders: legacy price-based stop/target/trailing
        stop_price = float(position.get('current_stop_price') or position.get('ai_stop_price') or 0)
        target_price = float(position.get('current_target_price') or position.get('ai_target_price') or 0)
        if stop_price <= 0:
            continue
        # Winners always run: no fixed target — managed by stop-loss + trailing.
        # (Legacy positions that still carry a target_price keep the take-profit branch below.)
        if current_price <= stop_price:
            _handle_stop_loss_hit(position, current_price, auto_exec)
        elif target_price and current_price >= target_price:
            _handle_take_profit_hit(position, current_price, auto_exec)
        else:
            _check_trailing_stop(position, current_price, entry_price, auto_exec)
    if auto_exec:
        try:
            from exit_manager import check_trailing_stops
            check_trailing_stops(current_prices)
        except Exception as e:
            logger.info(f"  Exit manager (trailing stop) error: {e}")
            traceback.print_exc()
        try:
            from exit_manager import check_stale_positions
            check_stale_positions(current_prices)
        except Exception as e:
            logger.info(f"  Exit manager (stale position) error: {e}")
            traceback.print_exc()
    try:
        _check_paper_position_exits(current_prices)
    except Exception as e:
        logger.debug(f"  Paper position exits error: {e}")


def _handle_stop_loss_hit(position, current_price, auto_exec):
    """Execute or alert on stop-loss hit. Never double-sell: if we already sold but close failed, only retry close."""
    if _skip_alerts_for_test_position(position):
        return
    symbol = position.get('coin_symbol', '')
    position_id = position.get('id')
    if not _should_alert(position_id, 'stop'):
        # Already in cooldown; check if we should send "missed exit" follow-up
        with _exit_alert_first_seen_lock:
            key = (position_id, 'stop')
            first_seen = _exit_alert_first_seen.get(key)
        if first_seen and (time.time() - first_seen) >= MISSED_EXIT_ALERT_MINUTES * 60:
            try:
                alerts.send_missed_exit_alert(position, 'stop', MISSED_EXIT_ALERT_MINUTES)
            except Exception:
                pass
            with _exit_alert_first_seen_lock:
                _exit_alert_first_seen.pop((position_id, 'stop'), None)
        return
    with _exit_alert_first_seen_lock:
        _exit_alert_first_seen[(position_id, 'stop')] = time.time()
    logger.info(f"  STOP-LOSS HIT: {symbol} at ${current_price:.8f}")
    if auto_exec:
        try:
            if position_id in _EXIT_SOLD_PENDING_CLOSE:
                closed, err = close_position(position_id, exit_price=current_price, exit_reason='stop_loss', notes="Retry close after prior sell")
                if not err:
                    _EXIT_SOLD_PENDING_CLOSE.discard(position_id)
                    _save_sold_pending_close()
                    alerts.send_stop_loss_executed_alert(position, {'fill_price': current_price, 'success': True}, closed=closed)
                return
            result = execute_market_sell(
                symbol=symbol,
                quantity=float(position.get('quantity', 0)),
                reason='stop_loss',
                position_id=position.get('id'),
                trade_id=position.get('trade_id'),
            )
            if result.get('success'):
                closed, err = close_position(position_id, exit_price=result.get('fill_price', current_price), exit_reason='stop_loss', notes=f"Auto-executed at ${result.get('fill_price', current_price):.8f}", exit_fee_usd=float(result.get('fee_usd', 0) or 0))
                if err:
                    if len(_EXIT_SOLD_PENDING_CLOSE) < _MAX_PENDING_CLOSE:
                        _EXIT_SOLD_PENDING_CLOSE.add(position_id)
                        _save_sold_pending_close()
                    logger.info(f"    close_position error: {err}")
                else:
                    alerts.send_stop_loss_executed_alert(position, result, closed=closed)
            else:
                alerts.send_stop_loss_alert(position, current_price)
        except Exception as e:
            logger.info(f"    Stop-loss execution failed: {e}")
            alerts.send_stop_loss_alert(position, current_price)
    else:
        alerts.send_stop_loss_alert(position, current_price)


def _handle_take_profit_hit(position, current_price, auto_exec):
    """Execute or alert on take-profit hit. Never double-sell: if we already sold but close failed, only retry close."""
    if _skip_alerts_for_test_position(position):
        return
    symbol = position.get('coin_symbol', '')
    position_id = position.get('id')
    if not _should_alert(position_id, 'target'):
        with _exit_alert_first_seen_lock:
            key = (position_id, 'target')
            first_seen = _exit_alert_first_seen.get(key)
        if first_seen and (time.time() - first_seen) >= MISSED_EXIT_ALERT_MINUTES * 60:
            try:
                alerts.send_missed_exit_alert(position, 'target', MISSED_EXIT_ALERT_MINUTES)
            except Exception:
                pass
            with _exit_alert_first_seen_lock:
                _exit_alert_first_seen.pop((position_id, 'target'), None)
        return
    with _exit_alert_first_seen_lock:
        _exit_alert_first_seen[(position_id, 'target')] = time.time()
    logger.info(f"  TAKE-PROFIT HIT: {symbol} at ${current_price:.8f}")
    if auto_exec:
        try:
            if position_id in _EXIT_SOLD_PENDING_CLOSE:
                closed, err = close_position(position_id, exit_price=current_price, exit_reason='take_profit', notes="Retry close after prior sell")
                if not err:
                    _EXIT_SOLD_PENDING_CLOSE.discard(position_id)
                    _save_sold_pending_close()
                    alerts.send_take_profit_executed_alert(position, {'fill_price': current_price, 'success': True}, closed=closed)
                return
            result = execute_market_sell(
                symbol=symbol,
                quantity=float(position.get('quantity', 0)),
                reason='take_profit',
                position_id=position.get('id'),
                trade_id=position.get('trade_id'),
            )
            if result.get('success'):
                closed, err = close_position(position_id, exit_price=result.get('fill_price', current_price), exit_reason='take_profit', notes=f"Target hit! Sold at ${result.get('fill_price', current_price):.8f}", exit_fee_usd=float(result.get('fee_usd', 0) or 0))
                if err:
                    if len(_EXIT_SOLD_PENDING_CLOSE) < _MAX_PENDING_CLOSE:
                        _EXIT_SOLD_PENDING_CLOSE.add(position_id)
                        _save_sold_pending_close()
                    logger.info(f"    close_position error: {err}")
                else:
                    alerts.send_take_profit_executed_alert(position, result, closed=closed)
            else:
                alerts.send_take_profit_alert(position, current_price)
        except Exception as e:
            logger.info(f"    Take-profit execution failed: {e}")
            alerts.send_take_profit_alert(position, current_price)
    else:
        alerts.send_take_profit_alert(position, current_price)


def _check_trailing_stop(position, current_price, entry_price, auto_exec):
    """Check and apply trailing stop (configurable via thresholds.py: TRAILING_STOP_MIN_PROFIT_PCT, TRAILING_STOP_TRAIL_PCT)."""
    if entry_price <= 0:
        return
    profit_pct = ((current_price - entry_price) / entry_price) * 100
    from thresholds import get_threshold
    min_profit = get_threshold('TRAILING_STOP_MIN_PROFIT_PCT', 8)
    if profit_pct < min_profit:
        return
    high_water = float(position.get('high_water_mark') or entry_price)
    if current_price > high_water:
        high_water = current_price
        update_position(position.get('id'), {'high_water_mark': high_water})
    trail_pct = get_threshold('TRAILING_STOP_TRAIL_PCT', 13)
    trailing_stop = high_water * (1 - trail_pct / 100)  # trail below high-water (from thresholds.py)
    if current_price <= trailing_stop:
        if _skip_alerts_for_test_position(position):
            return
        if not _should_alert(position.get('id'), 'trailing'):
            return
        logger.info(f"  TRAILING STOP HIT: {position.get('coin_symbol', '')} at ${current_price:.8f} (peak ${high_water:.8f})")
        if auto_exec:
            _handle_stop_loss_hit(position, current_price, auto_exec=True)
        else:
            alerts.send_trailing_stop_alert(position, current_price, high_water)


_POSITION_SYNC_INTERVAL_CYCLES = 10  # sync quantities every 10 cycles (5 min at 30s/cycle)
_LAST_CCXT_PERIODIC_WARN_TS = 0.0
_CCXT_PERIODIC_WARN_INTERVAL_SEC = 900.0  # avoid log spam when CCXT is missing from the venv

# Reuse position prices for 300s — swing-trade timescales don't need sub-minute price freshness.
# Exchange-side SL/TP orders fire independently; this cache only affects software-monitored exits.
_POSITION_PRICE_CACHE_TTL = 300
_position_price_cache = {'ts': 0, 'coin_ids': set(), 'prices': {}}
_position_price_cache_lock = threading.Lock()

_PRICE_ANOMALY_THRESHOLD = 0.30  # 30% change triggers alert before acting
_last_known_prices: dict[str, float] = {}
_price_anomaly_alerted: dict[str, float] = {}  # coin_id -> timestamp of last alert


def _get_position_prices_cached(coin_ids: list) -> dict:
    """Return prices for coin_ids, from cache if < 300s old and ids covered; else fetch and update cache.
    Normal TTLs govern freshness — CoinPaprika primary (free/unlimited) keeps prices current."""
    now = time.time()
    ids_set = set(coin_ids)
    with _position_price_cache_lock:
        if ids_set and now - _position_price_cache['ts'] < _POSITION_PRICE_CACHE_TTL and ids_set <= _position_price_cache['coin_ids']:
            return {cid: _position_price_cache['prices'][cid] for cid in coin_ids if cid in _position_price_cache['prices']}
    prices = fetcher.get_current_prices_fast(coin_ids)
    if prices:
        with _position_price_cache_lock:
            _position_price_cache['ts'] = now
            _position_price_cache['coin_ids'] = set(prices.keys())
            _position_price_cache['prices'] = dict(prices)
    return prices


def _periodic_quantity_sync(open_positions):
    """Sync journal quantities from exchange for all open positions. Runs periodically in monitor loop."""
    try:
        cfg = get_execution_config()
        if not cfg.get('auto_sync_balance'):
            return
        exchange_id = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
        result = fetch_balance_sync(exchange_id)
        holdings = result.get('holdings') or {}
        _ex = {}
        for k, v in holdings.items():
            cur = (k or '').strip().upper()
            if cur:
                _ex[cur] = _ex.get(cur, 0) + float(v or 0)
        holdings = _ex
        by_sym = {}
        for p in open_positions:
            sym = (p.get('coin_symbol') or '').strip().upper()
            if sym:
                by_sym.setdefault(sym, []).append(p)
        for sym, positions in by_sym.items():
            eq = holdings.get(sym, 0)
            if eq <= 0:
                continue
            if len(positions) != 1:
                continue
            pos = positions[0]
            jq = float(pos.get('quantity', 0) or 0)
            # Exchange = source of truth: amend on any fractional difference
            if abs(jq - eq) < 1e-8:
                continue
            updated, err = update_position(pos['id'], quantity=round(eq, 8))
            if not err:
                logger.info(f"  ✓ Periodic sync: {sym} qty {jq:.8f} → {eq:.8f}")
                sl_id = pos.get('sl_order_id')
                tp_id = pos.get('tp_order_id')
                if sl_id or tp_id:
                    try:
                        if sl_id:
                            cancel_order(sl_id)
                        if tp_id:
                            cancel_order(tp_id)
                        update_position(pos['id'], {'sl_order_id': None, 'tp_order_id': None})
                        pos['sl_order_id'] = None
                        pos['tp_order_id'] = None
                        pos['quantity'] = eq
                        _place_sl_tp_with_retry(pos)
                    except Exception as e:
                        logger.warning(f"  ⚠ Failed to re-place SL/TP after periodic sync for {sym}: {e}")
    except ImportError as e:
        if str(e) == "pip install ccxt for auto-execution":
            global _LAST_CCXT_PERIODIC_WARN_TS
            now = time.time()
            if now - _LAST_CCXT_PERIODIC_WARN_TS >= _CCXT_PERIODIC_WARN_INTERVAL_SEC:
                logger.warning(
                    "  ⚠ Periodic quantity sync skipped: CCXT not installed in this Python env. "
                    "From the project root: .venv\\Scripts\\python -m pip install -r requirements.txt "
                    f"(or: pip install ccxt). Underlying error: {e.__cause__!r}"
                )
                _LAST_CCXT_PERIODIC_WARN_TS = now
            return
        raise
    except Exception as e:
        logger.warning(f"  ⚠ Periodic quantity sync error: {e}")


def position_monitor_loop():
    """Background thread: monitor positions every 30 seconds, sync quantities every ~5 min."""
    logger.info("  Starting position monitor thread...")
    # ATOMIC JOURNAL DRAINER — makes TWAP → journal + SL/TP 100% reliable
    _journal_drainer_thread = threading.Thread(target=_drain_pending_journal_fills, daemon=True)
    _journal_drainer_thread.start()
    logger.info("[OK] Pending journal fill drainer started (60s cycle)")
    cycle_count = 0
    while True:
        try:
            open_positions = get_open_positions(exclude_test=True)
            paper_open = [p for p in get_open_positions(exclude_test=False) if is_paper_trade(p)]
            if not open_positions and not paper_open:
                time.sleep(30)
                continue
            cycle_count += 1
            if open_positions and cycle_count % _POSITION_SYNC_INTERVAL_CYCLES == 0:
                _periodic_quantity_sync(open_positions)
                open_positions = get_open_positions(exclude_test=True)
            coin_ids = list(set(
                [p.get('coin_id') for p in open_positions if p.get('coin_id')]
                + [p.get('coin_id') for p in paper_open if p.get('coin_id')]
            ))
            if not coin_ids:
                time.sleep(30)
                continue
            current_prices = _get_position_prices_cached(coin_ids)
            if not current_prices:
                logger.info(f"  No prices fetched for {len(coin_ids)} positions")
                time.sleep(30)
                continue

            # Phase 4: Price anomaly detection -- flag >30% moves before acting
            _anomalies = []
            _now_ts = time.time()
            for _cid, _px in current_prices.items():
                _prev = _last_known_prices.get(_cid)
                if _prev and _prev > 0 and _px > 0:
                    _drift = abs(_px - _prev) / _prev
                    if _drift > _PRICE_ANOMALY_THRESHOLD:
                        _anomalies.append((_cid, _prev, _px, _drift))
            if _anomalies:
                for _cid, _prev, _px, _drift in _anomalies:
                    logger.warning(
                        f"  PRICE ANOMALY {_cid}: ${_prev:.6g} -> ${_px:.6g} ({_drift*100:+.1f}%) in single cycle"
                    )
                _last_alert_ts = max((_price_anomaly_alerted.get(a[0], 0) for a in _anomalies), default=0)
                if _now_ts - _last_alert_ts > 300:
                    try:
                        _anom_lines = [f"  {c}: ${p:.6g} -> ${n:.6g} ({d*100:+.1f}%)" for c, p, n, d in _anomalies]
                        alerts.send_critical_alert(
                            subject=f"Price anomaly: {len(_anomalies)} coin(s) moved >{_PRICE_ANOMALY_THRESHOLD*100:.0f}%",
                            body=f"Anomalies detected in position monitor cycle:\n" + "\n".join(_anom_lines),
                            alert_type='price_anomaly',
                        )
                        for a in _anomalies:
                            _price_anomaly_alerted[a[0]] = _now_ts
                    except Exception:
                        pass
            _last_known_prices.update(current_prices)

            # Phase 4: Price source logging for forensic tracing
            try:
                _sources = fetcher.get_price_sources(list(current_prices.keys()))
                if _sources and cycle_count % 10 == 1:
                    _src_summary = {}
                    for _s in _sources.values():
                        _src_summary[_s] = _src_summary.get(_s, 0) + 1
                    logger.info(f"  Price sources: {_src_summary}")
            except Exception:
                pass

            refresh_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for _pos in open_positions:
                if _pos.get('coin_id') in current_prices:
                    update_position(_pos['id'], {'last_refreshed': refresh_ts})
            for _pos in paper_open:
                if _pos.get('coin_id') in current_prices:
                    update_position(_pos['id'], {'last_refreshed': refresh_ts})
            check_position_exits(current_prices)
        except Exception as e:
            logger.info(f"  Position monitor error: {e}")
            traceback.print_exc()
        time.sleep(30)


# =============================================================================
# Momentum Scan — lightweight high-frequency check (no AI calls)
# Fetches price data, runs indicators, checks for momentum conditions.
# If momentum detected on any coin, triggers a full refresh.
# =============================================================================

_MOMENTUM_SCAN_INTERVAL = 1800  # 30 minutes — reduces CoinGecko get_top_50 calls (~2/hour vs ~4/hour)
_momentum_scan_running = False
_momentum_scan_lock = threading.Lock()

def momentum_scan():
    """
    Lightweight scan: CoinGecko prices + indicators only.
    If any coin shows momentum acceleration + MTF alignment, trigger full refresh.
    Zero AI cost — pure math on existing price data.
    """
    global _momentum_scan_running
    with _momentum_scan_lock:
        if _momentum_scan_running:
            return
        _momentum_scan_running = True
    try:
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] MOMENTUM SCAN running...")
        from thresholds import _load_live_thresholds
        live = _load_live_thresholds()

        raw_coins = fetcher.get_top_50_coins()
        if not raw_coins:
            logger.info("  Momentum scan: no coin data, skipping.")
            return

        btc = next((c for c in raw_coins if c.get('id') in ('bitcoin', 'btc') or c.get('symbol') == 'BTC'), None)
        market_24h = 0.0
        market_7d = 0.0
        if btc:
            market_24h = float(btc.get('price_change_percentage_24h') or btc.get('price_change_percentage_24h_in_currency') or 0)
            market_7d = float(btc.get('price_change_percentage_7d_in_currency') or 0)

        scan_data = {
            'coins': raw_coins,
            'market_24h_pct': market_24h,
            'market_7d_pct': market_7d,
        }
        scan_data = calculate_indicators(scan_data)

        # Check each coin for momentum conditions
        early_thresh = float(live.get('EARLY_MOMENTUM_MIN_EARLY_MOM', 3.0))
        quality_thresh = float(live.get('EARLY_MOMENTUM_MIN_QUALITY', 20))
        max_rsi = float(live.get('EARLY_MOMENTUM_MAX_RSI', 78))

        momentum_hits = []
        for coin in scan_data.get('coins', []):
            if coin.get('id', '') in STABLECOINS:
                continue
            ind = coin.get('indicators') or {}
            accel = ind.get('momentum_accel') or {}
            early_mom = float(ind.get('early_momentum') or 0)
            quality = float(ind.get('momentum_quality') or 0)
            rsi = float(ind.get('rsi') or 50)
            ch1h = float(ind.get('ch1h') or 0)
            ch24h = float(ind.get('ch24h') or 0)
            ch7d = float(ind.get('ch7d') or 0)

            mtf_up = (ch1h > 0 and ch24h > 0 and ch7d > 0)
            if (accel.get('is_accelerating')
                    and early_mom > early_thresh
                    and quality > quality_thresh
                    and mtf_up
                    and rsi < max_rsi):
                momentum_hits.append({
                    'symbol': (coin.get('symbol') or '').upper(),
                    'early_mom': early_mom,
                    'quality': quality,
                    'accel': accel,
                })

        if momentum_hits:
            symbols = ', '.join(h['symbol'] for h in momentum_hits[:5])
            logger.info(f"  MOMENTUM DETECTED on {len(momentum_hits)} coin(s): {symbols} — triggering full refresh")
            try:
                if alerts:
                    alerts.send_alert(
                        f"Momentum scan: {len(momentum_hits)} coin(s) accelerating ({symbols}). Full refresh triggered.",
                        channel='system'
                    )
            except Exception:
                pass
            refresh_data()
        else:
            logger.info("  Momentum scan: no signals, sleeping.")
    except Exception as e:
        logger.warning(f"  Momentum scan error: {e}")
    finally:
        _momentum_scan_running = False


# =============================================================================
# Main Data Refresh Logic
# =============================================================================

def refresh_data():
    """
    Full data refresh: enhanced data, indicators, scoring, regime, AI consensus,
    veto pipeline, dynamic targets, portfolio/correlation, auto-exec (if enabled), drift check.
    """
    global cached_data, last_updated, refresh_count, current_regime, _current_refresh_id

    if not _refresh_lock.acquire(blocking=False):
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] ⏭ Skipping refresh — another refresh is already running")
        return 'skipped'
    try:
        return _refresh_data_inner()
    finally:
        _refresh_lock.release()


def _refresh_data_inner():
    """Actual refresh logic. Must only be called while _refresh_lock is held."""
    global cached_data, last_updated, refresh_count, current_regime, _current_refresh_id

    with _cache_lock:
        _current_refresh_id = refresh_count + 1
    try:
        logger.info(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🔄 Starting elite refresh #{_current_refresh_id}")

        # Auto-sync balance from exchange when enabled (Coinbase/Kraken as source of truth)
        do_auto_sync_balance()

        if _light_test_pipeline():
            logger.info("  TEST MODE (Light): reduced API calls")

        # DATA COLLECTION (with health tracking)
        with _cache_lock:
            use_cached_raw = _light_test_pipeline() and _enhanced_data_cache['raw'] is not None and (time.time() - _enhanced_data_cache['ts']) < _ENHANCED_CACHE_TTL
            raw_from_cache = _enhanced_data_cache['raw'] if use_cached_raw else None
        if use_cached_raw:
            raw = raw_from_cache
            logger.info("  📡 Using cached enhanced data (Light Mode, 30 min)")
        else:
            logger.info("  📡 Fetching enhanced data from 12 sources...")
        try:
            if not use_cached_raw:
                n_enhanced = 5 if _light_test_pipeline() else 10
                raw = fetcher.get_all_data(n_enhanced=n_enhanced)
            if _light_test_pipeline() and raw is not None:
                with _cache_lock:
                    _enhanced_data_cache['raw'] = raw
                    _enhanced_data_cache['ts'] = time.time()
            mark_source('coingecko', 'ok')
            
            # Check individual data components
            if raw.get('fear_greed'):
                mark_source('fear_greed', 'ok')
            else:
                mark_source('fear_greed', 'warning', 'No data returned')
            
            if raw.get('macro'):
                mark_source('fred', 'ok')
            else:
                mark_source('fred', 'warning', 'No macro data')
            
            if raw.get('sentiment'):
                mark_source('alpha_vantage', 'ok')
            else:
                mark_source('alpha_vantage', 'warning', 'No sentiment data')
            
            # Enhanced data (calculated from prices)
            if raw.get('coins') and len(raw['coins']) > 0:
                sample_coin = raw['coins'][0]
                enhanced = sample_coin.get('enhanced', {})
                
                # BTC Correlation (calculated from price movements)
                if enhanced.get('btc_correlation') is not None:
                    mark_source('btc_correlation', 'ok')
                else:
                    mark_source('btc_correlation', 'warning', 'No correlation')
                # On-chain (CryptoQuant)
                if any(enhanced.get(k) is not None for k in ('sopr', 'exchange_netflow_24h', 'realized_cap_hodl_signal', 'whale_netflow')):
                    mark_source('onchain', 'ok')
                else:
                    mark_source('onchain', 'warning', 'No on-chain data')
        except Exception as e:
            mark_source('coingecko', 'error', e)
            raise
        
        # =====================================================================
        # FILTER STABLECOINS
        # =====================================================================
        logger.info("  🚫 Filtering stablecoins...")
        coins_before = len(raw.get('coins', []))
        tradeable = [c for c in raw.get('coins', []) if not is_stablecoin(c)]
        if coins_before > len(tradeable):
            logger.info(f"     Removed {coins_before - len(tradeable)} stablecoins (USDT, USDC, DAI, etc.)")

        # =====================================================================
        # FILTER TO COINBASE ONLY (top 50 must be tradeable on Coinbase)
        # =====================================================================
        coinbase_symbols = get_coinbase_tradeable_symbols()
        if coinbase_symbols:
            before_cb = len(tradeable)
            tradeable = [c for c in tradeable if (c.get('symbol') or '').strip().upper() in coinbase_symbols]
            if before_cb > len(tradeable):
                logger.info(f"     Removed {before_cb - len(tradeable)} coins not listed on Coinbase")

        # Stage 1b: scan the top 100 by market cap (was 50) for more momentum throughput.
        # If we have fewer than 100 tradeable, fetch more pages until we fill 100 or run out.
        need = 100
        page = 2
        max_extra_pages = 6
        all_coins = list(raw.get('coins', []))  # full list in market-cap order (page 1)
        while len(tradeable) < need and page <= max_extra_pages:
            more = fetcher.get_coins_page(page=page, per_page=50)
            if not more:
                break
            seen_ids = {c.get('id') for c in all_coins}
            for c in more:
                if c.get('id') and c['id'] not in seen_ids:
                    seen_ids.add(c['id'])
                    all_coins.append(c)
            tradeable = [c for c in all_coins if not is_stablecoin(c)]
            if coinbase_symbols:
                tradeable = [c for c in tradeable if (c.get('symbol') or '').strip().upper() in coinbase_symbols]
            if len(tradeable) >= need:
                logger.info(f"     Fetched extra page(s) from CoinGecko to fill top {need}")
                break
            page += 1

        # Liquidity floor (Stage 1b): drop thin books in the expansion tier that TWAP can't fill
        # cleanly. Coins with unknown volume are kept (top-cap names always report volume).
        try:
            from thresholds import get_threshold as _gt
            _min_vol = float(_gt('MIN_24H_VOLUME_USD', 10_000_000))
        except Exception:
            _min_vol = 10_000_000.0

        def _liquid_enough(c):
            v = c.get('total_volume')
            if v is None:
                return True
            try:
                return float(v) >= _min_vol
            except (TypeError, ValueError):
                return True

        # Keep top 100 by market cap (liquidity-filtered) + any momentum-screened coins that passed filters
        mcap_coins = [c for c in tradeable
                      if c.get('_source') != 'momentum_screen' and _liquid_enough(c)][:100]
        momentum_coins = [c for c in tradeable if c.get('_source') == 'momentum_screen']
        mcap_ids = {c.get('id') for c in mcap_coins}
        extra_momentum = [c for c in momentum_coins if c.get('id') not in mcap_ids]
        raw['coins'] = mcap_coins + extra_momentum
        coins_after = len(raw['coins'])
        n_momentum = len(extra_momentum)
        logger.info(f"     {coins_after} tradeable coins ({coins_after - n_momentum} by market cap + {n_momentum} momentum, Coinbase only)")
        
        try:
            with_indicators = calculate_indicators(raw)
            mark_source('indicators', 'ok')
        except Exception as e:
            mark_source('indicators', 'error', e)
            logger.warning(f"  ⚠ Indicators failed ({e}); using raw data with minimal defaults so refresh can continue.")
            import copy
            with_indicators = copy.deepcopy(raw)
            for c in (with_indicators.get('coins') or []):
                if not c.get('indicators'):
                    c['indicators'] = {'rsi': 50.0, 'volatility': 50.0}

        # Bull/momentum: attach market (BTC) 24h/7d so scorer can reward outperforming tokens (see docs/RALLY_PARTICIPATION_ASSESSMENT.md)
        try:
            btc = next((c for c in (with_indicators.get('coins') or []) if c.get('id') in ('bitcoin', 'btc') or c.get('symbol') == 'BTC'), None)
            if btc is not None:
                m24 = btc.get('price_change_percentage_24h') or btc.get('price_change_percentage_24h_in_currency')
                m7 = btc.get('price_change_percentage_7d_in_currency')
                if m24 is not None:
                    with_indicators['market_24h_pct'] = float(m24)
                if m7 is not None:
                    with_indicators['market_7d_pct'] = float(m7)
        except (TypeError, ValueError):
            pass

        try:
            scored = score_tokens(with_indicators)
            # Preserve global market cap and macro from raw (pipeline must pass them through)
            if raw.get('global_market_cap'):
                scored['global_market_cap'] = raw['global_market_cap']
            if raw.get('macro') is not None:
                scored['macro'] = raw['macro']
            mark_source('scoring', 'ok')
        except Exception as e:
            mark_source('scoring', 'error', e)
            logger.warning(f"  ⚠ Scoring failed ({e}); using pass-through with HOLD so refresh can continue.")
            scored = dict(with_indicators)
            if raw.get('global_market_cap'):
                scored['global_market_cap'] = raw['global_market_cap']
            if raw.get('macro') is not None:
                scored['macro'] = raw['macro']
            coins = scored.get('coins') or []
            for c in coins:
                c.setdefault('signal', 'HOLD')
                c.setdefault('score', 0)
            scored['summary'] = {'total': len(coins), 'buy': 0, 'hold': len(coins), 'sell': 0}

        # MARKET REGIME DETECTION
        logger.info("  🌍 Detecting market regime...")
        # Stage 1b: compute the BTC risk-on/off master gate (cached 12h) and inject it so the
        # regime detector exposes risk_on in characteristics for the quant gate + sizer.
        try:
            from risk_regime import compute_btc_risk_on
            from thresholds import get_threshold as _gt
            _btc = next((c for c in (scored.get('coins') or [])
                         if c.get('id') in ('bitcoin', 'btc') or c.get('symbol') == 'BTC'), None)
            _btc_30d = None
            if _btc:
                _btc_30d = _btc.get('price_change_percentage_30d_in_currency')
            scored['btc_risk_on'] = compute_btc_risk_on(
                band_pct=float(_gt('RISK_OFF_MA_BAND_PCT', 1.5)),
                btc_30d_change_fallback=_btc_30d,
            )
        except Exception as _e:
            logger.warning("  ⚠ risk-on gate computation failed (%s); regime falls back to 30d-change", _e)
        regime_data = regime_detector.detect_regime(scored)
        with _cache_lock:
            current_regime = regime_data
        # Rally participation: pass market 24h % so quant/consensus can relax BEAR/VOLATILE gates when market is already up (see docs/RALLY_PARTICIPATION_ASSESSMENT.md)
        try:
            btc = next((c for c in (scored.get('coins') or []) if c.get('id') in ('bitcoin', 'btc') or c.get('symbol') == 'BTC'), None)
            market_24h = (btc.get('price_change_percentage_24h') or btc.get('price_change_percentage_24h_in_currency')) if btc else None
            if market_24h is not None:
                regime_data['market_24h_pct'] = float(market_24h)
        except (TypeError, ValueError):
            pass

        logger.info(f"  ✓ Regime: {regime_data['regime']} ({regime_data['confidence']}% confidence)")
        logger.info(f"    Strategy: {regime_data['strategy_adjustment']['recommendation']}")

        softened = apply_regime_sentiment_sell_softening(scored, regime_data)
        if softened:
            logger.info(f"  📊 Softened {softened} marginal SELL→HOLD (regime={regime_data.get('regime')}, sentiment aligned)")

        # QUANT SCREENING + VETO (pure quant — no LLM calls)
        logger.info("  📊 Running quant screening + veto...")

        regime = regime_data.get('regime', 'SIDEWAYS')
        fear_greed_value = scored.get('fear_greed', {}).get('value', 50)

        from thresholds import get_threshold as _gt
        score_threshold = _gt(f'GATE_SCORE_{regime}', 52)
        market_24h_pct = regime_data.get('market_24h_pct')
        if regime == 'VOLATILE' and market_24h_pct is not None and float(market_24h_pct) > 5.0:
            score_threshold = min(score_threshold, 68)
        if regime == 'BEAR':
            try:
                coins_for_atr = scored.get('coins', [])[:20]
                atr_vals = [float((c.get('indicators') or {}).get('atr_pct', 0) or 0) for c in coins_for_atr if c.get('indicators')]
                avg_atr = sum(atr_vals) / len(atr_vals) if atr_vals else 0
                if avg_atr >= 4.0:
                    score_threshold = max(score_threshold, 52)
            except Exception:
                pass
        if fear_greed_value < 15:
            score_threshold = max(44, score_threshold - 4)

        all_coins = scored.get('coins', [])
        high_score_coins = [c for c in all_coins if c.get('score', 0) > score_threshold]

        # Volume gate: compute median 24h volume for quant_signal penalty (Cycle 17)
        _vols = sorted([float(c.get('total_volume', 0) or 0) for c in high_score_coins if float(c.get('total_volume', 0) or 0) > 0])
        regime_data['volume_median'] = _vols[len(_vols) // 2] if _vols else 0

        n_top = 5 if _light_test_pipeline() else 15
        top_10 = sorted(high_score_coins, key=lambda c: c.get('score', 0), reverse=True)[:n_top]
        for rank, c in enumerate(top_10, 1):
            c['_rank'] = rank
            c['_is_top_3'] = (rank <= 3)
            c['_is_top_10'] = True

        if not top_10:
            logger.warning(f"  ⚠ No coins above {score_threshold}.")
            for coin in scored.get('coins', []):
                coin['ai_recommendation'] = None
        else:
            logger.info(f"  ✓ {len(top_10)} coins passed quant gate")

            # Total cash from exchange; subtract pending TWAP (exclude test orders)
            total_cash = float(getattr(position_sizer, 'account_size', 0) or 0)
            pending_twap = get_active_twap_orders()
            pending_twap_usd = sum(float(o.get('total_usd', 0) or 0) for o in pending_twap if not o.get('is_test'))
            cash_available = max(0.0, round(total_cash - pending_twap_usd, 2))

            for coin in top_10:
                consensus_result = {
                    'consensus_signal': None,
                    'reasoning': f"Regime: {regime}. Pure quant + veto.",
                    'regime_filter': f"Regime: {regime}",
                }

                final_signal, quant = apply_full_veto_logic(
                    coin, regime_data, consensus_result, fear_greed_value,
                    position_sizer=position_sizer, cash_available=cash_available,
                )
                is_buy = final_signal in ['ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY']
                position_calc = quant.get('position_sizing') if quant else None
                if is_buy and not position_calc:
                    _apply_dynamic_targets_to_coin(coin, regime_data, final_signal, target_calculator)
                    position_calc = position_sizer.calculate_for_consensus(
                        entry_price=coin.get('current_price'),
                        stop_loss_pct=coin.get('stop_loss_pct', 2.5),
                        consensus_signal=final_signal,
                        regime_data=regime_data,
                        coin_volatility=coin.get('indicators', {}).get('volatility', 50),
                        cash_available=cash_available,
                        edge_probability=quant.get('edge_probability') if quant else None,
                        rr_ratio=(coin.get('risk') or {}).get('rr_ratio'),
                    )
                    if get_test_mode():
                        try:
                            testing_log_signal(coin, consensus_result, position_calc)
                        except Exception as e:
                            logger.warning(f"  ⚠ Testing log error: {e}")

                # ML learning: log screened coin for missed-opportunity analysis
                try:
                    append_screening_entry(
                        coin_id=coin.get('id', ''),
                        symbol=coin.get('symbol', ''),
                        entry_price=coin.get('current_price') or 0,
                        quant_tier=quant.get('quant_tier', '') if quant else '',
                        market_regime=regime_data.get('regime', ''),
                    )
                except Exception:
                    pass

                coin['ai_recommendation'] = {
                    'consensus': consensus_result,
                    'position_sizing': position_calc,
                    'quant': quant,
                    'final_signal': final_signal,
                }
                logger.info(f"    ✓ {coin.get('name')}: {final_signal} (quant={quant['quant_tier']})")

            analyzed_ids = {c['id'] for c in top_10}
            for coin in scored.get('coins', []):
                if coin['id'] not in analyzed_ids:
                    coin['ai_recommendation'] = None

        # =====================================================================
        # Display-only quant tier for non–top-10 coins (markets tab column)
        # =====================================================================
        for coin in scored.get('coins', []):
            if coin.get('ai_recommendation') is not None:
                continue
            try:
                quant = generate_quant_signal(coin, regime_data, fear_greed_value)
                coin['ai_recommendation'] = {'quant': quant}
            except Exception as e:
                logger.debug(f"  Quant display for {coin.get('id', '?')}: {e}")

        # =====================================================================
        # FINAL PASS: Dynamic targets for any BUY coin that doesn't have them yet
        # (e.g. not in agreed, or no top_10, or consensus was HOLD/WEAK)
        # =====================================================================
        try:
            buy_count = 0
            for coin in scored.get('coins', []):
                if coin.get('signal') == 'BUY' and not coin.get('dynamic_targets'):
                    _apply_dynamic_targets_to_coin(coin, regime_data, 'MODERATE_BUY', target_calculator)
                    buy_count += 1
            if buy_count > 0:
                logger.info(f"  🎯 Applied dynamic targets (MODERATE_BUY) to {buy_count} BUY coin(s) without consensus targets")
            mark_source('dynamic_targets', 'ok')
        except Exception as e:
            logger.error(f"  ❌ Error in dynamic targets final pass: {e}")
            mark_source('dynamic_targets', 'error', str(e))
            traceback.print_exc()

        # =====================================================================
        # PORTFOLIO & CORRELATION HEDGING (35% cap on high-BTC-correlation risk)
        # =====================================================================
        try:
            open_positions = get_open_positions(exclude_test=True)
            all_positions = get_all_positions()
            closed_positions = [p for p in all_positions if p.get('status') == 'CLOSED' and not (p.get('is_test') or p.get('exit_reason') == 'test_cleanup')]
            coins_with_recs = [c for c in scored.get('coins', []) if (c.get('ai_recommendation') or {}).get('position_sizing')]
            account_size = _effective_account_size_for_risk() or float(_build_account_overview().get('total_cash', 0))
            portfolio_summary = get_portfolio_risk_summary(open_positions, coins_with_recs, account_size, closed_positions=closed_positions)
            factors = get_correlation_adjustment_factors(open_positions, coins_with_recs, account_size)
            for coin in scored.get('coins', []):
                pos = (coin.get('ai_recommendation') or {}).get('position_sizing')
                if not pos:
                    continue
                cid = coin.get('id', '')
                factor = factors.get(cid, 1.0)
                adjusted = apply_correlation_adjustment(pos, factor)
                if coin.get('ai_recommendation') is not None:
                    coin['ai_recommendation']['position_sizing'] = adjusted
            scored['portfolio_risk_summary'] = portfolio_summary
            scored['account_total_cash'] = account_size  # Same denominator as open_exposure_pct for risk tile
        except Exception as e:
            logger.warning(f"  ⚠ Portfolio/correlation step failed: {e}")
            traceback.print_exc()
            scored['portfolio_risk_summary'] = {
                'total_risk_pct': 0, 'open_exposure_pct': 0, 'correlated_risk_pct': 0, 'max_correlated_pct': 0,
                'risk_cap_pct': 25, 'suggestions': [], 'high_corr_count': 0
            }

        # Low-cash alert: if available cash is below 10% of total, alert once per 24h
        try:
            overview = _build_account_overview()
            cash_avail = overview.get('cash_available_to_trade') or 0
            total = overview.get('total_cash') or 0
            if total > 0 and cash_avail < total * 0.10:
                pct = (cash_avail / total) * 100
                alerts.send_low_cash_alert(cash_avail, total, pct)
        except Exception as e:
            logger.warning(f"  ⚠ Low-cash alert check failed: {e}")

        # =====================================================================
        # TRADE ALERTS (ULTRA/STRONG/MODERATE BUY — eligible to book; skip if already held, skip if risk would block)
        # =====================================================================
        try:
            open_positions_alert = get_open_positions(exclude_test=True)
            held_symbols = {
                (p.get('coin_symbol') or '').strip().upper()
                for p in open_positions_alert
                if p.get('coin_symbol')
            }
            account_size_alert = _effective_account_size_for_risk() or float(_build_account_overview().get('total_cash', 0))
            exec_risk_alert = get_portfolio_risk_summary(open_positions_alert, [], account_size_alert) if account_size_alert > 0 else None
            for coin in scored.get('coins', []):
                rec = coin.get('ai_recommendation') or {}
                signal = (rec.get('consensus') or {}).get('consensus_signal', '')
                if signal not in ('ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY'):
                    continue
                symbol = (coin.get('symbol') or '').strip().upper()
                if symbol in held_symbols:
                    logger.info(f"  ⏭ Alert skip {symbol}: already have open position")
                    continue
                if exec_risk_alert is not None and account_size_alert > 0:
                    pos_sizing = rec.get('position_sizing') or {}
                    new_risk_pct = float(pos_sizing.get('max_loss_pct') or 0) or float(pos_sizing.get('pct_of_account') or 0)
                    if new_risk_pct > 0:
                        ok, reason = execution_safety_ok(exec_risk_alert, new_risk_pct, account_size_alert)
                        if not ok:
                            logger.info(f"  ⏭ Alert skip {symbol}: {reason}")
                            continue
                try:
                    alerts.alert_ai_signal(coin)
                except Exception as ae:
                    logger.warning(f"  ⚠ AI signal alert failed for {coin.get('symbol', '')}: {ae}")
        except Exception as e:
            logger.warning(f"  ⚠ Trade alerts step failed: {e}")

        # =====================================================================
        # AUTO-EXECUTION (Coinbase TWAP for ULTRA_BUY / STRONG_BUY / MODERATE_BUY with high quant)
        # Quant STRONG/ULTRA wins — no AI gate; final_signal is post-quant and veto.
        # =====================================================================
        try:
            exec_cfg = get_execution_config()
            auto_exec_eligible = [
                c for c in scored.get('coins', [])
                if (c.get('ai_recommendation') or {}).get('final_signal') in ('ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY')
            ]
            n_eligible = len(auto_exec_eligible)
            enabled = bool(exec_cfg.get('auto_execution_enabled', True))
            exchange = (exec_cfg.get('auto_execution_exchange') or 'coinbase').lower()
            logger.info(f"  📍 Auto-exec: enabled={enabled}, exchange={exchange}, eligible={n_eligible}")
            # Lean observability (Stage 1c): one line per refresh so a cycle where lean
            # was inactive/bypassed (the 2026-06-02 leak) is visible from the log alone.
            try:
                import lean_policy
                _lean_on = lean_policy.is_lean_active()
                _risk_on_val = ((regime_data or {}).get('characteristics') or {}).get('risk_on')
                _n_lean_pruned = sum(
                    1 for _c in (scored.get('coins') or [])
                    if 'lean_pruned' in (((_c.get('ai_recommendation') or {}).get('quant') or {}).get('signal_badges') or [])
                )
                logger.info(
                    f"  📍 Lean: LEAN_ON={int(_lean_on)} risk_on={_risk_on_val} "
                    f"eligible={n_eligible} lean_pruned={_n_lean_pruned}"
                )
            except Exception as _le:
                logger.warning(f"  ⚠ Lean status log failed: {_le}")
            # Paper fills run alongside real execution (not instead of)
            if get_paper_trading() and n_eligible > 0:
                logger.info(f"  📍 Paper trading: simulating up to {n_eligible} journal fill(s)")
                for coin in auto_exec_eligible:
                    try:
                        _simulate_paper_fill(coin, regime_data)
                    except Exception as pe:
                        logger.warning(f"  ⚠ Paper sim failed {coin.get('symbol', '')}: {pe}")

            # STRATEGY.md candidate #1: BTC risk-on rotation paper cohort (runs every
            # refresh regardless of BUY signals — its trigger is the regime flip itself)
            if get_paper_trading():
                try:
                    from rotation_tracker import track_rotation
                    _btc_coin = next((c for c in scored.get('coins', []) if c.get('id') == 'bitcoin'), None)
                    _rot = track_rotation((_btc_coin or {}).get('current_price'))
                    if _rot:
                        logger.info(f"  📍 Rotation cohort: {_rot}")
                except Exception as re_:
                    logger.warning(f"  ⚠ Rotation tracker failed: {re_}")

            # Maker-fill groundwork (STRATEGY.md v1.7b): one BTC top-of-book snapshot
            # per refresh — spread history for the future maker-fill trial can't be
            # backfilled. Best-effort; never blocks the refresh.
            try:
                from spread_logger import capture_spread_snapshot
                capture_spread_snapshot()
            except Exception:
                pass

            # Market-state capture (STRATEGY.md v2.0, 2026-09-26): persist the environment
            # and the compact universe the refresh just fetched. Until now coins_cache was
            # overwritten hourly and no environment feature (ETH/BTC, breadth, dominance)
            # was recorded anywhere — the owner's alt-regime hypothesis (H-2026Q4-2) needs
            # a forward record that cannot be fitted. Best-effort; never blocks the refresh.
            try:
                from market_state_logger import record_market_state
                record_market_state(scored.get('coins') or [], regime_data)
            except Exception as _ms_e:
                logger.debug(f"market state capture skipped: {_ms_e}")

            # Gate-watch instrumentation (STRATEGY.md v1.6d): lean-pruned BUYs are
            # suppressed at the SIGNAL layer, so they never reach the auto-exec skip
            # logger — for 3 weeks the "trades we didn't take" stream was silently
            # empty. Log them here (once per coin per UTC day) so the falsifier loop
            # can evaluate the risk-on gate against forward returns.
            try:
                _today_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                if _lean_prune_logged_day.get('day') != _today_utc:
                    _lean_prune_logged_day.clear()
                    _lean_prune_logged_day['day'] = _today_utc
                    _lean_prune_logged_day['coins'] = set()
                from paper_evidence import append_shadow_skip
                for _c in scored.get('coins', []):
                    _rec = _c.get('ai_recommendation') or {}
                    _q = _rec.get('quant') or {}
                    if 'lean_pruned' not in (_q.get('signal_badges') or []):
                        continue
                    _cid = _c.get('id') or ''
                    if not _cid or _cid in _lean_prune_logged_day['coins']:
                        continue
                    _lean_prune_logged_day['coins'].add(_cid)
                    # Shadow verdict (2026-08-08 review): risk_on has been False on
                    # 100% of prunes, so the path allowlist and the 30d-momentum floor
                    # have never been the BINDING constraint — we cannot tell whether
                    # they discriminate or are vestigial. Record ch30d plus what
                    # lean_policy would rule with risk_on forced True, so the non-regime
                    # half of the gate accumulates evidence while we wait for a flip.
                    _ind_lp = _c.get('indicators') or {}
                    _ch30_lp = _ind_lp.get('ch30d')
                    try:
                        from lean_policy import entry_allowed as _lean_entry_allowed
                        _shadow_ok, _shadow_reason = _lean_entry_allowed(
                            _q.get('quant_path'), True, _ch30_lp)
                    except Exception:
                        _shadow_ok, _shadow_reason = None, None
                    append_shadow_skip({
                        'coin_id': _cid,
                        'symbol': (_c.get('symbol') or '').upper(),
                        'entry_price': float(_c.get('current_price') or 0) or None,
                        'final_signal': _rec.get('final_signal'),
                        'quant_tier': _q.get('quant_tier'),
                        'quant_path': _q.get('quant_path'),
                        'market_regime': (regime_data or {}).get('regime'),
                        'risk_on': (_q.get('risk_on') if isinstance(_q, dict) else None),
                        'price_change_30d': float(_ch30_lp) if _ch30_lp is not None else None,
                        # counterfactual: would path + momentum have allowed this, if risk-on?
                        'lean_shadow_allowed': _shadow_ok,
                        'lean_shadow_reason': _shadow_reason,
                        'live_skip_reason': 'lean_pruned',
                        'score': _q.get('score'),
                    })
            except Exception as _lp_err:
                logger.debug(f"lean-prune shadow log failed: {_lp_err}")

            account_size_shadow = _effective_account_size_for_risk() or float(_build_account_overview().get('total_cash', 0))
            exec_risk_shadow = get_portfolio_risk_summary(get_open_positions(exclude_test=True), [], account_size_shadow)
            for coin in auto_exec_eligible:
                skip = _classify_live_auto_exec_skip(
                    coin, exec_risk_shadow, account_size_shadow, regime_data,
                )
                if skip:
                    _log_shadow_skip(coin, skip, regime_data)

            if not enabled:
                logger.info("  ⏭ Auto-exec OFF (toggle On in dashboard header to run TWAP on buys)")
            elif get_test_mode():
                logger.info("  ⏭ Auto-exec skipped: Test Mode ON — real exchange orders disabled (paper simulation runs separately above)")
            elif exchange != 'coinbase':
                logger.info("  ⏭ Auto-exec only runs for Coinbase (current exchange:", exchange, ")")
            elif n_eligible == 0:
                logger.info("  ⏭ Auto-exec: no eligible BUY signals")
            else:
                # HELOC drawdown check: pause auto-exec if drawn capital drawdown exceeds limit
                _heloc_cfg = load_account_config()
                if _heloc_cfg.get('heloc_mode') and _heloc_cfg.get('heloc_drawn', 0) > 0:
                    _dd_metrics = _compute_heloc_metrics(_heloc_cfg)
                    if _dd_metrics.get('drawn_drawdown_alert'):
                        logger.warning(f"  ⏭ Auto-exec PAUSED: HELOC drawn capital drawdown {_dd_metrics['drawn_drawdown_pct']:.1f}% exceeds limit {_heloc_cfg.get('max_drawn_drawdown_pct', 30)}%")
                        try:
                            if _alerts_ready():
                                alerts.send_critical_alert(
                                    subject="HELOC Drawdown Limit Reached",
                                    body=f"Drawn capital drawdown is {_dd_metrics['drawn_drawdown_pct']:.1f}% (limit: {_heloc_cfg.get('max_drawn_drawdown_pct', 30)}%). Auto-execution paused for this cycle.",
                                    alert_type="heloc_drawdown",
                                )
                        except Exception:
                            pass
                        n_eligible = 0  # skip TWAP loop below
                account_size = _effective_account_size_for_risk() or float(_build_account_overview().get('total_cash', 0))
                exec_risk = get_portfolio_risk_summary(get_open_positions(exclude_test=True), [], account_size)
                # Running risk committed this round: after each TWAP start, add that position's risk
                # so the next candidate is checked against (current + already-started) and we stay under cap.
                committed_risk_pct = 0.0
                for coin in auto_exec_eligible:
                    adjusted = {**exec_risk, 'total_risk_pct': (exec_risk.get('total_risk_pct') or 0) + committed_risk_pct}
                    started = _start_twap_if_safe(coin, adjusted, account_size, regime_data)
                    if started:
                        pos = (coin.get('ai_recommendation') or {}).get('position_sizing') or {}
                        committed_risk_pct += float(pos.get('max_loss_pct', 0) or 0)
        except Exception as e:
            logger.warning(f"  ⚠ Auto-execution step failed: {e}")
            traceback.print_exc()

        # UPDATE BACKTESTER OUTCOMES (fetch prices for ALL historical rec coins, not just current watchlist)
        current_prices = {c['id']: c.get('current_price') for c in scored.get('coins', []) if c.get('current_price')}
        # Seed position-monitor price cache so exit loop doesn't call CoinGecko for next 90s
        if current_prices:
            with _position_price_cache_lock:
                _position_price_cache['ts'] = time.time()
                _position_price_cache['coin_ids'] = set(current_prices.keys())
                _position_price_cache['prices'] = dict(current_prices)
        # Outcome backfill for screening log (ML learning)
        try:
            from ml.outcome_backfill import get_coin_ids_needing_prices, run_backfill as _run_outcome_backfill
            backfill_needed = get_coin_ids_needing_prices()
            all_needed = backfill_needed - set(current_prices.keys())
            if all_needed:
                extra = fetcher.get_current_prices_fast(list(all_needed))
                if extra:
                    current_prices = {**current_prices, **extra}
            bf_stats = _run_outcome_backfill(lambda ids: {k: current_prices.get(k) for k in ids if k in current_prices})
            if bf_stats.get('screening_updated', 0):
                logger.info(f"  [OK] Screening log outcome backfill: {bf_stats['screening_updated']} entries updated")
        except Exception as e:
            logger.debug(f"  Outcome backfill failed: {e}")
            try:
                from ml.outcome_backfill import run_backfill as _run_outcome_backfill
                _run_outcome_backfill(fetcher.get_current_prices_fast)
            except Exception:
                pass
        
        # Mark AI consensus as successful
        mark_source('quant_veto', 'ok')

        # FINALIZE CACHE AND ONCHAIN STATUS
        
        # Strip sparklines (too large for JSON)
        for coin in scored.get('coins', []):
            coin.pop('sparkline_in_7d', None)

        scored['regime'] = regime_data
        
        # Ensure global_market_cap and macro are included if they were in raw data
        if raw.get('global_market_cap') and 'global_market_cap' not in scored:
            scored['global_market_cap'] = raw['global_market_cap']
        if raw.get('macro') is not None:
            scored['macro'] = raw['macro']
        # On-chain data source for frontend indicator (after enhanced data fetch)
        scored['onchain_status'] = 'live' if (hasattr(fetcher, 'onchain') and fetcher.onchain.api_key) else 'fallback'
        for coin in scored.get('coins', []):
            coin['onchain_status'] = coin.get('enhanced', {}).get('onchain_status', 'unknown')
        # Sample on-chain values for overview card: prefer Bitcoin (market proxy), else first with data
        btc_coin = next((c for c in scored.get('coins', []) if (c.get('symbol') or '').lower() == 'btc'), None)
        for coin in ([btc_coin] if btc_coin else []) + scored.get('coins', []):
            if not coin:
                continue
            enh = coin.get('enhanced', {})
            if enh.get('sopr') is not None or enh.get('exchange_netflow_24h') is not None:
                scored['onchain_overview'] = {
                    'sopr': enh.get('sopr', 1.0),
                    'exchange_netflow_24h': enh.get('exchange_netflow_24h', 0),
                    'lth_supply_change_24h_pct': enh.get('lth_supply_change_24h_pct', 0),
                }
                break
        else:
            scored['onchain_overview'] = None

        with _cache_lock:
            cached_data = scored
            last_updated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            refresh_count += 1
        summary = scored.get('summary', {})

        try:
            agg = signal_history_log_snapshot(summary, scored.get('coins') or [])
            with _cache_lock:
                cached_data['signal_30d'] = agg.get('signal_30d') or {}
                cached_data['ai_30d'] = agg.get('ai_30d') or {}
        except Exception as _e:
            pass
        try:
            log_refresh_snapshot(
                get_open_positions(exclude_test=True),
                scored.get('coins') or [],
                summary,
                regime_data,
            )
        except Exception as _e:
            pass


        logger.info(f"\n✅ Elite refresh complete!")
        logger.info(f"   Coins: {summary.get('total', 0)} | BUY: {summary.get('buy', 0)} | HOLD: {summary.get('hold', 0)} | SELL: {summary.get('sell', 0)}")
        logger.info(f"   Regime: {regime_data['regime']}")
        interval_info = scheduler.INTERVALS.get(scheduler.current_interval, {})
        if scheduler.current_interval == 'off':
            logger.info(f"   Next refresh: manual only\n")
        else:
            logger.info(f"   Next refresh: per schedule ({interval_info.get('label', scheduler.current_interval)}) or manual\n")
        return True

    except Exception as e:
        logger.error(f"❌ Error during refresh: {e}")
        traceback.print_exc()
        return False
    finally:
        _persist_data_source_status()


SOURCE_STATUS_FILE = os.path.join(os.path.dirname(__file__), 'data', 'source_status.json')


def _persist_data_source_status():
    """Persist data source health to data/source_status.json for monitoring/debugging."""
    try:
        import safe_json
        path = SOURCE_STATUS_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            'ts': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'sources': dict(data_source_status),
        }
        safe_json.save(path, payload)
    except Exception as e:
        logger.debug("Could not persist source status: %s", e)


# =============================================================================
# Startup
# =============================================================================

# Start server and trigger initial data load in background
logger.info("  ⚡ Starting server...\n")

# Initialize with empty data (safe defaults for all fields used by frontend)
with _cache_lock:
    cached_data = {
        'coins': [],
        'summary': {'total': 0, 'buy': 0, 'hold': 0, 'sell': 0},
        'fear_greed': {},
        'macro': {},
        'sentiment': {},
        'global_market_cap': {},
        'regime': {'regime': 'UNKNOWN', 'confidence': 0},
        'onchain_status': 'fallback',
        'onchain_overview': {},
        'performance_analytics': {'sharpe_ratio': 0, 'sortino_ratio': 0, 'max_drawdown_pct': 0, 'expectancy_pct': 0, 'win_rate_pct': 0, 'win_rate_by_regime': {}, 'win_rate_by_regime_scenario': {}},
        'portfolio_risk_summary': {'total_risk_pct': 0, 'open_exposure_pct': 0, 'max_single_position_pct': 0, 'var_1d_usd': 0, 'concentration_breaches': 0, 'risk_cap_pct': 25, 'suggestions': []},
        'model_attribution': {'by_model': {}, 'monthly_ranking': [], 'recommendations': []},
    }
    last_updated = 'Never'
    refresh_count = 0

# Configure scheduler
scheduler.set_refresh_callback(refresh_data)


def _run_ml_pipeline():
    """Run ML learning pipeline (backfill, backtest, missed-opportunity, suggestions).

    Raises on failure so callers (weekly loop, monthly scheduler) don't silently
    mark the run as done when the pipeline crashes.
    """
    from ml.run_pipeline import run
    run(save_expectancy=True)


def _run_monthly_learning():
    """Monthly: run ML pipeline then 30d/60d stress report and notify.

    Let the pipeline exception propagate — scheduler only marks last_learning_run
    after this returns cleanly, preventing silent skip of the rest of the month.
    """
    _run_ml_pipeline()
    # Stress report is best-effort — don't let it block the scheduler stamp
    try:
        from scripts.buy_signals_stress_report import run_both
        combined = run_both(write_md=True)
        if combined and alerts:
            alerts.send_stress_report_alert(combined)
    except Exception as e:
        logger.info(f"  [ERR] Monthly stress report: {e}")
        traceback.print_exc()


scheduler.set_learning_callback(_run_monthly_learning)


# Weekly task callbacks: replay → optimizer → roadmap (Saturday 3 AM ET)
def _weekly_replay():
    """Run historical replay to refresh backtest evidence for optimizer."""
    try:
        from historical_replay import run_historical_replay
        result = run_historical_replay(days=90, sim_interval_hours=8, verbose=False)
        logger.info("[weekly:replay] %d trades, profit_factor=%.2f",
                     result.get('total_trades', 0), result.get('profit_factor', 0))
    except Exception as e:
        logger.error("[weekly:replay] Failed: %s", e)


# NOTE: optimizer and roadmap callbacks removed — the dedicated _optimizer_loop thread
# (Sunday 4 AM ET) handles both with retry logic, roadmap chaining, and gated analyses.
# Only replay remains here so fresh backtest data is ready 1h before the optimizer runs.
scheduler.set_weekly_callback('replay', _weekly_replay)

# Start scheduler and background threads (skip during testing)
if not _TESTING:
    if scheduler.current_interval != 'off':
        scheduler.start()
        logger.info(f"[OK] Auto-refresh enabled: {scheduler.INTERVALS[scheduler.current_interval]['label']}")
    else:
        logger.info("[PAUSED] Auto-refresh disabled (manual mode)\n")

    # Daily status report scheduler (9 AM, 1 PM, 5 PM ET)
    def _daily_report_loop():
        while True:
            time.sleep(60)
            try:
                _run_daily_status_report_if_due()
            except Exception:
                pass
    _daily_report_thread = threading.Thread(target=_daily_report_loop, daemon=True)
    _daily_report_thread.start()

    # Monthly learning check: once per hour, run ML pipeline if enabled and due (works even when refresh is off)
    def _monthly_learning_loop():
        while True:
            time.sleep(3600)  # 1 hour
            try:
                scheduler.check_and_run_monthly_learning()
            except Exception:
                pass
    _monthly_learning_thread = threading.Thread(target=_monthly_learning_loop, daemon=True)
    _monthly_learning_thread.start()

    # Stage 1b paper→live promotion monitor: evaluate GO/NO-GO criteria from closed
    # paper trades, alert (Telegram/email, deduped), run the kill-switch, and — only
    # when explicitly armed — auto-promote to live. Every 30 min (kill-switch responsive).
    def _promotion_monitor_loop():
        time.sleep(180)  # let the first refresh populate trades
        while True:
            # 2026-09-26: the Stage 1b ladder (30 closes, 0.30% expectancy, 0.33% cost
            # stress, live-baseline comparison) is superseded by STRATEGY.md §3/§4. It
            # emailed the owner "Decision: GO" on 2026-09-25 for a cohort the current
            # gates reject — same-day clusters counted as independent trades, cost stress
            # 7x too small, benchmark = the retired live strategy. Asleep with the other
            # legacy weekly machinery; not deleted (the kill-switch half matters once live).
            if not scheduler.weekly_tasks_enabled:
                time.sleep(1800)
                continue
            try:
                from stage1b_promotion import run_promotion_check
                run_promotion_check(send_alert=True, allow_auto=True)
            except Exception:
                pass
            time.sleep(1800)
    _promotion_monitor_thread = threading.Thread(target=_promotion_monitor_loop, daemon=True)
    _promotion_monitor_thread.start()
    logger.info("[OK] Stage 1b promotion monitor enabled: every 30 min (alert + armed auto-promote)")

    # Momentum scan: lightweight 15-min price check for early momentum detection (zero AI cost)
    def _momentum_scan_loop():
        time.sleep(120)  # initial delay to let first full refresh complete
        while True:
            try:
                momentum_scan()
            except Exception:
                pass
            time.sleep(_MOMENTUM_SCAN_INTERVAL)
    _momentum_scan_thread = threading.Thread(target=_momentum_scan_loop, daemon=True)
    _momentum_scan_thread.start()
    logger.info("[OK] Momentum scan enabled: every 15 minutes (zero AI cost)")

    _position_monitor_thread = threading.Thread(target=position_monitor_loop, daemon=True)
    _position_monitor_thread.start()

    # Recursive optimizer — lean mode defaults to weekly (Saturday) at 4 AM ET
    def _optimizer_loop():
        from datetime import datetime as dt, timezone as tz
        import json as _json
        # Seed last_run_date from live_thresholds so app restarts after the 4 AM window
        # don't lose awareness of when the optimizer last ran.
        last_run_date = None
        _run_catchup = False
        try:
            # Determine last successful optimizer run from the log, not live_thresholds.json.
            # live_thresholds.json 'updated' only changes when thresholds are written,
            # which doesn't happen in data-collection/freeze mode — causing false staleness.
            import os as _os
            _log_path = _os.path.join(_os.path.dirname(__file__), 'data', 'optimizer_log.json')
            with open(_log_path) as _olf:
                _opt_log = _json.load(_olf)
            # Find the most recent non-blocked entry
            for _entry in reversed(_opt_log):
                if not _entry.get('safety_gate_blocked'):
                    _last_date_str = _entry.get('snapshot', {}).get('date', '')
                    if _last_date_str:
                        last_run_date = dt.strptime(_last_date_str, '%Y-%m-%d').date()
                    break
            if last_run_date:
                days_stale = (dt.now(tz.utc).date() - last_run_date).days
                if days_stale > 7:
                    _run_catchup = True
                    logger.warning(
                        "  [optimizer] Last successful run is %d days old — scheduling catch-up run",
                        days_stale
                    )
        except Exception:
            pass
        _retry_count = 0
        _MAX_RETRIES = 2
        while True:
            time.sleep(60)
            try:
                from zoneinfo import ZoneInfo
                et_now = dt.now(tz.utc).astimezone(ZoneInfo('America/New_York'))
            except Exception:
                et_now = dt.now(tz.utc)
            # Catch-up bypasses day-of-week (lean mode) but NOT hour-of-day.
            # Without the hour gate, catch-up retries every 60s forever when gate-blocked.
            _in_window = et_now.hour == 4 and (last_run_date != et_now.date() or _run_catchup)
            if _in_window:
                # STRATEGY.md v1.5 (D2): the optimizer/roadmap weekly machinery sleeps with
                # weekly_tasks_enabled — it has no surviving candidate to optimize, and its
                # cycles only generate unapplied recommendations + email noise + API cost.
                # Re-enable deliberately via scheduler_config.json after a new registered
                # hypothesis passes the STRATEGY.md gates.
                if not scheduler.weekly_tasks_enabled:
                    logger.info("  [optimizer] weekly_tasks_enabled=false — optimizer/roadmap cycle asleep (STRATEGY.md D2)")
                    last_run_date = et_now.date()
                    _run_catchup = False
                    continue
                try:
                    if LEAN_PROFIT_FIRST_MODE and et_now.weekday() != 5 and not _run_catchup:
                        logger.info("  [optimizer] Lean mode: skipping non-Saturday run")
                        last_run_date = et_now.date()
                        continue
                    from recursive_optimizer import run_full_recursive_optimizer_cycle
                    _opt_result = run_full_recursive_optimizer_cycle()
                    _gate_blocked = isinstance(_opt_result, dict) and _opt_result.get('safety_gate_blocked')
                    # Roadmap automation evaluation runs on the same cadence as optimizer decisions.
                    if not _gate_blocked:
                        try:
                            from roadmap_automation import run_roadmap_cycle
                            _roadmap = run_roadmap_cycle(dry_run=False)
                            if _roadmap.get('success'):
                                logger.info("  [roadmap] Automation cycle complete (%d transitions, %d pending approvals)",
                                            len(_roadmap.get('applied', [])), len(_roadmap.get('pending_approvals', [])))
                            else:
                                logger.warning("  [roadmap] Automation cycle error: %s", _roadmap.get('error'))
                        except Exception as _roadmap_err:
                            logger.warning("  [roadmap] Automation cycle failed: %s", _roadmap_err)
                        # Run analysis modules gated by roadmap feature flags
                        try:
                            from roadmap_dispatcher import run_roadmap_gated_analyses
                            _gated = run_roadmap_gated_analyses()
                            for _mod, _res in _gated.items():
                                if isinstance(_res, dict) and _res.get('success'):
                                    logger.info("  [roadmap-gated] %s: success", _mod)
                                elif isinstance(_res, dict):
                                    logger.warning("  [roadmap-gated] %s: %s", _mod,
                                                   _res.get('error') or _res.get('reason', 'failed'))
                        except Exception as _gated_err:
                            logger.warning("  [roadmap-gated] Dispatch failed: %s", _gated_err)
                    last_run_date = et_now.date()
                    _retry_count = 0
                    _run_catchup = False
                    if _gate_blocked:
                        logger.warning("  [optimizer] Safety gate blocked — fix failing tests and redeploy")
                    elif isinstance(_opt_result, dict) and _opt_result.get('error'):
                        logger.warning("  [optimizer] Cycle finished with error: %s", _opt_result.get('error'))
                    else:
                        logger.info("  [optimizer] Recursive optimization cycle complete")
                except Exception as e:
                    _retry_count += 1
                    logger.error(f"  [optimizer] recursive_optimizer error (attempt {_retry_count}/{_MAX_RETRIES + 1}): {e}")
                    if _retry_count > _MAX_RETRIES:
                        last_run_date = et_now.date()
                        _retry_count = 0
                        logger.error("  [optimizer] Max retries exceeded, skipping this run window")
    _optimizer_thread = threading.Thread(target=_optimizer_loop, daemon=True)
    _optimizer_thread.start()
    if LEAN_PROFIT_FIRST_MODE:
        logger.info("[OK] Recursive optimizer enabled: Saturday 4:00 AM ET (lean mode)")
    else:
        logger.info("[OK] Recursive optimizer enabled: daily at 4:00 AM ET")

    # Weekly ML pipeline — Saturday 2 AM ET (1h before replay, 2h before optimizer)
    # Refreshes learning_report.json so optimizer always reads fresh missed-opportunity data.
    def _weekly_ml_loop():
        from datetime import datetime as dt, timezone as tz
        last_ml_week = None
        while True:
            time.sleep(300)
            try:
                from zoneinfo import ZoneInfo
                et_now = dt.now(tz.utc).astimezone(ZoneInfo('America/New_York'))
            except Exception:
                et_now = dt.now(tz.utc)
            current_week = et_now.strftime('%G-W%V')
            # Gated on weekly_tasks_enabled (STRATEGY.md D2): this pipeline learns from a
            # retired strategy's trades and nothing reads its output. Found still running
            # ungated on 2026-09-26 (learning_report.json rewritten every Saturday 02:00).
            if (scheduler.weekly_tasks_enabled and et_now.weekday() == 5 and et_now.hour == 2
                    and last_ml_week != current_week):
                try:
                    logger.info("[ml] Starting weekly ML pipeline (backfill, missed-opps, suggestions)...")
                    _run_ml_pipeline()
                    last_ml_week = current_week
                    logger.info("[ml] Weekly ML pipeline complete — learning_report.json refreshed")
                except Exception as e:
                    last_ml_week = current_week  # Don't retry on failure — wait til next week
                    logger.error(f"[ml] Weekly ML pipeline failed: {e}")
    _weekly_ml_thread = threading.Thread(target=_weekly_ml_loop, daemon=True)
    _weekly_ml_thread.start()
    logger.info("[OK] Weekly ML pipeline enabled: Saturday 2:00 AM ET")

    # Weekly historical replay — Saturday 3 AM ET (1h before optimizer, so fresh backtest data is ready)
    def _weekly_replay_loop():
        from datetime import datetime as dt, timezone as tz
        last_replay_week = None
        while True:
            time.sleep(300)  # Check every 5 min
            try:
                from zoneinfo import ZoneInfo
                et_now = dt.now(tz.utc).astimezone(ZoneInfo('America/New_York'))
            except Exception:
                et_now = dt.now(tz.utc)
            # Saturday (weekday=5), 3 AM ET
            current_week = et_now.strftime('%G-W%V')
            # Gated on weekly_tasks_enabled (STRATEGY.md D2). Found still running ungated
            # on 2026-09-26: 15 coins x 90d hourly from CoinGecko every Saturday 03:00, plus
            # reset_breaker('coingecko') — which erases the outage signal the health
            # check depends on — to feed a replay of the retired pipeline.
            if (scheduler.weekly_tasks_enabled and et_now.weekday() == 5 and et_now.hour == 3
                    and last_replay_week != current_week):
                try:
                    logger.info("[replay] Starting weekly historical replay (90d, 15 coins, 8h intervals)...")
                    from historical_replay import run_historical_replay
                    from resilient_http import reset_breaker
                    reset_breaker('coingecko')  # Clear any stale circuit breaker state
                    result = run_historical_replay()
                    last_replay_week = current_week
                    trades = result.get('signals_generated', 0)
                    missed = len(result.get('missed_opportunities', []))
                    wr = result.get('summary', {}).get('win_rate_pct', 0)
                    logger.info(f"[replay] Weekly replay complete: {trades} trades ({wr:.1f}% WR), {missed} missed opportunities")
                    # Run adversarial backtester on fresh replay data (baseline-only for speed)
                    try:
                        from scripts.adversarial_backtest import run_adversarial_analysis
                        adv_report = run_adversarial_analysis(baseline_only=True)
                        n_issues = len(adv_report.get('structural_issues', []))
                        n_qw = len(adv_report.get('quick_wins', []))
                        logger.info(f"[adversarial] Analysis complete: {n_issues} issues, {n_qw} quick wins")
                    except Exception as adv_e:
                        logger.error(f"[adversarial] Adversarial analysis failed: {adv_e}")
                except Exception as e:
                    last_replay_week = current_week  # Don't retry on failure — wait til next week
                    logger.error(f"[replay] Weekly historical replay failed: {e}")
    _replay_thread = threading.Thread(target=_weekly_replay_loop, daemon=True)
    _replay_thread.start()
    logger.info("[OK] Weekly historical replay enabled: Saturday 3:00 AM ET")

    # Initial data load in background (so dashboard has data when user opens)
    def _initial_refresh():
        time.sleep(0.5)  # Brief pause for server to accept connections
        try:
            refresh_data()
            logger.info("  [OK] Initial data load complete")
        except Exception as e:
            logger.info(f"  [WARN] Initial data load failed: {e}")
            traceback.print_exc()
    threading.Thread(target=_initial_refresh, daemon=True).start()

# Alert system status
try:
    alert_settings = alerts.get_settings()
    enabled = []
    if alert_settings.get('email_enabled'):
        enabled.append('Email')
    if alert_settings.get('telegram_enabled'):
        enabled.append('Telegram')
    
    if enabled:
        logger.info(f"✅ Alert system ready ({', '.join(enabled)})")
    else:
        logger.warning("⚠️  Alert channels disabled (enable with: alerts.enable_email() / alerts.enable_telegram())")
except Exception as e:
    logger.warning(f"⚠️  Alert system error: {e}")

logger.info(f"\n{'=' * 80}")
logger.info(f"  ✅  Elite Dashboard Ready!")
logger.info(f"  Instance: {os.getpid()}-{APP_STARTED_AT}  (Runtime resets on each restart)")
try:
    _ec_boot = get_execution_config()
    if not _ec_boot.get('auto_execution_enabled'):
        logger.warning(
            "  ⚠️  Auto-execution is OFF on disk (%s). Refresh only reads this; it does not disable auto-exec. "
            "If you did not turn it off: check data/execution_config_audit.jsonl for API writes, git/sync, or a second workspace copy.",
            EXECUTION_CONFIG_FILE,
        )
except Exception as _ec_boot_err:
    logger.debug("execution_config boot check skipped: %s", _ec_boot_err)
logger.info(f"  👉  Open: http://localhost:5000")
logger.info(f"{'=' * 80}\n")


# =============================================================================
# Routes — Core Dashboard
# =============================================================================

@app.route('/')
def index():
    resp = app.make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp


def _safe_data_response():
    """Build API response with safe defaults for all new fields."""
    with _cache_lock:
        cd = cached_data or {}
        lu = last_updated
        rc = refresh_count
    perf = cd.get('performance_analytics') or {}
    perf_safe = {
        'sharpe_ratio': perf.get('sharpe_ratio', 0),
        'sortino_ratio': perf.get('sortino_ratio', 0),
        'max_drawdown_pct': perf.get('max_drawdown_pct', 0),
        'expectancy_pct': perf.get('expectancy_pct', 0),
        'expectancy_usd': perf.get('expectancy_usd', 0),
        'win_rate_pct': perf.get('win_rate_pct', 0),
        'total_pnl_usd': perf.get('total_pnl_usd', 0),
        'trades': perf.get('trades', 0),
        'win_rate_by_regime': perf.get('win_rate_by_regime', {}),
        'win_rate_by_regime_scenario': perf.get('win_rate_by_regime_scenario', {}),
    }
    pr = cd.get('portfolio_risk_summary') or {}
    pr_safe = {
        'total_risk_pct': pr.get('total_risk_pct', 0),
        'open_exposure_pct': pr.get('open_exposure_pct', 0),
        'correlated_risk_pct': pr.get('correlated_risk_pct', 0),
        'concentration_limit_pct': pr.get('concentration_limit_pct', 5),
        'max_single_position_pct': pr.get('max_single_position_pct', 0),
        'var_1d_usd': pr.get('var_1d_usd', 0),
        'concentration_breaches': pr.get('concentration_breaches', 0),
        'high_corr_count': pr.get('high_corr_count', 0),
        'correlation_cap_pct': pr.get('correlation_cap_pct', 35),
        'risk_cap_pct': pr.get('risk_cap_pct', 6),
        'suggestions': pr.get('suggestions', []),
    }
    ma = cd.get('model_attribution') or {}
    ma_safe = {
        'by_model': ma.get('by_model') if isinstance(ma.get('by_model'), dict) else {},
        'monthly_ranking': ma.get('monthly_ranking') if isinstance(ma.get('monthly_ranking'), list) else [],
        'recommendations': ma.get('recommendations') if isinstance(ma.get('recommendations'), list) else [],
    }
    out = dict(cd)
    out['performance_analytics'] = perf_safe
    out['portfolio_risk_summary'] = pr_safe
    out['model_attribution'] = ma_safe
    out['summary'] = cd.get('summary') or {'total': 0, 'buy': 0, 'hold': 0, 'sell': 0}
    out['regime'] = cd.get('regime') or {'regime': 'UNKNOWN', 'confidence': 0}
    out['fear_greed'] = cd.get('fear_greed') or {}
    macro = cd.get('macro') or {}
    if not _macro_has_any_value(macro):
        try:
            macro = fetcher.get_macro_data()
        except Exception:
            pass
    out['macro'] = macro
    out['sentiment'] = cd.get('sentiment') or {}
    coins_raw = cd.get('coins') or []
    out['coins'] = coins_raw if isinstance(coins_raw, list) else []
    out['onchain_status'] = cd.get('onchain_status') or 'fallback'
    out['onchain_overview'] = cd.get('onchain_overview') or {}
    out['global_market_cap'] = cd.get('global_market_cap') or {}
    out['signal_30d'] = cd.get('signal_30d')
    out['ai_30d'] = cd.get('ai_30d')
    if out['signal_30d'] is None or out['ai_30d'] is None:
        try:
            from signal_history import get_30d_aggregates
            agg = get_30d_aggregates()
            if out['signal_30d'] is None:
                out['signal_30d'] = agg.get('signal_30d') or {}
            if out['ai_30d'] is None:
                out['ai_30d'] = agg.get('ai_30d') or {}
        except Exception:
            if out['signal_30d'] is None:
                out['signal_30d'] = {}
            if out['ai_30d'] is None:
                out['ai_30d'] = {}
    out['account_total_cash'] = float(cd.get('account_total_cash', 0) or 0)  # Risk-budget denominator (matches open_exposure_pct)
    out['last_updated'] = lu
    out['refresh_count'] = rc
    out['test_mode'] = get_test_mode()
    out['paper_trading'] = get_paper_trading()
    out['paper_trade_notional_usd'] = get_paper_trade_notional_usd()
    out['server_started_at'] = APP_STARTED_AT
    out['server_instance_id'] = f"{os.getpid()}-{APP_STARTED_AT}"
    return out


@app.route('/api/data')
def get_data():
    """Main data endpoint with regime info"""
    return jsonify(_safe_data_response())


@app.route('/api/momentum-scan', methods=['POST'])
def manual_momentum_scan():
    """Trigger a manual momentum scan (lightweight, no AI cost)."""
    try:
        guard = require_admin_write_access()
        if guard is not None:
            return guard
        threading.Thread(target=momentum_scan, daemon=True).start()
        return jsonify({'status': 'ok', 'message': 'Momentum scan started'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/refresh', methods=['POST'])
def manual_refresh():
    """Manual refresh trigger"""
    try:
        guard = require_admin_write_access()
        if guard is not None:
            return guard
        refresh_result = refresh_data()
        with _cache_lock:
            lu = last_updated
            summary = (cached_data or {}).get('summary', {})
            regime = (cached_data or {}).get('regime', {})
        if refresh_result == 'skipped':
            return jsonify({
                'status': 'skipped',
                'message': 'Refresh skipped because another refresh is in progress',
                'last_updated': lu,
                'summary': summary,
                'regime': regime,
            })
        return jsonify({
            'status': 'ok',
            'last_updated': lu,
            'summary': summary,
            'regime': regime,
        })
    except Exception as e:
        logger.error(f"Error during manual refresh: {e}")
        traceback.print_exc()
        with _cache_lock:
            lu = last_updated
            summary = (cached_data or {}).get('summary', {})
            regime = (cached_data or {}).get('regime', {})
        return jsonify({
            'status': 'error',
            'error': str(e),
            'last_updated': lu,
            'summary': summary,
            'regime': regime,
        }), 500



# =============================================================================
# Routes — System Health
# =============================================================================

@app.route('/api/health')
def get_health():
    """Get data source health status + API pipeline resilience metrics"""
    try:
        from resilient_http import api_health_report, get_degraded_sources

        total = len(data_source_status)
        ok = sum(1 for s in data_source_status.values() if s['status'] == 'ok')
        errors = sum(1 for s in data_source_status.values() if s['status'] == 'error')
        warnings = sum(1 for s in data_source_status.values() if s['status'] == 'warning')

        with _cache_lock:
            lu = last_updated
        stale = fetcher.get_stale_sources() if hasattr(fetcher, 'get_stale_sources') else {}
        return jsonify({
            'sources': data_source_status,
            'summary': {
                'total': total,
                'ok': ok,
                'errors': errors,
                'warnings': warnings
            },
            'last_updated': lu or 'Never',
            'api_pipeline': api_health_report(),
            'degraded_sources': get_degraded_sources(),
            'stale_sources': stale,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Routes — Market Data
# =============================================================================

@app.route('/api/regime')
def get_regime():
    """Get current market regime"""
    with _cache_lock:
        cr = current_regime
    return jsonify(cr or {'regime': 'UNKNOWN'})


@app.route('/api/global-market-cap')
def get_global_market_cap_api():
    """Global market cap (CoinPaprika). Used by frontend overview tile. Returns CoinGecko-compatible format."""
    gmc = fetcher.get_global_market_cap()
    if gmc:
        # Frontend expects { data: { total_market_cap: {usd}, market_cap_change_percentage_*_usd } }
        data = {
            'total_market_cap': {'usd': gmc.get('total_market_cap_usd', 0)},
            'market_cap_change_percentage_24h_usd': gmc.get('market_cap_change_percentage_24h_usd'),
            'market_cap_change_percentage_7d_usd': gmc.get('market_cap_change_percentage_7d_usd'),
            'market_cap_change_percentage_30d_usd': gmc.get('market_cap_change_percentage_30d_usd'),
        }
        return jsonify({'data': data})
    return jsonify({'data': {}})


@app.route('/api/coin/<coin_id>')
def get_coin(coin_id):
    """Get single coin data. Supports CoinGecko ids (bitcoin) and symbols (e.g. btc)."""
    with _cache_lock:
        coins = (cached_data or {}).get('coins', [])
    # Direct match by id or symbol
    coin = next((c for c in coins if c.get('id') == coin_id or (c.get('symbol') or '').upper() == (coin_id or '').upper()), None)
    # Backward compat: map CoinGecko id to symbol (e.g. bitcoin -> BTC)
    if not coin and coin_id:
        from enhanced_data_fetcher import COINGECKO_ID_TO_SYMBOL
        sym = COINGECKO_ID_TO_SYMBOL.get(str(coin_id).lower())
        if sym:
            coin = next((c for c in coins if (c.get('symbol') or '').upper() == sym), None)
    if coin:
        return jsonify(coin)
    return jsonify({'error': 'Coin not found'}), 404


def _macro_has_any_value(macro):
    """True if macro dict has at least one non-null value (for lazy FRED load)."""
    if not macro or not isinstance(macro, dict):
        return False
    for key in ('fed_rate', 'cpi', 't10y', 'm2'):
        item = macro.get(key)
        if isinstance(item, dict) and item.get('value') is not None:
            return True
    return False


@app.route('/api/summary')
def get_summary():
    """Dashboard summary (overview tiles: regime, sentiment, onchain, macro, portfolio risk, etc.)"""
    with _cache_lock:
        cd = cached_data or {}
        lu = last_updated
    # Lazy-load FRED macro if cache has none (e.g. before first refresh or after FRED glitch)
    macro = cd.get('macro') or {}
    if not _macro_has_any_value(macro):
        try:
            macro = fetcher.get_macro_data()
        except Exception as e:
            logger.debug("Lazy FRED fetch failed: %s", e)
    signal_30d = cd.get('signal_30d')
    ai_30d = cd.get('ai_30d')
    if signal_30d is None or ai_30d is None:
        try:
            from signal_history import get_30d_aggregates
            agg = get_30d_aggregates()
            signal_30d = signal_30d if signal_30d is not None else agg.get('signal_30d', {})
            ai_30d = ai_30d if ai_30d is not None else agg.get('ai_30d', {})
        except Exception:
            signal_30d = signal_30d or {}
            ai_30d = ai_30d or {}
    return jsonify({
        'summary': cd.get('summary', {}),
        'fear_greed': cd.get('fear_greed', {}),
        'macro': macro,
        'sentiment': cd.get('sentiment', {}),
        'regime': cd.get('regime', {}),
        'onchain_status': cd.get('onchain_status', 'fallback'),
        'onchain_overview': cd.get('onchain_overview', {}),
        'global_market_cap': cd.get('global_market_cap', {}),
        'portfolio_risk_summary': cd.get('portfolio_risk_summary', {}),
        'signal_30d': signal_30d,
        'ai_30d': ai_30d,
        'test_mode': get_test_mode(),
        'paper_trading': get_paper_trading(),
        'paper_trade_notional_usd': get_paper_trade_notional_usd(),
        'last_updated': lu,
    })


@app.route('/api/news-sentiment-articles')
def get_news_sentiment_articles():
    """JSON list of the 50 articles used for news sentiment (url, title, score)."""
    articles = fetcher.get_last_sentiment_feed()
    return jsonify(articles)


@app.route('/news-sentiment-articles')
def news_sentiment_articles_page():
    """HTML page listing the 50 news sentiment articles with links."""
    articles = fetcher.get_last_sentiment_feed()
    return render_template('news_sentiment_articles.html', articles=articles)


# =============================================================================
# Routes — Position Sizing
# =============================================================================

@app.route('/api/position-size/calculate', methods=['POST'])
def calculate_position_size():
    """
    Calculate optimal position size.
    POST body: {entry_price, stop_loss_pct, consensus_signal}
    """
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        entry_price = data.get('entry_price')
        stop_loss_pct = data.get('stop_loss_pct')
        consensus_signal = data.get('consensus_signal', 'MODERATE_BUY')
        coin_volatility = data.get('volatility', 50)

        if entry_price is None or stop_loss_pct is None:
            return jsonify({'error': 'entry_price and stop_loss_pct required'}), 400
        try:
            entry_price_f = float(entry_price)
            stop_loss_pct_f = float(stop_loss_pct)
            coin_volatility_f = float(coin_volatility)
        except (TypeError, ValueError):
            return jsonify({'error': 'entry_price, stop_loss_pct, and volatility must be numbers'}), 400
        if entry_price_f <= 0:
            return jsonify({'error': 'entry_price must be > 0'}), 400
        if stop_loss_pct_f <= 0 or stop_loss_pct_f >= 100:
            return jsonify({'error': 'stop_loss_pct must be strictly between 0 and 100'}), 400
        if coin_volatility_f < 0:
            return jsonify({'error': 'volatility must be >= 0'}), 400

        overview = _build_account_overview()
        cash_available = overview.get('cash_available_to_trade')
        # regime_data must be a dict (position_sizer calls .get() on it); current_regime can be overwritten as str
        with _cache_lock:
            cr = current_regime
        regime_data = cr if isinstance(cr, dict) else {'regime': cr or 'SIDEWAYS'}
        result = position_sizer.calculate_for_consensus(
            entry_price=entry_price_f,
            stop_loss_pct=stop_loss_pct_f,
            consensus_signal=consensus_signal,
            regime_data=regime_data,
            coin_volatility=coin_volatility_f,
            cash_available=cash_available,
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error calculating position size: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _build_daily_status_summary():
    """Build HTML summary for daily status report (regime, on-chain, sync, cash, positions, drift, warnings).
    Includes daily performance (today's realized PnL), open positions snapshot, and total unrealized PnL."""
    global cached_data
    ov = _build_account_overview()
    regime_data = cached_data.get('regime') or cached_data.get('regime_data') or {}
    regime = (regime_data.get('regime') or 'Unknown')
    confidence = regime_data.get('confidence', 0)
    onchain_status = cached_data.get('onchain_status', 'Unknown')
    last_sync = ov.get('last_auto_sync_at') or 'Never'
    total_cash = ov.get('total_cash', 0) or 0
    cash_available = ov.get('cash_available_to_trade', 0) or 0
    open_positions = get_open_positions(exclude_test=True)
    positions_count = len(open_positions)
    cost_basis = ov.get('open_positions_cost_basis', 0) or 0
    market_value = ov.get('open_positions_market_value', 0) or 0
    unrealized_usd = round((market_value - cost_basis), 2)
    unrealized_pct = round((unrealized_usd / cost_basis * 100), 2) if cost_basis and cost_basis > 0 else 0.0
    exec_cfg = get_execution_config()
    acct_cfg = load_account_config()
    auto_book = 'ON' if exec_cfg.get('auto_execution_enabled', True) else 'OFF'
    test_mode = get_test_mode()
    paper_block = ''
    try:
        cutoff_30p = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')
        po_n = len([p for p in get_open_positions(exclude_test=False) if is_paper_trade(p)])
        pc_30 = [
            p for p in get_all_positions()
            if p.get('source') == 'paper_trade'
            and (p.get('status') or '').upper() == 'CLOSED'
            and (p.get('exit_time') or '')[:10] >= cutoff_30p
        ]
        wr_p = round(sum(1 for p in pc_30 if p.get('outcome') == 'win') / len(pc_30) * 100, 1) if pc_30 else 0.0
        pnl_p = round(sum(float(p.get('pnl_usd') or 0) for p in pc_30), 2)
        paper_block = f"""
    <div style="border-left: 4px solid #ca8a04; padding: 12px 16px; margin-bottom: 16px; background: #fefce8; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #a16207; margin-bottom: 8px;">Paper trading (simulated — not live P&amp;L)</div>
      <div style="font-size: 14px;">Paper mode: <strong>{'On' if get_paper_trading() else 'Off'}</strong> &nbsp;|&nbsp; Open paper: <strong>{po_n}</strong></div>
      <div style="font-size: 13px; margin-top: 6px; color: #713f12;">Last 30d closed: <strong>{len(pc_30)}</strong> &nbsp;|&nbsp; Win rate: <strong>{wr_p}%</strong> &nbsp;|&nbsp; Net P&amp;L: <strong>${pnl_p:+,.2f}</strong></div>
      <div style="font-size: 11px; color: #78716c; margin-top: 4px;">Notional ${get_paper_trade_notional_usd():,.0f}/trade — separate from live headline metrics</div>
    </div>"""
    except Exception:
        paper_block = ''
    stale_on = exec_cfg.get('stale_position_rule_enabled', True)
    total_account_value_ov = float(ov.get('total_account_value', 0) or 0)
    risk_pct = float(ov.get('risk_pct', 0) or 0)
    max_risk_usd = float(ov.get('max_risk_usd_per_trade', 0) or 0)
    ref_cost_basis = float(ov.get('reference_cost_basis_per_trade', 0) or 0)
    ref_stop_pct = float(ov.get('reference_stop_pct', 0) or 0)
    max_cash_pct = float(ov.get('max_cash_usage_pct', 40) or 40)
    max_open_total = int(ov.get('max_open_total', 8) or 8)
    deployed_pct = (market_value / total_account_value_ov * 100) if total_account_value_ov and total_account_value_ov > 0 else 0
    heloc_mode = bool(ov.get('heloc_mode', False))
    heloc_notional = float(ov.get('heloc_notional', 0) or 0)
    heloc_drawn = float(ov.get('heloc_drawn', 0) or 0)
    drift_status = 'OK'
    # Daily performance: today's realized PnL (closed trades that closed today ET)
    daily_pnl_usd = 0.0
    try:
        from zoneinfo import ZoneInfo
        et_now = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
        today_et = et_now.date()
        for p in get_all_positions():
            if p.get('status') != 'CLOSED':
                continue
            if p.get('is_test'):
                continue
            exit_str = (p.get('exit_time') or '').strip()
            if not exit_str or len(exit_str) < 10:
                continue
            try:
                exit_naive = datetime.strptime(exit_str[:19], '%Y-%m-%d %H:%M:%S')
                exit_utc = exit_naive.replace(tzinfo=timezone.utc)
                exit_et = exit_utc.astimezone(ZoneInfo('America/New_York'))
                if exit_et.date() == today_et:
                    daily_pnl_usd += float(p.get('pnl_usd') or 0)
            except Exception:
                pass
        daily_pnl_usd = round(daily_pnl_usd, 2)
    except Exception:
        pass
    total_account_value = float(ov.get('total_account_value', 0) or 0)
    daily_pnl_pct = (daily_pnl_usd / total_account_value * 100) if total_account_value and total_account_value > 0 else None
    # Price map for open positions: cached_data first, then fetch missing so unrealized $ are never blank
    price_by_id = {c['id']: float(c.get('current_price', 0) or 0) for c in (cached_data.get('coins') or []) if c.get('id') and c.get('current_price') is not None}
    if open_positions:
        missing_ids = [p.get('coin_id') for p in open_positions if p.get('coin_id') and price_by_id.get(p.get('coin_id')) is None]
        if missing_ids:
            try:
                extra = fetcher.get_current_prices_fast(list(set(missing_ids)), max_coins_cache_age_sec=0)
                if extra:
                    for cid, px in extra.items():
                        if cid and px is not None and float(px) > 0:
                            price_by_id[cid] = float(px)
            except Exception:
                pass
    warnings = []
    if total_cash <= 0:
        warnings.append('No cash reported')
    warnings_str = ', '.join(warnings) if warnings else 'None'
    try:
        from zoneinfo import ZoneInfo
        date_str = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    except Exception:
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    roadmap_pending: list[str] = []
    try:
        from roadmap_automation import get_roadmap_automation_status
        _ra = get_roadmap_automation_status() or {}
        _pending = _ra.get('pending_approvals') or []
        roadmap_pending = [str(p.get('feature_id')) for p in _pending if isinstance(p, dict) and p.get('status') == 'pending_approval']
    except Exception:
        roadmap_pending = []
    # Portfolio risk cap (for email when in place)
    # NOTE: must match dashboard denominator (_effective_account_size_for_risk)
    # and include coins_with_recommendations + exclude test closed trades for VaR.
    # Always compute pr from current account size so the displayed cap matches dashboard/execution.
    risk_cap_pct = None
    total_risk_pct = None
    open_exposure_pct = None
    var_1d_usd = None
    try:
        account_size = _effective_account_size_for_risk() or float(total_cash or 0)
        if account_size > 0:
            coins_with_recs = [c for c in (cached_data.get('coins') or []) if (c.get('ai_recommendation') or {}).get('position_sizing')]
            closed_for_var = [p for p in get_all_positions() if p.get('status') == 'CLOSED' and not (p.get('is_test') or p.get('exit_reason') == 'test_cleanup')]
            pr = get_portfolio_risk_summary(open_positions, coins_with_recs, account_size, closed_positions=closed_for_var)
        else:
            pr = cached_data.get('portfolio_risk_summary') or {}
        risk_cap_pct = pr.get('risk_cap_pct')
        total_risk_pct = pr.get('total_risk_pct')
        open_exposure_pct = pr.get('open_exposure_pct')
        var_1d_usd = pr.get('var_1d_usd')
    except Exception:
        pass
    # Match dashboard: Total Exposure card uses open_exposure_pct (open positions only), not total_risk_pct (includes pending BUYs)
    display_risk_pct = open_exposure_pct if open_exposure_pct is not None else total_risk_pct
    # Card-based layout with gradient header and accent sections (light theme)
    html = f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #f8fafc; color: #1e293b; padding: 20px;">
  <div style="background: linear-gradient(135deg, #0d9488 0%, #0f766e 100%); color: #fff; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
    <div style="font-weight: bold; font-size: 20px;">Status Report</div>
    <div style="font-size: 14px; margin-top: 8px; opacity: 0.95;">{date_str}</div>
  </div>
  <div style="background: #fff; padding: 16px; margin-top: 0; border: 1px solid #e2e8f0; border-top: none; border-radius: 0 0 8px 8px;">
    <div style="display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px;">
      <div style="flex: 1; min-width: 120px; padding: 12px; background: #f0fdfa; border-radius: 6px; text-align: center;">
        <div style="font-size: 11px; color: #64748b; text-transform: uppercase;">Regime</div>
        <div style="font-size: 16px; font-weight: 600;">{regime}</div>
        <div style="font-size: 12px; color: #64748b;">{confidence}% confidence</div>
      </div>
      <div style="flex: 1; min-width: 120px; padding: 12px; background: #f0fdfa; border-radius: 6px; text-align: center;">
        <div style="font-size: 11px; color: #64748b; text-transform: uppercase;">Open Positions</div>
        <div style="font-size: 16px; font-weight: 600;">{positions_count}</div>
        <div style="font-size: 12px; color: #64748b;">${cost_basis:,.2f} cost basis</div>
      </div>
      <div style="flex: 1; min-width: 120px; padding: 12px; background: #f0fdfa; border-radius: 6px; text-align: center;">
        <div style="font-size: 11px; color: #64748b; text-transform: uppercase;">System Health</div>
        <div style="font-size: 16px; font-weight: 600;">{drift_status}</div>
        <div style="font-size: 12px; color: #64748b;">On-chain: {onchain_status}</div>
      </div>
    </div>
    <div style="border-left: 4px solid #059669; padding: 12px 16px; margin-bottom: 16px; background: #ecfdf5; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #059669; margin-bottom: 8px;">📈 Daily Performance (today)</div>
      <div style="font-size: 14px;">Realized P&L: <strong style="color: {'#0d9488' if daily_pnl_usd >= 0 else '#dc2626'};">${daily_pnl_usd:+,.2f}</strong>{f' ({daily_pnl_pct:+.2f}% of account)' if daily_pnl_pct is not None else ''}</div>
    </div>
    {paper_block}
    <div style="border-left: 4px solid #1e40af; padding: 12px 16px; margin-bottom: 16px; background: #eff6ff; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #1e40af; margin-bottom: 8px;">Account Overview</div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        <tr><td style="padding: 4px 0; color: #64748b;">Total Account Value</td><td style="padding: 4px 0; text-align: right; font-weight: 600;">${total_account_value_ov:,.2f}</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Cash Balance</td><td style="padding: 4px 0; text-align: right;">${total_cash:,.2f}</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Cash available to trade</td><td style="padding: 4px 0; text-align: right;">${cash_available:,.2f}</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Risk Parameter %</td><td style="padding: 4px 0; text-align: right;">{risk_pct:.1f}%</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Risk per Trade</td><td style="padding: 4px 0; text-align: right;">${max_risk_usd:,.2f}</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Est. Next Trade Size</td><td style="padding: 4px 0; text-align: right;">${ref_cost_basis:,.2f} (at {ref_stop_pct:.1f}% stop)</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Max Cash Per Trade %</td><td style="padding: 4px 0; text-align: right;">{max_cash_pct:.0f}%</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Open Positions</td><td style="padding: 4px 0; text-align: right;">{positions_count} / {max_open_total} max</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Deployed capital</td><td style="padding: 4px 0; text-align: right;">${market_value:,.2f} ({deployed_pct:.0f}%)</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Exit strategy</td><td style="padding: 4px 0; text-align: right;"><strong>Winners run (trailing only)</strong></td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Last sync</td><td style="padding: 4px 0; text-align: right;">{last_sync}</td></tr>
      </table>
      {f'<div style="font-size: 13px; margin-top: 8px; color: #64748b;">HELOC: ${heloc_drawn:,.0f} / ${heloc_notional:,.0f} drawn</div>' if heloc_mode and heloc_notional > 0 else ''}
    </div>
    <div style="border-left: 4px solid #7c3aed; padding: 12px 16px; margin-bottom: 16px; background: #f5f3ff; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #7c3aed; margin-bottom: 8px;">System Controls</div>
      <div style="font-size: 14px;">Auto-Exec: <strong>{auto_book}</strong> &nbsp;|&nbsp; Test Mode: <strong>{'On' if test_mode else 'Off'}</strong> &nbsp;|&nbsp; 14d sell: <strong>{'On' if stale_on else 'Off'}</strong> &nbsp;|&nbsp; Exit: <strong>Winners run</strong></div>
      <div style="font-size: 12px; color: #64748b; margin-top: 4px;">Monitor: active</div>
    </div>
"""
    # Portfolio risk cap (when available); use display_risk_pct to match dashboard Total Exposure card
    if risk_cap_pct is not None and display_risk_pct is not None:
        risk_ok = display_risk_pct <= (risk_cap_pct or 999)
        risk_color = '#059669' if risk_ok else '#dc2626'
        risk_line = f"Portfolio risk: <strong style='color: {risk_color};'>{display_risk_pct:.1f}%</strong> (cap {risk_cap_pct:.0f}%)"
        if var_1d_usd is not None and var_1d_usd > 0:
            risk_line += f" &nbsp;|&nbsp; VaR (95% 1d): ${var_1d_usd:,.2f}"
        html += f"""
    <div style="border-left: 4px solid #b45309; padding: 12px 16px; margin-bottom: 16px; background: #fffbeb; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #b45309; margin-bottom: 8px;">Portfolio Risk Cap</div>
      <div style="font-size: 14px;">{risk_line}</div>
    </div>
"""
    # Open positions snapshot + total unrealized PnL
    unreal_color = '#0d9488' if unrealized_usd >= 0 else '#dc2626'
    html += f"""
    <div style="border-left: 4px solid #0369a1; padding: 12px 16px; margin-bottom: 16px; background: #f0f9ff; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #0369a1; margin-bottom: 8px;">🟢 Open Positions & Unrealized P&L</div>
      <div style="font-size: 14px; margin-bottom: 10px;">Total unrealized: <strong style="color: {unreal_color};">${unrealized_usd:+,.2f}</strong> ({unrealized_pct:+.2f}%)</div>
      <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
        <thead><tr style="border-bottom: 1px solid #e2e8f0;">
          <th style="text-align: left; padding: 6px 8px;">Symbol</th>
          <th style="text-align: right; padding: 6px 8px;">Entry</th>
          <th style="text-align: right; padding: 6px 8px;">Current</th>
          <th style="text-align: right; padding: 6px 8px;">Cost</th>
          <th style="text-align: right; padding: 6px 8px;">Unrealized $</th>
          <th style="text-align: right; padding: 6px 8px;">Unrealized %</th>
        </tr></thead>
        <tbody>"""
    def _fmt_price(x):
        if x is None or (isinstance(x, float) and (x != x)):  # NaN
            return '—'
        x = float(x)
        if x >= 1:
            return f'{x:,.2f}'
        if x >= 0.0001:
            return f'{x:.4f}'
        return f'{x:.2e}' if x != 0 else '0'
    if open_positions:
        for p in open_positions:
            sym = (p.get('coin_symbol') or p.get('coin_id') or '—').upper()
            entry = float(p.get('entry_price') or 0)
            qty = float(p.get('quantity') or 0)
            cap = float(p.get('capital_usd') or 0)
            cost = cap if cap > 0 else (entry * qty)
            current_price = price_by_id.get(p.get('coin_id', '')) or entry
            mv = qty * current_price if (qty and current_price) else 0
            try:
                u_usd = round(mv - cost, 2)
                u_pct = round((u_usd / cost * 100), 2) if cost and cost > 0 else 0
            except Exception:
                u_usd = 0
                u_pct = 0
            u_color = '#0d9488' if u_usd >= 0 else '#dc2626'
            u_usd_str = f'${u_usd:+,.2f}'
            u_pct_str = f'{u_pct:+.2f}%'
            html += f"""
        <tr style="border-bottom: 1px solid #f1f5f9;">
          <td style="padding: 6px 8px;">{sym}</td>
          <td style="text-align: right; padding: 6px 8px;">{_fmt_price(entry)}</td>
          <td style="text-align: right; padding: 6px 8px;">{_fmt_price(current_price)}</td>
          <td style="text-align: right; padding: 6px 8px;">${cost:,.2f}</td>
          <td style="text-align: right; padding: 6px 8px; color: {u_color};">{u_usd_str}</td>
          <td style="text-align: right; padding: 6px 8px; color: {u_color};">{u_pct_str}</td>
        </tr>"""
    else:
        html += """
        <tr><td colspan="6" style="padding: 8px; color: #64748b;">No open positions</td></tr>"""
    html += """
        </tbody>
      </table>
    </div>
"""
    if warnings:
        html += f"""
    <div style="border-left: 4px solid #dc2626; padding: 12px 16px; margin-bottom: 16px; background: #fef2f2; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #dc2626; margin-bottom: 8px;">Active Warnings</div>
      <div style="font-size: 14px;">{warnings_str}</div>
    </div>
"""
    if roadmap_pending:
        pending_html = ", ".join(roadmap_pending)
        html += f"""
    <div style="border-left: 4px solid #dc2626; padding: 12px 16px; margin-bottom: 16px; background: #fef2f2; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #dc2626; margin-bottom: 8px;">Roadmap Approvals Required</div>
      <div style="font-size: 14px;">
        High-risk roadmap features pending approval: <strong>{pending_html}</strong>.
        Review in Roadmap tab before activation.
      </div>
    </div>
"""
    # Optional: reconciliation check (exchange vs journal) — if discrepancy, add warning
    try:
        rec = _compute_reconcile_status_readonly()
        if rec.get('status') == 'discrepancy_detected':
            disp = rec.get('discrepancies') or []
            summary = f"{len(disp)} discrepancy(ies): " + ", ".join([d.get('type', '') for d in disp[:5]]) + ("..." if len(disp) > 5 else "")
            html += f"""
    <div style="border-left: 4px solid #d97706; padding: 12px 16px; margin-bottom: 16px; background: #fffbeb; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #d97706; margin-bottom: 8px;">Reconciliation</div>
      <div style="font-size: 14px;">Exchange vs journal mismatch detected. {summary} Check Reports → Reconciliation history and run Reconcile if needed.</div>
    </div>
"""
    except Exception:
        pass
    # On 1st of month, append monthly review summary (amber accent, clean table)
    try:
        from zoneinfo import ZoneInfo
        et_now = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
        if et_now.day == 1:
            mr = _build_monthly_review()
            perf = mr.get('performance', {})
            attr_recs = mr.get('model_attribution', {}).get('recommendations', [])
            risk_info = mr.get('risk', {})
            html += """
    <div style="border-left: 4px solid #d97706; padding: 12px 16px; margin-bottom: 16px; background: #fffbeb; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #d97706; margin-bottom: 8px;">📊 Monthly Review (30d)</div>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        <tr><td style="padding: 4px 0; color: #64748b;">Sharpe</td><td style="padding: 4px 0; text-align: right;">""" + str(perf.get('sharpe_ratio', 0)) + """</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Sortino</td><td style="padding: 4px 0; text-align: right;">""" + str(perf.get('sortino_ratio', 0)) + """</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Max DD</td><td style="padding: 4px 0; text-align: right;">""" + str(perf.get('max_drawdown_pct', 0)) + """%</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">Win rate</td><td style="padding: 4px 0; text-align: right;">""" + str(perf.get('win_rate_pct', 0)) + """%</td></tr>
        <tr><td style="padding: 4px 0; color: #64748b;">P&L</td><td style="padding: 4px 0; text-align: right; font-weight: 600;">$""" + f"{perf.get('total_pnl_usd', 0):,.2f}" + """</td></tr>
      </table>"""
            if risk_info.get('var_1d_usd'):
                html += f"<p style='margin: 8px 0 0; font-size: 13px; color: #64748b;'>VaR (95% 1d): ${risk_info.get('var_1d_usd', 0):,.2f}</p>"
            if attr_recs:
                html += "<p style='margin: 8px 0 0; font-size: 13px;'><strong>Model recommendations:</strong> " + '; '.join(attr_recs[:3]) + "</p>"
            html += """
    </div>
"""
            html = html.strip()
    except Exception:
        pass
    # Optimizer health check (audit flags)
    try:
        health_html = _build_optimizer_health_check_html()
        if health_html:
            html += health_html
    except Exception:
        pass
    html += """
  </div>
</div>
"""
    return html.strip()


def _build_optimizer_health_check_html() -> str:
    """Compact optimizer health check for daily email. Returns an HTML block with flags, or empty string if all clear."""
    import json as _json
    from datetime import timezone as _tz, timedelta as _td
    flags = []

    # 1. Optimizer sample size guard
    try:
        _lt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live_thresholds.json')
        with open(_lt_path) as _f:
            _lt = _json.load(_f)
        _n7 = _lt.get('7d_trades')
        _adj = _lt.get('adjustment', '')
        _upd = _lt.get('updated', '')
        if _n7 is not None and not _lt.get('7d_reliable', True):
            flags.append(f"Optimizer 7d window has only {_n7} trades (need ≥15 for auto-apply) — win rate unreliable, last updated {_upd[:10]}")
    except Exception:
        pass

    # 2. Live threshold drift vs defaults (>20% relative or >10 absolute for key params)
    try:
        from thresholds import DEFAULT_THRESHOLDS as _dt, OPTIMIZER_BOUNDS as _ob
        _DRIFT_PARAMS = [
            'SIDEWAYS_REGIME_MIN_SCORE', 'BEAR_REGIME_MIN_SCORE', 'BULL_REGIME_MIN_SCORE',
            'NARRATIVE_EXHAUSTION_7D_PCT', 'PUMP_24H_PCT', 'DIP_MIN_SCORE',
        ]
        for _p in _DRIFT_PARAMS:
            _default = _dt.get(_p)
            _live = _lt.get(_p)
            if _default is None or _live is None:
                continue
            _delta = abs(float(_live) - float(_default))
            _pct = _delta / abs(float(_default)) * 100 if float(_default) != 0 else 0
            if _delta >= 5 and _pct >= 15:
                _dir = 'tightened' if float(_live) > float(_default) else 'loosened'
                flags.append(f"Threshold drift: {_p} {_dir} from {_default} → {_live} ({_pct:.0f}%)")
    except Exception:
        pass

    # 3. Execution diagnostics in last 24h
    try:
        _elog = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'execution_log.jsonl')
        _cutoff_24h = (datetime.now(_tz.utc) - _td(hours=24)).isoformat()[:16]
        _err_24h = 0
        _non_actionable_24h = 0
        _reasons: dict[str, int] = {}
        with open(_elog) as _ef:
            for _line in _ef:
                try:
                    _entry = _json.loads(_line)
                    _ts = (_entry.get('ts') or '')[:16]
                    if _ts < _cutoff_24h:
                        continue
                    _non_actionable = bool(_entry.get('non_actionable'))
                    _reason = str(_entry.get('reason') or '')
                    if _entry.get('status') == 'error':
                        if not _non_actionable and _reason != 'would_trigger_immediately':
                            _err_24h += 1
                            _err_key = _entry.get('action') or _entry.get('error') or 'unknown_error'
                            _reasons[str(_err_key)] = _reasons.get(str(_err_key), 0) + 1
                    elif _entry.get('status') == 'skipped' and (_non_actionable or _reason):
                        _non_actionable_24h += 1
                        _skip_key = _reason or _entry.get('action') or 'skipped'
                        _reasons[str(_skip_key)] = _reasons.get(str(_skip_key), 0) + 1
                except Exception:
                    continue
        if _err_24h > 0:
            flags.append(f"Execution errors: {_err_24h} in last 24h — check execution_log.jsonl")
        if _err_24h > 0 or _non_actionable_24h > 0:
            _top = sorted(_reasons.items(), key=lambda kv: kv[1], reverse=True)[:3]
            _top_s = ", ".join([f"{k} ({v})" for k, v in _top]) if _top else "none"
            flags.append(
                f"Execution summary (24h): actionable={_err_24h}, non_actionable={_non_actionable_24h}; top={_top_s}"
            )
    except Exception:
        pass

    # 4. Win rate drift vs backtest baseline (>10pp gap per regime)
    try:
        _exp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'monitoring_expectancy.json')
        with open(_exp_path) as _ef2:
            _exp = _json.load(_ef2)
        _baseline = {r: v.get('win_rate_pct', 0) for r, v in _exp.get('by_regime', {}).items()}
        # Compute live 7d win rate per regime from positions.json
        _pos_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'positions.json')
        with open(_pos_path) as _pf:
            _all_pos = _json.load(_pf)
        _cutoff_7d = (datetime.now(_tz.utc) - _td(days=7)).strftime('%Y-%m-%d')
        _by_regime: dict = {}
        for _p in _all_pos:
            if _p.get('status') not in ('CLOSED', 'closed'):
                continue
            if _p.get('is_test') or _p.get('exit_reason') == 'test_cleanup':
                continue
            if (_p.get('exit_time') or '')[:10] < _cutoff_7d:
                continue
            _r = (_p.get('market_regime') or _p.get('regime') or 'UNKNOWN').upper()
            if _r in ('UNKNOWN', 'PRE_PIPELINE', ''):
                continue
            _by_regime.setdefault(_r, {'wins': 0, 'total': 0})
            _by_regime[_r]['total'] += 1
            if _p.get('outcome') == 'win':
                _by_regime[_r]['wins'] += 1
        for _r, _stats in _by_regime.items():
            if _stats['total'] < 5:
                continue  # too small to be meaningful
            _live_wr = 100 * _stats['wins'] / _stats['total']
            _base_wr = _baseline.get(_r)
            if _base_wr is not None and abs(_live_wr - _base_wr) > 10:
                _dir = 'below' if _live_wr < _base_wr else 'above'
                flags.append(
                    f"Win rate drift — {_r}: {_live_wr:.0f}% live vs {_base_wr:.0f}% baseline "
                    f"({_dir} by {abs(_live_wr - _base_wr):.0f}pp, n={_stats['total']})"
                )
    except Exception:
        pass

    if not flags:
        return ''

    _rows = ''.join(
        f'<li style="margin: 4px 0; font-size: 13px;">{_fl}</li>'
        for _fl in flags
    )
    return f"""
    <div style="border-left: 4px solid #dc2626; padding: 12px 16px; margin-bottom: 16px; background: #fef2f2; border-radius: 4px;">
      <div style="font-size: 12px; font-weight: 600; color: #dc2626; margin-bottom: 8px;">⚠️ Optimizer Health Check</div>
      <ul style="margin: 0; padding-left: 18px;">
        {_rows}
      </ul>
    </div>
"""


def _get_lean_profit_status() -> dict:
    """Milestone gate for re-enabling advanced optimization workflows."""
    required_fields = (
        'fear_greed', 'funding_rate', 'price_change_24h',
        'price_change_7d', 'price_change_30d', 'edge_score',
        'ath_change_pct', 'fdv_ratio',
    )
    out = {
        'closed_trades': 0,
        'attr_completeness_pct': 0.0,
        'expectancy_pct_30d': None,
        'ready_for_advanced': False,
        'thresholds': {
            'min_closed_trades': LEAN_MIN_CLOSED_TRADES_FOR_ADVANCED,
            'min_attr_completeness_pct': LEAN_MIN_ATTR_COMPLETENESS_PCT,
            'min_expectancy_pct_30d': LEAN_MIN_EXPECTANCY_PCT,
        },
    }
    try:
        _pos_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'positions.json')
        with open(_pos_path, 'r', encoding='utf-8') as _pf:
            _all_pos = json.load(_pf)
        _closed = [p for p in _all_pos if p.get('status') in ('CLOSED', 'closed') and not p.get('is_test')]
        out['closed_trades'] = len(_closed)
        if _closed:
            _hits = 0
            for _p in _closed:
                _hits += sum(1 for _f in required_fields if _p.get(_f) is not None)
            out['attr_completeness_pct'] = round(100.0 * _hits / (len(_closed) * len(required_fields)), 1)
    except Exception:
        pass
    try:
        _exp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'monitoring_expectancy.json')
        with open(_exp_path, 'r', encoding='utf-8') as _ef:
            _exp = json.load(_ef)
        _weighted_pnl = 0.0
        _weighted_n = 0
        for _r in (_exp.get('by_regime') or {}).values():
            _n = int(_r.get('trades', 0) or 0)
            _wr = float(_r.get('win_rate_pct', 0) or 0) / 100.0
            _aw = float(_r.get('avg_win_pct', 0) or 0)
            _al = abs(float(_r.get('avg_loss_pct', 0) or 0))
            _expectancy = (_wr * _aw) - ((1 - _wr) * _al)
            _weighted_pnl += _expectancy * _n
            _weighted_n += _n
        if _weighted_n > 0:
            out['expectancy_pct_30d'] = round(_weighted_pnl / _weighted_n, 3)
    except Exception:
        pass
    out['ready_for_advanced'] = bool(
        out['closed_trades'] >= LEAN_MIN_CLOSED_TRADES_FOR_ADVANCED
        and out['attr_completeness_pct'] >= LEAN_MIN_ATTR_COMPLETENESS_PCT
        and (out['expectancy_pct_30d'] is not None and out['expectancy_pct_30d'] >= LEAN_MIN_EXPECTANCY_PCT)
    )
    return out


_daily_report_slot_lock = threading.Lock()
_STATUS_REPORT_SLOT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', '.last_status_report_slot')
_STATUS_REPORT_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', '.status_report_lock')

def _run_daily_status_report_if_due():
    """If status email is enabled and current time ET is in configured hours, send daily report.
    Skips sending until at least one refresh has completed (last_updated != 'Never') so the email
    never reports 'Regime: Unknown' from cold start. Schedule (enabled, hours_et) from scheduler.
    Uses in-memory + file-based slot so only one report per slot is sent even with multiple processes."""
    try:
        if last_updated == 'Never':
            return
        status_email = scheduler.get_status_email_schedule()
        if not status_email.get('enabled'):
            return
        hours_et = status_email.get('hours_et') or [9, 13, 17]
        from zoneinfo import ZoneInfo
        et_now = datetime.now(timezone.utc).astimezone(ZoneInfo('America/New_York'))
        hour = et_now.hour
        if hour not in hours_et:
            return
        slot = f"{et_now.date()}_{hour}"
        with _daily_report_slot_lock:
            if getattr(_run_daily_status_report_if_due, '_last_slot', None) == slot:
                return
            _run_daily_status_report_if_due._last_slot = slot

        # Cross-process dedupe: only one send per slot even if multiple app instances run
        data_dir = os.path.dirname(_STATUS_REPORT_SLOT_FILE)
        if data_dir and not os.path.isdir(data_dir):
            os.makedirs(data_dir, exist_ok=True)
        try:
            fd = os.open(_STATUS_REPORT_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
        except FileExistsError:
            return  # another process is handling this slot or already sent
        try:
            if os.path.isfile(_STATUS_REPORT_SLOT_FILE):
                with open(_STATUS_REPORT_SLOT_FILE, 'r') as f:
                    if f.read().strip() == slot:
                        return
            summary_html = _build_daily_status_summary()
            alerts.send_daily_status_report(summary_html)
            logger.info(f"  ✓ Daily status report sent ({et_now.strftime('%Y-%m-%d %H:%M ET')})")
            with open(_STATUS_REPORT_SLOT_FILE, 'w') as f:
                f.write(slot)
            if LEAN_PROFIT_FIRST_MODE and hour == 9:
                _s = _get_lean_profit_status()
                if _s.get('ready_for_advanced'):
                    logger.info("  [lean-mode] Milestones met. Advanced optimization can be re-enabled in controlled phases.")
                else:
                    logger.info(
                        "  [lean-mode] Advanced features gated: closed=%s/%s, attr=%s%%/%s%%, exp30=%s%%/%s%%",
                        _s.get('closed_trades'),
                        _s.get('thresholds', {}).get('min_closed_trades'),
                        _s.get('attr_completeness_pct'),
                        _s.get('thresholds', {}).get('min_attr_completeness_pct'),
                        _s.get('expectancy_pct_30d'),
                        _s.get('thresholds', {}).get('min_expectancy_pct_30d'),
                    )
        finally:
            try:
                os.unlink(_STATUS_REPORT_LOCK_FILE)
            except OSError:
                pass
    except Exception as e:
        logger.warning(f"  ⚠ Daily status report error: {e}")


# =============================================================================
# HELOC Funding Helpers
# =============================================================================

def _compute_heloc_metrics(acct_cfg=None, open_positions=None):
    """Compute cost-of-capital metrics from HELOC config. Returns dict of derived values."""
    if acct_cfg is None:
        acct_cfg = load_account_config()
    if open_positions is None:
        open_positions = get_open_positions(exclude_test=True)

    heloc_rate = float(acct_cfg.get('heloc_rate_pct', 8.0) or 8.0)
    usdc_yield = float(acct_cfg.get('usdc_yield_pct', 4.1) or 4.1)
    drawn = float(acct_cfg.get('heloc_drawn', 0) or 0)
    drawn_peak = float(acct_cfg.get('heloc_drawn_peak', 0) or 0)
    max_dd_pct = float(acct_cfg.get('max_drawn_drawdown_pct', 30.0) or 30.0)

    # Deployed capital = sum of open position cost bases
    deployed = sum(float(p.get('capital_usd', 0) or 0) for p in open_positions)
    idle = max(0.0, drawn - deployed)

    # Net cost rates
    net_idle_cost_pct = heloc_rate - usdc_yield
    deployed_cost_pct = heloc_rate

    # Average hold days from closed trades
    avg_hold_days = 5.0  # default
    try:
        closed = get_closed_positions(exclude_test=True)
        hold_days_list = []
        for p in closed:
            entry_t = p.get('entry_time')
            exit_t = p.get('exit_time')
            if entry_t and exit_t:
                try:
                    entry_dt = datetime.strptime(str(entry_t)[:19], '%Y-%m-%d %H:%M:%S')
                    exit_dt = datetime.strptime(str(exit_t)[:19], '%Y-%m-%d %H:%M:%S')
                    days = max(0.01, (exit_dt - entry_dt).total_seconds() / 86400)
                    hold_days_list.append(days)
                except (ValueError, TypeError):
                    pass
        if hold_days_list:
            avg_hold_days = sum(hold_days_list) / len(hold_days_list)
    except Exception:
        pass

    # Hurdle rate per trade
    hurdle_per_trade_pct = heloc_rate * (avg_hold_days / 365)

    # Monthly carry estimate
    monthly_deployed_cost = deployed * (heloc_rate / 100) / 12
    monthly_idle_cost = idle * (net_idle_cost_pct / 100) / 12
    monthly_carry_est = monthly_deployed_cost + monthly_idle_cost

    # Drawn capital drawdown
    total_account_value = drawn  # approximate: cash + positions
    try:
        total_account_value = float(acct_cfg.get('total_cash', 0) or 0) + deployed
    except Exception:
        pass
    drawn_drawdown_pct = 0.0
    if drawn_peak > 0 and total_account_value < drawn_peak:
        drawn_drawdown_pct = (drawn_peak - total_account_value) / drawn_peak * 100

    return {
        'net_idle_cost_pct': round(net_idle_cost_pct, 2),
        'deployed_cost_pct': round(deployed_cost_pct, 2),
        'avg_hold_days': round(avg_hold_days, 2),
        'hurdle_per_trade_pct': round(hurdle_per_trade_pct, 4),
        'monthly_carry_est': round(monthly_carry_est, 2),
        'deployed_usd': round(deployed, 2),
        'idle_usd': round(idle, 2),
        'drawn_drawdown_pct': round(drawn_drawdown_pct, 2),
        'drawn_drawdown_alert': drawn_drawdown_pct > max_dd_pct,
    }


def _fetch_usdc_yield(force=False):
    """Fetch current USDC yield from Coinbase. Returns rate or None on failure."""
    import requests as http_requests
    acct_cfg = load_account_config()
    if not force:
        last_updated = acct_cfg.get('usdc_yield_last_updated')
        if last_updated:
            try:
                last_dt = datetime.strptime(str(last_updated)[:10], '%Y-%m-%d')
                if (datetime.now() - last_dt).days < 7:
                    return acct_cfg.get('usdc_yield_pct', 4.1)
            except (ValueError, TypeError):
                pass
    try:
        resp = http_requests.get('https://www.coinbase.com/earn/usdc', timeout=5,
                                 headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code == 200:
            import re
            # Look for APY pattern like "4.1% APY" or "4.1%"
            match = re.search(r'(\d+\.?\d*)\s*%\s*APY', resp.text)
            if not match:
                match = re.search(r'earn[^"]*?(\d+\.?\d*)\s*%', resp.text, re.IGNORECASE)
            if match:
                rate = float(match.group(1))
                if 0.1 <= rate <= 20.0:  # sanity check
                    save_account_config(
                        usdc_yield_pct=rate,
                        usdc_yield_last_updated=datetime.now().strftime('%Y-%m-%d'),
                    )
                    logger.info(f"USDC yield updated: {rate}%")
                    return rate
        logger.warning(f"USDC yield fetch: could not parse rate (status={resp.status_code})")
    except Exception as e:
        logger.warning(f"USDC yield fetch failed: {e}")
    return None


def _effective_account_size_for_risk():
    """Return notional if HELOC mode on, else total_cash (drawn). Used for risk cap tier lookup."""
    cfg = load_account_config()
    if cfg.get('heloc_mode') and cfg.get('heloc_notional', 0) > 0:
        return cfg['heloc_notional']
    return getattr(position_sizer, 'account_size', 0)


def _sync_sizer_account_size():
    """Set position_sizer.account_size to notional (HELOC on) or total_cash (HELOC off).
    This affects risk %, Kelly cap, and percentage-based calculations.
    cash_available (actual liquidity) is passed separately and always uses drawn/exchange balance."""
    cfg = load_account_config()
    if cfg.get('heloc_mode') and cfg.get('heloc_notional', 0) > 0:
        notional = cfg['heloc_notional']
        if position_sizer.account_size != notional:
            position_sizer.update_account_size(notional)
            logger.info(f"Position sizer account_size set to HELOC notional: ${notional:,.0f}")
    else:
        total_cash = cfg.get('total_cash', 0)
        if total_cash > 0 and position_sizer.account_size != total_cash:
            position_sizer.update_account_size(total_cash)


def _build_account_overview():
    """Build account overview dict for widget (used by GET and after POST update)."""
    global cached_data
    acct_cfg = load_account_config()
    cfg = get_execution_config()
    exchange = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
    fee_rate = 0.001 if exchange == 'coinbase' else 0.0026  # 0.1% Coinbase / 0.26% Kraken
    # Use persisted total_cash so dashboard shows latest after sync/reconcile (they write to account_config)
    total_cash = float(acct_cfg.get('total_cash', 0) or 0)
    if total_cash <= 0:
        total_cash = float(getattr(position_sizer, 'account_size', 0) or 0)
    elif abs(total_cash - getattr(position_sizer, 'account_size', 0)) > 1e-6:
        position_sizer.update_account_size(total_cash)
    risk_pct = float(getattr(position_sizer, 'max_risk_pct', 2.0) or 2.0)
    dust_threshold_usd = float(acct_cfg.get('dust_threshold_usd', 1.0) or 1.0)
    open_positions = get_open_positions(exclude_test=True)
    price_by_id = {c['id']: float(c.get('current_price', 0) or 0) for c in (cached_data.get('coins') or []) if c.get('id') and c.get('current_price') is not None}
    open_cost_basis = 0.0
    open_market_value = 0.0
    for p in open_positions:
        entry = float(p.get('entry_price', 0) or 0)
        qty = float(p.get('quantity', 0) or 0)
        cap = float(p.get('capital_usd', 0) or 0)
        open_cost_basis += cap if cap > 0 else (entry * qty)
        current_price = price_by_id.get(p.get('coin_id', '')) or entry
        open_market_value += qty * current_price
    open_cost_basis = round(open_cost_basis, 2)
    open_market_value = round(open_market_value, 2)
    total_account_value = round(total_cash + open_market_value, 2)
    estimated_fees = round(open_cost_basis * fee_rate, 2)
    pending_twap = get_active_twap_orders()
    pending_twap_usd = sum(float(o.get('total_usd', 0) or 0) for o in pending_twap if not o.get('is_test'))
    cash_available = round(max(0.0, total_cash - pending_twap_usd), 2)
    # Reference cost basis per new trade: use dynamic stop from current regime
    max_risk_usd = total_cash * (risk_pct / 100)
    max_risk_usd = round(max_risk_usd, 2)
    max_cash_usage_pct = float(getattr(position_sizer, 'max_cash_usage_pct', 40.0))
    # Dynamic stop: use current market regime + median volatility (50%) for a representative estimate
    regime_label = 'SIDEWAYS'
    if current_regime and isinstance(current_regime, dict):
        regime_label = current_regime.get('regime', 'SIDEWAYS') or 'SIDEWAYS'
    reference_stop_pct = target_calculator.base_stop_pct  # fallback
    try:
        _dummy_targets = target_calculator.calculate_targets(
            entry_price=100, volatility_pct=50.0,
            regime=regime_label, consensus_signal='MODERATE_BUY')
        reference_stop_pct = _dummy_targets.get('stop_loss_pct', target_calculator.base_stop_pct)
    except Exception:
        pass
    reference_cost_basis = max_risk_usd / (reference_stop_pct / 100) if reference_stop_pct > 0 else 0
    reference_cost_basis = min(reference_cost_basis, total_cash * 0.8)
    max_by_cash_cap = cash_available * (max_cash_usage_pct / 100.0)
    reference_cost_basis = min(reference_cost_basis, max_by_cash_cap)
    # Apply same Kelly cap as execution (position_sizer.calculate_for_consensus) so estimate matches actual trade size
    from thresholds import get_threshold
    kelly_cap_pct = float(get_threshold('KELLY_CAP_PCT', 5.0) or 5.0)
    kelly_cap_usd = total_cash * (kelly_cap_pct / 100)
    reference_cost_basis = min(reference_cost_basis, kelly_cap_usd)
    reference_cost_basis = round(max(0.0, reference_cost_basis), 2)
    # Open positions count & capacity
    max_open_total = get_threshold('MAX_OPEN_TOTAL', 8)
    open_positions_count = len(open_positions)
    exec_cfg = get_execution_config()
    keys = _exchange_keys_configured()
    exchange = (exec_cfg.get('auto_execution_exchange') or 'coinbase').lower()
    return {
        'total_account_value': total_account_value,
        'total_cash': round(total_cash, 2),
        'open_positions_market_value': open_market_value,
        'open_positions_count': open_positions_count,
        'max_open_total': max_open_total,
        'risk_pct': round(risk_pct, 2),
        'max_risk_usd_per_trade': max_risk_usd,
        'open_positions_cost_basis': open_cost_basis,
        'estimated_trade_fees': estimated_fees,
        'fee_rate_pct': round(fee_rate * 100, 2),
        'cash_available_to_trade': cash_available,
        'pending_twap_orders': pending_twap,
        'pending_twap_usd': round(pending_twap_usd, 2),
        'reference_cost_basis_per_trade': reference_cost_basis,
        'reference_stop_pct': round(reference_stop_pct, 1),
        'max_cash_usage_pct': round(max_cash_usage_pct, 1),
        'auto_execution_enabled': bool(exec_cfg.get('auto_execution_enabled', True)),
        'auto_execution_exchange': exchange,
        'coinbase_configured': keys.get('coinbase', False),
        'kraken_configured': keys.get('kraken', False),
        'last_auto_sync_at': exec_cfg.get('last_auto_sync_at'),
        'dust_threshold_usd': round(dust_threshold_usd, 2),
        'heloc_mode': bool(acct_cfg.get('heloc_mode', False)),
        'heloc_notional': round(float(acct_cfg.get('heloc_notional', 0) or 0), 2),
        'heloc_drawn': round(float(acct_cfg.get('heloc_drawn', 0) or 0), 2),
    }


# Account overview, update, sync-balance, reconcile, sync-position-quantities → routes/account_bp.py
# Funding (HELOC) routes → routes/account_bp.py


def _increment_sync_failure_count(exchange_id: str):
    """Increment consecutive sync failure count; send repeated-failure alert when count first reaches threshold."""
    import safe_json
    os.makedirs(os.path.dirname(SYNC_FAILURE_COUNT_FILE), exist_ok=True)
    data = safe_json.load(SYNC_FAILURE_COUNT_FILE, default=lambda: {"consecutive_failures": 0})
    count = data.get("consecutive_failures", 0) + 1
    data["consecutive_failures"] = count
    safe_json.save(SYNC_FAILURE_COUNT_FILE, data)
    if count == REPEATED_SYNC_FAILURE_THRESHOLD:
        try:
            alerts.send_repeated_sync_failure_alert(exchange_id, count)
        except Exception:
            pass


def _reset_sync_failure_count():
    """Reset consecutive sync failure count after a successful sync."""
    import safe_json
    os.makedirs(os.path.dirname(SYNC_FAILURE_COUNT_FILE), exist_ok=True)
    safe_json.save(SYNC_FAILURE_COUNT_FILE, {"consecutive_failures": 0})


def _log_sync_failure_audit(exchange_id: str, error: str, is_zero_cash: bool):
    """Append one line to data/sync_failure_audit.jsonl for later diagnosis (no alert cooldown)."""
    try:
        os.makedirs(os.path.dirname(SYNC_FAILURE_AUDIT_FILE), exist_ok=True)
        with open(SYNC_FAILURE_AUDIT_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                'ts': datetime.now(timezone.utc).isoformat(),
                'exchange_id': exchange_id,
                'error': error,
                'is_zero_cash': is_zero_cash,
            }) + '\n')
    except Exception:
        pass


def do_auto_sync_balance():
    """
    Run balance sync from exchange when auto_sync_balance is True (manual book, refresh, post-close, etc.).
    auto_execution_enabled is not required — stale USD/USDC/USDT after a fill would double-count with
    open_market_value in total_account_value until cash is refreshed.
    Returns (success, total_cash or None, error_msg or None).
    Critical "Sync Balance Failed" alert is sent only when the exchange fetch fails, not when save/update fails.
    """
    cfg = get_execution_config()
    if not cfg.get('auto_sync_balance'):
        return False, None, None
    exchange_id = (cfg.get('auto_execution_exchange') or 'coinbase').lower()

    # Fetch from exchange only; send critical alert only on exchange/network failure
    try:
        result = fetch_balance_sync(exchange_id)
    except Exception as e:
        # Optional: one retry after short delay to avoid alerting on transient blips
        try:
            time.sleep(2)
            result = fetch_balance_sync(exchange_id)
        except Exception as e2:
            logger.warning(f"  ⚠ Auto-sync balance failed (exchange): {e2}")
            _log_sync_failure_audit(exchange_id, str(e2), is_zero_cash=False)
            try:
                alerts.send_sync_balance_failure_alert(exchange_id, str(e2), is_zero_cash=False)
            except Exception:
                pass
            _increment_sync_failure_count(exchange_id)
            return False, None, str(e2)
        # Retry succeeded; result is set, fall through

    total_cash = float(result.get('total_cash', 0) or 0)
    if total_cash <= 0:
        _log_sync_failure_audit(exchange_id, "Exchange reported no cash", is_zero_cash=True)
        try:
            alerts.send_sync_balance_failure_alert(exchange_id, "Exchange reported no cash", is_zero_cash=True)
        except Exception:
            pass
        _increment_sync_failure_count(exchange_id)
        return False, None, "Exchange reported no cash"

    # Persist and update sizer; do not send sync-failure alert if this fails (exchange already succeeded)
    try:
        position_sizer.update_account_size(total_cash)
        save_account_config(total_cash=total_cash)
        _set_last_auto_sync_at(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z')
        _reset_sync_failure_count()
    except Exception as e:
        logger.warning(f"  ⚠ Auto-sync: exchange fetch OK but save/update failed: {e}")
        return False, None, str(e)

    ex_label = (result.get('exchange') or exchange_id).capitalize()
    logger.info(f"  ✓ Auto-synced balance from {ex_label}: ${total_cash:,.2f}")
    return True, total_cash, None


# Wire TWAP completion to auto-sync balance (when enabled)
_execution_engine_module._on_twap_done_sync_balance = do_auto_sync_balance


def _sync_account_from_exchange_at_startup():
    """Run balance sync at startup when auto_sync_balance is on so position sizer matches exchange cash."""
    try:
        cfg = get_execution_config()
        if cfg.get('auto_sync_balance'):
            ok, total_cash, _ = do_auto_sync_balance()
            if ok and total_cash is not None:
                return  # Successfully synced
    except Exception:
        pass  # Fall back to account_config; no alert at startup


# Skip startup sync when running under pytest so tests don't trigger real exchange calls or sync-failure alerts
if "pytest" not in sys.modules:
    _sync_account_from_exchange_at_startup()


def _sync_new_position_quantity_from_exchange(position: dict, symbol: str) -> None:
    """After opening a position, set journal quantity to exchange (Coinbase) quantity when this is the only open position for that symbol. Exchange is source of truth; any fractional difference is amended."""
    if not position or not symbol:
        return
    sym = (symbol or '').strip().upper()
    open_positions = get_open_positions(exclude_test=True)
    same_symbol = [p for p in open_positions if (p.get('coin_symbol') or '').strip().upper() == sym]
    if len(same_symbol) != 1:
        return  # multiple or zero positions for symbol; don't overwrite
    try:
        cfg = get_execution_config()
        exchange_id = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
        result = fetch_balance_sync(exchange_id)
        holdings = result.get('holdings') or {}
        eq = 0.0
        for k, v in holdings.items():
            if (k or '').strip().upper() == sym:
                eq += float(v or 0)
        # If exchange still shows 0 (balance update lag after fill), retry once after short delay — unless position just opened (grace period)
        jq = float(position.get('quantity', 0) or 0)
        if eq <= 0 and jq > 0:
            entry_time_str = position.get('entry_time') or ''
            try:
                from datetime import datetime, timezone
                if len(entry_time_str) >= 19:
                    et = datetime.strptime(entry_time_str[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - et).total_seconds() < 120:
                        return  # grace period: don't overwrite new position with 0 during balance lag
            except Exception:
                pass
            time.sleep(2)
            result = fetch_balance_sync(exchange_id)
            holdings = result.get('holdings') or {}
            eq = 0.0
            for k, v in holdings.items():
                if (k or '').strip().upper() == sym:
                    eq += float(v or 0)
        if eq <= 0:
            return
        jq = float(position.get('quantity', 0) or 0)
        # Exchange = source of truth: amend on any fractional difference (tight epsilon only)
        if abs(jq - eq) < 1e-8:
            return  # already matches
        pos, err = update_position(position.get('id'), quantity=round(eq, 8))
        if not err and pos:
            position['quantity'] = eq
            position['capital_usd'] = round(float(position.get('entry_price', 0) or 0) * eq, 2)
            logger.info(f"  ✓ Synced position quantity from exchange: {sym} = {eq}")
    except Exception as e:
        logger.warning(f"  ⚠ Sync new position quantity from exchange: {e}")


def _on_twap_done_open_position_and_place_orders(fill_data):
    """After TWAP buy completes: open journal position and place SL/TP limit orders. Prefers SL/TP captured at decision time; recalculates only if missing."""
    if not fill_data:
        return
    entry_price = fill_data.get('entry_price', 0)
    coin_id = fill_data.get('coin_id', '')
    symbol = fill_data.get('coin_symbol', '')
    expected_usd = float(fill_data.get('expected_usd') or fill_data.get('capital_usd') or 0)
    actual_usd = float(fill_data.get('quantity', 0) or 0) * float(entry_price or 0)
    if expected_usd > 0 and actual_usd > 0:
        slippage_pct = abs(actual_usd - expected_usd) / expected_usd * 100
        if slippage_pct > 5.0:
            logger.warning(f"  ⚠ HIGH SLIPPAGE on {symbol}: expected ${expected_usd:.2f}, filled ${actual_usd:.2f} ({slippage_pct:.1f}% slippage)")
            try:
                alerts.send_critical_alert(
                    f"High Slippage – {symbol}",
                    f"Order for {symbol} filled with {slippage_pct:.1f}% slippage: expected ${expected_usd:.2f}, got ${actual_usd:.2f}",
                )
            except Exception:
                pass
    ai_stop_pct = fill_data.get('ai_stop_pct') or 0
    ai_target_pct = fill_data.get('ai_target_pct') or 0
    if not ai_stop_pct or not ai_target_pct:
        dyn = _get_dynamic_stop_target_for_coin(coin_id, symbol, entry_price)
        if dyn:
            ai_stop_pct = ai_stop_pct or dyn['stop_loss_pct']
            ai_target_pct = ai_target_pct or dyn['take_profit_pct']
    if not ai_stop_pct or not ai_target_pct:
        cfg = get_execution_config()
        ai_stop_pct = ai_stop_pct or cfg.get('default_stop_pct', 4.0)
        ai_target_pct = ai_target_pct or cfg.get('default_target_pct', 6.0)
    open_payload = {
        'coin_id': coin_id,
        'coin_name': fill_data.get('coin_name', ''),
        'coin_symbol': symbol,
        'signal': 'BUY',
        'entry_price': entry_price,
        'quantity': fill_data.get('quantity', 0),
        'capital_usd': fill_data.get('capital_usd', 0),
        'ai_target_pct': ai_target_pct,
        'ai_stop_pct': ai_stop_pct,
        'is_test': fill_data.get('is_test', get_test_mode()),
    }
    for k in ('market_regime', 'quant_tier', 'quant_path', 'trading_scenario',
              'specialist_veto', 'confirmation_score',
              'fear_greed', 'funding_rate', 'price_change_24h', 'price_change_7d', 'price_change_30d', 'edge_score',
              'signal_badges', 'regression_features'):
        if fill_data.get(k) is not None:
            open_payload[k] = fill_data[k]
    position = open_position(open_payload)
    is_real_fill = not fill_data.get('is_test', get_test_mode())
    if position is None and is_real_fill:
        capital = fill_data.get('capital_usd', 0)
        logger.error(
            f"  🚨 CRITICAL: Real Coinbase fill for {symbol} (${capital}) was NOT journaled! "
            f"open_position() returned None. Raising to trigger retry queue."
        )
        try:
            alerts.send_critical_alert(
                f"UNJOURNALED FILL – {symbol} ${capital:.2f}",
                f"A real Coinbase buy for {symbol} filled (${capital:.2f}) but open_position() "
                f"rejected it (likely duplicate check). The position has NO stop-loss protection. "
                f"Use 'Add from Coinbase' in the dashboard to recover it.",
                alert_type='unjournaled_fill',
            )
        except Exception:
            pass
        raise RuntimeError(
            f"Real fill for {symbol} (${capital}) not journaled — open_position returned None"
        )
    if position and is_real_fill:
        _sync_new_position_quantity_from_exchange(position, symbol)
        try:
            alerts.send_buy_filled_alert(fill_data)
        except Exception as ae:
            logger.warning(f"  ⚠ Buy filled alert failed: {ae}")
    if position and is_real_fill:
        cfg = get_execution_config()
        if (cfg.get('auto_execution_exchange') or 'coinbase').lower() == 'coinbase':
            try:
                _place_sl_tp_with_retry(position)
            except Exception as e:
                logger.warning(f"  ⚠ Post-TWAP SL/TP placement failed: {e}")
                traceback.print_exc()


_execution_engine_module._on_twap_done_open_position_and_place_orders = _on_twap_done_open_position_and_place_orders


# sync_balance → routes/account_bp.py


@app.route('/api/execute/manual-book', methods=['POST'])
def manual_book_to_coinbase():
    """
    Manually execute a TWAP buy on Coinbase from the Quick Log form.
    Body: { symbol, total_usd, stop_pct, target_pct, coin_name, coin_id }
    On success: starts TWAP; when complete, opens journal position + places SL/TP.
    """
    try:
        guard = require_admin_write_access()
        if guard is not None:
            return guard
        if get_test_mode():
            return jsonify({'error': 'Book to Coinbase is disabled in Test Mode. Turn off Test Mode to execute real trades.'}), 400
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        symbol = (data.get('symbol') or '').strip().upper()
        total_usd = float(data.get('total_usd') or 0)
        cfg = get_execution_config()
        cfg_stop = cfg.get('default_stop_pct', 4.0)
        cfg_target = cfg.get('default_target_pct', 6.0)
        coin_name = data.get('coin_name') or symbol
        coin_id = data.get('coin_id') or symbol.lower()

        # Prefer dynamic levels from cache (same as dashboard) so booked SL/TP match display; fallback to _get_dynamic then form then config
        stop_pct = None
        target_pct = None
        entry_for_dyn = float(data.get('entry_price') or 0)
        if cached_data and cached_data.get('coins'):
            for c in cached_data.get('coins') or []:
                if (c.get('symbol') or '').strip().upper() == symbol:
                    if not entry_for_dyn:
                        entry_for_dyn = float(c.get('current_price') or 0)
                    dt = c.get('dynamic_targets') or {}
                    risk = c.get('risk') or {}
                    if dt:
                        stop_pct = float(dt.get('stop_loss_pct', 0))
                        target_pct = float(dt.get('take_profit_pct', 0))
                    if (not stop_pct or not target_pct) and risk:
                        stop_pct = stop_pct or float(risk.get('stop_loss_pct', 0))
                        target_pct = target_pct or float(risk.get('take_profit_pct', 0))
                    break
        if (not stop_pct or not target_pct) and entry_for_dyn and entry_for_dyn > 0:
            dyn = _get_dynamic_stop_target_for_coin(coin_id, symbol, entry_for_dyn)
            if dyn:
                stop_pct = stop_pct or dyn['stop_loss_pct']
                target_pct = target_pct or dyn['take_profit_pct']
        # Prefer explicit form overrides when user sent them (manual TP e.g. 10% over suggested 5.54%)
        if data.get('stop_pct') is not None and str(data.get('stop_pct')).strip() != '':
            try:
                stop_pct = float(data.get('stop_pct'))
            except (TypeError, ValueError):
                pass
        if data.get('target_pct') is not None and str(data.get('target_pct')).strip() != '':
            try:
                target_pct = float(data.get('target_pct'))
            except (TypeError, ValueError):
                pass
        stop_pct = float(stop_pct or cfg_stop)
        target_pct = float(target_pct or cfg_target)

        if not symbol:
            return jsonify({'error': 'symbol required'}), 400
        if total_usd <= 0:
            return jsonify({'error': 'total_usd must be greater than 0'}), 400

        exchange = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
        if exchange != 'coinbase':
            return jsonify({'error': 'Manual book only supports Coinbase. Switch exchange in settings.'}), 400

        keys = _exchange_keys_configured()
        if not keys.get('coinbase'):
            return jsonify({'error': 'Coinbase API keys not configured. Add COINBASE_API_KEY and COINBASE_API_SECRET to .env.'}), 400

        # Pre-validate symbol exists on Coinbase before starting TWAP
        from execution_engine import coinbase_symbol_available
        ok, err = coinbase_symbol_available(symbol)
        if not ok:
            return jsonify({'error': err or f'{symbol} is not available on Coinbase.'}), 400

        overview = _build_account_overview()
        cash_available = overview.get('cash_available_to_trade', 0) or 0
        if total_usd > cash_available:
            return jsonify({
                'error': f'Insufficient cash: ${total_usd:,.2f} required but only ${cash_available:,.2f} available.'
            }), 400

        # Execution context for journal position (from request or cached coin recommendation)
        execution_context = _signal_fields_from_cached(symbol)
        if data.get('market_regime') is not None or data.get('quant_tier') is not None:
            execution_context.update({
                'market_regime': data.get('market_regime') or execution_context.get('market_regime', ''),
                'quant_tier': data.get('quant_tier') or execution_context.get('quant_tier', ''),
                'quant_path': data.get('quant_path') or execution_context.get('quant_path', 'default'),
                'trading_scenario': data.get('trading_scenario') or execution_context.get('trading_scenario', 'default'),
            })

        cfg = get_execution_config()
        started = start_twap_buy_async(
            symbol=symbol,
            total_usd=total_usd,
            num_slices=5,
            duration_minutes=45,
            coin_name=coin_name,
            coin_id=coin_id,
            stop_pct=stop_pct,
            target_pct=target_pct,
            twap_min_usd=cfg.get('twap_min_usd', 500),
            is_test=get_test_mode(),
            execution_context=execution_context if execution_context else None,
            lean_override=True,  # explicit manual/admin book — exempt from the lean execution gate
        )
        if not started:
            return jsonify({'error': 'Order not started (cooldown or no Coinbase market for symbol)'}), 400

        use_single = total_usd < cfg.get('twap_min_usd', 500)
        msg = (f'Single market buy for {symbol} (${total_usd:,.2f}) started. Position will be logged and SL/TP placed when complete.'
               if use_single else f'TWAP buy for {symbol} (${total_usd:,.2f}) started. Position will be logged and SL/TP placed when complete (~45 min).')
        return jsonify({
            'status': 'started',
            'message': msg,
        })
    except Exception as e:
        logger.error(f"Manual book error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _price_by_symbol_from_cached():
    """Build symbol -> current_price from cached_data (for dust threshold in reconciliation)."""
    global cached_data
    out = {}
    for c in (cached_data.get('coins') or []):
        sym = (c.get('symbol') or '').strip().upper()
        price = c.get('current_price')
        if sym and price is not None:
            out[sym] = float(price)
    return out


def _signal_fields_from_cached(symbol: str) -> dict:
    """Extract specialist_veto, confirmation_score, market_regime,
    quant_tier, quant_path, trading_scenario, and optional attribution fields (fear_greed,
    funding_rate, price_change_24h/7d/30d, edge_score) from cached recommendation for a symbol.
    Returns dict of available fields (empty dict if symbol not found in cache)."""
    global cached_data
    for c in (cached_data or {}).get('coins') or []:
        if (c.get('symbol') or '').strip().upper() != (symbol or '').strip().upper():
            continue
        rec = c.get('ai_recommendation') or {}
        consensus = rec.get('consensus') or {}
        quant = rec.get('quant') or {}
        regime_data = (cached_data or {}).get('regime') or {}
        regime = regime_data.get('regime') or consensus.get('market_regime') or ''
        quant_tier = consensus.get('quant_tier') or quant.get('quant_tier') or ''
        quant_path = quant.get('quant_path') or 'default'
        badges = quant.get('signal_badges') or []
        sniper = 'light_sniper_entry' in badges or 'intra_swing_sniper' in badges
        veto_reason = consensus.get('veto_reason')
        out = {
            'market_regime': regime,
            'quant_tier': quant_tier,
            'quant_path': quant_path,
            'trading_scenario': 'sniper' if sniper else quant_path,
            'specialist_veto': 'VETO' if veto_reason else 'CONFIRM',
            'confirmation_score': quant.get('confirmation_score'),
            # Stage 1b: BTC risk-on/off at entry (promotion gate-integrity audit)
            'risk_regime': quant.get('risk_regime') or (regime_data.get('characteristics') or {}).get('risk_regime'),
        }
        # Attribution fields for recursive optimizer (price_change_7d/30d for veto params)
        fg = (cached_data or {}).get('fear_greed') or {}
        if fg.get('value') is not None:
            out['fear_greed'] = float(fg['value'])
        enhanced = c.get('enhanced') or {}
        fr = enhanced.get('funding_rate') or {}
        if fr.get('current') is not None:
            out['funding_rate'] = float(fr['current'])
        ch24 = c.get('price_change_percentage_24h') or c.get('price_change_percentage_24h_in_currency')
        if ch24 is not None:
            out['price_change_24h'] = float(ch24)
        ch7 = c.get('price_change_percentage_7d_in_currency')
        if ch7 is not None:
            out['price_change_7d'] = float(ch7)
        ch30 = c.get('price_change_percentage_30d_in_currency')
        if ch30 is not None:
            out['price_change_30d'] = float(ch30)
        if quant.get('edge_score') is not None:
            out['edge_score'] = float(quant['edge_score'])
        ath_pct = c.get('ath_change_percentage')
        if ath_pct is not None:
            out['ath_change_pct'] = float(ath_pct)
        fdv = c.get('fully_diluted_valuation')
        mc = c.get('market_cap')
        if fdv and mc and float(mc) > 0:
            out['fdv_ratio'] = round(float(fdv) / float(mc), 2)
        return out
    return {}


def _coin_id_and_name_for_symbol(symbol: str):
    """Resolve coin_id and coin_name for a symbol from cache; fallback to symbol-based defaults."""
    global cached_data
    sym = (symbol or '').strip().upper()
    if not sym:
        return None, None
    for c in (cached_data.get('coins') or []):
        if (c.get('symbol') or '').strip().upper() == sym:
            return (c.get('id') or sym.lower()), (c.get('name') or sym)
    return sym.lower(), sym


# journal_open_from_exchange → routes/journal_bp.py


# journal_add_from_exchange_clear_pending → routes/journal_bp.py


def _compute_reconcile_status_readonly():
    """Run reconcile comparison only (no sync, no history). Returns dict with status, discrepancies, and counts.
    Used by daily report to optionally warn when exchange vs journal mismatch."""
    try:
        cfg = get_execution_config()
        exchange_id = (cfg.get('auto_execution_exchange') or 'coinbase').lower()
        result = fetch_balance_sync(exchange_id)
        exchange_holdings = result.get('holdings') or {}
        _ex = {}
        for k, v in (exchange_holdings or {}).items():
            cur = (k or '').strip().upper()
            if cur:
                _ex[cur] = round(_ex.get(cur, 0) + float(v or 0), 8)
        exchange_holdings = _ex
        open_positions = get_open_positions(exclude_test=True)
        journal_holdings = {}
        for p in open_positions:
            sym = (p.get('coin_symbol') or '').strip().upper()
            if not sym:
                continue
            qty = round(float(p.get('quantity', 0) or 0), 8)
            journal_holdings[sym] = round(journal_holdings.get(sym, 0) + qty, 8)
        journal_holdings = {s: round(q, 8) for s, q in journal_holdings.items()}
        acct_cfg = load_account_config()
        dust_threshold = float(acct_cfg.get('dust_threshold_usd', 1.0) or 1.0)
        price_by_symbol = _price_by_symbol_from_cached()

        def value_usd(sym, qty):
            p = price_by_symbol.get((sym or '').strip().upper())
            return None if p is None else float(qty) * float(p)

        def qty_matched(jq, eq):
            if jq <= 0 and eq <= 0:
                return True
            if jq <= 0 or eq <= 0:
                return False
            return abs(float(jq) - float(eq)) < 1e-8

        external, in_journal_only, quantity_mismatch = [], [], []
        for sym, qty in exchange_holdings.items():
            if qty <= 0:
                continue
            jq = journal_holdings.get(sym, 0)
            if jq <= 0:
                v = value_usd(sym, qty)
                if v is not None and v < dust_threshold:
                    continue
                if v is None:
                    continue
                external.append({"type": "external", "symbol": sym, "exchange_quantity": round(qty, 8)})
            elif not qty_matched(jq, qty):
                delta_usd = value_usd(sym, abs(jq - qty))
                if delta_usd is not None and delta_usd >= dust_threshold:
                    quantity_mismatch.append({"type": "quantity_mismatch", "symbol": sym, "journal_quantity": round(jq, 8), "exchange_quantity": round(qty, 8)})
        for sym, qty in journal_holdings.items():
            if qty <= 0:
                continue
            eq = exchange_holdings.get(sym, 0)
            if eq <= 0:
                v = value_usd(sym, qty)
                if v is not None and v < dust_threshold:
                    continue
                if v is None:
                    continue
                in_journal_only.append({"type": "in_journal_not_on_exchange", "symbol": sym, "journal_quantity": round(qty, 8)})
            elif not qty_matched(qty, eq) and not any(m.get("symbol") == sym for m in quantity_mismatch):
                delta_usd = value_usd(sym, abs(qty - eq))
                if delta_usd is not None and delta_usd >= dust_threshold:
                    quantity_mismatch.append({"type": "quantity_mismatch", "symbol": sym, "journal_quantity": round(qty, 8), "exchange_quantity": round(eq, 8)})
        discrepancies = external + in_journal_only + quantity_mismatch
        status = "clean" if not discrepancies else "discrepancy_detected"
        return {"status": status, "discrepancies": discrepancies, "dashboard_open_positions_count": len(open_positions), "exchange_open_positions_count": len([s for s, q in exchange_holdings.items() if q > 0])}
    except Exception as e:
        logger.warning("Reconcile status check failed: %s", e)
        return {"status": "error", "discrepancies": [], "error": str(e)}


# reconcile_account + sync_position_quantities → routes/account_bp.py


def _execution_summary_data(days: int = 7):
    """Read execution_audit.jsonl (and execution_log.jsonl), filter by last N days, return aggregated summary."""
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    audit_path = os.path.join(data_dir, "execution_audit.jsonl")
    log_path = os.path.join(data_dir, "execution_log.jsonl")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z") if days else None
    fills = []
    fees_total = 0.0
    slippage_bps_list = []
    by_action = {}
    if os.path.isfile(audit_path):
        with open(audit_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                ts = entry.get("ts") or ""
                if since and ts < since:
                    continue
                action = entry.get("action") or "unknown"
                by_action[action] = by_action.get(action, 0) + 1
                fill_price = entry.get("fill_price")
                if fill_price is not None:
                    fills.append(entry)
                fee_usd = entry.get("fee_usd")
                if fee_usd is not None:
                    fees_total += float(fee_usd)
                sbps = entry.get("slippage_bps")
                if sbps is not None:
                    slippage_bps_list.append(float(sbps))
    order_count = 0
    if os.path.isfile(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                ts = entry.get("ts") or entry.get("timestamp") or ""
                if since and ts < since:
                    continue
                order_count += 1
    return {
        "days": days,
        "fill_count": len(fills),
        "order_count": order_count,
        "total_fees_usd": round(fees_total, 4),
        "slippage_bps_avg": round(sum(slippage_bps_list) / len(slippage_bps_list), 2) if slippage_bps_list else None,
        "slippage_bps_min": round(min(slippage_bps_list), 2) if slippage_bps_list else None,
        "slippage_bps_max": round(max(slippage_bps_list), 2) if slippage_bps_list else None,
        "by_action": by_action,
    }


@app.route('/api/reports/execution-summary', methods=['GET'])
def report_execution_summary():
    """GET ?days=7&export=0|1 or format=csv. Returns JSON or CSV download."""
    try:
        days = int(request.args.get("days", 7))
        export = request.args.get("export", "").strip().lower() in ("1", "true", "yes")
        fmt = (request.args.get("format", "") or "").strip().lower()
        do_csv = export or fmt == "csv"
        data = _execution_summary_data(days)
        if do_csv:
            import io
            buf = io.StringIO()
            buf.write("metric,value\n")
            buf.write(f"days,{data['days']}\n")
            buf.write(f"fill_count,{data['fill_count']}\n")
            buf.write(f"order_count,{data['order_count']}\n")
            buf.write(f"total_fees_usd,{data['total_fees_usd']}\n")
            buf.write(f"slippage_bps_avg,{data.get('slippage_bps_avg') or ''}\n")
            buf.write(f"slippage_bps_min,{data.get('slippage_bps_min') or ''}\n")
            buf.write(f"slippage_bps_max,{data.get('slippage_bps_max') or ''}\n")
            for action, count in (data.get("by_action") or {}).items():
                buf.write(f"by_action_{action},{count}\n")
            body = buf.getvalue()
            return Response(body, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=execution_summary.csv"})
        return jsonify(data)
    except Exception as e:
        logger.error("Execution summary report error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/reports/reconciliation-history', methods=['GET'])
def report_reconciliation_history():
    """GET ?limit=50. Returns last N lines from reconciliation_history.jsonl (newest last)."""
    try:
        limit = int(request.args.get("limit", 50))
        if limit <= 0 or limit > 500:
            limit = 50
        lines = []
        if os.path.isfile(_RECONCILIATION_HISTORY_FILE):
            with open(_RECONCILIATION_HISTORY_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        lines.append(line)
        # Return last N (newest at end of file)
        selected = lines[-limit:] if len(lines) > limit else lines
        out = []
        for line in selected:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return jsonify({"entries": out, "count": len(out)})
    except Exception as e:
        logger.error("Reconciliation history report error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/position-size/settings', methods=['GET'])
def get_position_sizer_settings():
    """Get current position sizer configuration"""
    return jsonify(position_sizer.get_settings())


@app.route('/api/position-size/update', methods=['POST'])
def update_position_sizer_settings():
    """
    Update account size or risk percentage.
    POST body: {account_size?, max_risk_pct?}
    """
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        if 'account_size' in data:
            position_sizer.update_account_size(float(data['account_size']))
        
        if 'max_risk_pct' in data:
            position_sizer.max_risk_pct = float(data['max_risk_pct'])
            position_sizer.max_risk_usd = position_sizer.account_size * (position_sizer.max_risk_pct / 100)
        
        return jsonify(position_sizer.get_settings())
    except Exception as e:
        logger.error(f"Error updating position sizer settings: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/execution/unregister-pending', methods=['POST'])
def execution_unregister_pending():
    """
    Remove a symbol from the pending TWAP/order list. Use when an order filled on the exchange
    but the app did not get fill confirmation (e.g. Coinbase returned NOT_FOUND on poll).
    Body: { "symbol": "AAVE" }. After unregistering, if you need the position in the journal,
    use POST /api/journal/open-from-exchange with symbol and quantity from your exchange balance.
    """
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json() or {}
        symbol = (data.get('symbol') or '').strip().upper()
        if not symbol:
            return jsonify({'error': 'symbol required'}), 400
        was_pending = unregister_pending_order(symbol)
        return jsonify({
            'status': 'ok',
            'symbol': symbol,
            'was_pending': was_pending,
            'message': 'Removed from pending.' if was_pending else 'Symbol was not in pending list.',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/execution-config', methods=['GET', 'POST'])
def execution_config_api():
    """GET: current auto-execution config. POST: set. Body: { auto_execution_enabled, auto_execution_exchange, auto_sync_balance, twap_min_usd, stale_position_rule_enabled }."""
    if request.method == 'GET':
        return jsonify(get_execution_config())
    try:
        guard = require_admin_write_access()
        if guard is not None:
            return guard
        data = request.get_json() or {}
        before_cfg = get_execution_config()
        req_auto_exec = data.get('auto_execution_enabled') if 'auto_execution_enabled' in data else None
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr) or 'unknown'
        user_agent = request.headers.get('User-Agent', 'unknown')

        # Hard guard: OFF must be an explicit dashboard/manual action.
        if req_auto_exec is False:
            disable_intent = (data.get('disable_intent') or '').strip().lower()
            disable_confirmed = data.get('disable_confirmed') is True
            if disable_intent not in ('dashboard_manual',) or not disable_confirmed:
                logger.warning(
                    "execution_config: REJECT disable auto_exec prev=%s requested=%s ip=%s ua=%r reason=missing_or_invalid_disable_contract",
                    before_cfg.get('auto_execution_enabled'),
                    req_auto_exec,
                    client_ip,
                    user_agent,
                )
                return jsonify({'error': 'Disable rejected: explicit disable_intent and disable_confirmed=true are required'}), 400

        ok = set_execution_config(
            auto_execution_enabled=req_auto_exec,
            auto_execution_exchange=data.get('auto_execution_exchange') if 'auto_execution_exchange' in data else None,
            auto_sync_balance=data.get('auto_sync_balance') if 'auto_sync_balance' in data else None,
            twap_min_usd=data.get('twap_min_usd') if 'twap_min_usd' in data else None,
            stale_position_rule_enabled=data.get('stale_position_rule_enabled') if 'stale_position_rule_enabled' in data else None,
            audit_source='execution_config_api',
            audit_extra={'ip': client_ip, 'ua': user_agent},
        )
        if not ok:
            logger.warning(
                "execution_config: REJECT save_failed prev=%s requested=%s ip=%s ua=%r",
                before_cfg.get('auto_execution_enabled'),
                req_auto_exec,
                client_ip,
                user_agent,
            )
            return jsonify({'error': 'Failed to save execution config'}), 500
        after_cfg = get_execution_config()
        logger.info(
            "execution_config: ACCEPT prev=%s new=%s requested=%s ip=%s ua=%r",
            before_cfg.get('auto_execution_enabled'),
            after_cfg.get('auto_execution_enabled'),
            req_auto_exec,
            client_ip,
            user_agent,
        )
        return jsonify(after_cfg)
    except Exception as e:
        return jsonify({'error': str(e)}), 500




@app.route('/api/telegram-webhook', methods=['POST'])
def telegram_webhook():
    """
    Telegram bot webhook. Replies to messages (e.g. status requests).
    Requires a PUBLIC URL — Telegram cannot reach localhost. Use ngrok or deploy, then:
    https://api.telegram.org/bot<TOKEN>/setWebhook?url=<your-public-url>/api/telegram-webhook
    Set env TELEGRAM_WEBHOOK_SECRET_TOKEN to the same value passed as secret_token in setWebhook;
    Telegram sends it on each request as header X-Telegram-Bot-Api-Secret-Token.
    """
    import hmac

    expected = (os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN") or "").strip()
    if not expected:
        return jsonify({
            "ok": False,
            "error": "Telegram webhook disabled: set TELEGRAM_WEBHOOK_SECRET_TOKEN and register it via setWebhook secret_token",
        }), 503
    provided = (request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        return jsonify({"ok": False, "error": "Unauthorized"}), 403
    try:
        data = request.get_json() or {}
        message = data.get('message') or {}
        text = (message.get('text') or '').strip()
        chat_id = message.get('chat', {}).get('id')
        if not text and not chat_id:
            return jsonify({'ok': True})
        if not chat_id:
            return jsonify({'ok': True})
        logger.info(f"  📩 Telegram webhook: chat_id={chat_id} text={text[:50]!r}")
        # (Kill switch removed — no special command handling needed)
        return jsonify({'ok': True})
    except Exception as e:
        logger.error(f"Telegram webhook error: {e}")
        return jsonify({'ok': True})

@app.route('/api/test-mode', methods=['GET', 'POST'])
def test_mode_api():
    """GET: test mode + paper flags. POST: set test_mode and/or paper_trading / paper_trade_notional_usd.
    Body: { test_mode?: bool, paper_trading?: bool, paper_trade_notional_usd?: number, close_test_positions?: true }.
    When turning test mode OFF, pass close_test_positions: true to close open test positions (not paper)."""
    if request.method == 'GET':
        return jsonify({
            'test_mode': get_test_mode(),
            'paper_trading': get_paper_trading(),
            'paper_trade_notional_usd': get_paper_trade_notional_usd(),
        })
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json() or {}
        has_tm = 'test_mode' in data
        has_paper = 'paper_trading' in data or 'paper_trade_notional_usd' in data
        if not has_tm and not has_paper:
            return jsonify({'error': 'Missing test_mode or paper fields'}), 400
        if has_tm:
            new_mode = bool(data['test_mode'])
            set_test_mode(new_mode)
            closed_count = 0
            if not new_mode and data.get('close_test_positions'):
                open_all = get_open_positions(exclude_test=False)
                for p in open_all:
                    if p.get('is_test'):
                        closed, _ = close_position(
                            p['id'],
                            exit_price=float(p.get('entry_price') or 0),
                            exit_reason='test_cleanup',
                            notes='Closed when switching Test Mode off',
                        )
                        if closed:
                            closed_count += 1
        else:
            closed_count = 0
        if 'paper_trading' in data:
            set_paper_trading(bool(data['paper_trading']))
        if 'paper_trade_notional_usd' in data:
            ok = set_paper_trade_notional_usd(float(data['paper_trade_notional_usd']))
            if not ok:
                return jsonify({'error': 'Invalid paper_trade_notional_usd'}), 400
        out = {
            'test_mode': get_test_mode(),
            'paper_trading': get_paper_trading(),
            'paper_trade_notional_usd': get_paper_trade_notional_usd(),
        }
        if closed_count:
            out['closed_test_positions'] = closed_count
        return jsonify(out)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/testing/log')
def testing_log_api():
    """Return last 20 signals for Testing Dashboard."""
    limit = request.args.get('limit', 20, type=int)
    limit = min(max(1, limit), 100)
    return jsonify({'signals': get_signal_log(limit=limit)})


@app.route('/api/testing/stats')
def testing_stats_api():
    """Return paper trading stats (signals logged, hypothetical win rate)."""
    return jsonify(get_testing_stats())


@app.route('/api/testing/positions')
def testing_positions_api():
    """Return test positions only (for Test tab). Open and closed."""
    try:
        positions = get_test_positions()
        prices = _live_price_map()
        if not prices and any(p.get('status') == 'OPEN' for p in positions):
            try:
                n_enhanced = 5 if get_test_mode() else 10
                raw = fetcher.get_all_data(n_enhanced=n_enhanced)
                prices = {c['id']: c.get('current_price') for c in raw.get('coins', []) if c.get('id') and c.get('current_price') is not None}
            except Exception:
                pass
        positions = enrich_open_positions(positions, prices)
        return jsonify({'positions': positions})
    except Exception as e:
        logger.error(f"Error getting test positions: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/testing/export/csv')
def testing_export_csv():
    """Download testing signal log as CSV."""
    csv_content = export_signal_log_csv()
    from flask import make_response
    resp = make_response(csv_content)
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename=testing_signal_log.csv'
    return resp


@app.route('/api/testing/documentation')
def testing_documentation():
    """Download test documentation (TESTING_PLAN.md) — automated test suite, exit-strategy tests, run commands."""
    testing_plan_path = os.path.join(os.path.dirname(__file__), 'TESTING_PLAN.md')
    try:
        with open(testing_plan_path, 'r', encoding='utf-8') as f:
            content = f.read()
        from flask import make_response
        resp = make_response(content)
        resp.headers['Content-Type'] = 'text/markdown; charset=utf-8'
        resp.headers['Content-Disposition'] = 'attachment; filename=TESTING_PLAN.md'
        return resp
    except FileNotFoundError:
        return jsonify({'error': 'TESTING_PLAN.md not found'}), 404


# =============================================================================
# Routes — Backtesting
# =============================================================================

@app.route('/api/backtest/run', methods=['POST'])
def run_backtest():
    """
    Run comprehensive backtest.
    POST body: {days: 30|60|90}
    """
    try:
        guard = require_admin_write_access()
        if guard:
            return guard
        data = request.get_json() or {}
        days = int(data.get('days', 30))
        
        report = backtester.run_full_backtest(days=days)
        save_backtest_expectancy(report)
        return jsonify(report)
    except Exception as e:
        logger.error(f"Error running backtest: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest/suggestions')
def get_backtest_suggestions():
    """Get optimization suggestions from backtest"""
    try:
        suggestions = backtester.get_optimization_suggestions()
        return jsonify(suggestions)
    except Exception as e:
        logger.error(f"Error getting backtest suggestions: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest/performance')
def get_performance_stats():
    """Get performance stats (consensus engine removed — returns empty)."""
    return jsonify({'note': 'AI consensus engine removed. Use trade journal for performance stats.'})


@app.route('/api/adversarial/report')
def get_adversarial_report():
    """Get latest adversarial backtester report (failure cells, sensitivity, structural issues, quick wins)."""
    try:
        report_path = os.path.join(os.path.dirname(__file__), 'data', 'adversarial_report.json')
        if not os.path.exists(report_path):
            return jsonify({
                'generated_at': None,
                'baseline': None,
                'failure_cells': [],
                'structural_issues': [],
                'quick_wins': [],
                'parameter_sensitivity': [],
                'note': 'No adversarial report yet. Run: python scripts/adversarial_backtest.py',
            })
        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
        return jsonify(report)
    except Exception as e:
        logger.error(f"Error loading adversarial report: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/learning-report')
def get_learning_report():
    """Get latest ML learning report (backtest summary, missed opportunities, suggestions). Includes stress report summary if present."""
    try:
        report_path = os.path.join(os.path.dirname(__file__), 'data', 'learning_report.json')
        if not os.path.exists(report_path):
            out = {
                'run_timestamp': None,
                'backtest': None,
                'backtest_suggestions': [],
                'missed_opportunity': None,
                'suggestions': [],
                'note': 'No report yet. Run: python -m ml.run_pipeline',
            }
        else:
            with open(report_path, 'r', encoding='utf-8') as f:
                out = json.load(f)
        out['stress_report'] = _load_stress_report_summary()
        return jsonify(out)
    except Exception as e:
        logger.error(f"Error loading learning report: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/monthly-review')
def monthly_review_api():
    """
    Monthly review: key metrics, model attribution, regime performance, risk metrics.
    Query param: days=30|90|365|0 (0 = all-time).
    Returns JSON report for dashboard or inclusion in daily status.
    """
    try:
        days_arg = request.args.get('days', '30')
        days = int(days_arg) if days_arg and days_arg != '0' else None  # None = all-time
        report = _build_monthly_review(days=days)
        return jsonify(report)
    except Exception as e:
        logger.error(f"Error building monthly review: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _build_monthly_review(days: int = 30) -> dict:
    """Build monthly review report: performance analytics, model attribution, regime, risk.
    days: 30, 90, 365, or None for all-time."""
    all_pos = get_all_positions()
    live_pos = [p for p in all_pos if not (p.get('is_test') or p.get('exit_reason') == 'test_cleanup')]
    ov = _build_account_overview()
    account_size = float(ov.get('total_account_value', 0) or ov.get('total_cash', 0) or 0)
    open_positions = get_open_positions(exclude_test=True)
    coins_with_recs = cached_data.get('coins') or []

    # Filter positions by exit_time for closed (for period-specific metrics)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff.strftime('%Y-%m-%d')
        filtered = [p for p in live_pos if p.get('status') == 'OPEN' or (p.get('exit_time') or '')[:10] >= cutoff_str]
    else:
        filtered = live_pos

    # System-only: exclude manual-override closes so metrics match recursive report (single source = system trades)
    system_filtered = [p for p in filtered if p.get('status') != 'CLOSED' or not p.get('manual_override')]

    # Performance analytics (from journal, system trades only)
    perf = {}
    try:
        from performance_analytics import PerformanceAnalytics
        perf = PerformanceAnalytics.from_positions(system_filtered, account_size)
    except Exception:
        pass

    # Model attribution
    attr = {}
    try:
        from model_attribution import get_model_attribution
        attr = get_model_attribution(days=days if days is not None else 3650)  # 3650 = ~10y for all-time
    except Exception:
        pass

    # Portfolio risk (with closed positions for historical VaR when available)
    risk = {}
    try:
        closed = [p for p in filtered if p.get('status') == 'CLOSED']
        risk = get_portfolio_risk_summary(open_positions, coins_with_recs, account_size, closed_positions=closed)
    except Exception:
        pass

    # Stats from journal (period-filtered, system trades only)
    stats = calculate_stats(system_filtered)

    period_label = f'last_{days}_days' if days else 'all_time'
    return {
        'period': period_label,
        'period_days': days,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'performance': {
            'sharpe_ratio': perf.get('sharpe_ratio', 0),
            'sortino_ratio': perf.get('sortino_ratio', 0),
            'max_drawdown_pct': perf.get('max_drawdown_pct', 0),
            'expectancy_pct': perf.get('expectancy_pct', 0),
            'expectancy_usd': perf.get('expectancy_usd', 0),
            'win_rate_pct': stats.get('win_rate_pct', 0),
            'total_pnl_usd': stats.get('total_pnl_usd', 0),
            'trades': perf.get('trades', 0),
            'win_rate_by_regime': perf.get('win_rate_by_regime', {}),
            'win_rate_by_regime_scenario': perf.get('win_rate_by_regime_scenario', {}),
        },
        'model_attribution': {
            'by_model': attr.get('by_model', {}),
            'monthly_ranking': attr.get('monthly_ranking', []),
            'recommendations': attr.get('recommendations', []),
        },
        'risk': {
            'total_risk_pct': risk.get('total_risk_pct', 0),
            'concentration_breaches': risk.get('concentration_breaches', []),
            'var_1d_usd': risk.get('var_1d_usd', 0),
            'max_single_position_pct': risk.get('max_single_position_pct', 0),
        },
        'stress_report': _load_stress_report_summary(),
    }


def _load_stress_report_summary():
    """Load 30d/60d stress report summaries from data/ if files exist (for monthly review / learning report overlap)."""
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    out = {}
    for d in (30, 60):
        path = os.path.join(data_dir, f'buy_signals_stress_{d}d.json')
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                out[f'{d}d'] = data.get('summary', {})
            except Exception:
                pass
    return out


# =============================================================================
# Routes — Trade Journal
# =============================================================================

def _live_price_map():
    """Helper: build {coin_id: price} dict"""
    return {c['id']: c.get('current_price') for c in cached_data.get('coins', []) if c.get('id')}


# get_journal_positions → routes/journal_bp.py


# journal_place_sl_tp → routes/journal_bp.py


# journal_open_position → routes/journal_bp.py


# journal_close_position → routes/journal_bp.py


# journal_update_targets → routes/journal_bp.py


# journal_update_position → routes/journal_bp.py


# journal_delete_position → routes/journal_bp.py


# journal_export_csv → routes/journal_bp.py


# journal_export_xlsx → routes/journal_bp.py


# =============================================================================
# Routes — AI Analysis
# =============================================================================

@app.route('/api/ai/analyze/<coin_id>', methods=['GET'])
def analyze_coin(coin_id):
    """Analyze a specific coin with quant signal + veto on demand."""
    try:
        if not cached_data or not cached_data.get('coins'):
            return jsonify({'success': False, 'error': 'No data loaded yet. Click "Refresh Now" first.'}), 400

        coins = cached_data.get('coins', [])
        coin = next((c for c in coins if c.get('id') == coin_id), None)
        if not coin:
            return jsonify({'success': False, 'error': f'Coin {coin_id} not found.'}), 404

        regime_data = cached_data.get('regime', {})
        logger.info(f"\n[Quant Analysis] Analyzing {coin.get('name')}...")

        fear_greed_value = (cached_data.get('fear_greed') or {}).get('value', 50)
        consensus_result = {
            'consensus_signal': None,
            'reasoning': f"Regime: {regime_data.get('regime', 'SIDEWAYS')}. Pure quant + veto.",
            'regime_filter': f"Regime: {regime_data.get('regime', 'SIDEWAYS')}",
        }
        overview = _build_account_overview()
        cash_available = overview.get('cash_available_to_trade')
        final_signal, quant = apply_full_veto_logic(
            coin, regime_data, consensus_result, fear_greed_value,
            position_sizer=position_sizer, cash_available=cash_available,
        )
        consensus_signal = final_signal
        dynamic_targets = None
        if consensus_signal in ['ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY']:
            try:
                dynamic_targets = target_calculator.calculate_targets(
                    entry_price=coin.get('current_price', 0),
                    volatility_pct=coin.get('indicators', {}).get('volatility', 50),
                    regime=regime_data.get('regime', 'SIDEWAYS'),
                    consensus_signal=consensus_signal
                )
            except Exception as e:
                logger.warning(f"  Warning: dynamic targets: {e}")

        position_calc = quant.get('position_sizing') if quant else None
        if consensus_signal in ['ULTRA_BUY', 'STRONG_BUY', 'MODERATE_BUY'] and not position_calc:
            stop_loss_pct = dynamic_targets['stop_loss_pct'] if dynamic_targets else 2.5
            position_calc = position_sizer.calculate_for_consensus(
                entry_price=coin.get('current_price'),
                stop_loss_pct=stop_loss_pct,
                consensus_signal=consensus_signal,
                regime_data=regime_data,
                coin_volatility=coin.get('indicators', {}).get('volatility', 50),
                cash_available=cash_available,
                edge_probability=quant.get('edge_probability'),
                rr_ratio=(coin.get('risk') or {}).get('rr_ratio'),
            )

        quant_response = dict(quant) if quant else {}
        quant_response.setdefault('quant_path', quant_response.get('quant_path', 'default'))
        quant_response.setdefault('edge_probability', 0.0)
        quant_response.setdefault('confirmation_score', 0.0)
        quant_response.setdefault('short_tf_sniper', {})
        return jsonify({
            'success': True,
            'coin_id': coin_id,
            'coin_name': coin.get('name'),
            'coin_symbol': coin.get('symbol'),
            'current_price': coin.get('current_price'),
            'consensus': consensus_result,
            'quant': quant_response,
            'dynamic_targets': dynamic_targets,
            'position_sizing': position_calc,
            'regime': regime_data,
            'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })

    except Exception as e:
        logger.error(f"Error analyzing {coin_id}: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai/top-picks', methods=['GET'])
def get_top_picks():
    """
    Get AI-analyzed top picks from the current market data.
    Called when user clicks "Get AI Top Picks" button.
    """
    try:
        # Check if data is loaded
        if not cached_data or not cached_data.get('coins'):
            return jsonify({
                'success': False,
                'picks': [],
                'message': 'No data loaded yet. Click "Refresh Now" first.'
            }), 400
        
        coins = cached_data.get('coins', [])
        
        # Filter to coins with AI recommendations
        picks = [c for c in coins if c.get('ai_recommendation') is not None]
        
        # If no AI-analyzed coins found
        if not picks:
            return jsonify({
                'success': True,
                'picks': [],
                'message': 'No AI-analyzed coins available. AI analysis runs on BUY signals only.',
                'total_analyzed': 0
            })
        
        # Sort by consensus signal quality
        signal_priority = {
            'ULTRA_BUY': 4,
            'STRONG_BUY': 3,
            'MODERATE_BUY': 2,
            'WEAK_BUY': 1,
            'HOLD': 0,
            'SELL': -1
        }
        
        picks.sort(
            key=lambda c: (
                signal_priority.get(
                    c.get('ai_recommendation', {}).get('consensus', {}).get('consensus_signal', 'HOLD'),
                    0
                ),
                c.get('score', 0)
            ),
            reverse=True
        )
        
        # Return top 5; ensure quant has final-polish fields
        top_picks = picks[:5]
        for p in top_picks:
            rec = p.get('ai_recommendation') or {}
            q = rec.get('quant')
            if isinstance(q, dict):
                q.setdefault('quant_path', 'default')
                q.setdefault('edge_probability', 0.0)
                q.setdefault('confirmation_score', 0.0)
                q.setdefault('short_tf_sniper', {})

        return jsonify({
            'success': True,
            'picks': top_picks,
            'total_analyzed': len(picks),
            'regime': cached_data.get('regime', {}),
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        
    except Exception as e:
        logger.error(f"❌ Error getting top picks: {e}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'picks': [],
            'error': str(e)
        }), 500




# =============================================================================
# Routes — User Manual
# =============================================================================

@app.route('/api/manual')
def get_manual():
    """Get user manual as JSON"""
    try:
        return jsonify(manual_generator.manual_content)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/manual/export/docx')
def export_manual_docx():
    """Download user manual as Word document. Also writes a copy to docs/ when possible."""
    try:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
            tmp_path = tmp.name

        manual_generator.generate_docx(tmp_path)

        with open(tmp_path, 'rb') as f:
            docx_data = f.read()

        os.unlink(tmp_path)

        # Keep a copy in docs/ for consistency
        docs_dir = os.path.join(os.path.dirname(__file__), 'docs')
        docs_path = os.path.join(docs_dir, 'Elite_Trading_Manual.docx')
        try:
            os.makedirs(docs_dir, exist_ok=True)
            with open(docs_path, 'wb') as f:
                f.write(docx_data)
        except Exception:
            pass

        filename = f'Elite_Trading_Manual_{datetime.now().strftime("%Y%m%d")}.docx'
        return Response(
            docx_data,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# XRPL Ledger tab: snapshot + events for the account in XRPL_ACCOUNT (.env)
try:
    from xrpl_ledger import get_snapshot_with_value, get_account_events
except ImportError:
    get_snapshot_with_value = get_account_events = None


@app.route('/api/xrpl/snapshot')
def api_xrpl_snapshot():
    """XRPL account snapshot: positions, XRP balance, value now and over 1d/1w/30d/90d/1y."""
    if get_snapshot_with_value is None:
        return jsonify({'error': 'xrpl_ledger module not available'}), 503
    try:
        account = request.args.get('account', '').strip() or None
        data = get_snapshot_with_value(account)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/xrpl/events')
def api_xrpl_events():
    """XRPL account transaction events (paginated)."""
    if get_account_events is None:
        return jsonify({'error': 'xrpl_ledger module not available'}), 503
    try:
        account = request.args.get('account', '').strip() or None
        limit = request.args.get('limit', 100, type=int)
        marker = request.args.get('marker', '').strip() or None
        data = get_account_events(account=account, limit=limit, marker=marker)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


class _QuietPollingFilter(logging.Filter):
    """Suppress Werkzeug request logs for high-frequency polling endpoints."""

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return '/api/data' not in msg and '/api/health' not in msg


def _build_data_sources_tab_rows():
    """
    Narrative rows for the Data Sources tab, overlaid with live pipeline + HTTP resilience state.
    Static copy explains edge; live_* fields reflect this process (see /api/health).
    """
    from resilient_http import api_health_report, get_degraded_sources

    rows_spec = [
        {
            "source": "Kraken Order Book",
            "pipeline_key": None,
            "resilient_key": "kraken_spot",
            "what_it_provides": "Real depth (10 bids/asks) + 24h volume",
            "edge_contribution": "Liquidity / Slippage Edge",
            "impact": "+12–18% probability boost when ratio >1.4 and slippage <0.4%. Optimizer learns real fill risk.",
        },
        {
            "source": "Kraken Futures",
            "pipeline_key": None,
            "resilient_key": "kraken_futures",
            "what_it_provides": "Perpetual funding rates (public API)",
            "edge_contribution": "Funding Veto & Regime",
            "impact": "BEAR + high funding triggers HOLD. Used in veto and quant.",
        },
        {
            "source": "CoinGecko",
            "pipeline_key": "coingecko",
            "resilient_key": "coingecko",
            "what_it_provides": "Price + 1h/4h/24h/7d/30d + sparkline",
            "edge_contribution": "Price Accuracy + Trend Strength",
            "impact": "Core input for regime, dynamic targets, regression scorer.",
        },
        {
            "source": "CoinPaprika",
            "pipeline_key": None,
            "resilient_key": "coinpaprika",
            "role": "fallback",
            "what_it_provides": "Top coins + volume-weighted tickers",
            "edge_contribution": "Volume Profile & Market Cap Rank",
            "impact": "Improves volume_accum_shift and regime detection.",
        },
        {
            "source": "CoinCap",
            "pipeline_key": None,
            "resilient_key": "coincap",
            "role": "fallback",
            "what_it_provides": "Historical % changes (fallback only)",
            "edge_contribution": "Historical % Changes",
            "impact": "Backup for trend alignment.",
        },
        {
            "source": "CoinMarketCap",
            "pipeline_key": None,
            "resilient_key": "coinmarketcap",
            "role": "optional",
            "what_it_provides": "Top coins + global market cap (optional fallback)",
            "edge_contribution": "Listings Fallback",
            "impact": "Third fallback for get_top_50 when CoinGecko/CoinPaprika fail. Requires COINMARKETCAP_API_KEY.",
        },
        {
            "source": "CoinGlass",
            "pipeline_key": None,
            "resilient_key": "coinglass",
            "role": "optional",
            "what_it_provides": "Aggregated funding across exchanges (optional)",
            "edge_contribution": "Multi-Venue Funding",
            "impact": "Optional worst-case funding when COINGLASS_API_KEY set; max(Kraken, CoinGlass) used.",
        },
        {
            "source": "Coinbase Exchange",
            "pipeline_key": None,
            "resilient_key": "coinbase_exchange",
            "what_it_provides": "Order book (optional) + position prices when execution is Coinbase",
            "edge_contribution": "Execution Venue & PnL",
            "impact": "Real-time PnL for open positions when using Coinbase. Optional second order book.",
        },
        {
            "source": "DeFi MCP Uniswap",
            "pipeline_key": None,
            "resilient_key": None,
            "what_it_provides": "Swap quote + gas + liquidity",
            "edge_contribution": "Execution Risk & On-Chain Flow",
            "impact": "Flags high-impact trades. Reduces slippage in position_sizer.",
        },
        {
            "source": "On-Chain (CryptoQuant)",
            "pipeline_key": "onchain",
            "resilient_key": "cryptoquant",
            "what_it_provides": "SOPR, LTH supply Δ, exchange netflow, whale netflow",
            "edge_contribution": "Whale Veto & Netflow Edge",
            "impact": "Hard veto on large moves. Dashboard shows \"On-Chain: Live\" or \"Fallback\".",
        },
        {
            "source": "Fear & Greed",
            "pipeline_key": "fear_greed",
            "resilient_key": "alternative_me",
            "what_it_provides": "Sentiment score 0–100",
            "edge_contribution": "Regime Modifier",
            "impact": "Prevents over-trading in extremes.",
        },
        {
            "source": "FRED Macro",
            "pipeline_key": "fred",
            "resilient_key": "fred",
            "what_it_provides": "Rates + inflation + yields",
            "edge_contribution": "Macro Regime Filter",
            "impact": "Protects against macro headwinds.",
        },
        {
            "source": "Alpha Vantage News",
            "pipeline_key": "alpha_vantage",
            "resilient_key": "alpha_vantage",
            "what_it_provides": "News feed → sentiment analysis",
            "edge_contribution": "News Sentiment",
            "impact": "Keyword-based sentiment from news articles. Feeds narrative_spike badge.",
        },
    ]

    api_report = api_health_report()
    degraded = get_degraded_sources()
    stale = {}
    try:
        stale = fetcher.get_stale_sources() if hasattr(fetcher, "get_stale_sources") else {}
    except Exception:
        pass
    with _cache_lock:
        pipeline_refresh = last_updated

    _stale_key_map = {
        "coingecko": "coins",
        "fear_greed": "fear_greed",
        "fred": "macro",
        "alpha_vantage": "sentiment",
        "kraken_futures": "funding",
        "kraken_spot": "order_book",
        "coinbase_exchange": "coinbase_spot",
    }

    out = []
    for spec in rows_spec:
        pk = spec.get("pipeline_key")
        rk = spec.get("resilient_key")
        pipe = data_source_status.get(pk) if pk else None
        http = api_report.get(rk) if rk else None

        status_parts = []
        feed_ok = "Unknown"
        last_touch = None

        if pipe:
            st = pipe.get("status") or "unknown"
            if st == "ok":
                status_parts.append("🟢 pipeline ok")
                feed_ok = "Yes"
            elif st == "warning":
                status_parts.append("🟡 pipeline warning")
                feed_ok = "Degraded"
            elif st == "error":
                status_parts.append("🔴 pipeline error")
                feed_ok = "No"
            else:
                status_parts.append("⚪ pipeline " + st)
            last_touch = pipe.get("time")
            err = pipe.get("error")
            if err:
                status_parts.append(f"({err[:80]})")

        if http:
            circ = http.get("circuit", "closed")
            successes = http.get("total_successes", 0)
            if circ == "open":
                status_parts.append("🔴 HTTP circuit OPEN")
                feed_ok = "No"
            elif circ == "half_open":
                status_parts.append("🟡 HTTP circuit half-open")
                if feed_ok != "No":
                    feed_ok = "Degraded"
            elif circ == "closed" and successes > 0:
                status_parts.append("🟢 HTTP ok")
                if feed_ok == "Unknown":
                    feed_ok = "Yes"
            elif circ == "closed":
                status_parts.append("⚪ awaiting first call")
            if http.get("degraded"):
                reason = http.get("degraded_reason") or "degraded"
                status_parts.append("⚠️ " + reason)
                if feed_ok in ("Yes", "Unknown"):
                    feed_ok = "Degraded"

        if rk and rk in degraded and not any("degraded" in str(p).lower() for p in status_parts):
            reason = degraded[rk].get("reason") or "resilience degraded"
            status_parts.append("⚠️ " + reason)
            if feed_ok == "Unknown":
                feed_ok = "Degraded"

        if spec["source"] == "DeFi MCP Uniswap":
            status_parts.append("ℹ️ not tracked in resilient_http (MCP / IDE)")
            feed_ok = "N/A"

        role = spec.get("role")
        if feed_ok == "Unknown" and role == "fallback":
            status_parts.append("💤 standby (primary sources healthy)")
            feed_ok = "Standby"
        elif feed_ok == "Unknown" and role == "optional":
            status_parts.append("💤 not activated")
            feed_ok = "N/A"

        status_display = " — ".join(status_parts) if status_parts else "⚪ no live signal yet (await refresh)"

        stale_key = _stale_key_map.get(pk) or _stale_key_map.get(rk)
        stale_hint = stale.get(stale_key) if stale_key else None

        row = {
            "source": spec["source"],
            "status": status_display,
            "last_updated": last_touch or "—",
            "feed_ok": feed_ok,
            "what_it_provides": spec["what_it_provides"],
            "edge_contribution": spec["edge_contribution"],
            "impact": spec["impact"],
            "live": {
                "pipeline_key": pk,
                "resilient_key": rk,
                "pipeline": pipe,
                "http_resilience": http,
                "stale_cache": stale_hint,
            },
        }
        out.append(row)

    return {
        "sources": out,
        "pipeline_last_refresh": pipeline_refresh,
        "note": "Narrative fields describe strategy intent; status/last_updated/live reflect this server instance (same signals as /api/health).",
    }


@app.route('/api/data/sources')
def get_data_sources():
    """Data Sources tab: narrative + live pipeline/resilience overlay (aligned with /api/health)."""
    try:
        payload = _build_data_sources_tab_rows()
        return jsonify(payload)
    except Exception as e:
        logger.error("get_data_sources: %s", e)
        traceback.print_exc()
        return jsonify({"error": str(e), "sources": []}), 500


@app.route('/api/tearsheets/<name>')
def serve_tearsheet(name):
    """Serve a quantstats tearsheet HTML file."""
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return jsonify({"error": "invalid tearsheet name"}), 400
    tearsheet_dir = os.path.join(DATA_DIR, 'tearsheets')
    path = os.path.join(tearsheet_dir, f'{name}.html')
    if not os.path.isfile(path):
        return jsonify({"error": "tearsheet not found"}), 404
    return send_file(path, mimetype='text/html')


if __name__ == '__main__':
    logging.getLogger('werkzeug').addFilter(_QuietPollingFilter())
    app.run(
        debug=False,
        port=5000,
        host='127.0.0.1',
        use_reloader=False,
    )
