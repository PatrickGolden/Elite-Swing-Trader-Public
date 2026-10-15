# scripts/profitability_review_4w.py — Four-week paper trade and missed-opportunity review.
# Run from repo root: python scripts/profitability_review_4w.py

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tls_setup  # noqa: F401,E402

from ml.config import DATA_DIR, RECOMMENDATIONS_FILE, SCREENING_LOG_FILE
from trade_journal import is_paper_trade

WINDOW_START = datetime(2026, 4, 16)
WINDOW_END = datetime(2026, 5, 14, 23, 59, 59)
FORWARD_DAYS = 7
THRESHOLD_PCT = 10.0
REPORT_FILE = DATA_DIR / "profitability_review_4w.json"
PASS_SIGNALS = frozenset({"HOLD", "WEAK_BUY", "SELL"})
BUY_TIERS = frozenset({"BUY", "WEAK_BUY", "MODERATE_BUY", "STRONG_BUY", "ULTRA_BUY"})
REFRESH_SNAPSHOTS_FILE = DATA_DIR / "refresh_snapshots.jsonl"
COINS_CACHE_FILE = DATA_DIR / "coins_cache.json"


def _parse_ts(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).replace(tzinfo=None)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(ts, str):
        return None
    s = ts.strip()
    if not s:
        return None
    if s.endswith("Z"):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    try:
        return datetime.strptime(s[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _in_window(ts: Optional[datetime]) -> bool:
    return ts is not None and WINDOW_START <= ts <= WINDOW_END


def _load_json_list(path: Path) -> List[dict]:
    import safe_json

    data = safe_json.load(str(path), default=list)
    return data if isinstance(data, list) else []


def _load_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _median(values: List[float]) -> Optional[float]:
    return round(statistics.median(values), 4) if values else None


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def _bucket_counts(rows: Iterable[dict], key: str) -> Dict[str, int]:
    out: Counter[str] = Counter()
    for row in rows:
        out[str(row.get(key) or "UNKNOWN")] += 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _build_symbol_map(*sources: Iterable[dict]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for source in sources:
        for row in source:
            coin_id = (row.get("coin_id") or "").strip()
            symbol = (row.get("coin_symbol") or row.get("symbol") or "").strip().upper()
            if coin_id and symbol:
                mapping[symbol] = coin_id
    return mapping


def _dedupe_key(coin_id: str, ts: datetime, signal_type: str) -> Tuple[str, str, str]:
    return coin_id, ts.strftime("%Y-%m-%d"), signal_type


def _forward_window_complete(signal_ts: datetime, last_ts_ms: int) -> bool:
    end_ms = int((signal_ts + timedelta(days=FORWARD_DAYS)).timestamp() * 1000)
    return last_ts_ms >= end_ms


def _max_gain_7d(
    hourly: List[Tuple[int, float, float]],
    signal_ts: datetime,
    entry_price: float,
    current_price: Optional[float] = None,
) -> Tuple[Optional[float], bool, str]:
    if entry_price <= 0:
        return None, False, "missing_entry_price"
    signal_ms = int(signal_ts.timestamp() * 1000)
    end_ms = signal_ms + int(timedelta(days=FORWARD_DAYS).total_seconds() * 1000)
    now_ms = int(datetime.now().timestamp() * 1000)
    complete_by_calendar = datetime.now() >= signal_ts + timedelta(days=FORWARD_DAYS)

    series = list(hourly or [])
    if current_price and current_price > 0:
        tail_ms = series[-1][0] if series else signal_ms
        if tail_ms < end_ms:
            series.append((min(now_ms, end_ms), float(current_price), 0.0))
    if not series:
        return None, False, "missing_price_history"

    timestamps = [row[0] for row in series]
    start_idx = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - signal_ms))
    max_price = entry_price
    saw_forward = False
    for ts_ms, price, _vol in series[start_idx:]:
        if ts_ms < signal_ms:
            continue
        if ts_ms > end_ms:
            break
        saw_forward = True
        if price > max_price:
            max_price = price
    if not saw_forward:
        return None, False, "missing_forward_window"
    complete = complete_by_calendar and series[-1][0] >= end_ms - int(timedelta(hours=24).total_seconds() * 1000)
    gain_pct = ((max_price - entry_price) / entry_price) * 100.0
    source = "historical_replay_cache"
    if current_price and current_price > 0:
        source = "historical_cache_plus_spot"
    return round(gain_pct, 2), complete, source


class PriceHistory:
    def __init__(self) -> None:
        self._cache: Dict[str, List[Tuple[int, float, float]]] = {}
        self._errors: Dict[str, str] = {}

    def get(self, coin_id: str) -> List[Tuple[int, float, float]]:
        if coin_id in self._cache:
            return self._cache[coin_id]
        if coin_id in self._errors:
            return []
        rows: List[Tuple[int, float, float]] = []
        try:
            from historical_replay import fetch_historical_prices

            rows = fetch_historical_prices(coin_id, days=90)
        except Exception as exc:  # noqa: BLE001
            self._errors[coin_id] = str(exc)
        if not rows:
            try:
                from historical_replay import _load_cache

                cached = _load_cache(f"prices_{coin_id}_90d")
                if cached:
                    rows = [tuple(x) for x in cached]
            except Exception as exc:  # noqa: BLE001
                self._errors[coin_id] = str(exc)
        self._cache[coin_id] = rows
        return rows


def _classify_recommendation_blocker(rec: dict) -> str:
    skipped = (rec.get("skipped_reason") or "").strip()
    if skipped == "insufficient_cash":
        return "insufficient_cash"
    final = (rec.get("final_signal") or rec.get("consensus_signal") or "").strip()
    veto = (rec.get("veto_reason") or "").strip()
    if veto and final not in ("ULTRA_BUY", "STRONG_BUY", "MODERATE_BUY"):
        return "veto"
    consensus = (rec.get("consensus_signal") or "").strip()
    if consensus in PASS_SIGNALS or consensus == "CONFLICT" or consensus == "":
        return "policy_no_auto_exec"
    if consensus == "MODERATE_BUY":
        return "policy_no_auto_exec"
    if consensus in ("ULTRA_BUY", "STRONG_BUY"):
        return "executed_or_would_auto_exec"
    return "unknown"


def _summarize_paper_trades(positions: List[dict], price_history: PriceHistory, current_prices: Dict[str, float]) -> dict:
    paper = [p for p in positions if is_paper_trade(p)]
    closed_in_window = []
    opened_in_window = []
    open_positions = []
    for pos in paper:
        entry_ts = _parse_ts(pos.get("entry_time"))
        exit_ts = _parse_ts(pos.get("exit_time"))
        status = (pos.get("status") or "").upper()
        if _in_window(exit_ts) and status == "CLOSED":
            closed_in_window.append(pos)
        if _in_window(entry_ts):
            opened_in_window.append(pos)
        if status == "OPEN":
            open_positions.append(pos)

    closed_pnls = [float(p.get("pnl_pct") or 0.0) for p in closed_in_window]
    closed_pnl_usd = [float(p.get("pnl_usd") or 0.0) for p in closed_in_window]
    wins = [p for p in closed_in_window if (p.get("outcome") or "").lower() == "win" or float(p.get("pnl_usd") or 0.0) > 0]
    losses = [p for p in closed_in_window if p not in wins]

    open_marks = []
    for pos in open_positions:
        coin_id = (pos.get("coin_id") or "").strip()
        entry_price = float(pos.get("entry_price") or 0.0)
        qty = float(pos.get("quantity") or 0.0)
        current = float(current_prices.get(coin_id) or 0.0)
        if entry_price > 0 and current > 0 and qty > 0:
            pnl_pct = ((current - entry_price) / entry_price) * 100.0
            pnl_usd = (current - entry_price) * qty
            open_marks.append(
                {
                    "trade_id": pos.get("trade_id"),
                    "coin_id": coin_id,
                    "symbol": pos.get("coin_symbol"),
                    "entry_time": pos.get("entry_time"),
                    "entry_price": entry_price,
                    "current_price": current,
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_usd": round(pnl_usd, 2),
                    "market_regime": pos.get("market_regime"),
                    "quant_tier": pos.get("quant_tier"),
                }
            )

    left_on_table = []
    for pos in closed_in_window:
        entry_ts = _parse_ts(pos.get("entry_time"))
        entry_price = float(pos.get("entry_price") or 0.0)
        coin_id = (pos.get("coin_id") or "").strip()
        realized = float(pos.get("pnl_pct") or 0.0)
        if not entry_ts or entry_price <= 0 or not coin_id:
            continue
        max_gain, complete, source = _max_gain_7d(
            price_history.get(coin_id),
            entry_ts,
            entry_price,
            current_prices.get(coin_id),
        )
        if max_gain is None:
            continue
        left_on_table.append(
            {
                "trade_id": pos.get("trade_id"),
                "coin_id": coin_id,
                "symbol": pos.get("coin_symbol"),
                "entry_time": pos.get("entry_time"),
                "realized_pnl_pct": round(realized, 2),
                "max_gain_7d_pct": max_gain,
                "upside_gap_pct": round(max_gain - realized, 2),
                "forward_window_complete": complete,
                "price_source": source,
            }
        )

    return {
        "totals": {
            "paper_positions_total": len(paper),
            "closed_in_window": len(closed_in_window),
            "opened_in_window": len(opened_in_window),
            "open_positions": len(open_positions),
        },
        "closed": {
            "win_rate_pct": round((len(wins) / len(closed_in_window)) * 100.0, 2) if closed_in_window else None,
            "net_pnl_usd": round(sum(closed_pnl_usd), 2),
            "avg_pnl_pct": _mean(closed_pnls),
            "median_pnl_pct": _median(closed_pnls),
            "avg_winner_pct": _mean([float(p.get("pnl_pct") or 0.0) for p in wins]),
            "avg_loser_pct": _mean([float(p.get("pnl_pct") or 0.0) for p in losses]),
            "by_regime": _bucket_counts(closed_in_window, "market_regime"),
            "by_quant_tier": _bucket_counts(closed_in_window, "quant_tier"),
            "by_exit_reason": _bucket_counts(closed_in_window, "exit_reason"),
            "by_quant_path": _bucket_counts(closed_in_window, "quant_path"),
        },
        "open_mark_to_market": {
            "positions": open_marks,
            "net_unrealized_usd": round(sum(row["pnl_usd"] for row in open_marks), 2),
            "avg_unrealized_pct": _mean([row["pnl_pct"] for row in open_marks]),
        },
        "left_on_table": {
            "count": len(left_on_table),
            "avg_upside_gap_pct": _mean([row["upside_gap_pct"] for row in left_on_table]),
            "top_examples": sorted(left_on_table, key=lambda row: row["upside_gap_pct"], reverse=True)[:20],
        },
    }


def _collect_signal_rows(
    recommendations: List[dict],
    screening: List[dict],
    symbol_map: Dict[str, str],
) -> Tuple[List[dict], dict]:
    rows: List[dict] = []
    coverage = {
        "recommendations_total": len(recommendations),
        "recommendations_in_window": 0,
        "recommendations_pass_signals_in_window": 0,
        "screening_total": len(screening),
        "screening_in_window": 0,
        "screening_hold_in_window": 0,
        "screening_earliest": None,
        "screening_latest": None,
    }
    if screening:
        scr_ts = [_parse_ts(row.get("timestamp")) for row in screening]
        scr_ts = [ts for ts in scr_ts if ts]
        if scr_ts:
            coverage["screening_earliest"] = min(scr_ts).isoformat(sep=" ")
            coverage["screening_latest"] = max(scr_ts).isoformat(sep=" ")

    for rec in recommendations:
        ts = _parse_ts(rec.get("timestamp"))
        if not _in_window(ts):
            continue
        coverage["recommendations_in_window"] += 1
        signal = (rec.get("consensus_signal") or "").strip()
        if signal not in PASS_SIGNALS:
            continue
        coverage["recommendations_pass_signals_in_window"] += 1
        entry_price = rec.get("entry_price")
        coin_id = (rec.get("coin_id") or "").strip()
        if entry_price is None or not coin_id:
            continue
        rows.append(
            {
                "source": "recommendations",
                "coin_id": coin_id,
                "symbol": rec.get("coin_symbol") or rec.get("symbol"),
                "timestamp": ts,
                "entry_price": float(entry_price),
                "signal_type": signal,
                "market_regime": rec.get("market_regime"),
                "quant_tier": rec.get("consensus_quality") or rec.get("quant_tier"),
                "reason_bucket": _classify_recommendation_blocker(rec),
            }
        )

    for entry in screening:
        ts = _parse_ts(entry.get("timestamp"))
        if not _in_window(ts):
            continue
        coverage["screening_in_window"] += 1
        quant_tier = (entry.get("quant_tier") or "").upper()
        if quant_tier not in ("HOLD", ""):
            continue
        coverage["screening_hold_in_window"] += 1
        entry_price = entry.get("entry_price")
        coin_id = (entry.get("coin_id") or "").strip()
        if entry_price is None or not coin_id:
            continue
        rows.append(
            {
                "source": "screening",
                "coin_id": coin_id,
                "symbol": entry.get("symbol"),
                "timestamp": ts,
                "entry_price": float(entry_price),
                "signal_type": "HOLD",
                "market_regime": entry.get("market_regime"),
                "quant_tier": entry.get("quant_tier") or "HOLD",
                "reason_bucket": "screening_hold",
            }
        )

    return rows, coverage


def _collect_veto_rows(veto_rows: List[dict], symbol_map: Dict[str, str]) -> List[dict]:
    out = []
    for row in veto_rows:
        ts = _parse_ts(row.get("ts"))
        if not _in_window(ts):
            continue
        coin_id = (row.get("coin_id") or "").strip()
        symbol = (row.get("symbol") or "").strip().upper()
        if not coin_id and symbol:
            coin_id = symbol_map.get(symbol, "")
        entry_price = row.get("current_price")
        if not coin_id or entry_price in (None, 0):
            continue
        out.append(
            {
                "source": "veto",
                "coin_id": coin_id,
                "symbol": symbol,
                "timestamp": ts,
                "entry_price": float(entry_price),
                "signal_type": (row.get("consensus_signal") or "BUY").strip(),
                "market_regime": row.get("regime"),
                "quant_tier": None,
                "reason_bucket": "veto",
                "veto_reason": row.get("veto_reason"),
            }
        )
    return out


def _dedupe_signal_rows(rows: List[dict]) -> List[dict]:
    deduped: Dict[Tuple[str, str, str], dict] = {}
    for row in rows:
        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        key = _dedupe_key(row["coin_id"], ts, row["signal_type"])
        deduped[key] = row
    return list(deduped.values())


def _analyze_missed_rows(rows: List[dict], price_history: PriceHistory, current_prices: Dict[str, float]) -> dict:
    rows = _dedupe_signal_rows(rows)
    incomplete: List[dict] = []
    evaluated = 0
    all_rows: List[dict] = []
    for row in rows:
        ts = row["timestamp"]
        coin_id = row["coin_id"]
        max_gain, complete, source = _max_gain_7d(
            price_history.get(coin_id),
            ts,
            row["entry_price"],
            current_prices.get(coin_id),
        )
        evaluated += 1
        enriched = {
            **row,
            "timestamp": ts.isoformat(sep=" "),
            "max_gain_7d_pct": max_gain,
            "forward_window_complete": complete,
            "price_source": source,
        }
        if max_gain is None or not complete:
            incomplete.append(enriched)
            continue
        all_rows.append(enriched)

    missed = [row for row in all_rows if row["max_gain_7d_pct"] >= THRESHOLD_PCT]
    missed_sorted = sorted(missed, key=lambda row: row["max_gain_7d_pct"], reverse=True)
    return {
        "evaluated_unique_rows": evaluated,
        "unique_rows_with_gain": len(all_rows),
        "missed_count": len(missed),
        "incomplete_forward_window": len(incomplete),
        "by_source": _bucket_counts(missed, "source"),
        "by_signal_type": _bucket_counts(missed, "signal_type"),
        "by_regime": _bucket_counts(missed, "market_regime"),
        "by_quant_tier": _bucket_counts(missed, "quant_tier"),
        "by_reason_bucket": _bucket_counts(missed, "reason_bucket"),
        "top_examples": missed_sorted[:50],
        "all": missed_sorted,
    }


def _compare_baselines(missed: dict) -> dict:
    learning_path = DATA_DIR / "learning_report.json"
    backtest_path = DATA_DIR / "backtest_missed.json"
    comparisons = {
        "learning_report_available": learning_path.exists(),
        "backtest_missed_available": backtest_path.exists(),
        "notes": [],
    }
    if learning_path.exists():
        try:
            learning = json.loads(learning_path.read_text(encoding="utf-8"))
            mo = learning.get("missed_opportunity") or {}
            comparisons["learning_report_threshold_pct"] = mo.get("threshold_pct")
            comparisons["learning_report_recommendations_missed"] = (mo.get("summary") or {}).get("recommendations_missed")
            comparisons["learning_report_screening_missed"] = (mo.get("summary") or {}).get("screening_missed")
            comparisons["notes"].append("learning_report uses outcome_7d at 5% threshold, not peak-7d at 10%.")
        except json.JSONDecodeError:
            comparisons["notes"].append("learning_report.json could not be parsed.")
    if backtest_path.exists():
        try:
            backtest = json.loads(backtest_path.read_text(encoding="utf-8"))
            comparisons["backtest_missed_total"] = backtest.get("total_missed")
            comparisons["backtest_missed_avg_outcome_7d"] = backtest.get("avg_outcome_7d")
            replay_rows = []
            seen = set()
            for row in (backtest.get("all") or backtest.get("top_regrets") or []):
                day = _parse_ts(row.get("date"))
                if day is None:
                    try:
                        day = datetime.strptime(str(row.get("date")), "%Y-%m-%d")
                    except (ValueError, TypeError):
                        continue
                if not _in_window(day):
                    continue
                if row.get("signal_type") not in PASS_SIGNALS:
                    continue
                if float(row.get("max_gain_7d") or 0.0) < THRESHOLD_PCT:
                    continue
                key = (row.get("coin_id"), day.strftime("%Y-%m-%d"), row.get("signal_type"))
                if key in seen:
                    continue
                seen.add(key)
                replay_rows.append(row)
            comparisons["replay_missed_in_window_peak_7d_10pct"] = len(replay_rows)
            comparisons["replay_top_examples"] = sorted(
                replay_rows,
                key=lambda row: float(row.get("max_gain_7d") or 0.0),
                reverse=True,
            )[:10]
            comparisons["notes"].append("backtest_missed.json is historical replay evidence, not live 4w logs.")
        except json.JSONDecodeError:
            comparisons["notes"].append("backtest_missed.json could not be parsed.")
    comparisons["live_missed_count_peak_7d_10pct"] = missed.get("missed_count")
    return comparisons


def _sparkline_change_7d(coin: dict) -> Optional[float]:
    spark = coin.get("sparkline_in_7d") or {}
    prices = spark.get("price") or []
    if len(prices) < 2:
        return None
    start = float(prices[0] or 0.0)
    end = float(prices[-1] or 0.0)
    if start <= 0:
        return None
    return round(((end - start) / start) * 100.0, 2)


def _price_at_ts(hourly: List[Tuple[int, float, float]], signal_ts: datetime) -> Optional[float]:
    if not hourly:
        return None
    signal_ms = int(signal_ts.timestamp() * 1000)
    idx = min(range(len(hourly)), key=lambda i: abs(hourly[i][0] - signal_ms))
    return float(hourly[idx][1])


def _load_refresh_snapshots_in_window() -> List[dict]:
    if not REFRESH_SNAPSHOTS_FILE.exists():
        return []
    rows = []
    for line in REFRESH_SNAPSHOTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(row.get("ts"))
        if _in_window(ts):
            row["_parsed_ts"] = ts
            rows.append(row)
    return rows


def _is_hold_snapshot(coin_row: dict) -> bool:
    quant_signal = (coin_row.get("quant_signal") or "").strip().upper()
    quant_tier = (coin_row.get("quant_tier") or "").strip().upper()
    if quant_signal in PASS_SIGNALS:
        return True
    return quant_tier in ("", "HOLD")


def _is_buy_snapshot(coin_row: dict) -> bool:
    quant_signal = (coin_row.get("quant_signal") or "").strip().upper()
    quant_tier = (coin_row.get("quant_tier") or "").strip().upper()
    if quant_signal == "BUY":
        return True
    return quant_tier in BUY_TIERS - {"WEAK_BUY"}


def _analyze_refresh_snapshot_holds(
    snapshots: List[dict],
    price_history: PriceHistory,
    current_prices: Dict[str, float],
) -> dict:
    rows: List[dict] = []
    for snap in snapshots:
        ts = snap.get("_parsed_ts")
        if not isinstance(ts, datetime):
            continue
        for coin_row in snap.get("coins") or []:
            if not _is_hold_snapshot(coin_row):
                continue
            coin_id = (coin_row.get("coin_id") or "").strip()
            if not coin_id:
                continue
            hourly = price_history.get(coin_id)
            entry_price = _price_at_ts(hourly, ts)
            if entry_price is None or entry_price <= 0:
                continue
            rows.append(
                {
                    "source": "refresh_snapshots",
                    "coin_id": coin_id,
                    "symbol": coin_row.get("symbol"),
                    "timestamp": ts,
                    "entry_price": entry_price,
                    "signal_type": "HOLD",
                    "market_regime": snap.get("regime"),
                    "quant_tier": coin_row.get("quant_tier") or "HOLD",
                    "reason_bucket": "refresh_hold",
                }
            )
    return _analyze_missed_rows(rows, price_history, current_prices)


def _load_coins_cache() -> List[dict]:
    if not COINS_CACHE_FILE.exists():
        return []
    try:
        payload = json.loads(COINS_CACHE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    coins = payload.get("coins") if isinstance(payload, dict) else None
    return coins if isinstance(coins, list) else []


def _open_position_symbols(positions: List[dict], paper_only: bool = True) -> set:
    symbols = set()
    for pos in positions:
        if paper_only and not is_paper_trade(pos):
            continue
        if (pos.get("status") or "").upper() != "OPEN":
            continue
        sym = (pos.get("coin_symbol") or "").strip().upper()
        if sym:
            symbols.add(sym)
    return symbols


def _analyze_market_tab_execution_gaps(
    snapshots: List[dict],
    coins_cache: List[dict],
    positions: List[dict],
) -> dict:
    latest = snapshots[-1] if snapshots else None
    if not latest:
        return {
            "latest_snapshot_ts": None,
            "buy_alerts_7d_ge_threshold": [],
            "hold_rallies_7d_ge_threshold": [],
            "buy_alerts_without_open_paper_position": [],
            "notes": ["No refresh snapshots in the analysis window."],
        }

    cache_by_id = {(c.get("id") or "").strip(): c for c in coins_cache if c.get("id")}
    open_paper = _open_position_symbols(positions, paper_only=True)
    buy_alerts = []
    hold_rallies = []
    unpositioned = []
    for coin_row in latest.get("coins") or []:
        coin_id = (coin_row.get("coin_id") or "").strip()
        symbol = (coin_row.get("symbol") or "").strip().upper()
        cache_coin = cache_by_id.get(coin_id) or {}
        change_7d = _sparkline_change_7d(cache_coin)
        if change_7d is None:
            continue
        base = {
            "symbol": symbol,
            "coin_id": coin_id,
            "change_7d_pct": change_7d,
            "quant_signal": coin_row.get("quant_signal"),
            "quant_tier": coin_row.get("quant_tier"),
            "consensus_signal": coin_row.get("consensus_signal"),
            "final_signal": coin_row.get("final_signal"),
            "score": coin_row.get("score"),
        }
        if _is_buy_snapshot(coin_row) and change_7d >= THRESHOLD_PCT:
            buy_alerts.append(base)
            if symbol not in open_paper:
                unpositioned.append({**base, "reason": "buy_alert_no_open_paper_position"})
        if _is_hold_snapshot(coin_row) and change_7d >= THRESHOLD_PCT:
            hold_rallies.append({**base, "reason": "market_tab_hold_with_7d_rally"})

    buy_alerts.sort(key=lambda row: row["change_7d_pct"], reverse=True)
    hold_rallies.sort(key=lambda row: row["change_7d_pct"], reverse=True)
    unpositioned.sort(key=lambda row: row["change_7d_pct"], reverse=True)
    return {
        "latest_snapshot_ts": latest.get("ts"),
        "buy_alerts_7d_ge_threshold": buy_alerts,
        "hold_rallies_7d_ge_threshold": hold_rallies,
        "buy_alerts_without_open_paper_position": unpositioned,
        "notes": [
            "Market tab 7d % uses CoinGecko sparkline endpoints, not peak gain after a logged pass-up.",
            "BUY-tier rows are execution gaps when no open paper position exists, not HOLD pass-ups.",
        ],
    }


def run() -> dict:
    positions = _load_json_list(REPO_ROOT / "positions.json")
    recommendations = _load_json_list(Path(RECOMMENDATIONS_FILE)) if Path(RECOMMENDATIONS_FILE).exists() else []
    screening = _load_json_list(Path(SCREENING_LOG_FILE))
    veto_rows = _load_jsonl(DATA_DIR / "veto_log.jsonl")
    symbol_map = _build_symbol_map(positions, screening, recommendations, veto_rows)

    price_history = PriceHistory()
    current_prices: Dict[str, float] = {}
    coin_ids = set()
    for p in positions:
        if is_paper_trade(p) and p.get("coin_id"):
            coin_ids.add((p.get("coin_id") or "").strip())
    signal_rows, coverage = _collect_signal_rows(recommendations, screening, symbol_map)
    veto_signal_rows = _collect_veto_rows(veto_rows, symbol_map)
    refresh_snapshots = _load_refresh_snapshots_in_window()
    for row in signal_rows + veto_signal_rows:
        if row.get("coin_id"):
            coin_ids.add(row["coin_id"])
    for snap in refresh_snapshots:
        for coin_row in snap.get("coins") or []:
            coin_id = (coin_row.get("coin_id") or "").strip()
            if coin_id:
                coin_ids.add(coin_id)
    try:
        from enhanced_data_fetcher import EnhancedDataFetcherV2

        fetcher = EnhancedDataFetcherV2()
        if coin_ids:
            current_prices = fetcher.get_current_prices_fast(sorted(coin_ids)) or {}
    except Exception:
        current_prices = {}

    paper_summary = _summarize_paper_trades(positions, price_history, current_prices)
    missed = _analyze_missed_rows(signal_rows, price_history, current_prices)
    veto_regrets = _analyze_missed_rows(veto_signal_rows, price_history, current_prices)
    refresh_hold_misses = _analyze_refresh_snapshot_holds(refresh_snapshots, price_history, current_prices)
    market_tab = _analyze_market_tab_execution_gaps(refresh_snapshots, _load_coins_cache(), positions)

    report = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_start": WINDOW_START.isoformat(sep=" "),
            "window_end": WINDOW_END.isoformat(sep=" "),
            "forward_days": FORWARD_DAYS,
            "missed_threshold_pct": THRESHOLD_PCT,
            "metric": "peak_gain_within_7d",
        },
        "data_coverage": {
            **coverage,
            "recommendations_file_present": Path(RECOMMENDATIONS_FILE).exists(),
            "veto_total": len(veto_rows),
            "veto_in_window": len(veto_signal_rows),
            "refresh_snapshots_in_window": len(refresh_snapshots),
            "paper_trading_enabled": None,
            "notes": [],
        },
        "paper_trades": paper_summary,
        "missed_opportunities": missed,
        "refresh_snapshot_hold_misses": refresh_hold_misses,
        "market_tab_reconciliation": market_tab,
        "veto_regrets": veto_regrets,
        "comparisons": _compare_baselines(missed),
        "top_examples": {
            "missed_opportunities": (missed.get("top_examples") or [])[:10],
            "refresh_snapshot_hold_misses": (refresh_hold_misses.get("top_examples") or [])[:10],
            "market_tab_buy_unpositioned": (market_tab.get("buy_alerts_without_open_paper_position") or [])[:10],
            "veto_regrets": (veto_regrets.get("top_examples") or [])[:10],
            "paper_left_on_table": (paper_summary.get("left_on_table") or {}).get("top_examples", [])[:10],
        },
    }

    if not Path(RECOMMENDATIONS_FILE).exists():
        report["data_coverage"]["notes"].append(
            "ai_recommendations_v2.json is missing; recommendation-level misses are unavailable."
        )
    if coverage.get("screening_earliest"):
        earliest = _parse_ts(coverage["screening_earliest"])
        if earliest and earliest > WINDOW_START:
            report["data_coverage"]["notes"].append(
                f"screening_log earliest timestamp {coverage['screening_earliest']} is after window start; early April pass-ups may be truncated."
            )
    try:
        import test_mode

        report["data_coverage"]["paper_trading_enabled"] = test_mode.get_paper_trading()
    except Exception:
        report["data_coverage"]["paper_trading_enabled"] = None

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main() -> int:
    report = run()
    missed = report.get("missed_opportunities", {}).get("missed_count")
    paper_closed = (report.get("paper_trades", {}).get("totals", {}) or {}).get("closed_in_window")
    print(
        f"Wrote {REPORT_FILE} | paper_closed_in_window={paper_closed} | missed_opportunities={missed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
