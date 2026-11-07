"""
DeathModeV2_Cardinal — 자율운영용 리스크 케이지 전략

근거:
  1) alpha404ai Cardinal desk
     - Risk Gate는 plain Python (LLM 사이즈 금지)
     - kill switch / min R:R / fixed equity risk
     - tilt cooldown (복수 보복매매 차단)
  2) dubidubabap / Martin Luk 비대칭
     - 손실 짧게 · 수익 길게
     - 손절폭 기준 고정 리스크로 포지션 크기 산정
     - +3R/+5R 부분익절 후 나머지 trailing
     - 돌파보다 눌림목(EMA/VWAP) 선호

NFI 신호는 유지하되, 진입·사이즈·청산은 Cardinal 케이지를 통과한다.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import DecimalParameter

import os

sys.path.insert(0, os.path.dirname(__file__))
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from NostalgiaForInfinityX7 import NostalgiaForInfinityX7
from core.risk import (
    DecisionJournal,
    FillEvent,
    RiskConfig,
    RiskGate,
    TiltGuard,
    TradePlan,
)

log = logging.getLogger(__name__)


class DeathModeV2_Cardinal(NostalgiaForInfinityX7):
    """NFI 신호 + Cardinal Risk Gate + Martin Luk 비대칭 관리."""

    INTERFACE_VERSION = 3

    def version(self) -> str:
        return "death-v2-cardinal-1.0"

    timeframe = "5m"

    # ── risk cage defaults (overridable via env / RiskConfig) ──
    stoploss = -0.03                       # tighter stop → larger size at same $ risk
    use_custom_stoploss = True
    trailing_stop = False                  # custom_stoploss handles trailing after partials
    use_exit_signal = True
    ignore_roi_if_entry_signal = False

    # Martin: partials early, rest rides — ROI only as safety net far out
    minimal_roi = {
        "0": 10.0,      # effectively disabled; custom_exit / adjust handles TP
        "10080": 0.01,  # 7d dust exit
    }

    position_adjustment_enable = True
    max_entry_position_adjustment = -1  # allow multiple partial exits

    # Prefer pullbacks (Martin #3)
    prefer_pullback = True
    pullback_ema_fast = 9
    pullback_ema_slow = 21
    vwap_tol = DecimalParameter(0.002, 0.02, default=0.008, space="buy", optimize=False)

    # Partial take-profit in R multiples
    partial_r_1 = 3.0
    partial_r_1_pct = 0.12   # take 12% at +3R
    partial_r_2 = 5.0
    partial_r_2_pct = 0.15   # take additional 15% at +5R

    # Block infinite grinding DCA (same as SharpV2)
    BLOCKED_SIGNALS = {"120"}

    process_only_new_candles = True

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._risk = RiskGate(RiskConfig.from_env())
        self._tilt = TiltGuard()
        self._journal = DecisionJournal()
        self._partial_done: dict[str, set[str]] = {}  # trade_id -> {"r1","r2"}

    # ═══════════════════════════════════════
    # Indicators — pullback helpers
    # ═══════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)

        dataframe["ema_fast"] = dataframe["close"].ewm(
            span=self.pullback_ema_fast, adjust=False
        ).mean()
        dataframe["ema_slow"] = dataframe["close"].ewm(
            span=self.pullback_ema_slow, adjust=False
        ).mean()

        # Session VWAP approximation (rolling cumulative)
        typical = (dataframe["high"] + dataframe["low"] + dataframe["close"]) / 3.0
        vol = dataframe["volume"].replace(0, np.nan)
        cum_vol = vol.cumsum()
        dataframe["vwap"] = (typical * vol).cumsum() / cum_vol

        near_ema = (
            (dataframe["close"] - dataframe["ema_fast"]).abs() / dataframe["close"]
            <= self.vwap_tol.value
        ) | (
            (dataframe["close"] - dataframe["ema_slow"]).abs() / dataframe["close"]
            <= self.vwap_tol.value
        )
        near_vwap = (
            (dataframe["close"] - dataframe["vwap"]).abs() / dataframe["close"]
            <= self.vwap_tol.value
        )
        # Uptrend context: fast > slow (pullback in trend, not dump)
        uptrend = dataframe["ema_fast"] >= dataframe["ema_slow"]
        dataframe["cardinal_pullback"] = (near_ema | near_vwap) & uptrend
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_entry_trend(dataframe, metadata)
        if "enter_long" not in dataframe.columns:
            return dataframe

        if "enter_tag" in dataframe.columns and self.BLOCKED_SIGNALS:
            grinding = dataframe["enter_tag"].astype(str).str.contains(
                "|".join(self.BLOCKED_SIGNALS), na=False
            )
            dataframe.loc[grinding, "enter_long"] = 0

        if self.prefer_pullback and "cardinal_pullback" in dataframe.columns:
            # Soft filter: keep NFI signal only if pullback OR strong tag already set
            mask = (dataframe["enter_long"] == 1) & (~dataframe["cardinal_pullback"].fillna(False))
            dataframe.loc[mask, "enter_long"] = 0
            dataframe.loc[mask, "enter_tag"] = dataframe.loc[mask, "enter_tag"].astype(str) + " blocked_breakout"

        return dataframe

    # ═══════════════════════════════════════
    # Stake = f(stop distance)  — LLM never sizes
    # ═══════════════════════════════════════
    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: Optional[float],
        max_stake: float,
        leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        wallets = self.wallets
        equity = wallets.get_total(self.config["stake_currency"]) if wallets else proposed_stake
        if equity <= 0:
            equity = proposed_stake

        stop_price = current_rate * (1.0 + self.stoploss) if side == "long" else current_rate * (1.0 - self.stoploss)
        # Planned target for RR bookkeeping (min_rr × risk)
        risk_dist = abs(current_rate - stop_price)
        target = current_rate + self._risk.config.min_rr * risk_dist if side == "long" else current_rate - self._risk.config.min_rr * risk_dist

        tilt = self._tilt.check(pair, now=current_time)
        if tilt.action.value == "block":
            self._journal.log({
                "symbol": pair, "side": side, "action": "block",
                "reason": f"tilt:{tilt.reason}", "entry": current_rate,
                "stop": stop_price, "target": target, "rr": self._risk.config.min_rr,
                "tilt_score": tilt.tilt_score, "size_mult": 0,
                "day_pnl": self._risk.day_pnl,
            })
            return 0.0

        plan = TradePlan(
            symbol=pair,
            side="long" if side == "long" else "short",
            entry=current_rate,
            stop=stop_price,
            target=target,
            confidence=1.0,
            reason=entry_tag or "",
        )
        result = self._risk.evaluate(plan, equity, size_multiplier=tilt.size_multiplier)
        self._journal.log({
            "symbol": pair, "side": side, "action": result.decision.value,
            "reason": result.reason, "entry": current_rate, "stop": stop_price,
            "target": target, "rr": result.rr, "qty": result.qty,
            "stake": (result.meta or {}).get("stake"),
            "risk_pct": result.risk_pct, "tilt_score": tilt.tilt_score,
            "size_mult": tilt.size_multiplier, "day_pnl": self._risk.day_pnl,
        })

        if not result.allowed:
            log.info(f"[Cardinal] stake blocked {pair}: {result.reason}")
            return 0.0

        stake = float((result.meta or {}).get("stake") or 0.0)
        if max_stake is not None:
            stake = min(stake, max_stake)
        if min_stake is not None and stake < min_stake:
            # Below exchange minimum — skip rather than oversize risk
            log.info(f"[Cardinal] stake {stake} < min_stake {min_stake} → skip")
            return 0.0
        return stake

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> bool:
        tag = entry_tag or ""
        for blocked in self.BLOCKED_SIGNALS:
            if blocked in tag.split():
                log.info(f"[Cardinal] {pair} grinding({blocked}) blocked")
                return False

        if self._risk.killed:
            log.warning(f"[Cardinal] kill switch active — reject {pair}")
            return False

        tilt = self._tilt.check(pair, now=current_time)
        if tilt.action.value == "block":
            log.info(f"[Cardinal] tilt block {pair}: {tilt.reason}")
            return False

        self._tilt.record_entry(pair, now=current_time)
        return True

    # ═══════════════════════════════════════
    # Partial TP at +3R / +5R, then trail
    # ═══════════════════════════════════════
    def _initial_r(self, trade: Trade) -> float:
        open_rate = trade.open_rate
        return abs(open_rate * self.stoploss)  # absolute price distance of 1R

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: Optional[float],
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ):
        if trade.has_open_orders:
            return None

        one_r = self._initial_r(trade)
        if one_r <= 0 or trade.open_rate <= 0:
            return None

        # Profit in R multiples (long-oriented; short uses same profit ratio)
        r_mult = (current_rate - trade.open_rate) / one_r
        if trade.is_short:
            r_mult = (trade.open_rate - current_rate) / one_r

        key = str(trade.id)
        done = self._partial_done.setdefault(key, set())
        stake = trade.stake_amount

        if r_mult >= self.partial_r_1 and "r1" not in done:
            done.add("r1")
            exit_stake = -(stake * self.partial_r_1_pct)
            log.info(f"[Cardinal] {trade.pair} +{self.partial_r_1:.0f}R partial {self.partial_r_1_pct:.0%}")
            return exit_stake

        if r_mult >= self.partial_r_2 and "r2" not in done:
            done.add("r2")
            exit_stake = -(stake * self.partial_r_2_pct)
            log.info(f"[Cardinal] {trade.pair} +{self.partial_r_2:.0f}R partial {self.partial_r_2_pct:.0%}")
            return exit_stake

        return None

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        one_r_pct = abs(self.stoploss)
        # After +3R: trail at +2R lock; after +5R: trail at +3.5R
        if current_profit >= self.partial_r_2 * one_r_pct:
            return max(self.stoploss, current_profit - 1.5 * one_r_pct)
        if current_profit >= self.partial_r_1 * one_r_pct:
            return max(self.stoploss, current_profit - one_r_pct)

        # DCA / grind protection
        filled = trade.select_filled_orders(trade.entry_side)
        dca = max(0, len(filled) - 1)
        if dca >= 3 and current_profit < -0.02:
            return -0.01

        return self.stoploss

    # ═══════════════════════════════════════
    # Exit / fill accounting → tilt + day PnL
    # ═══════════════════════════════════════
    def confirm_trade_exit(
        self,
        pair: str,
        trade: Trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: datetime,
        **kwargs,
    ) -> bool:
        return True

    def order_filled(
        self, pair: str, trade: Trade, order, current_time: datetime, **kwargs
    ) -> None:
        # Prefer closed-trade hook when available; this covers partials too
        return None

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        return None

    # Freqtrade calls this when a trade is fully closed (if available via bot loop);
    # also expose a helper for scripts.
    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        if self._risk.killed:
            log.warning("[Cardinal] kill switch ON — entries blocked for the day")
        self._sync_closed_trades(current_time)

    def _sync_closed_trades(self, current_time: datetime) -> None:
        """Feed RiskGate / TiltGuard from recently closed trades."""
        if not hasattr(self, "_seen_closed_ids"):
            self._seen_closed_ids: set[int] = set()
        try:
            closed = Trade.get_trades_proxy(is_open=False)
        except Exception:
            return
        for trade in closed[-50:]:
            tid = int(trade.id)
            if tid in self._seen_closed_ids:
                continue
            # Only process trades closed today (UTC)
            if not trade.close_date_utc:
                continue
            age_h = (current_time - trade.close_date_utc).total_seconds() / 3600
            if age_h > 36:
                self._seen_closed_ids.add(tid)
                continue
            try:
                pnl = float(trade.close_profit_abs or 0.0)
                equity = 0.0
                if self.wallets:
                    equity = float(self.wallets.get_total(self.config["stake_currency"]) or 0.0)
                is_stop = "stop" in str(trade.exit_reason or "").lower()
                self._risk.record_pnl(pnl, equity if equity > 0 else max(abs(pnl) * 50, 1.0))
                self._tilt.record_fill(FillEvent(
                    symbol=trade.pair,
                    side="short" if trade.is_short else "long",
                    pnl=pnl,
                    is_stop=is_stop,
                    ts=trade.close_date_utc,
                ))
                self._partial_done.pop(str(tid), None)
                self._seen_closed_ids.add(tid)
                log.info(
                    f"[Cardinal] closed sync {trade.pair} pnl={pnl:.4f} "
                    f"day_pnl={self._risk.day_pnl:.4f} killed={self._risk.killed}"
                )
            except Exception as e:
                log.error(f"[Cardinal] closed-trade sync error: {e}")
                self._seen_closed_ids.add(tid)
