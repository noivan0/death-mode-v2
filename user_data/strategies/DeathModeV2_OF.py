"""
DeathModeV2_OF — NFI X7 + OrderFlow 필터 통합 전략
- NFI X7 진입/청산 로직 100% 상속
- OrderFlow 지표 (CVD, VWAP, Volume Imbalance, Volume Momentum) 로깅
- OrderFlow 조건이 나쁠 때 진입 차단 (약한 필터)
- 5m 메인 TF / USDC 페어
"""
import logging
import numpy as np
import pandas as pd
from pandas import DataFrame

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from NostalgiaForInfinityX7 import NostalgiaForInfinityX7

log = logging.getLogger(__name__)


class DeathModeV2_OF(NostalgiaForInfinityX7):
    """NFI X7 + OrderFlow 필터"""

    INTERFACE_VERSION = 3

    def version(self) -> str:
        return "death-v2-of-1.0"

    timeframe = "5m"

    # =========================================
    # OrderFlow 지표 계산
    # =========================================

    def _calc_cvd(self, df: DataFrame, period: int = 20) -> pd.Series:
        """Cumulative Volume Delta (근사치)
        양봉: +volume, 음봉: -volume 누적
        """
        delta = df["volume"] * np.where(df["close"] >= df["open"], 1.0, -1.0)
        return delta.rolling(period).sum()

    def _calc_vwap(self, df: DataFrame, period: int = 20) -> pd.Series:
        """VWAP (이동 윈도우)"""
        typical = (df["high"] + df["low"] + df["close"]) / 3
        return (typical * df["volume"]).rolling(period).sum() / df["volume"].rolling(period).sum()

    def _calc_volume_imbalance(self, df: DataFrame, period: int = 5) -> pd.Series:
        """Volume Imbalance: 최근 N캔들 양봉/음봉 볼륨 비율"""
        bull_vol = (df["volume"] * (df["close"] >= df["open"]).astype(float)).rolling(period).sum()
        bear_vol = (df["volume"] * (df["close"] < df["open"]).astype(float)).rolling(period).sum()
        total = bull_vol + bear_vol
        return (bull_vol - bear_vol) / total.replace(0, np.nan)

    def _calc_volume_momentum(self, df: DataFrame, period: int = 10) -> pd.Series:
        """볼륨 모멘텀: 현재 볼륨 / 평균 볼륨"""
        avg_vol = df["volume"].rolling(period).mean()
        return df["volume"] / avg_vol.replace(0, np.nan)

    def _calc_absorption(self, df: DataFrame) -> pd.Series:
        """Absorption: 큰 볼륨에 비해 가격 움직임이 작은 경우 (매수/매도 흡수)"""
        price_move = (df["high"] - df["low"]) / df["close"]
        vol_norm = df["volume"] / df["volume"].rolling(20).mean().replace(0, np.nan)
        # 볼륨 크고 움직임 작으면 흡수 (absorption)
        return (vol_norm / price_move.replace(0, np.nan)).clip(0, 10)

    # =========================================
    # populate_entry_trend 오버라이드
    # =========================================

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        """NFI X7 신호 + OrderFlow 필터 적용"""
        # 1. NFI X7 원본 신호
        df = super().populate_entry_trend(df, metadata)

        # 2. OrderFlow 지표 계산
        try:
            cvd     = self._calc_cvd(df, 20)
            vwap    = self._calc_vwap(df, 20)
            vi      = self._calc_volume_imbalance(df, 5)
            vmom    = self._calc_volume_momentum(df, 10)
            absorb  = self._calc_absorption(df)

            # 가격 vs VWAP 위치 (%)
            price_vs_vwap = ((df["close"] - vwap) / vwap * 100).round(2)

        except Exception as e:
            log.warning(f"[{metadata['pair']}] OrderFlow 계산 오류: {e}")
            return df

        # 3. OrderFlow 필터 조건 (약한 필터 — 명백한 역방향만 차단)
        #    CVD 음수 + Volume Imbalance 강한 매도 → 롱 진입 차단
        of_block_long = (cvd < -cvd.abs().rolling(50).mean() * 1.5) & (vi < -0.5)

        # 4. 진입 신호 필터링
        entry_mask = df["enter_long"] == 1
        if entry_mask.any():
            # OrderFlow 블록 조건 적용
            df.loc[entry_mask & of_block_long, "enter_long"] = 0

            # 남은 진입에 OF 태그 추가
            remaining = (df["enter_long"] == 1)
            for i in df.index[remaining]:
                idx = df.index.get_loc(i)
                try:
                    cvd_val  = round(float(cvd.iloc[idx]), 1)
                    vi_val   = round(float(vi.iloc[idx]), 2)
                    pvwap    = round(float(price_vs_vwap.iloc[idx]), 1)
                    vmom_val = round(float(vmom.iloc[idx]), 1)
                    abs_val  = round(float(absorb.iloc[idx]), 1)

                    of_tag = f"OF[cvd={cvd_val:+.0f},vi={vi_val:.2f},vwap={pvwap:+.1f}%,vmom={vmom_val:.1f}x,abs={abs_val:.1f}]"
                    orig = df.at[i, "enter_tag"] if "enter_tag" in df.columns else ""
                    df.at[i, "enter_tag"] = f"{orig}{of_tag}" if orig else of_tag
                except Exception:
                    pass

        return df
