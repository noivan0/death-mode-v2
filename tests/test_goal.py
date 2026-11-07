"""Goal math stays outside the sizer."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.risk.goal import breakeven_win_rate, expected_growth, growth_table, trades_to_goal


def test_half_r_edge_reaches_goal():
    trades = trades_to_goal(80, 1_000_000, win_rate=0.5, risk=0.005, reward_risk=2.0)
    assert trades is not None
    assert trades > 1000
    edge = expected_growth(0.5, 0.005, 2.0)
    assert abs(edge - 0.0025) < 1e-12


def test_sub_breakeven_never_reaches_goal():
    assert breakeven_win_rate(2.0) == 1 / 3
    assert trades_to_goal(80, 1_000_000, win_rate=0.30, risk=0.005, reward_risk=2.0) is None


def test_growth_table_marks_unreachable_path():
    table = growth_table(80, 1_000_000, 0.005, 2.0, win_rates=(0.30, 0.50))
    assert table["multiple"] == 12500
    assert table["win_pays"] == 0.8
    assert table["loss_costs"] == 0.4
    by_rate = {row["win_rate"]: row for row in table["paths"]}
    assert by_rate[0.30]["trades"] is None
    assert by_rate[0.50]["trades"] > 0
    assert table["equity"] == 80
