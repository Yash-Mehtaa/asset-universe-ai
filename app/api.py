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


@router.post("/reset")
def reset_all_agents(db: Session = Depends(get_db)) -> dict:
    """Wipe all trades, holdings, decisions, and snapshots. Reset cash to starting capital."""
    agents = db.query(Agent).all()
    for a in agents:
        db.query(Trade).filter_by(agent_id=a.id).delete()
        db.query(Holding).filter_by(agent_id=a.id).delete()
        db.query(Decision).filter_by(agent_id=a.id).delete()
        db.query(PerformanceSnapshot).filter_by(agent_id=a.id).delete()
        a.cash = a.starting_capital
        a.last_trade_at = None
        a.last_review_at = None
    db.commit()
    return {"status": "reset", "agents": [a.name for a in agents]}


@router.post("/run/{agent_name}")
def run_agent(agent_name: str, db: Session = Depends(get_db)) -> dict:
    """Manually trigger a trade cycle. Public endpoint for demos."""
    from app.agents import run_trading_cycle
    from app.reasoning import generate_trade_reasoning

    if agent_name == "all":
        results = {}
        for name in ["short_term", "mid_term", "long_term"]:
            a = db.query(Agent).filter_by(name=name).first()
            if a:
                result = run_trading_cycle(db, a)
                recent_trades = (
                    db.query(Trade)
                    .filter_by(agent_id=a.id)
                    .order_by(Trade.executed_at.desc())
                    .limit(result.get("n_trades", 0))
                    .all()
                )
                for trade in recent_trades:
                    if not trade.ai_reasoning:
                        trade.ai_reasoning = generate_trade_reasoning(trade)
                db.commit()
                results[name] = result
        return results

    a = db.query(Agent).filter_by(name=agent_name).first()
    if not a:
        raise HTTPException(404, f"Agent {agent_name} not found")

    result = run_trading_cycle(db, a)

    recent_trades = (
        db.query(Trade)
        .filter_by(agent_id=a.id)
        .order_by(Trade.executed_at.desc())
        .limit(max(result.get("n_trades", 0), 1))
        .all()
    )
    for trade in recent_trades:
        if not trade.ai_reasoning:
            trade.ai_reasoning = generate_trade_reasoning(trade)
    db.commit()

    return {
        **result,
        "trades": [
            {
                "symbol": t.symbol,
                "side": t.side,
                "price": t.price,
                "notional": t.notional,
                "rationale": t.rationale,
                "realized_pnl": t.realized_pnl,
                "ai_reasoning": t.ai_reasoning,
            }
            for t in recent_trades
        ],
        "no_trade_reason": None if result.get("n_trades", 0) > 0 else generate_no_trade_reason(agent_name, result),
    }


def generate_no_trade_reason(agent_name: str, result: dict) -> str:
    from app.config import config
    from anthropic import Anthropic
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    signals = result.get("n_signals", 0)
    rejected = result.get("n_rejected", 0)
    prompt = f"""You are an AI investment agent ({agent_name.replace('_', ' ')}).
You just ran a trade cycle and made NO trades.
Signals generated: {signals}, Signals rejected by risk controls: {rejected}.

Write 2-3 sentences explaining why you chose not to trade right now.
Be specific — mention market conditions, your strategy requirements, or risk controls.
Write in first person as the AI agent. Be honest and educational."""

    try:
        msg = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()
    except Exception:
        return f"No trades executed this cycle. {signals} signals were analyzed but {rejected} were rejected by risk controls. Market conditions did not meet the strategy's requirements at this time."


