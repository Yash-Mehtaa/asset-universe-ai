"""Trend following using Finnhub quote data only.

Instead of MA crossover on historical bars, we use:
- dp: daily % change as a short-term trend signal
- Assets with positive daily change = uptrend
- Weight by inverse of volatility proxy (abs daily change)

Simple, fast, no historical API needed.
"""
from __future__ import annotations

import pandas as pd

from app.strategies.base import Strategy, TradeSignal


class TrendFollowingStrategy(Strategy):
    template_name = "trend_following"
    default_params = {
        "min_trend_pct": 0.1,        # Min daily % change to consider "uptrend"
        "max_position_weight": 0.25,
        "rebalance_threshold": 0.03,
        "vol_target_pct": 0.02,      # Target 2% weight per unit of daily move
    }

    def generate_signals(
        self,
        history: dict[str, pd.DataFrame],
        current_holdings: dict[str, float],
    ) -> list[TradeSignal]:
        in_trend: dict[str, tuple[str, float]] = {}

        for key, df in history.items():
            if df is None or df.empty:
                continue
            symbol, asset_type = key.split("|")

            if "dp" in df.columns:
                dp = float(df["dp"].iloc[-1])
            elif len(df) >= 2:
                prev = df["close"].iloc[-2]
                curr = df["close"].iloc[-1]
                dp = ((curr - prev) / prev * 100) if prev > 0 else 0.0
            else:
                continue

            if dp >= self.params["min_trend_pct"]:
                # Vol-target sizing: higher daily move = smaller position
                vol_proxy = max(abs(dp) / 100, 0.001)
                target_w = min(
                    self.params["vol_target_pct"] / vol_proxy,
                    self.params["max_position_weight"],
                )
                in_trend[symbol] = (asset_type, target_w)

        signals: list[TradeSignal] = []

        # Sell anything not in trend
        for sym, current_w in current_holdings.items():
            if sym not in in_trend and current_w > 0.001:
                signals.append(TradeSignal(
                    symbol=sym, asset_type="stock", side="sell",
                    target_weight=0.0,
                    rationale="Daily change below trend threshold — exiting."
                ))

        # Buy/adjust trending assets
        for symbol, (asset_type, target_w) in in_trend.items():
            current_w = current_holdings.get(symbol, 0.0)
            if abs(target_w - current_w) < self.params["rebalance_threshold"]:
                continue
            side = "buy" if target_w > current_w else "sell"
            signals.append(TradeSignal(
                symbol=symbol, asset_type=asset_type, side=side,
                target_weight=target_w,
                rationale=f"Positive daily trend — vol-targeted size {target_w*100:.1f}%."
            ))

        return signals
