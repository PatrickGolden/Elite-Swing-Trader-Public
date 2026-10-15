# Owner instruction 2026-09-27: "I have a big XRP position sitting in my coinbase account.
# Do not trade unless i approve." These tests pin that no code path can send an XRP order.

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import execution_engine as ee
from protected_assets import PROTECTED_ASSETS, base_asset, is_protected

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GUARDED_ORDER_FUNCTIONS = {"_place_market_buy_single", "place_stop_limit_sell_order",
                           "place_limit_sell_order", "place_market_sell"}


def test_xrp_is_protected_in_every_spelling():
    assert "XRP" in PROTECTED_ASSETS
    for s in ("XRP", "xrp", "XRP/USD", "XRP/USDC", "XRP-USD", "XRPUSD", " xrp-usdc "):
        assert is_protected(s), s
    for s in ("BTC", "BTC/USD", "ETH-USD", "WBTC", "XRPL", "SOL"):
        assert not is_protected(s), s
    assert base_asset("WBTC") == "WBTC"   # quote-suffix stripping must not mangle tickers


def test_market_buy_refused_without_touching_exchange():
    ex = MagicMock()
    r = ee._place_market_buy_single(ex, "XRP/USD", 100.0)
    assert r["success"] is False and r["protected"] is True
    assert ex.mock_calls == []            # not even a ticker fetch


def test_market_sell_refused_without_touching_exchange():
    ex = MagicMock()
    r = ee.place_market_sell(ex, "XRP/USDC", 5000.0, position_id=1)
    assert r["success"] is False and r["protected"] is True
    assert ex.mock_calls == []


@pytest.mark.parametrize("fn,args", [
    ("place_stop_limit_sell_order", ("XRP", 5000.0, 1.20)),
    ("place_limit_sell_order", ("XRP", 5000.0, 2.00)),
])
def test_resting_sell_orders_refused_before_exchange_lookup(fn, args):
    ex = MagicMock()
    with patch.object(ee, "get_coinbase_exchange", return_value=ex) as get_ex:
        r = getattr(ee, fn)(*args)
    assert r["success"] is False and r["protected"] is True
    get_ex.assert_not_called()
    assert ex.mock_calls == []


def test_unprotected_symbol_still_reaches_the_exchange():
    # The guard must not break normal trading paths.
    ex = MagicMock()
    ex.fetch_ticker.side_effect = RuntimeError("stop here")
    ex.create_market_buy_order.side_effect = RuntimeError("stop here")
    r = ee._place_market_buy_single(ex, "BTC/USD", 10.0)
    assert r.get("protected") is not True
    assert ex.mock_calls != []


def _enclosing_functions_of_order_calls(path: Path):
    """Yield (function_name, lineno) for every exchange.create_*order(...) call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr.startswith("create_") and node.func.attr.endswith("order")):
                yield fn.name, node.lineno


def test_every_order_call_in_the_codebase_is_inside_a_guarded_function():
    # Exhaustiveness: a NEW order path added anywhere must also be guarded, or this fails.
    offenders = []
    skip_dirs = {".venv", ".claude", "tests", "scripts", "__pycache__", ".git", "node_modules"}
    for path in PROJECT_ROOT.rglob("*.py"):
        if skip_dirs & set(path.relative_to(PROJECT_ROOT).parts):
            continue
        for name, line in _enclosing_functions_of_order_calls(path):
            # A call nested inside a guarded function is covered by that function's guard.
            if name not in GUARDED_ORDER_FUNCTIONS:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{line} in {name}()")
    assert offenders == [], "unguarded exchange order call(s): " + "; ".join(offenders)


def test_guard_is_the_first_statement_of_each_order_function():
    tree = ast.parse((PROJECT_ROOT / "execution_engine.py").read_text(encoding="utf-8"))
    seen = set()
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in GUARDED_ORDER_FUNCTIONS:
            body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))]
            first = ast.unparse(body[0])
            assert first.startswith("if is_protected("), f"{fn.name}: guard is not first ({first[:60]})"
            seen.add(fn.name)
    assert seen == GUARDED_ORDER_FUNCTIONS
