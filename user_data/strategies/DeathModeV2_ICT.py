"""
DeathModeV2_ICT — NFI X7 + ICT 7기법 로깅 통합 전략
- NFI X7 진입/청산 로직 100% 상속
- ICT 기법 판단근거를 enter_tag에 기록 (진입 차단 없음)
- 5m 메인 TF / 10페어 HL Futures
- ICT 기법: Unicorn(BB+FVG), OB+FVG, TurtleSoup, KillZone, PD Array, BOS/CHOCH, EQH/EQL
"""
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from freqtrade.strategy import IStrategy, merge_informative_pair, DecimalParameter, IntParameter
from pandas import DataFrame

# NFI X7 임포트 (같은 디렉토리)
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from NostalgiaForInfinityX7 import NostalgiaForInfinityX7

log = logging.getLogger(__name__)


class DeathModeV2_ICT(NostalgiaForInfinityX7):
    """NFI X7 완전 상속 + ICT 7기법 enter_tag 로깅"""

    # 전략 식별
    INTERFACE_VERSION = 3

    def version(self) -> str:
        return "death-v2-ict-1.0"

    # NFI X7 원설계 유지
    timeframe = "5m"

    # =========================================
    # ICT 파라미터 (로깅 전용, 진입 차단 없음)
    # =========================================
    # FVG 감지 임계값 (캔들 크기 대비 gap 비율)
    fvg_threshold = DecimalParameter(0.001, 0.01, default=0.003, space="buy", optimize=False)
    # OB lookback 캔들 수
    ob_lookback = IntParameter(3, 20, default=10, space="buy", optimize=False)

    # Kill Zone (UTC)
    KILL_ZONES = {
        "ASIA":    (0,  6),   # 00:00~06:00
        "LONDON":  (7,  11),  # 07:00~11:00
        "NY_AM":   (13, 16),  # 13:00~16:00
        "NY_PM":   (18, 21),  # 18:00~21:00
    }

    # =========================================
    # ICT 지표 계산
    # =========================================
    def _detect_fvg(self, df: DataFrame) -> pd.Series:
        """Fair Value Gap: 전전캔들 고가 < 다음캔들 저가 (bullish FVG)"""
        gap = df["low"].shift(0) - df["high"].shift(2)
        fvg_bull = gap > (df["close"].shift(1) * self.fvg_threshold.value)
        fvg_bear = (df["low"].shift(2) - df["high"].shift(0)) > (df["close"].shift(1) * self.fvg_threshold.value)
        result = pd.Series("none", index=df.index)
        result[fvg_bull] = "bull"
        result[fvg_bear] = "bear"
        return result

    def _detect_ob(self, df: DataFrame, lookback: int = 10) -> pd.Series:
        """Order Block: 추세 전환 직전의 마지막 반대 캔들"""
        result = pd.Series("none", index=df.index)
        for i in range(lookback, len(df)):
            # Bullish OB: 하락 캔들 이후 강한 상승
            if df["close"].iloc[i] > df["high"].iloc[i-1]:
                bull_move = df["close"].iloc[i] - df["open"].iloc[i]
                if bull_move > df["close"].iloc[i] * 0.002:
                    result.iloc[i] = "bull_ob"
            # Bearish OB: 상승 캔들 이후 강한 하락
            elif df["close"].iloc[i] < df["low"].iloc[i-1]:
                bear_move = df["open"].iloc[i] - df["close"].iloc[i]
                if bear_move > df["close"].iloc[i] * 0.002:
                    result.iloc[i] = "bear_ob"
        return result

    def _detect_kill_zone(self, df: DataFrame) -> pd.Series:
        """현재 캔들이 어느 Kill Zone에 속하는지"""
        hours = pd.to_datetime(df["date"]).dt.hour
        result = pd.Series("OFF", index=df.index)
        for kz, (start, end) in self.KILL_ZONES.items():
            mask = (hours >= start) & (hours < end)
            result[mask] = kz
        return result

    def _detect_pd_array(self, df: DataFrame, lookback: int = 96) -> pd.Series:
        """Premium/Discount Array: 현재가가 최근 레인지 대비 위치 (%)"""
        high_max = df["high"].rolling(lookback).max()
        low_min  = df["low"].rolling(lookback).min()
        rng = high_max - low_min
        pos = (df["close"] - low_min) / rng.replace(0, np.nan)
        # 0=완전 Discount, 1=완전 Premium, 0.5=Equilibrium
        return (pos * 100).round(1)

    def _detect_bos_choch(self, df: DataFrame, lookback: int = 20) -> pd.Series:
        """Break of Structure / Change of Character"""
        result = pd.Series("none", index=df.index)
        for i in range(lookback, len(df)):
            recent_high = df["high"].iloc[i-lookback:i].max()
            recent_low  = df["low"].iloc[i-lookback:i].min()
            prev_close  = df["close"].iloc[i-1]
            curr_close  = df["close"].iloc[i]
            # BOS: 이전 스윙 하이/로우 돌파
            if curr_close > recent_high and prev_close <= recent_high:
                result.iloc[i] = "bos_bull"
            elif curr_close < recent_low and prev_close >= recent_low:
                result.iloc[i] = "bos_bear"
            # CHOCH: 직전 반대 방향 BOS (단순화)
            elif df["close"].iloc[i-3:i].mean() < df["close"].iloc[i-6:i-3].mean() and curr_close > prev_close:
                result.iloc[i] = "choch_bull"
        return result

    def _detect_turtle_soup(self, df: DataFrame, lookback: int = 20) -> pd.Series:
        """Turtle Soup: 이전 스윙 고/저 위반 후 즉시 반전 (Liquidity Grab)"""
        result = pd.Series(False, index=df.index)
        for i in range(lookback + 1, len(df)):
            prev_sw_low  = df["low"].iloc[i-lookback:i-1].min()
            prev_sw_high = df["high"].iloc[i-lookback:i-1].max()
            # 저점 위반 후 양봉 반전 (Bull Turtle Soup)
            if df["low"].iloc[i] < prev_sw_low and df["close"].iloc[i] > df["open"].iloc[i]:
                result.iloc[i] = True
            # 고점 위반 후 음봉 반전 (Bear Turtle Soup)
            elif df["high"].iloc[i] > prev_sw_high and df["close"].iloc[i] < df["open"].iloc[i]:
                result.iloc[i] = True
        return result

    def _detect_eqhl(self, df: DataFrame, lookback: int = 30, tolerance: float = 0.001) -> pd.Series:
        """Equal Highs/Lows: 두 번 이상 같은 레벨 터치 → 유동성 집중 구간"""
        result = pd.Series("none", index=df.index)
        for i in range(lookback, len(df)):
            window_h = df["high"].iloc[i-lookback:i]
            window_l = df["low"].iloc[i-lookback:i]
            curr_h, curr_l = df["high"].iloc[i], df["low"].iloc[i]
            # EQH: 현재 고가와 비슷한 고가가 이미 존재
            near_highs = ((window_h - curr_h).abs() / curr_h) < tolerance
            if near_highs.sum() >= 2:
                result.iloc[i] = "eqh"
            # EQL: 현재 저가와 비슷한 저가가 이미 존재
            near_lows = ((window_l - curr_l).abs() / curr_l) < tolerance
            if near_lows.sum() >= 2:
                result.iloc[i] = "eql"
        return result

    # =========================================
    # populate_entry_trend 오버라이드: ICT 태그 추가
    # =========================================
    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        """NFI X7 진입 신호 그대로 + ICT 판단근거를 enter_tag에 append"""
        # 1. NFI X7 원본 진입 신호 계산
        df = super().populate_entry_trend(df, metadata)

        # 2. ICT 지표 계산
        try:
            fvg      = self._detect_fvg(df)
            ob       = self._detect_ob(df, self.ob_lookback.value)
            kz       = self._detect_kill_zone(df)
            pd_arr   = self._detect_pd_array(df)
            bos      = self._detect_bos_choch(df)
            ts       = self._detect_turtle_soup(df)
            eqhl     = self._detect_eqhl(df)
        except Exception as e:
            log.warning(f"[{metadata['pair']}] ICT 계산 오류: {e}")
            return df

        # 3. 진입 신호 있는 캔들에만 ICT 태그 추가
        entry_mask = df["enter_long"] == 1
        if not entry_mask.any():
            return df

        def build_ict_tag(idx):
            parts = []
            # Unicorn: BB 스퀴즈 + FVG
            f = fvg.iloc[idx]
            if f != "none":
                parts.append(f"FVG:{f}")
            # OB
            o = ob.iloc[idx]
            if o != "none":
                parts.append(f"OB:{o}")
            # Turtle Soup
            if ts.iloc[idx]:
                parts.append("TS:grab")
            # Kill Zone
            k = kz.iloc[idx]
            if k != "OFF":
                parts.append(f"KZ:{k}")
            # PD Array
            pd_val = pd_arr.iloc[idx]
            if not pd.isna(pd_val):
                zone = "disc" if pd_val < 35 else ("prem" if pd_val > 65 else "eq")
                parts.append(f"PD:{zone}{pd_val:.0f}%")
            # BOS/CHOCH
            b = bos.iloc[idx]
            if b != "none":
                parts.append(f"BOS:{b}")
            # EQH/EQL
            e = eqhl.iloc[idx]
            if e != "none":
                parts.append(f"EQ:{e}_raid")

            return "[" + ",".join(parts) + "]" if parts else ""

        # 4. enter_tag 뒤에 ICT 태그 붙이기
        for i in df.index[entry_mask]:
            idx = df.index.get_loc(i)
            ict_tag = build_ict_tag(idx)
            if ict_tag:
                orig = df.at[i, "enter_tag"] if "enter_tag" in df.columns else ""
                df.at[i, "enter_tag"] = f"{orig}{ict_tag}" if orig else ict_tag

        return df
