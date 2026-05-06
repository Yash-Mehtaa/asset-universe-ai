"""Market data client.
- Current prices + daily change: Finnhub quote endpoint (free tier, legally clean)
- Crypto prices: CoinGecko (free, no restrictions)

No historical bar APIs used — strategies work from quote data only.
"""
from __future__ import annotations

import time

import httpx
import pandas as pd

from app.config import config


class MarketDataError(Exception):
    pass


class MarketData:

    def __init__(self):
        self._client = httpx.Client(timeout=15.0)
        self._price_cache: dict[str, tuple[float, float]] = {}
        self._quote_cache: dict[str, tuple[dict, float]] = {}
        self._cache_ttl_sec = 60

    def get_price(self, symbol: str, asset_type: str) -> float:
        cached = self._price_cache.get(f"{asset_type}:{symbol}")
        if cached and (time.time() - cached[1]) < self._cache_ttl_sec:
            return cached[0]
        if asset_type in ("stock", "etf", "commodity"):
            price = self._finnhub_quote(symbol)["c"]
        elif asset_type == "crypto":
            price = self._coingecko_price(symbol)
        else:
            raise MarketDataError(f"Unsupported asset_type: {asset_type}")
        self._price_cache[f"{asset_type}:{symbol}"] = (price, time.time())
        return price

    def get_quote(self, symbol: str) -> dict:
        """Returns full Finnhub quote: c, pc, dp, h, l, o, t"""
        cached = self._quote_cache.get(symbol)
        if cached and (time.time() - cached[1]) < self._cache_ttl_sec:
            return cached[0]
        q = self._finnhub_quote(symbol)
        self._quote_cache[symbol] = (q, time.time())
        return q

    def _finnhub_quote(self, symbol: str) -> dict:
        if not config.FINNHUB_API_KEY:
            raise MarketDataError("FINNHUB_API_KEY not set")
        r = self._client.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": config.FINNHUB_API_KEY},
        )
        r.raise_for_status()
        data = r.json()
        if "c" not in data or data["c"] == 0:
            raise MarketDataError(f"No price for {symbol}: {data}")
        return {
            "c": float(data["c"]),       # current price
            "pc": float(data.get("pc", data["c"])),  # previous close
            "dp": float(data.get("dp", 0.0)),  # daily % change
            "h": float(data.get("h", data["c"])),    # day high
            "l": float(data.get("l", data["c"])),    # day low
            "o": float(data.get("o", data["c"])),    # open
        }

    def _coingecko_price(self, symbol: str) -> float:
        r = self._client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": symbol, "vs_currencies": "usd", "include_24hr_change": "true"},
        )
        r.raise_for_status()
        data = r.json()
        if symbol not in data:
            raise MarketDataError(f"No CoinGecko data for {symbol}")
        return float(data[symbol]["usd"])

    def get_history(self, symbol: str, asset_type: str, days: int = 90) -> pd.DataFrame:
        """Returns a single-row DataFrame with today's quote data.
        Strategies use the 'dp' column (daily % change) as their signal.
        No historical bar API needed.
        """
        if asset_type in ("stock", "etf", "commodity"):
            try:
                q = self.get_quote(symbol)
                df = pd.DataFrame([{
                    "date": pd.Timestamp.now(),
                    "open": q["o"],
                    "high": q["h"],
                    "low": q["l"],
                    "close": q["c"],
                    "volume": 0.0,
                    "dp": q["dp"],
                    "pc": q["pc"],
                }]).set_index("date")
                return df
            except Exception as e:
                raise MarketDataError(f"Quote failed for {symbol}: {e}")
        elif asset_type == "crypto":
            return self._coingecko_quote_as_df(symbol)
        else:
            raise MarketDataError(f"Unsupported asset_type: {asset_type}")

    def _coingecko_quote_as_df(self, symbol: str) -> pd.DataFrame:
        r = self._client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": symbol, "vs_currencies": "usd", "include_24hr_change": "true"},
        )
        r.raise_for_status()
        data = r.json()
        if symbol not in data:
            raise MarketDataError(f"No CoinGecko data for {symbol}")
        price = float(data[symbol]["usd"])
        dp = float(data[symbol].get("usd_24h_change", 0.0))
        df = pd.DataFrame([{
            "date": pd.Timestamp.now(),
            "open": price, "high": price, "low": price,
            "close": price, "volume": 0.0,
            "dp": dp, "pc": price,
        }]).set_index("date")
        return df


# Singleton
market_data = MarketData()
