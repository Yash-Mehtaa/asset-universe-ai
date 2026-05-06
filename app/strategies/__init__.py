"""Registry of all available strategy templates.

When Claude proposes "switch template", it picks from this dict.
To add a new strategy: implement it in this folder and register here.
"""
from app.strategies.base import Strategy
from app.strategies.momentum import MomentumStrategy
from app.strategies.trend_following import TrendFollowingStrategy
from app.strategies.risk_parity import RiskParityStrategy


STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    "momentum": MomentumStrategy,
    "trend_following": TrendFollowingStrategy,
    "risk_parity": RiskParityStrategy,
}


def get_strategy(template: str, params: dict | None = None) -> Strategy:
    """Build a strategy instance by name."""
    if template not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy template: {template}")
    return STRATEGY_REGISTRY[template](params=params)


def available_templates() -> list[str]:
    return list(STRATEGY_REGISTRY.keys())
