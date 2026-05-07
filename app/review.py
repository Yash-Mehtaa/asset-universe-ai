"""Self-review: ask Claude to evaluate the agent's recent performance and
optionally adjust its strategy. Output is validated against guardrails before
being applied. Mandatory change enforced if strategy unchanged for 30+ days."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.config import config
from app.db import Agent, Strategy, StrategyHistory, Decision, Trade, PerformanceSnapshot, WeeklyReviewSummary
from app.performance import performance_summary
from app.prompts import REVIEW_SYSTEM_PROMPT, REVIEW_USER_TEMPLATE, PLAIN_ENGLISH_PROMPT
from app.strategies import STRATEGY_REGISTRY, available_templates, get_strategy
from app.budget import log_usage, check_budget

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


def _days_since_strategy_change(agent: Agent) -> int:
    updated = agent.strategy.updated_at if agent.strategy else None
    if not updated:
        return 999
    return (datetime.utcnow() - updated).days


def _validate_proposal(
    proposal: dict[str, Any],
    current_strategy: Strategy,
    force_change: bool = False,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    action = proposal.get("action")
    if action not in ("keep", "tune", "switch", "blend"):
        return False, f"invalid action: {action}", None

    if action == "keep" and force_change:
        return False, "mandatory change required: strategy unchanged for 30+ days, keep not allowed", None

    if action == "keep":
        return True, None, {}

    new_template = proposal.get("new_template")
    new_params = proposal.get("new_params") or {}

    if action in ("switch", "blend"):
        if new_template not in available_templates():
            return False, f"unknown template: {new_template}", None

    if action == "tune":
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


def _get_plain_english(template: str, params: dict, db: Session, agent_name: str) -> str:
    try:
        msg = _client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": PLAIN_ENGLISH_PROMPT.format(template=template, params=params),
            }],
        )
        log_usage(db, "plain_english", msg.usage.input_tokens, msg.usage.output_tokens, agent_name)
        return msg.content[0].text.strip()
    except Exception as e:
        return f"{template} strategy ({e})"


def _build_weekly_summary(db: Session, all_decisions: list[dict], market_outlook: list[dict]) -> None:
    now = datetime.utcnow()
    next_review = now + timedelta(days=7)
    combined = "\n\n".join(
        f"[{d.get('agent')}] {d.get('reasoning', '')}" for d in all_decisions
    )
    changes = [
        {"agent": d.get("agent"), "action": d.get("action"), "changes": d.get("applied_changes")}
        for d in all_decisions if d.get("action") != "keep"
    ]
    db.query(WeeklyReviewSummary).delete()
    db.add(WeeklyReviewSummary(
        last_review_date=now,
        next_review_date=next_review,
        performance_analysis=combined or "No analysis available.",
        changes_made=changes or [],
        market_outlook=market_outlook or [],
    ))
    db.commit()


def _get_market_outlook(db: Session) -> list[dict]:
    ok, spend, cap = check_budget(db)
    if not ok:
        return []
    try:
        import re
        msg = _client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=800,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": """Search the web and identify the top 5 stocks or assets most likely to make significant price moves in the next 7 days.

Respond ONLY with a JSON array, no markdown:
[{"symbol": "NVDA", "direction": "up", "reason": "2 sentence reason referencing specific upcoming catalyst"}]

direction must be: up, down, or volatile"""}],
        )
        log_usage(db, "market_outlook", msg.usage.input_tokens, msg.usage.output_tokens)
        text = "\n".join(b.text for b in msg.content if hasattr(b, "text") and b.text)
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        print(f"[review] market outlook error: {e}")
    return []


def run_review(
    db: Session,
    agent: Agent,
    triggered_by: str = "scheduled",
) -> Decision:
    ok, spend, cap = check_budget(db)
    if not ok:
        decision = Decision(
            agent_id=agent.id,
            triggered_by=triggered_by,
            performance_summary={},
            action="keep",
            reasoning=f"Budget cap reached (${spend:.2f}/${cap:.2f}). Review skipped.",
            proposed_changes=None,
            applied_changes=None,
            rejected_reason="budget_cap",
        )
        db.add(decision)
        db.commit()
        return decision

    summary = performance_summary(agent, db)
    days_unchanged = _days_since_strategy_change(agent)
    force_change = days_unchanged >= config.MANDATORY_CHANGE_DAYS

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

    mandatory_note = ""
    if force_change:
        mandatory_note = (
            f"\n\nIMPORTANT: This strategy has been unchanged for {days_unchanged} days "
            f"(threshold: {config.MANDATORY_CHANGE_DAYS} days). "
            "You MUST propose a tune, switch, or blend. 'keep' is not allowed this cycle."
        )

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
    ) + mandatory_note

    response = _client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1500,
        system=REVIEW_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    log_usage(db, "review", response.usage.input_tokens, response.usage.output_tokens, agent.name)

    raw = response.content[0].text.strip()
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

    is_valid, reject_reason, sanitized = _validate_proposal(proposal, current_strat, force_change=force_change)

    if not is_valid and force_change and reject_reason and "mandatory" in reject_reason:
        retry_msg = user_msg + "\n\nYou responded with 'keep' but that is not allowed. You must choose tune, switch, or blend. Respond again with a valid JSON proposal."
        retry_resp = _client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1500,
            system=REVIEW_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": retry_msg}],
        )
        log_usage(db, "review_retry", retry_resp.usage.input_tokens, retry_resp.usage.output_tokens, agent.name)
        raw2 = retry_resp.content[0].text.strip()
        if raw2.startswith("```"):
            raw2 = raw2.split("```")[1]
            if raw2.startswith("json"):
                raw2 = raw2[4:]
            raw2 = raw2.strip()
        try:
            proposal = json.loads(raw2)
            is_valid, reject_reason, sanitized = _validate_proposal(proposal, current_strat, force_change=force_change)
        except Exception:
            pass

    decision = Decision(
        agent_id=agent.id,
        triggered_by=triggered_by,
        performance_summary=summary,
        action=proposal.get("action", "keep"),
        reasoning=proposal.get("reasoning", "") + (f"\n\n[Mandatory change: strategy was {days_unchanged} days old]" if force_change else ""),
        proposed_changes={
            "new_template": proposal.get("new_template"),
            "new_params": proposal.get("new_params"),
            "blend_with": proposal.get("blend_with"),
        },
        applied_changes=sanitized if is_valid else None,
        rejected_reason=reject_reason,
    )
    db.add(decision)
    db.flush()

    if is_valid and sanitized and sanitized.get("action") != "keep":
        action = sanitized["action"]
        new_template = sanitized.get("new_template") or agent.strategy.template
        new_params = sanitized.get("new_params") or {}

        if action == "tune":
            merged = {**agent.strategy.params, **new_params}
            agent.strategy.params = merged
        elif action in ("switch", "blend"):
            agent.strategy.template = new_template
            template_cls = STRATEGY_REGISTRY[new_template]
            agent.strategy.params = {**template_cls.default_params, **new_params}

        agent.strategy.updated_at = datetime.utcnow()
        agent.strategy.version += 1

        agent.strategy.plain_english = _get_plain_english(
            agent.strategy.template, agent.strategy.params, db, agent.name
        )

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
