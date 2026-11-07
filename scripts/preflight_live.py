#!/usr/bin/env python3
"""Preflight before live Lighter futures trading.

Checks:
  1) .env credentials present
  2) account collateral / assets
  3) jurisdiction sendTx probe (detect code 20558)
  4) Cardinal risk env sanity for small equity

Exit codes:
  0 = ready
  2 = config/credentials missing
  3 = collateral too low
  4 = restricted jurisdiction (cannot send orders from this host)
  5 = other send error
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.env_loader import load_env


MIN_COLLATERAL = float(os.environ.get("DM_MIN_LIVE_COLLATERAL", "20"))


async def account_info() -> dict:
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
        "index": a.get("index"),
        "collateral": float(a.get("collateral") or 0),
        "available_balance": float(a.get("available_balance") or 0),
        "LIT": float((assets.get("LIT") or {}).get("balance") or 0),
        "USDC_margin": float((assets.get("USDC") or {}).get("margin_balance") or 0),
        "pending_order_count": a.get("pending_order_count"),
    }
    await client.close()
    return out


async def jurisdiction_probe() -> tuple[str, str]:
    """Return (status, detail). status: ok|restricted|error"""
    from lighter import SignerClient

    idx = int(os.environ["LIGHTER_ACCOUNT_INDEX"])
    key_idx = int(os.environ["LIGHTER_API_KEY_INDEX"])
    sc = SignerClient(
        url=os.environ.get("LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai"),
        account_index=idx,
        api_private_keys={key_idx: os.environ["LIGHTER_PRIVATE_KEY"]},
        chain_id=int(os.environ.get("LIGHTER_CHAIN_ID", "304")),
    )
    try:
        # Tiny ETH perp buy — may fail for size/margin, but geo returns 20558 first.
        _tx, _resp, err = await sc.create_market_order_limited_slippage(
            market_index=0,
            client_order_index=int(time.time()) % 1_000_000_000,
            base_amount=1,  # intentionally tiny
            max_slippage=0.02,
            is_ask=False,
            api_key_index=key_idx,
        )
        detail = str(err) if err else "send_accepted"
        if err and "20558" in detail:
            return "restricted", detail
        if err and "jurisdiction" in detail.lower():
            return "restricted", detail
        # Any non-geo error means the host CAN reach sendTx path
        return "ok", detail or "ok"
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        if "20558" in msg or "jurisdiction" in msg.lower():
            return "restricted", msg
        return "error", msg
    finally:
        if hasattr(sc, "close"):
            c = sc.close()
            if asyncio.iscoroutine(c):
                await c


async def main() -> int:
    load_env(ROOT, override=True)
    for req in ("LIGHTER_ACCOUNT_INDEX", "LIGHTER_API_KEY_INDEX", "LIGHTER_PRIVATE_KEY"):
        if not os.environ.get(req):
            print(json.dumps({"ok": False, "reason": f"missing {req}"}))
            return 2

    info = await account_info()
    status, detail = await jurisdiction_probe()
    payload = {
        "ok": status == "ok" and info["collateral"] >= MIN_COLLATERAL,
        "account": info,
        "jurisdiction": status,
        "jurisdiction_detail": detail[:300],
        "min_collateral": MIN_COLLATERAL,
        "risk_env": {
            "DM_MAX_RISK": os.environ.get("DM_MAX_RISK"),
            "DM_MAX_DAY_LOSS": os.environ.get("DM_MAX_DAY_LOSS"),
            "DM_MIN_RR": os.environ.get("DM_MIN_RR"),
            "DM_MAX_STAKE_MULT": os.environ.get("DM_MAX_STAKE_MULT"),
            "DM_STRATEGY": os.environ.get("DM_STRATEGY", "DeathModeV2_Cardinal"),
        },
    }
    print(json.dumps(payload, indent=2))

    if status == "restricted":
        return 4
    if info["collateral"] < MIN_COLLATERAL:
        return 3
    if status != "ok":
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
