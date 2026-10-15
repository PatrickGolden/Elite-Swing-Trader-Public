# tests/test_xrpl_ledger.py — XRP price and history; CoinGecko primary, CoinPaprika fallback (no Binance)

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from xrpl_ledger import get_xrp_price_usd, get_xrp_price_history


class TestGetXrpPriceUsd:
    """XRP USD price: CoinGecko primary, CoinPaprika fallback (Binance removed)."""

    def test_returns_float_when_coingecko_ok(self):
        mock_r = MagicMock()
        mock_r.json.return_value = {"ripple": {"usd": 2.5}}
        mock_r.raise_for_status = MagicMock()
        with patch("xrpl_ledger.requests.get", return_value=mock_r):
            result = get_xrp_price_usd()
        assert result == 2.5

    def test_uses_coinpaprika_fallback_when_coingecko_fails(self):
        tickers = [
            {"symbol": "BTC", "quotes": {"USD": {"price": 50000}}},
            {"symbol": "XRP", "quotes": {"USD": {"price": 2.35}}},
        ]
        mock_r = MagicMock()
        mock_r.json.return_value = tickers
        mock_r.raise_for_status = MagicMock()
        call_count = [0]

        def get_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("429")
            return mock_r

        with patch("xrpl_ledger.requests.get", side_effect=get_side_effect):
            result = get_xrp_price_usd()
        assert result == 2.35

    def test_returns_none_when_both_fail(self):
        with patch("xrpl_ledger.requests.get", side_effect=Exception("Network error")):
            result = get_xrp_price_usd()
        assert result is None


class TestGetXrpPriceHistory:
    """XRP price history: CoinGecko primary, CoinPaprika fallback (no Binance klines)."""

    def test_returns_dict_with_expected_keys(self):
        with patch("xrpl_ledger.requests.get", side_effect=Exception("fail")):
            # Fallback path: at most "now" from get_xrp_price_usd
            with patch("xrpl_ledger.get_xrp_price_usd", return_value=2.0):
                result = get_xrp_price_history()
        assert isinstance(result, dict)
        assert "now" in result
        assert "1d" in result
        assert "1w" in result
        assert "30d" in result
        assert "90d" in result
        assert "1y" in result

    def test_fallback_fills_now_via_get_xrp_price_usd(self):
        with patch("xrpl_ledger.requests.get", side_effect=Exception("fail")):
            with patch("xrpl_ledger.get_xrp_price_usd", return_value=2.1):
                result = get_xrp_price_history()
        assert result["now"] == 2.1
