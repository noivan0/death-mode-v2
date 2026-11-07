"""TiltGuard unit tests."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.risk.tilt_guard import FillEvent, TiltAction, TiltConfig, TiltGuard


def _guard(path: str) -> TiltGuard:
    p = Path(path)
    if p.exists():
        p.unlink()
    return TiltGuard(TiltConfig(
        state_path=path,
        tilt_block_threshold=70,
        cooldown_minutes=40,
        loss_window_minutes=90,
        losses_for_cooldown=2,
        asset_stop_cooldown_hours=3.0,
        shrink_after_loss=0.5,
        wins_to_restore_size=2,
    ))


def test_calm_allow():
    g = _guard("/tmp/dm_tilt_calm.json")
    v = g.check("BTC/USDC:USDC")
    assert v.action == TiltAction.ALLOW
    assert v.size_multiplier == 1.0


def test_two_losses_trigger_cooldown():
    g = _guard("/tmp/dm_tilt_cd.json")
    now = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
    g.record_fill(FillEvent("ETH/USDC:USDC", "long", -10.0, True, now))
    g.record_fill(FillEvent("ETH/USDC:USDC", "long", -12.0, True, now + timedelta(minutes=5)))
    v = g.check("SOL/USDC:USDC", now=now + timedelta(minutes=6))
    assert v.action == TiltAction.BLOCK
    assert "cooldown" in v.reason


def test_asset_stop_blocks_same_pair():
    g = _guard("/tmp/dm_tilt_asset.json")
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    g.record_fill(FillEvent("BTC/USDC:USDC", "long", -5.0, True, now))
    # Clear global cooldown by resetting if set — asset block should still apply
    g._state["cooldown_until"] = None
    g._save_state()
    v = g.check("BTC/USDC:USDC", now=now + timedelta(hours=1))
    assert v.action == TiltAction.BLOCK
    assert "asset stop" in v.reason
    # Different pair OK (may shrink due to consecutive loss)
    v2 = g.check("ETH/USDC:USDC", now=now + timedelta(hours=1))
    assert v2.action in (TiltAction.ALLOW, TiltAction.SHRINK)


def test_shrink_after_one_loss():
    g = _guard("/tmp/dm_tilt_shrink.json")
    now = datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc)
    g.record_fill(FillEvent("BTC/USDC:USDC", "long", -3.0, False, now))
    g._state["cooldown_until"] = None
    g._state["asset_block_until"] = {}
    g._save_state()
    v = g.check("ETH/USDC:USDC", now=now + timedelta(minutes=1))
    assert v.action == TiltAction.SHRINK
    assert v.size_multiplier == 0.5


def test_two_wins_restore_full_size():
    g = _guard("/tmp/dm_tilt_restore.json")
    now = datetime(2026, 11, 7, 14, 0, tzinfo=timezone.utc)
    g.record_fill(FillEvent("BTC/USDC:USDC", "long", -3.0, False, now))
    g.record_fill(FillEvent("ETH/USDC:USDC", "long", 5.0, False, now + timedelta(minutes=1)))
    g.record_fill(FillEvent("SOL/USDC:USDC", "long", 6.0, False, now + timedelta(minutes=2)))
    g._state["cooldown_until"] = None
    g._state["asset_block_until"] = {}
    g._save_state()
    v = g.check("BTC/USDC:USDC", now=now + timedelta(hours=4))
    assert v.action == TiltAction.ALLOW
    assert v.size_multiplier == 1.0
