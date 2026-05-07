"""Momentum strategy using Finnhub quote data only.

Ranks assets by daily % change, allocates proportionally to momentum
strength. Iterative cap enforcement ensures no position exceeds max weight
even after normalization.
"""
from __future__ import annotations

import pandas as pd

from app.strategies.base import Strategy, TradeSignal


class MomentumStrategy(Strategy):
    template_name = "momentum"
    default_params = {
        "top_n": 5,
        "min_change_pct": 1.0,
        "rebalance_threshold": 0.02,
        "max_position_weight": 0.30,
    }

    def generate_signals(
        self,
        history: dict[str, pd.DataFrame],
        current_holdings: dict[str, float],
    ) -> list[TradeSignal]:
        returns: list[tuple[str, str, float]] = []
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

            returns.append((symbol, asset_type, dp))

        if not returns:
            return []

        returns.sort(key=lambda x: x[2], reverse=True)
        top = [r for r in returns[:self.params["top_n"]]
               if r[2] >= self.params["min_change_pct"]]

        if not top:
            return [
                TradeSignal(
                    symbol=sym,
                    asset_type=asset_types.get(sym, "stock"),
                    side="sell",
                    target_weight=0.0,
                    rationale="No assets meeting minimum momentum threshold — moving to cash."
                )
                for sym in current_holdings
            ]

        raw = {sym: abs(dp) for sym, _, dp in top}
        target_set = self._normalize_with_cap(raw, self.params["max_position_weight"])

        signals: list[TradeSignal] = []
        top_syms = {sym for sym, _, _ in top}
        rank = {sym: i + 1 for i, (sym, _, _) in enumerate(top)}

        for sym, current_w in current_holdings.items():
            if sym not in top_syms and current_w > 0.001:
                signals.append(TradeSignal(
                    symbol=sym,
                    asset_type=asset_types.get(sym, "stock"),
                    side="sell",
                    target_weight=0.0,
                    rationale=f"{sym} dropped from momentum top-{self.params['top_n']} — exiting position."
                ))

        for symbol, asset_type, dp in top:
            current_w = current_holdings.get(symbol, 0.0)
            target_w = target_set[symbol]
            diff = target_w - current_w
            if abs(diff) < self.params["rebalance_threshold"]:
                continue
            side = "buy" if diff > 0 else "sell"
            signals.append(TradeSignal(
                symbol=symbol,
                asset_type=asset_type,
                side=side,
                target_weight=target_w,
                rationale=f"#{rank[symbol]} momentum pick: {symbol} up {dp:+.2f}% today. Target {target_w*100:.1f}% of portfolio."
            ))

        return signals