#!/usr/bin/env python3
"""Collect OKX public data and run it through the Cardinal desk.

Starting equity and the account goal are bookkeeping. Quantity still comes
from RiskGate, then from the swap lot size when that lot fits the risk budget.

Usage:
  python scripts/collect_okx_desk.py
  python scripts/collect_okx_desk.py --equity 80 --goal 1000000
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.exchange.lighter_marketdata import analyze_bundle
from core.exchange.okx_public import DEFAULT_SWAP_IDS, OkxPublicClient
from core.risk.goal import growth_table
from core.risk.journal import DecisionJournal
from core.risk.risk_gate import RiskConfig, RiskGate
from core.risk.tilt_guard import TiltGuard
from core.strategy.desk import evaluate_symbol


def _pair_filename(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def save_ohlcv(directory: Path, pair: str, timeframe: str, candles) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_pair_filename(pair)}-{timeframe}-futures.json"
    rows = [[c.ts_ms, c.open, c.high, c.low, c.close, c.volume] for c in candles]
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _decision_row(decision, day_pnl: float) -> dict:
    plan = decision.plan
    fit = decision.fit
    return {
        "symbol": decision.pair,
        "side": plan.side if plan else "none",
        "action": decision.action,
        "reason": decision.reason,
        "entry": plan.entry if plan else "",
        "stop": plan.stop if plan else "",
        "target": plan.target if plan else "",
        "rr": decision.rr,
        "qty": fit.contracts if fit else "",
        "stake": fit.stake if fit else "",
        "risk_pct": "",
        "tilt_score": decision.tilt_score,
        "size_mult": decision.size_mult,
        "day_pnl": day_pnl,
        "extra": f"selected={int(decision.selected)} conf={decision.confidence}",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--equity", type=float, default=80.0)
    parser.add_argument("--goal", type=float, default=1_000_000.0)
    parser.add_argument("--candles", type=int, default=800)
    parser.add_argument("--max-open-trades", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.equity <= 0 or args.goal <= 0:
        print("equity and goal must be positive", file=sys.stderr)
        return 2

    client = OkxPublicClient()
    specs = client.instruments()
    by_id = {spec.inst_id: spec for spec in specs}
    missing = [inst_id for inst_id in DEFAULT_SWAP_IDS if inst_id not in by_id]
    gate = RiskGate(RiskConfig.from_env())
    tilt = TiltGuard()
    journal = DecisionJournal()
    data_dir = ROOT / "user_data" / "data" / "okx" / "futures"
    snapshot_path = ROOT / "user_data" / "logs" / "okx_desk_snapshot.json"

    fetched = []
    errors = [{"inst_id": inst_id, "error": "instrument missing"} for inst_id in missing]
    for inst_id in DEFAULT_SWAP_IDS:
        spec = by_id.get(inst_id)
        if spec is None:
            continue
        try:
            print(f"fetch {spec.inst_id}", flush=True)
            bundle = client.fetch_bundle(spec, candles_5m=args.candles)
            signal = analyze_bundle(bundle, min_rr=gate.config.min_rr)
            save_ohlcv(data_dir, spec.pair, "5m", bundle.candles_5m)
            save_ohlcv(data_dir, spec.pair, "1h", bundle.candles_1h)
            fetched.append({"spec": spec, "bundle": bundle, "signal": signal})
        except Exception as exc:
            errors.append({"inst_id": inst_id, "error": str(exc)})

    fetched.sort(key=lambda row: (row["signal"].side != "long", -row["signal"].confidence))
    open_risk = 0.0
    slots = args.max_open_trades
    decisions = []
    for row in fetched:
        spec = row["spec"]
        decision = evaluate_symbol(
            pair=spec.pair,
            signal=row["signal"],
            equity=args.equity,
            gate=gate,
            tilt=tilt,
            ct_val=spec.ct_val,
            min_sz=spec.min_sz,
            lot_sz=spec.lot_sz,
            open_risk=open_risk,
            slots_left=slots,
            select=True,
        )
        if decision.selected and decision.fit is not None:
            slots -= 1
            open_risk = decision.open_risk_after
        journal.log(_decision_row(decision, gate.day_pnl))
        decisions.append((row, decision))

    growth = growth_table(args.equity, args.goal, gate.config.max_risk, gate.config.min_rr)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "venue": "okx-public",
        "equity": args.equity,
        "goal": args.goal,
        "goal_does_not_size": True,
        "growth": growth,
        "open_risk": round(open_risk, 4),
        "results": [],
        "errors": errors,
        "journal": journal.summarize(),
    }
    for row, decision in decisions:
        spec = row["spec"]
        bundle = row["bundle"]
        signal = row["signal"]
        fit = decision.fit
        last = bundle.candles_5m[-1].close if bundle.candles_5m else None
        payload["results"].append(
            {
                "pair": spec.pair,
                "inst_id": spec.inst_id,
                "state": spec.state,
                "candles_5m": len(bundle.candles_5m),
                "candles_1h": len(bundle.candles_1h),
                "mark_5m": len(bundle.mark_5m),
                "fundings": len(bundle.fundings),
                "bids": len(bundle.bids),
                "asks": len(bundle.asks),
                "trades": len(bundle.trades),
                "last": last,
                "min_base": spec.min_sz * spec.ct_val,
                "ct_val": spec.ct_val,
                "signal": {
                    "side": signal.side,
                    "confidence": signal.confidence,
                    "entry": signal.entry,
                    "reasons": signal.reasons,
                    "ema_fast": signal.meta.get("ema_fast"),
                    "ema_slow": signal.meta.get("ema_slow"),
                    "vwap": signal.meta.get("vwap"),
                    "funding": signal.meta.get("funding"),
                    "book_imbalance": signal.meta.get("book_imbalance"),
                },
                "decision": {
                    "action": decision.action,
                    "reason": decision.reason,
                    "selected": decision.selected,
                    "entry": decision.plan.entry if decision.plan else None,
                    "stop": decision.plan.stop if decision.plan else None,
                    "target": decision.plan.target if decision.plan else None,
                    "rr": decision.rr,
                    "contracts": fit.contracts if fit else None,
                    "stake": fit.stake if fit else None,
                    "risk_usdt": fit.risk_amount if fit else None,
                },
            }
        )
    payload["ok"] = len(errors) == 0 and all(
        item["candles_5m"] >= 30 and item["bids"] > 0 and item["asks"] > 0
        for item in payload["results"]
    )
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if payload["ok"] else 2

    print("DEATH MODE v2 — OKX public desk")
    print(f"  {payload['ts']} equity={args.equity} goal={args.goal}")
    print(
        f"  growth: breakeven win rate {growth['breakeven_win_rate']} "
        f"win pays {growth['win_pays']} loss costs {growth['loss_costs']}"
    )
    for path_row in growth["paths"]:
        print(
            f"    win_rate={path_row['win_rate']:.2f} edge={path_row['edge_per_trade']} "
            f"trades_to_goal={path_row['trades']}"
        )
    print()
    for item in payload["results"]:
        dec = item["decision"]
        sig = item["signal"]
        print(
            f"{item['pair']} 5m={item['candles_5m']} book={item['bids']}/{item['asks']} "
            f"last={item['last']} side={sig['side']} conf={sig['confidence']}"
        )
        print(
            f"  {dec['action']} selected={dec['selected']} reason={dec['reason']} "
            f"entry={dec['entry']} stop={dec['stop']} target={dec['target']} "
            f"contracts={dec['contracts']} stake={dec['stake']} risk={dec['risk_usdt']}"
        )
    for err in errors:
        print(f"  error {err['inst_id']}: {err['error']}")
    print(f"snapshot: {snapshot_path}")
    print(f"overall: {'PASS' if payload['ok'] else 'FAIL'}")
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
