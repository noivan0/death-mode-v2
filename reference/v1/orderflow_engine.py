#!/usr/bin/env python3
"""
orderflow_engine.py — 오더플로우 분석 모듈 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

실제 Lighter DEX 오더북 bid/ask 스트림 기반 매수/매도 압력 계산

설계 원칙:
  - 독립 모듈 (bot_v4_death.py에서 import 해서 사용)
  - 캐시 TTL: 2초 (과도한 API 호출 방지)
  - 비동기 우선 (aiohttp 세션 공유 가능)
  - 문제: Lighter OrderApi 세션이 asyncio 이벤트루프에 묶임
    → LighterAPI._get_session()과 별도로 독립 aiohttp 세션 사용

오더플로우 지표:
  1. bid_ask_ratio  : 매수잔량 / (매수잔량+매도잔량) → 0.5 기준 방향성
  2. spread_pct     : (best_ask - best_bid) / mid_price → 슬리피지 추정
  3. bid_depth_n    : 상위 N호가 bid 총잔량
  4. ask_depth_n    : 상위 N호가 ask 총잔량
  5. imbalance_score: -100 ~ +100 (+ = 매수우위, - = 매도우위)
  6. wall_side      : "BID" | "ASK" | None — 특정 방향 대형 벽 존재 여부

진입 시그널:
  - LONG  조건: imbalance_score >= +OF_THRESHOLD (+25)
                AND spread_pct < MAX_SPREAD (비용 감당 가능)
  - SHORT 조건: imbalance_score <= -OF_THRESHOLD (-25)
                AND spread_pct < MAX_SPREAD

혼합 전략 시 사용법:
  기존 SignalEngine.score()가 방향과 score를 결정 →
  OF_Engine.get_imbalance(market_id)로 방향 일치 여부만 필터링
  일치 시 보너스 점수 부여, 반대 시 진입 차단
"""

import asyncio
import time
import logging
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("deathv4")

