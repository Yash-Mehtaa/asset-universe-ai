"""Budget tracking and enforcement for all Claude API calls."""
from __future__ import annotations

from datetime import datetime
from calendar import monthrange

from sqlalchemy.orm import Session
from app.config import config
from app.db import BudgetLog, SessionLocal


def log_usage(
    db: Session,
    endpoint: str,
    input_tokens: int,
    output_tokens: int,
    agent_name: str | None = None,
) -> float:
    """Log a Claude call and return estimated cost."""
    cost = (
        input_tokens * config.COST_PER_INPUT_TOKEN +
        output_tokens * config.COST_PER_OUTPUT_TOKEN
    )
    entry = BudgetLog(
        endpoint=endpoint,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=cost,
        agent_name=agent_name,
    )
    db.add(entry)
    db.commit()
    return cost


def monthly_spend(db: Session) -> float:
    """Sum of all Claude costs this calendar month."""
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)
    rows = db.query(BudgetLog).filter(BudgetLog.called_at >= month_start).all()
    return sum(r.estimated_cost_usd for r in rows)


def check_budget(db: Session) -> tuple[bool, float, float]:
    """Returns (ok_to_proceed, current_spend, cap)."""
    spend = monthly_spend(db)
    return spend < config.MONTHLY_BUDGET_CAP_USD, spend, config.MONTHLY_BUDGET_CAP_USD


def budget_status(db: Session) -> dict:
    """Full budget status for the API."""
    ok, spend, cap = check_budget(db)
    now = datetime.utcnow()
    _, days_in_month = monthrange(now.year, now.month)
    day_of_month = now.day
    projected = (spend / day_of_month) * days_in_month if day_of_month > 0 else 0
    return {
        "monthly_spend_usd": round(spend, 4),
        "monthly_cap_usd": cap,
        "remaining_usd": round(max(cap - spend, 0), 4),
        "projected_monthly_usd": round(projected, 4),
        "within_budget": ok,
        "pct_used": round(spend / cap * 100, 1) if cap else 0,
    }
