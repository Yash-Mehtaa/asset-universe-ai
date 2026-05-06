"""Manually run one cycle for one or all agents. Useful for testing.

Usage:
  python -m app.run_once trade short_term
  python -m app.run_once review mid_term
  python -m app.run_once trade all
"""
from __future__ import annotations

import sys

from app.bootstrap import bootstrap
from app.db import Agent, SessionLocal
from app.agents import run_trading_cycle
from app.review import run_review


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m app.run_once <trade|review> <agent_name|all>")
        sys.exit(1)

    mode, target = sys.argv[1], sys.argv[2]
    bootstrap()

    db = SessionLocal()
    try:
        if target == "all":
            agents = db.query(Agent).all()
        else:
            agents = db.query(Agent).filter_by(name=target).all()
            if not agents:
                print(f"Unknown agent: {target}")
                sys.exit(1)

        for agent in agents:
            print(f"\n=== {mode} {agent.name} ===")
            if mode == "trade":
                result = run_trading_cycle(db, agent)
                print(result)
            elif mode == "review":
                d = run_review(db, agent, triggered_by="manual")
                print(f"action={d.action}")
                print(f"reasoning={d.reasoning}")
                print(f"applied={d.applied_changes}")
                print(f"rejected={d.rejected_reason}")
            else:
                print(f"Unknown mode: {mode}")
                sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
