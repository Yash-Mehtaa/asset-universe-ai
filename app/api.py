"""Public REST API. The Next.js frontend calls these endpoints to render
the AI investor section."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import (
    Agent, Decision, Holding, PerformanceSnapshot, SessionLocal, Trade,
)
from app.performance import current_portfolio_value, performance_summary

router = APIRouter(prefix="/api", tags=["agents"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _agent_or_404(db: Session, agent_id_or_name: str) -> Agent:
    if agent_id_or_name.isdigit():
        a = db.query(Agent).get(int(agent_id_or_name))
    else:
        a = db.query(Agent).filter_by(name=agent_id_or_name).first()
    if not a:
        raise HTTPException(404, f"Agent {agent_id_or_name} not found")
    return a


@router.get("/agents")
def list_agents(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Summary of all three agents — what the frontend uses for the cards."""
    out = []
    for a in db.query(Agent).order_by(Agent.id).all():
        cash, holdings_value, _ = current_portfolio_value(a)
        total = cash + holdings_value
        out.append({
            "id": a.id,
            "name": a.name,
            "horizon": a.horizon,
            "cash": cash,
            "holdings_value": holdings_value,
            "total_value": total,
            "starting_capital": a.starting_capital,
            "pnl": total - a.starting_capital,
            "pnl_pct": (total - a.starting_capital) / a.starting_capital if a.starting_capital else 0,
            "strategy_template": a.strategy.template if a.strategy else None,
            "strategy_plain_english": a.strategy.plain_english if a.strategy else None,
            "last_trade_at": a.last_trade_at.isoformat() if a.last_trade_at else None,
            "last_review_at": a.last_review_at.isoformat() if a.last_review_at else None,
        })
    return out


@router.get("/agents/{agent_id}/portfolio")
def get_portfolio(agent_id: str, db: Session = Depends(get_db)) -> dict:
    a = _agent_or_404(db, agent_id)
    cash, holdings_value, prices = current_portfolio_value(a)
    holdings = [
        {
            "symbol": h.symbol,
            "asset_type": h.asset_type,
            "quantity": h.quantity,
            "avg_cost": h.avg_cost,
            "current_price": prices.get(h.symbol, h.last_price),
            "value": h.quantity * prices.get(h.symbol, h.last_price or h.avg_cost),
            "pnl_pct": ((prices.get(h.symbol, h.last_price or h.avg_cost) / h.avg_cost) - 1) if h.avg_cost else 0,
        }
        for h in a.holdings
    ]
    return {
        "agent": a.name,
        "cash": cash,
        "holdings_value": holdings_value,
        "total_value": cash + holdings_value,
        "holdings": holdings,
    }


@router.get("/agents/{agent_id}/trades")
def get_trades(agent_id: str, limit: int = 20, db: Session = Depends(get_db)) -> list[dict]:
    a = _agent_or_404(db, agent_id)
    trades = (
        db.query(Trade)
        .filter_by(agent_id=a.id)
        .order_by(Trade.executed_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": t.id,
            "symbol": t.symbol,
            "asset_type": t.asset_type,
            "side": t.side,
            "quantity": t.quantity,
            "price": t.price,
            "notional": t.notional,
            "rationale": t.rationale,
            "executed_at": t.executed_at.isoformat(),
        }
        for t in trades
    ]


@router.get("/agents/{agent_id}/performance")
def get_performance(agent_id: str, days: int = 90, db: Session = Depends(get_db)) -> dict:
    a = _agent_or_404(db, agent_id)
    snaps = (
        db.query(PerformanceSnapshot)
        .filter_by(agent_id=a.id)
        .order_by(PerformanceSnapshot.snapshot_date.asc())
        .all()
    )
    series = [
        {
            "date": s.snapshot_date.isoformat(),
            "value": s.total_value,
            "pnl_pct": s.pnl_pct,
        }
        for s in snaps
    ]
    return {
        "agent": a.name,
        "summary": performance_summary(a, db),
        "series": series,
    }


@router.get("/agents/{agent_id}/strategy")
def get_strategy(agent_id: str, db: Session = Depends(get_db)) -> dict:
    a = _agent_or_404(db, agent_id)
    return {
        "agent": a.name,
        "template": a.strategy.template,
        "params": a.strategy.params,
        "plain_english": a.strategy.plain_english,
        "version": a.strategy.version,
        "updated_at": a.strategy.updated_at.isoformat(),
    }


@router.get("/agents/{agent_id}/decisions")
def get_decisions(agent_id: str, limit: int = 10, db: Session = Depends(get_db)) -> list[dict]:
    a = _agent_or_404(db, agent_id)
    decisions = (
        db.query(Decision)
        .filter_by(agent_id=a.id)
        .order_by(Decision.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": d.id,
            "triggered_by": d.triggered_by,
            "action": d.action,
            "reasoning": d.reasoning,
            "performance_summary": d.performance_summary,
            "proposed_changes": d.proposed_changes,
            "applied_changes": d.applied_changes,
            "rejected_reason": d.rejected_reason,
            "created_at": d.created_at.isoformat(),
        }
        for d in decisions
    ]