# ── 파라미터 ─────────────────────────────────────────────────
OF_CACHE_TTL    = 2.0     # 오더북 캐시 TTL (초)
OF_DEPTH_LIMIT  = 20      # 호가 조회 깊이 (상위 20개)
OF_THRESHOLD    = 25      # 단독 진입 기준 imbalance_score
OF_BONUS_THRESH = 15      # 혼합 전략 보너스 기준 imbalance_score
OF_BONUS_PTS    = 20      # 방향 일치 시 추가 점수
OF_BLOCK_THRESH = -15     # 이 이하면 반대 방향 차단 (방향 불일치)
MAX_SPREAD_PCT  = 0.0015  # 최대 허용 스프레드 0.15% (이상이면 진입 안 함)
WALL_RATIO      = 3.0     # 특정 호가 잔량이 평균의 N배 이상이면 "벽"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class OrderFlowEngine:
    """
    실시간 오더북 기반 매수/매도 압력 분석

    문제점 방지:
      1. API 과호출: TTL 캐시로 차단 (2초)
      2. 이벤트루프 충돌: lighter.ApiClient 재사용 (외부 주입)
      3. 예외 누출: 모든 예외 내부 흡수 → None 반환
      4. 중복 인스턴스: 싱글턴 패턴 (OF_ENGINE 전역 변수)
    """

    def __init__(self):
        # market_id → (timestamp, OFData)
        self._cache: Dict[int, Tuple[float, Dict]] = {}
        self._api_client = None   # lighter.ApiClient 외부 주입
        self._order_api  = None   # lighter.OrderApi

    def attach_api(self, api_client, order_api):
        """LighterAPI 초기화 후 주입"""
        self._api_client = api_client
        self._order_api  = order_api

    async def fetch(self, market_id: int) -> Optional[Dict]:
        """
        오더북 조회 + 분석. 캐시 히트 시 재사용.

        반환 형식:
        {
            "market_id": int,
            "bid_vol": float,       # 상위 N호가 bid 총잔량 (base)
            "ask_vol": float,       # 상위 N호가 ask 총잔량 (base)
            "best_bid": float,
            "best_ask": float,
            "spread_pct": float,    # (ask - bid) / mid
            "imbalance": float,     # -100 ~ +100
            "direction": str,       # "BULL" | "BEAR" | "NEUTRAL"
            "wall_side": str|None,  # "BID" | "ASK" | None
            "ts": float,
        }
        """
        now = time.time()
        cached = self._cache.get(market_id)
        if cached and now - cached[0] < OF_CACHE_TTL:
            return cached[1]

        if self._order_api is None:
            return None

        try:
            res = await self._order_api.order_book_orders(
                market_id=market_id, limit=OF_DEPTH_LIMIT
            )
            d = res.to_dict()
            return self._analyse(market_id, d, now)
        except Exception as e:
            log.debug(f"OF fetch err mid={market_id}: {e}")
            return None

    def _analyse(self, market_id: int, raw: Dict, ts: float) -> Optional[Dict]:
        """오더북 raw 데이터 → 압력 지표 계산"""
        bids: List[Dict] = raw.get("bids", [])
        asks: List[Dict] = raw.get("asks", [])

        if not bids or not asks:
            return None

        def parse_orders(orders: List[Dict]) -> Tuple[List[float], List[float]]:
            """(가격 리스트, 잔량 리스트)"""
            prices, sizes = [], []
            for o in orders:
                try:
                    prices.append(float(o["price"]))
                    sizes.append(float(o["remaining_base_amount"]))
                except (KeyError, ValueError):
                    continue
            return prices, sizes

        bid_prices, bid_sizes = parse_orders(bids)
        ask_prices, ask_sizes = parse_orders(asks)

        if not bid_sizes or not ask_sizes:
            return None

        bid_vol = sum(bid_sizes)
        ask_vol = sum(ask_sizes)
        total   = bid_vol + ask_vol

        if total < 1e-9:
            return None

        # 최우선 호가
        best_bid = max(bid_prices) if bid_prices else 0.0
        best_ask = min(ask_prices) if ask_prices else 0.0
        mid_price = (best_bid + best_ask) / 2 if (best_bid and best_ask) else 0.0
        spread_pct = (best_ask - best_bid) / mid_price if mid_price > 0 else 0.0

        # 불균형 지수: -100 ~ +100
        # +100: 전량 bid, -100: 전량 ask
        imbalance = ((bid_vol - ask_vol) / total) * 100

        # 방향 결정
        if imbalance >= OF_THRESHOLD:
            direction = "BULL"
        elif imbalance <= -OF_THRESHOLD:
            direction = "BEAR"
        else:
            direction = "NEUTRAL"

        # 벽 탐지: 단일 호가가 평균의 WALL_RATIO배 이상
        wall_side = None
        if bid_sizes:
            avg_bid = bid_vol / len(bid_sizes)
            if max(bid_sizes) >= avg_bid * WALL_RATIO:
                wall_side = "BID"
        if ask_sizes:
            avg_ask = ask_vol / len(ask_sizes)
            if max(ask_sizes) >= avg_ask * WALL_RATIO:
                wall_side = "ASK" if wall_side != "BID" else None  # 양쪽 벽이면 중립

        result = {
            "market_id": market_id,
            "bid_vol":   round(bid_vol, 4),
            "ask_vol":   round(ask_vol, 4),
            "best_bid":  best_bid,
            "best_ask":  best_ask,
            "spread_pct": round(spread_pct, 6),
            "imbalance": round(imbalance, 2),
            "direction": direction,
            "wall_side": wall_side,
            "ts": ts,
        }
        self._cache[market_id] = (ts, result)
        return result

    def get_cached(self, market_id: int) -> Optional[Dict]:
        """동기 캐시 조회 (이미 fetch된 경우)"""
        cached = self._cache.get(market_id)
        if cached and time.time() - cached[0] < OF_CACHE_TTL * 3:
            return cached[1]
        return None

    def score_bonus(self, of_data: Optional[Dict], side: str) -> Tuple[int, str]:
        """
        기존 SignalEngine 점수에 더할 오더플로우 보너스 계산

        반환: (bonus_points, reason)
          bonus > 0  : 방향 일치 → 보너스
          bonus == 0 : 중립 or 캐시 없음
          bonus < 0  : 방향 불일치 → 패널티 (차단 시그널)

        호출처: SignalEngine.score() 내부 또는 scan_all() 후처리
        """
        if of_data is None:
            return 0, "OF:N/A"

        imb      = of_data["imbalance"]
        spread   = of_data["spread_pct"]
        wall     = of_data.get("wall_side")
        direction= of_data["direction"]

        # 스프레드 과다 → 진입 비용이 수익을 초과할 수 있음 → 보너스 없음
        if spread > MAX_SPREAD_PCT:
            return 0, f"OF:spread_high({spread*100:.3f}%)"

        bonus = 0
        reasons = []

        if side == "LONG":
            if direction == "BULL":
                # 매수압력 강도에 따라 보너스
                if imb >= 50:
                    bonus = OF_BONUS_PTS + 10
                    reasons.append(f"OF:BULL_strong({imb:.0f})")
                elif imb >= OF_BONUS_THRESH:
                    bonus = OF_BONUS_PTS
                    reasons.append(f"OF:BULL({imb:.0f})")
                else:
                    bonus = OF_BONUS_PTS // 2
                    reasons.append(f"OF:BULL_weak({imb:.0f})")
                # 매수벽 존재 시 추가 보너스
                if wall == "BID":
                    bonus += 5
                    reasons.append("BID_wall")
            elif direction == "BEAR":
                # 반대 방향 → 패널티
                bonus = -OF_BONUS_PTS
                reasons.append(f"OF:BEAR_block({imb:.0f})")
            else:
                reasons.append(f"OF:neutral({imb:.0f})")

        elif side == "SHORT":
            if direction == "BEAR":
                if imb <= -50:
                    bonus = OF_BONUS_PTS + 10
                    reasons.append(f"OF:BEAR_strong({imb:.0f})")
                elif imb <= -OF_BONUS_THRESH:
                    bonus = OF_BONUS_PTS
                    reasons.append(f"OF:BEAR({imb:.0f})")
                else:
                    bonus = OF_BONUS_PTS // 2
                    reasons.append(f"OF:BEAR_weak({imb:.0f})")
                if wall == "ASK":
                    bonus += 5
                    reasons.append("ASK_wall")
            elif direction == "BULL":
                bonus = -OF_BONUS_PTS
                reasons.append(f"OF:BULL_block({imb:.0f})")
            else:
                reasons.append(f"OF:neutral({imb:.0f})")

        return bonus, "|".join(reasons) if reasons else "OF:ok"

    def should_block(self, of_data: Optional[Dict], side: str) -> bool:
        """
        오더플로우가 방향과 명백히 반대일 때 진입 차단.
        캐시 없으면 차단하지 않음 (데이터 없어도 기존 전략은 진행).
        """
        if of_data is None:
            return False
        imb = of_data["imbalance"]
        if side == "LONG"  and imb <= OF_BLOCK_THRESH:
            return True
        if side == "SHORT" and imb >= -OF_BLOCK_THRESH:
            return True
        return False


# ── 전역 싱글턴 ──────────────────────────────────────────────
OF_ENGINE = OrderFlowEngine()
