"""Cardinal risk_gate unit tests — must pass before anything else matters."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.risk.risk_gate import GateDecision, RiskConfig, RiskGate, TradePlan, risk_gate


def _plan(**kwargs) -> TradePlan:
    base = dict(
        symbol="BTC/USDC:USDC",
        side="long",
        entry=84000.0,
        stop=83160.0,   # -1%
        target=85680.0,  # +2% → R:R 2.0
        confidence=0.9,
    )
    base.update(kwargs)
    return TradePlan(**base)


def test_ok_qty_on_10k_account():
    gate = RiskGate(RiskConfig(max_risk=0.005, min_rr=2.0, state_path=":memory:"))
    # bypass file state
    gate._state_path = Path("/tmp/dm_test_risk_ok.json")
    if gate._state_path.exists():
        gate._state_path.unlink()
    gate._state = {"day": None, "day_pnl": 0.0, "killed": False, "consecutive_losses": 0, "consecutive_wins": 0, "open_risk": 0.0}

    result = gate.evaluate(_plan(), equity=10_000)
    assert result.allowed
    assert result.decision == GateDecision.ALLOW
    # risk $50 / $840 stop distance → qty ≈ 0.0595
    assert abs(result.qty - 0.0595) < 0.001
    assert abs(result.meta["stake"] - 5000) < 5


def test_bad_rr_blocked():
    gate = RiskGate(RiskConfig(min_rr=2.0, state_path="/tmp/dm_test_rr.json"))
    gate._state = {"day": None, "day_pnl": 0.0, "killed": False, "consecutive_losses": 0, "consecutive_wins": 0, "open_risk": 0.0}
    result = gate.evaluate(_plan(target=84500.0), equity=10_000)  # ~0.6R
    assert not result.allowed
    assert result.reason == "bad R:R"


def test_kill_switch():
    cfg = RiskConfig(max_day_loss=0.02, state_path="/tmp/dm_test_kill.json")
    gate = RiskGate(cfg)
    gate._state = {"day": "2099-01-01", "day_pnl": -250.0, "killed": False, "consecutive_losses": 0, "consecutive_wins": 0, "open_risk": 0.0}
    result = gate.evaluate(_plan(), equity=10_000, day_pnl=-250.0)
    assert not result.allowed
    assert result.reason == "kill switch"
    assert gate.killed


def test_zero_risk_blocked():
    qty, reason = risk_gate(
        {"symbol": "X", "side": "long", "entry": 100, "stop": 100, "target": 110, "confidence": 1},
        equity=10_000,
        config=RiskConfig(state_path="/tmp/dm_test_zero.json"),
    )
    assert qty is None
    assert reason == "zero risk"


def test_low_confidence_blocked():
    gate = RiskGate(RiskConfig(min_confidence=0.6, state_path="/tmp/dm_test_conf.json"))
    gate._state = {"day": None, "day_pnl": 0.0, "killed": False, "consecutive_losses": 0, "consecutive_wins": 0, "open_risk": 0.0}
    result = gate.evaluate(_plan(confidence=0.4), equity=10_000)
    assert not result.allowed
    assert result.reason == "low confidence"


def test_shrink_multiplier():
    gate = RiskGate(RiskConfig(max_risk=0.005, state_path="/tmp/dm_test_shrink.json"))
    gate._state = {"day": None, "day_pnl": 0.0, "killed": False, "consecutive_losses": 0, "consecutive_wins": 0, "open_risk": 0.0}
    full = gate.evaluate(_plan(), equity=10_000, size_multiplier=1.0)
    half = gate.evaluate(_plan(), equity=10_000, size_multiplier=0.5)
    assert full.allowed and half.allowed
    assert half.decision == GateDecision.SHRINK
    assert abs(half.qty - full.qty * 0.5) < 1e-6


def test_stake_from_stop():
    gate = RiskGate(RiskConfig(max_risk=0.005, state_path="/tmp/dm_test_stake.json"))
    gate._state = {"day": None, "day_pnl": 0.0, "killed": False, "consecutive_losses": 0, "consecutive_wins": 0, "open_risk": 0.0}
    stake = gate.stake_from_stop(10_000, entry=100.0, stop=99.0)  # 1% stop
    # risk $50 / 1% = $5000 stake
    assert stake is not None
    assert abs(stake - 5000) < 1.0


def test_long_stop_above_entry_blocked():
    gate = RiskGate(RiskConfig(state_path="/tmp/dm_test_dir.json"))
    gate._state = {"day": None, "day_pnl": 0.0, "killed": False, "consecutive_losses": 0, "consecutive_wins": 0, "open_risk": 0.0}
    result = gate.evaluate(_plan(stop=85000.0, target=86000.0), equity=10_000)
    assert not result.allowed
    assert "stop above" in result.reason


def test_rr_float_epsilon_allows_near_min():
    gate = RiskGate(RiskConfig(min_rr=2.0, state_path="/tmp/dm_test_eps.json"))
    gate._state = {"day": None, "day_pnl": 0.0, "killed": False, "consecutive_losses": 0, "consecutive_wins": 0, "open_risk": 0.0}
    # reward/risk == 1.99999998 should pass with epsilon
    entry, stop = 100.0, 99.0
    target = entry + 1.99999998 * (entry - stop)
    result = gate.evaluate(_plan(entry=entry, stop=stop, target=target), equity=10_000)
    assert result.allowed


def test_stake_cap_on_tight_stop():
    gate = RiskGate(RiskConfig(max_risk=0.005, max_stake_mult=2.0, state_path="/tmp/dm_test_cap.json"))
    gate._state = {"day": None, "day_pnl": 0.0, "killed": False, "consecutive_losses": 0, "consecutive_wins": 0, "open_risk": 0.0}
    # 0.05% stop → uncapped stake would be ~10× equity
    result = gate.evaluate(
        _plan(entry=100.0, stop=99.95, target=100.1),
        equity=10_000,
    )
    assert result.allowed
    assert result.meta["stake_capped"] is True
    assert result.meta["stake"] <= 10_000 * 2.0 + 1e-6
    assert result.decision == GateDecision.SHRINK
