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
    signals = result.get("n_signals", 0)
    rejected = result.get("n_rejected", 0)
    name = agent_name.replace("_", " ").title()
    if signals == 0:
        return f"No signals generated this cycle. The {name} strategy scanned the full asset universe but no assets met the minimum threshold requirements. This is normal during low-volatility or off-hours market conditions."
    if rejected == signals:
        return f"{signals} signal{'s' if signals != 1 else ''} generated but all rejected by risk controls. The {name} strategy identified potential trades but position size limits, cash floor requirements, or turnover caps prevented execution. Capital is preserved for higher-conviction opportunities."
    return f"{signals} signal{'s' if signals != 1 else ''} analyzed, {rejected} rejected by risk controls. The remaining signals did not meet the {name} strategy's minimum threshold for execution. No trades were made this cycle."


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
        # Server-side rate limit: max 1 full review sweep per day
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        manual_reviews_today = db.query(Decision).filter(
            Decision.triggered_by == "manual",
            Decision.created_at >= today_start
        ).count()
        if manual_reviews_today >= 3:
            raise HTTPException(429, "Manual reviews already triggered today for all agents.")
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

    # Server-side rate limit: max 1 manual review per agent per day
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    reviews_today = db.query(Decision).filter(
        Decision.agent_id == a.id,
        Decision.triggered_by == "manual",
        Decision.created_at >= today_start
    ).count()
    if reviews_today >= 1:
        raise HTTPException(429, f"Manual review already triggered for {agent_name} today. Next review available tomorrow.")

    d = run_review(db, a, triggered_by="manual")
    return {
        "action": d.action,
        "reasoning": d.reasoning,
        "applied_changes": d.applied_changes,
        "rejected_reason": d.rejected_reason,
    }


# ── New endpoints ──────────────────────────────────────────────────────────────

@router.get("/catalyst")
def get_catalyst_events(db: Session = Depends(get_db)) -> list[dict]:
    """Latest daily catalyst scan results."""
    from app.db import CatalystEvent
    from sqlalchemy import desc
    # Get most recent scan date
    latest = db.query(CatalystEvent).order_by(desc(CatalystEvent.scan_date)).first()
    if not latest:
        return []
    scan_date = latest.scan_date.replace(hour=0, minute=0, second=0, microsecond=0)
    events = (
        db.query(CatalystEvent)
        .filter(CatalystEvent.scan_date >= scan_date)
        .order_by(CatalystEvent.rank)
        .all()
    )
    return [
        {
            "rank": e.rank,
            "symbol": e.symbol,
            "event_type": e.event_type,
            "title": e.title,
            "description": e.description,
            "expected_impact": e.expected_impact,
            "date_of_event": e.date_of_event,
            "scan_date": e.scan_date.isoformat(),
        }
        for e in events
    ]


@router.post("/catalyst/scan")
def trigger_catalyst_scan(db: Session = Depends(get_db)) -> dict:
    from app.db import CatalystEvent
    from app.catalyst import run_catalyst_scan
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    existing = db.query(CatalystEvent).filter(CatalystEvent.scan_date >= today_start).first()
    if existing:
        return {"status": "already_scanned", "message": "Catalyst scan already ran today.", "events_found": 0}
    events = run_catalyst_scan(db)
    return {"status": "ok", "events_found": len(events)}


@router.get("/review-summary")
def get_review_summary(db: Session = Depends(get_db)) -> dict:
    """Latest weekly review summary for the UI."""
    from app.db import WeeklyReviewSummary
    row = db.query(WeeklyReviewSummary).order_by(WeeklyReviewSummary.created_at.desc()).first()
    if not row:
        return {"available": False}
    return {
        "available": True,
        "last_review_date": row.last_review_date.isoformat(),
        "next_review_date": row.next_review_date.isoformat(),
        "performance_analysis": row.performance_analysis,
        "changes_made": row.changes_made or [],
        "market_outlook": row.market_outlook or [],
        "created_at": row.created_at.isoformat(),
    }


@router.get("/agents/{agent_id}/strategy/history")
def get_strategy_history(agent_id: str, db: Session = Depends(get_db)) -> list[dict]:
    """All historical strategy versions for an agent."""
    from app.db import StrategyHistory
    a = _agent_or_404(db, agent_id)
    history = (
        db.query(StrategyHistory)
        .filter_by(agent_id=a.id)
        .order_by(StrategyHistory.changed_at.desc())
        .all()
    )
    return [
        {
            "id": h.id,
            "template": h.template,
            "params": h.params,
            "changed_at": h.changed_at.isoformat(),
            "triggered_by": h.triggered_by,
        }
        for h in history
    ]


