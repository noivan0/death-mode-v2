"""
DeathModeV2_OrderFlow v2 — NFI X7 + OrderFlow 필터 (dip-buy 호환)

문제: CVD>0 + Close>VWAP = 추세추종 필터 → NFI 역추세와 100% 충돌 → 0건
해결: 역추세 dip-buy에 맞는 OrderFlow 필터로 재설계
  - CVD 반등 감지: CVD 기울기 상승 중 (저점 찍고 회복 시작)
  - Volume Spike: 평균 대비 1.2배 이상 (급락 확인, 방향 무관)
  - CVD 과매도: CVD가 최근 50캔들 내 하위 20% (진짜 dip)
ICT: enter_tag 로깅만 (진입 차단 없음)
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


class DeathModeV2_OrderFlow(NostalgiaForInfinityX7):
    """NFI X7 + OrderFlow 필터 (dip-buy 역추세 호환)"""

    INTERFACE_VERSION = 3

    def version(self) -> str:
        return "death-v2-orderflow-2.0"

    timeframe = "5m"

    # ICT 파라미터 (로깅 전용)
    KILL_ZONES = {
        "LONDON": (7, 11),
        "NY_AM":  (13, 16),
        "NY_PM":  (18, 21),
    }
    fvg_threshold = DecimalParameter(0.001, 0.01, default=0.003, space="buy", optimize=False)
    pd_lookback   = IntParameter(48, 200, default=96, space="buy", optimize=False)

    # OrderFlow 파라미터
    cvd_window     = IntParameter(5, 30,  default=14,  space="buy", optimize=False)
    cvd_slope_bars = IntParameter(2, 10,  default=5,   space="buy", optimize=False)   # CVD 반등 확인 기간
    vol_mult       = DecimalParameter(1.0, 2.5, default=1.2, space="buy", optimize=False)  # 거래량 배수
    cvd_oversold   = IntParameter(10, 40, default=20,  space="buy", optimize=False)   # CVD 하위 N%

    # ═══════════════════════════════════════
    # ICT 지표 (로깅 전용)
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
    # OrderFlow 지표 (dip-buy 호환)
    # ═══════════════════════════════════════
    def _calc_cvd(self, df: DataFrame) -> pd.Series:
        """단순 CVD 근사: 캔들 방향 * volume 누적"""
        rng = (df["high"] - df["low"]).replace(0, np.nan)
        delta = ((df["close"] - df["open"]) / rng) * df["volume"]
        return delta.rolling(self.cvd_window.value).sum()

    def _cvd_slope_rising(self, df: DataFrame, cvd: pd.Series) -> pd.Series:
        """CVD 기울기 반등: 현재 CVD > N캔들 전 CVD (저점 반등 감지)"""
        n = self.cvd_slope_bars.value
        return cvd > cvd.shift(n)

    def _cvd_oversold(self, df: DataFrame, cvd: pd.Series) -> pd.Series:
        """CVD가 최근 50캔들 하위 N% — 진짜 dip 확인"""
        pct = self.cvd_oversold.value / 100.0
        rolling_min = cvd.rolling(50).quantile(pct)
        return cvd <= rolling_min

    def _vol_spike(self, df: DataFrame) -> pd.Series:
        """거래량 평균 대비 spike — dip 진짜성 확인 (방향 무관)"""
        avg = df["volume"].rolling(20).mean()
        return df["volume"] >= (avg * self.vol_mult.value)

    # ═══════════════════════════════════════
    # 지표 계산
    # ═══════════════════════════════════════
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_indicators(dataframe, metadata)

        # ICT 로깅용
        dataframe["ict_kill_zone"] = self._ict_kill_zone(dataframe)
        dataframe["ict_fvg"]       = self._ict_fvg(dataframe)
        dataframe["ict_pd_pct"]    = self._ict_pd_position(dataframe)

        # OrderFlow
        cvd = self._calc_cvd(dataframe)
        dataframe["of_cvd"]           = cvd
        dataframe["of_cvd_rising"]    = self._cvd_slope_rising(dataframe, cvd)
        dataframe["of_cvd_oversold"]  = self._cvd_oversold(dataframe, cvd)
        dataframe["of_vol_spike"]     = self._vol_spike(dataframe)

        return dataframe

    # ═══════════════════════════════════════
    # 진입: NFI 신호 + OrderFlow 필터 (dip-buy 호환)
    # ═══════════════════════════════════════
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = super().populate_entry_trend(dataframe, metadata)

        if "enter_long" in dataframe.columns:
            # dip-buy 호환 OrderFlow 필터:
            # NFI는 하락 중 진입 → CVD 기울기 반등은 맞지 않음
            # 대신: CVD 과매도 구간(매도압 충분히 쌓임) OR 거래량 급증(dip 진짜성)
            of_filter = (
                dataframe["of_cvd_oversold"] | dataframe["of_vol_spike"]
            )
            dataframe.loc[~of_filter, "enter_long"] = 0

        return dataframe

    # ═══════════════════════════════════════
    # 진입 확인 + ICT/OF 로깅
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
            log.info(
                f"[OF-LOG] {pair} | tag={entry_tag} "
                f"| KZ={last.get('ict_kill_zone','?')} "
                f"| FVG={last.get('ict_fvg','?')} "
                f"| PD={last.get('ict_pd_pct',0):.1f}% "
                f"| CVD={last.get('of_cvd',0):.0f} "
                f"| rising={last.get('of_cvd_rising',False)} "
                f"| oversold={last.get('of_cvd_oversold',False)} "
                f"| vol_spike={last.get('of_vol_spike',False)}"
            )
        return True  # 항상 진입 허용 (필터는 populate에서 완료)
