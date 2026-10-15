# execution_engine.py — Optional auto-execution via CCXT (Coinbase Advanced Trade)
# TWAP: 4–6 orders over 30–60 min; logging; safety (6% risk cap, correlation limits)

import logging
import math
import os
import json
import time
import threading
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

# Owner-held coins that no code path may trade (buy, sell, stop, take-profit). Checked at
# the top of every function below that sends an order to an exchange — these four are the
# ONLY create_*order call sites in the codebase. See protected_assets.py.
from protected_assets import is_protected, refusal as _protected_refusal

logger = logging.getLogger(__name__)

# Optional callback set by app: called after TWAP completes to trigger balance sync
_on_twap_done_sync_balance = None
# Optional callback: called after TWAP buy completes with fill details for opening journal + placing SL/TP
_on_twap_done_open_position_and_place_orders = None

# ---------------------------------------------------------------------------
# Execution audit trail — forensic log for every order fill (thread-safe)
# ---------------------------------------------------------------------------
_AUDIT_LOG_FILE = os.path.join(os.path.dirname(__file__), 'data', 'execution_audit.jsonl')

_PENDING_JOURNAL_FILLS = os.path.join(os.path.dirname(__file__), 'data', 'pending_journal_fills.jsonl')
_pending_journal_lock = threading.Lock()

