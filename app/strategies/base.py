"""Strategy interface. A strategy generates trade signals from market data
and the agent's current portfolio. Strategies don't execute trades — they
propose them. Risk and execution are separate layers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class TradeSignal:
    """A proposed trade. Risk layer can resize, reject, or pass through."""
    symbol: str
    asset_type: str  # "stock", "etf", "crypto", "commodity"
    side: str        # "buy" or "sell"
    target_weight: float  # Desired position size as fraction of total portfolio (0-1)
    rationale: str   # Why this signal exists


class Strategy:
    """Base class. Subclasses implement generate_signals."""

    template_name: str = "base"
    default_params: dict[str, Any] = {}

    def __init__(self, params: dict[str, Any] | None = None):
        # Merge user params over defaults
        self.params = {**self.default_params, **(params or {})}

    def generate_signals(
        self,
        history: dict[str, pd.DataFrame],
        current_holdings: dict[str, float],  # symbol -> current weight
    ) -> list[TradeSignal]:
        """Returns a list of TradeSignals. Override in subclasses."""
        raise NotImplementedError

    def describe(self) -> str:
        """Plain-English fallback (Claude can override)."""
        return f"{self.template_name} with params {self.params}"
