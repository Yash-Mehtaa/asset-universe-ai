"""Risk layer.

Strategies generate signals (target weights). The risk layer:
  1. Converts target weights to actual share counts given current portfolio value
  2. Enforces per-position size caps
  3. Enforces cash floor
  4. Enforces daily turnover cap
  5. Rejects nonsense (zero/negative prices, NaN, etc.)
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import config
from app.strategies.base import TradeSignal


@dataclass
class ValidatedTrade:
    symbol: str
    asset_type: str
    side: str
    quantity: float
    price: float
    notional: float
    rationale: str


def validate_and_size(
    signals: list[TradeSignal],
    portfolio_value: float,
    cash: float,
    current_holdings: dict[str, dict],  # symbol -> {"quantity": x, "price": y}
    prices: dict[str, float],            # symbol -> current price
    today_turnover: float = 0.0,
) -> tuple[list[ValidatedTrade], list[tuple[TradeSignal, str]]]:
    """Returns (approved_trades, rejected_signals_with_reason)."""
    approved: list[ValidatedTrade] = []
    rejected: list[tuple[TradeSignal, str]] = []

    cash_floor = portfolio_value * config.MIN_CASH_FLOOR_PCT
    turnover_remaining = portfolio_value * config.MAX_DAILY_TURNOVER_PCT - today_turnover

    # Process sells first — they free up cash
    sells = [s for s in signals if s.side == "sell"]
    buys = [s for s in signals if s.side == "buy"]

    running_cash = cash

    for sig in sells:
        price = prices.get(sig.symbol)
        if not price or price <= 0:
            rejected.append((sig, "no valid price"))
            continue
        held = current_holdings.get(sig.symbol)
        if not held or held["quantity"] <= 0:
            rejected.append((sig, "no position to sell"))
            continue

        target_value = sig.target_weight * portfolio_value
        current_value = held["quantity"] * price
        sell_value = max(current_value - target_value, 0)
        if sell_value <= 0:
            rejected.append((sig, "target weight not below current"))
            continue

        if sell_value > turnover_remaining:
            sell_value = turnover_remaining
        if sell_value <= 1.0:
            rejected.append((sig, "turnover budget exhausted"))
            continue

        qty = sell_value / price
        qty = min(qty, held["quantity"])  # Can't sell more than we hold

        approved.append(ValidatedTrade(
            symbol=sig.symbol, asset_type=sig.asset_type, side="sell",
            quantity=qty, price=price, notional=qty * price,
            rationale=sig.rationale,
        ))
        running_cash += qty * price
        turnover_remaining -= qty * price

    for sig in buys:
        price = prices.get(sig.symbol)
        if not price or price <= 0:
            rejected.append((sig, "no valid price"))
            continue

        # Per-position cap
        target_w = min(sig.target_weight, config.MAX_POSITION_PCT)
        target_value = target_w * portfolio_value
        held = current_holdings.get(sig.symbol)
        current_value = (held["quantity"] * price) if held else 0
        buy_value = max(target_value - current_value, 0)

        if buy_value <= 0:
            rejected.append((sig, "target weight already met"))
            continue

        # Cash floor
        if running_cash - buy_value < cash_floor:
            buy_value = max(running_cash - cash_floor, 0)
        if buy_value <= 1.0:
            rejected.append((sig, "cash floor would be breached"))
            continue

        # Turnover cap
        if buy_value > turnover_remaining:
            buy_value = turnover_remaining
        if buy_value <= 1.0:
            rejected.append((sig, "turnover budget exhausted"))
            continue

        qty = buy_value / price
        approved.append(ValidatedTrade(
            symbol=sig.symbol, asset_type=sig.asset_type, side="buy",
            quantity=qty, price=price, notional=qty * price,
            rationale=sig.rationale,
        ))
        running_cash -= qty * price
        turnover_remaining -= qty * price

    return approved, rejected
