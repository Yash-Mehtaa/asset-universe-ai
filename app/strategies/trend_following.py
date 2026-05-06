"""Trend following via moving-average crossover.

Idea: hold an asset when its short MA > long MA (uptrend), exit when it crosses
below. Used by managed-futures funds for decades; the original Donchian and
Turtle systems are variants. Solid mid-horizon default.
"""
from __future__ import annotations

import pandas as pd

from app.strategies.base import Strategy, TradeSignal


class TrendFollowingStrategy(Strategy):
    template_name = "trend_following"
    default_params = {
        "fast_ma": 20,             # Short MA window (days)
        "slow_ma": 50,             # Long MA window (days)
        "atr_period": 14,          # Volatility window
        "vol_target_pct": 0.02,    # Target 2% daily portfolio vol per position
        "max_position_weight": 0.25,
        "exit_threshold": 0.0,     # MA delta below which we exit
    }

    def generate_signals(
        self,
        history: dict[str, pd.DataFrame],
        current_holdings: dict[str, float],
    ) -> list[TradeSignal]:
        signals: list[TradeSignal] = []
        in_trend: dict[str, tuple[str, float]] = {}

        for key, df in history.items():
            if df is None or len(df) < self.params["slow_ma"] + 5:
                continue
            symbol, asset_type = key.split("|")
            close = df["close"]
            fast = close.rolling(self.params["fast_ma"]).mean().iloc[-1]
            slow = close.rolling(self.params["slow_ma"]).mean().iloc[-1]
            if pd.isna(fast) or pd.isna(slow):
                continue

            ma_delta = (fast - slow) / slow

            # Volatility-target sizing using ATR proxy (stdev of returns)
            ret = close.pct_change().tail(self.params["atr_period"])
            vol = ret.std() if len(ret) > 1 else 0.02
            if vol == 0 or pd.isna(vol):
                vol = 0.02
            target_w = min(
                self.params["vol_target_pct"] / vol,
                self.params["max_position_weight"],
            )

            if ma_delta > self.params["exit_threshold"]:
                in_trend[symbol] = (asset_type, target_w)

        # Sell anything not in trend
        for sym, current_w in current_holdings.items():
            if sym not in in_trend and current_w > 0.001:
                signals.append(TradeSignal(
                    symbol=sym, asset_type="stock", side="sell",
                    target_weight=0.0,
                    rationale="Trend has reversed (fast MA below slow MA)."
                ))

        # Buy/adjust trending assets
        for symbol, (asset_type, target_w) in in_trend.items():
            current_w = current_holdings.get(symbol, 0.0)
            if abs(target_w - current_w) < 0.03:
                continue
            side = "buy" if target_w > current_w else "sell"
            signals.append(TradeSignal(
                symbol=symbol, asset_type=asset_type, side=side,
                target_weight=target_w,
                rationale=f"In uptrend: {self.params['fast_ma']}d MA above {self.params['slow_ma']}d MA. Vol-targeted size."
            ))

        return signals
