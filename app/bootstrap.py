"""One-shot bootstrap: create the three agents with starting strategies.

Run with: python -m app.bootstrap
Idempotent — safe to re-run; it skips agents that already exist.
"""
from app.config import config
from app.db import Agent, SessionLocal, Strategy, StrategyHistory, init_db
from app.strategies import STRATEGY_REGISTRY


AGENT_DEFS = [
    {
        "name": "short_term",
        "horizon": "short",
        "starting_template": "momentum",
    },
    {
        "name": "mid_term",
        "horizon": "mid",
        "starting_template": "trend_following",
    },
    {
        "name": "long_term",
        "horizon": "long",
        "starting_template": "risk_parity",
    },
]


def bootstrap():
    init_db()
    db = SessionLocal()
    try:
        for spec in AGENT_DEFS:
            existing = db.query(Agent).filter_by(name=spec["name"]).first()
            if existing:
                print(f"[skip] agent '{spec['name']}' already exists")
                continue

            agent = Agent(
                name=spec["name"],
                horizon=spec["horizon"],
                cash=config.STARTING_CAPITAL,
                starting_capital=config.STARTING_CAPITAL,
            )
            db.add(agent)
            db.flush()  # Need agent.id

            template = spec["starting_template"]
            template_cls = STRATEGY_REGISTRY[template]
            params = dict(template_cls.default_params)

            strat = Strategy(
                agent_id=agent.id,
                template=template,
                params=params,
                plain_english=f"Starting {template} strategy with default parameters.",
                version=1,
            )
            db.add(strat)

            db.add(StrategyHistory(
                agent_id=agent.id,
                template=template,
                params=params,
                triggered_by="initial",
            ))
            print(f"[ok] created '{spec['name']}' with {template}")

        db.commit()
        print("Bootstrap complete.")
    finally:
        db.close()


if __name__ == "__main__":
    bootstrap()
