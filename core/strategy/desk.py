"""Turn a market signal into a Cardinal order ticket.

Size stays on RiskGate. Exchange lot size can only shrink a ticket, and only
when the smaller order still fits inside the dollar risk budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import math

from ..exchange.lighter_marketdata import SignalReport
from ..risk.risk_gate import GateDecision, RiskGate, TradePlan
from ..risk.tilt_guard import TiltGuard

# DeathModeV2_Cardinal.stoploss
CARDINAL_STOP_PCT = 0.03
# Freqtrade futures stake is collateral. Position notional = collateral × leverage.
CARDINAL_LEVERAGE = 3.0
GOAL_EQUITY = 1_000_000.0


def collateral_for_notional(notional: float, leverage: float) -> float:
    """Convert a notional stake into the collateral Freqtrade will send.

    Freqtrade computes base amount as ``stake / price * leverage``. The risk
    gate's stake is notional, so the value returned to Freqtrade is notional
    divided by leverage.
    """
    lev = float(leverage) if leverage and float(leverage) > 0 else 1.0
    return float(notional) / lev


@dataclass
class SizeFit:
    allowed: bool
    reason: str
    contracts: Optional[float]
    base_qty: float
    stake: float
    risk_amount: float


@dataclass
class DeskDecision:
    pair: str
    action: str
    reason: str
    selected: bool
    plan: Optional[TradePlan]
    fit: Optional[SizeFit]
    confidence: float
    rr: float
    tilt_score: int
    size_mult: float
    open_risk_after: float


def cardinal_plan(signal: SignalReport, pair: str, min_rr: float, stop_pct: float = CARDINAL_STOP_PCT) -> Optional[TradePlan]:
    """Long-only pullback plan. Stop is the strategy percent, not an opinion."""
    if signal.side != "long" or signal.entry <= 0:
        return None
    entry = float(signal.entry)
    stop = entry * (1.0 - stop_pct)
    risk = entry - stop
    if risk <= 0:
        return None
    target = entry + (min_rr + 1e-4) * risk
    return TradePlan(
        symbol=pair,
        side="long",
        entry=entry,
        stop=stop,
        target=target,
        confidence=float(signal.confidence),
        reason=";".join(signal.reasons),
    )


def _step_decimals(step: float) -> int:
    text = f"{step:.10f}".rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return len(text.split(".")[1])


def _floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    units = math.floor((value / step) + 1e-9)
    return round(units * step, _step_decimals(step))


def fit_swap_size(
    base_qty: float,
    entry: float,
    stop: float,
    risk_budget: float,
    ct_val: float,
    min_sz: float,
    lot_sz: float,
) -> SizeFit:
    """Convert a base-asset quantity into swap contracts without raising risk."""
    empty = SizeFit(False, "no size", None, 0.0, 0.0, 0.0)
    if entry <= 0 or ct_val <= 0 or lot_sz <= 0 or base_qty <= 0 or risk_budget <= 0:
        return empty
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return SizeFit(False, "zero risk", None, 0.0, 0.0, 0.0)

    desired = _floor_step(base_qty / ct_val, lot_sz)
    minimum = _floor_step(min_sz, lot_sz) if min_sz > 0 else lot_sz
    if minimum <= 0:
        minimum = lot_sz
    contracts = desired
    reason = "ok"
    if contracts < minimum:
        min_risk = minimum * ct_val * stop_dist
        if min_risk > risk_budget + 1e-9:
            return SizeFit(False, "min size exceeds risk budget", None, 0.0, 0.0, round(min_risk, 4))
        contracts = minimum
        reason = "shrunk to min contract"

    base = contracts * ct_val
    risk_amount = base * stop_dist
    if risk_amount > risk_budget + 1e-9:
        return SizeFit(False, "rounded size exceeds risk budget", None, 0.0, 0.0, round(risk_amount, 4))
    if desired > 0 and contracts + 1e-12 < desired:
        reason = "shrunk to lot"
    return SizeFit(
        allowed=True,
        reason=reason,
        contracts=contracts,
        base_qty=base,
        stake=round(base * entry, 4),
        risk_amount=round(risk_amount, 4),
    )


def evaluate_symbol(
    *,
    pair: str,
    signal: SignalReport,
    equity: float,
    gate: RiskGate,
    tilt: TiltGuard,
    ct_val: float,
    min_sz: float,
    lot_sz: float,
    open_risk: float,
    slots_left: int,
    select: bool,
) -> DeskDecision:
    """Journal-ready decision. Selects at most one new risk slot when asked."""
    tilt_v = tilt.check(pair)
    plan = cardinal_plan(signal, pair, gate.config.min_rr)
    base = dict(
        pair=pair,
        selected=False,
        plan=plan,
        fit=None,
        confidence=float(signal.confidence),
        rr=plan.reward_risk() if plan else 0.0,
        tilt_score=tilt_v.tilt_score,
        size_mult=tilt_v.size_multiplier,
        open_risk_after=open_risk,
    )
    if tilt_v.action.value == "block":
        return DeskDecision(action="block", reason=f"tilt:{tilt_v.reason}", **base)
    if plan is None:
        reason = "no_signal:" + ";".join(signal.reasons)
        return DeskDecision(action="block", reason=reason, **base)
    if slots_left <= 0:
        return DeskDecision(action="block", reason="max open trades", **base)

    result = gate.evaluate(
        plan,
        equity,
        size_multiplier=tilt_v.size_multiplier,
        open_risk=open_risk,
    )
    if not result.allowed or not result.qty:
        return DeskDecision(action=result.decision.value, reason=result.reason, **base)

    risk_budget = equity * gate.config.max_risk * max(tilt_v.size_multiplier, 0.0)
    fit = fit_swap_size(
        base_qty=float(result.qty),
        entry=plan.entry,
        stop=plan.stop,
        risk_budget=risk_budget,
        ct_val=ct_val,
        min_sz=min_sz,
        lot_sz=lot_sz,
    )
    base["fit"] = fit
    if not fit.allowed or fit.contracts is None:
        return DeskDecision(action="block", reason=fit.reason, **base)

    action = result.decision.value
    reason = result.reason
    if fit.reason != "ok":
        action = GateDecision.SHRINK.value
        reason = fit.reason if reason == "ok" else f"{reason}+{fit.reason}"
    chosen = bool(select and slots_left > 0)
    return DeskDecision(
        pair=pair,
        action=action,
        reason=reason,
        selected=chosen,
        plan=plan,
        fit=fit,
        confidence=float(signal.confidence),
        rr=result.rr,
        tilt_score=tilt_v.tilt_score,
        size_mult=tilt_v.size_multiplier,
        open_risk_after=open_risk + (fit.risk_amount if chosen else 0.0),
    )