@router.post("/agents/{agent_id}/strategy/rollback/{history_id}")
def rollback_strategy(agent_id: str, history_id: int, db: Session = Depends(get_db)) -> dict:
    """Roll back an agent's strategy to a previous version."""
    from app.db import StrategyHistory
    a = _agent_or_404(db, agent_id)
    hist = db.query(StrategyHistory).filter_by(id=history_id, agent_id=a.id).first()
    if not hist:
        raise HTTPException(404, "History entry not found")

    # Save current as history first
    db.add(StrategyHistory(
        agent_id=a.id,
        template=a.strategy.template,
        params=a.strategy.params,
        triggered_by="rollback_snapshot",
    ))

    a.strategy.template = hist.template
    a.strategy.params = hist.params
    a.strategy.updated_at = datetime.utcnow()
    a.strategy.version += 1

    db.commit()
    return {"status": "rolled_back", "template": hist.template, "params": hist.params}


@router.get("/budget")
def get_budget(db: Session = Depends(get_db)) -> dict:
    """Current month Claude API spend vs cap."""
    from app.budget import budget_status
    return budget_status(db)


# ── New endpoints ──────────────────────────────────────────────────────────────

@router.get("/catalyst")
def get_catalyst_events(db: Session = Depends(get_db)) -> list[dict]:
    """Latest daily catalyst scan results."""
    from app.db import CatalystEvent
    from sqlalchemy import desc
    # Get most recent scan date
    latest = db.query(CatalystEvent).order_by(desc(CatalystEvent.scan_date)).first()
    if not latest:
        return []
    scan_date = latest.scan_date.replace(hour=0, minute=0, second=0, microsecond=0)
    events = (
        db.query(CatalystEvent)
        .filter(CatalystEvent.scan_date >= scan_date)
        .order_by(CatalystEvent.rank)
        .all()
    )
    return [
        {
            "rank": e.rank,
            "symbol": e.symbol,
            "event_type": e.event_type,
            "title": e.title,
            "description": e.description,
            "expected_impact": e.expected_impact,
            "date_of_event": e.date_of_event,
            "scan_date": e.scan_date.isoformat(),
        }
        for e in events
    ]


@router.post("/catalyst/scan")
def trigger_catalyst_scan(db: Session = Depends(get_db)) -> dict:
    from app.db import CatalystEvent
    from app.catalyst import run_catalyst_scan
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    existing = db.query(CatalystEvent).filter(CatalystEvent.scan_date >= today_start).first()
    if existing:
        return {"status": "already_scanned", "message": "Catalyst scan already ran today.", "events_found": 0}
    events = run_catalyst_scan(db)
    return {"status": "ok", "events_found": len(events)}


@router.get("/review-summary")
def get_review_summary(db: Session = Depends(get_db)) -> dict:
    """Latest weekly review summary for the UI."""
    from app.db import WeeklyReviewSummary
    row = db.query(WeeklyReviewSummary).order_by(WeeklyReviewSummary.created_at.desc()).first()
    if not row:
        return {"available": False}
    return {
        "available": True,
        "last_review_date": row.last_review_date.isoformat(),
        "next_review_date": row.next_review_date.isoformat(),
        "performance_analysis": row.performance_analysis,
        "changes_made": row.changes_made or [],
        "market_outlook": row.market_outlook or [],
        "created_at": row.created_at.isoformat(),
    }


@router.get("/agents/{agent_id}/strategy/history")
def get_strategy_history(agent_id: str, db: Session = Depends(get_db)) -> list[dict]:
    """All historical strategy versions for an agent."""
    from app.db import StrategyHistory
    a = _agent_or_404(db, agent_id)
    history = (
        db.query(StrategyHistory)
        .filter_by(agent_id=a.id)
        .order_by(StrategyHistory.changed_at.desc())
        .all()
    )
    return [
        {
            "id": h.id,
            "template": h.template,
            "params": h.params,
            "changed_at": h.changed_at.isoformat(),
            "triggered_by": h.triggered_by,
        }
        for h in history
    ]


