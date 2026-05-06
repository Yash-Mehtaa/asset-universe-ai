"""Self-review: ask Claude to evaluate the agent's recent performance and
optionally adjust its strategy. Output is validated against guardrails before
being applied."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.config import config
from app.db import Agent, Strategy, StrategyHistory, Decision, Trade, PerformanceSnapshot
from app.performance import performance_summary
from app.prompts import REVIEW_SYSTEM_PROMPT, REVIEW_USER_TEMPLATE, PLAIN_ENGLISH_PROMPT
from app.strategies import STRATEGY_REGISTRY, available_templates, get_strategy


_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _format_trades(trades: list[Trade]) -> str:
    if not trades:
        return "(no trades yet)"
    lines = []
    for t in trades:
        lines.append(
            f"- {t.executed_at.strftime('%Y-%m-%d')} {t.side.upper()} {t.symbol} "
            f"qty={t.quantity:.4f} @ ${t.price:.2f} — {t.rationale}"
        )
    return "\n".join(lines)


def _format_snapshots(snaps: list[PerformanceSnapshot]) -> str:
    if not snaps:
        return "(no snapshots yet)"
    return "\n".join(
        f"- {s.snapshot_date.strftime('%Y-%m-%d')}: ${s.total_value:.2f} "
        f"({s.pnl_pct*100:+.2f}%)"
        for s in snaps
    )


def _validate_proposal(
    proposal: dict[str, Any],
    current_strategy: Strategy,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """Returns (is_valid, rejection_reason, sanitized_changes)."""
    action = proposal.get("action")
    if action not in ("keep", "tune", "switch", "blend"):
        return False, f"invalid action: {action}", None

    if action == "keep":
        return True, None, {}

    new_template = proposal.get("new_template")
    new_params = proposal.get("new_params") or {}

    if action in ("switch", "blend"):
        if new_template not in available_templates():
            return False, f"unknown template: {new_template}", None

    if action == "tune":
        # Enforce max param change
        for k, v in new_params.items():
            if k not in current_strategy.params:
                return False, f"unknown param: {k}", None
            current_v = current_strategy.params[k]
            if isinstance(current_v, (int, float)) and current_v != 0:
                pct_change = abs((v - current_v) / current_v)
                if pct_change > config.MAX_PARAM_CHANGE_PCT:
                    return False, f"param {k} changes by {pct_change:.1%}, exceeds {config.MAX_PARAM_CHANGE_PCT:.0%} cap", None

    return True, None, {
        "action": action,
        "new_template": new_template,
        "new_params": new_params,
    }


def _get_plain_english(template: str, params: dict) -> str:
    try:
        msg = _client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": PLAIN_ENGLISH_PROMPT.format(template=template, params=params),
            }],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"{template} strategy ({e})"


def run_review(
    db: Session,
    agent: Agent,
    triggered_by: str = "scheduled",
) -> Decision:
    """Run a single review cycle. Returns the persisted Decision row."""
    # Gather context
    summary = performance_summary(agent, db)

    recent_trades = (
        db.query(Trade)
        .filter_by(agent_id=agent.id)
        .order_by(Trade.executed_at.desc())
        .limit(20)
        .all()
    )
    recent_snaps = (
        db.query(PerformanceSnapshot)
        .filter_by(agent_id=agent.id)
        .order_by(PerformanceSnapshot.snapshot_date.desc())
        .limit(10)
        .all()
    )
    recent_snaps = list(reversed(recent_snaps))

    current_strat = get_strategy(agent.strategy.template, agent.strategy.params)

    user_msg = REVIEW_USER_TEMPLATE.format(
        agent_name=agent.name,
        horizon=agent.horizon,
        template=agent.strategy.template,
        params=json.dumps(agent.strategy.params, indent=2),
        total_return_pct=summary["total_return_pct"],
        max_drawdown_pct=summary["max_drawdown_pct"],
        sharpe=f"{summary['sharpe']:.2f}" if summary["sharpe"] else "n/a",
        n_snapshots=summary["n_snapshots"],
        current_value=summary.get("current_value", agent.cash),
        starting_capital=agent.starting_capital,
        n_trades=len(recent_trades),
        trade_history=_format_trades(recent_trades),
        n_recent=len(recent_snaps),
        recent_snapshots=_format_snapshots(recent_snaps),
        available_templates="\n".join(f"- {t}" for t in available_templates()),
    )

    # Call Claude
    response = _client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1500,
        system=REVIEW_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text.strip()

    # Strip code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        proposal = json.loads(raw)
    except json.JSONDecodeError as e:
        decision = Decision(
            agent_id=agent.id,
            triggered_by=triggered_by,
            performance_summary=summary,
            action="keep",
            reasoning=f"Could not parse Claude response: {e}. Raw: {raw[:200]}",
            proposed_changes={"raw": raw[:500]},
            applied_changes=None,
            rejected_reason="json parse error",
        )
        db.add(decision)
        db.commit()
        return decision

    # Validate
    is_valid, reject_reason, sanitized = _validate_proposal(proposal, current_strat)

    decision = Decision(
        agent_id=agent.id,
        triggered_by=triggered_by,
        performance_summary=summary,
        action=proposal.get("action", "keep"),
        reasoning=proposal.get("reasoning", ""),
        proposed_changes={
            "new_template": proposal.get("new_template"),
            "new_params": proposal.get("new_params"),
            "blend_with": proposal.get("blend_with"),
        },
        applied_changes=sanitized if is_valid else None,
        rejected_reason=reject_reason,
    )
    db.add(decision)
    db.flush()  # Need decision.id for history link

    # Apply if valid and non-keep
    if is_valid and sanitized and sanitized.get("action") != "keep":
        action = sanitized["action"]
        new_template = sanitized.get("new_template") or agent.strategy.template
        new_params = sanitized.get("new_params") or {}

        if action == "tune":
            merged = {**agent.strategy.params, **new_params}
            agent.strategy.params = merged
        elif action in ("switch", "blend"):
            agent.strategy.template = new_template
            # For switch/blend, fall back to defaults overlaid with any provided params
            template_cls = STRATEGY_REGISTRY[new_template]
            agent.strategy.params = {**template_cls.default_params, **new_params}

        agent.strategy.updated_at = datetime.utcnow()
        agent.strategy.version += 1

        # Refresh plain English
        agent.strategy.plain_english = _get_plain_english(
            agent.strategy.template, agent.strategy.params
        )

        # Log history
        db.add(StrategyHistory(
            agent_id=agent.id,
            template=agent.strategy.template,
            params=agent.strategy.params,
            triggered_by=triggered_by,
            decision_id=decision.id,
        ))

    agent.last_review_at = datetime.utcnow()
    db.commit()
    db.refresh(decision)
    return decision
