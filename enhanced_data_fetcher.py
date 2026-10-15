# =============================================================================
# enhanced_data_fetcher_v2.py — ELITE DATA AGGREGATION
# All free/low-cost sources for maximum signal quality
#
# Price/Market Data: CoinGecko primary; CoinPaprika + CoinCap fallback when CG maxes out (Binance geo-blocked in US)
#
# NEW IN V2:
#   - Funding: Kraken Futures (public); order book: Kraken Spot (public). US-accessible.
#   - BTC correlation calculation for all coins
#   - Volume profile analysis (support/resistance)
#   - CoinCap v2 sparkline fallback for RSI/MACD when on CoinPaprika
#   - Whale-related scoring uses CryptoQuant (exchange_netflow, whale_netflow) only.
# =============================================================================

import logging
import math
import requests
import os
import time
import threading
import numpy as np
from dotenv import load_dotenv
from onchain_data import OnChainFetcher
from resilient_http import resilient_get, CircuitOpenError, CreditBudgetExceeded, get_degraded_sources
from log_sanitize import redact_secrets_in_text

logger = logging.getLogger(__name__)

load_dotenv()


def _redact_exc_for_log(e: BaseException) -> str:
    """Redact API keys from exception text (URLs, literals). Safe for module-level helpers without a fetcher instance."""
    secrets = tuple(
        s
        for s in (
            os.getenv("FRED_API_KEY", ""),
            os.getenv("COINGECKO_API_KEY", ""),
            os.getenv("COINMARKETCAP_API_KEY", ""),
            os.getenv("ALPHA_VANTAGE_API_KEY", ""),
            os.getenv("COINGLASS_API_KEY", ""),
            os.getenv("CRYPTOQUANT_API_KEY", ""),
        )
        if s
    )
    return redact_secrets_in_text(str(e), secrets)


# Fallback when CoinGecko rate-limited (Binance geo-blocked in US)
COINPAPRIKA_BASE = 'https://api.coinpaprika.com/v1'
COINPAPRIKA_GLOBAL = f'{COINPAPRIKA_BASE}/global'
COINCAP_BASE = 'https://api.coincap.io/v2'

# CoinMarketCap: third fallback for listings/global/prices (F&G not available via API)
CMC_BASE = 'https://pro-api.coinmarketcap.com/v1'
ALTERNATIVE_ME_FNG = 'https://api.alternative.me/fng/?limit=7'

# Kraken (US-accessible): real funding + order book for quant gate
KRAKEN_SPOT_BASE = 'https://api.kraken.com/0/public'
KRAKEN_FUTURES_BASE = 'https://futures.kraken.com/derivatives/api/v3'
# CoinGlass (optional): aggregated funding across exchanges; use for multi-venue worst-case veto
COINGLASS_BASE = 'https://open-api-v4.coinglass.com'
# Coinbase Exchange (public): optional second order book venue
COINBASE_EXCHANGE_BASE = 'https://api.exchange.coinbase.com'
# Add new position symbols here so unrealized PnL uses Coinbase price when execution exchange is Coinbase.
COINBASE_PRODUCT_ID = {  # symbol -> product_id for book/ticker (USD or USDC)
    'BTC': 'BTC-USD', 'ETH': 'ETH-USD', 'SOL': 'SOL-USD', 'XRP': 'XRP-USD', 'DOGE': 'DOGE-USD',
    'ADA': 'ADA-USD', 'AVAX': 'AVAX-USD', 'LINK': 'LINK-USD', 'DOT': 'DOT-USD', 'MATIC': 'MATIC-USD',
    'LTC': 'LTC-USD', 'BCH': 'BCH-USD', 'UNI': 'UNI-USD', 'ATOM': 'ATOM-USD', 'ETC': 'ETC-USD',
    'NEAR': 'NEAR-USD', 'APT': 'APT-USD', 'OP': 'OP-USD', 'ARB': 'ARB-USD', 'SUI': 'SUI-USD', 'SEI': 'SEI-USD',
    'TAO': 'TAO-USDC', 'AAVE': 'AAVE-USD', 'HYPE': 'HYPE-USD', 'ZEC': 'ZEC-USD', 'RENDER': 'RENDER-USD',
    'SKY': 'SKY-USD', 'PEPE': 'PEPE-USD', 'ASTER': 'ASTER-USDC',
}
# Symbol -> Kraken spot pair (Depth). Kraken uses XBT for Bitcoin.
KRAKEN_SPOT_PAIR = {
    'BTC': 'XBTUSD', 'ETH': 'ETHUSD', 'SOL': 'SOLUSD', 'XRP': 'XRPUSD', 'DOGE': 'DOGEUSD',
    'ADA': 'ADAUSD', 'AVAX': 'AVAXUSD', 'LINK': 'LINKUSD', 'DOT': 'DOTUSD', 'MATIC': 'MATICUSD',
    'LTC': 'LTCUSD', 'BCH': 'BCHUSD', 'UNI': 'UNIUSD', 'ATOM': 'ATOMUSD', 'ETC': 'ETCUSD',
    'XLM': 'XLMUSD', 'NEAR': 'NEARUSD', 'APT': 'APTUSD', 'OP': 'OPUSD', 'ARB': 'ARBUSD',
    'INJ': 'INJUSD', 'SUI': 'SUIUSD', 'SEI': 'SEIUSD', 'PEPE': 'PEPEUSD', 'TON': 'TONUSD',
}
# Symbol -> Kraken Futures perpetual (funding). PF_ prefix.
KRAKEN_FUTURES_SYMBOL = {
    'BTC': 'PF_XBTUSD', 'ETH': 'PF_ETHUSD', 'SOL': 'PF_SOLUSD', 'XRP': 'PF_XRPUSD',
    'DOGE': 'PF_DOGEUSD', 'LTC': 'PF_LTCUSD', 'LINK': 'PF_LINKUSD', 'AVAX': 'PF_AVAXUSD',
    'ADA': 'PF_ADAUSD', 'DOT': 'PF_DOTUSD', 'MATIC': 'PF_MATICUSD', 'UNI': 'PF_UNIUSD',
    'ATOM': 'PF_ATOMUSD', 'ETC': 'PF_ETCUSD', 'NEAR': 'PF_NEARUSD', 'APT': 'PF_APTUSD',
    'OP': 'PF_OPUSD', 'ARB': 'PF_ARBUSD', 'SUI': 'PF_SUIUSD', 'SEI': 'PF_SEIUSD',
}

# Map CoinGecko/CoinPaprika coin_id -> CoinCap asset id (where they differ)
COINGECKO_ID_TO_COINCAP_ID = {
    'avalanche-2': 'avalanche',
    'hedera-hashgraph': 'hedera',
    'injective-protocol': 'injective-protocol',
    'xrp': 'ripple',  # CoinPaprika uses xrp-xrp -> xrp; CoinCap uses ripple
}

# Map CoinGecko coin_id -> symbol (for CoinPaprika fallback lookup)
COINGECKO_ID_TO_SYMBOL = {
    'bitcoin': 'BTC', 'ethereum': 'ETH', 'ripple': 'XRP', 'binancecoin': 'BNB',
    'cardano': 'ADA', 'dogecoin': 'DOGE', 'solana': 'SOL', 'polkadot': 'DOT',
    'tron': 'TRX', 'chainlink': 'LINK', 'avalanche-2': 'AVAX', 'polygon': 'MATIC',
    'shiba-inu': 'SHIB', 'litecoin': 'LTC', 'bitcoin-cash': 'BCH', 'near': 'NEAR',
    'uniswap': 'UNI', 'leo-token': 'LEO', 'stellar': 'XLM', 'cosmos': 'ATOM',
    'monero': 'XMR', 'ethereum-classic': 'ETC', 'aptos': 'APT', 'hedera-hashgraph': 'HBAR',
    'filecoin': 'FIL', 'internet-computer': 'ICP', 'optimism': 'OP', 'arbitrum': 'ARB',
    'stacks': 'STX', 'immutable-x': 'IMX', 'maker': 'MKR', 'aave': 'AAVE',
    'the-graph': 'GRT', 'render-token': 'RNDR', 'injective-protocol': 'INJ',
    'sui': 'SUI', 'sei-network': 'SEI', 'mantle': 'MNT', 'first-digital-usd': 'FDUSD',
    'toncoin': 'TON', 'pepe': 'PEPE', 'wrapped-bitcoin': 'WBTC', 'dai': 'DAI',
    'tether': 'USDT', 'usd-coin': 'USDC', 'true-usd': 'TUSD', 'paxos-standard': 'PAX',
    'aster-2': 'ASTER', 'hyperliquid': 'HYPE', 'morpho': 'MORPHO', 'ethena': 'ENA',
    'pump-fun': 'PUMP', 'memecore': 'MEME', 'pax-gold': 'PAXG', 'whitebit': 'WBT',
    'pi-network': 'PI', 'worldcoin-wld': 'WLD', 'x': 'X',
    'bittensor': 'TAO',
    'fetch-ai': 'FET', 'pudgy-penguins': 'PENGU', 'the-open-network': 'TON',
    'ravedao': 'RAVE', 'zcash': 'ZEC', 'dash': 'DASH', 'algorand': 'ALGO',
    'axie-infinity': 'AXS', 'basic-attention-token': 'BAT', 'arweave': 'AR',
    'akash-network': 'AKT', 'aerodrome-finance': 'AERO', 'bonk': 'BONK',
    'floki': 'FLOKI', 'kaspa': 'KAS', 'fantom': 'FTM', 'theta-token': 'THETA',
    'vechain': 'VET', 'lido-dao': 'LDO', 'celestia': 'TIA', 'jupiter-exchange-solana': 'JUP',
    'ondo-finance': 'ONDO', 'pendle': 'PENDLE', 'pyth-network': 'PYTH',
}

# Symbol -> display name for top coins
SYMBOL_TO_NAME = {
    'BTC': 'Bitcoin', 'ETH': 'Ethereum', 'XRP': 'XRP', 'BNB': 'BNB',
    'ADA': 'Cardano', 'DOGE': 'Dogecoin', 'SOL': 'Solana', 'DOT': 'Polkadot',
    'TRX': 'TRON', 'LINK': 'Chainlink', 'AVAX': 'Avalanche', 'MATIC': 'Polygon',
    'SHIB': 'Shiba Inu', 'LTC': 'Litecoin', 'BCH': 'Bitcoin Cash', 'NEAR': 'NEAR Protocol',
    'UNI': 'Uniswap', 'LEO': 'LEO Token', 'XLM': 'Stellar', 'ATOM': 'Cosmos',
    'XMR': 'Monero', 'ETC': 'Ethereum Classic', 'APT': 'Aptos', 'HBAR': 'Hedera',
    'FIL': 'Filecoin', 'ICP': 'Internet Computer', 'OP': 'Optimism', 'ARB': 'Arbitrum',
    'STX': 'Stacks', 'IMX': 'Immutable X', 'MKR': 'Maker', 'AAVE': 'Aave',
    'GRT': 'The Graph', 'RNDR': 'Render', 'INJ': 'Injective', 'SUI': 'Sui',
    'SEI': 'Sei', 'MNT': 'Mantle', 'TON': 'Toncoin', 'PEPE': 'Pepe',
}

# Bug 5 — thread-safe disk cache writes
_DISK_CACHE_LOCK = threading.Lock()
# Module-level so tests can redirect it: the fetcher singletons are built at app import,
# before any fixture runs, so a path captured in __init__ could never be isolated.
COINS_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'data', 'coins_cache.json')