@router.post("/calculate")
def calculate_allocation(payload: dict, db: Session = Depends(get_db)) -> dict:
    """What would the AI buy with $X? Claude generates a hypothetical allocation with news."""
    import json
    import re
    from app.config import config
    from anthropic import Anthropic

    amount = payload.get("amount", 10000)
    strategy = payload.get("strategy", "momentum")

    strategy_descriptions = {
        "momentum": "momentum — buy the strongest recent performers, the stocks moving up the most today",
        "trend_following": "trend following — buy assets in clear uptrends based on moving averages",
        "risk_parity": "risk parity — diversify across asset classes weighted by inverse volatility",
    }
    strategy_desc = strategy_descriptions.get(strategy, strategy)

    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    prompt = f"""You are an AI investment agent using a {strategy_desc} strategy.
A user wants to invest ${amount:,.0f} right now today.

First, search the web for: today's top performing stocks, current market conditions, biggest movers right now.

Then, based on what you find, create a specific investment allocation.

Respond with ONLY a JSON object in this exact format:
{{
  "allocations": [
    {{
      "symbol": "AAPL",
      "name": "Apple Inc",
      "amount": 3000,
      "pct": 30,
      "reason": "Explain in 2-3 sentences why you'd buy this RIGHT NOW based on today's news and market conditions. Be specific — mention actual current events, price movements, or catalysts you found."
    }}
  ],
  "summary": "2-3 sentence explanation of your overall strategy and why these picks make sense together right now."
}}

Rules:
- Include exactly 3-5 positions
- Amounts must sum to exactly {amount}
- Use real ticker symbols
- Reasons must reference actual current news or market data you found
- No markdown, no explanation outside the JSON"""

    try:
        msg = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
        text = "\n".join(b.text for b in msg.content if hasattr(b, "text") and b.text is not None)
        text = re.sub(r"```json|```", "", text).strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {"error": "Could not parse response", "raw": text[:500]}
    except Exception as e:
        return {"error": str(e)}


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
            "realized_pnl": t.realized_pnl,
            "ai_reasoning": t.ai_reasoning,
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
    series = [{"date": s.snapshot_date.isoformat(), "value": s.total_value, "pnl_pct": s.pnl_pct} for s in snaps]
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


@router.get("/timeline")
def get_timeline(limit: int = 30, db: Session = Depends(get_db)) -> list[dict]:
    """Combined feed of all trades and decisions across all agents, newest first."""
    items = []

    trades = (
        db.query(Trade)
        .order_by(Trade.executed_at.desc())
        .limit(limit)
        .all()
    )
    for t in trades:
        agent = db.query(Agent).get(t.agent_id)
        items.append({
            "type": "trade",
            "agent_name": agent.name if agent else "unknown",
            "symbol": t.symbol,
            "side": t.side,
            "price": t.price,
            "notional": t.notional,
            "rationale": t.rationale,
            "ai_reasoning": t.ai_reasoning,
            "realized_pnl": t.realized_pnl,
            "timestamp": t.executed_at.isoformat(),
        })

    decisions = (
        db.query(Decision)
        .order_by(Decision.created_at.desc())
        .limit(limit)
        .all()
    )
    for d in decisions:
        agent = db.query(Agent).get(d.agent_id)
        items.append({
            "type": "decision",
            "agent_name": agent.name if agent else "unknown",
            "action": d.action,
            "reasoning": d.reasoning,
            "triggered_by": d.triggered_by,
            "timestamp": d.created_at.isoformat(),
        })

    items.sort(key=lambda x: x["timestamp"], reverse=True)
    return items[:limit]	

@router.post("/review/{agent_name}")
def review_agent(agent_name: str, db: Session = Depends(get_db)) -> dict:
    """Trigger Claude strategy review for one or all agents."""
    from app.review import run_review

    if agent_name == "all":
        results = {}
        for name in ["short_term", "mid_term", "long_term"]:
            a = db.query(Agent).filter_by(name=name).first()
            if a:
                d = run_review(db, a, triggered_by="manual")
                results[name] = {
                    "action": d.action,
                    "reasoning": d.reasoning,
                    "applied_changes": d.applied_changes,
                    "rejected_reason": d.rejected_reason,
                }
        return results

    a = db.query(Agent).filter_by(name=agent_name).first()
    if not a:
        raise HTTPException(404, f"Agent {agent_name} not found")

    d = run_review(db, a, triggered_by="manual")
    return {
        "action": d.action,
        "reasoning": d.reasoning,
        "applied_changes": d.applied_changes,
        "rejected_reason": d.rejected_reason,
    }
