#!/usr/bin/env python3
"""Test CoinMarketCap fallback feeds. Run from project root: python scripts/test_cmc_fallback.py
Requires COINMARKETCAP_API_KEY in .env. Mocks CoinGecko and CoinPaprika to fail so CMC is used."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

def _make_mock(real_get):
    def _mock(url, *args, **kwargs):
        if "coingecko.com" in (url or ""):
            raise Exception("CoinGecko mocked failure")
        if "coinpaprika.com" in (url or ""):
            raise Exception("CoinPaprika mocked failure")
        if "alternative.me" in (url or ""):
            raise Exception("Alternative.me mocked (not needed for this test)")
        return real_get(url, *args, **kwargs)
    return _mock

def main():
    from enhanced_data_fetcher import EnhancedDataFetcherV2

    fetcher = EnhancedDataFetcherV2()
    if not fetcher.cmc_key:
        print("ERROR: COINMARKETCAP_API_KEY not set in .env")
        sys.exit(1)
    print(f"CMC key present: {fetcher.cmc_key[:8]}...")

    import requests
    with patch("enhanced_data_fetcher.requests.get", side_effect=_make_mock(requests.get)):
        print("\n1. Testing CMC listings fallback (get_top_50_coins)...")
        fetcher._cache.clear()
        fetcher._cache_times.clear()
        coins = fetcher.get_top_50_coins()
        if coins and len(coins) > 0:
            print(f"   OK: Got {len(coins)} coins from CMC fallback")
            btc = next((c for c in coins if (c.get('symbol') or '').upper() == 'BTC'), None)
            if btc:
                print(f"   Sample: BTC @ ${btc.get('current_price', 0):,.0f}")
        else:
            print("   FAIL: No coins returned")
            sys.exit(1)

        print("\n2. Testing CMC global market cap fallback...")
        fetcher._cache_times["global_market_cap"] = 0
        gmc = fetcher.get_global_market_cap()
        if gmc and gmc.get("total_market_cap_usd", 0) > 0:
            print(f"   OK: Total market cap ${gmc['total_market_cap_usd']/1e12:.2f}T")
        else:
            print("   FAIL: No global market cap")
            sys.exit(1)

        print("\n3. Testing CMC batch prices fallback (get_current_prices_fast)...")
        fetcher._simple_price_cache = {"ts": 0, "prices": {}}
        prices = fetcher.get_current_prices_fast(["bitcoin", "ethereum"], max_coins_cache_age_sec=99999)
        if prices:
            print(f"   OK: Got {len(prices)} prices: {list(prices.keys())}")
            for k, v in list(prices.items())[:2]:
                print(f"      {k}: ${v:,.2f}")
        else:
            print("   FAIL: No prices returned")
            sys.exit(1)

    print("\nAll CMC fallback feeds OK.")

if __name__ == "__main__":
    main()
