"""Market data client. Wraps Finnhub (stocks/ETFs), CoinGecko (crypto),
and Alpha Vantage (historical bars). Mirrors the APIs your Next.js frontend uses."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
import pandas as pd

from app.config import config


class MarketDataError(Exception):
    pass


class MarketData:
    """Single entry point for all price data."""

    def __init__(self):
        self._client = httpx.Client(timeout=15.0)
        # Naive in-memory cache to avoid hammering free-tier APIs
        self._price_cache: dict[str, tuple[float, float]] = {}  # symbol -> (price, ts)
        self._cache_ttl_sec = 60

    # ------------------------------------------------------------------
    # Current prices
    # ------------------------------------------------------------------
    def get_price(self, symbol: str, asset_type: str) -> float:
        """Latest price for any supported asset."""
        cached = self._price_cache.get(f"{asset_type}:{symbol}")
        if cached and (time.time() - cached[1]) < self._cache_ttl_sec:
            return cached[0]

        if asset_type in ("stock", "etf", "commodity"):
            price = self._finnhub_quote(symbol)
        elif asset_type == "crypto":
            price = self._coingecko_price(symbol)
        else:
            raise MarketDataError(f"Unsupported asset_type: {asset_type}")

        self._price_cache[f"{asset_type}:{symbol}"] = (price, time.time())
        return price

    def _finnhub_quote(self, symbol: str) -> float:
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
        return float(data["c"])

    def _coingecko_price(self, symbol: str) -> float:
        # CoinGecko uses ids like "bitcoin", "ethereum"
        r = self._client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": symbol, "vs_currencies": "usd"},
        )
        r.raise_for_status()
        data = r.json()
        if symbol not in data:
            raise MarketDataError(f"No CoinGecko data for {symbol}")
        return float(data[symbol]["usd"])

    # ------------------------------------------------------------------
    # Historical bars (used by strategies)
    # ------------------------------------------------------------------
    def get_history(
        self,
        symbol: str,
        asset_type: str,
        days: int = 90,
    ) -> pd.DataFrame:
        """Daily OHLCV bars. Returns a DataFrame indexed by date with
        columns: open, high, low, close, volume."""
        if asset_type in ("stock", "etf", "commodity"):
            return self._alpha_vantage_daily(symbol, days)
        elif asset_type == "crypto":
            return self._coingecko_history(symbol, days)
        else:
            raise MarketDataError(f"Unsupported asset_type: {asset_type}")

    def _alpha_vantage_daily(self, symbol: str, days: int) -> pd.DataFrame:
        if not config.ALPHA_VANTAGE_API_KEY:
            raise MarketDataError("ALPHA_VANTAGE_API_KEY not set")
        r = self._client.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": symbol,
                "outputsize": "compact" if days <= 100 else "full",
                "apikey": config.ALPHA_VANTAGE_API_KEY,
            },
        )
        r.raise_for_status()
        data = r.json()
        ts = data.get("Time Series (Daily)")
        if not ts:
            raise MarketDataError(f"No Alpha Vantage data for {symbol}: {data}")
        rows = []
        for date_str, bar in ts.items():
            rows.append({
                "date": pd.to_datetime(date_str),
                "open": float(bar["1. open"]),
                "high": float(bar["2. high"]),
                "low": float(bar["3. low"]),
                "close": float(bar["4. close"]),
                "volume": float(bar["5. volume"]),
            })
        df = pd.DataFrame(rows).sort_values("date").set_index("date")
        cutoff = datetime.utcnow() - timedelta(days=days)
        return df[df.index >= cutoff]

    def _coingecko_history(self, symbol: str, days: int) -> pd.DataFrame:
        r = self._client.get(
            f"https://api.coingecko.com/api/v3/coins/{symbol}/market_chart",
            params={"vs_currency": "usd", "days": days, "interval": "daily"},
        )
        r.raise_for_status()
        data = r.json()
        prices = data.get("prices", [])
        if not prices:
            raise MarketDataError(f"No CoinGecko history for {symbol}")
        rows = [
            {"date": pd.to_datetime(ms, unit="ms"), "close": float(price)}
            for ms, price in prices
        ]
        df = pd.DataFrame(rows).set_index("date")
        # Daily-resample so multiple intra-day points collapse cleanly.
        df = df.resample("D").last().dropna()
        # Crypto endpoint doesn't give OHLC on this endpoint — synthesize
        df["open"] = df["close"].shift(1).fillna(df["close"])
        df["high"] = df[["open", "close"]].max(axis=1)
        df["low"] = df[["open", "close"]].min(axis=1)
        df["volume"] = 0.0
        return df[["open", "high", "low", "close", "volume"]]


# Singleton
market_data = MarketData()
