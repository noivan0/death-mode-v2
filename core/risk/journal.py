"""Decision journal — every allow/block/shrink is logged.

Cardinal rule: log skips as carefully as fills. Compare average R,
max drawdown, trades/week after paper runs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import csv
import os


def _default_path() -> Path:
    root = Path(os.environ.get("PROJ_DIR", Path(__file__).resolve().parents[2]))
    return root / "user_data" / "logs" / "journal.csv"


class DecisionJournal:
    FIELDNAMES = [
        "ts",
        "symbol",
        "side",
        "action",
        "reason",
        "entry",
        "stop",
        "target",
        "rr",
        "qty",
        "stake",
        "risk_pct",
        "tilt_score",
        "size_mult",
        "day_pnl",
        "extra",
    ]

    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path else _default_path()

    def log(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: row.get(k, "") for k in self.FIELDNAMES}
        if not payload.get("ts"):
            payload["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_header = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDNAMES)
            if write_header:
                writer.writeheader()
            writer.writerow(payload)

    def summarize(self, limit: int = 500) -> dict[str, Any]:
        if not self.path.exists():
            return {"rows": 0, "allows": 0, "blocks": 0, "shrinks": 0}
        allows = blocks = shrinks = 0
        rr_vals: list[float] = []
        with self.path.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)[-limit:]
        for r in rows:
            action = (r.get("action") or "").lower()
            if action == "allow":
                allows += 1
            elif action == "block":
                blocks += 1
            elif action == "shrink":
                shrinks += 1
            try:
                rr = float(r.get("rr") or 0)
                if rr > 0:
                    rr_vals.append(rr)
            except ValueError:
                pass
        return {
            "rows": len(rows),
            "allows": allows,
            "blocks": blocks,
            "shrinks": shrinks,
            "avg_planned_rr": round(sum(rr_vals) / len(rr_vals), 2) if rr_vals else None,
            "path": str(self.path),
        }
