#!/usr/bin/env python3
"""Sell spot LIT → USDC on Lighter (market_id=2049).

Requires .env credentials. Live order — not dry-run.

Usage:
  python scripts/sell_lit_spot.py              # sell all (floored to size decimals)
  python scripts/sell_lit_spot.py --amount 10  # sell 10 LIT
  python scripts/sell_lit_spot.py --dry-check  # balances + book only, no order
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.env_loader import load_env

MARKET_ID = 2049  # LIT/USDC spot
SIZE_DECIMALS = 2
DEFAULT_SLIPPAGE = 0.015


async def snapshot(label: str) -> dict:
    from lighter import ApiClient, Configuration, AccountApi

    conf = Configuration(
        host=os.environ.get("LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai")
    )
    client = ApiClient(configuration=conf)
    api = AccountApi(client)
    acc = await api.account(by="index", value=os.environ["LIGHTER_ACCOUNT_INDEX"])
    a = (acc.to_dict().get("accounts") or [acc.to_dict()])[0]
    assets = {x["symbol"]: x for x in (a.get("assets") or [])}
    out = {
        "label": label,
        "collateral": a.get("collateral"),
        "available_balance": a.get("available_balance"),
        "total_asset_value": a.get("total_asset_value"),
        "LIT": assets.get("LIT"),
        "USDC": assets.get("USDC"),
    }
    await client.close()
    return out


def floor_amount(human: float, decimals: int = SIZE_DECIMALS) -> float:
    scale = 10**decimals
    return math.floor(human * scale) / scale


def to_base_amount(human: float, decimals: int = SIZE_DECIMALS) -> int:
    return int(round(human * (10**decimals)))


async def main() -> int:
    load_env(ROOT, override=True)
    parser = argparse.ArgumentParser(description="Sell spot LIT for USDC")
    parser.add_argument("--amount", type=float, default=None, help="LIT amount (default: all)")
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE)
    parser.add_argument("--dry-check", action="store_true", help="No order, print balances only")
    args = parser.parse_args()

    for req in ("LIGHTER_ACCOUNT_INDEX", "LIGHTER_API_KEY_INDEX", "LIGHTER_PRIVATE_KEY"):
        if not os.environ.get(req):
            print(f"missing {req} in .env", file=sys.stderr)
            return 2

    before = await snapshot("before")
    lit_bal = float((before.get("LIT") or {}).get("balance") or 0)
    sell = floor_amount(args.amount if args.amount is not None else lit_bal)
    if sell < 1.0:
        print(f"amount {sell} below min_base_amount=1.00")
        print(json.dumps(before, indent=2))
        return 1
    if sell > lit_bal + 1e-12:
        print(f"amount {sell} > balance {lit_bal}")
        return 1

    base_amount = to_base_amount(sell)
    print(
        json.dumps(
            {
                "market_id": MARKET_ID,
                "sell_lit": sell,
                "base_amount": base_amount,
                "slippage": args.slippage,
                "before": before,
            },
            indent=2,
        )
    )
    if args.dry_check:
        return 0

    from lighter import SignerClient

    idx = int(os.environ["LIGHTER_ACCOUNT_INDEX"])
    key_idx = int(os.environ["LIGHTER_API_KEY_INDEX"])
    sc = SignerClient(
        url=os.environ.get("LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai"),
        account_index=idx,
        api_private_keys={key_idx: os.environ["LIGHTER_PRIVATE_KEY"]},
        chain_id=int(os.environ.get("LIGHTER_CHAIN_ID", "304")),
    )
    coi = int(time.time() * 1000) % 1_000_000_000
    tx, resp, err = await sc.create_market_order_limited_slippage(
        market_index=MARKET_ID,
        client_order_index=coi,
        base_amount=base_amount,
        max_slippage=args.slippage,
        is_ask=True,
        api_key_index=key_idx,
    )
    if err:
        print("ORDER_ERR", err)
        after = await snapshot("after")
        print(json.dumps(after, indent=2))
        return 3

    rd = resp.to_dict() if hasattr(resp, "to_dict") else str(resp)
    print("ORDER_OK", json.dumps(rd, default=str)[:1000])
    await asyncio.sleep(3)
    after = await snapshot("after")
    print(json.dumps(after, indent=2))
    out = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "sell_lit": sell,
        "base_amount": base_amount,
        "resp": rd,
        "before": before,
        "after": after,
    }
    Path("/opt/cursor/artifacts/lit_spot_sell.json").write_text(
        json.dumps(out, indent=2, default=str)
    )
    if hasattr(sc, "close"):
        c = sc.close()
        if asyncio.iscoroutine(c):
            await c
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
