# =============================================================================
# xrpl_ledger.py — XRPL account snapshot and transaction events
# Tracks a single XRPL account: balances, USD value over 1d/1w/30d/90d/1y, events
# =============================================================================

import os
import time
import logging
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)



def _resolve_account(account: str | None) -> str:
    """Explicit account, else XRPL_ACCOUNT from .env. No hardcoded default: the address is personal."""
    resolved = (account or os.getenv("XRPL_ACCOUNT") or "").strip()
    if not resolved:
        raise ValueError("No XRPL account configured — set XRPL_ACCOUNT in .env")
    return resolved

# Public XRPL JSON-RPC (no key required)
XRPL_RPC_URL = os.getenv("XRPL_RPC_URL", "https://xrplcluster.com")
COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINPAPRIKA_BASE = "https://api.coinpaprika.com/v1"

# 1 XRP = 1e6 drops
DROPS_PER_XRP = 1_000_000

# Token display names (currency code -> name; extend from https://xrpscan.com/ as needed)
TOKEN_NAMES = {
    "XRP": "XRP",
    "FXRP": "FXRP",
}


def _xrpl_request(method: str, params: list) -> dict:
    """Send JSON-RPC request to XRPL."""
    try:
        r = requests.post(
            XRPL_RPC_URL,
            json={"method": method, "params": [params]},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        # Check for explicit XRPL error status BEFORE checking for success,
        # so callers aren't silently handed an empty dict on account_not_found etc.
        if data.get("status") == "error" or (
            isinstance(data.get("result"), dict) and data["result"].get("status") == "error"
        ):
            err_code = data.get("error") or (data.get("result") or {}).get("error") or "unknown"
            logger.warning("XRPL %s returned error: %s", method, err_code)
            return {}
        if "result" in data and data.get("status") == "success":
            return data["result"]
        return data.get("result") or {}
    except Exception as e:
        logger.exception("XRPL request failed: %s", e)
        return {}


def _drops_to_xrp(drops: str) -> float:
    try:
        return int(drops) / DROPS_PER_XRP
    except (ValueError, TypeError):
        return 0.0


def _get_token_price_xrp(currency: str, issuer: str) -> float | None:
    """
    Get best ask price on XRPL DEX: XRP per 1 unit of token (currency/issuer).
    Uses book_offers: taker_gets=XRP, taker_pays=token => offers to sell token for XRP; first offer = best.
    """
    if not currency or not issuer:
        return None
    try:
        params = {
            "taker_gets": {"currency": "XRP"},
            "taker_pays": {"currency": currency, "issuer": issuer},
            "limit": 1,
            "ledger_index": "validated",
        }
        result = _xrpl_request("book_offers", params)
        offers = result.get("offers") or []
        if not offers:
            return None
        offer = offers[0]
        taker_gets = offer.get("TakerGets")
        taker_pays = offer.get("TakerPays")
        if isinstance(taker_gets, (str, int)):
            # XRP in drops (API may return int or string)
            xrp_amount = _drops_to_xrp(str(taker_gets))
        elif isinstance(taker_gets, dict):
            xrp_amount = float(taker_gets.get("value", 0) or 0)
        else:
            return None
        if isinstance(taker_pays, dict):
            token_amount = float(taker_pays.get("value", 0) or 0)
        else:
            return None
        if token_amount <= 0:
            return None
        return xrp_amount / token_amount
    except Exception as e:
        logger.debug("DEX price for %s/%s failed: %s", currency, issuer[:8], e)
        return None


def get_xrp_price_usd() -> float | None:
    """Current XRP price in USD. CoinGecko primary; CoinPaprika fallback when CG maxes out."""
    try:
        url = f"{COINGECKO_BASE}/simple/price"
        params = {"ids": "ripple", "vs_currencies": "usd"}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        return float(data.get("ripple", {}).get("usd", 0) or 0)
    except Exception as e:
        logger.warning("CoinGecko XRP price fallback: %s", e)
    try:
        r = requests.get(f"{COINPAPRIKA_BASE}/tickers", params={"quotes": "USD"}, timeout=10)
        r.raise_for_status()
        tickers = r.json()
        if isinstance(tickers, list):
            for t in tickers:
                if (t.get("symbol") or "").upper() == "XRP":
                    q = (t.get("quotes") or {}).get("USD") or {}
                    p = float(q.get("price", 0) or 0)
                    if p > 0:
                        return p
                    break
    except Exception as e:
        logger.warning("CoinPaprika XRP price fallback: %s", e)
    return None


def get_xrp_price_history() -> dict:
    """
    Return XRP USD price at now and at 1d, 1w, 30d, 90d, 1y ago.
    CoinGecko primary; CoinPaprika fallback when CG maxes out.
    """
    now = int(time.time())
    from_ts = now - 86400 * 370
    targets = {"now": now, "1d": now - 86400, "1w": now - 86400 * 7, "30d": now - 86400 * 30, "90d": now - 86400 * 90, "1y": now - 86400 * 365}
    out = {k: None for k in targets}
    # Try CoinGecko first
    try:
        url = f"{COINGECKO_BASE}/coins/ripple/market_chart/range"
        params = {"vs_currency": "usd", "from": from_ts, "to": now}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        prices = (r.json().get("prices") or [])
        if prices:
            for label, target_ts in targets.items():
                best = None
                best_diff = float("inf")
                for ts_ms, p in prices:
                    ts = int(ts_ms) // 1000
                    if abs(ts - target_ts) < best_diff:
                        best_diff = abs(ts - target_ts)
                        best = float(p)
                if best is not None:
                    out[label] = best
            return out
    except Exception as e:
        logger.warning("CoinGecko XRP history fallback: %s", e)
    try:
        url = f"{COINGECKO_BASE}/coins/ripple/market_chart"
        r = requests.get(url, params={"vs_currency": "usd", "days": "365"}, timeout=15)
        r.raise_for_status()
        prices = (r.json().get("prices") or [])
        if prices:
            for label, target_ts in targets.items():
                best = None
                best_diff = float("inf")
                for ts_ms, p in prices:
                    ts = int(ts_ms) // 1000
                    if abs(ts - target_ts) < best_diff:
                        best_diff = abs(ts - target_ts)
                        best = float(p)
                if best is not None and out.get(label) is None:
                    out[label] = best
            return out
    except Exception:
        pass
    # Fallback: CoinPaprika (now from ticker; historical from OHLC where free tier allows)
    try:
        now_price = get_xrp_price_usd()
        if now_price is not None:
            out["now"] = now_price
    except Exception:
        pass
    try:
        # CoinPaprika OHLC free tier: last 24h; can fill 1d if we get one daily point
        end_d = datetime.fromtimestamp(now, tz=timezone.utc)
        start_d = datetime.fromtimestamp(now - 86400 * 2, tz=timezone.utc)
        r = requests.get(
            f"{COINPAPRIKA_BASE}/coins/xrp-xrp/ohlcv/historical",
            params={"start": start_d.strftime("%Y-%m-%d"), "end": end_d.strftime("%Y-%m-%d"), "interval": "1d"},
            timeout=10,
        )
        r.raise_for_status()
        ohlc = r.json()
        if isinstance(ohlc, list) and len(ohlc) >= 1 and out.get("1d") is None:
            out["1d"] = float(ohlc[-1].get("close", 0) or 0)
    except Exception:
        pass
    return out


def get_account_snapshot(account: str | None = None) -> dict:
    """
    Fetch account_info + account_lines, normalize to positions and reserve.
    Returns dict: account, xrp_balance, lines (list of {currency, value, issuer, ...}), reserve_xrp.
    """
    account = _resolve_account(account)
    info = _xrpl_request("account_info", {"account": account, "ledger_index": "validated"})
    lines = _xrpl_request("account_lines", {"account": account, "ledger_index": "validated"})

    xrp_balance = 0.0
    reserve_xrp = 0.0
    if info:
        ad = info.get("account_data", {})
        xrp_balance = _drops_to_xrp(ad.get("Balance", "0"))
        owner_count = int(ad.get("OwnerCount", 0) or 0)
        reserve_xrp = 10 + owner_count * 2  # 10 XRP base + 2 per object

    positions = []
    for line in (lines.get("lines") or []):
        currency = line.get("currency", "")
        try:
            value = float(line.get("balance", 0))
        except (TypeError, ValueError):
            value = 0.0
        issuer = line.get("issuer", "")
        positions.append({
            "currency": currency,
            "value": value,
            "issuer": issuer,
        })

    return {
        "account": account,
        "xrp_balance": round(xrp_balance, 6),
        "reserve_xrp": round(reserve_xrp, 6),
        "positions": positions,
    }


def _token_display_name(currency: str) -> str:
    """Display name for asset (from TOKEN_NAMES or currency code)."""
    return TOKEN_NAMES.get(currency, currency) if currency else ""


def get_snapshot_with_value(account: str | None = None) -> dict:
    """
    Full snapshot for the tab: positions + value now and over 1d, 1w, 30d, 90d, 1y.
    Returns assets list (XRP + trust lines) each with token_name, balance, value_usd; value_usd tile = sum of all.
    """
    snap = get_account_snapshot(account)
    price_now = get_xrp_price_usd()
    history = get_xrp_price_history()

    xrp_total = snap["xrp_balance"]
    xrp_value_now = (price_now * xrp_total) if price_now and xrp_total else 0.0

    # Enrich positions with token_name, usd_per_token, value_usd (balance × price)
    positions = []
    for p in snap["positions"]:
        pos = dict(p)
        pos["token_name"] = _token_display_name(p.get("currency", ""))
        price_xrp = _get_token_price_xrp(p.get("currency", ""), p.get("issuer", ""))
        if price_xrp is not None and price_now is not None:
            pos["usd_per_token"] = round(price_xrp * price_now, 6)
        else:
            pos["usd_per_token"] = None
        bal = pos.get("value") or 0
        if pos["usd_per_token"] is not None:
            pos["value_usd"] = round(bal * pos["usd_per_token"], 2)
        else:
            pos["value_usd"] = None
        positions.append(pos)

    # Value now tile = sum of XRP value + all position values
    total_value_now = xrp_value_now + sum(
        p.get("value_usd") or 0 for p in positions
    )
    value_now = total_value_now if total_value_now else None

    # Historical value tiles (XRP balance × historical price; approximation for multi-asset)
    value_1d = (history.get("1d") or 0) * xrp_total if xrp_total else None
    value_1w = (history.get("1w") or 0) * xrp_total if xrp_total else None
    value_30d = (history.get("30d") or 0) * xrp_total if xrp_total else None
    value_90d = (history.get("90d") or 0) * xrp_total if xrp_total else None
    value_1y = (history.get("1y") or 0) * xrp_total if xrp_total else None

    # Build assets list for table: XRP row + positions (token name, balance, value)
    assets = [
        {
            "token_name": "XRP",
            "currency": "XRP",
            "value": xrp_total,
            "usd_per_token": round(price_now, 6) if price_now else None,
            "value_usd": round(xrp_value_now, 2) if xrp_value_now else None,
        }
    ]
    for p in positions:
        assets.append({
            "token_name": p["token_name"],
            "currency": p.get("currency", ""),
            "value": p.get("value"),
            "usd_per_token": p.get("usd_per_token"),
            "value_usd": p.get("value_usd"),
        })

    return {
        "account": snap["account"],
        "xrp_balance": snap["xrp_balance"],
        "reserve_xrp": snap["reserve_xrp"],
        "positions": positions,
        "assets": assets,
        "xrp_price_usd": price_now,
        "value_usd": round(value_now, 2) if value_now is not None else None,
        "value_1d": round(value_1d, 2) if value_1d is not None else None,
        "value_1w": round(value_1w, 2) if value_1w is not None else None,
        "value_30d": round(value_30d, 2) if value_30d is not None else None,
        "value_90d": round(value_90d, 2) if value_90d is not None else None,
        "value_1y": round(value_1y, 2) if value_1y is not None else None,
    }


def _parse_tx_for_account(tx: dict, account: str) -> dict:
    """Turn one tx blob from account_tx into a simple event row."""
    meta = tx.get("meta", {})
    tx_json = tx.get("tx", {}) or tx
    tx_type = tx_json.get("TransactionType", "Unknown")
    ledger_index = tx_json.get("ledger_index")
    date_str = tx_json.get("date")
    if date_str:
        try:
            # Ripple epoch: seconds since 2000-01-01 00:00:00 UTC
            dt = datetime(2000, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=int(date_str))
            time_str = dt.isoformat()
        except Exception:
            time_str = str(date_str)
    else:
        time_str = None

    # Amount: Payment has Amount; others may have different fields
    amount_raw = tx_json.get("Amount")
    amount_xrp = None
    amount_currency = None
    if isinstance(amount_raw, str):
        if amount_raw.isdigit():
            amount_xrp = _drops_to_xrp(amount_raw)
        else:
            amount_currency = amount_raw  # issued currency amount object as string
    elif isinstance(amount_raw, dict):
        amount_currency = amount_raw.get("value")
        amount_currency = f"{amount_currency} {amount_raw.get('currency', '')}".strip()

    return {
        "hash": tx_json.get("hash"),
        "type": tx_type,
        "time": time_str,
        "ledger_index": ledger_index,
        "amount_xrp": round(amount_xrp, 6) if amount_xrp is not None else None,
        "amount_currency": amount_currency,
        "fee_drops": tx_json.get("Fee"),
        "account": tx_json.get("Account"),
        "destination": tx_json.get("Destination"),
    }


def get_account_events(account: str | None = None, limit: int = 100, marker: str | None = None) -> dict:
    """
    Fetch account_tx, return list of events and optional next marker.
    """
    account = _resolve_account(account)
    params = {
        "account": account,
        "limit": min(limit, 200),
        "forward": False,
    }
    if marker:
        params["marker"] = marker
    result = _xrpl_request("account_tx", params)
    transactions = result.get("transactions") or []
    events = [_parse_tx_for_account(t, account) for t in transactions]
    return {
        "events": events,
        "marker": result.get("marker"),
        "account": account,
    }
