# tests/test_data_fetcher.py — EnhancedDataFetcherV2: caching, price fetch, API fallback,
# stablecoin filtering
# Uses absolute imports; run pytest from project root (c:\swing-trader)

import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enhanced_data_fetcher import EnhancedDataFetcherV2

_RESILIENT_GET = "enhanced_data_fetcher.resilient_get"


def _fresh_fetcher() -> EnhancedDataFetcherV2:
    """Return a fetcher with empty in-memory caches (avoids cross-test pollution)."""
    fetcher = EnhancedDataFetcherV2()
    fetcher._cache.clear()
    fetcher._cache_times.clear()
    fetcher._simple_price_cache = {"ts": 0, "prices": {}}
    fetcher._price_source_log.clear()
    fetcher._btc_prices = []
    return fetcher


# ---------------------------------------------------------------------------
# 1. Cache Helpers
# ---------------------------------------------------------------------------

class TestCacheHelpers:
    def test_stale_when_no_cache(self):
        fetcher = _fresh_fetcher()
        assert fetcher._is_stale("coins") is True
        assert fetcher._is_stale("fear_greed") is True

    def test_not_stale_after_set(self):
        fetcher = _fresh_fetcher()
        fetcher._set_cache("coins", [{"id": "bitcoin"}])
        assert fetcher._is_stale("coins") is False

    def test_stale_after_ttl_expires(self):
        fetcher = _fresh_fetcher()
        fetcher._set_cache("coins", [{"id": "bitcoin"}])
        fetcher._cache_times["coins"] = time.time() - 9999
        assert fetcher._is_stale("coins") is True

    def test_unknown_key_defaults_to_300s(self):
        fetcher = _fresh_fetcher()
        fetcher._set_cache("unknown_key", "data")
        assert fetcher._is_stale("unknown_key") is False
        fetcher._cache_times["unknown_key"] = time.time() - 400
        assert fetcher._is_stale("unknown_key") is True


# ---------------------------------------------------------------------------
# 2. Fear & Greed Fallback
# ---------------------------------------------------------------------------

