"""Risk parity using Finnhub quote data only.

Instead of computing volatility from historical bars, we use
the absolute daily % change as a volatility proxy.
Inverse-volatility weights: lower daily move = larger allocation.

Assets with zero or missing daily change get excluded.
"""
from __future__ import annotations

import pandas as pd

from app.strategies.base import Strategy, TradeSignal


class RiskParityStrategy(Strategy):
    template_name = "risk_parity"
    default_params = {
        "rebalance_threshold": 0.05,
        "min_position_weight": 0.03,
        "max_position_weight": 0.30,
        "asset_class_caps": {
            "stock": 0.50,
            "etf": 0.60,
            "crypto": 0.20,
            "commodity": 0.30,
        },
    }

    def generate_signals(
        self,
        history: dict[str, pd.DataFrame],
        current_holdings: dict[str, float],
    ) -> list[TradeSignal]:
        vol_proxies: dict[str, tuple[str, float]] = {}

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

            vol = max(abs(dp) / 100, 0.001)
            vol_proxies[symbol] = (asset_type, vol)

        if not vol_proxies:
            return []

        # Inverse-vol raw weights
        inv_vols = {sym: 1.0 / v for sym, (_, v) in vol_proxies.items()}
        total = sum(inv_vols.values())
        raw_weights = {sym: w / total for sym, w in inv_vols.items()}

        # Apply per-position bounds
        weights = {
            sym: min(max(w, self.params["min_position_weight"]),
                     self.params["max_position_weight"])
            for sym, w in raw_weights.items()
        }

        # Apply asset-class caps
        by_class: dict[str, float] = {}
        for sym, w in weights.items():
            ac = vol_proxies[sym][0]
            by_class[ac] = by_class.get(ac, 0) + w

        for ac, total_w in by_class.items():
            cap = self.params["asset_class_caps"].get(ac, 1.0)
            if total_w > cap:
                scale = cap / total_w
                for sym in list(weights.keys()):
                    if vol_proxies[sym][0] == ac:
                        weights[sym] *= scale

        # Renormalize
        s = sum(weights.values())
        if s > 0:
            weights = {sym: w / s for sym, w in weights.items()}

        signals: list[TradeSignal] = []

        for sym, current_w in current_holdings.items():
            if sym not in weights and current_w > 0.001:
                signals.append(TradeSignal(
                    symbol=sym,
                    asset_type=vol_proxies.get(sym, ("stock",))[0],
                    side="sell",
                    target_weight=0.0,
                    rationale=f"{sym} no longer in risk-parity universe — exiting position."
                ))

        for sym, target_w in weights.items():
            current_w = current_holdings.get(sym, 0.0)
            if abs(target_w - current_w) < self.params["rebalance_threshold"]:
                continue
            side = "buy" if target_w > current_w else "sell"
            asset_type = vol_proxies[sym][0]
            signals.append(TradeSignal(
                symbol=sym, asset_type=asset_type, side=side,
                target_weight=float(target_w),
                rationale=f"Risk-parity rebalance: {sym} target {target_w*100:.1f}% (inverse vol weight)."
            ))

        return signals