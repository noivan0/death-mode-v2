"""Cardinal Risk Gate — plain Python, zero LLM opinions.

Based on alpha404ai Cardinal desk pattern:
  - LLM never sets size
  - Size comes from stop distance × fixed equity risk
  - Kill switch on daily loss
  - Min reward:risk veto

Also encodes Martin Luk asymmetry (dubidubabap summary):
  - Fixed risk per trade (0.3–0.5% equity)
  - Tight stop → larger size at identical account risk
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional
import json
import os


class GateDecision(str, Enum):
    ALLOW = "allow"
    SHRINK = "shrink"
    BLOCK = "block"


@dataclass
class TradePlan:
    """Proposed trade. Size is NEVER set by the caller — gate computes it."""

    symbol: str
    side: str  # "long" | "short"
    entry: float
    stop: float
    target: float
    confidence: float = 1.0
    reason: str = ""

    def risk_distance(self) -> float:
        return abs(self.entry - self.stop)

    def reward_distance(self) -> float:
        return abs(self.target - self.entry)

    def reward_risk(self) -> float:
        risk = self.risk_distance()
        if risk <= 0:
            return 0.0
        return self.reward_distance() / risk


@dataclass
class GateResult:
    decision: GateDecision
    qty: Optional[float]
    reason: str
    risk_pct: float = 0.0
    rr: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision in (GateDecision.ALLOW, GateDecision.SHRINK) and self.qty is not None


@dataclass
class RiskConfig:
    """Tunable desk limits. Override via env or constructor."""

    max_risk: float = 0.005          # 0.5% equity per trade (Martin: 0.3–0.5%)
    max_day_loss: float = 0.02       # 2% daily kill switch
    min_rr: float = 2.0              # Cardinal MIN_RR
    min_confidence: float = 0.6
    shrink_factor: float = 0.5       # after a loss, next size × this
    max_open_risk: float = 0.02      # total open risk cap (2% equity)
    max_stake_mult: float = 2.0      # stake ≤ equity × this (caps ultra-tight stops)
    rr_epsilon: float = 1e-6         # float tolerance for R:R compare
    qty_precision: int = 6
    state_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "RiskConfig":
        return cls(
            max_risk=float(os.environ.get("DM_MAX_RISK", "0.005")),
            max_day_loss=float(os.environ.get("DM_MAX_DAY_LOSS", "0.02")),
            min_rr=float(os.environ.get("DM_MIN_RR", "2.0")),
            min_confidence=float(os.environ.get("DM_MIN_CONFIDENCE", "0.6")),
            shrink_factor=float(os.environ.get("DM_SHRINK_FACTOR", "0.5")),
            max_open_risk=float(os.environ.get("DM_MAX_OPEN_RISK", "0.02")),
            max_stake_mult=float(os.environ.get("DM_MAX_STAKE_MULT", "2.0")),
            state_path=os.environ.get("DM_RISK_STATE_PATH"),
        )


def _default_state_path() -> Path:
    root = Path(os.environ.get("PROJ_DIR", Path(__file__).resolve().parents[2]))
    return root / "user_data" / "risk_state.json"


class RiskGate:
    """Pure-Python veto. No opinions — only hard numbers."""

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig.from_env()
        self._state_path = Path(self.config.state_path) if self.config.state_path else _default_state_path()
        self._state = self._load_state()

    # ── persistence ──────────────────────────────────────────────
    def _load_state(self) -> dict:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "day": None,
            "day_pnl": 0.0,
            "killed": False,
            "consecutive_losses": 0,
            "consecutive_wins": 0,
            "open_risk": 0.0,
        }

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2))
        tmp.replace(self._state_path)

    def _rollover_day(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        if self._state.get("day") != day:
            self._state = {
                "day": day,
                "day_pnl": 0.0,
                "killed": False,
                "consecutive_losses": self._state.get("consecutive_losses", 0),
                "consecutive_wins": self._state.get("consecutive_wins", 0),
                "open_risk": self._state.get("open_risk", 0.0),
            }
            self._save_state()

    # ── public API ───────────────────────────────────────────────
    @property
    def killed(self) -> bool:
        self._rollover_day()
        return bool(self._state.get("killed"))

    @property
    def day_pnl(self) -> float:
        self._rollover_day()
        return float(self._state.get("day_pnl", 0.0))

    def record_pnl(self, pnl: float, equity: float) -> None:
        """Record closed-trade P&L (absolute currency units)."""
        self._rollover_day()
        self._state["day_pnl"] = float(self._state.get("day_pnl", 0.0)) + pnl
        if pnl < 0:
            self._state["consecutive_losses"] = int(self._state.get("consecutive_losses", 0)) + 1
            self._state["consecutive_wins"] = 0
        elif pnl > 0:
            self._state["consecutive_wins"] = int(self._state.get("consecutive_wins", 0)) + 1
            self._state["consecutive_losses"] = 0
        kill_limit = -self.config.max_day_loss * equity
        if self._state["day_pnl"] <= kill_limit:
            self._state["killed"] = True
        self._save_state()

    def reset_kill_switch(self) -> None:
        self._rollover_day()
        self._state["killed"] = False
        self._save_state()

    def evaluate(
        self,
        plan: TradePlan,
        equity: float,
        *,
        day_pnl: Optional[float] = None,
        size_multiplier: float = 1.0,
        open_risk: Optional[float] = None,
    ) -> GateResult:
        """Return allow/shrink/block + computed quantity.

        qty is in base-asset units assuming linear quote (entry price × qty ≈ notional).
        For futures stake_currency sizing, callers convert via custom_stake_amount.
        """
        self._rollover_day()
        cfg = self.config
        pnl = self.day_pnl if day_pnl is None else day_pnl
        open_r = float(self._state.get("open_risk", 0.0) if open_risk is None else open_risk)

        if equity <= 0:
            return GateResult(GateDecision.BLOCK, None, "zero equity")

        if self._state.get("killed") or pnl <= -cfg.max_day_loss * equity:
            self._state["killed"] = True
            self._save_state()
            return GateResult(GateDecision.BLOCK, None, "kill switch")

        risk = plan.risk_distance()
        reward = plan.reward_distance()
        if risk <= 0:
            return GateResult(GateDecision.BLOCK, None, "zero risk", rr=0.0)

        rr = reward / risk
        if rr + cfg.rr_epsilon < cfg.min_rr:
            return GateResult(GateDecision.BLOCK, None, "bad R:R", rr=rr)

        if plan.confidence < cfg.min_confidence:
            return GateResult(GateDecision.BLOCK, None, "low confidence", rr=rr)

        if plan.side not in ("long", "short"):
            return GateResult(GateDecision.BLOCK, None, "invalid side", rr=rr)

        # Directional sanity: stop must be on the loss side of entry
        if plan.side == "long" and plan.stop >= plan.entry:
            return GateResult(GateDecision.BLOCK, None, "stop above entry (long)", rr=rr)
        if plan.side == "short" and plan.stop <= plan.entry:
            return GateResult(GateDecision.BLOCK, None, "stop below entry (short)", rr=rr)

        if open_r >= cfg.max_open_risk * equity:
            return GateResult(GateDecision.BLOCK, None, "open risk cap", rr=rr)

        # Martin Luk: risk_amount fixed; stop distance sets size
        risk_amount = equity * cfg.max_risk * max(0.0, size_multiplier)
        # qty such that |entry-stop| * qty ≈ risk_amount  (linear contract approx)
        qty = risk_amount / risk

        decision = GateDecision.ALLOW
        reason = "ok"
        if size_multiplier < 1.0 - 1e-9:
            decision = GateDecision.SHRINK
            reason = f"shrunk x{size_multiplier:.2f}"

        qty = round(qty, cfg.qty_precision)
        if qty <= 0:
            return GateResult(GateDecision.BLOCK, None, "qty rounded to zero", rr=rr)

        stake = qty * plan.entry  # notional in quote
        max_stake = equity * cfg.max_stake_mult
        capped = False
        if max_stake > 0 and stake > max_stake:
            # Cap notional; effective $ risk shrinks with the same stop distance
            qty = round(max_stake / plan.entry, cfg.qty_precision)
            stake = qty * plan.entry
            capped = True
            reason = f"{reason}+stake_cap" if reason == "ok" else f"{reason}+stake_cap"
            if decision == GateDecision.ALLOW:
                decision = GateDecision.SHRINK

        return GateResult(
            decision=decision,
            qty=qty,
            reason=reason,
            risk_pct=cfg.max_risk * size_multiplier,
            rr=rr,
            meta={
                "stake": round(stake, 4),
                "risk_amount": round(risk_amount, 4),
                "stop_distance_pct": round(risk / plan.entry, 6) if plan.entry else 0.0,
                "stake_capped": capped,
            },
        )

    def stake_from_stop(
        self,
        equity: float,
        entry: float,
        stop: float,
        *,
        size_multiplier: float = 1.0,
    ) -> Optional[float]:
        """Freqtrade-friendly: return stake (quote currency) for fixed equity risk."""
        risk = abs(entry - stop)
        if equity <= 0 or entry <= 0 or risk <= 0:
            return None
        self._rollover_day()
        if self.killed:
            return None
        risk_amount = equity * self.config.max_risk * max(0.0, size_multiplier)
        stop_pct = risk / entry
        stake = risk_amount / stop_pct
        return round(stake, 4)


# Module-level convenience matching the Cardinal article snippet
def risk_gate(
    plan: dict,
    equity: float,
    day_pnl: float = 0.0,
    config: Optional[RiskConfig] = None,
) -> tuple[Optional[float], str]:
    """Cardinal-compatible helper: (qty|None, reason)."""
    gate = RiskGate(config)
    p = TradePlan(
        symbol=plan["symbol"],
        side=plan["side"],
        entry=float(plan["entry"]),
        stop=float(plan["stop"]),
        target=float(plan["target"]),
        confidence=float(plan.get("confidence", 1.0)),
    )
    result = gate.evaluate(p, equity, day_pnl=day_pnl)
    return (result.qty if result.allowed else None), result.reason
