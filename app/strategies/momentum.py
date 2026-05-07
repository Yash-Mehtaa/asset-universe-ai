"""Momentum strategy using Finnhub quote data only.

Instead of historical bars, we use:
- dp: daily % change (momentum signal)
- c: current price
- pc: previous close

We rank assets by their daily % change and hold the top N.
Simple, legally clean, no historical API needed.
"""
from __future__ import annotations

import pandas as pd

from app.strategies.base import Strategy, TradeSignal


class MomentumStrategy(Strategy):
    template_name = "momentum"
    default_params = {
        "top_n": 3,
        "min_change_pct": 0.0,       # Only buy if daily change > this
        "rebalance_threshold": 0.05,
        "max_position_weight": 0.30,
    }

    def generate_signals(
        self,
        history: dict[str, pd.DataFrame],
        current_holdings: dict[str, float],
    ) -> list[TradeSignal]:
        # Extract daily % change from the last row of each symbol's data
        # history may be empty if we switched to quote-only mode —
        # in that case we look for a 'dp' column injected by the agent runner
        returns: list[tuple[str, str, float]] = []

        for key, df in history.items():
            if df is None or df.empty:
                continue
            symbol, asset_type = key.split("|")

            # Use 'dp' column if available (daily % change from Finnhub quote)
            # Otherwise fall back to last 2 rows of close price
            if "dp" in df.columns:
                dp = float(df["dp"].iloc[-1])
            elif len(df) >= 2:
                prev = df["close"].iloc[-2]
                curr = df["close"].iloc[-1]
                dp = ((curr - prev) / prev * 100) if prev > 0 else 0.0
            else:
                continue

            returns.append((symbol, asset_type, dp))

        if not returns:
            return []

        returns.sort(key=lambda x: x[2], reverse=True)
        top = [r for r in returns[:self.params["top_n"]]
               if r[2] >= self.params["min_change_pct"]]

        if not top:
            return [
                TradeSignal(
                    symbol=sym, asset_type="stock", side="sell",
                    target_weight=0.0,
                    rationale="No positive momentum — moving to cash."
                )
                for sym in current_holdings
            ]

        total_dp = sum(abs(dp) for _, _, dp in top) or 1.0
        target_set = {
            sym: min((abs(dp) / total_dp), self.params["max_position_weight"])
            for sym, _, dp in top
        }

        signals: list[TradeSignal] = []

        for sym, current_w in current_holdings.items():
            if sym not in target_set and current_w > 0.001:
                signals.append(TradeSignal(
                    symbol=sym, asset_type="stock", side="sell",
                    target_weight=0.0,
                    rationale="Dropped from momentum top-N."
                ))

        for symbol, asset_type, dp in top:
            current_w = current_holdings.get(symbol, 0.0)
            if abs(target_set[symbol] - current_w) < self.params["rebalance_threshold"]:
                continue
            side = "buy" if target_set[symbol] > current_w else "sell"
            signals.append(TradeSignal(
                symbol=symbol, asset_type=asset_type, side=side,
                target_weight=target_set[symbol],
                rationale=f"{symbol} momentum {dp:+.2f}% today — allocated {target_set[symbol]*100:.1f}% of portfolio (${target_set[symbol] * 100000:,.0f} target)."
            ))

        return signals
