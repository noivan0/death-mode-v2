#!/usr/bin/env python3
"""Read-only autonomy monitor for Lighter live account.

Safe to run from any jurisdiction (public account API + local journal/risk state).
Does not place orders.

Usage:
  python scripts/autonomy_monitor.py --once
  python scripts/autonomy_monitor.py --loop --interval 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.env_loader import load_env


async def fetch_account() -> dict:
    from lighter import ApiClient, Configuration, AccountApi

    conf = Configuration(
        host=os.environ.get("LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai")
    )
    client = ApiClient(configuration=conf)
    api = AccountApi(client)
    acc = await api.account(by="index", value=os.environ["LIGHTER_ACCOUNT_INDEX"])
    a = (acc.to_dict().get("accounts") or [acc.to_dict()])[0]
    assets = {x["symbol"]: x for x in (a.get("assets") or [])}
    positions = [
        {
            "symbol": p.get("symbol"),
            "market_id": p.get("market_id"),
            "position": p.get("position"),
            "sign": p.get("sign"),
            "position_value": p.get("position_value"),
            "unrealized_pnl": p.get("unrealized_pnl"),
            "avg_entry_price": p.get("avg_entry_price"),
        }
        for p in (a.get("positions") or [])
        if float(p.get("position") or 0) != 0
    ]
    out = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "index": a.get("index"),
        "collateral": a.get("collateral"),
        "available_balance": a.get("available_balance"),
        "total_asset_value": a.get("total_asset_value"),
        "pending_order_count": a.get("pending_order_count"),
        "LIT": (assets.get("LIT") or {}).get("balance"),
        "USDC_margin": (assets.get("USDC") or {}).get("margin_balance"),
        "positions": positions,
    }
    await client.close()
    return out


def local_bot_status() -> dict:
    import subprocess

    def _ps(pattern: str) -> str:
        raw = subprocess.getoutput(f"pgrep -af '{pattern}' || true")
        lines = [
            ln
            for ln in raw.splitlines()
            if ln.strip()
            and "pgrep" not in ln
            and "autonomy_monitor" not in ln
        ]
        return "\n".join(lines)

    paper = _ps("freqtrade trade --config config/lighter_paper_cardinal.json")
    live = _ps("freqtrade trade --config config/lighter_live_cardinal.json")
    return {
        "paper_running": bool(paper.strip()),
        "live_running": bool(live.strip()),
        "paper_ps": paper.strip()[:200],
        "live_ps": live.strip()[:200],
    }


def risk_snapshot() -> dict:
    risk = ROOT / "user_data" / "risk_state.json"
    tilt = ROOT / "user_data" / "tilt_state.json"
    journal = ROOT / "user_data" / "logs" / "journal.csv"
    out = {}
    if risk.exists():
        try:
            out["risk"] = json.loads(risk.read_text())
        except Exception as e:
            out["risk_err"] = str(e)
    if tilt.exists():
        try:
            out["tilt"] = json.loads(tilt.read_text())
        except Exception as e:
            out["tilt_err"] = str(e)
    if journal.exists():
        lines = journal.read_text().strip().splitlines()
        out["journal_rows"] = max(0, len(lines) - 1)
    return out


async def once() -> dict:
    load_env(ROOT, override=True)
    report = {
        "account": await fetch_account(),
        "bots": local_bot_status(),
        "risk": risk_snapshot(),
    }
    out_dir = Path("/opt/cursor/artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "autonomy_monitor.json"
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return report


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()
    if args.loop:
        while True:
            await once()
            time.sleep(args.interval)
    else:
        await once()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
