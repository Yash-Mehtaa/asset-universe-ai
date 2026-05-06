"""Risk parity — equal risk contribution across asset classes.

Idea: allocate so each asset contributes equally to portfolio variance,
rather than equal dollar weights. Popularized by Ray Dalio's Bridgewater
(All Weather). Reasonable long-horizon default — diversifies across
stocks/bonds/commodities/crypto and rebalances on volatility, not price.

Simplification for v1: inverse-volatility weighting, which is risk parity
under the assumption of zero correlation between assets. Good enough for v1;
proper risk parity needs a covariance matrix and we can upgrade later.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies.base import Strategy, TradeSignal


class RiskParityStrategy(Strategy):
    template_name = "risk_parity"
    default_params = {
        "vol_window": 60,             # Days of returns to compute vol
        "rebalance_threshold": 0.05,  # Only adjust if drift > 5%
        "min_position_weight": 0.05,
        "max_position_weight": 0.30,
        "asset_class_caps": {
            "stock": 0.50,
            "etf": 0.50,
            "crypto": 0.20,
            "commodity": 0.30,
        },
    }

    def generate_signals(
        self,
        history: dict[str, pd.DataFrame],
        current_holdings: dict[str, float],
    ) -> list[TradeSignal]:
        # Inverse-volatility weight per asset
        vols: dict[str, tuple[str, float]] = {}
        for key, df in history.items():
            if df is None or len(df) < self.params["vol_window"]:
                continue
            symbol, asset_type = key.split("|")
            ret = df["close"].pct_change().tail(self.params["vol_window"])
            vol = ret.std()
            if vol is None or pd.isna(vol) or vol <= 0:
                continue
            vols[symbol] = (asset_type, float(vol))

        if not vols:
            return []

        # Inverse-vol raw weights
        inv_vols = {sym: 1.0 / v for sym, (_, v) in vols.items()}
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
            ac = vols[sym][0]
            by_class[ac] = by_class.get(ac, 0) + w

        for ac, total_w in by_class.items():
            cap = self.params["asset_class_caps"].get(ac, 1.0)
            if total_w > cap:
                scale = cap / total_w
                for sym in list(weights.keys()):
                    if vols[sym][0] == ac:
                        weights[sym] *= scale

        # Renormalize to sum to ~1
        s = sum(weights.values())
        if s > 0:
            weights = {sym: w / s for sym, w in weights.items()}

        # Generate signals
        signals: list[TradeSignal] = []

        # Sell anything not in target
        for sym, current_w in current_holdings.items():
            if sym not in weights and current_w > 0.001:
                signals.append(TradeSignal(
                    symbol=sym, asset_type="stock", side="sell",
                    target_weight=0.0,
                    rationale="No longer in risk-parity universe."
                ))

        for sym, target_w in weights.items():
            current_w = current_holdings.get(sym, 0.0)
            if abs(target_w - current_w) < self.params["rebalance_threshold"]:
                continue
            side = "buy" if target_w > current_w else "sell"
            asset_type = vols[sym][0]
            signals.append(TradeSignal(
                symbol=sym, asset_type=asset_type, side=side,
                target_weight=float(target_w),
                rationale=f"Risk-parity rebalance to {target_w*100:.1f}% (inverse-vol)."
            ))

        return signals
