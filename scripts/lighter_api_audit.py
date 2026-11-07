#!/usr/bin/env python3
"""DEATH MODE v2 — Live Lighter API compatibility audit.

Checks the 2026 API surface before autonomous / live trading:
  - chainId vs base URL
  - REST / + systemConfig + orderBookDetails
  - market_type / frozen / OI utilization
  - priceOracleInfo staleness
  - status page (best-effort)
  - L1 Priority TX cutover reminder

Usage:
  python scripts/lighter_api_audit.py
  python scripts/lighter_api_audit.py --json
  LIGHTER_CHAIN_ID=300 LIGHTER_BASE_URL=https://testnet.zklighter.elliot.ai \\
    python scripts/lighter_api_audit.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.exchange.lighter_compat import LighterApiClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Lighter API audit")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--symbols", nargs="*", default=["BTC", "ETH", "SOL", "BNB", "XRP"])
    args = parser.parse_args()

    client = LighterApiClient()
    health = client.audit()
    tradable = {m.symbol: m for m in client.tradable_perps(symbols=set(args.symbols))}

    payload = {
        "ok": health.ok,
        "network": health.network,
        "chain_id": health.chain_id,
        "rest_status": health.rest_status,
        "exchange_ok": health.exchange_ok,
        "status_page": health.status_page,
        "market_count": health.market_count,
        "perp_count": health.perp_count,
        "frozen_count": health.frozen_count,
        "high_oi_markets": health.high_oi_markets,
        "stale_oracle_symbols": health.stale_oracle_symbols,
        "warnings": health.warnings,
        "l1_cutover_pending": health.l1_cutover_pending,
        "meta": health.meta,
        "whitelist_check": {
            sym: (
                None
                if sym not in tradable
                else {
                    "market_id": tradable[sym].market_id,
                    "market_type": tradable[sym].market_type,
                    "mark": tradable[sym].mark_price,
                    "oi_usd": tradable[sym].open_interest_usd,
                    "oi_util": tradable[sym].oi_utilization,
                    "frozen": tradable[sym].is_frozen,
                    "min_base": tradable[sym].min_base_amount,
                }
            )
            for sym in args.symbols
        },
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if health.ok else 2

    print("💀 DEATH MODE v2 — Lighter API Audit")
    print(f"   network={health.network} chainId={health.chain_id}")
    print(f"   rest={health.meta.get('rest')}")
    print()
    icon = "✅" if health.ok else "❌"
    print(f"  {icon} overall ok={health.ok}  exchange_ok={health.exchange_ok} status={health.rest_status}")
    print(f"  📊 markets={health.market_count} perps={health.perp_count} frozen={health.frozen_count}")
    if health.status_page:
        print(f"  🛰️  status_page={health.status_page}")
    if health.high_oi_markets:
        print(f"  ⚠️  high OI util: {', '.join(health.high_oi_markets)}")
    if health.stale_oracle_symbols:
        print(f"  ⚠️  stale oracle: {', '.join(health.stale_oracle_symbols)}")
    for w in health.warnings:
        print(f"  ⚠️  {w}")
    print()
    print("  Whitelist:")
    for sym, info in payload["whitelist_check"].items():
        if info is None:
            print(f"    ❌ {sym}: not tradable (frozen / missing / filtered)")
        else:
            util = info["oi_util"]
            util_s = f"{util*100:.1f}%" if util is not None else "n/a"
            print(
                f"    ✅ {sym}: id={info['market_id']} mark={info['mark']} "
                f"oi_usd={info['oi_usd']:.0f} oi_util={util_s}"
            )
    print()
    print("  SDK pin: lighter-sdk>=1.1.8 (int64 market IDs + WS shutdown)")
    return 0 if health.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