class TestFearGreedFallback:
    def test_fear_greed_returns_cached_on_error(self):
        fetcher = _fresh_fetcher()
        fetcher._set_cache("fear_greed", {"value": 65, "label": "Greed", "history": []})
        fetcher._cache_times["fear_greed"] = time.time() - 99999
        with patch(_RESILIENT_GET, side_effect=Exception("Network error")):
            result = fetcher.get_fear_greed()
        assert result["value"] == 65

    def test_fear_greed_returns_default_when_no_cache(self):
        fetcher = _fresh_fetcher()
        with patch(_RESILIENT_GET, side_effect=Exception("Network error")):
            result = fetcher.get_fear_greed()
        assert result["value"] == 50
        assert result["label"] == "Neutral"

    def test_fear_greed_returns_alternative_me_when_success(self):
        """When Alternative.me responds, get_fear_greed returns value/label/history."""
        fetcher = _fresh_fetcher()
        fetcher._cache_times["fear_greed"] = 0
        alt_me_response = {
            "data": [
                {"value": "28", "value_classification": "Fear"},
                {"value": "30", "value_classification": "Fear"},
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = alt_me_response
        mock_resp.raise_for_status = MagicMock()
        with patch(_RESILIENT_GET, return_value=mock_resp):
            result = fetcher.get_fear_greed()
        assert result["value"] == 28
        assert result["label"] == "Fear"
        assert len(result["history"]) == 2


# ---------------------------------------------------------------------------
# 3. Top 50 Coins Fallback
# ---------------------------------------------------------------------------

class TestTop50CoinsFallback:
    def test_returns_cached_on_api_error(self):
        fetcher = _fresh_fetcher()
        cached_data = [{"id": "bitcoin", "current_price": 50000}]
        fetcher._set_cache("coins", cached_data)
        fetcher._cache_times["coins"] = time.time() - 99999
        with patch(_RESILIENT_GET, side_effect=Exception("API down")):
            result = fetcher.get_top_50_coins()
        assert result == cached_data

    def test_returns_empty_when_no_cache_and_error(self):
        fetcher = _fresh_fetcher()
        with patch(_RESILIENT_GET, side_effect=Exception("API down")):
            result = fetcher.get_top_50_coins()
        assert result == []

    def test_returns_cache_when_not_stale(self):
        fetcher = _fresh_fetcher()
        cached_data = [{"id": "bitcoin"}]
        fetcher._set_cache("coins", cached_data)
        result = fetcher.get_top_50_coins()
        assert result == cached_data


# ---------------------------------------------------------------------------
# 4. get_current_prices_fast
# ---------------------------------------------------------------------------

class TestGetCurrentPricesFast:
    def test_returns_price_dict(self):
        fetcher = _fresh_fetcher()
        mock_cg = MagicMock()
        mock_cg.json.return_value = {"bitcoin": {"usd": 50000}, "ethereum": {"usd": 3000}}
        mock_cg.raise_for_status = MagicMock()

        def get_side_effect(url, *args, **kwargs):
            if "coinpaprika" in url:
                raise Exception("Paprika unavailable in test")
            if "simple/price" in url:
                return mock_cg
            raise Exception("Unexpected URL: " + str(url))

        with patch(_RESILIENT_GET, side_effect=get_side_effect):
            result = fetcher.get_current_prices_fast(["bitcoin", "ethereum"])
        assert result["bitcoin"] == 50000
        assert result["ethereum"] == 3000

    def test_empty_input_returns_empty(self):
        fetcher = _fresh_fetcher()
        assert fetcher.get_current_prices_fast([]) == {}

    def test_none_input_returns_empty(self):
        fetcher = _fresh_fetcher()
        assert fetcher.get_current_prices_fast(None) == {}

    def test_api_error_returns_empty(self):
        fetcher = _fresh_fetcher()
        with patch(_RESILIENT_GET, side_effect=Exception("Timeout")):
            result = fetcher.get_current_prices_fast(["bitcoin"])
        assert result == {}

    def test_method_exists(self):
        fetcher = _fresh_fetcher()
        assert hasattr(fetcher, "get_current_prices_fast")


# ---------------------------------------------------------------------------
# 5. Stablecoin Filtering (from app.py is_stablecoin)
# ---------------------------------------------------------------------------

class TestStablecoinFiltering:
    def test_stablecoins_identified(self):
        from app import is_stablecoin
        assert is_stablecoin({"id": "tether", "symbol": "USDT"}) is True
        assert is_stablecoin({"id": "usd-coin", "symbol": "USDC"}) is True

    def test_non_stablecoins_pass(self):
        from app import is_stablecoin
        assert is_stablecoin({"id": "bitcoin", "symbol": "BTC"}) is False
        assert is_stablecoin({"id": "ethereum", "symbol": "ETH"}) is False

    def test_dai_is_stablecoin(self):
        from app import is_stablecoin
        assert is_stablecoin({"id": "dai", "symbol": "DAI"}) is True


# ---------------------------------------------------------------------------
# 6. Macro Data Fallback
# ---------------------------------------------------------------------------

class TestMacroDataFallback:
    def test_macro_returns_defaults_without_fred_key(self):
        fetcher = _fresh_fetcher()
        fetcher.fred_key = ""
        result = fetcher.get_macro_data()
        assert "fed_rate" in result
        assert result["fed_rate"]["value"] is None


# ---------------------------------------------------------------------------
# 7. CoinCap sparkline fallback (full data when CG maxed out)
# ---------------------------------------------------------------------------

class TestCoinCapSparkline:
    """Test _fetch_sparkline_coincap returns prices for RSI/MACD when on fallback."""

    def test_fetch_sparkline_returns_empty_for_unknown_coin(self):
        fetcher = _fresh_fetcher()
        with patch(_RESILIENT_GET, side_effect=Exception("404")):
            result = fetcher._fetch_sparkline_coincap("unknown-coin-xyz")
        assert result == []

    def test_fetch_sparkline_returns_prices_when_coincap_ok(self):
        fetcher = _fresh_fetcher()
        now_ms = int(time.time() * 1000)
        mock_data = [
            {"priceUsd": "50000", "time": now_ms - (168 - i) * 3600 * 1000}
            for i in range(168)
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": mock_data}
        mock_resp.raise_for_status = MagicMock()
        with patch(_RESILIENT_GET, return_value=mock_resp):
            result = fetcher._fetch_sparkline_coincap("bitcoin")
        assert len(result) >= 50
        assert all(isinstance(p, (int, float)) for p in result)
        assert result[0] == 50000.0

    def test_fetch_sparkline_returns_empty_when_fewer_than_50_points(self):
        fetcher = _fresh_fetcher()
        mock_data = [{"priceUsd": "50000", "time": 1}] * 30
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": mock_data}
        mock_resp.raise_for_status = MagicMock()
        with patch(_RESILIENT_GET, return_value=mock_resp):
            result = fetcher._fetch_sparkline_coincap("bitcoin")
        assert result == []


# ---------------------------------------------------------------------------
# 8. CoinPaprika + CoinCap fallback (full data path)
# ---------------------------------------------------------------------------

class TestCoinPaprikaCoinCapFallback:
    """When CoinGecko fails, fallback returns coins with sparklines so RSI/MACD work."""

    def test_coinpaprika_fallback_returns_coins_with_expected_shape(self):
        fetcher = _fresh_fetcher()
        cp_tickers = [
            {
                "id": "btc-bitcoin",
                "symbol": "BTC",
                "name": "Bitcoin",
                "quotes": {"USD": {"price": 50000, "market_cap": 1e12, "percent_change_24h": 1.0}},
            },
            {
                "id": "eth-ethereum",
                "symbol": "ETH",
                "name": "Ethereum",
                "quotes": {"USD": {"price": 3000, "market_cap": 400e9, "percent_change_24h": -0.5}},
            },
        ]
        mock_cp = MagicMock()
        mock_cp.json.return_value = cp_tickers
        mock_cp.raise_for_status = MagicMock()
        mock_cc_data = [
            {"priceUsd": str(50000 + i), "time": int(time.time() * 1000) - (168 - i) * 3600 * 1000}
            for i in range(168)
        ]
        mock_cc = MagicMock()
        mock_cc.json.return_value = {"data": mock_cc_data}
        mock_cc.raise_for_status = MagicMock()

        def get_side_effect(url, *args, **kwargs):
            if "coinpaprika" in url:
                return mock_cp
            if "coincap" in url:
                return mock_cc
            raise Exception("Unexpected URL: " + str(url))

        with patch(_RESILIENT_GET, side_effect=get_side_effect), patch(
            "enhanced_data_fetcher.time.sleep"
        ):
            coins = fetcher._get_top_50_coins_coinpaprika()
        assert len(coins) == 2
        assert coins[0]["id"] == "bitcoin"
        assert coins[0]["symbol"] == "BTC"
        assert coins[0]["current_price"] == 50000
        assert "sparkline_in_7d" in coins[0]
        assert len(coins[0].get("sparkline_in_7d", {}).get("price", [])) >= 50
        assert fetcher._btc_prices and len(fetcher._btc_prices) >= 50

    def test_get_top_50_uses_coinpaprika_when_coingecko_fails(self):
        fetcher = _fresh_fetcher()
        fetcher._cache_times["coins"] = 0
        cp_tickers = [
            {
                "id": "btc-bitcoin",
                "symbol": "BTC",
                "name": "Bitcoin",
                "quotes": {"USD": {"price": 60000, "market_cap": 1.2e12, "percent_change_24h": 2.0}},
            },
        ]
        mock_cp = MagicMock()
        mock_cp.json.return_value = cp_tickers
        mock_cp.raise_for_status = MagicMock()
        mock_cc_data = [{"priceUsd": "60000", "time": int(time.time() * 1000) - i * 3600 * 1000} for i in range(168)]
        mock_cc = MagicMock()
        mock_cc.json.return_value = {"data": mock_cc_data}
        mock_cc.raise_for_status = MagicMock()

        def get_side_effect(url, *args, **kwargs):
            if "coingecko" in url:
                raise Exception("429 rate limit")
            if "coinpaprika" in url:
                return mock_cp
            if "coincap" in url:
                return mock_cc
            raise Exception("Unexpected: " + str(url))

        with patch(_RESILIENT_GET, side_effect=get_side_effect), patch(
            "enhanced_data_fetcher.time.sleep"
        ):
            result = fetcher.get_top_50_coins()
        assert len(result) == 1
        assert result[0]["id"] == "bitcoin"
        assert result[0]["current_price"] == 60000
        assert len(result[0].get("sparkline_in_7d", {}).get("price", [])) >= 50


# ---------------------------------------------------------------------------
# 9. 24h momentum gainers merge
# ---------------------------------------------------------------------------

class TestTopGainers24h:
    def test_get_top_gainers_fetches_by_24h_change(self):
        fetcher = _fresh_fetcher()
        fetcher._cache_times["gainers_24h"] = 0
        mock_data = [
            {"id": "coin-a", "symbol": "A", "price_change_percentage_24h": 25.0},
            {"id": "coin-b", "symbol": "B", "price_change_percentage_24h": 15.0},
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_data
        mock_resp.raise_for_status = MagicMock()
        with patch(_RESILIENT_GET, return_value=mock_resp):
            result = fetcher.get_top_gainers_24h()
        assert len(result) == 2
        assert result[0]["id"] == "coin-a"

    def test_get_all_data_merges_unique_momentum_coins(self):
        fetcher = _fresh_fetcher()
        mcap = [{"id": "bitcoin", "symbol": "BTC"}]
        gainers = [
            {"id": "bitcoin", "symbol": "BTC"},
            {"id": "solana", "symbol": "SOL", "price_change_percentage_24h": 12},
        ]
        with patch.object(fetcher, "get_top_50_coins", return_value=list(mcap)), \
             patch.object(fetcher, "get_top_gainers_24h", return_value=gainers), \
             patch.object(fetcher, "get_fear_greed", return_value={"value": 50, "label": "Neutral", "history": []}), \
             patch.object(fetcher, "get_macro_data", return_value={}), \
             patch.object(fetcher, "get_news_sentiment", return_value={}), \
             patch.object(fetcher, "get_global_market_cap", return_value={}), \
             patch.object(fetcher, "get_trending_coins", return_value=set()):
            data = fetcher.get_all_data(n_enhanced=0)
        coins = data["coins"]
        assert len(coins) == 2
        ids = {c["id"] for c in coins}
        assert ids == {"bitcoin", "solana"}
        sol = next(c for c in coins if c["id"] == "solana")
        assert sol.get("_source") == "momentum_screen"
        btc = next(c for c in coins if c["id"] == "bitcoin")
        assert btc.get("_source") is None
