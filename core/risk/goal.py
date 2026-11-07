"""Compounding math for a fixed-risk book.

The goal never changes position size. RiskGate still owns quantity.
"""
from __future__ import annotations

import math
from typing import Optional


def expected_growth(win_rate: float, risk: float, reward_risk: float) -> float:
    """Expected equity change per trade as a fraction.

    A win pays ``risk * reward_risk``. A loss costs ``risk``.
    """
    if not 0.0 <= win_rate <= 1.0:
        raise ValueError("win_rate must be between 0 and 1")
    if risk < 0 or reward_risk < 0:
        raise ValueError("risk and reward_risk must be non-negative")
    return win_rate * risk * reward_risk - (1.0 - win_rate) * risk


def breakeven_win_rate(reward_risk: float) -> float:
    if reward_risk <= 0:
        return 1.0
    return 1.0 / (1.0 + reward_risk)


def trades_to_goal(
    equity: float,
    goal: float,
    win_rate: float,
    risk: float,
    reward_risk: float,
) -> Optional[int]:
    """How many trades the expected edge needs to grow equity to goal.

    Returns None when the edge is not positive. Returns 0 when equity is
    already at the goal.
    """
    if equity <= 0 or goal <= 0:
        raise ValueError("equity and goal must be positive")
    if equity >= goal:
        return 0
    edge = expected_growth(win_rate, risk, reward_risk)
    if edge <= 0:
        return None
    needed = math.log(goal / equity) / math.log(1.0 + edge)
    return math.ceil(needed)


def growth_table(
    equity: float,
    goal: float,
    risk: float,
    reward_risk: float,
    win_rates: tuple[float, ...] = (0.35, 0.40, 0.50, 0.55),
) -> dict:
    rows = []
    for rate in win_rates:
        edge = expected_growth(rate, risk, reward_risk)
        rows.append(
            {
                "win_rate": rate,
                "edge_per_trade": round(edge, 6),
                "trades": trades_to_goal(equity, goal, rate, risk, reward_risk),
            }
        )
    return {
        "equity": equity,
        "goal": goal,
        "multiple": round(goal / equity, 4) if equity else None,
        "risk": risk,
        "reward_risk": reward_risk,
        "breakeven_win_rate": round(breakeven_win_rate(reward_risk), 4),
        "win_pays": round(equity * risk * reward_risk, 4),
        "loss_costs": round(equity * risk, 4),
        "paths": rows,
    }
