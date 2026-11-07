#!/usr/bin/env python3
"""Live verification: Lighter market data → signals → Cardinal RiskGate.

Pulls real trading data from Lighter public API and exercises the same
decision path DeathModeV2_Cardinal uses (pullback preference + risk_gate).

Usage:
  python scripts/verify_lighter_trading_data.py
  python scripts/verify_lighter_trading_data.py --symbols BTC ETH SOL --json
  python scripts/verify_lighter_trading_data.py --equity 10000
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.exchange.lighter_marketdata import LighterMarketData, analyze_bundle
from core.risk import DecisionJournal, RiskConfig, RiskGate, TiltGuard, TradePlan


def verify_symbol(md: LighterMarketData, symbol: str, equity: float, gate: RiskGate, tilt: TiltGuard, journal: DecisionJournal) -> dict:
    bundle = md.fetch_bundle(symbol)
    signal = analyze_bundle(bundle)
    tilt_v = tilt.check(f"{symbol}/USDC:USDC")

    gate_result = None
    if signal.side in ("long", "short") and signal.entry > 0 and signal.stop > 0:
        plan = TradePlan(
            symbol=f"{symbol}/USDC:USDC",
            side=signal.side,
            entry=signal.entry,
            stop=signal.stop,
            target=signal.target,
            confidence=signal.confidence,
            reason=";".join(signal.reasons),
        )
        size_mult = tilt_v.size_multiplier if tilt_v.action.value != "block" else 0.0
        if size_mult <= 0:
            gate_result = {"decision": "block", "reason": f"tilt:{tilt_v.reason}", "qty": None}
            journal.log({
                "symbol": plan.symbol, "side": plan.side, "action": "block",
                "reason": gate_result["reason"], "entry": plan.entry, "stop": plan.stop,
                "target": plan.target, "rr": plan.reward_risk(), "tilt_score": tilt_v.tilt_score,
                "size_mult": 0, "day_pnl": gate.day_pnl,
            })
        else:
            result = gate.evaluate(plan, equity, size_multiplier=size_mult)
            gate_result = {
                "decision": result.decision.value,
                "reason": result.reason,
                "qty": result.qty,
                "rr": result.rr,
                "stake": (result.meta or {}).get("stake"),
                "risk_pct": result.risk_pct,
            }
            journal.log({
                "symbol": plan.symbol, "side": plan.side, "action": result.decision.value,
                "reason": result.reason, "entry": plan.entry, "stop": plan.stop,
                "target": plan.target, "rr": result.rr, "qty": result.qty,
                "stake": gate_result.get("stake"), "risk_pct": result.risk_pct,
                "tilt_score": tilt_v.tilt_score, "size_mult": size_mult,
                "day_pnl": gate.day_pnl, "extra": ";".join(signal.reasons),
            })
    else:
        journal.log({
            "symbol": f"{symbol}/USDC:USDC", "side": signal.side or "none",
            "action": "block", "reason": "no_signal:" + ";".join(signal.reasons),
            "entry": signal.entry, "stop": signal.stop, "target": signal.target,
            "rr": 0, "tilt_score": tilt_v.tilt_score, "size_mult": tilt_v.size_multiplier,
            "day_pnl": gate.day_pnl,
        })

    d = bundle.details
    return {
        "symbol": symbol,
        "market_id": bundle.market_id,
        "data": {
            "candles_5m": len(bundle.candles_5m),
            "candles_1h": len(bundle.candles_1h),
            "mark_5m": len(bundle.mark_5m),
            "fundings": len(bundle.fundings),
            "bids": len(bundle.bids),
            "asks": len(bundle.asks),
            "trades": len(bundle.trades),
            "last_price": bundle.candles_5m[-1].close if bundle.candles_5m else None,
            "mark_price": bundle.mark_5m[-1].close if bundle.mark_5m else None,
            "oi_usd": d.open_interest_usd if d else None,
            "oi_util": d.oi_utilization if d else None,
            "frozen": d.is_frozen if d else None,
            "funding_rate": signal.meta.get("funding"),
            "oracle_stale": bool(
                (bundle.oracle.get("index") or {}).get("stale")
                or (bundle.oracle.get("mark") or {}).get("stale")
            ),
        },
        "signal": {
            "side": signal.side,
            "entry": signal.entry,
            "stop": signal.stop,
            "target": signal.target,
            "confidence": signal.confidence,
            "reasons": signal.reasons,
            "meta": {
                k: signal.meta.get(k)
                for k in ("atr", "ema_fast", "ema_slow", "vwap", "book_imbalance", "rr")
            },
        },
        "tilt": {
            "action": tilt_v.action.value,
            "score": tilt_v.tilt_score,
            "reason": tilt_v.reason,
            "size_mult": tilt_v.size_multiplier,
        },
        "gate": gate_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["BTC", "ETH", "SOL"])
    parser.add_argument("--equity", type=float, default=10_000.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    md = LighterMarketData()
    md.refresh_symbol_map()
    gate = RiskGate(RiskConfig.from_env())
    tilt = TiltGuard()
    journal = DecisionJournal()

    rows = []
    errors = []
    for sym in args.symbols:
        try:
            rows.append(verify_symbol(md, sym, args.equity, gate, tilt, journal))
        except Exception as e:
            errors.append({"symbol": sym, "error": str(e)})

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "equity": args.equity,
        "network": md.endpoints.name,
        "chain_id": md.endpoints.chain_id,
        "results": rows,
        "errors": errors,
        "journal": journal.summarize(),
        "ok": len(errors) == 0 and all(
            (r["data"]["candles_5m"] or 0) >= 30
            and (r["data"]["bids"] or 0) > 0
            and (r["data"]["asks"] or 0) > 0
            for r in rows
        ),
    }

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0 if payload["ok"] else 2

    print("💀 DEATH MODE v2 — Lighter Trading Data Verification")
    print(f"   {payload['ts']}  network={payload['network']} chainId={payload['chain_id']} equity={args.equity}")
    print()
    for r in rows:
        d, s, g = r["data"], r["signal"], r["gate"]
        print(f"=== {r['symbol']} (market_id={r['market_id']}) ===")
        print(
            f"  data: 5m={d['candles_5m']} 1h={d['candles_1h']} mark5m={d['mark_5m']} "
            f"fundings={d['fundings']} book={d['bids']}/{d['asks']} trades={d['trades']}"
        )
        print(
            f"  px={d['last_price']} mark={d['mark_price']} oi_usd={d['oi_usd']} "
            f"oi_util={d['oi_util']} funding={d['funding_rate']} oracle_stale={d['oracle_stale']}"
        )
        print(
            f"  signal: side={s['side']} conf={s['confidence']} "
            f"entry={s['entry']} stop={s['stop']} target={s['target']}"
        )
        print(f"  reasons: {', '.join(s['reasons'])}")
        print(f"  tilt: {r['tilt']['action']} score={r['tilt']['score']} mult={r['tilt']['size_mult']}")
        if g:
            print(
                f"  gate: {g.get('decision')} reason={g.get('reason')} "
                f"rr={g.get('rr')} qty={g.get('qty')} stake={g.get('stake')}"
            )
        else:
            print("  gate: skipped (no actionable signal)")
        print()

    for e in errors:
        print(f"  ❌ {e['symbol']}: {e['error']}")

    j = payload["journal"]
    print(
        f"Journal: allow={j.get('allows')} block={j.get('blocks')} shrink={j.get('shrinks')} "
        f"avg_rr={j.get('avg_planned_rr')}"
    )
    print(f"Overall: {'✅ PASS' if payload['ok'] else '❌ FAIL'}")
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