def _log_audit(entry: Dict[str, Any]) -> None:
    """Append one JSON line to data/execution_audit.jsonl (forensic trail). Thread-safe, auto-rotated."""
    import safe_json
    entry.setdefault("ts", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    ok = safe_json.append_jsonl(_AUDIT_LOG_FILE, entry, max_lines=10_000, default=str)
    if not ok:
        logger.error("CRITICAL: execution audit append failed — forensic trail may be incomplete")


_STOP_TELEMETRY_FILE = os.path.join(os.path.dirname(__file__), 'data', 'dynamic_stop_telemetry.jsonl')

def log_stop_telemetry(event: str, symbol: str, intended_stop: float,
                       actual_stop: float = 0.0, success: bool = True,
                       details: Optional[Dict] = None) -> None:
    """Log stop placement/reprice telemetry for roadmap rollback triggers.
    Events: 'place', 'reprice', 'breakeven', 'trailing_update'."""
    import safe_json
    entry = {
        'ts': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'event': event,
        'symbol': symbol,
        'intended_stop': intended_stop,
        'actual_stop': actual_stop,
        'slippage_pct': round(abs(actual_stop - intended_stop) / intended_stop * 100, 4) if intended_stop > 0 else 0.0,
        'success': success,
    }
    if details:
        entry.update(details)
    safe_json.append_jsonl(_STOP_TELEMETRY_FILE, entry, max_lines=5_000, default=str)


def _queue_for_journal(fill_data: dict) -> None:
    """Persist a TWAP fill that failed to journal/SL/TP so it can be retried automatically."""
    try:
        os.makedirs(os.path.dirname(_PENDING_JOURNAL_FILLS), exist_ok=True)
        entry = {**fill_data, "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "retry_count": 0}
        line = json.dumps(entry, default=str) + "\n"
        with _pending_journal_lock:
            with open(_PENDING_JOURNAL_FILLS, "a", encoding="utf-8") as f:
                f.write(line)
        logger.warning(f"  Post-TWAP callback failed — queued for retry: {fill_data.get('coin_symbol')}")
    except Exception as e:
        logger.error(f"  Failed to queue fill for journal: {e}")


def _drain_pending_journal_fills():
    """Background drainer thread (will be started from app.py). Retries up to 5 times."""
    while True:
        try:
            time.sleep(60)
            if not os.path.exists(_PENDING_JOURNAL_FILLS):
                continue
            with _pending_journal_lock:
                lines = []
                with open(_PENDING_JOURNAL_FILLS, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                if not lines:
                    continue
                new_lines = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        new_lines.append(line + "\n")
                        continue
                    try:
                        if callable(_on_twap_done_open_position_and_place_orders):
                            _on_twap_done_open_position_and_place_orders(data)
                        # success — drop the line
                    except Exception as e:
                        data["retry_count"] = data.get("retry_count", 0) + 1
                        if data["retry_count"] < 5:
                            new_lines.append(json.dumps(data, default=str) + "\n")
                        else:
                            logger.error(f"  Permanent failure on queued fill: {data.get('coin_symbol')}")
                with open(_PENDING_JOURNAL_FILLS, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
        except Exception as e:
            logger.warning(f"  Journal drainer error: {e}")


def _extract_fee(order: dict) -> Dict[str, Any]:
    """Extract fee info from a CCXT order response. Returns {fee_usd, fee_rate, fee_currency}."""
    fee_info: Dict[str, Any] = {"fee_usd": 0.0, "fee_rate": 0.0, "fee_currency": ""}
    try:
        fee = order.get("fee") or {}
        cost = float(fee.get("cost", 0) or 0)
        currency = (fee.get("currency") or "").upper()
        rate = float(fee.get("rate", 0) or 0)
        # CCXT returns fee.cost in fee.currency units; for USD/USDC it's already dollar-denominated
        if cost > 0:
            fee_info["fee_usd"] = round(cost, 6)
            fee_info["fee_rate"] = round(rate, 6) if rate else 0.0
            fee_info["fee_currency"] = currency
        # Some exchanges put fees in 'fees' (list) instead of 'fee'
        if cost == 0 and order.get("fees"):
            for f in order["fees"]:
                fc = float(f.get("cost", 0) or 0)
                if fc > 0:
                    fee_info["fee_usd"] += round(fc, 6)
                    fee_info["fee_currency"] = (f.get("currency") or "").upper()
    except Exception:
        pass
    return fee_info


def _compute_slippage_bps(fill_price: float, reference_price: float) -> Optional[float]:
    """Compute slippage in basis points: (fill - reference) / reference * 10000.
    Positive means paid more than reference (buys) or received less (sells)."""
    if not fill_price or not reference_price or reference_price <= 0:
        return None
    return round((fill_price - reference_price) / reference_price * 10000, 2)


# Optional: only load CCXT when exchange is used
def _ensure_ccxt():
    try:
        import ccxt
        return ccxt
    except ImportError as e:
        raise ImportError("pip install ccxt for auto-execution") from e

# -----------------------------------------------------------------------------
# Safety limits (from requirements)
# -----------------------------------------------------------------------------
MAX_TOTAL_RISK_PCT = 6.0   # Default for large accounts; small accounts use get_max_total_risk_pct()
CORRELATION_CAP_PCT = 35.0 # Respect portfolio correlation cap (no new size if breached)

# Dynamic risk cap by account size (small accounts get higher cap so they can trade).
# Small dedicated trading accounts need more headroom; capital sitting idle is itself a cost.
_RISK_TIERS = [
    (500, 50.0),
    (2000, 40.0),
    (5000, 25.0),
    (10000, 15.0),
]
_RISK_FLOOR = 6.0  # accounts >= $10k


def get_max_total_risk_pct(account_size: float) -> float:
    """Return max total risk % for auto-exec; higher for small accounts, 6% for large."""
    if account_size <= 0:
        return _RISK_FLOOR
    for threshold, cap in _RISK_TIERS:
        if account_size < threshold:
            return cap
    return _RISK_FLOOR


# -----------------------------------------------------------------------------
# Execution log (append JSONL)
# -----------------------------------------------------------------------------
EXECUTION_LOG_PATH = os.path.join(os.path.dirname(__file__), "data", "execution_log.jsonl")
SKIPPED_INSUFFICIENT_CASH_LOG = os.path.join(os.path.dirname(__file__), "data", "skipped_insufficient_cash.jsonl")

def _log_dir():
    d = os.path.join(os.path.dirname(__file__), "data")
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d

def log_execution(entry: Dict[str, Any]) -> None:
    _log_dir()
    line = json.dumps(entry, default=str) + "\n"
    try:
        with open(EXECUTION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.warning(f"  Execution log write failed: {e}")


def log_skipped_insufficient_cash(
    coin_id: str,
    coin_name: str,
    symbol: str,
    position_usd: float,
    cash_available: float,
    consensus_signal: str = "",
) -> None:
    """
    Append a skipped (good signal, not traded due to insufficient cash) to the backtester log.
    So you can see which good trades were missed and later compare outcomes.
    """
    _log_dir()
    entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": "insufficient_cash",
        "coin_id": coin_id,
        "coin_name": coin_name,
        "symbol": symbol,
        "position_usd": position_usd,
        "cash_available": cash_available,
        "consensus_signal": consensus_signal,
    }
    line = json.dumps(entry, default=str) + "\n"
    try:
        with open(SKIPPED_INSUFFICIENT_CASH_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.warning(f"  Skipped-cash log write failed: {e}")

# -----------------------------------------------------------------------------
# Coinbase Advanced Trade (CCXT)
# -----------------------------------------------------------------------------
PEM_BEGIN = "-----BEGIN EC PRIVATE KEY-----"
PEM_END = "-----END EC PRIVATE KEY-----"


def _clean_coinbase_secret(raw: str) -> str:
    """
    Clean Coinbase EC private key PEM for CCXT/ecdsa parsing.
    Handles extra whitespace, escaped \\n, and missing/duplicate headers.
    """
    if not raw or not raw.strip():
        return raw
    # Replace literal \n (from .env) with real newlines
    s = (raw.strip().replace("\\n", "\n").replace("\\r", "")
         .replace("\r\n", "\n").replace("\r", "\n"))
    lines = [ln.strip() for ln in s.split("\n") if ln.strip()]
    if not lines:
        return raw.strip()
    # Ensure proper PEM envelope
    if not any(PEM_BEGIN in ln for ln in lines):
        lines.insert(0, PEM_BEGIN)
    if not any(PEM_END in ln for ln in lines):
        lines.append(PEM_END)
    # Rebuild: first line must be exact BEGIN, last line exact END; end with newline for parsers
    if not lines[0].startswith("-----BEGIN"):
        lines[0] = PEM_BEGIN
    if not lines[-1].startswith("-----END"):
        lines[-1] = PEM_END
    return "\n".join(lines) + "\n"


# Cached exchange singletons (thread-safe; avoid re-creating CCXT instance per call)
_coinbase_exchange = None
_kraken_exchange = None
_exchange_lock = threading.Lock()


def get_coinbase_exchange() -> Any:
    """Build or return cached CCXT Coinbase (Advanced Trade) exchange. Keys from .env."""
    global _coinbase_exchange
    if _coinbase_exchange is not None:
        return _coinbase_exchange
    with _exchange_lock:
        if _coinbase_exchange is not None:
            return _coinbase_exchange
        ccxt = _ensure_ccxt()
        api_key = (os.getenv("COINBASE_API_KEY") or "").strip()
        api_secret_raw = (os.getenv("COINBASE_API_SECRET") or "").strip()
        if not api_key or not api_secret_raw:
            raise ValueError("COINBASE_API_KEY and COINBASE_API_SECRET must be set in .env for Coinbase execution")
        cleaned_secret = _clean_coinbase_secret(api_secret_raw)
        _coinbase_exchange = ccxt.coinbase({
            "apiKey": api_key,
            "secret": cleaned_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        return _coinbase_exchange


def get_kraken_exchange() -> Any:
    """Build or return cached CCXT Kraken exchange. Keys from .env."""
    global _kraken_exchange
    if _kraken_exchange is not None:
        return _kraken_exchange
    with _exchange_lock:
        if _kraken_exchange is not None:
            return _kraken_exchange
        ccxt = _ensure_ccxt()
        api_key = (os.getenv("KRAKEN_API_KEY") or "").strip()
        api_secret = (os.getenv("KRAKEN_API_SECRET") or "").strip()
        if not api_key or not api_secret:
            raise ValueError("KRAKEN_API_KEY and KRAKEN_API_SECRET must be set in .env for Kraken")
        _kraken_exchange = ccxt.kraken({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        return _kraken_exchange


def get_exchange_for_balance(exchange_id: str) -> Any:
    """Return CCXT exchange instance for balance fetch. exchange_id: 'coinbase' or 'kraken'."""
    ex = (exchange_id or "coinbase").lower()
    if ex == "kraken":
        return get_kraken_exchange()
    return get_coinbase_exchange()


def fetch_balance_sync(exchange_id: str) -> Dict[str, Any]:
    """
    Fetch balance from exchange via CCXT. Read-only; no orders.
    Returns: { "total_cash": float, "holdings": {symbol: quantity}, "exchange": str }.
    total_cash = sum of USD + USDC + USDT (total balance).
    holdings = all other spot currencies with total > 0 (for future open-positions reconciliation).
    """
    ex = (exchange_id or "coinbase").lower()
    try:
        exchange = get_exchange_for_balance(exchange_id)
        ex_name = exchange.id if hasattr(exchange, "id") else ex
        # Coinbase: force v3 API so response has has_next/cursor (v2 uses next_starting_after and we don't paginate v2).
        # Then paginate to get all accounts (v3 returns max 250 per page; see ccxt#20044, #16944).
        if ex == "coinbase":
            balance = exchange.fetch_balance({"v3": True})
        else:
            balance = exchange.fetch_balance()
        if ex == "coinbase":
            info = balance.get("info") or {}
            while info.get("has_next") and (info.get("cursor") or "").strip():
                next_balance = exchange.fetch_balance({"v3": True, "cursor": info.get("cursor", "")})
                for currency, data in (next_balance or {}).items():
                    if currency == "info":
                        continue
                    if currency is None:
                        continue
                    if not isinstance(data, dict):
                        continue
                    total = float(data.get("total", 0) or 0)
                    if total <= 0:
                        continue
                    existing = balance.get(currency)
                    if existing is None:
                        balance[currency] = dict(data)
                    else:
                        prev_total = float(existing.get("total", 0) or 0)
                        prev_free = float(existing.get("free", 0) or 0)
                        prev_used = float(existing.get("used", 0) or 0)
                        balance[currency] = {
                            "total": prev_total + total,
                            "free": prev_free + float(data.get("free", total) or 0),
                            "used": prev_used + float(data.get("used", 0) or 0),
                        }
                info = (next_balance or {}).get("info") or {}
                balance["info"] = info
    except IndexError as e:
        if ex == "coinbase":
            raise ValueError(
                "Coinbase key parsing failed — please regenerate the API key with 'Trade' permissions only."
            ) from e
        raise
    except Exception as e:
        if ex == "coinbase" and ("key" in str(e).lower() or "ecdsa" in str(e).lower() or "parse" in str(e).lower()):
            raise ValueError(
                "Coinbase key parsing failed — please regenerate the API key with 'Trade' permissions only."
            ) from e
        raise
    cash_currencies = ("USD", "USDC", "USDT")
    total_cash = 0.0
    holdings = {}
    # CCXT: balance[currency] = { "free", "used", "total" } or number
    for currency, data in balance.items():
        if currency == "info" or currency is None:
            continue
        total = float(data.get("total", 0) or 0) if isinstance(data, dict) else float(data or 0)
        if total <= 0:
            continue
        cur = (currency or "").upper()
        if cur in cash_currencies:
            total_cash += total
        else:
            holdings[cur] = total
    return {"total_cash": round(total_cash, 2), "holdings": holdings, "exchange": ex_name}

def symbol_to_ccxt(symbol: str, prefer_usdc: bool = True) -> str:
    """Convert dashboard symbol (e.g. BTC) to CCXT symbol. Prefers USDC pair for interest on dry powder."""
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    if "/" in s:
        return s
    return f"{s}/USDC" if prefer_usdc else f"{s}/USD"

def _place_market_buy_single(exchange: Any, symbol_ccxt: str, usd_amount: float) -> Dict[str, Any]:
    """Internal: place one market buy. Returns result dict with status, error, fee_usd, slippage_bps, etc.
    For Coinbase spot, passes cost (USD) via params so createOrder gets quote amount.
    If Coinbase returns before fill (filled=0), polls fetch_order once so journal gets actual filled quantity."""
    if is_protected(symbol_ccxt):
        return _protected_refusal(symbol_ccxt, "market buy")
    result = {"exchange": "coinbase", "symbol": symbol_ccxt, "side": "buy", "usd_amount": usd_amount}
    try:
        # Snapshot pre-trade mid price for slippage calc (best-effort from ticker)
        pre_trade_mid = 0.0
        try:
            ticker = exchange.fetch_ticker(symbol_ccxt)
            bid = float(ticker.get("bid", 0) or 0)
            ask = float(ticker.get("ask", 0) or 0)
            if bid > 0 and ask > 0:
                pre_trade_mid = (bid + ask) / 2
            elif ticker.get("last"):
                pre_trade_mid = float(ticker["last"])
        except Exception:
            pass

        # Coinbase spot market buy requires either price or cost; pass cost so amount is quote (USD)
        client_order_id = f"elite-buy-{symbol_ccxt}-{int(time.time()*1000)}"
        params = {"createMarketBuyOrderRequiresPrice": False, "client_order_id": client_order_id}
        order = exchange.create_market_buy_order(symbol_ccxt, usd_amount, params)

        result["order_id"] = order.get("id")
        result["status"] = order.get("status", "unknown")
        filled = float(order.get("filled", 0) or 0)
        result["filled_amount"] = filled
        raw_avg = float(order.get("average") or 0) if order.get("average") else 0
        result["average_price"] = raw_avg if raw_avg > 0 else (usd_amount / filled if filled > 0 else 0)

        # Extract fee from initial order response
        fee_info = _extract_fee(order)
        latest_order = order

        # Coinbase often returns order accepted but not yet filled; poll once so we log actual filled quantity
        if filled == 0 and result.get("order_id") and result.get("status") != "error":
            time.sleep(2)
            try:
                order2 = exchange.fetch_order(result["order_id"])
                if order2 and order2.get("status") in ("closed", "filled", "done"):
                    filled = float(order2.get("filled", 0) or 0)
                    if filled > 0:
                        result["filled_amount"] = filled
                        result["average_price"] = float(order2.get("average") or 0) or (usd_amount / filled)
                        result["status"] = order2.get("status", result["status"])
                        fee_info = _extract_fee(order2)
                        latest_order = order2
            except Exception:
                pass

        result["fee_usd"] = fee_info["fee_usd"]
        result["fee_rate"] = fee_info["fee_rate"]

        # Slippage: compare fill price vs pre-trade mid (not self-referential)
        result["slippage_bps"] = _compute_slippage_bps(result["average_price"], pre_trade_mid) if pre_trade_mid > 0 else None

        result["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Forensic audit
        _log_audit({
            "action": "market_buy",
            "order_id": result.get("order_id"),
            "client_order_id": client_order_id,
            "symbol": symbol_ccxt,
            "side": "buy",
            "usd_amount": usd_amount,
            "fill_price": result["average_price"],
            "filled_amount": result["filled_amount"],
            "fee_usd": result["fee_usd"],
            "fee_rate": result["fee_rate"],
            "slippage_bps": result["slippage_bps"],
            "pre_trade_mid": pre_trade_mid,
            "status": result["status"],
        })
        return result
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        result["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return result


def place_market_buy_usd(exchange: Any, symbol_ccxt: str, usd_amount: float) -> Dict[str, Any]:
    """
    Place a single market buy for approximately usd_amount.
    Prefers USDC pair; falls back to USD if USDC fails or has poor liquidity.
    Returns dict with order id, filled amount, average price, slippage (bps), status.
    """
    result = _place_market_buy_single(exchange, symbol_ccxt, usd_amount)
    if result.get("status") != "error":
        pair_log = "Using USDC pair" if "USDC" in symbol_ccxt else "Using USD pair"
        result["pair_log"] = pair_log
        log_execution({"pair_log": pair_log, "symbol": symbol_ccxt})
        return result

    # Fallback to USD if USDC failed
    base = symbol_ccxt.split("/")[0] if "/" in symbol_ccxt else symbol_ccxt
    usd_pair = f"{base}/USD"
    if usd_pair != symbol_ccxt and "USDC" in symbol_ccxt:
        log_execution({"pair_log": "Falling back to USD pair", "original": symbol_ccxt, "fallback": usd_pair})
        result = _place_market_buy_single(exchange, usd_pair, usd_amount)
        result["pair_log"] = "Falling back to USD pair"
        result["symbol"] = usd_pair
    return result


def _is_insufficient_fund_error(err_str: str) -> bool:
    """Detect Coinbase INSUFFICIENT_FUND / preview-fund errors by substring."""
    if not err_str:
        return False
    haystack = err_str.upper()
    return "INSUFFICIENT_FUND" in haystack or "PREVIEW_INSUFFICIENT_FUND" in haystack


AUCTION_MODE_RETRY_AFTER_SEC = 90


def _is_auction_mode_error(err_str: str) -> bool:
    """Detect Coinbase auction-mode style rejections for stop-limit placement."""
    if not err_str:
        return False
    haystack = err_str.upper()
    auction_markers = (
        "ORDERBOOK IS IN AUCTION MODE",
        "AUCTION MODE",
        "AUCTION",
    )
    # INVALID_ARGUMENT is common but not guaranteed in all payload variants.
    return any(marker in haystack for marker in auction_markers)


def place_stop_limit_sell_order(
    symbol: str,
    quantity: float,
    stop_price: float,
    limit_price: Optional[float] = None,
    prefer_usdc: bool = True,
) -> Dict[str, Any]:
    """
    Place a STOP-LIMIT sell order for stop-loss: triggers when price FALLS TO stop_price, then sells at limit_price.
    A plain limit sell at stop_price would fill immediately when price is above stop (wrong). This order type
    only triggers when price drops to stop_price. Returns {success, order_id, error?, symbol_ccxt?}.
    """
    if is_protected(symbol):
        return _protected_refusal(symbol, "stop-limit sell")
    symbol_ccxt = symbol_to_ccxt(symbol, prefer_usdc=prefer_usdc)
    if not symbol_ccxt or quantity <= 0 or stop_price <= 0:
        return {"success": False, "error": "Invalid symbol, quantity, or stop_price"}
    limit_price = limit_price if limit_price is not None and limit_price > 0 else round(stop_price * 0.995, 8)
    try:
        exchange = get_coinbase_exchange()
        # Pre-flight balance clamp: use min(journal_qty, exchange_free) to prevent
        # INSUFFICIENT_FUND errors from fee dust / rounding drift.
        try:
            base_asset = symbol_ccxt.split("/")[0]
            balance = exchange.fetch_balance()
            available = float((balance.get(base_asset) or {}).get('free') or 0)
            if available <= 0:
                logger.warning(
                    f"  ⚠ Stop-limit preflight: {base_asset} free balance is 0 — position may be closed externally"
                )
                return {"success": False, "error": f"{base_asset} free balance is 0", "balance_zero": True}
            if available < quantity:
                logger.info(
                    f"  ↩ Stop-limit preflight: clamping qty from {quantity:.8f} to exchange free {available:.8f} "
                    f"({base_asset}, diff {quantity - available:.8f})"
                )
                quantity = available
        except Exception as pf_e:
            logger.debug(f"  Preflight balance check skipped: {pf_e}")
        quantity = round(quantity, 8)
        stop_price = round(stop_price, 8)
        limit_price = round(limit_price, 8)
        # Preflight: stop-down sell stop must be below current market.
        # If market is already at/below stop, create_order can fail with preview errors;
        # caller should use market-sell fallback path instead of logging hard execution errors.
        try:
            ticker = exchange.fetch_ticker(symbol_ccxt) or {}
            last_price = float(ticker.get("last") or ticker.get("close") or 0)
        except Exception:
            last_price = 0.0
        if last_price > 0 and stop_price >= last_price:
            log_execution({
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "action": "place_stop_limit_sell",
                "status": "skipped",
                "reason": "would_trigger_immediately",
                "symbol": symbol_ccxt,
                "side": "sell",
                "quantity": quantity,
                "stop_price": stop_price,
                "limit_price": limit_price,
                "last_price": last_price,
                "non_actionable": True,
            })
            return {
                "success": False,
                "status": "skipped",
                "reason": "would_trigger_immediately",
                "error": f"stop_price {stop_price} >= last_price {last_price}",
                "symbol_ccxt": symbol_ccxt,
            }
        client_order_id = f"elite-sl-{symbol}-{int(time.time()*1000)}"
        order = exchange.create_order(
            symbol_ccxt,
            "limit",
            "sell",
            quantity,
            limit_price,
            params={
                "stopLossPrice": stop_price,
                "stop_direction": "STOP_DIRECTION_STOP_DOWN",
                "client_order_id": client_order_id,
            },
        )
        order_id = order.get("id")
        log_execution({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "action": "place_stop_limit_sell",
            "symbol": symbol_ccxt,
            "side": "sell",
            "quantity": quantity,
            "stop_price": stop_price,
            "limit_price": limit_price,
            "order_id": order_id,
            "msg": f"Placed stop-loss sell: trigger ${stop_price:.8f}, limit ${limit_price:.8f}",
        })
        _log_audit({
            "action": "place_stop_limit_sell",
            "order_id": order_id,
            "client_order_id": client_order_id,
            "symbol": symbol_ccxt,
            "quantity": quantity,
            "stop_price": stop_price,
            "limit_price": limit_price,
        })
        logger.info(f"  Placed stop-loss sell: trigger ${stop_price:.8f}, limit ${limit_price:.8f} (order_id={order_id})")
        log_stop_telemetry('place', symbol_ccxt, intended_stop=stop_price, actual_stop=stop_price, success=True)
        return {"success": True, "order_id": order_id, "symbol_ccxt": symbol_ccxt}
    except Exception as e:
        err_str = str(e)
        if _is_auction_mode_error(err_str):
            log_execution({
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "action": "place_stop_limit_sell",
                "status": "deferred",
                "reason": "auction_mode",
                "error": err_str,
                "symbol": symbol_ccxt,
                "side": "sell",
                "quantity": quantity,
                "stop_price": stop_price,
                "limit_price": limit_price,
            })
            log_stop_telemetry('place', symbol_ccxt, intended_stop=stop_price, actual_stop=0.0,
                               success=False, details={'error': 'auction_mode', 'reason': 'deferred_retry'})
            logger.warning(
                f"  ⏸ Stop-limit placement deferred for {symbol_ccxt}: "
                "orderbook in auction mode"
            )
            return {
                "success": False,
                "status": "deferred",
                "reason": "auction_mode",
                "retry_after_sec": AUCTION_MODE_RETRY_AFTER_SEC,
                "error": err_str,
                "symbol_ccxt": symbol_ccxt,
            }
        if "PREVIEW_STOP_PRICE_ABOVE_LAST_TRADE_PRICE" in err_str:
            log_execution({
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "action": "place_stop_limit_sell",
                "status": "skipped",
                "reason": "would_trigger_immediately",
                "error": err_str,
                "symbol": symbol_ccxt,
                "side": "sell",
                "quantity": quantity,
                "stop_price": stop_price,
                "limit_price": limit_price,
                "non_actionable": True,
            })
            return {
                "success": False,
                "status": "skipped",
                "reason": "would_trigger_immediately",
                "error": err_str,
                "symbol_ccxt": symbol_ccxt,
            }
        # Fall back to USD pair only for genuine pair-availability errors. INSUFFICIENT_FUND
        # is shared across quote pairs (same base balance) so retrying just locks more.
        if (
            prefer_usdc
            and "USDC" in symbol_ccxt
            and not _is_insufficient_fund_error(err_str)
            and not _is_auction_mode_error(err_str)
        ):
            base = symbol_ccxt.split("/")[0]
            usd_pair = f"{base}/USD"
            log_execution({"pair_log": "Falling back to USD for stop limit sell", "fallback": usd_pair})
            return place_stop_limit_sell_order(symbol, quantity, stop_price, limit_price, prefer_usdc=False)
        # INSUFFICIENT_FUND recovery: fetch actual exchange balance.
        # Dust from fees often makes position.quantity slightly exceed the available balance.
        # If actual balance is within 1% of intended, retry with the real amount.
        # If balance is 0, the position was closed externally — signal this to the caller.
        if _is_insufficient_fund_error(err_str):
            try:
                base_asset = symbol_ccxt.split("/")[0]
                balance = exchange.fetch_balance()
                available = float((balance.get(base_asset) or {}).get('free') or 0)
                log_execution({
                    "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "action": "place_stop_limit_sell",
                    "status": "balance_check",
                    "symbol": symbol_ccxt,
                    "intended_qty": quantity,
                    "available_qty": available,
                })
                if available <= 0:
                    logger.warning(
                        f"  ⚠ Stop-limit INSUFFICIENT_FUND for {symbol_ccxt}: "
                        f"exchange balance is 0 — position may already be closed externally"
                    )
                    log_stop_telemetry('place', symbol_ccxt, intended_stop=stop_price, actual_stop=0.0,
                                       success=False, details={'error': 'balance_zero', 'intended_qty': quantity})
                    return {"success": False, "error": err_str, "balance_zero": True}
                elif abs(quantity - available) / quantity < 0.01:
                    logger.info(
                        f"  ↩ Retrying SL with actual balance {available:.8f} "
                        f"(intended {quantity:.8f}, diff {abs(quantity - available):.8f})"
                    )
                    return place_stop_limit_sell_order(symbol, available, stop_price, limit_price, prefer_usdc=prefer_usdc)
                else:
                    logger.warning(
                        f"  ⚠ Stop-limit INSUFFICIENT_FUND for {symbol_ccxt}: "
                        f"balance {available:.8f} vs intended {quantity:.8f} — large mismatch, not retrying"
                    )
            except Exception as bal_e:
                logger.debug(f"  Balance check failed after INSUFFICIENT_FUND: {bal_e}")
        log_execution({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "action": "place_stop_limit_sell",
            "status": "error",
            "error": err_str,
        })
        logger.error(f"  Stop limit sell failed: {e}")
        log_stop_telemetry('place', symbol_ccxt, intended_stop=stop_price, actual_stop=0.0,
                           success=False, details={'error': err_str[:200]})
        return {"success": False, "error": err_str}


def place_limit_sell_order(
    symbol: str,
    quantity: float,
    price: float,
    prefer_usdc: bool = True,
) -> Dict[str, Any]:
    """
    Place a limit sell order on Coinbase (for TAKE-PROFIT only). Executes when price reaches limit or higher.
    Do NOT use for stop-loss: use place_stop_limit_sell_order so the order triggers when price falls to stop.
    Returns {success, order_id, error?, symbol_ccxt?}.
    """
    if is_protected(symbol):
        return _protected_refusal(symbol, "limit sell")
    symbol_ccxt = symbol_to_ccxt(symbol, prefer_usdc=prefer_usdc)
    if not symbol_ccxt or quantity <= 0 or price <= 0:
        return {"success": False, "error": "Invalid symbol, quantity, or price"}
    try:
        exchange = get_coinbase_exchange()
        # Pre-flight balance clamp: use min(journal_qty, exchange_free) to prevent
        # INSUFFICIENT_FUND errors from fee dust / rounding drift.
        try:
            base_asset = symbol_ccxt.split("/")[0]
            balance = exchange.fetch_balance()
            available = float((balance.get(base_asset) or {}).get('free') or 0)
            if available <= 0:
                logger.warning(
                    f"  ⚠ Limit sell preflight: {base_asset} free balance is 0 — position may be closed externally"
                )
                return {"success": False, "error": f"{base_asset} free balance is 0", "balance_zero": True}
            if available < quantity:
                logger.info(
                    f"  ↩ Limit sell preflight: clamping qty from {quantity:.8f} to exchange free {available:.8f} "
                    f"({base_asset}, diff {quantity - available:.8f})"
                )
                quantity = available
        except Exception as pf_e:
            logger.debug(f"  Preflight balance check skipped: {pf_e}")
        quantity = round(quantity, 8)
        price = round(price, 8)
        client_order_id = f"elite-tp-{symbol}-{int(time.time()*1000)}"
        order = exchange.create_limit_sell_order(symbol_ccxt, quantity, price, {"client_order_id": client_order_id})
        order_id = order.get("id")
        log_execution({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "action": "place_limit_sell",
            "symbol": symbol_ccxt,
            "side": "sell",
            "quantity": quantity,
            "price": price,
            "order_id": order_id,
            "msg": f"Placed limit sell at ${price:.8f}",
        })
        _log_audit({
            "action": "place_limit_sell",
            "order_id": order_id,
            "client_order_id": client_order_id,
            "symbol": symbol_ccxt,
            "quantity": quantity,
            "price": price,
        })
        logger.info(f"  Placed limit sell at ${price:.8f} (order_id={order_id})")
        return {"success": True, "order_id": order_id, "symbol_ccxt": symbol_ccxt}
    except Exception as e:
        err_str = str(e)
        # Fall back to USD pair only for genuine pair-availability errors. INSUFFICIENT_FUND
        # is shared across quote pairs (same base balance) so retrying just locks more.
        if prefer_usdc and "USDC" in symbol_ccxt and not _is_insufficient_fund_error(err_str):
            base = symbol_ccxt.split("/")[0]
            usd_pair = f"{base}/USD"
            log_execution({"pair_log": "Falling back to USD for limit sell", "fallback": usd_pair})
            return place_limit_sell_order(symbol, quantity, price, prefer_usdc=False)
        log_execution({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "action": "place_limit_sell",
            "status": "error",
            "error": err_str,
        })
        logger.error(f"  Limit sell failed: {e}")
        return {"success": False, "error": err_str}


def cancel_order(order_id: str, exchange_id: str = "coinbase") -> Dict[str, Any]:
    """
    Cancel an order on the exchange. Returns {success, error?}.
    """
    if not order_id or not str(order_id).strip():
        return {"success": False, "error": "Invalid order_id"}
    try:
        exchange = get_coinbase_exchange() if exchange_id.lower() == "coinbase" else get_kraken_exchange()
        exchange.cancel_order(order_id)
        log_execution({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "action": "cancel_order",
            "order_id": order_id,
            "msg": "Cancelled order",
        })
        _log_audit({"action": "cancel_order", "order_id": order_id, "exchange_id": exchange_id, "status": "success"})
        logger.info(f"  Cancelled order {order_id}")
        return {"success": True}
    except Exception as e:
        # Order may already be filled or cancelled
        log_execution({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "action": "cancel_order",
            "order_id": order_id,
            "status": "error",
            "error": str(e),
        })
        _log_audit({"action": "cancel_order", "order_id": order_id, "exchange_id": exchange_id, "status": "error", "error": str(e)})
        logger.warning(f"  Cancel order {order_id}: {e}")
        return {"success": False, "error": str(e)}


CANCEL_WAIT_TIMEOUT_S = 2.0
CANCEL_WAIT_POLL_INTERVAL_S = 0.1


def cancel_and_wait(
    order_id: str,
    exchange_id: str = "coinbase",
    timeout_s: float = CANCEL_WAIT_TIMEOUT_S,
    poll_interval_s: float = CANCEL_WAIT_POLL_INTERVAL_S,
) -> Dict[str, Any]:
    """
    Cancel an order and poll until the exchange reports it as canceled/filled,
    so balance reservations are released before any replacement order is placed.

    Returns {success, status, waited_ms, error?}.
    A terminal status of 'canceled' or 'filled' is treated as success.
    """
    cancel_result = cancel_order(order_id, exchange_id=exchange_id)
    started = time.monotonic()
    deadline = started + max(0.0, timeout_s)
    last_status: Optional[str] = None
    while True:
        last_status = fetch_order_status(order_id, exchange_id=exchange_id)
        if last_status in ("canceled", "filled"):
            waited_ms = int((time.monotonic() - started) * 1000)
            return {"success": True, "status": last_status, "waited_ms": waited_ms}
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval_s)
    waited_ms = int((time.monotonic() - started) * 1000)
    # If the underlying cancel call itself failed AND status never reached terminal,
    # surface that as the error. Otherwise report timeout.
    if not cancel_result.get("success") and last_status not in ("canceled", "filled"):
        return {
            "success": False,
            "status": last_status or "unknown",
            "waited_ms": waited_ms,
            "error": cancel_result.get("error", "cancel failed"),
        }
    return {
        "success": False,
        "status": last_status or "unknown",
        "waited_ms": waited_ms,
        "error": f"timeout waiting for cancel (last_status={last_status})",
    }


def execute_trailing_stop_sell(
    coin_symbol: str,
    quantity: float,
    num_slices: int = 5,
    duration_minutes: int = 15,
    coin_name: str = "",
    position_usd: Optional[float] = None,
    twap_min_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Execute sell when trailing stop triggers (single market sell for small positions, else TWAP).
    Cancels TP/SL orders first (caller responsibility).
    Returns {success, fill_price, average_price, total_filled, error?, mode?}.
    """
    result = execute_market_sell_twap(
        symbol=coin_symbol,
        quantity=quantity,
        num_slices=num_slices,
        duration_minutes=duration_minutes,
        coin_name=coin_name,
        position_usd=position_usd,
        twap_min_usd=twap_min_usd,
    )
    if result.get("success"):
        log_execution({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "action": "execute_trailing_stop_sell",
            "symbol": coin_symbol,
            "quantity": quantity,
            "fill_price": result.get("fill_price"),
            "msg": "Executed trailing stop sell",
        })
        logger.info(f"  Executed trailing stop sell for {coin_symbol} at ${result.get('fill_price', 0):.8f}")
    return result


def fetch_order_status(order_id: str, exchange_id: str = "coinbase") -> Optional[str]:
    """
    Fetch order status. Returns 'open', 'closed', 'canceled', 'filled', or None on error.
    """
    if not order_id or not str(order_id).strip():
        return None
    try:
        exchange = get_coinbase_exchange() if exchange_id.lower() == "coinbase" else get_kraken_exchange()
        order = exchange.fetch_order(order_id)
        status = (order.get("status") or "").lower()
        if status in ("closed", "filled", "done"):
            status_str = "filled"
        elif status in ("canceled", "cancelled"):
            status_str = "canceled"
        else:
            status_str = status or "open"
        _log_audit({"action": "fetch_order_status", "order_id": order_id, "status": status_str, "exchange_id": exchange_id})
        return status_str
    except Exception:
        return None


def fetch_order_with_fee(
    order_id: str,
    exchange_id: str = "coinbase",
    position_id: Optional[int] = None,
    trade_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch full order details including fee. Returns dict with status, fill_price, fee_usd,
    or None on error. position_id/trade_id optional for audit linkage.
    """
    if not order_id or not str(order_id).strip():
        return None
    try:
        exchange = get_coinbase_exchange() if exchange_id.lower() == "coinbase" else get_kraken_exchange()
        order = exchange.fetch_order(order_id)
        status = (order.get("status") or "").lower()
        if status in ("closed", "filled", "done"):
            status_str = "filled"
        elif status in ("canceled", "cancelled"):
            status_str = "canceled"
        else:
            status_str = status or "open"
        fee_info = _extract_fee(order)
        fill_price = float(order.get("average") or order.get("price") or 0)
        filled_amount = float(order.get("filled", 0) or 0)

        # Audit every fetch (forensics)
        entry = {
            "action": "fetch_order_with_fee",
            "order_id": order_id,
            "status": status_str,
            "fill_price": fill_price,
            "filled_amount": filled_amount,
            "fee_usd": fee_info["fee_usd"],
            "fee_rate": fee_info["fee_rate"],
        }
        if position_id is not None:
            entry["position_id"] = position_id
        if trade_id:
            entry["trade_id"] = trade_id
        _log_audit(entry)

        # Audit the fill if it's a completed order (legacy action name for filled)
        if status_str == "filled":
            filled_entry = {
                "action": "limit_order_filled",
                "order_id": order_id,
                "status": status_str,
                "fill_price": fill_price,
                "filled_amount": filled_amount,
                "fee_usd": fee_info["fee_usd"],
                "fee_rate": fee_info["fee_rate"],
            }
            if position_id is not None:
                filled_entry["position_id"] = position_id
            if trade_id:
                filled_entry["trade_id"] = trade_id
            _log_audit(filled_entry)

        return {
            "status": status_str,
            "fill_price": fill_price,
            "filled_amount": filled_amount,
            "fee_usd": fee_info["fee_usd"],
            "fee_rate": fee_info["fee_rate"],
            "order": order,
        }
    except Exception:
        return None


def place_market_sell(
    exchange: Any,
    symbol_ccxt: str,
    amount_base: float,
    position_id: Optional[int] = None,
    trade_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Place a market sell for amount_base (in base currency, e.g. BTC).
    position_id/trade_id optional for audit linkage. Returns dict with order id, filled amount, etc.
    """
    if is_protected(symbol_ccxt):
        return _protected_refusal(symbol_ccxt, "market sell")
    result = {"exchange": "coinbase", "symbol": symbol_ccxt, "side": "sell", "amount_base": amount_base}
    try:
        amount_base = round(amount_base, 8)
        if amount_base <= 0:
            result["status"] = "error"
            result["error"] = "Amount must be positive"
            result["success"] = False
            return result

        # Pre-trade mid for slippage
        pre_trade_mid = 0.0
        try:
            ticker = exchange.fetch_ticker(symbol_ccxt)
            bid = float(ticker.get("bid", 0) or 0)
            ask = float(ticker.get("ask", 0) or 0)
            if bid > 0 and ask > 0:
                pre_trade_mid = (bid + ask) / 2
            elif ticker.get("last"):
                pre_trade_mid = float(ticker["last"])
        except Exception:
            pass

        client_order_id = f"elite-sell-{symbol_ccxt}-{int(time.time()*1000)}"
        order = exchange.create_market_sell_order(symbol_ccxt, amount_base, {"client_order_id": client_order_id})
        result["order_id"] = order.get("id")
        result["status"] = order.get("status", "unknown")
        filled = float(order.get("filled", 0) or 0)
        result["filled_amount"] = filled
        avg_price = float(order.get("average") or 0) if order.get("average") else 0
        result["average_price"] = avg_price
        result["fill_price"] = avg_price
        fee_info = _extract_fee(order)

        # Poll for fill if not yet confirmed (parity with buy side)
        if filled == 0 and result.get("order_id") and result.get("status") != "error":
            time.sleep(2)
            try:
                order2 = exchange.fetch_order(result["order_id"])
                if order2 and order2.get("status") in ("closed", "filled", "done"):
                    filled = float(order2.get("filled", 0) or 0)
                    if filled > 0:
                        avg_price = float(order2.get("average") or 0) or avg_price
                        result["filled_amount"] = filled
                        result["average_price"] = avg_price
                        result["fill_price"] = avg_price
                        result["status"] = order2.get("status", result["status"])
                        fee_info = _extract_fee(order2)
            except Exception:
                pass

        result["fee_usd"] = fee_info["fee_usd"]
        result["fee_rate"] = fee_info["fee_rate"]
        # Slippage for sells: positive means received less than mid (bad for seller), consistent with buy convention
        result["slippage_bps"] = _compute_slippage_bps(pre_trade_mid, avg_price) if pre_trade_mid > 0 and avg_price > 0 else None
        result["success"] = filled > 0 and (result.get("status") in ("closed", "filled", "done"))
        result["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Forensic audit
        audit_entry = {
            "action": "market_sell",
            "order_id": result.get("order_id"),
            "client_order_id": client_order_id,
            "symbol": symbol_ccxt,
            "side": "sell",
            "amount_base": amount_base,
            "fill_price": avg_price,
            "filled_amount": filled,
            "fee_usd": result["fee_usd"],
            "fee_rate": result["fee_rate"],
            "slippage_bps": result["slippage_bps"],
            "pre_trade_mid": pre_trade_mid,
            "status": result.get("status"),
        }
        if position_id is not None:
            audit_entry["position_id"] = position_id
        if trade_id:
            audit_entry["trade_id"] = trade_id
        _log_audit(audit_entry)
        return result
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        result["success"] = False
        result["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return result


def execute_market_sell(
    symbol: str,
    quantity: float,
    reason: str = "manual",
    position_id: Optional[int] = None,
    trade_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute a market sell. symbol e.g. 'BTC', quantity in base units.
    Returns {success, fill_price, error?, ...}.
    """
    symbol_ccxt = symbol_to_ccxt(symbol)
    if not symbol_ccxt:
        return {"success": False, "error": "Invalid symbol"}
    try:
        exchange = get_coinbase_exchange()
        r = place_market_sell(exchange, symbol_ccxt, quantity, position_id=position_id, trade_id=trade_id)
        r["reason"] = reason
        return r
    except Exception as e:
        return {"success": False, "error": str(e), "reason": reason}


def run_twap_sell_coinbase(
    symbol_ccxt: str,
    quantity_base: float,
    num_slices: int = 5,
    duration_minutes: int = 15,
    coin_name: str = "",
    position_id: Optional[int] = None,
    trade_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run TWAP sell: split quantity_base into num_slices market sells over duration_minutes.
    Prefers USDC pair; falls back to USD if USDC fails.
    Returns dict with success, fill_price (avg), avg_price, error.
    """
    if quantity_base <= 0:
        return {"success": False, "error": "Quantity must be positive"}

    exchange = None
    try:
        exchange = get_coinbase_exchange()
    except Exception as e:
        log_execution({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "exchange": "coinbase",
            "symbol": symbol_ccxt,
            "side": "sell",
            "quantity_base": quantity_base,
            "status": "error",
            "twap_summary": True,
            "error": str(e),
            "coin_name": coin_name,
        })
        return {"success": False, "error": str(e)}

    slice_qty = quantity_base / num_slices
    interval_sec = max(30, (duration_minutes * 60) // num_slices)
    fills: List[Dict[str, Any]] = []
    start_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sold_so_far = 0.0

    for i in range(num_slices):
        # Last slice gets the remainder to avoid rounding drift
        if i == num_slices - 1:
            this_slice = round(quantity_base - sold_so_far, 8)
        else:
            this_slice = round(slice_qty, 8)
            sold_so_far += this_slice
        # Retry with exponential backoff (3 attempts per slice)
        r = None
        for attempt in range(3):
            r = place_market_sell(exchange, symbol_ccxt, this_slice, position_id=position_id, trade_id=trade_id)
            if r.get("status") != "error":
                break
            err_str = str(r.get("error", "")).lower()
            is_transient = any(k in err_str for k in ("rate", "limit", "timeout", "network", "502", "503", "429"))
            if not is_transient:
                break
            backoff = (2 ** attempt) * 2
            logger.warning(f"  TWAP sell slice {i+1} attempt {attempt+1} failed (transient): {r.get('error')} — retrying in {backoff}s")
            time.sleep(backoff)

        r["slice"] = i + 1
        r["slice_qty"] = slice_qty
        fills.append(r)
        log_execution(r)
        if r.get("status") == "error":
            break
        if i < num_slices - 1:
            time.sleep(interval_sec)

    total_filled = sum(float(f.get("filled_amount", 0) or 0) for f in fills)
    total_value = sum(float(f.get("filled_amount", 0) or 0) * float(f.get("average_price", 0) or 0) for f in fills)
    total_fee_usd = sum(float(f.get("fee_usd", 0) or 0) for f in fills)
    avg_price = (total_value / total_filled) if total_filled > 0 else 0
    # Guard against NaN/inf from malformed API responses
    if math.isnan(avg_price) or math.isinf(avg_price):
        logger.error(f"  TWAP sell: avg_price is {avg_price}, resetting to 0 — check fill data")
        avg_price = 0

    success = total_filled >= quantity_base * 0.99
    log_execution({
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "exchange": "coinbase",
        "symbol": symbol_ccxt,
        "side": "sell",
        "quantity_base": quantity_base,
        "num_slices": num_slices,
        "duration_minutes": duration_minutes,
        "start_ts": start_ts,
        "status": "filled" if success else "partial",
        "twap_summary": True,
        "fills": fills,
        "total_filled": total_filled,
        "avg_price": avg_price,
        "total_fee_usd": round(total_fee_usd, 6),
        "coin_name": coin_name,
    })

    if callable(_on_twap_done_sync_balance):
        try:
            _on_twap_done_sync_balance()
        except Exception as e:
            logger.error(f"  Post-TWAP balance sync callback FAILED: {e} — account balance may be stale")

    return {
        "success": success,
        "fill_price": avg_price,
        "average_price": avg_price,
        "total_filled": total_filled,
        "total_fee_usd": round(total_fee_usd, 6),
        "fills": fills,
    }


def execute_market_sell_twap(
    symbol: str,
    quantity: float,
    num_slices: int = 5,
    duration_minutes: int = 15,
    coin_name: str = "",
    position_usd: Optional[float] = None,
    twap_min_usd: Optional[float] = None,
    position_id: Optional[int] = None,
    trade_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute a TWAP sell on Coinbase, or single market sell for small positions.
    position_id/trade_id optional for audit linkage.
    """
    threshold = float(twap_min_usd) if twap_min_usd is not None else None
    use_single = (
        position_usd is not None
        and threshold is not None
        and position_usd < threshold
    )
    if use_single:
        logger.info(
            f"  Trailing stop sell: ${position_usd:.0f} < ${threshold:.0f} threshold → single market sell (no TWAP)"
        )
        result = execute_market_sell(symbol, quantity, reason="trailing_stop", position_id=position_id, trade_id=trade_id)
        # Normalize to same shape as TWAP for callers
        filled = result.get("filled_amount", 0) or 0
        avg = result.get("average_price") or result.get("fill_price") or 0
        return {
            "success": result.get("success", False),
            "fill_price": result.get("fill_price"),
            "average_price": avg,
            "total_filled": filled,
            "total_fee_usd": float(result.get("fee_usd", 0) or 0),
            "error": result.get("error"),
            "mode": "market",
        }
    symbol_ccxt = symbol_to_ccxt(symbol, prefer_usdc=True)
    if not symbol_ccxt:
        return {"success": False, "error": "Invalid symbol"}

    result = run_twap_sell_coinbase(
        symbol_ccxt=symbol_ccxt,
        quantity_base=quantity,
        num_slices=num_slices,
        duration_minutes=duration_minutes,
        coin_name=coin_name,
        position_id=position_id,
        trade_id=trade_id,
    )
    result["mode"] = "twap"

    if result.get("success"):
        return result

    base = symbol.split("/")[0] if "/" in symbol else symbol
    usd_pair = f"{base}/USD"
    filled_first_leg = float(result.get("total_filled", 0) or 0)
    remaining_qty = max(0.0, float(quantity) - filled_first_leg)
    if "USDC" in symbol_ccxt and usd_pair != symbol_ccxt:
        if remaining_qty <= 0:
            # Defensive: if first leg reports full fill but not marked success due to shape/rounding mismatch, do not oversell.
            result["success"] = True
            return result
        log_execution({"pair_log": "Falling back to USD pair for sell", "fallback": usd_pair})
        result = run_twap_sell_coinbase(
            symbol_ccxt=usd_pair,
            quantity_base=remaining_qty,
            num_slices=num_slices,
            duration_minutes=duration_minutes,
            coin_name=coin_name,
            position_id=position_id,
            trade_id=trade_id,
        )
        result["mode"] = "twap"
    return result


def run_twap_buy_coinbase(
    symbol_ccxt: str,
    total_usd: float,
    num_slices: int = 5,
    duration_minutes: int = 45,
    coin_name: str = "",
    coin_id: str = "",
    stop_pct: Optional[float] = None,
    target_pct: Optional[float] = None,
    execution_context: Optional[Dict[str, Any]] = None,
    is_test: bool = False,
) -> None:
    """
    Run TWAP: split total_usd into num_slices market buys over duration_minutes.
    Runs in current thread (call from a daemon thread to avoid blocking).
    Logs each slice and a summary to execution_log.jsonl.
    """
    exchange = None
    try:
        exchange = get_coinbase_exchange()
    except Exception as e:
        log_execution({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "exchange": "coinbase",
            "symbol": symbol_ccxt,
            "side": "buy",
            "total_usd": total_usd,
            "num_slices": num_slices,
            "duration_minutes": duration_minutes,
            "status": "error",
            "twap_summary": True,
            "error": str(e),
            "coin_name": coin_name,
        })
        base = symbol_ccxt.split("/")[0] if "/" in symbol_ccxt else symbol_ccxt
        _unregister_active_twap(base)
        return

    slice_usd = total_usd / num_slices
    interval_sec = max(60, (duration_minutes * 60) // num_slices)
    fills: List[Dict[str, Any]] = []
    start_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    spent_so_far = 0.0

    for i in range(num_slices):
        # Last slice gets the remainder to avoid rounding drift
        if i == num_slices - 1:
            this_slice_usd = round(total_usd - spent_so_far, 2)
        else:
            this_slice_usd = round(slice_usd, 2)
            spent_so_far += this_slice_usd
        # Retry with exponential backoff (3 attempts per slice)
        slice_result = None
        for attempt in range(3):
            slice_result = place_market_buy_usd(exchange, symbol_ccxt, this_slice_usd)
            if slice_result.get("status") != "error":
                break
            err_str = str(slice_result.get("error", "")).lower()
            is_transient = any(k in err_str for k in ("rate", "limit", "timeout", "network", "502", "503", "429"))
            if not is_transient:
                break  # Non-transient error — don't retry
            backoff = (2 ** attempt) * 2  # 2s, 4s, 8s
            logger.warning(f"  TWAP buy slice {i+1} attempt {attempt+1} failed (transient): {slice_result.get('error')} — retrying in {backoff}s")
            time.sleep(backoff)

        slice_result["slice"] = i + 1
        slice_result["slice_usd"] = this_slice_usd
        fills.append(slice_result)
        log_execution(slice_result)
        if slice_result.get("status") == "error":
            break
        if i < num_slices - 1:
            time.sleep(interval_sec)

    total_filled_usd = sum(float(f.get("filled_amount", 0) or 0) * float(f.get("average_price", 0) or 0) for f in fills)
    total_filled_base = sum(float(f.get("filled_amount", 0) or 0) for f in fills)
    total_fee_usd = sum(float(f.get("fee_usd", 0) or 0) for f in fills)
    avg_price = (total_filled_usd / total_filled_base) if total_filled_base > 0 else 0
    # Guard against NaN/inf from malformed API responses
    if math.isnan(avg_price) or math.isinf(avg_price):
        logger.error(f"  TWAP buy: avg_price is {avg_price}, resetting to 0 — check fill data")
        avg_price = 0
    base_symbol = symbol_ccxt.split("/")[0] if "/" in symbol_ccxt else symbol_ccxt
    success = total_filled_base > 0 and len(fills) >= num_slices * 0.8
    log_execution({
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "exchange": "coinbase",
        "symbol": symbol_ccxt,
        "side": "buy",
        "total_usd": total_usd,
        "num_slices": num_slices,
        "duration_minutes": duration_minutes,
        "start_ts": start_ts,
        "status": "filled" if success else "partial",
        "twap_summary": True,
        "fills": fills,
        "total_filled_usd": round(total_filled_usd, 2),
        "total_filled_base": total_filled_base,
        "total_fee_usd": round(total_fee_usd, 6),
        "coin_name": coin_name,
    })
    # Optional: open position + place SL/TP (app sets _on_twap_done_open_position_and_place_orders)
    if callable(_on_twap_done_open_position_and_place_orders) and total_filled_base > 0:
        fill_data = {
            "coin_symbol": base_symbol,
            "coin_id": coin_id or base_symbol.lower(),
            "coin_name": coin_name or base_symbol,
            "quantity": total_filled_base,
            "entry_price": avg_price,
            "capital_usd": round(total_filled_usd, 2),
            "entry_fee_usd": round(total_fee_usd, 6),
            "is_test": is_test,
        }
        if stop_pct is not None:
            fill_data["ai_stop_pct"] = stop_pct
        if target_pct is not None:
            fill_data["ai_target_pct"] = target_pct
        if execution_context:
            fill_data.update(execution_context)
        try:
            _on_twap_done_open_position_and_place_orders(fill_data)
        except Exception as e:
            logger.error(f"  Post-TWAP callback failed — queuing for retry: {e}")
            _queue_for_journal(fill_data)
    # Optional: trigger balance sync after TWAP (app sets _on_twap_done_sync_balance)
    if callable(_on_twap_done_sync_balance):
        try:
            _on_twap_done_sync_balance()
        except Exception as e:
            logger.warning(f"  Post-TWAP balance sync callback error: {e}")
    base_symbol = symbol_ccxt.split("/")[0] if "/" in symbol_ccxt else symbol_ccxt
    _unregister_active_twap(base_symbol)

# Cooldown: don't start another TWAP for the same symbol within this many seconds
TWAP_COOLDOWN_SEC = 3600  # 60 minutes

# Default: positions below this USD use single market order instead of TWAP (reduces latency for small trades)
DEFAULT_TWAP_MIN_USD = 500.0

_COOLDOWN_FILE = os.path.join(os.path.dirname(__file__), 'data', 'twap_cooldowns.json')
_twap_lock = threading.Lock()

def _load_cooldowns() -> Dict[str, float]:
    """Load persisted cooldown timestamps from disk."""
    try:
        import safe_json
        data = safe_json.load(_COOLDOWN_FILE, default=dict)
        if isinstance(data, dict):
            # Prune expired entries
            now = time.time()
            return {k: v for k, v in data.items() if (now - v) < TWAP_COOLDOWN_SEC}
        return {}
    except Exception:
        return {}

def _save_cooldowns(cooldowns: Dict[str, float]) -> None:
    """Persist cooldown timestamps to disk."""
    try:
        import safe_json
        os.makedirs(os.path.dirname(_COOLDOWN_FILE), exist_ok=True)
        safe_json.save(_COOLDOWN_FILE, cooldowns)
    except Exception:
        pass

# Load persisted cooldowns at startup (survives restarts)
with _twap_lock:
    _twap_last_start: Dict[str, float] = _load_cooldowns()

# Active TWAP/market buys in progress (for UI and cash reservation)
_PENDING_ORDERS_FILE = os.path.join(os.path.dirname(__file__), 'data', 'pending_orders.json')
_active_twap_lock = threading.Lock()
_PENDING_ORDER_MAX_AGE_S = 7200  # 2 hours — auto-expire stale pending entries

def _load_pending_orders() -> Dict[str, Dict[str, Any]]:
    """Load pending orders from disk, pruning stale entries. Caller must hold _active_twap_lock."""
    import safe_json
    data = safe_json.load(_PENDING_ORDERS_FILE, default=dict)
    if not isinstance(data, dict):
        return {}
    now = time.time()
    stale = [k for k, v in data.items() if (now - v.get("start_ts", now)) > _PENDING_ORDER_MAX_AGE_S]
    for k in stale:
        logger.warning(f"  Pruning stale pending order on load: {k} (>{_PENDING_ORDER_MAX_AGE_S // 3600}h old)")
        data.pop(k)
    return data

def _save_pending_orders():
    """Save pending orders to disk. Caller must hold _active_twap_lock."""
    import safe_json
    os.makedirs(os.path.dirname(_PENDING_ORDERS_FILE), exist_ok=True)
    safe_json.save(_PENDING_ORDERS_FILE, _active_twap_orders)

# Load initial state under lock to prevent race at import time
with _active_twap_lock:
    _active_twap_orders: Dict[str, Dict[str, Any]] = _load_pending_orders()


def _register_active_twap(symbol: str, total_usd: float, mode: str = "twap", coin_name: str = "", is_test: bool = False):
    with _active_twap_lock:
        _active_twap_orders[(symbol or "").strip().upper()] = {
            "symbol": (symbol or "").strip().upper(),
            "total_usd": float(total_usd),
            "start_ts": time.time(),
            "mode": mode,
            "coin_name": coin_name or (symbol or "").strip().upper(),
            "is_test": bool(is_test),
        }
        _save_pending_orders()


def _unregister_active_twap(symbol: str):
    with _active_twap_lock:
        key = (symbol or "").strip().upper()
        _active_twap_orders.pop(key, None)
        _save_pending_orders()


def unregister_pending_order(symbol: str) -> bool:
    """Remove symbol from pending TWAP/order list. Use when an order filled on exchange but app didn't get fill (e.g. NOT_FOUND on poll). Returns True if it was registered."""
    with _active_twap_lock:
        key = (symbol or "").strip().upper()
        if key in _active_twap_orders:
            _active_twap_orders.pop(key, None)
            _save_pending_orders()
            return True
        return False


def get_active_twap_orders() -> List[Dict[str, Any]]:
    """Return list of pending TWAP/market buy orders. Used for Positions tab and cash reservation. Auto-expires entries older than 2 hours. Includes is_test so callers can exclude test orders from cash reservation."""
    now = time.time()
    with _active_twap_lock:
        stale = [k for k, v in _active_twap_orders.items() if (now - v.get("start_ts", now)) > _PENDING_ORDER_MAX_AGE_S]
        if stale:
            for k in stale:
                logger.warning(f"  Auto-expiring stale pending order: {k} (>{_PENDING_ORDER_MAX_AGE_S // 3600}h old)")
                _active_twap_orders.pop(k, None)
            _save_pending_orders()
        return [
            {"symbol": v["symbol"], "total_usd": v["total_usd"], "start_ts": v["start_ts"], "mode": v["mode"], "coin_name": v.get("coin_name", v["symbol"]), "is_test": bool(v.get("is_test", False))}
            for v in list(_active_twap_orders.values())
        ]


def run_single_market_buy_coinbase(
    symbol_ccxt: str,
    total_usd: float,
    coin_name: str = "",
    coin_id: str = "",
    stop_pct: Optional[float] = None,
    target_pct: Optional[float] = None,
    execution_context: Optional[Dict[str, Any]] = None,
    is_test: bool = False,
) -> None:
    """
    Execute a single market buy for small positions (below twap_min_usd).
    Same callbacks as TWAP: opens journal position, places SL/TP, syncs balance.
    """
    exchange = None
    try:
        exchange = get_coinbase_exchange()
    except Exception as e:
        log_execution({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "exchange": "coinbase",
            "symbol": symbol_ccxt,
            "side": "buy",
            "total_usd": total_usd,
            "mode": "single_market",
            "status": "error",
            "error": str(e),
            "coin_name": coin_name,
        })
        base = symbol_ccxt.split("/")[0] if "/" in symbol_ccxt else symbol_ccxt
        _unregister_active_twap(base)
        return

    r = place_market_buy_usd(exchange, symbol_ccxt, total_usd)
    filled = float(r.get("filled_amount", 0) or 0)
    avg_price = float(r.get("average_price") or 0)
    total_filled_usd = filled * avg_price if filled and avg_price else 0
    base_symbol = symbol_ccxt.split("/")[0] if "/" in symbol_ccxt else symbol_ccxt
    success = r.get("status") in ("closed", "filled") and filled > 0

    # Coinbase often returns order accepted but not yet filled (status null, filled=0). Poll so we log the position when it fills.
    # Sometimes fetch_order returns NOT_FOUND immediately after place; retry once after 5s (order may have filled and moved to closed).
    if not success and r.get("order_id") and r.get("status") != "error":
        for wait_s, attempt in [(3, 1), (5, 2)]:
            time.sleep(wait_s)
            try:
                order = exchange.fetch_order(r["order_id"])
                if order and order.get("status") in ("closed", "filled"):
                    filled = float(order.get("filled", 0) or 0)
                    if filled > 0:
                        avg_price = float(order.get("average") or 0) or (total_usd / filled if filled else 0)
                        total_filled_usd = filled * avg_price
                        r["status"] = order.get("status")
                        r["filled_amount"] = filled
                        r["average_price"] = avg_price
                        success = True
                        break
            except Exception as e:
                log_execution({"ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "action": "fetch_order_after_buy", "order_id": r.get("order_id"), "attempt": attempt, "error": str(e)})
                if attempt == 2:
                    break

    log_execution({
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "exchange": "coinbase",
        "symbol": symbol_ccxt,
        "side": "buy",
        "total_usd": total_usd,
        "mode": "single_market",
        "status": "filled" if success else "partial",
        "fills": [r],
        "total_filled_usd": round(total_filled_usd, 2),
        "total_filled_base": filled,
        "coin_name": coin_name,
    })

    entry_fee = float(r.get("fee_usd", 0) or 0)
    if filled <= 0 or avg_price <= 0:
        logger.error(
            f"  Market buy {base_symbol}: filled={filled}, avg_price={avg_price} — refusing to open position with zero fill/price"
        )
        _unregister_active_twap(base_symbol)
        return
    if callable(_on_twap_done_open_position_and_place_orders) and filled > 0:
        fill_data = {
            "coin_symbol": base_symbol,
            "coin_id": coin_id or base_symbol.lower(),
            "coin_name": coin_name or base_symbol,
            "quantity": filled,
            "entry_price": avg_price,
            "capital_usd": round(total_filled_usd, 2),
            "entry_fee_usd": round(entry_fee, 6),
            "is_test": is_test,
        }
        if stop_pct is not None:
            fill_data["ai_stop_pct"] = stop_pct
        if target_pct is not None:
            fill_data["ai_target_pct"] = target_pct
        if execution_context:
            fill_data.update(execution_context)
        try:
            _on_twap_done_open_position_and_place_orders(fill_data)
        except Exception as e:
            logger.error(f"  Post-TWAP callback failed — queuing for retry: {e}")
            _queue_for_journal(fill_data)
    if callable(_on_twap_done_sync_balance):
        try:
            _on_twap_done_sync_balance()
        except Exception as e:
            logger.warning(f"  Post-market-buy balance sync callback error: {e}")
    _unregister_active_twap(base_symbol)
    if not success:
        logger.warning(f"  Single market buy for {base_symbol} did not confirm fill. Use 'Add from Coinbase' if order filled on exchange.")


# Cache for Coinbase tradeable base symbols (for filtering top-50 list). TTL 10 min.
_coinbase_symbols_cache: Optional[set] = None
_coinbase_symbols_ts: float = 0
_COINBASE_SYMBOLS_TTL = 10 * 60


def get_coinbase_tradeable_symbols() -> set:
    """
    Return set of base symbols (e.g. {'BTC', 'ETH'}) that are tradeable on Coinbase (USDC or USD pair).
    Cached 10 min. Used to filter the top-50 list so we only screen coins you can buy.
    On failure (no API keys, network), returns empty set so app skips Coinbase filter.
    """
    global _coinbase_symbols_cache, _coinbase_symbols_ts
    now = time.time()
    if _coinbase_symbols_cache is not None and (now - _coinbase_symbols_ts) < _COINBASE_SYMBOLS_TTL:
        return _coinbase_symbols_cache
    try:
        exchange = get_coinbase_exchange()
        exchange.load_markets()
        symbols = set()
        for market_id in (exchange.markets or {}):
            if "/" not in market_id:
                continue
            base, quote = market_id.split("/", 1)
            if quote in ("USDC", "USD"):
                symbols.add(base.upper())
        with _exchange_lock:
            _coinbase_symbols_cache = symbols
            _coinbase_symbols_ts = now
        logger.info(f"  [OK] Coinbase: {len(symbols)} tradeable symbols (cached 10 min)")
        return symbols
    except Exception as e:
        logger.warning(f"  Coinbase symbols unavailable: {e}. Skipping Coinbase filter for top-50.")
        return set()


def coinbase_symbol_available(symbol: str) -> tuple[bool, str]:
    """
    Check if symbol is tradeable on Coinbase. Returns (True, "") if ok, (False, error_msg) if not.
    Uses load_markets to verify USDC or USD pair exists before starting TWAP.
    """
    symbol_ccxt = symbol_to_ccxt(symbol, prefer_usdc=True)
    if not symbol_ccxt:
        return False, "Invalid symbol"
    try:
        exchange = get_coinbase_exchange()
        exchange.load_markets()
        if symbol_ccxt in exchange.markets:
            return True, ""
        base = symbol_ccxt.split("/")[0] if "/" in symbol_ccxt else symbol_ccxt
        usd_pair = f"{base}/USD"
        if usd_pair in exchange.markets:
            return True, ""
        return False, f"{symbol} is not available on Coinbase. Use a supported symbol (e.g. BTC, ETH)."
    except Exception as e:
        return False, str(e)


def _twap_cooldown_ok(symbol: str) -> bool:
    with _twap_lock:
        key = (symbol or "").strip().upper()
        if not key:
            return False
        last = _twap_last_start.get(key, 0)
        if time.time() - last < TWAP_COOLDOWN_SEC:
            return False
        _twap_last_start[key] = time.time()
        _save_cooldowns(_twap_last_start)
        return True


def start_twap_buy_async(
    symbol: str,
    total_usd: float,
    num_slices: int = 5,
    duration_minutes: int = 45,
    coin_name: str = "",
    coin_id: str = "",
    stop_pct: Optional[float] = None,
    target_pct: Optional[float] = None,
    twap_min_usd: Optional[float] = None,
    is_test: bool = False,
    execution_context: Optional[Dict[str, Any]] = None,
    lean_override: bool = False,
) -> bool:
    """Start TWAP (or single market buy for small positions) in a daemon thread. Respects per-symbol cooldown. Returns True if started. is_test=True so test orders do not reserve cash. execution_context: optional dict (market_regime, quant_tier, quant_path, trading_scenario) for journal position tagging. lean_override: True only for explicit manual/admin routes — bypasses the lean execution gate."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return False
    if not _twap_cooldown_ok(sym):
        logger.info(f"  Auto-exec skip {sym}: TWAP cooldown (same symbol in last {TWAP_COOLDOWN_SEC // 60} min)")
        return False
    symbol_ccxt = symbol_to_ccxt(symbol)
    if not symbol_ccxt:
        logger.warning(f"  Auto-exec skip {sym}: no Coinbase market for symbol")
        return False
    # LEAN fail-closed execution gate (Stage 1c): block non-lean entries BEFORE any
    # order reaches the exchange — the only enforcement point ahead of the irreversible
    # buy. The signal prune (quant_signal) and journal fill guard (open_position) remain
    # as upstream/downstream backstops; all three delegate to lean_policy so they cannot
    # drift. Re-derives from execution_context primitives, fail-closed. The manual admin
    # route passes lean_override=True.
    if not lean_override:
        import lean_policy
        if lean_policy.is_lean_active():
            _audit = {**(execution_context or {}), 'coin_symbol': sym}
            reason = lean_policy.assert_exec_compliant(execution_context or {})
            if reason:
                lean_policy.log_lean_event('exec_blocked', _audit, reason)
                logger.warning(f"  Auto-exec skip {sym}: LEAN execution gate — {reason}")
                return False
            lean_policy.log_lean_event('exec_allowed', _audit, None)
    threshold = float(twap_min_usd) if twap_min_usd is not None else DEFAULT_TWAP_MIN_USD
    use_single = total_usd < threshold
    _register_active_twap(sym, total_usd, mode="market" if use_single else "twap", coin_name=coin_name or sym, is_test=is_test)
    ctx = execution_context or {}
    if use_single:
        logger.info(f"  Auto-exec: ${total_usd:.0f} < ${threshold:.0f} threshold → single market buy (no TWAP)")
        target = run_single_market_buy_coinbase
        args = (symbol_ccxt, total_usd, coin_name, coin_id, stop_pct, target_pct, ctx, is_test)
    else:
        target = run_twap_buy_coinbase
        args = (symbol_ccxt, total_usd, num_slices, duration_minutes, coin_name, coin_id, stop_pct, target_pct, ctx, is_test)
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return True

# -----------------------------------------------------------------------------
# Safety: dynamic total risk cap and correlation limits
# -----------------------------------------------------------------------------
def execution_safety_ok(
    portfolio_risk_summary: Optional[Dict[str, Any]],
    new_position_risk_pct: float,
    account_size: float,
) -> tuple[bool, str]:
    """
    Returns (ok, reason). Do not auto-execute if total risk would exceed cap or correlation cap breached.
    Cap scales by account size (higher for small accounts); see get_max_total_risk_pct().
    Also blocks new buys if an active TWAP buy is in progress (prevent overlap).
    portfolio_risk_summary: from get_portfolio_risk_summary (total_risk_pct, max_correlated_pct, etc.)
    new_position_risk_pct: max_loss_pct (or risk as % of account) for this new position.
    """
    # Block if an active TWAP buy is still running (prevent overlapping buys)
    active = get_active_twap_orders()
    live_twaps = [t for t in active if not t.get("is_test", False)]
    if live_twaps:
        syms = ", ".join(t.get("symbol", "?") for t in live_twaps)
        return False, f"Active TWAP buy in progress ({syms}) — wait for completion"

    if portfolio_risk_summary is None:
        return True, ""
    total = float(portfolio_risk_summary.get("total_risk_pct", 0) or 0)
    cap = get_max_total_risk_pct(account_size)
    # After adding this position, total risk would be total + new_position_risk_pct (approx)
    projected_total = total + new_position_risk_pct
    if projected_total > cap:
        return False, f"Total risk would exceed {cap:.0f}% (projected {projected_total:.1f}%)"
    max_corr = float(portfolio_risk_summary.get("max_correlated_pct", 0) or 0)
    if max_corr > CORRELATION_CAP_PCT:
        return False, f"Correlation limit breached (high-BTC exposure {max_corr:.1f}% > {CORRELATION_CAP_PCT}%)"
    return True, ""
