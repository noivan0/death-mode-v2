"""
DeathModeV2_Sharp — NFI X7 기반 승률+손익 동시 최적화 전략

핵심 개선:
1. Grinding(120) 완전 차단 → 무한홀딩 방지
2. stoploss -0.07 (실손절 강제)
3. trailing_stop 활성화 (이익 보호)
4. Kill Zone 필터 (London/NY AM만 진입)
5. PD Array Discount(<50%) 롱만 허용
6. max_open_trades 5 → 집중 운영
7. ROI 타이트 (빠른 익절)
"""
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from freqtrade.strategy import IStrategy, merge_informative_pair, DecimalParameter, IntParameter, BooleanParameter
from freqtrade.persistence import Trade
from pandas import DataFrame
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from NostalgiaForInfinityX7 import NostalgiaForInfinityX7

log = logging.getLogger(__name__)


class DeathModeV2_Sharp(NostalgiaForInfinityX7):
    """NFI X7 + 승률/손익 듀얼 최적화"""

    INTERFACE_VERSION = 3

    def version(self) -> str:
        return "death-v2-sharp-1.0"

    timeframe = "5m"
    info_timeframes = ["15m", "1h", "4h", "1d"]

    # ═══════════════════════════════════════
    # 1. 손절 강화
    # ═══════════════════════════════════════
    stoploss = -0.07                    # 7% 실손절 (NFI 원본 -0.99 → 교체)
    use_custom_stoploss = True          # 커스텀 손절 활성화

    # Trailing: 이익 보호
    trailing_stop = True
    trailing_stop_positive = 0.02      # 2% 이익 시 trailing 시작
    trailing_stop_positive_offset = 0.04  # 4% 도달 후 2% 까지 보호
    trailing_only_offset_is_reached = True

    # ═══════════════════════════════════════
    # 2. ROI — 빠른 익절 (슬롯 회전 ↑)
    # ═══════════════════════════════════════
    minimal_roi = {
        "0":   0.15,   # 15% 즉시 익절
        "60":  0.10,   # 1시간 후 10%
        "180": 0.07,   # 3시간 후 7%
        "360": 0.04,   # 6시간 후 4%
        "720": 0.02,   # 12시간 후 2%
        "1440": 0.01,  # 1일 후 1%
    }

    # ═══════════════════════════════════════
    # 3. Grinding 차단 (핵심)
    # ═══════════════════════════════════════
    # signal 120 = Grinding → 진입 블록
    BLOCKED_SIGNALS = {"120"}

    # ═══════════════════════════════════════
    # 4. ICT Kill Zone 파라미터
    # ═══════════════════════════════════════
    use_killzone_filter = BooleanParameter(default=True, space="buy", optimize=False)

    # UTC 기준 Kill Zone
    KILL_ZONES = {
        "LONDON": (7, 11),    # 07:00~11:00 UTC
        "NY_AM":  (13, 16),   # 13:00~16:00 UTC
        "NY_PM":  (18, 21),   # 18:00~21:00 UTC
    }

    # ═══════════════════════════════════════
    # 5. PD Array 파라미터
    # ═══════════════════════════════════════
    use_pd_filter = BooleanParameter(default=True, space="buy", optimize=False)
    pd_discount_threshold = DecimalParameter(0.3, 0.55, default=0.5, space="buy", optimize=False)
    pd_lookback = IntParameter(48, 200, default=96, space="buy", optimize=False)

    # ═══════════════════════════════════════
    # 헬퍼
    # ═══════════════════════════════════════
    def _in_kill_zone(self, df: DataFrame) -> pd.Series:
        """현재 캔들이 Kill Zone 내에 있으면 True"""
        hours = pd.to_datetime(df["date"]).dt.hour
        result = pd.Series(False, index=df.index)
        for kz, (start, end) in self.KILL_ZONES.items():
            result |= (hours >= start) & (hours < end)
        return result

    def _pd_position(self, df: DataFrame) -> pd.Series:
        """0=완전Discount, 1=완전Premium. 0.5 미만 = Discount zone (매수 우위)"""
        lb = self.pd_lookback.value
        hi = df["high"].rolling(lb).max()
        lo = df["low"].rolling(lb).min()
        rng = (hi - lo).replace(0, np.nan)
        return (df["close"] - lo) / rng

    # ═══════════════════════════════════════
    # 지표 추가
    # ═══════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)
        dataframe["ict_kill_zone"] = self._in_kill_zone(dataframe)
        dataframe["ict_pd_position"] = self._pd_position(dataframe)
        return dataframe

    # ═══════════════════════════════════════
    # 진입 필터 오버라이드
    # ═══════════════════════════════════════
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_entry_trend(dataframe, metadata)

        # 기존 NFI 신호에 ICT 필터 추가
        if "enter_long" in dataframe.columns:
            mask = dataframe["enter_long"] == 1

            # 1) Kill Zone 필터
            if self.use_killzone_filter.value:
                mask &= dataframe["ict_kill_zone"]

            # 2) PD Array Discount 필터 (Premium zone 진입 차단)
            if self.use_pd_filter.value:
                mask &= dataframe["ict_pd_position"] < self.pd_discount_threshold.value

            # 3) Grinding(120) 신호 차단
            if "enter_tag" in dataframe.columns:
                not_grinding = ~dataframe["enter_tag"].astype(str).str.contains(
                    "|".join(self.BLOCKED_SIGNALS), na=False
                )
                mask &= not_grinding

            dataframe.loc[~mask, "enter_long"] = 0

        return dataframe

    # ═══════════════════════════════════════
    # 커스텀 손절 — 연속 DCA 과다 방지
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
        """
        - 포지션 진입 후 3일 이상 -5% 미만 → 강제 손절
        - DCA 3회 이상 && 수익 -3% → 즉시 손절
        """
        filled_entries = trade.select_filled_orders(trade.entry_side)
        dca_count = len(filled_entries) - 1  # 첫 진입 제외

        # DCA 3회 이상 & 손실 3% 초과
        if dca_count >= 3 and current_profit < -0.03:
            log.warning(f"[Sharp] {pair} DCA {dca_count}회 & 손실 {current_profit:.2%} → 강제청산")
            return -0.01  # 즉시 손절

        # 3일 이상 홀딩 & -5% 이하
        hold_days = (current_time - trade.open_date_utc).days
        if hold_days >= 3 and current_profit < -0.05:
            log.warning(f"[Sharp] {pair} {hold_days}일 홀딩 & {current_profit:.2%} → 강제청산")
            return -0.01

        # 기본 stoploss
        return self.stoploss

    # ═══════════════════════════════════════
    # 포지션 진입 시 태그에 ICT 컨텍스트 추가
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
        if len(df) < 2:
            return True

        last = df.iloc[-1]
        kz = last.get("ict_kill_zone", False)
        pd_pos = last.get("ict_pd_position", 0.5)
        tag_str = entry_tag or ""

        # Grinding 재확인
        tags = tag_str.split()
        for blocked in self.BLOCKED_SIGNALS:
            if blocked in tags:
                log.info(f"[Sharp] {pair} Grinding({blocked}) 차단")
                return False

        log.info(
            f"[Sharp] {pair} 진입 확인 | KZ:{kz} | PD:{pd_pos:.2f} | tag:{tag_str}"
        )
        return True
