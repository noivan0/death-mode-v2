"""
DeathModeV2_Base — NFI X7 기반 전략 (전략1)

변경사항 (NFI 원본 대비):
- stoploss: -0.99 → -0.07 (실손절)
- trailing_stop: True (이익 보호)
- minimal_roi: 타이트 설정 (슬롯 회전 ↑)
- Grinding(120) DCA 과다 시 강제청산 (리스크 관리)
- ICT 기법: enter_tag 로깅만 (진입 차단 없음)
"""
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from freqtrade.strategy import DecimalParameter, IntParameter
from freqtrade.persistence import Trade
from pandas import DataFrame
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from NostalgiaForInfinityX7 import NostalgiaForInfinityX7

log = logging.getLogger(__name__)


class DeathModeV2_Base(NostalgiaForInfinityX7):
    """NFI X7 + 리스크 개선 + ICT 로깅"""

    INTERFACE_VERSION = 3

    def version(self) -> str:
        return "death-v2-base-1.0"

    timeframe = "5m"

    # ═══════════════════════════════════════
    # 리스크 관리 (NFI 원본 대비 수정)
    # ═══════════════════════════════════════
    stoploss = -0.07                          # 원본 -0.99 → 실손절
    use_custom_stoploss = True

    trailing_stop = True
    trailing_stop_positive = 0.02             # 2% 이익 시 trailing 시작
    trailing_stop_positive_offset = 0.04      # 4% 이상에서 trailing 적용
    trailing_only_offset_is_reached = True

    minimal_roi = {
        "0":    0.20,
        "60":   0.12,
        "240":  0.08,
        "720":  0.05,
        "1440": 0.03,
        "4320": 0.01,
    }

    # ═══════════════════════════════════════
    # ICT 파라미터 (로깅 전용)
    # ═══════════════════════════════════════
    KILL_ZONES = {
        "LONDON": (7, 11),
        "NY_AM":  (13, 16),
        "NY_PM":  (18, 21),
    }
    fvg_threshold = DecimalParameter(0.001, 0.01, default=0.003, space="buy", optimize=False)
    pd_lookback   = IntParameter(48, 200, default=96,            space="buy", optimize=False)

    # ═══════════════════════════════════════
    # ICT 지표 계산 (로깅 전용)
    # ═══════════════════════════════════════
    def _ict_kill_zone(self, df: DataFrame) -> pd.Series:
        hours = pd.to_datetime(df["date"]).dt.hour
        result = pd.Series("OFF", index=df.index)
        for kz, (s, e) in self.KILL_ZONES.items():
            result[(hours >= s) & (hours < e)] = kz
        return result

    def _ict_fvg(self, df: DataFrame) -> pd.Series:
        gap_bull = df["low"] - df["high"].shift(2)
        gap_bear = df["low"].shift(2) - df["high"]
        thr = df["close"].shift(1) * self.fvg_threshold.value
        result = pd.Series("none", index=df.index)
        result[gap_bull > thr] = "bull"
        result[gap_bear > thr] = "bear"
        return result

    def _ict_pd_position(self, df: DataFrame) -> pd.Series:
        lb = self.pd_lookback.value
        hi = df["high"].rolling(lb).max()
        lo = df["low"].rolling(lb).min()
        rng = (hi - lo).replace(0, np.nan)
        return ((df["close"] - lo) / rng * 100).round(1)

    # ═══════════════════════════════════════
    # 지표 추가 (ICT 로깅용 컬럼만)
    # ═══════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        # ICT 로깅 전용 컬럼 (진입 조건에 사용 안 함)
        dataframe["ict_kill_zone"] = self._ict_kill_zone(dataframe)
        dataframe["ict_fvg"]       = self._ict_fvg(dataframe)
        dataframe["ict_pd_pct"]    = self._ict_pd_position(dataframe)
        return dataframe

    # ═══════════════════════════════════════
    # 진입: NFI 원본 신호 그대로 (ICT 필터 없음)
    # ═══════════════════════════════════════
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # NFI 원본 진입 신호 100% 유지
        return super().populate_entry_trend(dataframe, metadata)

    # ═══════════════════════════════════════
    # 커스텀 손절 — DCA 과다 / 장기 홀딩 방지
    # ═══════════════════════════════════════
    def custom_stoploss(
        self,
        pair: str,
        trade: "Trade",
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        filled_entries = trade.select_filled_orders(trade.entry_side)
        dca_count = max(0, len(filled_entries) - 1)

        # DCA 3회 이상 & 손실 3% → 강제청산 (Grinding 무한홀딩 방지)
        if dca_count >= 3 and current_profit < -0.03:
            log.warning(f"[Base] {pair} DCA{dca_count}회/{current_profit:.1%} → 강제청산")
            return -0.01

        # 2일 이상 홀딩 & 손실 5% → 강제청산
        hold_days = (current_time - trade.open_date_utc).days
        if hold_days >= 2 and current_profit < -0.05:
            log.warning(f"[Base] {pair} {hold_days}일/{current_profit:.1%} → 강제청산")
            return -0.01

        return self.stoploss

    # ═══════════════════════════════════════
    # ICT 컨텍스트 로깅 (진입 시)
    # ═══════════════════════════════════════
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
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(df) >= 1:
            last = df.iloc[-1]
            kz   = last.get("ict_kill_zone", "?")
            fvg  = last.get("ict_fvg", "?")
            pd_p = last.get("ict_pd_pct", 0)
            log.info(
                f"[ICT-LOG] {pair} | tag={entry_tag} | KZ={kz} | FVG={fvg} | PD={pd_p:.1f}%"
            )
        return True   # 항상 진입 허용 (ICT는 로깅만)
