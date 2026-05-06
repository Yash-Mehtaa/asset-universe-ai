"""Cross-sectional momentum.

Idea: rank assets by their recent return and hold the top N. This is one of
the most-studied anomalies in finance (Jegadeesh & Titman 1993; Asness, Moskowitz,
Pedersen 2013). It's a textbook short-horizon strategy and a defensible default.
"""
from __future__ import annotations

import pandas as pd

from app.strategies.base import Strategy, TradeSignal


class MomentumStrategy(Strategy):
    template_name = "momentum"
    default_params = {
        "lookback_days": 20,       # How far back to measure return
        "top_n": 3,                # How many top performers to hold
        "rebalance_threshold": 0.05,  # Only swap if delta > 5%
        "min_return_threshold": 0.0,  # Don't buy if everything is negative
        "max_position_weight": 0.30,
    }

    def generate_signals(
        self,
        history: dict[str, pd.DataFrame],
        current_holdings: dict[str, float],
    ) -> list[TradeSignal]:
        # Compute lookback return per symbol
        returns: list[tuple[str, str, float]] = []  # (symbol, asset_type, return)
        for key, df in history.items():
            if df is None or len(df) < self.params["lookback_days"]:
                continue
            symbol, asset_type = key.split("|")
            window = df.tail(self.params["lookback_days"])
            if len(window) < 2:
                continue
            ret = (window["close"].iloc[-1] / window["close"].iloc[0]) - 1
            returns.append((symbol, asset_type, ret))

        if not returns:
            return []

        returns.sort(key=lambda x: x[2], reverse=True)
        top = returns[: self.params["top_n"]]

        # Filter out negative momentum if threshold says so
        top = [t for t in top if t[2] >= self.params["min_return_threshold"]]

        if not top:
            # All negative — go to cash by closing all positions
            return [
                TradeSignal(
                    symbol=sym, asset_type="stock", side="sell",
                    target_weight=0.0,
                    rationale="No positive momentum candidates; flatten."
                )
                for sym in current_holdings
            ]

        # Equal-weight the top N, capped at max_position_weight
        target_w = min(1.0 / len(top), self.params["max_position_weight"])
        target_set = {symbol: target_w for symbol, _, _ in top}

        signals: list[TradeSignal] = []

        # Sell anything not in target set
        for sym, current_w in current_holdings.items():
            if sym not in target_set and current_w > 0.001:
                signals.append(TradeSignal(
                    symbol=sym, asset_type="stock", side="sell",
                    target_weight=0.0,
                    rationale="Dropped from momentum top-N."
                ))

        # Buy/hold target set
        for symbol, asset_type, ret in top:
            current_w = current_holdings.get(symbol, 0.0)
            delta = abs(target_set[symbol] - current_w)
            if delta < self.params["rebalance_threshold"]:
                continue
            side = "buy" if target_set[symbol] > current_w else "sell"
            signals.append(TradeSignal(
                symbol=symbol, asset_type=asset_type, side=side,
                target_weight=target_set[symbol],
                rationale=f"Top-{self.params['top_n']} momentum: {ret*100:.1f}% over {self.params['lookback_days']}d."
            ))

        return signals
