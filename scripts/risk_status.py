#!/usr/bin/env python3
"""DEATH MODE v2 — Cardinal risk / tilt status for autonomous ops.

Usage:
  python scripts/risk_status.py
  python scripts/risk_status.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.risk import DecisionJournal, RiskGate, TiltGuard


def main() -> int:
    parser = argparse.ArgumentParser(description="Cardinal risk status")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    gate = RiskGate()
    tilt = TiltGuard()
    journal = DecisionJournal()
    summary = journal.summarize()
    score = tilt.tilt_score()

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "killed": gate.killed,
        "day_pnl": gate.day_pnl,
        "max_risk": gate.config.max_risk,
        "max_day_loss": gate.config.max_day_loss,
        "min_rr": gate.config.min_rr,
        "tilt_score": score,
        "journal": summary,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("💀 DEATH MODE v2 — Cardinal Risk Status")
    print(f"   Time: {payload['ts']}")
    print()
    kill_icon = "🛑" if payload["killed"] else "✅"
    print(f"  {kill_icon} Kill switch : {'ON' if payload['killed'] else 'OFF'}")
    print(f"  📉 Day PnL     : {payload['day_pnl']:.4f}")
    print(f"  🎯 Max risk    : {payload['max_risk']*100:.2f}% / trade")
    print(f"  🧱 Day loss cap: {payload['max_day_loss']*100:.2f}%")
    print(f"  📐 Min R:R     : {payload['min_rr']}")
    print(f"  🌡️  Tilt score  : {payload['tilt_score']}/100")
    print()
    print("  Journal:")
    print(f"    rows={summary.get('rows')} allow={summary.get('allows')} "
          f"block={summary.get('blocks')} shrink={summary.get('shrinks')} "
          f"avg_rr={summary.get('avg_planned_rr')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