@router.post("/agents/{agent_id}/strategy/rollback/{history_id}")
def rollback_strategy(agent_id: str, history_id: int, db: Session = Depends(get_db)) -> dict:
    """Roll back an agent's strategy to a previous version."""
    from app.db import StrategyHistory
    a = _agent_or_404(db, agent_id)
    hist = db.query(StrategyHistory).filter_by(id=history_id, agent_id=a.id).first()
    if not hist:
        raise HTTPException(404, "History entry not found")

    # Save current as history first
    db.add(StrategyHistory(
        agent_id=a.id,
        template=a.strategy.template,
        params=a.strategy.params,
        triggered_by="rollback_snapshot",
    ))

    a.strategy.template = hist.template
    a.strategy.params = hist.params
    a.strategy.updated_at = datetime.utcnow()
    a.strategy.version += 1

    db.commit()
    return {"status": "rolled_back", "template": hist.template, "params": hist.params}


@router.get("/budget")
def get_budget(db: Session = Depends(get_db)) -> dict:
    """Current month Claude API spend vs cap."""
    from app.budget import budget_status
    return budget_status(db)


@router.post("/calculate-strategy")
def calculate_strategy(payload: dict, db: Session = Depends(get_db)) -> dict:
    """Run the real strategy algorithm on live prices. Same logic the agents use. Zero Claude cost."""
    import pandas as pd
    from app.market_data import market_data
    from app.strategies import get_strategy
    from app.config import config

    amount = float(payload.get("amount", 10000))
    strategy_name = payload.get("strategy", "momentum")

    universe = (
        [(s, "stock") for s in config.STOCK_UNIVERSE] +
        [(s, "etf") for s in config.ETF_UNIVERSE] +
        [(s, "crypto") for s in config.CRYPTO_UNIVERSE] +
        [(s, "commodity") for s in config.COMMODITY_ETF_UNIVERSE]
    )

    # Fetch live quotes and build history dict
    history: dict[str, pd.DataFrame] = {}
    quotes: dict[str, dict] = {}
    for symbol, asset_type in universe:
        try:
            df = market_data.get_history(symbol, asset_type)
            history[f"{symbol}|{asset_type}"] = df
            # Store the quote fields for response
            if not df.empty:
                row = df.iloc[-1]
                quotes[symbol] = {
                    "c": float(row.get("close", 0)),
                    "dp": float(row.get("dp", 0)),
                }
        except Exception:
            continue

    if not history:
        return {"error": "Could not fetch market data. Check Finnhub API key."}

    # Use the matching agent's current params if available
    agent_name_map = {
        "momentum": "short_term",
        "trend_following": "mid_term",
        "risk_parity": "long_term",
    }
    agent = db.query(Agent).filter_by(name=agent_name_map.get(strategy_name, "")).first()
    if agent and agent.strategy and agent.strategy.template == strategy_name:
        params = agent.strategy.params
    else:
        params = None

    try:
        strat = get_strategy(strategy_name, params)
    except Exception:
        return {"error": f"Unknown strategy: {strategy_name}"}

    signals = strat.generate_signals(history, current_holdings={})

    buys = [s for s in signals if s.side == "buy" and s.target_weight > 0]
    buys.sort(key=lambda s: s.target_weight, reverse=True)

    if not buys:
        return {
            "allocations": [],
            "summary": "The strategy found no buy signals in current market conditions. All assets are below the minimum threshold right now.",
            "strategy": strategy_name,
            "live_data": True,
        }

    total_w = sum(s.target_weight for s in buys)
    allocations = []
    for sig in buys:
        normalized_w = sig.target_weight / total_w if total_w > 0 else 0
        q = quotes.get(sig.symbol, {})
        allocations.append({
            "symbol": sig.symbol,
            "name": sig.symbol,
            "amount": round(amount * normalized_w),
            "pct": round(normalized_w * 100, 1),
            "reason": sig.rationale,
            "current_price": q.get("c", 0),
            "daily_change_pct": q.get("dp", 0),
        })

    strategy_summaries = {
        "momentum": "Momentum strategy: ranked all assets by today's live daily % change from Finnhub. Strongest movers get the largest allocations proportional to their momentum score.",
        "trend_following": "Trend following strategy: filtered assets with positive daily momentum above the minimum threshold, sized proportionally to trend strength using live Finnhub data.",
        "risk_parity": "Risk parity strategy: sized each position by inverse daily volatility from live Finnhub data so every asset contributes equal risk to the portfolio.",
    }

    return {
        "allocations": allocations,
        "summary": strategy_summaries.get(strategy_name, f"{strategy_name} allocation based on live market data."),
        "strategy": strategy_name,
        "live_data": True,
    }
