"""Performance computation and snapshotting."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.db import Agent, PerformanceSnapshot
from app.market_data import market_data


def current_portfolio_value(agent: Agent) -> tuple[float, float, dict[str, float]]:
    """Returns (cash, holdings_value, prices_used)."""
    prices: dict[str, float] = {}
    holdings_value = 0.0
    for h in agent.holdings:
        try:
            price = market_data.get_price(h.symbol, h.asset_type)
        except Exception:
            price = h.last_price or h.avg_cost
        prices[h.symbol] = price
        holdings_value += h.quantity * price
    return agent.cash, holdings_value, prices


def take_snapshot(db: Session, agent: Agent) -> PerformanceSnapshot:
    cash, holdings_value, _ = current_portfolio_value(agent)
    total = cash + holdings_value
    pnl = total - agent.starting_capital
    pnl_pct = pnl / agent.starting_capital if agent.starting_capital > 0 else 0.0

    snap = PerformanceSnapshot(
        agent_id=agent.id,
        snapshot_date=datetime.utcnow(),
        cash=cash,
        holdings_value=holdings_value,
        total_value=total,
        pnl=pnl,
        pnl_pct=pnl_pct,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def performance_summary(agent: Agent, db: Session) -> dict[str, Any]:
    """Compute summary stats for review prompts."""
    snaps = (
        db.query(PerformanceSnapshot)
        .filter_by(agent_id=agent.id)
        .order_by(PerformanceSnapshot.snapshot_date.asc())
        .all()
    )
    if not snaps:
        return {
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": None,
            "n_snapshots": 0,
        }

    values = np.array([s.total_value for s in snaps])
    rets = np.diff(values) / values[:-1] if len(values) > 1 else np.array([])

    # Drawdown
    peak = np.maximum.accumulate(values)
    dd = (values - peak) / peak
    max_dd = float(dd.min()) if len(dd) else 0.0

    sharpe = None
    if len(rets) >= 5 and rets.std() > 0:
        sharpe = float((rets.mean() / rets.std()) * np.sqrt(252))

    return {
        "total_return_pct": float((values[-1] - agent.starting_capital) / agent.starting_capital),
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "n_snapshots": len(snaps),
        "current_value": float(values[-1]),
    }
