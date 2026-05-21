"""
DeathModeV2_Sharp v2 — 승률+손익 동시 최적화 (필터 최소화)

핵심 원칙:
- Grinding(120) 차단만 → NFI 신호 최대 살리기
- stoploss -0.07 (실손절)
- trailing_stop (이익 보호)
- minimal_roi (빠른 익절 + 슬롯 회전)
- DCA 3회 초과 시 강제청산 (무한홀딩 방지)
- Kill Zone/PD Array: 백테스트에서 OFF (실거래 시 ON 가능)
"""
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from freqtrade.strategy import IStrategy, merge_informative_pair, DecimalParameter, IntParameter
from freqtrade.persistence import Trade
from pandas import DataFrame
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from NostalgiaForInfinityX7 import NostalgiaForInfinityX7

log = logging.getLogger(__name__)


class DeathModeV2_SharpV2(NostalgiaForInfinityX7):
    """NFI X7 + 승률/손익 듀얼 최적화 v2"""

    INTERFACE_VERSION = 3

    def version(self) -> str:
        return "death-v2-sharp-2.0"

    timeframe = "5m"

    # ═══════════════════════════════════════
    # 1. 손절 강화 (NFI 원본 -0.99 → -0.07)
    # ═══════════════════════════════════════
    stoploss = -0.07
    use_custom_stoploss = True

    # Trailing: 이익 보호
    trailing_stop = True
    trailing_stop_positive = 0.02       # 2% 이익 도달 시 trailing 시작
    trailing_stop_positive_offset = 0.04  # 4% 도달 후 trailing_stop_positive 적용
    trailing_only_offset_is_reached = True

    # ═══════════════════════════════════════
    # 2. ROI — 빠른 익절
    # ═══════════════════════════════════════
    minimal_roi = {
        "0":    0.20,   # 즉시 20%
        "60":   0.12,   # 1h 12%
        "240":  0.08,   # 4h 8%
        "720":  0.05,   # 12h 5%
        "1440": 0.03,   # 1d 3%
        "4320": 0.01,   # 3d 1%
    }

    # ═══════════════════════════════════════
    # 3. Grinding(120) 차단 — 핵심
    # ═══════════════════════════════════════
    BLOCKED_SIGNALS = {"120"}

    # ═══════════════════════════════════════
    # 진입 필터: Grinding만 차단
    # ═══════════════════════════════════════
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_entry_trend(dataframe, metadata)

        if "enter_long" in dataframe.columns and "enter_tag" in dataframe.columns:
            # Grinding(120) 신호만 차단, 나머지 NFI 신호 전부 허용
            grinding_mask = dataframe["enter_tag"].astype(str).str.contains(
                "|".join(self.BLOCKED_SIGNALS), na=False
            )
            dataframe.loc[grinding_mask, "enter_long"] = 0

        return dataframe

    # ═══════════════════════════════════════
    # 커스텀 손절 — DCA 과다 + 장기 홀딩 방지
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

        # DCA 3회 이상 & -3% → 강제청산
        if dca_count >= 3 and current_profit < -0.03:
            log.warning(f"[SharpV2] {pair} DCA{dca_count}회/{current_profit:.1%} → 강제청산")
            return -0.01

        # 2일 이상 홀딩 & -5% → 강제청산
        hold_days = (current_time - trade.open_date_utc).days
        if hold_days >= 2 and current_profit < -0.05:
            log.warning(f"[SharpV2] {pair} {hold_days}일/{current_profit:.1%} → 강제청산")
            return -0.01

        # 이익 중 trailing 적용 (2% 이익 → 1% 보호)
        if current_profit >= 0.04:
            return max(self.stoploss, current_profit - 0.03)

        return self.stoploss

    # ═══════════════════════════════════════
    # 진입 확인 — Grinding 재차단
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
        tag_str = entry_tag or ""
        for blocked in self.BLOCKED_SIGNALS:
            if blocked in tag_str.split():
                log.info(f"[SharpV2] {pair} Grinding({blocked}) 진입차단")
                return False
        return True
