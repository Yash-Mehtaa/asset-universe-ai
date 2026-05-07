"""Trend following using Finnhub quote data only.

Buys the top N assets with positive daily momentum.
Position sized proportionally to trend strength.
Iterative cap enforcement ensures no position exceeds max weight
even after normalization.
"""
from __future__ import annotations

import pandas as pd

from app.strategies.base import Strategy, TradeSignal


class TrendFollowingStrategy(Strategy):
    template_name = "trend_following"
    default_params = {
        "top_n": 8,
        "min_trend_pct": 0.5,
        "max_position_weight": 0.20,
        "rebalance_threshold": 0.02,
    }

    def generate_signals(
        self,
        history: dict[str, pd.DataFrame],
        current_holdings: dict[str, float],
    ) -> list[TradeSignal]:
        trending: list[tuple[str, str, float]] = []
        asset_types: dict[str, str] = {}

        for key, df in history.items():
            if df is None or df.empty:
                continue
            symbol, asset_type = key.split("|")
            asset_types[symbol] = asset_type

            if "dp" in df.columns:
                dp = float(df["dp"].iloc[-1])
            elif len(df) >= 2:
                prev = df["close"].iloc[-2]
                curr = df["close"].iloc[-1]
                dp = ((curr - prev) / prev * 100) if prev > 0 else 0.0
            else:
                continue

            if dp >= self.params["min_trend_pct"]:
                trending.append((symbol, asset_type, dp))

        trending.sort(key=lambda x: x[2], reverse=True)
        top = trending[:self.params["top_n"]]
        top_syms = {sym for sym, _, _ in top}

        signals: list[TradeSignal] = []

        for sym, current_w in current_holdings.items():
            if sym not in top_syms and current_w > 0.001:
                signals.append(TradeSignal(
                    symbol=sym,
                    asset_type=asset_types.get(sym, "stock"),
                    side="sell",
                    target_weight=0.0,
                    rationale=f"{sym} no longer in top trending assets — exiting position."
                ))

        if not top:
            return signals

        raw = {sym: abs(dp) for sym, _, dp in top}
        target_weights = self._normalize_with_cap(raw, self.params["max_position_weight"])
        rank = {sym: i + 1 for i, (sym, _, _) in enumerate(top)}

        for symbol, asset_type, dp in top:
            current_w = current_holdings.get(symbol, 0.0)
            target_w = target_weights[symbol]
            if abs(target_w - current_w) < self.params["rebalance_threshold"]:
                continue
            side = "buy" if target_w > current_w else "sell"
            signals.append(TradeSignal(
                symbol=symbol,
                asset_type=asset_type,
                side=side,
                target_weight=target_w,
                rationale=f"#{rank[symbol]} trend pick: {symbol} up {dp:+.2f}% today. Target {target_w*100:.1f}% of portfolio."
            ))

        return signals