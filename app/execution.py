"""Simulated execution. Updates DB state to reflect approved trades."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.db import Agent, Holding, Trade
from app.risk import ValidatedTrade


def execute_trades(
    db: Session,
    agent: Agent,
    trades: list[ValidatedTrade],
    strategy_snapshot: dict,
) -> list[Trade]:
    """Apply trades to portfolio. Returns the persisted Trade rows."""
    persisted: list[Trade] = []

    for vt in trades:
        if vt.side == "buy":
            # Reduce cash, create or augment holding
            cost = vt.notional
            if cost > agent.cash:
                continue  # Defensive — risk layer should have caught this
            agent.cash -= cost

            existing = next(
                (h for h in agent.holdings if h.symbol == vt.symbol), None
            )
            if existing:
                # Weighted average cost basis
                total_qty = existing.quantity + vt.quantity
                existing.avg_cost = (
                    (existing.avg_cost * existing.quantity)
                    + (vt.price * vt.quantity)
                ) / total_qty
                existing.quantity = total_qty
                existing.last_price = vt.price
                existing.last_price_at = datetime.utcnow()
            else:
                db.add(Holding(
                    agent_id=agent.id,
                    symbol=vt.symbol,
                    asset_type=vt.asset_type,
                    quantity=vt.quantity,
                    avg_cost=vt.price,
                    last_price=vt.price,
                    last_price_at=datetime.utcnow(),
                ))

        elif vt.side == "sell":
            existing = next(
                (h for h in agent.holdings if h.symbol == vt.symbol), None
            )
            if not existing or existing.quantity < vt.quantity - 1e-9:
                continue
            proceeds = vt.notional
            agent.cash += proceeds
            existing.quantity -= vt.quantity
            existing.last_price = vt.price
            existing.last_price_at = datetime.utcnow()
            if existing.quantity <= 1e-9:
                db.delete(existing)

        # Log the trade
        trade = Trade(
            agent_id=agent.id,
            symbol=vt.symbol,
            asset_type=vt.asset_type,
            side=vt.side,
            quantity=vt.quantity,
            price=vt.price,
            notional=vt.notional,
            rationale=vt.rationale,
            strategy_snapshot=strategy_snapshot,
        )
        db.add(trade)
        persisted.append(trade)

    agent.last_trade_at = datetime.utcnow()
    db.commit()
    return persisted
