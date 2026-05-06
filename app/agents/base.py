"""Agent runner. Pulls market data, asks strategy for signals, validates,
executes. Also handles emergency drawdown checks."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

import pandas as pd
from sqlalchemy.orm import Session

from app.config import config
from app.db import Agent
from app.execution import execute_trades
from app.market_data import market_data, MarketDataError
from app.performance import current_portfolio_value, performance_summary, take_snapshot
from app.review import run_review
from app.risk import validate_and_size
from app.strategies import get_strategy


def _universe_for(horizon: str) -> list[tuple[str, str]]:
    """Universe of (symbol, asset_type) for each agent."""
    if horizon == "short":
        return (
            [(s, "stock") for s in config.STOCK_UNIVERSE]
            + [(s, "etf") for s in config.ETF_UNIVERSE]
        )
    if horizon == "mid":
        return (
            [(s, "stock") for s in config.STOCK_UNIVERSE]
            + [(s, "etf") for s in config.ETF_UNIVERSE]
            + [(s, "commodity") for s in config.COMMODITY_ETF_UNIVERSE]
        )
    if horizon == "long":
        return (
            [(s, "etf") for s in config.ETF_UNIVERSE]
            + [(s, "commodity") for s in config.COMMODITY_ETF_UNIVERSE]
            + [(s, "crypto") for s in config.CRYPTO_UNIVERSE]
        )
    raise ValueError(f"Unknown horizon: {horizon}")


def _gather_history(universe: list[tuple[str, str]], days: int) -> dict[str, pd.DataFrame]:
    history: dict[str, pd.DataFrame] = {}
    for symbol, asset_type in universe:
        try:
            df = market_data.get_history(symbol, asset_type, days=days)
            history[f"{symbol}|{asset_type}"] = df
        except (MarketDataError, Exception):
            # Skip on transient API errors — don't fail the whole cycle
            continue
    return history


def _gather_prices(universe: list[tuple[str, str]]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol, asset_type in universe:
        try:
            prices[symbol] = market_data.get_price(symbol, asset_type)
        except Exception:
            continue
    return prices


def run_trading_cycle(db: Session, agent: Agent) -> dict:
    """One full cycle: data -> strategy -> risk -> execute. Returns summary."""
    # Determine universe and lookback window
    horizon = agent.horizon
    lookback = {"short": 60, "mid": 120, "long": 365}[horizon]
    universe = _universe_for(horizon)

    history = _gather_history(universe, days=lookback)
    prices = _gather_prices(universe)

    # Build current weights (% of total portfolio value)
    cash, holdings_value, _ = current_portfolio_value(agent)
    portfolio_value = cash + holdings_value

    if portfolio_value <= 0:
        return {"status": "skipped", "reason": "zero portfolio value"}

    current_weights: dict[str, float] = {}
    current_holdings: dict[str, dict] = {}
    for h in agent.holdings:
        price = prices.get(h.symbol, h.last_price or h.avg_cost)
        weight = (h.quantity * price) / portfolio_value if portfolio_value > 0 else 0
        current_weights[h.symbol] = weight
        current_holdings[h.symbol] = {"quantity": h.quantity, "price": price}

    # Run strategy
    strategy = get_strategy(agent.strategy.template, agent.strategy.params)
    signals = strategy.generate_signals(history, current_weights)

    # Validate
    approved, rejected = validate_and_size(
        signals,
        portfolio_value=portfolio_value,
        cash=cash,
        current_holdings=current_holdings,
        prices=prices,
    )

    # Execute
    trades = execute_trades(
        db, agent, approved,
        strategy_snapshot={
            "template": agent.strategy.template,
            "params": agent.strategy.params,
            "version": agent.strategy.version,
        },
    )

    # Snapshot performance
    snap = take_snapshot(db, agent)

    # Emergency drawdown check
    summary = performance_summary(agent, db)
    if summary["max_drawdown_pct"] <= -config.EMERGENCY_DRAWDOWN_PCT:
        # Trigger off-cycle review
        run_review(db, agent, triggered_by="emergency_drawdown")

    return {
        "status": "ok",
        "n_signals": len(signals),
        "n_approved": len(approved),
        "n_rejected": len(rejected),
        "n_trades": len(trades),
        "portfolio_value": snap.total_value,
        "pnl_pct": snap.pnl_pct,
    }