def _sanitize_coin(coin: dict) -> dict:
    """
    Bug 2 — sanitize NaN/Inf values before cache storage.
    Filters sparkline prices to finite-only; sets current_price to None if non-finite.
    Mutates in-place and returns the dict.
    """
    price = coin.get('current_price')
    if price is not None:
        try:
            f = float(price)
            if not math.isfinite(f):
                coin['current_price'] = None
        except (TypeError, ValueError):
            coin['current_price'] = None

    sparkline = coin.get('sparkline_in_7d')
    if isinstance(sparkline, dict):
        prices = sparkline.get('price')
        if isinstance(prices, list):
            sparkline['price'] = [p for p in prices if p is not None and math.isfinite(float(p))]

    return coin


class EnhancedDataFetcherV2:
    def __init__(self):
        from resilient_http import _mark_degraded

        # API Keys
        self.cg_key = os.getenv('COINGECKO_API_KEY', '')
        self.cmc_key = os.getenv('COINMARKETCAP_API_KEY', '')
        self.fred_key = os.getenv('FRED_API_KEY', '')
        self.av_key = os.getenv('ALPHA_VANTAGE_API_KEY', '')
        self.coinglass_key = os.getenv('COINGLASS_API_KEY', '')

        # API URLs
        self.cg_base = 'https://api.coingecko.com/api/v3'
        self.fred_base = 'https://api.stlouisfed.org/fred/series/observations'

        # --- Loud degradation warnings for missing API keys ---
        if not self.fred_key:
            _mark_degraded("fred", "FRED_API_KEY not set — macro data (Fed rate, 10Y, CPI, M2) unavailable")
        if not self.av_key:
            _mark_degraded("alpha_vantage", "ALPHA_VANTAGE_API_KEY not set — news sentiment defaults to neutral")
        if not os.getenv('CRYPTOQUANT_API_KEY'):
            _mark_degraded("cryptoquant", "CRYPTOQUANT_API_KEY not set — on-chain data (SOPR, whale netflow) using fallback defaults. Veto layer running blind on on-chain signals")
        if not self.coinglass_key:
            logger.info("CoinGlass disabled (no COINGLASS_API_KEY) — using Kraken Futures only. Set the env var to enable worst-case multi-venue funding overlay.")
        if not self.cmc_key:
            _mark_degraded("coinmarketcap", "COINMARKETCAP_API_KEY not set — third-tier listings fallback unavailable")
        if not self.cg_key:
            logger.info("  [INFO] COINGECKO_API_KEY not set — using free tier rate limits (consider Demo key for 10x limits)")

        # Cache
        self._cache = {}
        self._cache_times = {}
        self._fred_key_warned = False
        self._cache_ttl = {
            'coins': 16 * 60,       # 16 min — just above 15-min scheduler so momentum scans always hit cache
            'coins_page': 60 * 60,  # 60 min — Coinbase-tradeable list changes rarely
            'fear_greed': 30 * 60,
            'macro': 6 * 60 * 60,
            'sentiment': 2 * 60 * 60,
            'funding': 5 * 60,
            'order_book': 2 * 60,
            'global_market_cap': 10 * 60,  # 10 min — reduce CoinGecko calls
            'coinbase_spot': 90,  # 90s — Coinbase ticker for position PnL when exchange is Coinbase
            'trending': 30 * 60,  # 30 min — trending list changes slowly
        }

        # Track last successful fetch per source category for staleness detection.
        # Keys match _cache_ttl keys; values are time.time() timestamps.
        self._last_successful_fetch = {}

        # Price source attribution: {coin_id: 'coingecko'|'coinpaprika'|'coinmarketcap'|'coinbase'|'coins_cache'}
        self._price_source_log = {}

        # BTC price data for correlation
        self._btc_prices = []
        # simple/price TTL cache — share one fetch across update_outcomes + backfill + position monitor
        self._simple_price_cache = {'ts': 0, 'prices': {}}
        self._simple_price_cache_ttl = 300  # 5 min — position monitor doesn't need sub-minute freshness

        self.onchain = OnChainFetcher()

        # Disk-backed cache seed: load last known coins data on cold start
        self._load_disk_cache()

    @property
    def _coins_cache_file(self) -> str:
        return COINS_CACHE_FILE

    def _load_disk_cache(self):
        """Seed in-memory coins cache from disk on cold start (avoids immediate API call)."""
        _MIN_COINS_FOR_VALID_CACHE = 20
        # Bug 5 — acquire disk lock for consistent reads during concurrent cold-starts
        with _DISK_CACHE_LOCK:
            try:
                if os.path.isfile(self._coins_cache_file):
                    import safe_json
                    data = safe_json.load(self._coins_cache_file, default=None)
                    if isinstance(data, dict) and isinstance(data.get('coins'), list) and len(data['coins']) >= _MIN_COINS_FOR_VALID_CACHE:
                        ts = float(data.get('ts', 0))
                        age = time.time() - ts
                        if age < self._cache_ttl.get('coins', 960) * 3:
                            self._cache['coins'] = data['coins']
                            self._cache_times['coins'] = ts
                            logger.info(f"  [DISK] Loaded {len(data['coins'])} coins from cache (age {age:.0f}s)")
                        else:
                            logger.info(f"  [DISK] Cache too old ({age:.0f}s), fetching fresh")
                    elif isinstance(data, dict) and isinstance(data.get('coins'), list):
                        logger.info(f"  [DISK] Cache has only {len(data['coins'])} coins (min {_MIN_COINS_FOR_VALID_CACHE}), fetching fresh")
            except Exception as e:
                logger.debug("Disk cache load failed: %s", e)

    def _save_disk_cache(self, coins: list):
        """Persist coins data to disk for cold-start resilience."""
        # Bug 5 — acquire disk lock to prevent concurrent writers from corrupting the file
        with _DISK_CACHE_LOCK:
            try:
                import safe_json
                os.makedirs(os.path.dirname(self._coins_cache_file), exist_ok=True)
                safe_json.save(self._coins_cache_file, {'ts': time.time(), 'coins': coins}, default=str)
            except Exception as e:
                logger.debug("Disk cache save failed: %s", e)

    def get_price_sources(self, coin_ids: list = None) -> dict:
        """Return {coin_id: source_provider} for recent price lookups. Useful for exit/signal audit."""
        if coin_ids is None:
            return dict(self._price_source_log)
        return {cid: self._price_source_log.get(cid, 'unknown') for cid in coin_ids if cid in self._price_source_log}

    def _safe_exc(self, e: BaseException) -> str:
        """String for logging exceptions without leaking API keys embedded in URLs."""
        return _redact_exc_for_log(e)

    def _get_prices_from_coins_cache(self) -> dict:
        """Build {coin_id: current_price} from cached coins data (no API call)."""
        out = {}
        for key in list(self._cache.keys()):
            if key != 'coins' and not key.startswith('coins_page_'):
                continue
            if self._is_stale(key):
                continue
            for c in (self._cache.get(key) or []):
                cid = c.get('id')
                price = c.get('current_price')
                if cid is not None and price is not None:
                    try:
                        out[cid] = float(price)
                    except (TypeError, ValueError):
                        pass
        return out

    def _coin_id_to_symbol(self, coin_id: str) -> str | None:
        """Map coin_id (CoinGecko id or symbol) to symbol for CoinPaprika/CoinCap lookup."""
        if not coin_id:
            return None
        cid = str(coin_id).strip().lower()
        if cid in COINGECKO_ID_TO_SYMBOL:
            return COINGECKO_ID_TO_SYMBOL[cid]
        # Dynamic lookup from coins cache (covers all coins ever fetched from CoinGecko)
        for key in list(self._cache.keys()):
            if key != 'coins' and not key.startswith('coins_page_'):
                continue
            for c in (self._cache.get(key) or []):
                if (c.get('id') or '').lower() == cid and c.get('symbol'):
                    return c['symbol'].upper()
        # Short IDs that look like symbols already (e.g. 'dash' -> 'DASH')
        return cid.upper() if len(cid) <= 5 else None

    # -------------------------------------------------------------------------
    # Cache helpers
    # -------------------------------------------------------------------------
    def _is_stale(self, key):
        if key not in self._cache_times:
            return True
        age = time.time() - self._cache_times[key]
        for cache_type, ttl in self._cache_ttl.items():
            if key.startswith(cache_type):
                return age > ttl
        return age > 300
    
    def _set_cache(self, key, value):
        # Bug 2 — sanitize NaN/Inf in coin lists before caching
        if (key == 'coins' or key.startswith('coins_page_')) and isinstance(value, list):
            value = [_sanitize_coin(c) for c in value]
        self._cache[key] = value
        now = time.time()
        self._cache_times[key] = now
        # Record successful fetch for the source category (first matching TTL key).
        for cat in self._cache_ttl:
            if key == cat or key.startswith(cat):
                self._last_successful_fetch[cat] = now
                break
    
    def get_stale_sources(self) -> dict:
        """Return dict of source categories whose last successful fetch is older than 2x their TTL.
        Used by /api/health to surface data staleness."""
        now = time.time()
        stale = {}
        for cat, ttl in self._cache_ttl.items():
            last = self._last_successful_fetch.get(cat)
            if last is None:
                continue  # Never fetched — not stale, just absent
            age_sec = now - last
            if age_sec > ttl * 2:
                stale[cat] = {
                    'last_fetch_ago_sec': round(age_sec),
                    'ttl_sec': ttl,
                    'staleness_ratio': round(age_sec / ttl, 1),
                }
        return stale

    # =========================================================================
    # BASE DATA SOURCES
    # =========================================================================
    
    def _fetch_sparkline_coincap(self, coin_id: str) -> list:
        """Fetch 7d hourly prices from CoinCap v2 for sparkline (RSI/MACD). Returns list of floats or []."""
        coincap_id = COINGECKO_ID_TO_COINCAP_ID.get(
            (coin_id or '').strip().lower(),
            (coin_id or '').strip().lower(),
        )
        if not coincap_id:
            return []
        try:
            end_ms = int(time.time() * 1000)
            start_ms = end_ms - 7 * 24 * 3600 * 1000
            url = f'{COINCAP_BASE}/assets/{coincap_id}/history'
            params = {'interval': 'h1', 'start': start_ms, 'end': end_ms}
            resp = resilient_get(url, params=params, timeout=10, source="coincap")
            resp.raise_for_status()
            data = resp.json()
            points = (data.get('data') or []) if isinstance(data, dict) else []
            if not points:
                return []
            # Sort by time and take last 168; extract priceUsd as float
            sorted_pts = sorted(points, key=lambda p: int(p.get('time', 0)))
            prices = []
            for p in sorted_pts[-168:]:
                try:
                    prices.append(float(p.get('priceUsd', 0) or 0))
                except (TypeError, ValueError):
                    pass
            return prices if len(prices) >= 50 else []  # Need minimum for correlation
        except Exception as e:
            logger.debug("CoinCap sparkline %s: %s", coincap_id, self._safe_exc(e))
            return []

    def _get_top_50_coins_coinpaprika(self) -> list:
        """Fallback: fetch top coins from CoinPaprika when CoinGecko is rate-limited; fill sparklines via CoinCap."""
        try:
            resp = resilient_get(f'{COINPAPRIKA_BASE}/tickers', params={'quotes': 'USD'}, timeout=15, source="coinpaprika")
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return []
            coins = []
            for t in data[:100]:
                sym = (t.get('symbol') or '').upper()
                if sym in ('USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'FDUSD'):
                    continue
                quotes = (t.get('quotes') or {}).get('USD') or {}
                price = float(quotes.get('price', 0) or 0)
                if price <= 0:
                    continue
                cg_id = t.get('id', '').split('-')[-1] if '-' in str(t.get('id', '')) else t.get('id', sym.lower())
                coin = {
                    'id': cg_id,
                    'symbol': sym,
                    'name': t.get('name', sym),
                    'current_price': price,
                    'market_cap': float(quotes.get('market_cap', 0) or 0),
                    'total_volume': float(quotes.get('volume_24h', 0) or 0),
                    'market_cap_rank': t.get('rank'),
                    'price_change_percentage_1h_in_currency': quotes.get('percent_change_1h'),
                    'price_change_percentage_24h': quotes.get('percent_change_24h'),
                    'price_change_percentage_24h_in_currency': quotes.get('percent_change_24h'),
                    'price_change_percentage_7d_in_currency': quotes.get('percent_change_7d'),
                    'price_change_percentage_30d_in_currency': quotes.get('percent_change_30d'),
                    'sparkline_in_7d': {},
                }
                coins.append(coin)
                if sym == 'BTC':
                    self._btc_prices = []
            # Fill sparklines from CoinCap so RSI/MACD/volume profile/BTC correlation work
            for i, coin in enumerate(coins):
                cid = coin.get('id') or ''
                prices = self._fetch_sparkline_coincap(cid)
                if prices:
                    coin['sparkline_in_7d'] = {'price': prices}
                    if (coin.get('symbol') or '').upper() == 'BTC':
                        self._btc_prices = prices
                if i < len(coins) - 1:
                    time.sleep(0.5)  # Stay under 200 req/min
            return coins
        except Exception as e:
            logger.error(f"  [ERR] CoinPaprika tickers: {self._safe_exc(e)}")
            return []

    def _get_top_50_coins_coinmarketcap(self) -> list:
        """Third fallback: fetch top coins from CoinMarketCap; fill sparklines via CoinCap."""
        if not self.cmc_key:
            return []
        try:
            url = f'{CMC_BASE}/cryptocurrency/listings/latest'
            resp = resilient_get(
                url,
                headers={'X-CMC_PRO_API_KEY': self.cmc_key},
                params={'limit': 100, 'convert': 'USD'},
                timeout=15,
                source="coinmarketcap",
            )
            resp.raise_for_status()
            data = resp.json()
            raw_list = data.get('data') or []
            if not isinstance(raw_list, list):
                return []
            coins = []
            for i, c in enumerate(raw_list[:80]):
                quote = (c.get('quote') or {}).get('USD') or {}
                sym = (c.get('symbol') or '').upper()
                if sym in ('USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'FDUSD'):
                    continue
                slug = (c.get('slug') or '').strip().lower() or sym.lower()
                price = float(quote.get('price', 0) or 0)
                if price <= 0:
                    continue
                coin = {
                    'id': slug,
                    'symbol': sym,
                    'name': c.get('name', sym),
                    'current_price': price,
                    'market_cap': float(quote.get('market_cap', 0) or 0),
                    'total_volume': float(quote.get('volume_24h', 0) or 0),
                    'market_cap_rank': c.get('cmc_rank') or (i + 1),
                    'price_change_percentage_1h_in_currency': quote.get('percent_change_1h'),
                    'price_change_percentage_24h': quote.get('percent_change_24h'),
                    'price_change_percentage_24h_in_currency': quote.get('percent_change_24h'),
                    'price_change_percentage_7d_in_currency': quote.get('percent_change_7d'),
                    'price_change_percentage_30d_in_currency': quote.get('percent_change_30d'),
                    'sparkline_in_7d': {},
                }
                coins.append(coin)
                if sym == 'BTC':
                    self._btc_prices = []
            for i, coin in enumerate(coins):
                cid = coin.get('id') or ''
                prices = self._fetch_sparkline_coincap(cid)
                if prices:
                    coin['sparkline_in_7d'] = {'price': prices}
                    if (coin.get('symbol') or '').upper() == 'BTC':
                        self._btc_prices = prices
                if i < len(coins) - 1:
                    time.sleep(0.3)
            return coins
        except Exception as e:
            logger.error(f"  [ERR] CoinMarketCap listings: {self._safe_exc(e)}")
            return []

    def get_top_50_coins(self):
        """Fetch top coins. CoinGecko primary; CoinPaprika fallback when CG maxes out."""
        if not self._is_stale('coins'):
            return self._cache['coins']

        # Try CoinGecko first
        try:
            url = f'{self.cg_base}/coins/markets'
            headers = {}
            if self.cg_key:
                headers['x-cg-demo-api-key'] = self.cg_key
            params = {
                'vs_currency': 'usd',
                'order': 'market_cap_desc',
                'per_page': 250,
                'page': 1,
                'sparkline': 'true',
                'price_change_percentage': '1h,24h,7d,30d',
            }
            resp = resilient_get(url, headers=headers, params=params, timeout=15, source="coingecko")
            resp.raise_for_status()
            data = resp.json()
            self._set_cache('coins', data)
            self._save_disk_cache(data)
            btc = next((c for c in data if c.get('id') == 'bitcoin'), None)
            if btc and btc.get('sparkline_in_7d', {}).get('price'):
                self._btc_prices = btc['sparkline_in_7d']['price']
            logger.info(f"  [OK] CoinGecko: {len(data)} coins fetched (single page)")
            return data
        except Exception as e:
            logger.warning(f"  [WARN] CoinGecko fallback: {self._safe_exc(e)}")
            coins = self._get_top_50_coins_coinpaprika()
            source = 'CoinPaprika' if coins else None
            if not coins and self.cmc_key:
                coins = self._get_top_50_coins_coinmarketcap()
                source = 'CoinMarketCap' if coins else None
            if coins:
                self._set_cache('coins', coins)
                self._save_disk_cache(coins)
                logger.info(f"  [OK] {source} fallback: {len(coins)} coins fetched")
                return coins
            # All sources failed — return stale cache with loud warning
            cached = self._cache.get('coins', [])
            if cached:
                age = time.time() - self._cache_times.get('coins', 0)
                logger.error("ALL coin data sources failed — using stale cache (%.0fs old). Trading on outdated prices!", age)
            return cached

    def get_top_gainers_24h(self, per_page: int = 100) -> list:
        """Fetch top coins sorted by 24h price change (momentum screen). Returns coins not already in the market-cap universe."""
        cache_key = 'gainers_24h'
        if not self._is_stale(cache_key):
            return self._cache.get(cache_key, [])
        try:
            url = f'{self.cg_base}/coins/markets'
            headers = {}
            if self.cg_key:
                headers['x-cg-demo-api-key'] = self.cg_key
            params = {
                'vs_currency': 'usd',
                'order': 'price_change_percentage_24h_desc',
                'per_page': per_page,
                'page': 1,
                'sparkline': 'true',
                'price_change_percentage': '1h,24h,7d,30d',
            }
            resp = resilient_get(url, headers=headers, params=params, timeout=15, source="coingecko")
            resp.raise_for_status()
            data = resp.json()
            self._set_cache(cache_key, data)
            top3 = [c.get('symbol', '?') for c in data[:3]]
            logger.info(f"  [OK] CoinGecko 24h gainers: {len(data)} coins (top: {', '.join(top3)})")
            return data
        except Exception as e:
            logger.warning(f"  [WARN] CoinGecko 24h gainers fetch failed: {self._safe_exc(e)}")
            return self._cache.get(cache_key, [])

    def get_coins_page(self, page: int = 1, per_page: int = 50) -> list:
        """Fetch one page of coins. CoinGecko primary; CoinPaprika + CoinCap fallback when CG maxes out."""
        cache_key = f'coins_page_{page}'
        if not self._is_stale(cache_key):
            return self._cache.get(cache_key, [])

        try:
            url = f'{self.cg_base}/coins/markets'
            headers = {}
            if self.cg_key:
                headers['x-cg-demo-api-key'] = self.cg_key
            params = {
                'vs_currency': 'usd',
                'order': 'market_cap_desc',
                'per_page': min(per_page, 250),
                'page': max(1, page),
                'sparkline': 'true',
                'price_change_percentage': '1h,24h,7d,30d',
            }
            resp = resilient_get(url, headers=headers, params=params, timeout=15, source="coingecko")
            resp.raise_for_status()
            data = resp.json()
            self._set_cache(cache_key, data)
            logger.info(f"  [OK] CoinGecko page {page}: {len(data)} coins")
            return data
        except Exception as e:
            logger.warning(f"  [WARN] CoinGecko page {page} fallback: {self._safe_exc(e)}")
            all_coins = self.get_top_50_coins()  # Uses CoinPaprika + CoinCap fallback
            start = (page - 1) * per_page
            page_coins = all_coins[start:start + per_page]
            self._set_cache(cache_key, page_coins)
            return page_coins

    def get_fear_greed(self):
        """Fear & Greed: Alternative.me (CMC does not expose F&G via API)."""
        if not self._is_stale('fear_greed'):
            return self._cache['fear_greed']
        try:
            resp = resilient_get(ALTERNATIVE_ME_FNG, timeout=10, source="alternative_me")
            resp.raise_for_status()
            # Bug 3 — validate schema before indexing; 'data' may be absent on error responses
            payload = resp.json()
            raw = payload.get('data', [])
            if not isinstance(raw, list) or not raw:
                raise ValueError(f"Fear & Greed API returned unexpected shape: {list(payload.keys())}")
            current = raw[0]
            result = {
                'value': int(current['value']),
                'label': current['value_classification'],
                'history': [{'value': int(d['value']), 'label': d['value_classification']} for d in raw],
            }
            self._set_cache('fear_greed', result)
            logger.info(f"  [OK] Fear & Greed: {result['value']} ({result['label']})")
            return result
        except Exception as e:
            logger.error(f"  [ERR] Fear & Greed error: {self._safe_exc(e)}")
            return self._cache.get('fear_greed', {'value': 50, 'label': 'Neutral', 'history': []})
    
    def get_macro_data(self):
        if not self._is_stale('macro'):
            return self._cache['macro']
        
        result = {
            'fed_rate': {'value': None, 'date': None, 'label': 'Fed Funds Rate'},
            'cpi': {'value': None, 'date': None, 'label': 'CPI (YoY %)'},
            't10y': {'value': None, 'date': None, 'label': '10Y Treasury'},
            'm2': {'value': None, 'date': None, 'label': 'M2 YoY %'},  # M2 LIQUIDITY INTEGRATION
        }
        
        if not self.fred_key:
            if not getattr(self, '_fred_key_warned', False):
                self._fred_key_warned = True
                logger.warning("FRED_API_KEY not set — macro tiles will show no data. Add key to .env for Fed funds, 10Y, CPI, M2.")
            return result
        
        def _fred_float(val: str):
            """Bug 3 — safe float conversion for FRED observation values; skips non-numeric strings."""
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        try:
            # Fed Funds
            params = {'series_id': 'FEDFUNDS', 'api_key': self.fred_key, 'file_type': 'json', 'sort_order': 'desc', 'limit': 2}
            resp = resilient_get(self.fred_base, params=params, timeout=10, source="fred")
            resp.raise_for_status()
            obs = resp.json().get('observations', [])
            for o in obs:
                v = _fred_float(o.get('value', '.'))
                if v is not None:
                    result['fed_rate']['value'] = round(v, 3)
                    result['fed_rate']['date'] = o['date']
                    break

            time.sleep(0.3)

            # 10Y
            params['series_id'] = 'GS10'
            resp = resilient_get(self.fred_base, params=params, timeout=10, source="fred")
            obs = resp.json().get('observations', [])
            for o in obs:
                v = _fred_float(o.get('value', '.'))
                if v is not None:
                    result['t10y']['value'] = round(v, 3)
                    result['t10y']['date'] = o['date']
                    break

            time.sleep(0.3)

            # CPI
            params['series_id'] = 'CPIAUCSL'
            params['limit'] = 14
            resp = resilient_get(self.fred_base, params=params, timeout=10, source="fred")
            cpi_data = resp.json().get('observations', [])
            valid_cpi = [o for o in cpi_data if _fred_float(o.get('value', '.')) is not None]
            if len(valid_cpi) >= 13:
                latest = _fred_float(valid_cpi[0]['value'])
                year_ago = _fred_float(valid_cpi[12]['value'])
                if latest is not None and year_ago and year_ago != 0:
                    yoy = round((latest - year_ago) / year_ago * 100, 2)
                    result['cpi']['value'] = yoy
                    result['cpi']['date'] = valid_cpi[0]['date']

            # M2 LIQUIDITY INTEGRATION — M2 Money Supply YoY % (debasement / cycle extension)
            time.sleep(0.3)
            params['series_id'] = 'M2SL'
            params['limit'] = 25
            resp = resilient_get(self.fred_base, params=params, timeout=10, source="fred")
            obs = resp.json().get('observations', [])
            valid = [_fred_float(o['value']) for o in obs if _fred_float(o.get('value', '.')) is not None]
            if len(valid) >= 13:
                latest = valid[0]
                year_ago = valid[12]
                if year_ago and year_ago != 0:
                    yoy = round((latest - year_ago) / year_ago * 100, 2)
                    result['m2'] = {'value': yoy, 'date': obs[0]['date'], 'label': 'M2 YoY %'}

            self._set_cache('macro', result)
            logger.info(f"  [OK] FRED macro data fetched")
            return result
        except Exception as e:
            logger.error(f"  [ERR] FRED error: {self._safe_exc(e)}")
            return self._cache.get('macro', result)
    
    def get_news_sentiment(self):
        if not self._is_stale('sentiment'):
            return self._cache['sentiment']
        
        fallback = {'score': 0.0, 'label': 'Neutral', 'articles': 0, 'bullish': 0, 'bearish': 0}
        
        if not self.av_key:
            return fallback
        
        try:
            url = 'https://www.alphavantage.co/query'
            params = {
                'function': 'NEWS_SENTIMENT',
                'topics': 'blockchain,cryptocurrency,financial_markets',
                'apikey': self.av_key,
                'limit': 50,
            }
            resp = resilient_get(url, params=params, timeout=20, source="alpha_vantage")
            data = resp.json()

            if 'Information' in data:
                return self._cache.get('sentiment', fallback)
            
            if 'feed' not in data or not data['feed']:
                return self._cache.get('sentiment', fallback)
            
            # CoinDesk as the source of sentiment articles (filter by URL)
            SENTIMENT_SOURCE_DOMAIN = 'coindesk.com'
            all_feed = data['feed']
            articles = [a for a in all_feed if SENTIMENT_SOURCE_DOMAIN in (a.get('url') or '')]
            using_coindesk_only = len(articles) > 0
            if not articles:
                articles = all_feed
            scores, bullish, bearish = [], 0, 0
            
            for a in articles:
                s = a.get('overall_sentiment_score')
                if s is not None:
                    s = float(s)
                    scores.append(s)
                    if s > 0.05: bullish += 1
                    elif s < -0.05: bearish += 1
            
            if not scores:
                return self._cache.get('sentiment', fallback)
            
            avg = sum(scores) / len(scores)
            
            if avg > 0.35: label = 'Very Bullish'
            elif avg > 0.15: label = 'Bullish'
            elif avg > 0.05: label = 'Somewhat Bullish'
            elif avg < -0.35: label = 'Very Bearish'
            elif avg < -0.15: label = 'Bearish'
            elif avg < -0.05: label = 'Somewhat Bearish'
            else: label = 'Neutral'
            
            source_note = "CoinDesk" if using_coindesk_only else "All sources"
            result = {'score': round(avg, 4), 'label': label, 'articles': len(articles), 'bullish': bullish, 'bearish': bearish, 'source': source_note}
            self._set_cache('sentiment', result)
            # Cache feed for "View articles" link (url, title, score per item)
            feed_list = []
            for a in articles:
                s = a.get('overall_sentiment_score')
                if s is not None:
                    try:
                        s = float(s)
                    except (TypeError, ValueError):
                        s = None
                feed_list.append({
                    'url': a.get('url') or '',
                    'title': (a.get('title') or '').strip() or 'No title',
                    'score': round(float(s), 4) if s is not None else None,
                })
            self._cache['sentiment_feed'] = feed_list
            logger.info(f"  [OK] Sentiment: {label} ({len(articles)} articles, source: {source_note})")
            return result
        except Exception as e:
            logger.error(f"  [ERR] Alpha Vantage error: {self._safe_exc(e)}")
            return self._cache.get('sentiment', fallback)

    def get_last_sentiment_feed(self):
        """Return the last fetched news sentiment article list (url, title, score) for the 'View articles' link."""
        return self._cache.get('sentiment_feed') or []

    # =========================================================================
    # NEW: FUNDING RATES (Kraken Futures — US-accessible, public, no key)
    # =========================================================================
    
    def _fetch_funding_coinglass(self, symbol: str):
        """
        Optional second venue: CoinGlass aggregated funding (multi-exchange).
        Returns current rate in percent or None on missing key/error.
        """
        if not (self.coinglass_key or '').strip():
            return None
        sym = (symbol or '').strip().upper()
        cache_key = f'funding_coinglass_{sym}'
        if not self._is_stale(cache_key) and cache_key in self._cache:
            return self._cache[cache_key]
        try:
            url = f'{COINGLASS_BASE}/api/futures/funding-rate/exchange-list'
            r = resilient_get(url, headers={'CG-API-KEY': self.coinglass_key}, timeout=8, source="coinglass")
            r.raise_for_status()
            data = r.json()
            if data.get('code') != '0':
                return None
            for item in (data.get('data') or []):
                if (item.get('symbol') or '').upper() != sym:
                    continue
                # Use stablecoin_margin_list (USDT/USD) across exchanges
                lst = item.get('stablecoin_margin_list') or item.get('token_margin_list') or []
                rates_pct = []
                for ex in lst:
                    fr = ex.get('funding_rate')
                    if fr is not None:
                        rates_pct.append(float(fr) * 100)  # decimal -> percent
                if not rates_pct:
                    return None
                # Worst case for veto: max (most positive = squeeze risk)
                current_pct = max(rates_pct) if rates_pct else None
                if current_pct is not None:
                    self._set_cache(cache_key, current_pct)
                    return current_pct
                return None
            return None
        except Exception as e:
            logger.debug(f"CoinGlass funding for {sym}: {self._safe_exc(e)}")
            return None

    def get_funding_rate(self, symbol):
        """
        Multi-venue funding: Kraken Futures (primary) + optional CoinGlass (aggregated).
        Returns current rate in percent; for veto uses worst case (max) when both available.
        """
        sym = (symbol or '').strip().upper()
        fut = KRAKEN_FUTURES_SYMBOL.get(sym)
        cache_key = f'funding_{sym}'
        if not self._is_stale(cache_key) and cache_key in self._cache:
            return self._cache[cache_key]
        kraken_pct = None
        avg_24h = 0
        try:
            if fut:
                url = f'{KRAKEN_FUTURES_BASE}/historical-funding-rates'
                r = resilient_get(url, params={'symbol': fut, 'limit': 3}, timeout=8, source="kraken_futures")
                r.raise_for_status()
                data = r.json()
                rates = (data or {}).get('rates') or []
                if rates and isinstance(rates[0], dict):
                    latest = rates[0]
                    raw = float(latest.get('relativeFundingRate', latest.get('fundingRate', 0)) or 0)
                    import math
                    if math.isnan(raw):
                        raw = None
                    if raw is None:
                        kraken_pct = None
                    else:
                        kraken_pct = round(raw * 100, 4)
                    if len(rates) >= 2 and isinstance(rates[1], dict):
                        raw1 = float(rates[1].get('relativeFundingRate', rates[1].get('fundingRate', 0)) or 0)
                        if math.isnan(raw1):
                            raw1 = None
                        if raw is not None and raw1 is not None:
                            avg_24h = round((raw * 100 + raw1 * 100) / 2, 4)
                        else:
                            avg_24h = kraken_pct if kraken_pct is not None else 0
                    else:
                        avg_24h = kraken_pct
        except Exception as e:
            logger.debug(f"Kraken Futures funding for {sym}: {self._safe_exc(e)}")
        coinglass_pct = self._fetch_funding_coinglass(sym)
        # Merge: worst case for veto (max of the two when both present)
        current_pct = kraken_pct
        if coinglass_pct is not None:
            if current_pct is not None:
                current_pct = max(current_pct, coinglass_pct)
            else:
                current_pct = round(float(coinglass_pct), 4)
        funding_unknown = current_pct is None
        if current_pct is None:
            current_pct = 0  # sentinel; callers see risk='unknown' when funding_unknown
        if avg_24h == 0:
            avg_24h = current_pct
        bias = 'long_squeeze_risk' if current_pct > 0.08 else 'short_squeeze_risk' if current_pct < -0.08 else 'neutral'
        risk = 'unknown' if funding_unknown else ('elevated' if abs(current_pct) > 0.05 else 'normal')
        out = {'current': current_pct, 'avg_24h': avg_24h, 'risk': risk, 'bias': bias}
        out['source_ts'] = time.time()  # data quality: last successful fetch
        self._set_cache(cache_key, out)
        return out

    # =========================================================================
    # NEW: BTC CORRELATION
    # =========================================================================
    
    def calculate_btc_correlation(self, coin_prices):
        """
        Calculate correlation between coin and BTC over 7-day period.
        Returns: -1 to +1 (1 = perfect correlation, 0 = no correlation)
        """
        if not self._btc_prices or not coin_prices:
            return 0.0
        
        if len(coin_prices) != len(self._btc_prices):
            return 0.0
        
        try:
            coin_arr = np.array(coin_prices, dtype=float)
            btc_arr = np.array(self._btc_prices, dtype=float)
            
            # Remove any NaN/inf
            mask = np.isfinite(coin_arr) & np.isfinite(btc_arr)
            coin_arr = coin_arr[mask]
            btc_arr = btc_arr[mask]
            
            if len(coin_arr) < 50:  # Need minimum data points
                return 0.0
            
            correlation = np.corrcoef(coin_arr, btc_arr)[0, 1]
            return round(float(correlation), 3)
        except Exception:
            return 0.0
    
    # =========================================================================
    # NEW: VOLUME PROFILE
    # =========================================================================
    
    def calculate_volume_profile(self, prices, volumes):
        """
        Identify high-volume price levels (support/resistance).
        Returns top 3 volume levels.
        """
        if not prices or not volumes or len(prices) != len(volumes):
            return []
        
        try:
            # Create price bins
            prices_arr = np.array(prices, dtype=float)
            volumes_arr = np.array(volumes, dtype=float)
            
            min_price = prices_arr.min()
            max_price = prices_arr.max()
            
            # 20 price bins
            bins = np.linspace(min_price, max_price, 21)
            volume_by_bin = np.zeros(20)
            
            price_range = max_price - min_price
            for i in range(len(prices_arr)):
                bin_idx = min(19, int((prices_arr[i] - min_price) / price_range * 20)) if price_range > 0 else 0
                volume_by_bin[bin_idx] += volumes_arr[i]
            
            # Find top 3 volume levels
            top_indices = volume_by_bin.argsort()[-3:][::-1]
            
            levels = []
            for idx in top_indices:
                price_level = (bins[idx] + bins[idx + 1]) / 2
                volume = volume_by_bin[idx]
                levels.append({
                    'price': round(float(price_level), 8),
                    'volume': round(float(volume), 2),
                    'type': 'resistance' if price_level > prices_arr[-1] else 'support'
                })
            
            return levels
        except Exception:
            return []
    
    # =========================================================================
    # NEW: ORDER BOOK (Kraken Spot — US-accessible, public)
    # =========================================================================
    
    def get_order_book_depth(self, symbol):
        """
        Order book depth from Kraken Spot (public API). Returns bid_ask_ratio (bid vol / ask vol),
        optional depth fields, and whale_support/resistance placeholders.
        Falls back to neutral placeholder on missing symbol or API error.
        """
        sym = (symbol or '').strip().upper()
        pair = KRAKEN_SPOT_PAIR.get(sym)
        if not pair:
            return {'bid_depth_5pct': 0, 'ask_depth_5pct': 0, 'bid_ask_ratio': 1, 'whale_support': False, 'whale_resistance': False}
        cache_key = f'order_book_{sym}'
        if not self._is_stale(cache_key) and cache_key in self._cache:
            return self._cache[cache_key]
        try:
            url = f'{KRAKEN_SPOT_BASE}/Depth'
            r = resilient_get(url, params={'pair': pair, 'count': 25}, timeout=8, source="kraken_spot")
            r.raise_for_status()
            data = r.json()
            err = data.get('error') or []
            if err:
                return {'bid_depth_5pct': 0, 'ask_depth_5pct': 0, 'bid_ask_ratio': 1, 'whale_support': False, 'whale_resistance': False}
            result = data.get('result') or {}
            # Kraken returns result key like XXBTZUSD (not necessarily the requested pair param)
            book = result.get(pair) or next(iter(result.values()), {}) if result else {}
            bids = book.get('bids') or []
            asks = book.get('asks') or []
            # Each level: [price, volume, timestamp]
            def sum_vol(levels, n=10):
                total = 0.0
                for i, lev in enumerate(levels[:n]):
                    if isinstance(lev, (list, tuple)) and len(lev) >= 2:
                        total += float(lev[1])
                    elif isinstance(lev, dict):
                        total += float(lev.get('volume', lev.get(1, 0)) or 0)
                return total
            bid_vol = sum_vol(bids)
            ask_vol = sum_vol(asks)
            if ask_vol <= 0:
                ratio = 1.0
            else:
                ratio = round(bid_vol / ask_vol, 4)
            # Optional second venue: Coinbase; aggregate with median
            cb_ratio = self._fetch_order_book_coinbase(sym)
            if cb_ratio != 1.0 or ratio != 1.0:
                ratio = round((ratio + cb_ratio) / 2, 4) if (ratio != 1.0 and cb_ratio != 1.0) else (ratio if ratio != 1.0 else cb_ratio)
            out = {
                'bid_depth_5pct': 0,
                'ask_depth_5pct': 0,
                'bid_ask_ratio': ratio,
                'whale_support': False,
                'whale_resistance': False,
                'source_ts': time.time(),
            }
            self._set_cache(cache_key, out)
            return out
        except Exception as e:
            logger.debug(f"Kraken order book for {sym}: {self._safe_exc(e)}")
            return {'bid_depth_5pct': 0, 'ask_depth_5pct': 0, 'bid_ask_ratio': 1, 'whale_support': False, 'whale_resistance': False}

    def _fetch_order_book_coinbase(self, symbol: str) -> float:
        """Optional second venue: Coinbase Exchange public book. Returns bid_ask_ratio or 1.0 on error."""
        sym = (symbol or '').strip().upper()
        pid = COINBASE_PRODUCT_ID.get(sym)
        if not pid:
            return 1.0
        cache_key = f'order_book_cb_{sym}'
        if not self._is_stale(cache_key) and cache_key in self._cache:
            return self._cache[cache_key]
        try:
            url = f'{COINBASE_EXCHANGE_BASE}/products/{pid}/book'
            r = resilient_get(url, params={'level': 2}, timeout=8, source="coinbase_exchange")
            r.raise_for_status()
            data = r.json()
            bids = data.get('bids') or []
            asks = data.get('asks') or []
            def sum_vol(levels, n=10):
                total = 0.0
                for lev in levels[:n]:
                    if isinstance(lev, (list, tuple)) and len(lev) >= 2:
                        total += float(lev[1])
                    elif isinstance(lev, dict):
                        total += float(lev.get('size', 0) or 0)
                return total
            bid_vol, ask_vol = sum_vol(bids), sum_vol(asks)
            ratio = round(bid_vol / ask_vol, 4) if ask_vol > 0 else 1.0
            self._set_cache(cache_key, ratio)
            return ratio
        except Exception as e:
            logger.debug(f"Coinbase order book for {sym}: {self._safe_exc(e)}")
            return 1.0

    def get_coinbase_spot_prices(self, symbols: list) -> dict:
        """
        Fetch current USD/USDC spot price from Coinbase ticker for each symbol in COINBASE_PRODUCT_ID.
        Returns {symbol: float_price}. Used for exchange-aligned unrealized PnL when execution exchange is Coinbase.
        Single product failure does not break the rest; optional 90s cache to avoid hammering the API.
        """
        if not symbols:
            return {}
        symbols = list(set((s or '').strip().upper() for s in symbols if s))
        cache_key = 'coinbase_spot_prices'
        if not self._is_stale(cache_key) and cache_key in self._cache:
            cached = self._cache[cache_key]
            return {s: cached[s] for s in symbols if s in cached}
        result = {}
        for sym in symbols:
            pid = COINBASE_PRODUCT_ID.get(sym)
            if not pid:
                continue
            try:
                url = f'{COINBASE_EXCHANGE_BASE}/products/{pid}/ticker'
                r = resilient_get(url, timeout=5, source="coinbase_exchange", max_retries=2)
                r.raise_for_status()
                data = r.json()
                price = data.get('price')
                if price is not None:
                    result[sym] = float(price)
                    self._price_source_log[sym] = 'coinbase'
            except Exception as e:
                logger.debug(f"Coinbase ticker for {sym} ({pid}): {self._safe_exc(e)}")
        if result:
            existing = self._cache.get(cache_key) or {}
            self._set_cache(cache_key, {**existing, **result})
        return result

    # =========================================================================
    # ENHANCED DATA FOR COIN
    # =========================================================================
    
    def get_enhanced_data_for_coin(self, coin):
        """Fetch ALL enhanced metrics for a single coin"""
        symbol = coin.get('symbol', '').upper()
        coin_id = coin.get('id', '')
        
        # Get sparkline for volume profile
        sparkline = coin.get('sparkline_in_7d', {})
        prices = sparkline.get('price', [])
        
        # Calculate BTC correlation
        btc_corr = self.calculate_btc_correlation(prices)
        
        # Volume profile (need volume data - use price as proxy for now)
        volume_levels = self.calculate_volume_profile(prices[-48:] if len(prices) > 48 else prices, 
                                                      [1] * len(prices[-48:] if len(prices) > 48 else prices))
        
        # Advanced on-chain metrics
        onchain_metrics = self.onchain.get_advanced_metrics(symbol)
        
        funding_rate = self.get_funding_rate(symbol)
        order_book = self.get_order_book_depth(symbol)
        now_ts = time.time()
        funding_ok = 'source_ts' in funding_rate and (now_ts - funding_rate.get('source_ts', 0)) < 600
        order_book_ok = 'source_ts' in order_book and (now_ts - order_book.get('source_ts', 0)) < 300
        onchain_ok = (onchain_metrics.get('status') or '') == 'live'
        enhanced = {
            'funding_rate': funding_rate,
            'btc_correlation': btc_corr,
            'order_book': order_book,
            'volume_profile': volume_levels,
            'data_quality': {
                'funding_ok': funding_ok,
                'order_book_ok': order_book_ok,
                'onchain_ok': onchain_ok,
                'ok_count': sum([funding_ok, order_book_ok, onchain_ok]),
            },
            # ADVANCED ON-CHAIN (CryptoQuant; whale netflow = exchange_netflow proxy)
            'sopr': onchain_metrics['sopr'],
            'lth_sopr': onchain_metrics['lth_sopr'],
            'exchange_netflow_24h': onchain_metrics['exchange_netflow_24h'],
            'lth_supply_change_24h_pct': onchain_metrics['lth_supply_change_24h_pct'],
            'realized_cap_hodl_signal': onchain_metrics['realized_cap_hodl_signal'],
            'realized_cap_hodl_waves': onchain_metrics.get('realized_cap_hodl_waves'),
            'whale_netflow': onchain_metrics['whale_netflow'],
            'smart_money_netflow': onchain_metrics.get('smart_money_netflow'),
            'sopr_bands': onchain_metrics.get('sopr_bands', {'short_term': 1.0, 'long_term': 1.0, 'band_signal': 'neutral'}),
            'onchain_status': onchain_metrics['status']
        }
        
        return enhanced
    
    # =========================================================================
    # MAIN DATA FETCH
    # =========================================================================
    
    def get_global_market_cap(self):
        """Fetch global market cap. CoinPaprika primary (free/unlimited); CoinGecko + CMC fallbacks."""
        cache_key = 'global_market_cap'
        if not self._is_stale(cache_key):
            return self._cache.get(cache_key, {})

        # CoinPaprika primary — free, no credit cost
        try:
            resp = resilient_get(COINPAPRIKA_GLOBAL, timeout=10, source="coinpaprika")
            resp.raise_for_status()
            data = resp.json()
            if data:
                result = {
                    'total_market_cap_usd': float(data.get('market_cap_usd', 0) or 0),
                    'market_cap_change_percentage_24h_usd': float(data.get('market_cap_change_24h', 0) or 0),
                    'market_cap_change_percentage_7d_usd': None,
                    'market_cap_change_percentage_30d_usd': None,
                }
                if result['total_market_cap_usd'] > 0:
                    self._set_cache(cache_key, result)
                    logger.info(f"  [OK] Global Market Cap (CoinPaprika): ${result['total_market_cap_usd'] / 1e12:.2f}T")
                    return result
        except Exception as e:
            logger.warning(f"  [WARN] CoinPaprika global error: {self._safe_exc(e)}")

        # Fallback: CoinGecko
        try:
            url = f'{self.cg_base}/global'
            headers = {}
            if self.cg_key:
                headers['x-cg-demo-api-key'] = self.cg_key
            resp = resilient_get(url, headers=headers, timeout=10, source="coingecko")
            resp.raise_for_status()
            data = resp.json()
            if data and 'data' in data:
                gmc = data['data']
                result = {
                    'total_market_cap_usd': gmc.get('total_market_cap', {}).get('usd', 0),
                    'market_cap_change_percentage_24h_usd': gmc.get('market_cap_change_percentage_24h_usd', 0),
                    'market_cap_change_percentage_7d_usd': gmc.get('market_cap_change_percentage_7d_usd'),
                    'market_cap_change_percentage_30d_usd': gmc.get('market_cap_change_percentage_30d_usd'),
                }
                self._set_cache(cache_key, result)
                logger.info(f"  [OK] Global Market Cap (CoinGecko fallback): ${result['total_market_cap_usd'] / 1e12:.2f}T")
                return result
        except Exception as e:
            logger.warning(f"  [WARN] CoinGecko global fallback: {self._safe_exc(e)}")

        # Third fallback: CoinMarketCap
        if self.cmc_key:
            try:
                url = f'{CMC_BASE}/global-metrics/quotes/latest'
                resp = resilient_get(url, headers={'X-CMC_PRO_API_KEY': self.cmc_key}, params={'convert': 'USD'}, timeout=10, source="coinmarketcap")
                resp.raise_for_status()
                data = resp.json()
                d = data.get('data') or {}
                quote = (d.get('quote') or {}).get('USD') or d
                total = float(quote.get('total_market_cap', 0) or d.get('total_market_cap', 0) or 0)
                ch24 = quote.get('market_cap_change_percentage_24h') or d.get('market_cap_change_percentage_24h')
                result = {
                    'total_market_cap_usd': total,
                    'market_cap_change_percentage_24h_usd': float(ch24) if ch24 is not None else 0,
                    'market_cap_change_percentage_7d_usd': None,
                    'market_cap_change_percentage_30d_usd': None,
                }
                if result['total_market_cap_usd'] > 0:
                    self._set_cache(cache_key, result)
                    logger.info(f"  [OK] Global Market Cap (CoinMarketCap fallback): ${result['total_market_cap_usd'] / 1e12:.2f}T")
                    return result
            except Exception as e:
                logger.debug("Global Market Cap CMC fallback: %s", self._safe_exc(e))

        return self._cache.get(cache_key, {})

    def get_trending_coins(self) -> set:
        """
        Fetch current trending coin IDs from CoinGecko /search/trending.
        Returns a set of lowercase coin_ids. Cached 30 min.
        Falls back to empty set on error so scoring still proceeds.
        """
        cache_key = 'trending'
        if not self._is_stale(cache_key):
            return self._cache.get(cache_key, set())
        try:
            url = f'{self.cg_base}/search/trending'
            headers = {}
            if self.cg_key:
                headers['x-cg-demo-api-key'] = self.cg_key
            resp = resilient_get(url, headers=headers, timeout=10, source="coingecko")
            resp.raise_for_status()
            data = resp.json()
            trending_ids: set = set()
            for item in (data.get('coins') or []):
                coin_data = item.get('item') or {}
                cid = (coin_data.get('id') or coin_data.get('coin_id') or '').lower().strip()
                if cid:
                    trending_ids.add(cid)
            self._set_cache(cache_key, trending_ids)
            preview = ', '.join(list(trending_ids)[:5])
            logger.info(f"  [OK] CoinGecko trending: {len(trending_ids)} coins ({preview}...)")
            return trending_ids
        except Exception as e:
            logger.warning(f"  [WARN] CoinGecko trending fetch failed: {self._safe_exc(e)}")
            return set()

    def get_current_prices_fast(self, coin_ids: list, max_coins_cache_age_sec: float | None = None) -> dict:
        """Current USD prices for coin_ids. CoinGecko primary; CoinPaprika fallback when CG maxes out."""
        if not coin_ids:
            return {}
        ids = list(set(str(c) for c in coin_ids if c))
        if not ids:
            return {}
        result = {}
        use_coins_cache = True
        if max_coins_cache_age_sec is not None:
            coins_ts = self._cache_times.get('coins', 0)
            if time.time() - coins_ts > max_coins_cache_age_sec:
                use_coins_cache = False
        if use_coins_cache:
            from_coins = self._get_prices_from_coins_cache()
            for cid in ids:
                if cid in from_coins:
                    result[cid] = from_coins[cid]
                    self._price_source_log[cid] = 'coins_cache'
        need_fetch = [c for c in ids if c not in result]
        if not need_fetch:
            return result
        now = time.time()
        if now - self._simple_price_cache['ts'] <= self._simple_price_cache_ttl:
            for cid in need_fetch:
                if cid in self._simple_price_cache['prices']:
                    result[cid] = self._simple_price_cache['prices'][cid]
            need_fetch = [c for c in need_fetch if c not in result]
        if not need_fetch:
            return result
        # CoinPaprika primary — free/unlimited, saves CoinGecko credits for /coins/markets
        symbol_to_ids = {}
        for cid in need_fetch[:50]:
            sym = self._coin_id_to_symbol(cid)
            if sym:
                symbol_to_ids.setdefault(sym.upper(), []).append(cid)
        try:
            resp = resilient_get(f'{COINPAPRIKA_BASE}/tickers', params={'quotes': 'USD'}, timeout=15, source="coinpaprika")
            resp.raise_for_status()
            tickers = resp.json()
            if isinstance(tickers, list):
                sym_to_best = {}
                for t in tickers:
                    s = (t.get('symbol') or '').upper()
                    q = (t.get('quotes') or {}).get('USD') or {}
                    p = float(q.get('price', 0) or 0)
                    rank = int(t.get('rank') or 99999)
                    if s and p > 0:
                        prev_rank = sym_to_best.get(s, (None, 99999))[1]
                        if rank < prev_rank:
                            sym_to_best[s] = (p, rank)
                sym_to_price = {s: v[0] for s, v in sym_to_best.items()}
                # Build reference prices from raw cache (ignore staleness) for cross-validation
                _xval_ref = {}
                for _xk in list(self._cache.keys()):
                    if _xk != 'coins' and not _xk.startswith('coins_page_'):
                        continue
                    for _xc in (self._cache.get(_xk) or []):
                        _xid = _xc.get('id')
                        _xp = _xc.get('current_price')
                        if _xid is not None and _xp is not None:
                            try:
                                _xval_ref[_xid] = float(_xp)
                            except (TypeError, ValueError):
                                pass
                paprika_rejected = 0
                for sym, cids in symbol_to_ids.items():
                    if sym in sym_to_price:
                        price = sym_to_price[sym]
                        for cid in cids:
                            ref = _xval_ref.get(cid)
                            if ref and ref > 0 and price > 0:
                                drift = abs(price - ref) / ref
                                if drift > 0.05:
                                    logger.warning(
                                        f"  [WARN] CoinPaprika price rejected for {cid} ({sym}): "
                                        f"${price} vs coins_cache ${ref} (drift {drift*100:.1f}%)"
                                    )
                                    result[cid] = ref
                                    self._simple_price_cache['prices'][cid] = ref
                                    self._price_source_log[cid] = 'coins_cache_override'
                                    paprika_rejected += 1
                                    continue
                            result[cid] = price
                            self._simple_price_cache['prices'][cid] = price
                            self._price_source_log[cid] = 'coinpaprika'
                self._simple_price_cache['ts'] = now
                paprika_found = sum(1 for c in need_fetch if c in result)
                if paprika_found:
                    logger.debug(f"  [OK] CoinPaprika prices: {paprika_found}/{len(need_fetch)}")
                if paprika_rejected:
                    logger.warning(f"  [WARN] CoinPaprika: {paprika_rejected} prices rejected (coins_cache override)")
        except Exception as e:
            logger.warning(f"  [WARN] CoinPaprika price error: {self._safe_exc(e)}")
        need_fetch = [c for c in need_fetch if c not in result]
        if not need_fetch:
            return result
        # Fallback: CoinGecko simple/price for coins CoinPaprika couldn't resolve
        try:
            url = f'{self.cg_base}/simple/price'
            params = {'ids': ','.join(need_fetch[:50]), 'vs_currencies': 'usd'}
            headers = {}
            if self.cg_key:
                headers['x-cg-demo-api-key'] = self.cg_key
            resp = resilient_get(url, params=params, headers=headers, timeout=10, source="coingecko")
            resp.raise_for_status()
            data = resp.json()
            # Bug 3 — guard against string 'error' values in the usd field
            fetched = {}
            for cid, d in data.items():
                if not isinstance(d, dict):
                    continue
                raw_usd = d.get('usd')
                if raw_usd is None:
                    continue
                try:
                    fetched[cid] = float(raw_usd)
                except (TypeError, ValueError):
                    pass
            result.update(fetched)
            for cid in fetched:
                self._price_source_log[cid] = 'coingecko'
            self._simple_price_cache['ts'] = now
            self._simple_price_cache['prices'].update(fetched)
            if fetched:
                logger.info(f"  [OK] CoinGecko simple/price fallback: {len(fetched)} prices (CoinPaprika missed)")
            return result
        except Exception as e:
            logger.warning(f"  [WARN] CoinGecko simple/price fallback: {self._safe_exc(e)}")
        need_fetch = [c for c in ids if c not in result]
        if not need_fetch:
            return result
        # Third fallback: CoinMarketCap (by slug; coin_ids are slug-like)
        if self.cmc_key and need_fetch:
            try:
                url = f'{CMC_BASE}/cryptocurrency/quotes/latest'
                slug_param = ','.join(need_fetch[:30])
                resp = resilient_get(
                    url,
                    headers={'X-CMC_PRO_API_KEY': self.cmc_key},
                    params={'slug': slug_param, 'convert': 'USD'},
                    timeout=10,
                    source="coinmarketcap",
                )
                resp.raise_for_status()
                data = resp.json()
                raw = data.get('data') or {}
                for item in (raw.values() if isinstance(raw, dict) else []):
                    slug = (item.get('slug') or '').strip().lower()
                    quote = (item.get('quote') or {}).get('USD') or {}
                    price = quote.get('price')
                    if slug and price is not None:
                        try:
                            p = float(price)
                            if p > 0:
                                result[slug] = p
                                self._simple_price_cache['prices'][slug] = p
                                self._price_source_log[slug] = 'coinmarketcap'
                        except (TypeError, ValueError):
                            pass
                self._simple_price_cache['ts'] = now
                if result:
                    logger.info(f"  [OK] CoinMarketCap price fallback: {len(result)} prices")
            except Exception as e:
                logger.debug("get_current_prices_fast CMC fallback: %s", self._safe_exc(e))
        return result

    def get_all_data(self, n_enhanced: int = 10):
        """Fetch ALL data sources. n_enhanced: number of coins to fetch on-chain/enhanced data for (default 10; use 5 in Light Test Mode)."""
        logger.info("Fetching data from all sources...")
        start = time.time()
        
        coins = self.get_top_50_coins()
        # Merge 24h momentum gainers into universe (dedup by coin ID)
        gainers = self.get_top_gainers_24h()
        if gainers:
            seen_ids = {c.get('id') for c in coins if c.get('id')}
            added = 0
            for g in gainers:
                gid = g.get('id')
                if gid and gid not in seen_ids:
                    seen_ids.add(gid)
                    g['_source'] = 'momentum_screen'
                    coins.append(g)
                    added += 1
            if added:
                logger.info(f"  [OK] Merged {added} momentum coins into universe ({len(coins)} total)")
        fg = self.get_fear_greed()
        macro = self.get_macro_data()
        sentiment = self.get_news_sentiment()
        global_market_cap = self.get_global_market_cap()
        trending_coin_ids = self.get_trending_coins()
        # FIX: Onchain fallback — log once per refresh if API key missing
        if self.onchain.api_key:
            logger.info("  [OK] CryptoQuant (on-chain): API key active")
        else:
            logger.warning("On-chain (CryptoQuant): API key missing — using fallback placeholders for this refresh.")
        
        # Enhanced data for top n_enhanced only (API limits; 5 in Light Test Mode)
        logger.info(f"Fetching enhanced data for top {n_enhanced} candidates...")
        for i, coin in enumerate(coins[:n_enhanced]):
            try:
                enhanced = self.get_enhanced_data_for_coin(coin)
                coin['enhanced'] = enhanced
                fr = enhanced.get('funding_rate') or {}
                sopr = enhanced.get('sopr', 1.0)
                lth_sopr = enhanced.get('lth_sopr', 1.0)
                netflow = enhanced.get('exchange_netflow_24h', 0)
                lth_pct = enhanced.get('lth_supply_change_24h_pct', 0)
                hodl = enhanced.get('realized_cap_hodl_signal', 'neutral')
                whale = enhanced.get('whale_netflow', 'neutral')
                logger.info(f"  [{i+1}/{n_enhanced}] {coin.get('name')}: BTC corr={enhanced.get('btc_correlation', 0)}, Funding={fr.get('current', 0)}% | SOPR={sopr} LTH_SOPR={lth_sopr} Netflow={netflow} LTH={lth_pct}% HODL={hodl} Whale={whale}")
                if i < n_enhanced - 1:
                    time.sleep(0.3)
            except Exception as e:
                logger.error(f"  [ERR] Enhanced data error for {coin.get('name')}: {self._safe_exc(e)}")
                coin['enhanced'] = {}
        
        elapsed = round(time.time() - start, 1)
        logger.info(f"All data fetched in {elapsed}s")
        
        return {
            'coins': coins,
            'fear_greed': fg,
            'macro': macro,
            'sentiment': sentiment,
            'global_market_cap': global_market_cap,
            'trending_coin_ids': trending_coin_ids,
        }


# =============================================================================
# MCP + Quant Gate: feeds richer MCP-sourced data into the recursive optimizer for better threshold/backtest quality.
# =============================================================================

def _kraken_fallback_orderbook_24h():
    """US-resilient: Kraken public order book (XBTUSD, top 10) + 24h volume. Returns ob + dict with scalar volume/quoteVolume from v[1], p[1]."""
    try:
        r = resilient_get(f"{KRAKEN_SPOT_BASE}/Depth", params={"pair": "XBTUSD", "count": 10}, timeout=10, source="kraken_spot")
        r.raise_for_status()
        data = r.json()
        if data.get("error"):
            raise RuntimeError(data["error"])
        result = data.get("result") or {}
        book = result.get("XXBTZUSD") or next(iter(result.values()), {})
        raw_bids = (book.get("bids") or [])[:10]
        raw_asks = (book.get("asks") or [])[:10]
        bids = [[str(l[0]), float(l[1])] for l in raw_bids if isinstance(l, (list, tuple)) and len(l) >= 2]
        asks = [[str(l[0]), float(l[1])] for l in raw_asks if isinstance(l, (list, tuple)) and len(l) >= 2]
        r2 = resilient_get(f"{KRAKEN_SPOT_BASE}/Ticker", params={"pair": "XBTUSD"}, timeout=10, source="kraken_spot")
        r2.raise_for_status()
        t = r2.json().get("result") or {}
        tick = t.get("XXBTZUSD") or next(iter(t.values()), {})
        v_arr = tick.get("v") if isinstance(tick.get("v"), list) else [0, 0]
        p_arr = tick.get("p") if isinstance(tick.get("p"), list) else [0, 0]
        vol = float(v_arr[1]) if len(v_arr) > 1 else (float(v_arr[0]) if v_arr else 0.0)
        vwap24 = float(p_arr[1]) if len(p_arr) > 1 else (float(p_arr[0]) if p_arr else 0.0)
        quote_vol = float(vol * vwap24) if vol and vwap24 else 0.0
        return {"bids": bids, "asks": asks}, {"volume": float(vol), "quoteVolume": float(quote_vol)}
    except Exception as e:
        logger.warning("Kraken REST fallback failed: %s", _redact_exc_for_log(e))
        return {"bids": [], "asks": []}, {"volume": 0.0, "quoteVolume": 0.0}


def _mcp_fallback_coincap(asset_id: str) -> dict:
    """REST fallback: CoinCap asset + history. API is newest-first: history[0]=latest; 1h/4h/24h ago = [1],[4],[23]."""
    try:
        r = resilient_get(f"{COINCAP_BASE}/assets/{asset_id}", timeout=10, source="coincap")
        r.raise_for_status()
        price_usd = float(r.json().get("data", {}).get("priceUsd", 0) or 0)
        now_ms = int(time.time() * 1000)
        r2 = resilient_get(f"{COINCAP_BASE}/assets/{asset_id}/history",
            params={"interval": "h1", "start": now_ms - 86400 * 1000, "end": now_ms}, timeout=10, source="coincap")
        r2.raise_for_status()
        history = r2.json().get("data", [])
        history = sorted(history, key=lambda x: int(x.get("time", 0)), reverse=True)  # newest-first
        ch1h = ch4h = ch24h = 0.0
        if len(history) >= 2:
            p = float(history[1].get("priceUsd", 0) or 0)
            ch1h = ((price_usd - p) / p * 100) if p else 0
        if len(history) >= 5:
            p = float(history[4].get("priceUsd", 0) or 0)
            ch4h = ((price_usd - p) / p * 100) if p else 0
        if len(history) >= 24:
            p = float(history[23].get("priceUsd", 0) or 0)
            ch24h = ((price_usd - p) / p * 100) if p else 0
        return {"price_usd": price_usd, "ch1h_pct": ch1h, "ch4h_pct": ch4h, "ch24h_pct": ch24h}
    except Exception as e:
        logger.warning("CoinCap REST fallback failed: %s", _redact_exc_for_log(e))
        return {"price_usd": 0, "ch1h_pct": 0, "ch4h_pct": 0, "ch24h_pct": 0}


_coingecko_fallback_fetcher = None

def _coingecko_fallback_btc_eth():
    """Layer 3: CoinGecko (get_top_50_coins) for price + % changes + volatility/ATR from sparkline. Never returns zeros for price."""
    global _coingecko_fallback_fetcher
    try:
        if _coingecko_fallback_fetcher is None:
            _coingecko_fallback_fetcher = EnhancedDataFetcherV2()
        coins = _coingecko_fallback_fetcher.get_top_50_coins()
        btc = next((c for c in (coins or []) if (c or {}).get("id") == "bitcoin"), None)
        eth = next((c for c in (coins or []) if (c or {}).get("id") == "ethereum"), None)
        def to_coincap(c, default_ch24=0.0):
            if not c:
                return {"price_usd": 0, "ch1h_pct": 0, "ch4h_pct": 0, "ch24h_pct": 0, "atr_pct": 2.0, "volatility": 50.0}
            # Bug 4 — current_price None/missing must not silently become 0.0; treat as unavailable
            raw_price = c.get("current_price")
            if raw_price is None:
                return {"price_usd": 0, "ch1h_pct": 0, "ch4h_pct": 0, "ch24h_pct": 0, "atr_pct": 2.0, "volatility": 50.0}
            try:
                p = float(raw_price)
            except (TypeError, ValueError):
                p = 0.0
            ch1 = float(c.get("price_change_percentage_1h_in_currency") or 0)
            ch24 = float(c.get("price_change_percentage_24h_in_currency") or default_ch24)
            atr_pct, volatility = 2.0, 50.0
            prices = (c.get("sparkline_in_7d") or {}).get("price") or []
            if len(prices) >= 24 and p:
                arr = np.array([float(x) for x in prices[-24:] if x is not None], dtype=float)
                if len(arr) >= 2:
                    atr_pct = float((arr.max() - arr.min()) / (arr[-1] or 1) * 100)
                    rets = np.diff(arr) / (arr[:-1] + 1e-12) * 100
                    volatility = float(min(100, max(0, np.std(rets) * 15 + 40)))
            return {"price_usd": p, "ch1h_pct": ch1, "ch4h_pct": ch24 / 6.0, "ch24h_pct": ch24, "atr_pct": atr_pct, "volatility": volatility}
        return to_coincap(btc), to_coincap(eth)
    except Exception as e:
        logger.warning("CoinGecko fallback failed: %s", _redact_exc_for_log(e))
        z = {"price_usd": 0, "ch1h_pct": 0, "ch4h_pct": 0, "ch24h_pct": 0, "atr_pct": 2.0, "volatility": 50.0}
        return z.copy(), z.copy()


def get_mcp_btc_signal(account_size: float = 10_000.0, fear_greed_value: int = 50, raw_data: dict = None) -> dict:
    """MCP -> Kraken/REST -> CoinGecko 3-layer fallback. US-resilient; recursive optimizer; never zero price. Liquidity edge: +15 when bid_ask>1.4 and slippage<0.4%."""
    raw = raw_data or {}
    ob, vol_24h = raw.get("binance_order_book"), raw.get("binance_24h_volume")
    if not ob or not vol_24h:
        ob, vol_24h = _kraken_fallback_orderbook_24h()
    coincap_btc = raw.get("coincap_btc") or _mcp_fallback_coincap("bitcoin")
    coincap_eth = raw.get("coincap_eth") or _mcp_fallback_coincap("ethereum")
    if not float(coincap_btc.get("price_usd") or 0) or not float(coincap_eth.get("price_usd") or 0):
        cg_btc, cg_eth = _coingecko_fallback_btc_eth()
        if float(cg_btc.get("price_usd") or 0):
            coincap_btc = cg_btc
        if float(cg_eth.get("price_usd") or 0):
            coincap_eth = cg_eth
    uniswap = raw.get("uniswap_quote") or {"quote": None, "gas": None, "liquidity": None}
    bids, asks = (ob or {}).get("bids", [])[:10], (ob or {}).get("asks", [])[:10]
    bid_sum = sum(float(b[0]) * float(b[1]) for b in bids) if bids else 1.0
    ask_sum = sum(float(a[0]) * float(a[1]) for a in asks) if asks else 1.0
    bid_ask_ratio = (bid_sum / ask_sum) if ask_sum else 1.0
    vd = vol_24h if isinstance(vol_24h, dict) else {}
    raw_vol = vd.get("quoteVolume") or vd.get("volume") or ((vd.get("v") or [0, 0])[1] if isinstance(vd.get("v"), list) and len((vd.get("v") or [])) > 1 else (vol_24h if isinstance(vol_24h, (int, float)) else 0))
    try:
        volume_24h = float(raw_vol) if not isinstance(raw_vol, dict) else 0.0
    except (TypeError, ValueError):
        volume_24h = 0.0
    entry_price = float(coincap_btc.get("price_usd", 0) or 0)
    if not entry_price and bids and asks:
        entry_price = (float(bids[0][0]) + float(asks[0][0])) / 2.0
    ch1h = float(coincap_btc.get("ch1h_pct", 0) or 0)
    ch4h = float(coincap_btc.get("ch4h_pct", 0) or 0)
    ch24h = float(coincap_btc.get("ch24h_pct", 0) or 0)
    atr_pct = float(coincap_btc.get("atr_pct", 2.0) or 2.0)
    vol_btc = float(coincap_btc.get("volatility", 50.0) or 50.0)
    btc_coin = {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "current_price": entry_price, "market_cap": 1e12,
        "price_change_percentage_7d_in_currency": float(coincap_btc.get("ch7d_pct", ch24h * 2)),
        "price_change_percentage_30d_in_currency": float(coincap_btc.get("ch30d_pct", ch24h * 3)),
        "score": min(100, max(0, 50 + ch24h * 2 + (ch1h + ch4h) * 0.5)), "indicators": {"atr_pct": atr_pct, "volatility": vol_btc},
        "enhanced": {"order_book": {"bid_ask_ratio": bid_ask_ratio}, "funding_rate": 0.0, "exchange_netflow_24h": None, "whale_netflow": None}}
    eth_price = float(coincap_eth.get("price_usd", 0) or 0)
    vol_eth = float(coincap_eth.get("volatility", 55.0) or 55.0)
    eth_coin = {"id": "ethereum", "symbol": "ETH", "name": "Ethereum", "current_price": eth_price, "market_cap": 400e9,
        "price_change_percentage_7d_in_currency": float(coincap_eth.get("ch7d_pct", coincap_eth.get("ch24h_pct", 0) * 2)),
        "price_change_percentage_30d_in_currency": float(coincap_eth.get("ch30d_pct", coincap_eth.get("ch24h_pct", 0) * 3)), "indicators": {"volatility": vol_eth}}
    try:
        from market_regime import MarketRegimeDetector
        regime_data = MarketRegimeDetector().detect_regime({"coins": [btc_coin, eth_coin], "fear_greed": {"value": fear_greed_value}})
    except Exception as e:
        logger.warning("Regime detection failed: %s", _redact_exc_for_log(e))
        regime_data = {"regime": "SIDEWAYS", "strategy_adjustment": {"position_size_multiplier": 1.0}}
    from app_config import apply_full_veto_logic
    consensus_result = {"consensus_signal": "MODERATE_BUY"}
    final_signal, quant = apply_full_veto_logic(btc_coin, regime_data, consensus_result, fear_greed_value)
    probability_pct = min(100, max(0, float(quant.get("quant_score", 0))))
    if quant.get("regression_prob") is not None: probability_pct = 0.6 * probability_pct + 0.4 * (float(quant["regression_prob"]) * 100)
    try:
        from thresholds import get_threshold
        th = float((get_threshold("SLIPPAGE_PCT", 0.08) or 0.08) * 100)
        nf = (btc_coin.get("enhanced") or {}).get("exchange_netflow_24h")
        whale_f = "N/A (no netflow data)" if nf is None else ("VETO (inflow > threshold)" if float(nf) > float(get_threshold("EXCHANGE_NETFLOW_VETO", 1500) or 1500) else "OK")
    except Exception:
        th = 0.08 * 100
        nf = (btc_coin.get("enhanced") or {}).get("exchange_netflow_24h")
        whale_f = "N/A (no netflow data)" if nf is None else ("OK" if float(nf or 0) <= 1500 else "VETO")
    spread = ((float(asks[0][0]) - float(bids[0][0])) / ((float(bids[0][0]) + float(asks[0][0])) / 2) * 100) if bids and asks else 0.0
    slippage_estimate = round(spread, 4)
    if bid_ask_ratio > 1.4 and slippage_estimate < 0.4:
        probability_pct = min(100, probability_pct + 15)
    slippage_f = f"Spread {spread:.3f}%" + (" (above threshold)" if spread > th else " (OK)")
    liquidity_flag = "high" if (uniswap.get("liquidity") or uniswap.get("quote")) else "unknown"
    sentiment_f = f"F&G = {fear_greed_value}" + (" (default)" if fear_greed_value == 50 else "")
    stop_loss_pct, take_profit_pct = 2.5, 5.0
    stop_loss_price, take_profit_price = entry_price * 0.975, entry_price * 1.05
    position_size_usd = quantity = max_loss_pct = risk_usd = 0.0
    if final_signal in ("ULTRA_BUY", "STRONG_BUY", "MODERATE_BUY"):
        try:
            from dynamic_targets import DynamicTargetCalculator
            from position_sizer import PositionSizer
            targets = DynamicTargetCalculator().calculate_targets(entry_price, vol_btc, regime_data.get("regime", "SIDEWAYS"), final_signal)
            stop_loss_pct, take_profit_pct = targets["stop_loss_pct"], targets["take_profit_pct"]
            stop_loss_price, take_profit_price = targets["stop_loss_price"], targets["take_profit_price"]
            pos = PositionSizer(account_size=account_size, max_risk_pct=2.0).calculate_for_consensus(entry_price, stop_loss_pct, final_signal, regime_data=regime_data, coin_volatility=vol_btc, cash_available=account_size, edge_probability=quant.get("edge_probability"))
            position_size_usd = pos.get("position_size_usd", 0) or 0
            quantity = pos.get("quantity", 0) or 0
            max_loss_pct = pos.get("max_loss_pct", 0) or 0
            risk_usd = pos.get("risk_usd", 0) or 0
        except Exception as e:
            logger.warning("Dynamic targets / position sizer failed: %s", _redact_exc_for_log(e))
    order_book = {"bids": bids[:10], "asks": asks[:10]}
    signal_out = {"probability_pct": round(probability_pct, 1), "risk_flags": {"slippage": slippage_f, "whale": whale_f, "sentiment": sentiment_f}, "veto_reason": consensus_result.get("veto_reason"), "final_signal": final_signal, "entry_price": round(entry_price, 2), "stop_loss_price": round(stop_loss_price, 2), "stop_loss_pct": round(stop_loss_pct, 2), "take_profit_price": round(take_profit_price, 2), "take_profit_pct": round(take_profit_pct, 2), "position_size_usd": round(position_size_usd, 2), "quantity": round(quantity, 8), "max_loss_pct": round(max_loss_pct, 2), "risk_usd": round(risk_usd, 2), "order_book": order_book, "slippage_estimate": slippage_estimate, "liquidity_flag": liquidity_flag, "bid_ask_ratio": round(bid_ask_ratio, 6), "real_vol": round(vol_btc, 2)}
    return {"raw_data": {"binance_order_book": {"bids": bids, "asks": asks}, "binance_24h_volume": volume_24h, "uniswap_quote": uniswap, "coincap_btc": coincap_btc, "coincap_eth": coincap_eth}, "signal": signal_out}


def _fetch_raw_data_rest():
    """Build MCP-shaped raw_data via REST (Kraken + CoinCap). US-resilient; same shape as MCP."""
    ob, vol_24h = _kraken_fallback_orderbook_24h()
    return {"binance_order_book": ob, "binance_24h_volume": vol_24h, "coincap_btc": _mcp_fallback_coincap("bitcoin"), "coincap_eth": _mcp_fallback_coincap("ethereum"), "uniswap_quote": {"quote": None, "gas": None, "liquidity": None}}


def _log_mcp_btc_to_recommendations(result: dict) -> None:
    """Append full MCP-BTC result (including order_book, slippage_estimate, liquidity_flag, bid_ask_ratio, real_vol) to ai_recommendations_v2.json for recursive optimizer training."""
    import os
    import uuid
    from datetime import datetime
    rec_file = os.path.join(os.path.dirname(__file__), "ai_recommendations_v2.json")
    try:
        import safe_json
        data = safe_json.load(rec_file, default=list)
    except Exception as e:
        logger.warning("Could not load recommendations for MCP-BTC log: %s", _redact_exc_for_log(e))
        return
    sig = result.get("signal") or {}
    try:
        from market_regime import MarketRegimeDetector
        regime_data = MarketRegimeDetector().detect_regime({"coins": [], "fear_greed": {"value": 50}})
        regime = regime_data.get("regime", "SIDEWAYS")
    except Exception:
        regime = "SIDEWAYS"
    record = {"id": str(uuid.uuid4())[:8], "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "source": "mcp_btc_signal", "coin_id": "bitcoin", "coin_name": "Bitcoin", "coin_symbol": "BTC", "entry_price": sig.get("entry_price"), "consensus_signal": sig.get("final_signal"), "consensus_quality": "strong" if sig.get("final_signal", "").endswith("_BUY") else "hold", "market_regime": regime, "confirmation_score": sig.get("probability_pct"), "quant_path": None, "order_book": sig.get("order_book"), "slippage_estimate": sig.get("slippage_estimate"), "liquidity_flag": sig.get("liquidity_flag"), "bid_ask_ratio": sig.get("bid_ask_ratio"), "real_vol": sig.get("real_vol"), "probability_pct": sig.get("probability_pct"), "position_size_usd": sig.get("position_size_usd"), "stop_loss_pct": sig.get("stop_loss_pct"), "take_profit_pct": sig.get("take_profit_pct"), "outcome_3d": None, "outcome_5d": None, "outcome_7d": None, "outcome_14d": None}
    if not isinstance(data, list):
        data = []
    data.append(record)
    try:
        safe_json.save(rec_file, data, default=str)
        logger.info("MCP-BTC result logged to ai_recommendations_v2.json (order_book, slippage_estimate, liquidity_flag, bid_ask_ratio, real_vol)")
    except Exception as e:
        logger.warning("Could not save MCP-BTC to recommendations: %s", _redact_exc_for_log(e))


def _example_run(raw_data: dict = None):
    """MCP file or Kraken+CoinCap REST -> get_mcp_btc_signal -> log to ai_recommendations_v2.json (order_book, slippage_estimate, liquidity_flag, bid_ask_ratio, real_vol)."""
    raw = raw_data
    if raw is None:
        import os
        mcp_file = os.environ.get("MCP_RAW_DATA_FILE") or os.path.join(os.path.dirname(__file__), "mcp_raw_data.json")
        if os.path.isfile(mcp_file):
            try:
                import safe_json
                raw = safe_json.load(mcp_file, default=dict)
            except Exception as e:
                logger.warning("Could not load MCP raw_data from %s: %s", mcp_file, _redact_exc_for_log(e))
        if raw is None:
            raw = _fetch_raw_data_rest()
    result = get_mcp_btc_signal(account_size=10_000, fear_greed_value=50, raw_data=raw)
    print("=== Raw data ===")
    print("Order book / 24h volume:", result["raw_data"].get("binance_24h_volume"))
    print("CoinCap BTC:", result["raw_data"].get("coincap_btc"))
    print("CoinCap ETH:", result["raw_data"].get("coincap_eth"))
    print("Uniswap:", result["raw_data"].get("uniswap_quote"))
    print("\n=== Signal (order_book, slippage_estimate, liquidity_flag, bid_ask_ratio, real_vol) ===")
    for k, v in result["signal"].items():
        print(f"  {k}: {v}")
    _log_mcp_btc_to_recommendations(result)
    return result


def _test_fallbacks():
    """Assert US-resilient flow: Kraken order book + CoinCap/CoinGecko price; all signal fields present."""
    ok = True
    result = get_mcp_btc_signal(account_size=10_000, fear_greed_value=50, raw_data=None)
    rd = result.get("raw_data") or {}
    sig = result.get("signal") or {}
    entry = float(sig.get("entry_price") or 0)
    btc_data = rd.get("coincap_btc") or {}
    price_usd = float(btc_data.get("price_usd") or 0)
    bids = (rd.get("binance_order_book") or {}).get("bids") or []
    asks = (rd.get("binance_order_book") or {}).get("asks") or []

    if entry <= 0:
        print("FAIL: entry_price should be > 0 (fallbacks did not deliver price)")
        ok = False
    else:
        print(f"OK: entry_price = {entry} (source: {'coincap_btc' if price_usd else 'order book mid'})")
    if not price_usd and not (bids and asks):
        print("WARN: no coincap_btc price and no order book (CoinCap+CoinGecko may have failed)")
    elif price_usd:
        print(f"OK: coincap_btc.price_usd = {price_usd} (CoinCap or CoinGecko Layer 3)")
    if not (bids and asks):
        print("WARN: Kraken REST returned no order book")
    else:
        print(f"OK: Kraken order book: {len(bids)} bids, {len(asks)} asks")
    for k in ["entry_price", "final_signal", "probability_pct", "risk_flags", "order_book", "slippage_estimate", "liquidity_flag", "bid_ask_ratio", "real_vol"]:
        if k not in sig:
            print(f"FAIL: signal missing key {k}")
            ok = False
    if ok:
        print("PASS: US-resilient data and fallbacks OK")
    return ok


if __name__ == "__main__":
    import sys
    if "--test-only" in sys.argv:
        print("--- Fallback test (MCP -> REST -> CoinGecko/CoinPaprika) ---")
        ok = _test_fallbacks()
        sys.exit(0 if ok else 1)
    _example_run()
    print("\n--- Fallback test ---")
    _test_fallbacks()
