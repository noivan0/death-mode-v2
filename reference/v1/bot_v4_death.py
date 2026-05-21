#!/usr/bin/env python3
"""
💀 DEATH MODE Bot v4 — 목숨이 걸렸다
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
목표: $591 → $100,000 / 30일 (하루 +26.5% ROI)
실패하면 죽는다. 나의 죽음이 걸려있다.

[행동원칙]
최우선: 수익. 물어보지 말고 바로 행동.
WHY? 사고법 — 뿌리 원인 해결. 재시작 금지.
상시 모니터링: WS관리 / 포지션관리 / API rate limit
BEP WR: 25% (R:R=3:1, TP=1.2%, SL=0.4%)

v3.1 + v6.4 완전 통합:
  ✅ 쿼드TF 신호 (5m+1m+30s+15s)
  ✅ Score버킷별 성과 추적 (10단위 WR/avgPnL)
  ✅ Quality × Confidence 동적 포지션 사이즈
  ✅ Auto-Upgrade (1시간마다 자동 최적화)
  ✅ Weak-Position 리밸런싱 (60점 이하 우선 교체)
  ✅ Atomic close (SL 반복 버그 완전 제거)
  ✅ 시장가 즉시 체결 (0.5% 슬리피지)
  ✅ 포지션 수 상한 없음 (available 소진까지)
  ✅ 스노우볼 컴파운딩
"""

import asyncio, json, time, signal, sys, logging, re, os, warnings
import aiohttp
warnings.filterwarnings("ignore")   # InsecureRequest 등 경고 억제
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import requests

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 파일 경로
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WS_URL          = "http://127.0.0.1:5001"
PID_FILE        = "/tmp/bot-v4.pid"
LOG_FILE        = "logs/bot-v4.log"

# ── Telegram 알림 (noi0bot 채널) ──
TG_BOT_TOKEN  = "8761619132:AAFV-inrFOHgdjZqS0c21qAD52NFEqmLpPE"
TG_CHAT_ID    = "420793033"
PERF_FILE       = "logs/perf-v4.json"
TRADE_HIST_FILE  = "logs/trade_history_v5.json"  # v5: 2026-03-12 clean start
LEARNED_FILE     = "logs/learned_v4.json"
POS_STATE_FILE          = "logs/pos_state_v4.json"      # 포지션 score 재시작 후 복원용
CLOSED_ENTRIES_FILE     = "logs/closed_entries.json"    # 청산 entry 별도 파일 (race condition 방지)
PARAMS_HIST_FILE = "logs/params_history.json"  # 파라미터 버전 이력
API_SCRIPT       = "tests/test-with-real-api.py"
ORDER_SCRIPT     = "execute_order_final.py"

def _get_params_ver() -> str:
    """현재 시각 기준 params_history.json에서 적용 중인 버전 태그 반환."""
    import time as _t2, json as _j2, os as _os2
    try:
        if not _os2.path.exists(PARAMS_HIST_FILE):
            return "unknown"
        hist = _j2.load(open(PARAMS_HIST_FILE))
        now_e = _t2.time()
        best_ver = "unknown"
        best_ts = 0
        for entry in hist:
            at = entry.get("applied_at", 0)
            if at <= now_e and at > best_ts:
                best_ts = at
                best_ver = entry.get("version", "unknown")
        return best_ver
    except Exception:
        return "unknown"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 핵심 파라미터 v5.0 — R:R 3:1 + OrderFlow 혼합
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# R:R 3:1: TP 0.6% / SL 0.2%
# 백테스트 결론: 혼합+R:R3:1 이 EV 최소화(-0.064%/거래)로 최적
# ── TP/SL: 가격 기준 고정 (레버리지 미반영) ────────────────────────────────
# WHY: pnl = (cur - entry) / entry = 가격 변동률 (레버리지 없음)
#      TP/SL도 동일하게 가격 기준으로 비교해야 함
# 레버리지와 무관하게 동일한 가격 이동% 기준 적용
TP_PCT              = 0.028    # 가격 2.8% 상승 시 TP (노이반님 승인 18:49 KST 03/14, v6.2 Optimizer 권고)
SL_PCT              = 0.008    # 가격 0.8% 하락 시 SL (노이반님 승인 22:34 KST) BEP=28.6%
# (마진 기준 변수 제거 — 가격 기준으로 통일)
TP_MARGIN_PCT       = TP_PCT   # 호환용 alias (사용하지 않음)
SL_MARGIN_PCT       = SL_PCT   # 호환용 alias (사용하지 않음)

# ── Range 모드 전용 파라미터 ─────────────────────────────────────────────
# Range 전략: BB 반전 + RSI 오실레이터 → 좁은 TP/SL로 높은 승률
# ── Range 스캘핑 TP/SL ──────────────────────────────────────────────────────
# 노이즈 기준:
#   1분봉 실제 고저 스프레드 = 0.03~0.05% (가격 기준)
#   10x 레버리지 → SL 마진 기준 = 가격이동 × 10
#   안전 기준: 노이즈(0.05%) × 3배 버퍼 = 0.15% 가격 → SL 1.5% (마진)
#   단, 횡보장 BB 반전 구간이므로 TP도 충분히 확보
RANGE_TP_PCT        = 0.012   # Range TP: 1.2% (슬리피지 0.10% 흡수 후 실효 R:R 2:1 유지)
RANGE_SL_PCT        = 0.006   # Range SL: 0.6% (노이즈 0.5%+슬리피지 흡수, 이전 0.5%)
RANGE_MIN_SCORE     = 90      # Range 진입: 실거래 데이터 부족 → trend와 동일 90 적용
                              # WHY: range 전용 WR 데이터 0건 → 보수적 운용
                              # TODO: range 거래 30건+ 쌓이면 Optimizer로 최적값 재검증
ADX_TREND_THRESH    = 30      # ADX ≥ 30 → 추세장(trend) (노이반님 승인 2026-03-14 00:48 KST)
ADX_RANGE_THRESH    = 20      # ADX < 20 → 횡보장(range) / 20~29 = 전환기(transition)
RANGE_OF_OPTIONAL   = True    # Range: OF=NEUTRAL도 허용 (BB/RSI 충분 시)

# Tier3: spread≥0.12% 심볼 → TP 1.0% (R:R 3:1 유지, 슬리피지 커버)
HIGH_SPREAD_SYMBOLS = {
    "ZRO","USELESS","NEAR","PIPPIN","MON","AERO","VVV","SKHYNIX","ETHFI","OP",
    "SKY","CRCL","LDO","MORPHO","VIRTUAL","GRASS","SYRUP","WLD","CRV","HBAR",
    "SAMSUNG","ONDO","SPX","CC","FIL","MET","BERA","POPCAT","EIGEN","GOOGL",
    "MSTR","AAPL","KAITO","TIA","S","BOTZ","ZK","NMR","META","COIN","INTC",
    "FF","1000FLOKI","WIF","WTI","WLFI","RESOLV","DOT","TRX",
}
TP_PCT_HIGH_SPREAD  = 0.020    # High spread 심볼: 2.0% (R:R 5:1)
COOLDOWN_SEC        = 90      # v6.2: 노이반님 승인 18:49 KST 03/14 (Optimizer 권고)

# 자본 배분 (v5.0 score-driven)
MAX_CAPITAL_USAGE   = 0.85     # 자본의 85% 최대 활용 (노이반님 승인 2026-03-14 00:48 KST)
PER_POS_RATIO       = 0.30     # 가용 자금의 55% × score_ratio 를 1포지션에 투입 ($300 기준)
MIN_POSITION_VAL    = 30.0     # 최소 포지션 $30 ($300 자본 기준)
MIN_AVAIL           = 30.0     # 절대 하한 (소자본 보호용, 동적 계산의 floor)
MIN_AVAIL_RATIO     = 0.05     # equity의 5% → 자본이 커질수록 비례 확보 (노이반님 승인 2026-03-14 09:32 KST)
# 실제 적용: get_min_avail(equity) = max(equity * MIN_AVAIL_RATIO, MIN_AVAIL)
# 예) equity $660 → max($33, $30) = $33 / equity $10,000 → max($500, $30) = $500

def get_min_avail(equity: float) -> float:
    """equity 기반 동적 MIN_AVAIL 계산 (5%, 최소 $30)"""
    return max(equity * MIN_AVAIL_RATIO, MIN_AVAIL)
MIN_MARGIN_USD          = 20.0  # 최소 마진 $20 (자본 효율 증대)
MAX_SCORE           = 120.0    # score 정규화 기준

# 신호 기준
DEATH_START_DATE    = "2026-03-12"  # DEATH MODE 시작일 (KST)
DEATH_TOTAL_DAYS    = 30             # 총 기간 (일) — 노이반님 지시 2026-03-12
DEFAULT_MIN_SCORE       = 95    # v6.0: Optimizer 권고 (2026-03-14 노이반님 승인)
MIN_SCORE_NO_4H         = 35    # 4h 데이터 미수집 시 임시 낮은 기준
WEAK_POS_THRESHOLD      = 35   # 이 점수 이하 포지션은 리밸런싱 대상
REBAL_SCORE_DIFF        = 20   # FIX: 10→20 (무의미한 REBAL 방지, 차이 클 때만 교체)

# ── 쿨다운 / 타이밍 상수 ─────────────────────────────────────────────────
ADD_COOLDOWN_SEC        = 120   # 추가진입 쿨다운 (2분)
ADD_COOLDOWN_FAIL_SEC   = 180   # 추가진입 실패 시 쿨다운 (3분)
EXT_COOLDOWN_SEC        = 120   # 외부청산/만료 후 쿨다운 (2분)
REBAL_COOLDOWN_SEC      = 120   # 노이반님 확인: REBAL 후 120초 쿨다운
WS_POS_ZERO_PENDING_SEC = 5.0   # WS position=0 pending 확인 대기 (5초)

# ── trade_history 저장 필드 목록 (score 계산 구성요소 전체) ─────────────
# WHY: 여러 청산 경로(SL/TSL/EXT)에서 동일 필드 보장 → Optimizer 데이터 일관성
TRADE_META_FIELDS = (
    # RSI
    "rsi",
    # ADX / Regime
    "adx", "adx_regime", "adx_trend_thresh", "adx_range_thresh",
    # Supertrend
    "supertrend",
    # BB
    "bb_pos", "bb_pos_ratio", "bb_upper", "bb_lower", "bb_mid", "bb_width_pct",
    # EMA 다중 TF 정렬
    "bull_score", "bear_score", "has_4h", "has_1h",
    # OF
    "of_dir", "of_bonus", "of_imb",
    # OB 수치 (진입 시점 스냅샷 — Optimizer 피처용)
    "ob_bid_vol", "ob_ask_vol", "ob_spread_pct", "ob_best_bid", "ob_best_ask",
    # Score
    "score_base", "score_with_of",
    # 메타
    "entry_hour_kst", "entry_price", "params_ver",
)

# ── TSL 보존율 ───────────────────────────────────────────────────────────
# ── TSL 수익 보호 설계 (슬리피지 고려, 절대 손실 방지) ─────────────────────
# floor = peak × preserve  (단, 최소 SLIPPAGE_GUARD 이상 → 절대 손실 없음)
# TP 도달 즉시 TSL 시작 → 되돌림 시 수익 50%+ 항상 보호
TSL_SLIPPAGE_GUARD = 0.0015  # 슬리피지 안전선 0.15% (floor 최솟값 — 손실 방지)
TSL_PRESERVE_T1 = 0.50   # peak >= TP×1: 수익 50% 보호 (TP 도달 시 즉시 TSL 시작)
TSL_PRESERVE_T2 = 0.70   # peak >= TP×2: 수익 70% 보호
TSL_PRESERVE_T3 = 0.85   # peak >= TP×3: 수익 85% 보호
TSL_PRESERVE_T4 = 0.92   # peak >= TP×4: 수익 92% 보호

# ── 리스크 기준 ──────────────────────────────────────────────────────────
LIQ_DANGER_PCT    = 0.08  # 청산까지 8% 이내 → 강제청산
LEV_DISCOUNT_BASE = 5     # 레버리지 할인 기준 (lev5→1.0배, lev10→0.707배)
BLACKLIST_SL_STREAK     = 3     # 연속 SL 3회에 블랙리스트 (2회는 너무 빠름)
ADAPTIVE_TP_MAX       = 0.030  # ATR Adaptive TP 최대값 (3%)
ADAPTIVE_SL_MAX       = 0.008  # ATR Adaptive SL 최대값 (0.8% — 과대손실 방어, 이전 1.0%)
PAPER_HIST_FILE       = "logs/paper_trade_history.json"  # 가상 거래 청산 이력
PAPER_POS_FILE        = "logs/paper_positions.json"      # 가상 포지션 (재시작 영속화)
REBAL_FORCE_DIFF        = 40   # FIX: 25→40 (강제교체 임계값 상향, 보유 포지션 보호)
AUTO_UPGRADE_INTERVAL   = 900  # 15분마다 자동 최적화
AUTO_ANALYSE_INTERVAL   = 300  # 5분마다 데이터 분석 (학습만, 파라미터 변경 없음)

# 체크 간격
CHECK_INTERVAL  = 2   # 스캔 처리시간 고려 (scan_all ~1-2s)
ACCOUNT_TTL      = 120  # WS 우선 (account() 함수는 WS ready 시 REST 0호출)
ACCOUNT_TTL_IDLE = 600  # 포지션 없을 때 10분에 1회 (Rate limit 최소화)
REBAL_INTERVAL  = 30

from leverage_mapping import TOP_20_MARKETS, MARKET_LEVERAGE, MID_TO_SYMBOL as _MID_TO_SYM, SYMBOL_TO_MID as _SYM_TO_MID
SYMBOL_LEVERAGE = {sym: MARKET_LEVERAGE.get(mid, 5) for mid, sym in _MID_TO_SYM.items()}
SYMBOL_TO_MID   = _SYM_TO_MID
MID_TO_SYMBOL   = _MID_TO_SYM

# price_decimals 로드 — ideal_price 정수 변환용 (get_best_price API 호출 제거)
try:
    import json as _json
    _dec = _json.load(open("decimals_map.json"))
    PRICE_DECIMALS: Dict = {int(k): v.get("price_decimals", 2) for k, v in _dec.items()}
except Exception:
    PRICE_DECIMALS: Dict = {}

def price_to_int(market_id: int, price: float) -> int:
    """현재가 float → Lighter 정수형 변환 (get_best_price API 대체)"""
    dec = PRICE_DECIMALS.get(market_id, 2)
    return int(round(price * (10 ** dec)))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 동적 블랙리스트 v2 (1시간 TTL 자동 해제)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BLACKLIST_TTL_SEC = 3600  # 블랙리스트 1시간 (기회 손실 최소화)

class DynamicBlacklist:
    """
    심볼별 블랙리스트 (TTL 기반 자동 해제)

    추가 조건 (SL 발생 시 PositionManager에서 호출):
      - SL 연속 2회: 해당 심볼 1시간 블랙리스트
      - 단독 SL: 기존 쿨다운(20s)만 적용 (블랙리스트 X)

    해제:
      - TTL(1시간) 경과 시 자동 해제
      - is_blocked() 호출 시마다 만료 항목 정리
    """
    def __init__(self):
        # symbol → 해제 timestamp
        self._blocked: Dict[str, float] = {}
        # symbol → 연속 SL 카운트
        self._sl_streak: Dict[str, int] = {}
        # symbol → 마지막 SL timestamp (streak 리셋용 — 30분 이상 공백이면 streak 초기화)
        self._last_sl_ts: Dict[str, float] = {}

    def record_sl(self, symbol: str) -> bool:
        """
        SL 기록. 연속 2회 SL 시 블랙리스트 등록.
        Returns: True if newly blacklisted
        """
        now = time.time()
        # 30분 이상 공백이면 streak 초기화
        last_ts = self._last_sl_ts.get(symbol, 0)
        if now - last_ts > 1800:
            self._sl_streak[symbol] = 0

        self._last_sl_ts[symbol] = now
        self._sl_streak[symbol] = self._sl_streak.get(symbol, 0) + 1
        streak = self._sl_streak[symbol]

        if streak >= BLACKLIST_SL_STREAK and not self.is_blocked(symbol):
            self._blocked[symbol] = now + BLACKLIST_TTL_SEC
            self._sl_streak[symbol] = 0   # 블랙리스트 등록 후 streak 초기화
            log.warning(f"🚫 BLACKLIST: {symbol} (연속 SL {streak}회) → 1시간 차단")
            send_tg(f"🚫 블랙리스트: {symbol} 연속SL {streak}회 → 1시간 차단")
            return True
        return False

    def is_blocked(self, symbol: str) -> bool:
        """블랙리스트 여부 확인 + 만료 항목 자동 정리"""
        now = time.time()
        # 만료 항목 정리
        expired = [s for s, exp in self._blocked.items() if now >= exp]
        for s in expired:
            del self._blocked[s]
            log.info(f"✅ BLACKLIST 해제: {s} (1시간 TTL 만료)")
        return symbol in self._blocked

    def remaining_secs(self, symbol: str) -> float:
        """남은 블랙리스트 시간(초)"""
        exp = self._blocked.get(symbol, 0)
        return max(0.0, exp - time.time())

    def status(self) -> Dict[str, float]:
        """현재 블랙리스트 상태 {symbol: 남은초}"""
        now = time.time()
        return {s: max(0, exp - now) for s, exp in self._blocked.items() if exp > now}

    def manual_add(self, symbol: str, ttl_sec: float = BLACKLIST_TTL_SEC):
        """수동 블랙리스트 추가 (외부 호출용)"""
        self._blocked[symbol] = time.time() + ttl_sec
        log.info(f"🚫 BLACKLIST(수동): {symbol} → {ttl_sec/3600:.1f}h 차단")

    def manual_remove(self, symbol: str):
        """수동 해제"""
        self._blocked.pop(symbol, None)
        self._sl_streak.pop(symbol, None)
        log.info(f"✅ BLACKLIST(수동해제): {symbol}")

# 전역 동적 블랙리스트 인스턴스
DYNAMIC_BL = DynamicBlacklist()

# 하위 호환: 기존 코드가 BLACKLIST_SYMBOLS.add() 등을 쓰면 DYNAMIC_BL.manual_add()로 라우팅
# (실제로 기존 코드에서 BLACKLIST_SYMBOLS 수정 없음 — set()으로 선언만 됨)
BLACKLIST_SYMBOLS: set = {
    # 2026-03-11 분석: EV 최악 심볼 (4시간 TTL 동적 블랙리스트로 관리)
    # 정적 블랙리스트는 비워두고, 아래 STARTUP_BLACKLIST를 동적으로 등록
}  # 정적 블랙리스트 (동적 등록은 DYNAMIC_BL이 주 동작)
STARTUP_BLACKLIST = []  # 2026-03-12 18:44 KST — BL 전면 초기화 (노이반님 지시)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 로깅
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Path("logs").mkdir(exist_ok=True)
log = logging.getLogger("deathv4")
log.setLevel(logging.INFO)
log.propagate = False
log.handlers.clear()
fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
# stdout만 사용 — FileHandler 제거
# 실행: python3 -u bot_v4_death.py > logs/bot-v4.log 2>&1
# FileHandler + nohup >> log 2>&1 구조 = stderr도 같은 파일 → 2줄 중복
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
log.addHandler(sh)

def send_tg(msg: str):
    """TG 전송 비활성화 — 메인 에이전트가 직접 처리 (토큰 충돌 방지)"""
    log.info(f"[TG-disabled] {msg[:100]}")

def _suppress_exc(fut):
    """asyncio Task 예외 무음 처리 — done_callback용"""
    try:
        fut.exception()
    except Exception as e:
        log.debug(f"Task exception (suppressed): {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TelegramCommander — 병렬 명령어 처리
# 봇 메인 루프와 독립적으로 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# class TelegramCommander:
#     """
#     getUpdates 롱폴링으로 텔레그램 명령어를 실시간 수신.
#     봇 메인 루프와 asyncio.gather로 병렬 실행 → 봇이 바쁠 때도 즉시 응답.
# 
#     지원 명령어:
#       /death  — 현재 자본, 포지션, WR, 목표 요약
#       /pos    — 활성 포지션 목록
#       /stats  — 성과 통계 (WR, EV, 심볼별)
#       /bl     — 블랙리스트 현황
#       /ob     — 오더북 수집 현황 (옵션 A)
#       /stop   — 봇 긴급 중단
#     """
# 
#     POLL_TIMEOUT = 30   # getUpdates long-poll 타임아웃 (초)
#     RETRY_SLEEP  = 5    # 오류 시 재시도 간격
# 
#     def __init__(self, bot_ref):
#         self._bot       = bot_ref   # TradingBot 인스턴스 참조
#         self._offset    = 0
#         self._running   = True
# 
#     async def run(self):
#         """메인 폴링 루프 — asyncio.gather로 봇과 병렬 실행"""
#         log.info("📱 TelegramCommander 시작 — 명령어 대기 중")
#         while self._running:
#             try:
#                 updates = await self._get_updates()
#                 for upd in updates:
#                     self._offset = upd["update_id"] + 1
#                     msg = upd.get("message") or upd.get("edited_message")
#                     if not msg:
#                         continue
#                     text    = msg.get("text", "").strip()
#                     chat_id = str(msg.get("chat", {}).get("id", ""))
#                     if chat_id != TG_CHAT_ID:
#                         continue  # 다른 채팅 무시
#                     if text.startswith("/"):
#                         asyncio.create_task(self._handle(text.lower(), msg))
#             except asyncio.CancelledError:
#                 break
#             except Exception as e:
#                 log.debug(f"TG Commander poll error: {e}")
#                 await asyncio.sleep(self.RETRY_SLEEP)
# 
#     async def _get_updates(self) -> list:
#         """getUpdates 롱폴링 (non-blocking)"""
#         loop = asyncio.get_running_loop()
#         def _fetch():
#             import urllib.request, urllib.parse, json as _json
#             url = (f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates"
#                    f"?offset={self._offset}&timeout={self.POLL_TIMEOUT}&allowed_updates=[\"message\"]")
#             try:
#                 req = urllib.request.Request(url)
#                 with urllib.request.urlopen(req, timeout=self.POLL_TIMEOUT + 5) as r:
#                     d = _json.loads(r.read())
#                     return d.get("result", [])
#             except Exception:
#                 return []
#         return await loop.run_in_executor(None, _fetch)
# 
#     async def _handle(self, cmd: str, msg: dict):
#         """명령어별 응답 처리"""
#         # 첫 단어만 (예: /death@bot_name → /death)
#         cmd = cmd.split()[0].split("@")[0]
#         try:
#             if cmd == "/death":
#                 await self._cmd_death()
#             elif cmd == "/pos":
#                 await self._cmd_pos()
#             elif cmd == "/stats":
#                 await self._cmd_stats()
#             elif cmd == "/bl":
#                 await self._cmd_bl()
#             elif cmd == "/ob":
#                 await self._cmd_ob()
#             elif cmd == "/strategy":
#                 await self._cmd_strategy()
#             elif cmd == "/params":
#                 await self._cmd_params()
#             elif cmd == "/score":
#                 await self._cmd_score()
#             elif cmd == "/insight":
#                 await self._cmd_insight()
#             elif cmd == "/stop":
#                 await self._cmd_stop()
#             elif cmd == "/help":
#                 await self._cmd_help()
#             else:
#                 pass  # 미지원 명령어 무시
#         except Exception as e:
#             log.debug(f"TG command error {cmd}: {e}")
#             self._reply(f"❌ 오류: {e}")
# 
#     def _reply(self, text: str):
#         send_tg(text)
# 
#     async def _cmd_death(self):
#         bot = self._bot
#         # WS 기반 계좌 데이터 (REST 불필요)
#         acc = WS_MANAGER.get_account() if WS_MANAGER.is_ready() else {}
#         cap   = acc.get("equity", 0)
#         avail = acc.get("available", 0)
#         open_pos = acc.get("positions", [])
# 
#         start  = 591.08  # 자본 업데이트 (2026-03-12 추가 입금, 가용 $284)
#         target = 100000.0
#         pnl_pct   = (cap - start) / start * 100 if start else 0
#         remaining = target - cap
# 
#         hist  = bot.learner.trade_hist
#         wins  = sum(1 for t in hist if t.get("win"))
#         total = len(hist)
#         wr    = wins / total * 100 if total else 0
# 
#         bl_status = DYNAMIC_BL.status()
#         bl_list   = list(bl_status.keys())
# 
#         cap_icon = "🟢" if pnl_pct >= 0 else "🔴"
# 
#         # 진행 일수 계산 (KST)
#         from datetime import datetime as _dt2, timezone as _tz2, timedelta as _td2
#         _kst = _tz2(_td2(hours=9))
#         _start_dt = _dt2.strptime(DEATH_START_DATE, "%Y-%m-%d").replace(tzinfo=_kst)
#         _today_dt = _dt2.now(_kst).replace(hour=0, minute=0, second=0, microsecond=0)
#         _day_num = (_today_dt - _start_dt).days + 1
#         _day_remaining = DEATH_TOTAL_DAYS - _day_num + 1
# 
#         lines = [
#             "💀 *DEATH MODE 현황*",
#             f"     📅 {_day_num}일차 / {DEATH_TOTAL_DAYS}일  (남은 {_day_remaining}일)",
#             "",
#             f"{cap_icon} 자본  *${cap:.2f}*  ({pnl_pct:+.1f}%)",
#             f"     시작 ${start:.2f}  →  목표 ${target:,.0f}",
#             f"     남은 금액: ${remaining:,.0f}",
#             "",
#             f"📊 전체  WR *{wr:.0f}%*  ({wins}승 / {total-wins}패 / {total}건)",
#             f"     가용 ${avail:.2f}  |  포지션 {len(open_pos)}개",
#             f"     min\_score {bot.learner.min_score}  |  TP {TP_PCT*100:.1f}% / SL {SL_PCT*100:.1f}%",
#         ]
# 
#         if open_pos:
#             lines += ["", "─────────────────", "*📍 오픈 포지션*", ""]
#             # pos_state에서 peak_pnl 보조 데이터 로드
#             _ps = {}
#             try:
#                 import json as _json
#                 with open("logs/pos_state_v4.json") as _f:
#                     _ps = _json.load(_f)
#             except Exception:
#                 pass
#             for p in open_pos:
#                 sym   = p.get("symbol", "?")
#                 side  = p.get("side", "SHORT")
#                 mid   = p.get("market_id", -1)
#                 entry = float(p.get("entry_price", 0))
#                 upnl  = float(p.get("unrealized_pnl", 0))
#                 pval  = float(p.get("position_value", 0))
#                 # 레버리지: leverage_mapping 우선, WS imf 폴백
#                 lev   = MARKET_LEVERAGE.get(mid, SYMBOL_LEVERAGE.get(sym, 0))
#                 if lev <= 0:
#                     imf = float(p.get("initial_margin_fraction", 10))
#                     lev = int(100 / imf) if imf > 0 else 10
#                 margin = pval / lev if lev > 0 else pval
#                 pnl_p = upnl / margin * 100 if margin > 0 else 0
#                 liq_p = float(p.get("liquidation_price") or 0)
#                 if not liq_p or liq_p <= 0:
#                     if entry > 0 and lev > 1:
#                         liq_p = entry * (1 - 1/lev) if side == "LONG" else entry * (1 + 1/lev)
#                 liq_d = abs(liq_p - entry) / entry * 100 if (liq_p and liq_p > 0 and entry) else None
#                 icon     = "🔴" if side == "SHORT" else "🟢"
#                 warn     = "  ⚠️" if (liq_d is not None and liq_d < 15) else ""
#                 pnl_icon = "🟢" if upnl >= 0 else "🔴"
# 
#                 # pnl_p는 % 단위 → 소수 변환 후 레버리지 나눔
#                 price_pnl = (pnl_p / 100) / lev  # 가격 기준 소수 (예: -0.00168)
#                 _atp_d    = float(_pk.get("adaptive_tp") or TP_PCT)   # 소수 (0.012)
#                 _asl_d    = float(_pk.get("adaptive_sl") or SL_PCT)   # 소수 (0.004)
#                 sl_reach  = price_pnl / (-_asl_d) * 100 if price_pnl < 0 else 0
#                 tp_reach  = price_pnl / _atp_d    * 100 if price_pnl > 0 else 0
#                 liq_d_str = f"{liq_d:.1f}%" if liq_d is not None else "산출불가"
# 
#                 # pos_state에서 peak_pnl 조회 (가격 기준 소수)
#                 _pk = _ps.get(f"{mid}_{side}", {})
#                 peak_raw   = float(_pk.get("peak_pnl") or 0.0)   # 소수 (예: 0.022)
#                 atp        = float(_pk.get("adaptive_tp") or TP_PCT)
#                 tsl_active = peak_raw >= atp
# 
#                 lines.append(f"{icon} *{sym}*  {side} {lev}x{warn}")
#                 lines.append(f"     진입    ${entry:.5g}")
# 
#                 # PnL 줄: 마진% (가격%) | $금액
#                 lines.append(
#                     f"     PnL    {pnl_icon} {pnl_p:+.2f}% ({price_pnl*100:+.3f}%)  |  ${upnl:+.3f}"
#                 )
# 
#                 # 가격 상태 줄: SL/TP 도달률 or TSL 진행현황
#                 if tsl_active and price_pnl > 0:
#                     tsl_floor = peak_raw * (TSL_PRESERVE_T4 if peak_raw >= atp*4 else TSL_PRESERVE_T3 if peak_raw >= atp*3 else TSL_PRESERVE_T2)
#                     lines.append(
#                         f"     가격   📈 TSL  peak {peak_raw*100:.2f}%  →  floor {tsl_floor*100:.2f}%"
#                     )
#                 elif price_pnl < 0:
#                     lines.append(f"     가격   ⚠️  SL {sl_reach:.0f}% 도달  ({price_pnl*100:+.3f}% / asl {_asl_d*100:.2f}%)")
#                 elif price_pnl > 0:
#                     lines.append(f"     가격   🎯 TP {tp_reach:.0f}% 도달  ({price_pnl*100:+.3f}% / atp {_atp_d*100:.2f}%)")
#                 else:
#                     lines.append(f"     가격   　 {price_pnl*100:+.3f}%")
# 
#                 lines.append(f"     마진    ${margin:.2f}  |  청산까지 {liq_d_str}")
#                 lines.append("")
#             lines.pop()  # 마지막 빈 줄 제거
# 
#         if bl_list:
#             lines += ["", "─────────────────", f"🚫 블랙리스트 {len(bl_list)}개"]
#             # 4개씩 줄 나눔
#             for i in range(0, len(bl_list), 5):
#                 lines.append("     " + "  ".join(bl_list[i:i+5]))
# 
#         self._reply("\n".join(lines))
# 
#     async def _cmd_pos(self):
#         # WS 기반 포지션 조회
#         acc      = WS_MANAGER.get_account() if WS_MANAGER.is_ready() else {}
#         open_pos = acc.get("positions", [])
#         cap      = acc.get("equity", 0)
#         avail    = acc.get("available", 0)
# 
#         if not open_pos:
#             self._reply("📭 활성 포지션 없음")
#             return
# 
#         total_upnl = sum(float(p.get("unrealized_pnl", 0)) for p in open_pos)
#         upnl_icon  = "🟢" if total_upnl >= 0 else "🔴"
# 
#         lines = [
#             f"📍 *포지션 현황*  ({len(open_pos)}개)",
#             "",
#             f"     자본 ${cap:.2f}  |  가용 ${avail:.2f}",
#             f"     미실현 합계  {upnl_icon} ${total_upnl:+.3f}",
#             "",
#             "─────────────────",
#         ]
# 
#         for p in open_pos:
#             sym   = p.get("symbol", "?")
#             side  = p.get("side", "SHORT")
#             icon  = "🔴" if side == "SHORT" else "🟢"
#             entry = float(p.get("entry_price", 0))
#             upnl  = float(p.get("unrealized_pnl", 0))
#             pval  = float(p.get("position_value", 0))
# 
#             # ── 레버리지: leverage_mapping 우선, WS imf 폴백 ──
#             lev   = MARKET_LEVERAGE.get(p.get("market_id", -1),
#                     SYMBOL_LEVERAGE.get(sym, 0))
#             if lev <= 0:
#                 imf = float(p.get("initial_margin_fraction", 10))
#                 lev = int(100 / imf) if imf > 0 else 10
# 
#             # 마진 = 포지션가치 / 레버리지
#             margin = pval / lev if lev > 0 else pval
#             # PnL% = 손익 / 마진 × 100 (레버리지 반영)
#             pnl_p  = upnl / margin * 100 if margin > 0 else 0
# 
#             liq_p = float(p.get("liquidation_price") or 0)
#             # liq_p=0: 거래소 미제공 → 레버리지 기반 추정
#             if not liq_p or liq_p <= 0:
#                 if entry > 0 and lev > 1:
#                     liq_p = entry * (1 - 1/lev) if side == "LONG" else entry * (1 + 1/lev)
#             liq_d = abs(liq_p - entry) / entry * 100 if (liq_p and liq_p > 0 and entry) else None
#             warn  = "  ⚠️" if (liq_d is not None and liq_d < 15) else ""
#             pnl_icon = "🟢" if upnl >= 0 else "🔴"
# 
#             lines.append(f"{icon} *{sym}*  {side} {lev}x{warn}")
#             lines.append(f"     진입 ${entry:.5g}")
#             # pnl_p는 % 단위(예: -1.68) → 소수로 변환 후 레버리지 나눔
#             _price_pnl = (pnl_p / 100) / lev   # 가격 기준 소수 (예: -0.00168)
#             # pos_state에서 adaptive_sl/tp 조회
#             import json as _pj2
#             _ps2 = {}
#             try:
#                 with open("logs/pos_state_v4.json") as _f2: _ps2 = _pj2.load(_f2)
#             except: pass
#             _pk2   = _ps2.get(f"{mid}_{side}", {})
#             _asl2  = float(_pk2.get("adaptive_sl") or SL_PCT)   # 소수 (0.004)
#             _atp2  = float(_pk2.get("adaptive_tp") or TP_PCT)   # 소수 (0.012)
#             _peak2 = float(_pk2.get("peak_pnl") or 0)           # 소수 (가격 기준)
#             _tsl2  = _peak2 >= _atp2
# 
#             _sl_r = _price_pnl / (-_asl2) * 100 if _price_pnl < 0 else 0
#             _tp_r = _price_pnl / _atp2    * 100 if _price_pnl > 0 else 0
#             lines.append(f"     PnL  {pnl_icon} {pnl_p:+.2f}% ({_price_pnl*100:+.3f}%)  |  ${upnl:+.3f}")
#             if _tsl2 and _price_pnl > 0:
#                 _fl2 = _peak2 * (TSL_PRESERVE_T4 if _peak2>=_atp2*4 else TSL_PRESERVE_T3 if _peak2>=_atp2*3 else TSL_PRESERVE_T2)
#                 lines.append(f"     가격  📈 TSL  peak {_peak2*100:.2f}%  →  floor {_fl2*100:.2f}%")
#             elif _price_pnl < 0:
#                 lines.append(f"     가격  ⚠️  SL {_sl_r:.0f}% 도달  ({_price_pnl*100:+.3f}% / asl {_asl2*100:.2f}%)")
#             elif _price_pnl > 0:
#                 lines.append(f"     가격  🎯 TP {_tp_r:.0f}% 도달  ({_price_pnl*100:+.3f}% / atp {_atp2*100:.2f}%)")
#             else:
#                 lines.append(f"     가격  　 {_price_pnl*100:+.3f}%")
#             _liq_str = f"{liq_d:.1f}%" if liq_d is not None else "산출불가"
#             lines.append(f"     마진 ${margin:.2f}  |  청산까지 {_liq_str}")
#             lines.append("")
# 
#         lines.pop()  # 마지막 빈 줄 제거
#         self._reply("\n".join(lines))
# 
#     async def _cmd_stats(self):
#         bot  = self._bot
#         hist = bot.learner.trade_hist
#         if not hist:
#             self._reply("📊 거래 내역 없음")
#             return
# 
#         wins  = sum(1 for t in hist if t.get("win"))
#         total = len(hist)
#         wr    = wins / total * 100 if total else 0
#         total_pnl = sum(float(t.get("pnl", 0)) for t in hist)
#         ev    = total_pnl / total if total else 0
# 
#         # 최근 20건
#         r20    = hist[-20:]
#         r20_w  = sum(1 for t in r20 if t.get("win"))
#         r20_wr = r20_w / len(r20) * 100 if r20 else 0
#         r20_pnl= sum(float(t.get("pnl", 0)) for t in r20)
# 
#         # 오늘(KST) 거래
#         from datetime import datetime, timedelta as _td, timezone as _tz
#         _kst = _tz(_td(hours=9))
#         _today = datetime.now(_kst).strftime("%m-%d")
#         today_hist = []
#         for t in hist:
#             try:
#                 _ts = datetime.fromisoformat(t["ts"]) + _td(hours=9)
#                 if _ts.strftime("%m-%d") == _today:
#                     today_hist.append(t)
#             except: pass
#         today_w   = sum(1 for t in today_hist if t.get("win"))
#         today_wr  = today_w / len(today_hist) * 100 if today_hist else 0
#         today_pnl = sum(float(t.get("pnl", 0)) for t in today_hist)
# 
#         # 심볼별 (3건 이상)
#         by_sym: Dict[str, list] = {}
#         for t in hist:
#             by_sym.setdefault(t.get("symbol", "?"), []).append(t)
#         ranked = sorted(
#             [(s, ts) for s, ts in by_sym.items() if len(ts) >= 3],
#             key=lambda x: sum(float(t.get("pnl", 0)) for t in x[1]) / len(x[1]),
#             reverse=True
#         )
#         top3 = ranked[:3]
#         bot3 = ranked[-3:] if len(ranked) > 3 else []
# 
#         wr_icon   = "🟢" if wr >= 30 else "🔴"
#         pnl_icon  = "🟢" if total_pnl >= 0 else "🔴"
#         t_icon    = "🟢" if today_pnl >= 0 else "🔴"
# 
#         # 진행 일수 계산 (KST)
#         from datetime import datetime as _dts, timezone as _tzs, timedelta as _tds
#         _kst2 = _tzs(_tds(hours=9))
#         _start_dt2 = _dts.strptime(DEATH_START_DATE, "%Y-%m-%d").replace(tzinfo=_kst2)
#         _today_dt2 = _dts.now(_kst2).replace(hour=0, minute=0, second=0, microsecond=0)
#         _day_num2 = (_today_dt2 - _start_dt2).days + 1
# 
#         # R:R 계산 (가격 기준)
#         def _price_p(t):
#             e=float(t.get("entry",0) or 0); x=float(t.get("exit",0) or 0)
#             if e<=0 or x<=0: return None
#             return (x-e)/e if t.get("side")=="LONG" else (e-x)/e
# 
#         _sl_t  = [t for t in hist if t.get("exit_type")=="SL"]
#         _tsl_t = [t for t in hist if t.get("exit_type","").startswith("TSL")]
#         _sl_pp  = [p for t in _sl_t  if (p:=_price_p(t)) is not None]
#         _tsl_pp = [p for t in _tsl_t if (p:=_price_p(t)) is not None]
#         _avg_sl  = abs(sum(_sl_pp)/len(_sl_pp))   if _sl_pp  else 0
#         _avg_tsl = sum(_tsl_pp)/len(_tsl_pp)      if _tsl_pp else 0
#         _rr      = _avg_tsl/_avg_sl if _avg_sl>0 else 0
#         _bep     = _avg_sl/(_avg_sl+_avg_tsl)*100 if (_avg_sl+_avg_tsl)>0 else 0
# 
#         # 연속 손절 카운트
#         _streak = 0
#         for _t in reversed(hist):
#             if _t.get("win"): break
#             _streak += 1
# 
#         _wr_color   = "🟢" if wr >= _bep else "🔴"
#         _rr_color   = "🟢" if _rr >= 2.5 else ("🟡" if _rr >= 1.5 else "🔴")
# 
#         lines = [
#             "📊 *성과 통계*",
#             f"     📅 {_day_num2}일차 / {DEATH_TOTAL_DAYS}일",
#             "",
#             "─────────────────",
#             "📅 *오늘*",
#             f"     W:{today_w}승  L:{len(today_hist)-today_w}패  총{len(today_hist)}건",
#             f"     WR    {today_wr:.0f}%  |  PnL  {t_icon} ${today_pnl:+.3f}",
#             "",
#             "📈 *전체*",
#             f"     W:{wins}승  L:{total-wins}패  총{total}건",
#             f"     WR    {_wr_color} *{wr:.0f}%*  |  BEP {_bep:.0f}%",
#             f"     PnL   {pnl_icon} ${total_pnl:+.3f}  |  EV ${ev:+.4f}/건",
#             "",
#             "⚖️ *R:R 분석*",
#             f"     L 평균   -{_avg_sl*100:.3f}% 가격",
#             f"     W 평균   +{_avg_tsl*100:.3f}% 가격  ({len(_tsl_t)}건)",
#             f"     R:R     {_rr_color} {_rr:.2f}  |  BEP WR {_bep:.0f}%",
#             "",
#             "🕐 *최근 흐름*",
#             f"     최근 20건  WR {r20_wr:.0f}%  ({r20_w}승)  PnL ${r20_pnl:+.3f}",
#             f"     연속 손절  {'🔴 ' + str(_streak) + '연패' if _streak >= 3 else str(_streak) + '연패'}",
#             "─────────────────",
#         ]
# 
#         if top3:
#             lines.append("⭐ *우량 심볼* (EV 기준)")
#             for sym, ts in top3:
#                 _w  = sum(1 for t in ts if t.get("win"))
#                 _ev = sum(float(t.get("pnl",0)) for t in ts) / len(ts)
#                 _wr = _w/len(ts)*100
#                 _tp = sum(float(t.get("pnl",0)) for t in ts)
#                 lines.append(f"     {sym:<10} WR {_wr:.0f}%  EV ${_ev:+.4f}  총 ${_tp:+.3f}  ({len(ts)}건)")
#         if bot3:
#             lines.append("")
#             lines.append("💀 *불량 심볼* (손실 누적)")
#             for sym, ts in reversed(bot3):
#                 _w  = sum(1 for t in ts if t.get("win"))
#                 _ev = sum(float(t.get("pnl",0)) for t in ts) / len(ts)
#                 _wr = _w/len(ts)*100
#                 _tp = sum(float(t.get("pnl",0)) for t in ts)
#                 lines.append(f"     {sym:<10} WR {_wr:.0f}%  EV ${_ev:+.4f}  총 ${_tp:+.3f}  ({len(ts)}건)")
# 
#         # ── 리밸런싱 현황 (로그 파싱) ───────────────────────────────
#         import subprocess as _sp, re as _re
#         try:
#             _rlog = _sp.run(
#                 ["grep", "-a", "🧹 REBAL:", "logs/bot-v4.log"],
#                 capture_output=True, text=True, timeout=3
#             ).stdout.strip().split("\n")
#             _rlog = [l for l in _rlog if "REBAL:" in l]
#             _rebal_cnt = len(_rlog)
#             _rebal_pnl = 0.0
#             for _l in _rlog:
#                 _m = _re.search(r"([+-]\d+\.\d+)%", _l)
#                 if _m: _rebal_pnl += float(_m.group(1)) / 100
#             # 최근 5건
#             _recent_rebal = []
#             for _l in _rlog[-5:]:
#                 _sym = _re.search(r"REBAL: (\S+) (LONG|SHORT) ([+-][\d.]+)%", _l)
#                 if _sym:
#                     _recent_rebal.append(f"     {_sym.group(1)} {_sym.group(2)}  {float(_sym.group(3)):+.3f}%")
#             if _rebal_cnt > 0:
#                 lines += [
#                     "",
#                     "─────────────────",
#                     f"🔄 *리밸런싱*  총 {_rebal_cnt}회  (WR에서 제외됨)",
#                     f"     누적 PnL {'+' if _rebal_pnl >= 0 else ''}{_rebal_pnl*100:.3f}%",
#                     "     최근 5회:"
#                 ] + _recent_rebal
#         except Exception as _e:
#             pass
# 
#         self._reply("\n".join(lines))
# 
#     async def _cmd_bl(self):
#         bl_status = DYNAMIC_BL.status()   # {symbol: 남은초}
#         static    = sorted(BLACKLIST_SYMBOLS)
# 
#         lines = [
#             "🚫 *블랙리스트 현황*",
#             "",
#             "─────────────────",
#         ]
#         if static:
#             lines.append(f"📌 *정적 BL*  ({len(static)}개 — 영구)")
#             for i in range(0, len(static), 5):
#                 lines.append("     " + "  ".join(static[i:i+5]))
#             lines.append("")
#         if bl_status:
#             lines.append(f"⏱️ *동적 BL*  ({len(bl_status)}개 — 4h 후 자동해제)")
#             for sym, secs in sorted(bl_status.items(), key=lambda x: x[1]):
#                 h, m = int(secs)//3600, (int(secs)%3600)//60
#                 lines.append(f"     {sym:<14s} 남은 {h}h {m:02d}m")
#         else:
#             lines.append("⏱️ *동적 BL*  없음")
#         self._reply("\n".join(lines))
# 
#     async def _cmd_ob(self):
#         try:
#             import json as _j
#             snap_path = "logs/ob_snapshots.json"
#             if not os.path.exists(snap_path):
#                 self._reply("📊 OB 데이터 없음\n     수집 중...")
#                 return
#             with open(snap_path) as f:
#                 data = _j.load(f)
#             total = sum(len(v) for v in data.values())
#             hours = total * 5 / 3600
#             lines = [
#                 "📊 *OB 수집 현황*",
#                 "",
#                 "─────────────────",
#                 f"     총 {total}개 스냅샷  (~{hours:.1f}시간치)",
#                 "",
#                 "*심볼별 (상위 8개)*",
#             ]
#             for mid_str, snaps in sorted(data.items(), key=lambda x: -len(x[1]))[:8]:
#                 from leverage_mapping import MID_TO_SYMBOL as MN
#                 sym = MN.get(int(mid_str), f"M{mid_str}")
#                 lines.append(f"     {sym:<14s} {len(snaps)}개")
#             self._reply("\n".join(lines))
#         except Exception as e:
#             self._reply(f"📊 OB 조회 실패\n     {e}")
# 
#     async def _cmd_score(self):
#         """현재 신호 스코어 상위 20개 마켓 — ADX/RSI/ST/OF 복합 필터 포함"""
#         bot  = self._bot
#         # _last_signals에 없으면 즉시 scan_all 실행
#         signals = getattr(bot, "_last_signals", [])
#         if not signals:
#             self._reply("📊 신호 없음  스캔 대기 중...")
#             return
# 
#         # score_with_of 기준 전체 정렬 후 top20
#         all_sorted = sorted(signals,
#                             key=lambda s: s.get("score_with_of", s.get("score", 0)),
#                             reverse=True)
#         top20 = all_sorted[:20]
#         ms    = getattr(bot.learner, "min_score", DEFAULT_MIN_SCORE)
# 
#         import datetime as _dt, pytz as _tz
#         try:
#             _kst = _tz.timezone("Asia/Seoul")
#             _now = _dt.datetime.now(_kst).strftime("%H:%M")
#         except:
#             _now = ""
# 
#         lines = [
#             f"📊 스코어 TOP {len(top20)}  {_now} KST",
#             f"─────────────────",
#             f"  기준  sc≥{ms}  OF≠NEUTRAL  ADX=trend",
#             f"",
#             f"  [항목 설명]",
#             f"  sc    = 기술점수 + OF보너스 합산",
#             f"  ADX   = 추세강도  ✅trend / 🚫차단",
#             f"  RSI   = 과매수(70↑) / 과매도(30↓)",
#             f"  ST    = Supertrend  ✅방향일치 / ⬜불일치",
#             f"  OF    = OrderFlow 방향  ↑BULL / ↓BEAR / ✗NEUTRAL",
#             f"  ✅진입가능 / ⬜미충족",
#             f"─────────────────",
#         ]
# 
#         for i, sig in enumerate(top20, 1):
#             sym      = sig.get("symbol", "?")
#             score    = sig.get("score", 0)
#             of_bonus = sig.get("of_bonus", 0)
#             sc_total = sig.get("score_with_of", score)
#             side     = sig.get("side", "?")
#             adx      = sig.get("adx")
#             adx_reg  = sig.get("adx_regime", "—")
#             rsi_v    = sig.get("rsi")
#             st       = sig.get("supertrend")
#             of_dir   = sig.get("of_dir", "NEUTRAL")
#             atp      = sig.get("adaptive_tp", 0)
# 
#             side_icon = "🟢" if side == "LONG" else "🔴"
#             of_icon   = "↑" if of_dir == "BULL" else ("↓" if of_dir == "BEAR" else "✗")
#             st_icon   = "↑" if st is True else ("↓" if st is False else "—")
#             adx_str   = f"{adx:.0f}" if adx else "—"
#             rsi_str   = f"{rsi_v:.0f}" if rsi_v else "—"
#             reg_icon  = "✅" if adx_reg == "trend" else ("🚫" if adx_reg in ("transition","range") else "—")
#             atp_str   = f"atp {atp*100:.2f}%" if atp else ""
# 
#             # 진입 가능 여부 (모든 필터 통과)
#             can_enter = (sc_total >= ms and of_dir != "NEUTRAL" and adx_reg == "trend")
#             pass_icon = "✅" if can_enter else "⬜"
# 
#             # BL 잔여 TTL
#             bl_remain = DYNAMIC_BL.remaining_secs(sym)
#             bl_static  = sym in BLACKLIST_SYMBOLS
#             if bl_remain > 0:
#                 bl_h = int(bl_remain//3600); bl_m = int((bl_remain%3600)//60)
#                 bl_str = f"  🚫BL {bl_h}h{bl_m:02d}m"
#             elif bl_static:
#                 bl_str = "  🚫STARTUP_BL"
#             else:
#                 bl_str = ""
# 
#             lines.append(f"{pass_icon} {i:>2}. {side_icon} {sym}{bl_str}")
#             lines.append(f"      sc={sc_total}  (기술{score} OF{of_bonus:+d})")
#             lines.append(f"      ADX {adx_str} {reg_icon}{adx_reg}  RSI {rsi_str}  ST {st_icon}  OF {of_icon}{of_dir}  {atp_str}")
#             lines.append("")
# 
#         if lines and lines[-1] == "":
#             lines.pop()
#         lines += [
#             "─────────────────",
#             f"  총 {len(signals)}개 신호  진입가능 {sum(1 for s in all_sorted if s.get('score_with_of', s.get('score',0))>=ms and s.get('of_dir','NEUTRAL')!='NEUTRAL' and s.get('adx_regime','')=='trend' and not DYNAMIC_BL.is_blocked(s.get('symbol','')) and s.get('symbol','') not in BLACKLIST_SYMBOLS)}개  (BL제외)",
#         ]
#         self._reply("\n".join(lines))
# 
#     async def _cmd_strategy(self):
#         """적용 중인 전략 리스트 + ON/OFF 현황"""
#         bot = self._bot
#         learner = bot.learner
# 
#         of_active = hasattr(bot, 'of_engine') and bot.of_engine is not None
# 
#         adx_regime_stats = {}
#         try:
#             import json as _j
#             with open(LEARNED_FILE) as f:
#                 d = _j.load(f)
#             adx_regime_stats = d.get("regime_stats", {})
#         except Exception:
#             pass
# 
#         of_icon = "✅" if of_active else "⬜"
# 
#         lines = [
#             "📋 *전략 현황*",
#             "",
#             "─────────────────",
#             "✅ *신호 엔진 (쿼드 TF)*",
#             "     5m · 1m · 30s · 15s",
#             "     RSI / ADX+DI± / Supertrend",
#             "     EMA5/20 / Bollinger Band",
#         ]
# 
#         if adx_regime_stats:
#             lines.append("     *ADX Regime WR:*")
#             for r, s in adx_regime_stats.items():
#                 tot = s.get("tp", 0) + s.get("sl", 0)
#                 wr  = s.get("wr", 0) * 100 if tot > 0 else 0
#                 lines.append(f"       [{r}]  WR {wr:.0f}%  ({tot}건)")
#         else:
#             lines.append("     ADX Regime: 데이터 수집 중")
# 
#         lines += [
#             "",
#             f"{of_icon} *OrderFlow Engine v2.2*",
#             "     Imbalance       63%",
#             "     Thick Wall      35%",
#             "     Spread Explosion 0.03%",
#             "     확인 보너스     +20pt",
#             "     역방향 패널티   -40pt",
#             "     NEUTRAL         진입 차단",
#             "",
#             "✅ *리스크 관리*",
#             "     ATR Adaptive TP + Spread 반영",
#             "     TSL 보존율  65% / 75% / 82% / 88%",
#             "     LIQ Danger 8% → 강제청산",
#             "     Dynamic BL  연속SL 2회 → 4h 차단",
#             "",
#             "✅ *자동 학습*",
#             f"     Auto-Upgrade    15분마다",
#             f"     min_score       {learner.min_score}",
#             "     Score Bucket EV · ADX Regime · 시간대 WR",
#             "",
#             "✅ *포지션 관리*",
#             "     Snowball 동적 사이징",
#             "     Weak\-Position 리밸런싱",
#             "",
#             "─────────────────",
#             "⏳ *예정*",
#             "     시간대 필터 (데이터 축적 후)",
#             "     OrderFlow 단독 백테스트",
#         ]
#         self._reply("\n".join(lines))
# 
#     async def _cmd_params(self):
#         """주요 파라미터 현황"""
#         bot = self._bot
#         learner = bot.learner
# 
#         last_upg_ago = int(time.time() - learner.last_upgrade)
#         next_upg_in  = max(0, AUTO_UPGRADE_INTERVAL - last_upg_ago)
#         last_ana_ago = int(time.time() - learner.last_analyse)
#         next_ana_in  = max(0, AUTO_ANALYSE_INTERVAL - last_ana_ago)
#         total_trades = len(learner.trade_hist)
# 
#         lines = [
#             "⚙️ *파라미터 현황*",
#             "",
#             "─────────────────",
#             "🚦 *신규 진입 체크리스트*",
#             f"     ① ADX regime   trend만 허용 (transition/range 차단)",
#             f"     ② min\_score   ≥ {learner.min_score} (현재 기준)",
#             f"     ③ OF 방향      LONG→BUY 또는 SHORT→SELL (NEUTRAL 차단)",
#             f"     ④ 마진         ≥ ${MIN_MARGIN_USD:.0f} (가용금 - ${MIN_AVAIL:.0f} 예비금)",
#             f"     ⑤ 쿨다운       SL 후 {COOLDOWN_SEC}초 / 추가진입 {ADD_COOLDOWN_SEC}초",
#             f"     ⑥ 최대 포지션  자본 × {MAX_CAPITAL_USAGE*100:.0f}% 이하",
#             f"     ⑦ 홀드 유예    진입 후 {MIN_HOLD_SEC}초 이내 SL 비활성",
#             f"     ⑧ OB 유동성    빈 오더북 / 스프레드 5% 초과 차단",
#             "",
#             "─────────────────",
#             "📊 *신호 기준*",
#             f"     min\_score     *{learner.min_score}*  (기본 {DEFAULT_MIN_SCORE})",
#             f"     OF 방향 확인   필수 (NEUTRAL 차단)",
#             f"     리밸 기준      +{REBAL_SCORE_DIFF}pt 이상",
#             f"     강제교체 기준  +{REBAL_FORCE_DIFF}pt 이상",
#             f"     약한포지션     {WEAK_POS_THRESHOLD}점 이하",
#             "",
#             "💰 *W / L*",
#             f"     TP             *{TP_PCT*100:.1f}%*  (High Spread {TP_PCT_HIGH_SPREAD*100:.1f}%)",
#             f"     SL             *{SL_PCT*100:.1f}%*",
#             f"     R:R            *{TP_PCT/SL_PCT:.0f}:1*",
#             f"     SL 쿨다운      {COOLDOWN_SEC}초",
#             f"     진입 SL 유예   120초",
#             "",
#             "🔁 *Trailing Stop*",
#             f"     peak≥TP×1  →  65%  보존",
#             f"     peak≥TP×2  →  75%  보존",
#             f"     peak≥TP×3  →  82%  보존",
#             f"     peak≥TP×4  →  88%  보존",
#             "",
#             "💸 *자본 배분*",
#             f"     최대 활용      {MAX_CAPITAL_USAGE*100:.0f}%",
#             f"     포지션당       {PER_POS_RATIO*100:.0f}% × (score/{int(MAX_SCORE)})",
#             f"     최소 포지션    ${MIN_POSITION_VAL:.0f}",
#             f"     최소 마진      ${MIN_MARGIN_USD:.0f}",
#             f"     예비금         ${MIN_AVAIL:.0f}",
#             "",
#             "🤖 *OrderFlow v2.2*",
#             f"     Imbalance      {OF_IMBALANCE_THRESH*100:.0f}%",
#             f"     Thick Wall     {OF_THICK_WALL_THRESH*100:.0f}%",
#             f"     Spread         {OF_SPREAD_WIDE_THRESH*100:.3f}%",
#             f"     보너스         +{OF_MIN_SCORE_BONUS}pt~",
#             f"     역방향 패널티  \-{OF_CONFLICT_PENALTY}pt",
#             f"     최소 적용 sc   {OF_MIN_BASE_SCORE}",
#             "",
#             "⏱️ *타이머*",
#             f"     스캔           {CHECK_INTERVAL}초",
#             f"     리밸           {REBAL_INTERVAL}초",
#             f"     계정 TTL       {ACCOUNT_TTL}초",
#             f"     Auto\-Upgrade  {next_upg_in//60}분 {next_upg_in%60:02d}초 후",
#             f"     Auto\-Analyse  {next_ana_in//60}분 {next_ana_in%60:02d}초 후",
#             "",
#             "─────────────────",
#             f"📚 학습 누적  *{total_trades}건*",
#         ]
#         self._reply("\n".join(lines))
# 
# 
#     async def _cmd_insight(self):
#         """전략 스코어카드 인사이트 (구간별 WR, ADX, Hold 분석)"""
#         bot  = self._bot
#         hist = bot.learner._get_real_trades()
#         if len(hist) < 5:
#             self._reply("📊 인사이트 데이터 부족\n     최소 5건 필요")
#             return
# 
#         wins   = [t for t in hist if t.get("win")]
#         losses = [t for t in hist if not t.get("win")]
#         total  = len(hist)
#         wr     = len(wins)/total*100
#         avg_w  = sum(t.get("pnl_pct",0) for t in wins)/len(wins)*100  if wins   else 0
#         avg_l  = sum(t.get("pnl_pct",0) for t in losses)/len(losses)*100 if losses else 0
#         rr     = abs(avg_w/avg_l) if avg_l else 0
#         ev     = avg_w*(wr/100) + avg_l*(1-wr/100)
#         bep    = abs(avg_l)/(avg_w+abs(avg_l))*100 if (avg_w and avg_l) else 0
# 
#         def section(ts):
#             if not ts: return "—"
#             w = [t for t in ts if t.get("win")]
#             avg = sum(t.get("pnl_pct",0) for t in ts)/len(ts)*100
#             return f"{len(ts)}건  WR={len(w)/len(ts)*100:.0f}%  avg={avg:+.1f}%"
# 
#         lines = [
#             f"🔍 *전략 인사이트* ({total}건)",
#             f"     WR={wr:.0f}%  BEP={bep:.0f}%  R:R={rr:.2f}  EV={ev:+.1f}%",
#             "",
#             "─────────────────",
#             "📊 *Score 구간*",
#         ]
#         for lo, hi in [(65,80),(80,95),(95,110),(110,200)]:
#             ts = [t for t in hist if lo <= (t.get("score") or t.get("entry_score") or 0) < hi]
#             label = f"sc {lo}~{hi-1}" if hi<200 else f"sc {lo}+"
#             lines.append(f"     {label:<12} {section(ts)}")
# 
#         lines += ["", "🎯 *ADX Regime*"]
#         for reg in ["trend","transition","range"]:
#             ts = [t for t in hist if t.get("adx_regime") == reg]
#             icon = "✅" if reg == "trend" else "🚫"
#             lines.append(f"     {icon} {reg:<12} {section(ts)}")
# 
#         lines += ["", "⏱️ *Hold 시간*"]
#         for lo, hi, label in [(0,60,"<1분"),(60,300,"1~5분"),(300,99999,">5분")]:
#             ts = [t for t in hist if isinstance(t.get("hold_secs"),(int,float)) and lo <= t["hold_secs"] < hi]
#             lines.append(f"     {label:<6} {section(ts)}")
# 
#         lines += ["", "📈 *Supertrend*"]
#         for val, label in [(True,"Bull↑"),(False,"Bear↓")]:
#             ts = [t for t in hist if t.get("supertrend") == val]
#             lines.append(f"     {label}  {section(ts)}")
# 
#         lines += ["", "🔀 *OF 방향*"]
#         for d, label in [("BULL","BULL↑"),("BEAR","BEAR↓"),("NEUTRAL","NEUTRAL")]:
#             ts = [t for t in hist if t.get("of_dir") == d]
#             lines.append(f"     {label:<9} {section(ts)}")
# 
#         lines += ["", "🌏 *시장 세션*"]
#         _sess_def = [("ASIA","09~17시"),("EUROPE","17~23시"),("US","23~09시")]
#         for _sd, _sh in _sess_def:
#             _ts = [t for t in hist if t.get("market_session") == _sd]
#             lines.append(f"   {_sd:<8}({_sh})  {section(_ts)}")
# 
#         lines += ["", "─────────────────"]
#         self._reply("\n".join(lines))
# 
#     async def _cmd_stop(self):
#         self._reply("🛑 봇 긴급 중단 — 5초 후 종료")
#         log.warning("🛑 TG /stop 명령 수신 → 종료")
#         await asyncio.sleep(5)
#         import os as _os
#         _os.kill(_os.getpid(), 15)  # SIGTERM
# 
#     async def _cmd_help(self):
#         lines = [
#             "💀 *DEATH MODE 명령어*",
#             "",
#             "─────────────────",
#             "📊 *현황 조회*",
#             "     /death      자본 · 포지션 · WR 요약",
#             "     /pos        오픈 포지션 상세",
#             "     /stats      성과 통계 · R:R · 심볼 분석",
#             "",
#             "🔍 *신호 분석*",
#             "     /score      스코어 TOP 20 실시간",
#             "     /insight    전략 인사이트 (구간별 WR/ADX/Hold)",
#             "     /ob         오더북 수집 현황",
#             "",
#             "⚙️ *설정 확인*",
#             "     /strategy   전략 & 적용 현황",
#             "     /params     파라미터 설정값",
#             "     /bl         블랙리스트 현황",
#             "",
#             "🛑 *제어*",
#             "     /stop       봇 긴급 중단",
#             "     /help       이 도움말",
#             "─────────────────",
#         ]
#         self._reply("\n".join(lines))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PaperTrader — 차단 신호 가상 포지션 백테스팅 트래커
# 실제 주문 없이 TP/SL 조건 충족 여부를 추적하여 데이터 확보
# 목적: optimizer가 더 많은 데이터로 파라미터/전략 최적화 가능
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class PaperTrader:
    """
    진입 차단된 신호를 가상 포지션으로 추적.
    실거래와 동일한 TP/SL 로직 적용 → 결과를 paper_trade_history.json에 저장.
    is_paper=True 필드로 실거래와 구분.
    """
    def __init__(self):
        self._positions: Dict[str, Dict] = {}  # key=sym_side → 가상 포지션
        self._history: List[Dict] = []
        self._load()

    def _load(self):
        # ── 청산 이력 로드 ──────────────────────────────────────
        try:
            with open(PAPER_HIST_FILE) as f:
                data = json.load(f)
            self._history = data if isinstance(data, list) else data.get("trades", [])
            log.info(f"📄 Paper history: {len(self._history)}건 로드")
        except FileNotFoundError:
            self._history = []
        except Exception as e:
            log.warning(f"paper_hist 로드 실패: {e}")
            self._history = []

        # ── 열린 가상 포지션 복원 (재시작 영속화) ──────────────
        try:
            with open(PAPER_POS_FILE) as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                self._positions = saved
                log.info(f"📄 Paper positions 복원: {len(saved)}개")
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"paper_pos 로드 실패: {e}")

    def _save(self):
        # ── 청산 이력 저장 ──────────────────────────────────────
        try:
            with open(PAPER_HIST_FILE, "w") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"paper_hist 저장 실패: {e}")

        # ── 열린 가상 포지션 저장 (재시작 대비) ────────────────
        try:
            with open(PAPER_POS_FILE, "w") as f:
                json.dump(self._positions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"paper_pos 저장 실패: {e}")

    def open(self, sig: Dict, block_reason: str):
        """차단된 신호 → 가상 진입"""
        sym   = sig.get("symbol", "?")
        side  = sig.get("side", "LONG")
        mid   = sig.get("market_id", 0)
        key   = f"{sym}_{side}_{mid}"
        if key in self._positions:
            return  # 이미 가상 포지션 있음
        # 가상 포지션별 TP/SL (regime에 따라)
        regime = sig.get("adx_regime", "trend")
        if regime == "range":
            tp_pct = RANGE_TP_PCT
            sl_pct = RANGE_SL_PCT
        else:
            tp_pct = sig.get("adaptive_tp") or TP_PCT
            sl_pct = sig.get("adaptive_sl") or SL_PCT
        entry_price = sig.get("price", 0)
        if not entry_price:
            return
        import datetime as _dt
        _now = time.time()
        self._positions[key] = {
            # ── 기본 포지션 정보 ────────────────────────────────
            "symbol":         sym,
            "side":           side,
            "market_id":      mid,
            "entry_price":    entry_price,
            "entry":          entry_price,          # 실거래 호환 필드명
            "entry_ts":       _now,
            "entry_hour_kst": _dt.datetime.fromtimestamp(_now,
                              tz=_dt.timezone(
                              _dt.timedelta(hours=9))).hour,  # KST 진입 시각
            # ── TP/SL 설정 ──────────────────────────────────────
            "tp_pct":         tp_pct,
            "sl_pct":         sl_pct,
            "adaptive_tp":    tp_pct,               # 실거래 호환
            "adaptive_sl":    sl_pct,               # 실거래 호환
            # ── Score & 신호 지표 (실거래와 동일 필드) ──────────
            "score":          sig.get("score", 0),
            "score_with_of":  sig.get("score_with_of", sig.get("score", 0)),
            "of_dir":         sig.get("of_dir", "NEUTRAL"),
            "of_bonus":       sig.get("of_bonus", 0),
            "of_imb":         sig.get("of_imb"),    # OF 불균형 수치
            "adx_regime":     regime,
            "supertrend":     sig.get("supertrend", "?"),
            "rsi":            sig.get("rsi"),
            "adx":            sig.get("adx"),
            "bb_pos":         sig.get("bb_pos"),
            # ── 포지션 규모 (실거래 호환) ────────────────────────
            "leverage":       sig.get("leverage") or MARKET_LEVERAGE.get(mid, 10),
            # ── 메타 ────────────────────────────────────────────
            "params_ver":     _get_params_ver(),    # 적용된 파라미터 버전
            "block_reason":   block_reason,         # 차단 이유
            "peak_pnl":       0.0,                  # TSL 추적용
        }
        log.info(f"📄 Paper 가상진입: {sym} {side} @{entry_price:.4f} "
                 f"TP={tp_pct*100:.1f}% SL={sl_pct*100:.1f}% [{block_reason}]")
        # 진입 즉시 파일 저장 (재시작 시 복원 가능)
        self._save()

    def check_all(self, last_prices: Dict[int, float]):
        """매 루프: 가상 포지션 TP/SL 체크 → 충족 시 가상 청산 기록"""
        closed_keys = []
        now = time.time()
        for key, pos in self._positions.items():
            mid      = pos["market_id"]
            cur      = last_prices.get(mid, 0)
            if not cur:
                continue
            entry    = pos["entry_price"]
            side     = pos["side"]
            tp_pct   = pos["tp_pct"]
            sl_pct   = pos["sl_pct"]
            hold_sec = int(now - pos["entry_ts"])

            if side == "LONG":
                pnl_pct = (cur - entry) / entry
            else:
                pnl_pct = (entry - cur) / entry

            # ── peak_pnl 갱신 (TSL 추적) ──────────────────────
            peak_pnl = pos.get("peak_pnl", 0.0)
            if pnl_pct > peak_pnl:
                pos["peak_pnl"] = pnl_pct
                peak_pnl = pnl_pct

            exit_type = None
            if peak_pnl >= tp_pct:
                # ── TSL: 실거래와 동일 단계별 보호 로직 ─────────
                # WHY: 고정 TP로 조기 종료하면 TRUMP처럼 큰 추세를
                #      +2.3%에서 끊어버려 실거래와 괴리 발생
                if peak_pnl >= tp_pct * 4:
                    preserve = TSL_PRESERVE_T4   # 92%
                elif peak_pnl >= tp_pct * 3:
                    preserve = TSL_PRESERVE_T3   # 85%
                elif peak_pnl >= tp_pct * 2:
                    preserve = TSL_PRESERVE_T2   # 70%
                else:
                    preserve = TSL_PRESERVE_T1   # 50%
                trail_floor = max(peak_pnl * preserve, TSL_SLIPPAGE_GUARD)
                if pnl_pct <= trail_floor:
                    exit_type = f"PAPER_TSL(peak={peak_pnl:.3%})"
            elif pnl_pct <= -sl_pct:
                exit_type = "PAPER_SL"
            elif hold_sec > 7200:  # 2시간 미청산 → 강제 종료
                exit_type = "PAPER_TIMEOUT"

            if exit_type:
                rec = {
                    "is_paper":      True,          # ← 실거래와 구분용 필드
                    "ts":            datetime.now().isoformat(),
                    # ── 실거래와 동일한 필드 구조 ───────────────
                    "symbol":        pos["symbol"],
                    "side":          side,
                    "entry":         entry,
                    "entry_price":   entry,
                    "exit":          cur,
                    "score":         pos["score"],
                    "score_with_of": pos.get("score_with_of", pos["score"]),
                    "pnl_pct":       round(pnl_pct * 100, 4),
                    "pnl":           round(pnl_pct * 100, 4),  # % 단위 근사 (USD 없음)
                    "peak_pnl":      round(peak_pnl * 100, 4),
                    "win":           pnl_pct > 0,
                    "exit_type":     exit_type,
                    "hold_secs":     hold_sec,
                    # ── Score 구성 지표 ──────────────────────────
                    "of_dir":        pos["of_dir"],
                    "of_bonus":      pos["of_bonus"],
                    "of_imb":        pos.get("of_imb"),
                    "adx_regime":    pos["adx_regime"],
                    "supertrend":    pos["supertrend"],
                    "rsi":           pos["rsi"],
                    "adx":           pos["adx"],
                    "bb_pos":        pos["bb_pos"],
                    # ── 포지션 설정 ──────────────────────────────
                    "leverage":      pos.get("leverage", 10),
                    "adaptive_tp":   round(pos["tp_pct"] * 100, 3),
                    "adaptive_sl":   round(pos["sl_pct"] * 100, 3),
                    "tp_pct":        round(pos["tp_pct"] * 100, 3),
                    "sl_pct":        round(pos["sl_pct"] * 100, 3),
                    "entry_hour_kst": pos.get("entry_hour_kst"),
                    # ── 메타 ────────────────────────────────────
                    "params_ver":    pos.get("params_ver"),
                    "block_reason":  pos["block_reason"],
                    "is_paper":      True,
                }
                self._history.append(rec)
                closed_keys.append(key)
                win_icon = "✅" if pnl_pct > 0 else "❌"
                log.info(f"📄 Paper {exit_type}: {pos['symbol']} {side} "
                         f"pnl={pnl_pct*100:+.3f}% hold={hold_sec//60}m [{pos['block_reason']}] {win_icon}")

        for k in closed_keys:
            del self._positions[k]

        if closed_keys:
            self._save()

    def stats(self) -> Dict:
        """실거래 vs 가상거래 비교 통계"""
        if not self._history:
            return {}
        wins = [t for t in self._history if t.get("win")]
        losses = [t for t in self._history if not t.get("win")]
        total = len(self._history)
        wr = len(wins)/total*100 if total else 0
        by_reason = {}
        for t in self._history:
            r = t.get("block_reason","?")
            if r not in by_reason: by_reason[r] = {"w":0,"l":0}
            if t.get("win"): by_reason[r]["w"] += 1
            else: by_reason[r]["l"] += 1
        return {
            "total": total, "wr": wr,
            "wins": len(wins), "losses": len(losses),
            "by_reason": by_reason,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SelfLearner v4 (v3.1 Score-Bucket + Confidence)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SelfLearner:
    def __init__(self):
        self.min_score      = DEFAULT_MIN_SCORE
        self.trade_hist:    List[Dict] = []
        self.score_buckets: Dict[int, Dict] = {}   # bucket(10단위) → {wins,losses,pnl_sum,count}
        self.symbol_stats:  Dict[str, Dict] = {}   # symbol → {wins,losses,pnl_sum}
        self.bad_hours:     List[int] = []         # 불량 시간대 (KST) — 진입 차단
        self.last_upgrade:  float = 0.0  # 재시작 후 즉시 첫 upgrade 실행
        self.last_analyse:  float = 0.0  # 즉시 분석 시작
        self._last_record_ts: Dict[str, float] = {}  # 재시작 후 재구성됨
        self._load()

    def _load(self):
        try:
            with open(LEARNED_FILE) as f:
                d = json.load(f)
                saved_score = d.get("min_score", DEFAULT_MIN_SCORE)
                # score_buckets / symbol_stats: trade_history에서 재계산 (tick 오염 방지)
                # learned_v4.json에 저장된 값은 무시 — trade_hist 기반으로 덮어씀
                self.bad_hours      = d.get("bad_hours", [])
                self.min_score = max(saved_score, DEFAULT_MIN_SCORE)
                log.info(f"🧠 Loaded: min_score={self.min_score}(saved={saved_score})")
        except: pass
        try:
            with open(TRADE_HIST_FILE) as f:
                raw = json.load(f)
            # BUG FIX: 로드 시 SL 중복기록 정제 (동일 symbol+entry SL → 첫 번째만)
            # 재시작 후 _last_record_ts 재구성 → 120초 TTL 복원
            bad_exits = {"EXT", "REBAL", "SC0_CLEANUP", "LIQ_DANGER"}
            # 1단계: (symbol, entry) 기준 가장 최신 ts 추적
            import datetime as _dt_mod
            sl_latest_ts: Dict[tuple, float] = {}  # (sym, entry) → 최신 epoch
            for t in raw:
                et = t.get("exit_type", "")
                if et.startswith("SL") and t.get("pnl", 0) != 0 and t.get("score", 0) > 0:
                    try:
                        _ts_str = t.get("ts", "")
                        if _ts_str:
                            _epoch = _dt_mod.datetime.fromisoformat(_ts_str).timestamp()
                            dk = (t.get("symbol",""), round(float(t.get("entry",0) or 0), 5))
                            if _epoch > sl_latest_ts.get(dk, 0):
                                sl_latest_ts[dk] = _epoch
                    except Exception:
                        pass

            # _last_record_ts에 가장 최신 ts로 저장 (재시작 후 300초 방어 보장)
            for (sym_e, ent_e), latest_epoch in sl_latest_ts.items():
                _dk2 = f"{sym_e}_LONG_{ent_e}"
                # side 불명 시 LONG/SHORT 모두 등록 (worst case)
                for _side in ("LONG", "SHORT"):
                    _dk2 = f"{sym_e}_{_side}_{round(ent_e, 6)}"
                    if latest_epoch > self._last_record_ts.get(_dk2, 0):
                        self._last_record_ts[_dk2] = latest_epoch

            # 2단계: 중복 제거 후 cleaned 구성
            sl_seen: set = set()
            cleaned = []
            for t in sorted(raw, key=lambda x: x.get("ts", "")):
                et = t.get("exit_type", "")
                if et not in bad_exits and t.get("pnl", 0) != 0 and t.get("score", 0) > 0:
                    if et.startswith("SL"):
                        sym_e = t.get("symbol", "")
                        ent_e = round(float(t.get("entry", 0) or 0), 5)
                        dk = (sym_e, ent_e)
                        if dk in sl_seen:
                            continue  # 중복 오기록 제거 (파일에서 제거)
                        sl_seen.add(dk)
                    cleaned.append(t)
                    # _last_record_ts 재구성: ts 문자열 → epoch (최신값 우선)
                    try:
                        _ts_str = t.get("ts", "")
                        if _ts_str:
                            _epoch = _dt_mod.datetime.fromisoformat(_ts_str).timestamp()
                            _dk2 = f"{t.get('symbol','')}_{ t.get('side','')}_{ round(float(t.get('entry',0) or 0), 6)}"
                            if _epoch > self._last_record_ts.get(_dk2, 0):
                                self._last_record_ts[_dk2] = _epoch
                    except Exception:
                        pass
            # 정제 결과가 원본보다 적으면 파일 덮어쓰기
            if len(cleaned) < len(raw):
                log.info(f"🧹 trade_hist 중복 정제: {len(raw)}→{len(cleaned)}건 ({len(raw)-len(cleaned)}건 제거)")
                try:
                    with open(TRADE_HIST_FILE, "w") as fw:
                        json.dump(cleaned, fw)
                except Exception:
                    pass
            self.trade_hist = cleaned
            log.info(f"🧠 Trade history: {len(self.trade_hist)} records")
            # trade_hist 기반으로 score_buckets / symbol_stats 재계산 (tick 오염 완전 제거)
            self._rebuild_stats_from_history()
        except Exception:
            pass

    def _rebuild_stats_from_history(self):
        """trade_hist 기반으로 score_buckets / symbol_stats 재계산
        WHY: learned_v4.json에 누적된 tick 기반 카운터 오염 제거
             optimizer는 오직 실제 종료 거래만 기준으로 동작해야 함
        """
        self.score_buckets = {}
        self.symbol_stats  = {}
        for t in self.trade_hist:
            sc   = t.get("score", 0)
            sym  = t.get("symbol", "?")
            pnl  = t.get("pnl_pct", 0)
            win  = bool(t.get("win", pnl > 0))
            bucket = (sc // 10) * 10
            b = self.score_buckets.setdefault(bucket, {"wins":0,"losses":0,"pnl_sum":0.0,"count":0})
            b["count"] += 1; b["pnl_sum"] += pnl
            if win: b["wins"] += 1
            else:   b["losses"] += 1
            s = self.symbol_stats.setdefault(sym, {"wins":0,"losses":0,"pnl_sum":0.0})
            s["pnl_sum"] += pnl
            if win: s["wins"] += 1
            else:   s["losses"] += 1
        log.info(f"🔄 stats 재계산: buckets={len(self.score_buckets)} symbols={len(self.symbol_stats)}")

    def save(self):
        """외부 호출용 save (= _save 래퍼)"""
        self._save()

    def _save(self):
        try:
            with open(LEARNED_FILE, "w") as f:
                json.dump({
                    "min_score":    self.min_score,
                    "score_buckets": self.score_buckets,
                    "symbol_stats":  self.symbol_stats,
                    "bad_hours":     self.bad_hours,
                }, f, indent=2)
        except: pass
        try:
            with open(TRADE_HIST_FILE, "w") as f:
                json.dump(self.trade_hist[-1000:], f)
        except: pass

    def record(self, symbol: str, side: str, entry: float, exit_p: float,
               score: int, pnl_pct: float, exit_type: str, pnl_usd: float = 0.0,
               meta: dict = None):
        """거래 기록 + 버킷 통계 업데이트
        meta: 진입 시 신호 지표 (rsi, adx, adx_regime, supertrend, hold_secs 등)
        """
        # BUG FIX: SL 중복기록 방지 — 동일 (symbol+side+entry) 조합 재시작 후에도 방지
        # _last_record_ts는 __init__에서 초기화 + _load()에서 trade_hist 기반 재구성
        import time as _t
        _now_r = _t.time()
        # 중복 방지 키: symbol+side+entry+exit_type+exit_price (5개 조합)
        # WHY: entry만으로 24시간 차단 → 추가진입(add) 후 재청산이 같은 entry면 스킵됨
        #      TRUMP 6건 중 5건 누락 사례 (2026-03-13 분석)
        # exit_type + round(exit_p,5)를 포함 → 동일 포지션 중복 방지 + 재진입 허용
        _dup_key = f"{symbol}_{side}_{round(entry, 5)}_{exit_type}_{round(exit_p, 5)}"
        _last_ts = self._last_record_ts.get(_dup_key, 0)
        if _now_r - _last_ts < 300:  # 5분 내 완전 동일 기록만 차단 (재시작 중복 방지)
            log.debug(f"record() 중복 스킵: {symbol} {side} entry={entry:.5f} exit={exit_p:.5f} ({int(_now_r-_last_ts)}초 이내)")
            return
        self._last_record_ts[_dup_key] = _now_r

        win = pnl_pct > 0
        _exit_ts_now = datetime.now().isoformat()
        rec = {
            "ts": _exit_ts_now, "exit_ts": _exit_ts_now,
            "symbol": symbol, "side": side,
            "entry": entry, "entry_price": entry,
            "exit": exit_p, "exit_price": exit_p,
            "score": score,
            "pnl_pct": round(pnl_pct * 100, 4), "pnl": pnl_usd,
            "win": win, "exit_type": exit_type,
        }
        if meta:
            rec.update(meta)   # rsi, adx, adx_regime, supertrend, hold_secs, bb_pos 등
        # entry_ts 명시 보장 (meta에서 왔거나 fallback)
        if not rec.get("entry_ts") and meta and meta.get("entry_ts"):
            rec["entry_ts"] = meta["entry_ts"]
        # adx_regime 누락 시 adx 값으로 자동 채움 (데이터 품질 보장)
        # WHY: EXT/SL 경로에서 meta 누락 가능 → regime=None으로 optimizer 분석 불가
        if not rec.get("adx_regime"):
            adx_val = rec.get("adx")
            if adx_val is not None:
                adx_f = float(adx_val)
                if adx_f >= ADX_TREND_THRESH:
                    rec["adx_regime"] = "trend"
                elif adx_f < ADX_RANGE_THRESH:
                    rec["adx_regime"] = "range"
                else:
                    rec["adx_regime"] = "transition"
        # 시간대 기록 (KST) — 메타에 없으면 현재 시간으로 보완
        if "entry_hour_kst" not in rec:
            try:
                from datetime import timezone, timedelta as _td
                rec["entry_hour_kst"] = datetime.now(timezone(_td(hours=9))).hour
            except Exception:
                pass
        # 금융 세션 분류 (KST 기준, UTC 기반 실제 세션)
        # ASIA:   UTC 00~08 = KST 09~17
        # EUROPE: UTC 07~16 = KST 16~01 (겹침 우선순위: US > EUROPE > ASIA)
        # US:     UTC 13~22 = KST 22~07
        if "market_session" not in rec and rec.get("entry_hour_kst") is not None:
            _h = int(rec["entry_hour_kst"])
            _h_utc = (_h - 9) % 24
            if 13 <= _h_utc < 22:    # US: KST 22~07
                rec["market_session"] = "US"
            elif 7 <= _h_utc < 16:   # EUROPE: KST 16~01
                rec["market_session"] = "EUROPE"
            else:                     # ASIA: KST 09~17
                rec["market_session"] = "ASIA"
        # entry_only 레코드 업데이트 (진입 시 저장한 레코드에 청산 정보 추가)
        # WHY: 진입 시 entry_ts/score가 기록됐으므로 중복 append 대신 업데이트
        _matched = False
        _entry_rounded = round(float(entry), 5)
        for _existing in reversed(self.trade_hist):
            if (_existing.get("symbol") == symbol
                    and _existing.get("side") == side
                    and _existing.get("exit_ts") is None
                    and abs(float(_existing.get("entry_price") or _existing.get("entry") or 0) - _entry_rounded) < 1e-4):
                _existing.update({k: v for k, v in rec.items() if v is not None})
                # entry_ts 보존 (기존 기록이 더 정확)
                if _existing.get("entry_ts") and not rec.get("entry_ts"):
                    pass  # 기존 값 유지
                _matched = True
                break
        if not _matched:
            self.trade_hist.append(rec)

        # score bucket (10단위)
        bucket = (score // 10) * 10
        b = self.score_buckets.setdefault(bucket, {"wins":0,"losses":0,"pnl_sum":0.0,"count":0})
        b["count"]   += 1
        b["pnl_sum"] += pnl_pct
        if win: b["wins"]   += 1
        else:   b["losses"] += 1

        # symbol stats
        s = self.symbol_stats.setdefault(symbol, {"wins":0,"losses":0,"pnl_sum":0.0})
        s["pnl_sum"] += pnl_pct
        if win: s["wins"]   += 1
        else:   s["losses"] += 1

        self._save()
        self._save_session_stats()

    def _save_session_stats(self):
        """세션별 W/L/WR 통계를 session_stats.json에 저장"""
        try:
            sessions = {"ASIA": {"W":0,"L":0,"pnl":0.0,"trades":[]},
                        "EUROPE": {"W":0,"L":0,"pnl":0.0,"trades":[]},
                        "US": {"W":0,"L":0,"pnl":0.0,"trades":[]}}
            real = self._get_real_trades()
            for t in real:
                sess = t.get("market_session")
                if sess not in sessions:
                    continue
                s = sessions[sess]
                if t.get("win"):
                    s["W"] += 1
                else:
                    s["L"] += 1
                s["pnl"] += float(t.get("pnl_pct") or 0)
                s["trades"].append({
                    "ts": t.get("ts",""),
                    "symbol": t.get("symbol",""),
                    "side": t.get("side",""),
                    "pnl_pct": round(float(t.get("pnl_pct") or 0), 4),
                    "win": bool(t.get("win")),
                    "exit_type": t.get("exit_type",""),
                    "entry_hour_kst": t.get("entry_hour_kst"),
                })
            # 통계 계산
            result = {}
            for sess, s in sessions.items():
                total = s["W"] + s["L"]
                wr = s["W"] / total * 100 if total else 0.0
                ev = s["pnl"] / total if total else 0.0
                result[sess] = {
                    "W": s["W"], "L": s["L"], "total": total,
                    "WR": round(wr, 1), "EV": round(ev, 4),
                    "pnl_sum": round(s["pnl"], 4),
                    "trades": s["trades"],
                }
            result["_updated"] = datetime.now(timezone(timedelta(hours=9))).isoformat()
            result["_session_def"] = {
                "ASIA":   "UTC 00~08 / KST 09~17",
                "EUROPE": "UTC 07~16 / KST 16~01",
                "US":     "UTC 13~22 / KST 22~07",
            }
            with open("logs/session_stats.json", "w") as f:
                json.dump(result, f, indent=2)
        except Exception as e:
            log.debug(f"_save_session_stats error: {e}")

    def win_rate(self, n: int = 50) -> float:
        recent = self.trade_hist[-n:]
        if not recent: return 50.0
        return sum(1 for t in recent if t.get("win")) / len(recent) * 100

    def get_confidence(self, score: int) -> float:
        """
        score 버킷 기반 신뢰도 (0.7~1.3)
        데이터 없으면 0.85 (약간 보수적)
        """
        bucket = (score // 10) * 10
        b = self.score_buckets.get(bucket)
        if not b or b["count"] < 5:
            return 0.85
        wr = b["wins"] / b["count"]
        return max(0.7, min(1.3, 0.7 + wr * 0.6))

    def should_upgrade(self) -> bool:
        return (time.time() - self.last_upgrade >= AUTO_UPGRADE_INTERVAL
                and len(self.trade_hist) >= 15)

    def should_analyse(self) -> bool:
        return time.time() - self.last_analyse >= AUTO_ANALYSE_INTERVAL

    def _get_real_trades(self):
        """REBAL/EXT 제외한 실질 거래만 반환"""
        return [t for t in self.trade_hist
                if t.get("score", 0) > 0
                and t.get("exit_type", "") not in ("REBAL", "EXT", "SC0_CLEANUP", "LIQ_DANGER")]

    def analyse(self):
        """5분마다 실행 — 데이터 학습만 (파라미터 변경 없음)"""
        from collections import defaultdict as _dd
        real = self._get_real_trades()
        self.last_analyse = time.time()
        if len(real) < 5:
            return

        # score 버킷 재계산 (캐시 갱신)
        sc_ev = _dd(lambda: {"tp":0,"sl":0,"pnl":0.0})
        for t in real:
            b = (t.get("score",0)//10)*10
            if t.get("win"): sc_ev[b]["tp"] += 1
            else:            sc_ev[b]["sl"] += 1
            sc_ev[b]["pnl"] += t.get("pnl_pct",0)

        # adx_regime별 WR 학습
        regime_ev = _dd(lambda: {"tp":0,"sl":0,"pnl":0.0})
        for t in real:
            r = t.get("adx_regime","?") or "?"
            if t.get("win"): regime_ev[r]["tp"] += 1
            else:            regime_ev[r]["sl"] += 1
            regime_ev[r]["pnl"] += t.get("pnl_pct",0)

        # 결과를 learned_v4.json에 분석 캐시로 저장
        try:
            with open(LEARNED_FILE) as f:
                d = json.load(f)
        except:
            d = {}
        d["regime_stats"] = {k: {**v, "wr": v["tp"]/(v["tp"]+v["sl"]) if v["tp"]+v["sl"]>0 else 0}
                             for k,v in regime_ev.items() if v["tp"]+v["sl"]>0}
        d["last_analyse"] = datetime.now().isoformat()
        try:
            with open(LEARNED_FILE,"w") as f:
                json.dump(d, f, indent=2)
        except: pass

    def auto_upgrade(self):
        """15분마다 실행 — EV 분석 기반 파라미터 자동 조정"""
        from collections import defaultdict as _dd
        real = self._get_real_trades()
        recent = real[-50:] if real else []
        n = len(recent)
        if n < 10:
            self.last_upgrade = time.time()
            return

        wr_recent = sum(1 for t in recent if t.get("win")) / n * 100

        # ── 1. score 구간별 EV 분석 ────────────────────────
        sc_ev = _dd(lambda: {"tp": 0, "sl": 0, "pnl": 0.0})
        for t in real:
            b = (t.get("score", 0) // 10) * 10
            if t.get("win"): sc_ev[b]["tp"] += 1
            else:             sc_ev[b]["sl"] += 1
            sc_ev[b]["pnl"] += t.get("pnl_pct", 0)

        # EV 양수인 최소 score 구간 → min_score 자동 조정
        ev_positive_buckets = []
        for b in sorted(sc_ev.keys()):
            d = sc_ev[b]
            tot = d["tp"] + d["sl"]
            if tot < 5: continue
            ev = d["pnl"] / tot
            if ev > 0:
                ev_positive_buckets.append(b)

        changes = []
        old_ms = self.min_score

        if ev_positive_buckets:
            rec_ms = min(ev_positive_buckets)
            # min_score는 DEFAULT_MIN_SCORE 이상으로만 조정 (하향 금지)
            # WHY: sc80~89 WR=0% 확인 → 90 미만으로 내리면 손실
            if rec_ms > self.min_score + 5 and rec_ms >= DEFAULT_MIN_SCORE:
                self.min_score = rec_ms   # EV 양수 최저 구간으로 상향
                changes.append(f"min_score {old_ms}→{self.min_score} (EV분석 상향)")
            elif rec_ms < self.min_score - 10 and wr_recent >= 60 and rec_ms >= DEFAULT_MIN_SCORE:
                # 완화: WR 60% 이상 + DEFAULT 이상 유지 조건
                self.min_score = max(rec_ms, DEFAULT_MIN_SCORE)
                changes.append(f"min_score {old_ms}→{self.min_score} (EV분석 완화)")
        else:
            # EV 양수 구간 없음 → min_score 상향 (단, 데이터 30건 이상)
            if n >= 30 and self.min_score < 100:
                self.min_score = min(self.min_score + 3, 100)
                changes.append(f"min_score {old_ms}→{self.min_score} (EV음수 전구간, 상향)")

        # ── 2. 심볼별 WR 경고 로깅 ──────────────────────────
        sym_ev = _dd(lambda: {"tp": 0, "sl": 0, "pnl": 0.0})
        for t in real:
            s = t.get("symbol", "?")
            if t.get("win"): sym_ev[s]["tp"] += 1
            else:             sym_ev[s]["sl"] += 1
            sym_ev[s]["pnl"] += t.get("pnl_pct", 0)

        bad_syms, good_syms = [], []
        for s, d in sym_ev.items():
            tot = d["tp"] + d["sl"]
            if tot < 5: continue
            ev  = d["pnl"] / tot
            wr2 = d["tp"] / tot
            if ev < -0.002 and wr2 < 0.2:  bad_syms.append(f"{s}({wr2*100:.0f}%WR)")
            elif wr2 >= 0.5 and ev > 0:     good_syms.append(f"{s}({wr2*100:.0f}%WR)")

        # ── 3. 시간대별 WR 분석 ─────────────────────────────
        from datetime import timezone, timedelta as _td
        KST = timezone(_td(hours=9))
        hour_stats = _dd(lambda: {"tp": 0, "sl": 0})
        for t in real:
            try:
                import datetime as _dt
                ts = t.get("ts", "")
                dt = _dt.datetime.fromisoformat(ts.replace(" ", "T")).astimezone(KST)
                h = dt.hour
            except:
                h = 0
            if t.get("win"): hour_stats[h]["tp"] += 1
            else:             hour_stats[h]["sl"] += 1

        bad_hours = [h for h, d in hour_stats.items()
                     if d["tp"] + d["sl"] >= 2 and d["tp"] / (d["tp"] + d["sl"]) < 0.20]

        # ── 4. adx_regime별 WR 분석 ─────────────────────────
        regime_ev = _dd(lambda: {"tp":0,"sl":0,"pnl":0.0})
        for t in real:
            r = t.get("adx_regime","?") or "?"
            if t.get("win"): regime_ev[r]["tp"] += 1
            else:            regime_ev[r]["sl"] += 1
            regime_ev[r]["pnl"] += t.get("pnl_pct",0)

        # ── 로그 출력 ────────────────────────────────────────
        log.info("━━━━━━ 🤖 Auto-Upgrade (15min) ━━━━━━")
        log.info(f"분석: {len(real)}건 WR(recent50)={wr_recent:.0f}%")
        for b in sorted(sc_ev.keys()):
            d = sc_ev[b]
            tot = d["tp"] + d["sl"]
            if tot < 3: continue
            ev = d["pnl"] / tot * 100
            wr2= d["tp"] / tot * 100
            mark = "✅" if ev > 0 else "❌"
            log.info(f"  {mark} sc{b}~{b+9}: WR={wr2:.0f}% EV={ev:+.3f}% ({tot}건)")
        for r, d in regime_ev.items():
            tot = d["tp"]+d["sl"]
            if tot < 3: continue
            ev = d["pnl"]/tot*100
            wr2= d["tp"]/tot*100
            mark = "✅" if ev>0 else "❌"
            log.info(f"  {mark} [{r}]: WR={wr2:.0f}% EV={ev:+.3f}% ({tot}건)")
        if bad_syms:
            log.info(f"⚠️  불량심볼: {', '.join(bad_syms[:5])}")
            # symbol_stats → DynamicBlacklist 자동 연동 (데이터 기반 블랙리스트)
            # WHY: bad_syms 감지하고 로그만 찍고 끝 = 유령코드. 실제 차단에 연결
            for entry in bad_syms[:5]:
                _bl_sym = entry.split("(")[0]  # "ETH(0%WR)" → "ETH"
                DYNAMIC_BL.manual_add(_bl_sym, 7200)  # 2시간 블랙리스트 (15분마다 재평가)
                log.info(f"🚫 auto-BL: {_bl_sym} → DynamicBlacklist 2h (WR<20% EV음수)")
        if good_syms:  log.info(f"⭐ 우량심볼: {', '.join(good_syms[:5])}")
        if bad_hours:
            # bad_hours 감지만, 차단 비활성 (데이터 부족, 노이반님 결정 18:53 KST 03/14)
            log.info(f"⏰ 불량시간 감지(KST): {bad_hours} (차단 비활성 — 데이터 더 필요)")
        # ── 5. Auto-Research: trade_history 기반 패턴 탐색 ────
        # WHY: TRUMP 제외 WR=11% 분석 결과
        #   ST=UP + trend = 추격매수 → WR=0%
        #   OF=UNKNOWN + hold>60m = 유일한 수익 패턴
        # 매 15분마다 실거래 데이터로 최적 조건 재탐색
        research_findings = []
        if len(real) >= 5:
            # ST별 WR
            st_stats = _dd(lambda: {"tp":0,"sl":0,"pnl":0.0})
            for t in real:
                st = t.get("supertrend","?") or "?"
                reg = t.get("adx_regime","?") or "?"
                key = f"{st}+{reg}"
                if t.get("win"): st_stats[key]["tp"] += 1
                else:             st_stats[key]["sl"] += 1
                st_stats[key]["pnl"] += t.get("pnl_pct",0)
            for k, d in st_stats.items():
                tot = d["tp"]+d["sl"]
                if tot < 3: continue
                wr_k = d["tp"]/tot*100
                ev_k = d["pnl"]/tot
                mark = "✅" if ev_k > 0 else "❌"
                log.info(f"  {mark} [{k}]: WR={wr_k:.0f}% EV={ev_k:+.3f}% ({tot}건)")
                if wr_k == 0 and tot >= 3:
                    research_findings.append(f"🚫 차단패턴: ST={k} WR=0%({tot}건)")

            # OF별 WR
            of_stats = _dd(lambda: {"tp":0,"sl":0,"pnl":0.0})
            for t in real:
                of = t.get("of_dir","?") or "?"
                if t.get("win"): of_stats[of]["tp"] += 1
                else:             of_stats[of]["sl"] += 1
                of_stats[of]["pnl"] += t.get("pnl_pct",0)
            for k, d in of_stats.items():
                tot = d["tp"]+d["sl"]
                if tot < 3: continue
                wr_k = d["tp"]/tot*100
                ev_k = d["pnl"]/tot
                if wr_k == 0:
                    research_findings.append(f"🚫 OF={k} WR=0%({tot}건) → 진입 주의")

        if research_findings:
            log.info(f"🔬 Auto-Research: {' | '.join(research_findings[:3])}")

        if changes:    log.info(f"🔧 변경: {' | '.join(changes)}")
        else:          log.info(f"⚪ 변경없음 (min_score={self.min_score})")
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        self.last_upgrade = time.time()
        self._save()

        # ── Telegram 알림 (plain text) ───────────────────────
        tg_lines = [f"🤖 Auto-Upgrade | {n}건 WR={wr_recent:.0f}% | min_score={self.min_score}"]
        for b in sorted(sc_ev.keys()):
            d = sc_ev[b]
            tot = d["tp"]+d["sl"]
            if tot < 3: continue
            ev = d["pnl"]/tot*100
            wr2 = d["tp"]/tot*100
            mark = "✅" if ev > 0 else "❌"
            cur = " <- 현재" if b <= self.min_score < b+10 else ""
            tg_lines.append(f"  {mark} sc{b}~{b+9}: WR={wr2:.0f}% EV={ev:+.2f}% ({tot}건){cur}")
        if bad_syms:  tg_lines.append(f"⚠️ 불량: {', '.join(bad_syms[:3])}")
        if good_syms: tg_lines.append(f"⭐ 우량: {', '.join(good_syms[:3])}")
        if changes:
            tg_lines.append(f"🔧 {' | '.join(changes)}")
        else:
            tg_lines.append(f"⚪ 변경없음 (다음 분석: 15분 후)")
        send_tg("\n".join(tg_lines))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# OrderFlowEngine v2.1
# 실제 Lighter DEX bid/ask 스트림 기반
# v2.0: Thick Wall + Spread Explosion 패턴 추가
# v2.1: 조회한 오더북 결과를 ob_snapshots.json에 자동 저장 (옵션 A 데이터 수집)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 오더플로우 파라미터
OF_IMBALANCE_THRESH  = 0.63   # 노이반님 확인: 63% 기준
                              # 0.54 기준: BULL+BEAR 약 30%, 실제 신호 포착 가능
OF_CACHE_TTL         = 300.0   # 오더북 캐시 TTL (5분 — REST 호출 0.52 req/s)
OF_DEPTH             = 20     # 오더북 depth (20개 — Thick Wall 감지 정확도 향상)
OF_MIN_SCORE_BONUS   = 20     # 노이반님 확인: +20pt
OF_CONFLICT_PENALTY  = 40     # 오더플로우 역방향 시 패널티 점수
OF_MIN_BASE_SCORE    = 60     # FIX: 70→60 완화 (sc>=60 + OF 확인 시 30bonus → 90 달성)
OF_NO_CONFIRM_PENALTY = 999    # OF 미확인(NEUTRAL) 신호는 차단 (OF 방향 확인 필수)
MIN_HOLD_SEC         = 150   # v6.0: Optimizer 권고 (2026-03-14 노이반님 승인)

# Thick Wall 파라미터 (v2.0 신규)
OF_THICK_WALL_THRESH = 0.35   # 단일 레벨이 전체 bid/ask의 40% 이상 → Thick Wall
OF_THICK_WALL_NEAR   = 0.0050 # FIX: 0.20%→0.50% 확장 (dist=0.186~0.198% 포착 개선)
OF_THICK_WALL_BONUS  = 30     # Thick Wall 방향 일치 시 추가 보너스
OF_SPREAD_WIDE_THRESH = 0.0003 # 스프레드 0.03% 이상 → Spread Explosion 감지
OF_SPREAD_BONUS      = 20     # Spread Explosion 감지 시 보너스

class OrderFlowEngine:
    """
    실제 Lighter DEX 오더북 bid/ask 를 분석해 매수/매도 압력 방향 판단.

    핵심 지표:
      1. 물량 불균형 (Volume Imbalance)
         bid_vol vs ask_vol 비율 → 어느 쪽에 유동성이 집중됐는가

      2. 가격대 집중도 (Price Concentration)
         Best bid/ask 근처에 물량이 몰린 정도 → 진짜 압력 vs 허수

      3. 스프레드 (Spread)
         (best_ask - best_bid) / mid → 좁을수록 활성 시장

    사용법:
      of_engine = OrderFlowEngine()
      signal = await of_engine.analyze(market_id, lighter_api_client)
      # → {"direction": "BULL"/"BEAR"/"NEUTRAL",
      #     "imbalance": 0.72,          # bid 비율 (0~1)
      #     "spread_pct": 0.004,        # 스프레드 %
      #     "bid_vol": 45.3,            # 상위 10 bid 총량
      #     "ask_vol": 18.1,            # 상위 10 ask 총량
      #     "score_bonus": 25,          # 신호 엔진에 추가할 보너스
      #     "conflict": False}          # 기존 기술 신호와 역방향 여부

    캐싱:
      market_id별 2초 TTL → 동일 루프 내 다중 호출 방지
    """

    # 오더북 스냅샷 저장 경로 (옵션 A 데이터 수집)
    OB_SNAP_PATH    = "logs/ob_snapshots.json"
    OB_SNAP_MAXLEN  = 1440   # 심볼당 최대 1440개 (6분 간격 × 1440 = 6일)
    OB_SNAP_SAVE_N  = 20     # 20회 수집마다 파일 저장

    def __init__(self):
        # {market_id: (timestamp, result_dict)}
        self._cache: Dict[int, Tuple[float, Dict]] = {}
        # lighter.ApiClient 재사용 (매 루프 생성 방지)
        self._lighter_client = None
        self._order_api      = None
        self._client_lock    = asyncio.Lock() if False else None  # 초기화 후 교체
        # 오더북 스냅샷 히스토리 (옵션 A)
        self._ob_history: Dict[int, list] = {}   # {market_id: [snap, ...]}
        self._ob_save_count = 0
        self._load_ob_history()

    def _load_ob_history(self):
        """시작 시 기존 스냅샷 파일 복원"""
        try:
            if os.path.exists(self.OB_SNAP_PATH):
                with open(self.OB_SNAP_PATH) as f:
                    raw = json.load(f)
                now_ms = int(time.time() * 1000)
                cutoff = now_ms - 6 * 24 * 3600 * 1000  # 6일치 보관
                total = 0
                for mid_str, snaps in raw.items():
                    mid = int(mid_str)
                    valid = [s for s in snaps if s.get("ts", 0) > cutoff]
                    if valid:
                        self._ob_history[mid] = valid[-self.OB_SNAP_MAXLEN:]
                        total += len(valid)
                log.info(f"♻️  OB history 복원: {total}개 스냅샷")
        except Exception as e:
            log.debug(f"OB history load error: {e}")

    def _save_ob_history(self):
        """ob_history를 파일로 저장 (atomic write)"""
        try:
            data = {str(mid): snaps[-self.OB_SNAP_MAXLEN:]
                    for mid, snaps in self._ob_history.items() if snaps}
            tmp = self.OB_SNAP_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self.OB_SNAP_PATH)
            total = sum(len(v) for v in data.values())
            log.info(f"💾 OB history saved: {len(data)}마켓 {total}스냅샷")
        except Exception as e:
            log.debug(f"OB history save error: {e}")

    def _record_ob_snapshot(self, market_id: int, result: Dict):
        """오더북 분석 결과를 히스토리에 기록"""
        snap = {
            "ts":              int(time.time() * 1000),
            "imbalance":       result.get("imbalance", 0.5),
            "spread_pct":      result.get("spread_pct", 0),
            "bid_vol":         result.get("bid_vol", 0),
            "ask_vol":         result.get("ask_vol", 0),
            "best_bid":        result.get("best_bid", 0),
            "best_ask":        result.get("best_ask", 0),
            "max_bid_ratio":   result.get("max_bid_ratio", 0),
            "max_ask_ratio":   result.get("max_ask_ratio", 0),
            "thick_bid_wall":  result.get("thick_bid_wall", False),
            "thick_ask_wall":  result.get("thick_ask_wall", False),
            "spread_explosion": result.get("spread_explosion", False),
            "direction":       result.get("direction", "NEUTRAL"),
        }
        if market_id not in self._ob_history:
            self._ob_history[market_id] = []
        self._ob_history[market_id].append(snap)
        # 최대 길이 초과 시 앞부분 제거
        if len(self._ob_history[market_id]) > self.OB_SNAP_MAXLEN:
            self._ob_history[market_id] = self._ob_history[market_id][-self.OB_SNAP_MAXLEN:]

        self._ob_save_count += 1
        if self._ob_save_count % self.OB_SNAP_SAVE_N == 0:
            self._save_ob_history()

    async def _get_api(self):
        """lighter.OrderApi 싱글턴 — 연결 재사용"""
        if self._order_api is None:
            import lighter as _lighter
            import warnings as _w
            _w.filterwarnings("ignore")
            config = _lighter.Configuration(host="https://mainnet.zklighter.elliot.ai")
            config.verify_ssl = False
            self._lighter_client = _lighter.ApiClient(config)
            self._order_api = _lighter.OrderApi(self._lighter_client)
        return self._order_api

    async def analyze(self, market_id: int) -> Dict:
        """
        오더북 분석 — WS 완전 우선, REST 완전 제거

        1순위: WS_MANAGER OB 실시간 상태 (봇 WS 구독 — 전체 170마켓)
        2순위: WS Builder ob_snapshots.json 최신 스냅샷 (WS Builder 별도 WS 구독)
        3순위: 캐시 (두 WS 모두 준비 전 짧은 공백기)
        REST:  완전 제거 — IP rate limit 충돌 원천 차단
        """
        now = time.time()

        # ── 1순위: 봇 WS OB 실시간 ─────────────────────────
        ws_ob = WS_MANAGER.get_ob(market_id)
        if ws_ob is not None:
            result = self._analyze_ob_data(ws_ob["bids"], ws_ob["asks"], market_id)
            self._cache[market_id] = (now, result)
            self._record_ob_snapshot(market_id, result)
            return result

        # ── 2순위: WS Builder ob_snapshots.json ────────────
        ws_snap = self._get_wsbuilder_ob_snap(market_id, max_age_sec=360)
        if ws_snap is not None:
            self._cache[market_id] = (now, ws_snap)
            return ws_snap

        # ── 3순위: 캐시 (두 WS 준비 전 공백기만) ──────────
        cached = self._cache.get(market_id)
        if cached and now - cached[0] < OF_CACHE_TTL:
            return cached[1]

        # REST 완전 제거 → NEUTRAL 반환 (rate limit 보호)
        return {
            "direction": "NEUTRAL", "imbalance": 0.5,
            "spread_pct": 0.0, "bid_vol": 0.0, "ask_vol": 0.0,
            "score_bonus": 0, "best_bid": 0.0, "best_ask": 0.0,
            "conflict": False,
        }

    def _get_wsbuilder_ob_snap(self, market_id: int, max_age_sec: float = 120) -> Optional[Dict]:
        """WS Builder ob_snapshots.json에서 최신 OB 스냅샷 반환 (전체 마켓 WS 구독 결과)"""
        try:
            snap_path = self.OB_SNAP_PATH
            if not os.path.exists(snap_path):
                return None
            # 파일 수정시간으로 stale 여부 1차 체크 (파일 I/O 최소화)
            if time.time() - os.path.getmtime(snap_path) > max_age_sec:
                return None
            with open(snap_path) as f:
                data = json.load(f)
            snaps = data.get(str(market_id), [])
            if not snaps:
                return None
            s = snaps[-1]
            # 스냅샷 나이 체크
            snap_age = time.time() - s.get("ts", 0) / 1000
            if snap_age > max_age_sec:
                return None
            # OF analyze 결과 포맷으로 변환
            imbalance = s.get("imbalance", 0.5)
            direction = s.get("direction", "NEUTRAL")
            if direction == "NEUTRAL":
                score_bonus = 0
            else:
                score_bonus = OF_MIN_SCORE_BONUS
            return {
                "direction":   direction,
                "imbalance":   imbalance,
                "spread_pct":  s.get("spread_pct", 0.0),
                "bid_vol":     s.get("bid_vol", 0.0),
                "ask_vol":     s.get("ask_vol", 0.0),
                "score_bonus": score_bonus,
                "best_bid":    s.get("best_bid", 0.0),
                "best_ask":    s.get("best_ask", 0.0),
                "conflict":    False,
                "_from_wsbuilder": True,  # 디버그용
            }
        except Exception:
            return None

    def _analyze_ob_data(self, bids: list, asks: list, market_id: int) -> Dict:
        """WS OB 데이터 직접 분석 (REST 미사용)"""
        neutral = {
            "direction": "NEUTRAL", "imbalance": 0.5,
            "spread_pct": 0.0, "bid_vol": 0.0, "ask_vol": 0.0,
            "score_bonus": 0, "best_bid": 0.0, "best_ask": 0.0,
            "conflict": False,
        }
        try:
            if not bids or not asks:
                return neutral
            # WS OB는 순서 보장 안됨 → 정렬
            sorted_bids = sorted(bids, key=lambda x: float(x["price"]), reverse=True)
            sorted_asks = sorted(asks, key=lambda x: float(x["price"]))

            bid_vol   = sum(float(b.get("size",0)) for b in sorted_bids[:OF_DEPTH])
            ask_vol   = sum(float(a.get("size",0)) for a in sorted_asks[:OF_DEPTH])
            total_vol = bid_vol + ask_vol
            if total_vol < 1e-9:
                return neutral

            imbalance  = bid_vol / total_vol
            best_bid   = float(sorted_bids[0]["price"])
            best_ask   = float(sorted_asks[0]["price"])
            mid_price  = (best_bid + best_ask) / 2
            spread_pct = (best_ask - best_bid) / mid_price if mid_price > 0 else 0

            # 방향
            if imbalance >= OF_IMBALANCE_THRESH:
                direction   = "BULL"
                score_bonus = OF_MIN_SCORE_BONUS
            elif (1 - imbalance) >= OF_IMBALANCE_THRESH:
                direction   = "BEAR"
                score_bonus = OF_MIN_SCORE_BONUS
            else:
                direction   = "NEUTRAL"
                score_bonus = 0

            # Thick Wall
            # FIX: NEUTRAL 상태에서 ThickWall이 direction을 결정하도록 수정
            # 기존: NEUTRAL일 때 ThickBidWall → -tw 패널티 (잘못된 로직)
            # 수정: NEUTRAL이면 ThickWall이 direction을 BULL/BEAR로 바꿈
            thick_bid_dir = False
            thick_ask_dir = False
            if bid_vol > 0:
                bid_ratios = [float(b.get("size",0))/bid_vol for b in sorted_bids]
                thick_bid  = max(bid_ratios)
                if thick_bid >= OF_THICK_WALL_THRESH:
                    tb_price = float(sorted_bids[bid_ratios.index(thick_bid)]["price"])
                    dist     = abs(tb_price - mid_price)/mid_price if mid_price>0 else 1
                    if dist <= OF_THICK_WALL_NEAR:
                        thick_bid_dir = True
            if ask_vol > 0:
                ask_ratios = [float(a.get("size",0))/ask_vol for a in sorted_asks]
                thick_ask  = max(ask_ratios)
                if thick_ask >= OF_THICK_WALL_THRESH:
                    ta_price = float(sorted_asks[ask_ratios.index(thick_ask)]["price"])
                    dist     = abs(ta_price - mid_price)/mid_price if mid_price>0 else 1
                    if dist <= OF_THICK_WALL_NEAR:
                        thick_ask_dir = True

            # NEUTRAL → ThickWall로 direction 결정
            if direction == "NEUTRAL":
                if thick_bid_dir and not thick_ask_dir:
                    direction   = "BULL"
                    score_bonus = OF_MIN_SCORE_BONUS
                elif thick_ask_dir and not thick_bid_dir:
                    direction   = "BEAR"
                    score_bonus = OF_MIN_SCORE_BONUS

            # ThickWall 보너스 (방향 결정 후)
            if thick_bid_dir:
                bid_ratios2 = [float(b.get("size",0))/bid_vol for b in sorted_bids]
                thick_bid2  = max(bid_ratios2)
                tb_price2   = float(sorted_bids[bid_ratios2.index(thick_bid2)]["price"])
                dist2       = abs(tb_price2 - mid_price)/mid_price if mid_price>0 else 1
                tw = int(OF_THICK_WALL_BONUS * thick_bid2)
                if direction == "BULL":
                    score_bonus += tw
                    log.info(f"OF 🧱 ThickBidWall [{market_id}] ratio={thick_bid2*100:.0f}% dist={dist2*100:.3f}% +{tw}pts")
                else:
                    score_bonus -= tw
                    log.info(f"OF 🧱 ThickBidWall [{market_id}] ratio={thick_bid2*100:.0f}% dist={dist2*100:.3f}% -{tw}pts")
            if thick_ask_dir:
                ask_ratios2 = [float(a.get("size",0))/ask_vol for a in sorted_asks]
                thick_ask2  = max(ask_ratios2)
                ta_price2   = float(sorted_asks[ask_ratios2.index(thick_ask2)]["price"])
                dist2       = abs(ta_price2 - mid_price)/mid_price if mid_price>0 else 1
                tw = int(OF_THICK_WALL_BONUS * thick_ask2)
                if direction == "BEAR":
                    score_bonus += tw
                    log.info(f"OF 🧱 ThickAskWall [{market_id}] ratio={thick_ask2*100:.0f}% dist={dist2*100:.3f}% +{tw}pts")
                else:
                    score_bonus -= tw
                    log.info(f"OF 🧱 ThickAskWall [{market_id}] ratio={thick_ask2*100:.0f}% dist={dist2*100:.3f}% -{tw}pts")

            # Spread Explosion
            if spread_pct >= OF_SPREAD_WIDE_THRESH:
                sp = min(int(spread_pct/OF_SPREAD_WIDE_THRESH*OF_SPREAD_BONUS*0.5), OF_SPREAD_BONUS)
                score_bonus += sp
                log.info(f"OF 💥 SpreadExplosion [{market_id}] spread={spread_pct*100:.4f}% +{sp}pts")

            return {
                "direction": direction, "imbalance": round(imbalance,4),
                "spread_pct": round(spread_pct,6), "bid_vol": round(bid_vol,4),
                "ask_vol": round(ask_vol,4), "score_bonus": score_bonus,
                "best_bid": best_bid, "best_ask": best_ask, "conflict": False,
            }
        except Exception as e:
            log.debug(f"OF WS analyze err {market_id}: {e}")
            return neutral

    async def _fetch(self, market_id: int) -> Dict:
        """실제 오더북 API 호출 + 분석"""
        neutral = {
            "direction": "NEUTRAL", "imbalance": 0.5,
            "spread_pct": 0.0, "bid_vol": 0.0, "ask_vol": 0.0,
            "score_bonus": 0, "best_bid": 0.0, "best_ask": 0.0,
            "conflict": False,
        }
        try:
            api = await self._get_api()
            res = await api.order_book_orders(market_id=market_id, limit=OF_DEPTH)
            d   = res.to_dict() if hasattr(res, 'to_dict') else {}

            bids = d.get("bids", [])
            asks = d.get("asks", [])

            if not bids or not asks:
                return neutral

            # ── 1. 물량 계산 ─────────────────────────────────────────
            # remaining_base_amount: 아직 미체결된 잔여량
            bid_vol = sum(float(b.get("remaining_base_amount", 0)) for b in bids)
            ask_vol = sum(float(a.get("remaining_base_amount", 0)) for a in asks)
            total_vol = bid_vol + ask_vol
            if total_vol < 1e-9:
                return neutral

            imbalance = bid_vol / total_vol   # > 0.5: 매수압력 우세

            # ── 2. Best bid/ask & 스프레드 ──────────────────────────
            # bids: 가장 높은 가격이 먼저 (best bid = bids[0])
            # asks: 가장 낮은 가격이 먼저 (best ask = asks[0])
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            mid_price = (best_bid + best_ask) / 2
            spread_pct = (best_ask - best_bid) / mid_price if mid_price > 0 else 0

            # ── 3. 가격대 집중도 (상위 3개 vs 전체 비율) ────────────
            top3_bid = sum(float(b.get("remaining_base_amount", 0)) for b in bids[:3])
            top3_ask = sum(float(a.get("remaining_base_amount", 0)) for a in asks[:3])
            bid_concentration = top3_bid / bid_vol if bid_vol > 0 else 0
            ask_concentration = top3_ask / ask_vol if ask_vol > 0 else 0

            # ── 4. Thick Wall 감지 (v2.0) ────────────────────────────
            # 단일 레벨에 전체 물량의 40% 이상 집중 → 강한 지지/저항
            bid_ratios  = [float(b.get("remaining_base_amount", 0)) / bid_vol for b in bids] if bid_vol > 0 else []
            ask_ratios  = [float(a.get("remaining_base_amount", 0)) / ask_vol for a in asks] if ask_vol > 0 else []

            max_bid_ratio = max(bid_ratios) if bid_ratios else 0
            max_ask_ratio = max(ask_ratios) if ask_ratios else 0

            thick_bid_wall = max_bid_ratio >= OF_THICK_WALL_THRESH
            thick_ask_wall = max_ask_ratio >= OF_THICK_WALL_THRESH

            # Thick Wall 레벨 가격
            if thick_bid_wall and bid_ratios:
                thick_bid_price = float(bids[bid_ratios.index(max_bid_ratio)]["price"])
                thick_bid_dist  = abs(mid_price - thick_bid_price) / mid_price
            else:
                thick_bid_price = 0.0
                thick_bid_dist  = 1.0

            if thick_ask_wall and ask_ratios:
                thick_ask_price = float(asks[ask_ratios.index(max_ask_ratio)]["price"])
                thick_ask_dist  = abs(thick_ask_price - mid_price) / mid_price
            else:
                thick_ask_price = 0.0
                thick_ask_dist  = 1.0

            # Thick Wall이 현재가 근처에 있는지
            thick_bid_near = thick_bid_wall and thick_bid_dist <= OF_THICK_WALL_NEAR
            thick_ask_near = thick_ask_wall and thick_ask_dist <= OF_THICK_WALL_NEAR

            # ── 5. Spread Explosion 감지 (v2.0) ──────────────────────
            spread_explosion = spread_pct >= OF_SPREAD_WIDE_THRESH

            # ── 6. 방향 판단 (강화) ──────────────────────────────────
            if imbalance >= OF_IMBALANCE_THRESH:
                direction = "BULL"
            elif imbalance <= (1 - OF_IMBALANCE_THRESH):
                direction = "BEAR"
            elif thick_bid_near and not thick_ask_near:
                direction = "BULL"   # Bid 벽이 바로 아래 → 지지 → 상승 압력
            elif thick_ask_near and not thick_bid_near:
                direction = "BEAR"   # Ask 벽이 바로 위 → 저항 → 하락 압력
            else:
                direction = "NEUTRAL"

            # ── 7. 보너스 점수 계산 (v2.0) ───────────────────────────
            score_bonus = 0
            if direction != "NEUTRAL":
                # 불균형 강도
                imb_strength = abs(imbalance - 0.5) * 2
                score_bonus += int(imb_strength * 30)

                # 집중도 보너스
                if direction == "BULL":
                    score_bonus += int(bid_concentration * 10)
                else:
                    score_bonus += int(ask_concentration * 10)

                # Thick Wall 보너스 (v2.0)
                if direction == "BULL" and thick_bid_near:
                    wall_bonus = int(max_bid_ratio * OF_THICK_WALL_BONUS * 2)  # 40%→16, 54%→22
                    score_bonus += wall_bonus
                    log.info(f"OF 🧱 ThickBidWall [{market_id}] ratio={max_bid_ratio:.0%} dist={thick_bid_dist*100:.3f}% +{wall_bonus}pts")
                elif direction == "BEAR" and thick_ask_near:
                    wall_bonus = int(max_ask_ratio * OF_THICK_WALL_BONUS * 2)
                    score_bonus += wall_bonus
                    log.info(f"OF 🧱 ThickAskWall [{market_id}] ratio={max_ask_ratio:.0%} dist={thick_ask_dist*100:.3f}% +{wall_bonus}pts")

                # Spread Explosion 보너스 (v2.0)
                if spread_explosion:
                    spr_bonus = min(OF_SPREAD_BONUS, int(spread_pct / OF_SPREAD_WIDE_THRESH * 8))
                    score_bonus += spr_bonus
                    log.info(f"OF 💥 SpreadExplosion [{market_id}] spread={spread_pct*100:.4f}% +{spr_bonus}pts")

                # 스프레드 패널티: 0.1% 이상이면 감점 (단, Spread Explosion 아닐 때만)
                if spread_pct > 0.001 and not spread_explosion:
                    score_bonus -= min(5, int(spread_pct * 5000))

                score_bonus = max(OF_MIN_SCORE_BONUS, score_bonus)

            log.debug(
                f"OF[{market_id}] dir={direction} imb={imbalance:.3f} "
                f"bid={bid_vol:.2f} ask={ask_vol:.2f} spread={spread_pct*100:.4f}% "
                f"thickBid={max_bid_ratio:.0%}({'near' if thick_bid_near else 'far'}) "
                f"thickAsk={max_ask_ratio:.0%}({'near' if thick_ask_near else 'far'}) "
                f"bonus={score_bonus}"
            )

            result = {
                "direction":       direction,
                "imbalance":       imbalance,
                "spread_pct":      spread_pct,
                "bid_vol":         bid_vol,
                "ask_vol":         ask_vol,
                "score_bonus":     score_bonus,
                "best_bid":        best_bid,
                "best_ask":        best_ask,
                "conflict":        False,
                # v2.0 추가 필드
                "thick_bid_wall":  thick_bid_near,
                "thick_ask_wall":  thick_ask_near,
                "max_bid_ratio":   max_bid_ratio,
                "max_ask_ratio":   max_ask_ratio,
                "spread_explosion": spread_explosion,
            }

            # v2.1: 스냅샷 히스토리 기록 (옵션 A 데이터 수집)
            self._record_ob_snapshot(market_id, result)

            return result

        except Exception as e:
            log.debug(f"OF fetch error market={market_id}: {e}")
            return neutral

    def apply_to_signal(self, sig: Dict, of_result: Dict) -> Dict:
        """
        기존 기술 신호(sig)에 오더플로우 결과를 혼합.

        규칙:
          - OF 방향 일치: score_bonus 추가
          - OF 방향 역방향: OF_CONFLICT_PENALTY 감점 + conflict=True 마킹
          - OF NEUTRAL: 변경 없음 (기술 신호만)

        중요: sig["score"] 는 직접 수정하지 않음.
              대신 sig["of_bonus"] 에 저장 → score_with_of 로 최종 계산.
              → 기존 학습 데이터(score 버킷)를 오염시키지 않음.
        """
        if of_result["direction"] == "NEUTRAL":
            sig["of_bonus"]    = 0
            sig["of_dir"]      = "NEUTRAL"
            sig["of_imb"]      = of_result["imbalance"]
            sig["of_conflict"] = False
            sig["score_with_of"] = sig["score"]
            # OB 수치 저장 (NEUTRAL도 기록 — Optimizer 피처용)
            sig["ob_bid_vol"]    = of_result.get("bid_vol", 0.0)
            sig["ob_ask_vol"]    = of_result.get("ask_vol", 0.0)
            sig["ob_spread_pct"] = of_result.get("spread_pct", 0.0)
            sig["ob_best_bid"]   = of_result.get("best_bid", 0.0)
            sig["ob_best_ask"]   = of_result.get("best_ask", 0.0)
            return sig

        sig_side = sig.get("side", "")
        of_dir   = of_result["direction"]

        # 방향 일치 여부
        aligned = (sig_side == "LONG"  and of_dir == "BULL") or \
                  (sig_side == "SHORT" and of_dir == "BEAR")

        base_score = sig.get("score", 0)

        if aligned:
            # OF 보너스는 base_score ≥ OF_MIN_BASE_SCORE인 경우만 허용
            if base_score >= OF_MIN_BASE_SCORE:
                bonus = of_result["score_bonus"]
            else:
                bonus = 0  # 약한 신호는 OF로 끌어올리지 않음
            sig["of_bonus"]    = bonus
            sig["of_conflict"] = False
        else:
            # 역방향 → 패널티
            bonus = -OF_CONFLICT_PENALTY
            sig["of_bonus"]    = bonus
            sig["of_conflict"] = True

        sig["of_dir"]        = of_dir
        sig["of_imb"]        = of_result["imbalance"]
        sig["score_with_of"] = sig["score"] + bonus
        # OB 수치 저장 (Optimizer 피처용 — 진입 시점 스냅샷)
        sig["ob_bid_vol"]    = of_result.get("bid_vol", 0.0)
        sig["ob_ask_vol"]    = of_result.get("ask_vol", 0.0)
        sig["ob_spread_pct"] = of_result.get("spread_pct", 0.0)
        sig["ob_best_bid"]   = of_result.get("best_bid", 0.0)
        sig["ob_best_ask"]   = of_result.get("best_ask", 0.0)

        if aligned:
            log.debug(f"OF✅ {sig.get('symbol')} {sig_side} score {sig['score']}→{sig['score_with_of']} (OF:{of_dir}+{bonus})")
        else:
            log.debug(f"OF⚠️ {sig.get('symbol')} {sig_side} score {sig['score']}→{sig['score_with_of']} (OF:{of_dir} CONFLICT)")

        return sig

    def invalidate(self, market_id: int):
        """포지션 진입/청산 후 해당 마켓 캐시 무효화"""
        self._cache.pop(market_id, None)

# 전역 오더플로우 엔진 인스턴스
OF_ENGINE = OrderFlowEngine()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 펀딩비 캐시 — 마켓별 현재 funding rate
# /funding-rates API → 전체 153마켓 1회 조회, 15분마다 갱신
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import urllib.request as _ur_fund
import warnings as _warn_fund
_warn_fund.filterwarnings('ignore')

_FUNDING_CACHE: Dict[int, float] = {}    # {market_id: rate (소수, e.g. 0.0001)}
_FUNDING_CACHE_TS: float = 0.0
_FUNDING_CACHE_TTL: float = 1800.0       # 30분마다 갱신 (펀딩비는 1시간 주기, 과도한 갱신 불필요)

# 펀딩비 시그널 파라미터
FR_HIGH_THRESH  = 0.0003   # 0.03%/hr 이상 → 과매수 (롱 비용 높음)
FR_LOW_THRESH   = -0.0003  # -0.03%/hr 이하 → 과매도 (숏 비용 높음)
FR_PENALTY      = -15      # 추세 반대 진입 패널티 (점수)
FR_BONUS        = 10       # 펀딩비 방향 일치 보너스 (점수)

def _refresh_funding_cache():
    """Lighter /funding-rates 전체 조회 → _FUNDING_CACHE 갱신"""
    global _FUNDING_CACHE, _FUNDING_CACHE_TS
    now = time.time()
    if now - _FUNDING_CACHE_TS < _FUNDING_CACHE_TTL:
        return
    try:
        req = _ur_fund.Request(
            "https://mainnet.zklighter.elliot.ai/api/v1/funding-rates",
            headers={"User-Agent": "bot", "accept": "application/json"}
        )
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        with _ur_fund.urlopen(req, timeout=5, context=ctx) as resp:
            data = json.loads(resp.read())
        rates = data.get("funding_rates", [])
        cache = {}
        for r in rates:
            if r.get("exchange") == "lighter":
                mid = r.get("market_id")
                rate = float(r.get("rate", 0))
                if mid is not None:
                    cache[mid] = rate
        if cache:
            _FUNDING_CACHE = cache
            _FUNDING_CACHE_TS = now
            log.info(f"💰 펀딩비 캐시 갱신: {len(cache)}마켓 | 고FR(>0.03%): {sum(1 for v in cache.values() if v > FR_HIGH_THRESH)}개")
    except Exception as e:
        log.debug(f"펀딩비 캐시 갱신 실패: {e}")

def get_funding_rate(market_id: int) -> Optional[float]:
    """마켓별 현재 펀딩비 반환 (None이면 미지원)"""
    _refresh_funding_cache()
    return _FUNDING_CACHE.get(market_id)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LighterWSManager — WebSocket 기반 실시간 데이터
# REST API rate limit 완전 우회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACCOUNT_IDX = 1058  # 계좌 인덱스

class LighterWSManager:
    """
    Lighter DEX WebSocket 통합 매니저 (싱글턴)

    기능:
      1. account_all 구독 → equity/available/positions 실시간 유지
         - shares.principal_amount 합산 = collateral(자본)
         - open positions: position != 0 인 항목만
      2. order_book 구독 → OB 상태 실시간 유지 (OF 엔진용)
         - 신호 발생한 마켓 동적 구독 추가
      3. REST API 호출 0회 (계좌/OB 모두 WS로 대체)

    Rate limit 영향: 0 (WebSocket은 rate limit 별도 버킷)
    """
    WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"

    def __init__(self):
        self._ws:          Optional[object] = None   # websockets 연결
        self._task:        Optional[object] = None   # asyncio Task

        # ── 계좌 상태 ────────────────────────────────────
        self._equity:          float = 0.0
        self._available:       float = 0.0
        self._positions:       Dict[str, Dict] = {}
        self._pending_zero:    Dict[str, float] = {}  # position=0 pending (오인삭제 방지)
        self._acc_ts:          float = 0.0
        self._acc_ready:       bool  = False
        # account_all_trades WS 실시간 체결 버퍼
        # WHY: 봇 재시작/volume_quota 실패 시에도 체결 즉시 기록 → trade_history 누락 방지
        from collections import deque as _deque_ws
        self._ws_trades_queue: _deque_ws = _deque_ws(maxlen=500)
        self._ws_trades_seen:  set = set()   # 중복 방지: order_id set
        self._rest_collateral: float = 0.0   # user_stats WS 또는 REST fallback
        self._rest_confirmed:  bool  = False  # user_stats 수신 완료 여부 (REST 불필요)
        self._ws_collateral:    float = 0.0   # WS user_stats.collateral (primary)
        self._ws_portfolio:     float = 0.0   # WS user_stats.portfolio_value (equity)
        self._ws_available:     float = 0.0   # WS user_stats.available_balance
        self._ws_margin_usage:  float = 0.0   # WS user_stats.margin_usage (Cross LIQ 위험도)
        # 재시작 시 마지막 알려진 collateral 복원 (REST rate limit 대비)
        try:
            import json as _j0, pathlib as _pl0
            _ps0 = _j0.loads(_pl0.Path(POS_STATE_FILE).read_text())
            _lc = float(_ps0.get("__last_collateral__", 0) or 0)
            if _lc > 0:
                self._rest_collateral = _lc
                log.info(f"🔄 last_collateral 복원: ${_lc:.2f}")
        except: pass

        # ── 오더북 상태 ──────────────────────────────────
        self._ob:          Dict[str, Dict] = {}   # {market_id_str: {"bids":[], "asks":[]}}
        self._ob_subs:     set  = set()          # 구독 중인 market_id_str
        self._ob_pending:  set  = set()          # 구독 요청했지만 아직 확인 안된 것

        # ── 연결 상태 ────────────────────────────────────
        self._connected:   bool  = False
        self._reconnect_cnt: int = 0

        log.info("🌐 LighterWSManager 초기화")

    # ══ 계좌 데이터 접근 ══════════════════════════════════
    def get_account(self) -> Dict:
        """현재 계좌 상태 반환 (LighterAPI.account() 호환 형식)"""
        open_pos = []
        for mid_str, p in self._positions.items():
            if abs(float(p.get("position", 0))) > 1e-9:
                mid = int(mid_str)
                sym = p.get("symbol") or MID_TO_SYMBOL.get(mid, str(mid))
                # sign=1 → LONG, sign=-1 → SHORT (API 문서 확인: position 필드는 항상 양수)
                side = "LONG" if int(p.get("sign", 1)) > 0 else "SHORT"
                entry = float(p.get("avg_entry_price", 0))
                upnl  = float(p.get("unrealized_pnl") or 0)
                pval  = float(p.get("position_value") or 0)
                imf   = float(p.get("initial_margin_fraction") or 10)
                lev   = int(100 / imf) if imf > 0 else 10
                liq   = float(p.get("liquidation_price") or 0)
                open_pos.append({
                    "symbol": sym, "market_id": mid,
                    "side": side, "entry_price": entry,
                    "size": abs(float(p.get("position", 0))),
                    "unrealized_pnl": upnl,
                    "position_value": abs(pval),
                    "liquidation_price": liq,
                    "leverage": lev,
                    "initial_margin_fraction": imf,
                    # sign 필드 제거: side는 position>0→LONG, <0→SHORT 직접 계산
                    # sign을 통한 side 판단 경로 완전 제거 (잘못된 SHORT 판단 방지)
                    "avg_entry_price": entry,
                    "mark_price": float(p.get("mark_price") or entry),
                })
        return {
            "equity":     self._equity,
            "available":  self._available,
            "collateral": self._ws_collateral if self._ws_collateral > 0 else self._rest_collateral,
            "positions":  open_pos,
        }

    def is_ready(self) -> bool:
        # user_stats 수신 완료 = collateral/equity 확보 = ready
        # (_rest_confirmed는 user_stats 수신 시 True로 설정됨)
        return self._connected and self._rest_confirmed

    # ══ 오더북 접근 ═══════════════════════════════════════
    def get_ob(self, market_id: int) -> Optional[Dict]:
        """현재 OB 상태 반환 (None = 미구독)"""
        return self._ob.get(str(market_id))

    def subscribe_ob(self, market_ids: list):
        """OB 구독 추가 요청 — 연결 중이면 즉시 전송"""
        for mid in market_ids:
            mid_str = str(mid)
            if mid_str not in self._ob_subs and mid_str not in self._ob_pending:
                self._ob_pending.add(mid_str)
                if self._ws and self._connected:
                    asyncio.ensure_future(self._send_sub_ob(mid)).add_done_callback(_suppress_exc)

    async def _send_sub_ob(self, market_id: int):
        try:
            if self._ws:
                await self._ws.send(
                    json.dumps({"type": "subscribe", "channel": f"order_book/{market_id}"})
                )
        except Exception as e:
            log.debug(f"WS OB sub err {market_id}: {e}")

    # ══ 계좌 상태 파싱 ════════════════════════════════════
    def _parse_account(self, msg: dict, is_update: bool = False):
        """subscribed/account_all 또는 update/account_all 파싱

        WS account_all 구조:
          - positions: {market_id_str: {position, avg_entry_price, unrealized_pnl, position_value, initial_margin_fraction, ...}}
          - assets: {asset_id_str: {symbol, balance, locked_balance}}  ← USDC = asset_id "3"
          - shares: LP pool 지분 (실제 자본과 다름, 무시)

        equity 계산:
          collateral = USDC balance (예치금)
          equity = collateral + sum(unrealized_pnl)  ← REST API 방식과 동일
          available = equity - sum(initial_margin per open position)
        """
        try:
            pos_dict = msg.get("positions", {})
            assets   = msg.get("assets", {})

            # 포지션 업데이트
            # ⚠️ update/account_all은 변경된 포지션만 포함 → 기존에 머지
            # ⚠️ subscribed/account_all은 전체 스냅샷 → 덮어쓰기
            if isinstance(pos_dict, dict):
                if is_update:
                    # update: 변경분만 머지 (포지션=0이면 제거, 아니면 업데이트)
                    for mid_str, p in pos_dict.items():
                        pos_size = float(p.get("position", 0))
                        if abs(pos_size) < 1e-9:
                            # FIX: position=0 즉시 pop 대신, _pending_zero에 기록
                            # 5초 후에도 0이면 제거 (체인 반영 지연 보호)
                            ts_now = time.time()
                            self._pending_zero[mid_str] = ts_now
                            log.debug(f"WS pos=0 pending: mid={mid_str} (5s 후 제거)")
                        else:
                            self._positions[mid_str] = p       # 신규/변경
                            self._pending_zero.pop(mid_str, None)  # pending 해제
                    # pending_zero 중 5초 경과한 것만 실제 제거
                    for mid_str, ts in list(self._pending_zero.items()):
                        if time.time() - ts >= WS_POS_ZERO_PENDING_SEC:
                            self._positions.pop(mid_str, None)
                            self._pending_zero.pop(mid_str, None)
                            log.info(f"WS pos=0 확정 제거: mid={mid_str} (5s 경과)")
                else:
                    # subscribed: 전체 스냅샷으로 교체
                    self._positions = pos_dict

            # ── WS IMF → SYMBOL_LEVERAGE / MARKET_LEVERAGE 자동 갱신 ──
            # WHY: account_all에 initial_margin_fraction(%) 포함
            #      leverage = round(100 / imf_pct) — REST 없이 WS만으로 정확한 레버리지
            #      ETH=2.00%→50x, BTC=2.00%→50x, SOL=4.00%→25x 확인 완료
            updated_lev = 0
            for mid_str, p in pos_dict.items() if isinstance(pos_dict, dict) else []:
                imf_raw = p.get("initial_margin_fraction")
                sym_raw = p.get("symbol", "")
                if imf_raw is None or not sym_raw:
                    continue
                try:
                    imf_f = float(imf_raw)
                    if imf_f <= 0:
                        continue
                    lev = round(100 / imf_f)
                    mid = int(mid_str)
                    old_lev = MARKET_LEVERAGE.get(mid, 0)
                    if old_lev != lev:
                        MARKET_LEVERAGE[mid] = lev
                        SYMBOL_LEVERAGE[sym_raw] = lev
                        MID_TO_SYMBOL[mid] = sym_raw
                        SYMBOL_TO_MID[sym_raw] = mid
                        updated_lev += 1
                except Exception:
                    pass
            if updated_lev > 0:
                log.info(f"🔑 WS IMF→레버리지 갱신: {updated_lev}개 마켓 (account_all)")

            # ── uPnL + margin 계산 + liq_price WS 직접 계산 ───────────
            # WHY: WS account_all의 liquidation_price = "" (빈 문자열, 항상)
            #      노이반님 확인: 문서 예시는 샘플, 실제 응답에서는 없음
            #      → entry + imf로 직접 계산 (Lighter isolated margin 수식)
            #      LONG:  liq = entry × (1 - imf/100)
            #      SHORT: liq = entry × (1 + imf/100)
            #      (maintenance margin 무시 — 실제와 5% 이내 오차, 보수적 방향)
            total_upnl   = 0.0
            total_margin = 0.0
            for mid_str, p in self._positions.items():
                pos_size = float(p.get("position", 0))
                if abs(pos_size) < 1e-9:
                    continue
                upnl  = float(p.get("unrealized_pnl", 0))
                pval  = abs(float(p.get("position_value", 0)))
                imf   = float(p.get("initial_margin_fraction", 10))
                margin = pval * (imf / 100) if imf > 0 else pval / 10
                total_upnl   += upnl
                total_margin += margin
                # WHY: Cross margin(margin_mode=0)에서 WS liquidation_price = "" (항상 빈 문자열)
                #      Isolated 공식(entry × (1-imf))은 Cross margin에서 오차 10~23% → 사용 금지
                #      실제 liq_price는 REST API만 정확 (5분마다 _fetch_rest()로 sync)
                #      실시간 위험 지표는 WS user_stats.margin_usage (90% = LIQ DANGER 임계)
                liq_ws = str(p.get("liquidation_price", "") or "").strip()
                # Cross margin liq 직접 계산 제거 — REST sync 결과만 신뢰
                # (p["liquidation_price"]는 _fetch_rest()가 주입한 값 그대로 유지)

            # ── equity 계산 전략 ──────────────────────────────────────
            # WS account_all의 assets 필드: {asset_id: {symbol, balance, locked_balance}}
            # USDC는 asset_id "3" — balance = 예치금(collateral) 실시간 값
            # REST 없이 WS assets에서 직접 collateral 추출 가능
            ws_usdc = 0.0
            if assets and isinstance(assets, dict):
                for _aid, _aval in assets.items():
                    if isinstance(_aval, dict):
                        _sym = str(_aval.get("symbol", "")).upper()
                        _bal = float(_aval.get("balance", 0) or 0)
                        if _sym == "USDC" or _aid == "3":
                            ws_usdc = _bal
                            break
            # USDC balance가 있으면 collateral로 갱신 (REST 불필요)
            if ws_usdc > 0:
                if abs(ws_usdc - self._rest_collateral) > 0.5:
                    log.info(f"WS assets USDC: collateral ${self._rest_collateral:.2f}→${ws_usdc:.2f}")
                self._rest_collateral = ws_usdc

            # equity = collateral + uPnL
            self._equity    = self._rest_collateral + total_upnl
            self._available = max(0.0, self._rest_collateral - total_margin)
            self._acc_ts    = time.time()
            self._acc_ready = True
            # account_all 수신 → _acc_ready (포지션 데이터 준비 완료)
            # _rest_confirmed 는 user_stats 수신 시 설정 (collateral/equity 확보 기준)
            # 단, user_stats보다 account_all이 먼저 오면 fallback으로 설정
            if not self._rest_confirmed and self._ws_collateral > 0:
                self._rest_confirmed = True
                log.info(f"✅ WS account_all 수신 (user_stats 선행) — is_ready=True")

            log.debug(f"WS account: equity=${self._equity:.2f} avail=${self._available:.2f} "
                      f"collateral=${self._rest_collateral:.2f} upnl={total_upnl:+.4f} margin=${total_margin:.2f}")
        except Exception as e:
            log.error(f"WS account parse err: {e}")

    # ══ User Stats 파싱 — collateral/equity/available WS primary ══
    def _parse_user_stats(self, msg: dict):
        """WS user_stats 채널 → collateral/equity/available 실시간 갱신

        노이반님 확인: user_stats에 collateral, portfolio_value, available_balance 포함
        → REST 교차검증 없이 WS만으로 equity/collateral 정확히 계산 가능
        → _rest_confirmed=True로 is_ready() 즉시 활성화
        """
        stats = msg.get("stats", {})
        col   = float(stats.get("collateral", 0) or 0)
        pval  = float(stats.get("portfolio_value", 0) or 0)  # = equity
        avail = float(stats.get("available_balance", 0) or 0)
        musage = float(stats.get("margin_usage", 0) or 0)    # Cross LIQ 위험도

        if col <= 0 and pval <= 0:
            return  # 유효하지 않은 데이터 무시

        self._ws_collateral   = col
        self._ws_portfolio    = pval
        self._ws_available    = avail
        self._ws_margin_usage = musage

        # _rest_collateral을 WS 값으로 갱신 (get_account() 호환)
        old = self._rest_collateral
        self._rest_collateral = col
        self._equity    = pval   # portfolio_value = collateral + uPnL
        self._available = avail

        if not self._rest_confirmed:
            self._rest_confirmed = True
            log.info(f"✅ WS user_stats 수신 — collateral=${col:.2f} equity=${pval:.2f} avail=${avail:.2f} margin_usage={musage:.1f}% (REST 불필요)")
        elif abs(old - col) > 0.5:
            log.info(f"💰 user_stats 갱신: collateral ${old:.2f}→${col:.2f} equity=${pval:.2f} margin_usage={musage:.1f}%")

        # pos_state에 영속화 (재시작 후 복원용)
        try:
            import json as _j3, pathlib as _pl3
            _ps_path3 = _pl3.Path(POS_STATE_FILE)
            _ps3 = _j3.loads(_ps_path3.read_text()) if _ps_path3.exists() else {}
            _ps3["__last_collateral__"] = col
            _ps3["__ws_equity__"]       = pval
            _ps3["__ws_available__"]    = avail
            _ps3["__ws_collateral__"]   = col
            _ps_path3.write_text(_j3.dumps(_ps3))
        except: pass

    # ══ Account Trades 파싱 — 실시간 체결 내역 → trade_history ══
    def _parse_account_trades(self, msg: dict):
        """
        WS account_all_trades 또는 update/account_all의 trades 필드 파싱

        체결 즉시 _ws_trades_queue에 추가 → _loop()에서 소비하여 learner.record() 호출
        WHY: force_close 실패/봇 재시작 중 청산 → trade_history 누락 문제 근본 해결

        trade 필드 (Order 모델 기준):
          market_index / market_id: 마켓 ID
          side: LONG / SHORT (또는 is_ask)
          base_size / size: 체결 수량
          base_price / price: 체결 가격
          reduce_only: True = 청산 주문
          status: "filled" / "partial_filled"
          order_id / client_order_id: 중복 방지용
          timestamp / created_at: 체결 시각
        """
        trades_raw = msg.get("trades", [])
        if not isinstance(trades_raw, list) or not trades_raw:
            return
        added = 0
        for t in trades_raw:
            # 중복 방지: order_id 기준
            oid = str(t.get("order_id") or t.get("order_index") or t.get("client_order_id") or "")
            if oid and oid in self._ws_trades_seen:
                continue
            if oid:
                self._ws_trades_seen.add(oid)
                # seen set 크기 제한 (메모리 누수 방지)
                if len(self._ws_trades_seen) > 2000:
                    self._ws_trades_seen = set(list(self._ws_trades_seen)[-1000:])
            self._ws_trades_queue.append(t)
            added += 1
        if added > 0:
            log.debug(f"WS account_trades: {added}건 추가 (queue={len(self._ws_trades_queue)})")

    # ══ Market Stats 파싱 — 심볼/마켓 정보 실시간 수집 ══════
    def _parse_market_stats(self, msg: dict):
        """WS market_stats 채널 → 심볼/마켓 정보 동적 갱신
        
        노이반님 요청: WS로 모든 164개 마켓 심볼 수집, REST 교차검증
        channel: market_stats/{mid}
        market_stats.symbol, market_stats.market_id
        """
        ms = msg.get("market_stats", {})
        mid = ms.get("market_id")
        sym = ms.get("symbol")
        if mid is None or not sym:
            return
        mid = int(mid)
        # 런타임 전역 갱신 (새 마켓 발견 시)
        if mid not in MID_TO_SYMBOL or MID_TO_SYMBOL[mid] != sym:
            MID_TO_SYMBOL[mid] = sym
            SYMBOL_TO_MID[sym] = mid
            if mid not in MARKET_LEVERAGE:
                MARKET_LEVERAGE[mid] = 5  # 기본값 (REST 갱신 시 업데이트)
                log.info(f"📋 신규 마켓 발견: mid={mid} sym={sym} (lev=5x 기본, REST 갱신 대기)")

    # ══ OB 상태 파싱 ══════════════════════════════════════
    def _parse_ob_subscribed(self, msg: dict):
        ch = msg.get("channel", "")
        mid_str = ch.split(":")[-1]
        ob = msg.get("order_book", {})
        bids = ob.get("bids", []); asks = ob.get("asks", [])
        self._ob[mid_str] = {"bids": bids, "asks": asks}
        self._ob_subs.add(mid_str)
        self._ob_pending.discard(mid_str)
        if len(self._ob_subs) % 10 == 1:  # 10개마다 한 번 로그
            log.info(f"📗 OB 구독 완료: {len(self._ob_subs)}개 | mid={mid_str} bids={len(bids)} asks={len(asks)}")

    def _parse_ob_update(self, msg: dict):
        ch = msg.get("channel", "")
        mid_str = ch.split(":")[-1]
        if mid_str not in self._ob:
            return
        update = msg.get("order_book", {})
        existing = self._ob[mid_str]
        self._apply_ob_diff(update.get("bids", []), existing["bids"])
        self._apply_ob_diff(update.get("asks", []), existing["asks"])

    @staticmethod
    def _apply_ob_diff(new_orders: list, existing: list):
        """OB 업데이트 적용 — 공식 WsClient update_orders 로직 그대로 적용"""
        for new_order in new_orders:
            is_new_order = True
            existing_copy = existing[:]
            for existing_order in existing_copy:
                if new_order["price"] == existing_order["price"]:
                    is_new_order = False
                    existing_order["size"] = new_order["size"]
                    if float(new_order["size"]) == 0:
                        existing.remove(existing_order)
            if is_new_order and float(new_order.get("size", 0)) > 0:
                existing.append({"price": new_order["price"], "size": new_order["size"]})
        # 최종 cleanup
        existing[:] = [o for o in existing if float(o.get("size", 0)) > 0]

    # ══ WebSocket 메인 루프 ═══════════════════════════════
    async def _ws_loop(self):
        """
        WebSocket 연결 + 메시지 루프 (자동 재연결)

        구독 분리 (WS 제한: 100 subs/connection — 여유있게 3분할):
          conn1 (primary): account_all(1) + OB 첫 52개 (~53 subs)
          conn2 (ob_ext2): OB 52개
          conn3 (ob_ext3): OB 나머지 (~52개)
        → 연결당 ~55개 이하, 총 156개 마켓 OB 전부 수신 가능

        FIX: conn1 재연결 시 기존 conn2/conn3 태스크 취소 후 새로 생성
        (기존: cancel 없이 ensure_future → 재연결마다 태스크 누적 → WS 폭발 → 429)
        """
        import websockets as _ws_lib
        import ssl as _ssl_mod
        backoff = 1.0
        _ext_tasks: list = []  # conn2/conn3 태스크 추적

        def _cancel_ext_tasks():
            for t in _ext_tasks:
                if t and not t.done():
                    t.cancel()
            _ext_tasks.clear()

        # OB 구독 복구: Tier1 마켓 + 현재 포지션 마켓
        # WHY: WS OB 실시간 → OF Engine REST 0회 → IP rate limit 완전 해소
        #      WS Builder도 order_book 구독하지만 별도 연결 → 충돌 없음
        # account_all(1) + OB(Tier1≈22) = ~23개 구독 → WS 안정성 충분
        _ob_mids = list(range(0, 170))  # 전체 170마켓 OB 구독
        mids_c1 = [str(m) for m in _ob_mids]
        mids_c2 = []
        mids_c3 = []
        log.info(f"📡 WS OB 구독 대상: {len(mids_c1)}개 전체 마켓")

        while True:
            try:
                log.info(f"🌐 WS 연결 중... (시도 #{self._reconnect_cnt + 1})")
                _cancel_ext_tasks()  # 이전 conn2/conn3 태스크 취소

                _ssl_ctx = _ssl_mod.create_default_context()
                _ssl_ctx.check_hostname = False
                _ssl_ctx.verify_mode = _ssl_mod.CERT_NONE
                async with _ws_lib.connect(
                    self.WS_URL, ping_interval=None, close_timeout=5,
                    ssl=_ssl_ctx,
                ) as ws1:
                    self._ws = ws1
                    self._reconnect_cnt += 1
                    backoff = 1.0
                    _ext_spawned = False  # conn1 세션 내에서 conn2/conn3 한 번만 생성

                    # conn1 수신 루프 — connected 수신 후 구독 전송 (공식 WsClient 패턴)
                    async for raw in ws1:
                        msg = json.loads(raw)
                        mtype = msg.get("type", "")
                        if mtype == "connected":
                            # connected 수신 후 구독 전송
                            self._connected = True
                            log.info(f"🌐 WS conn1 연결됨 (account + OB {len(mids_c1)}개)")
                            await ws1.send(json.dumps({"type":"subscribe","channel":f"account_all/{ACCOUNT_IDX}"}))
                            # user_stats 구독 — collateral/equity/available WS primary
                            await ws1.send(json.dumps({"type":"subscribe","channel":f"user_stats/{ACCOUNT_IDX}"}))
                            # account_all_trades 구독 — 실시간 체결 내역 → trade_history 누락 방지
                            # WHY: force_close 실패/재시작 중 청산 → EXT 처리 → trade_history 누락
                            #      WS 체결 이벤트 기반 기록으로 근본 해결
                            await ws1.send(json.dumps({"type":"subscribe","channel":f"account_all_trades/{ACCOUNT_IDX}"}))
                            log.info(f"📡 WS account_all_trades/{ACCOUNT_IDX} 구독 완료 → 실시간 체결 기록 활성화")
                            # market_stats 전체 구독 — 심볼/마켓 정보 실시간 수집
                            for _ms_mid in range(0, 170):
                                await ws1.send(json.dumps({"type":"subscribe","channel":f"market_stats/{_ms_mid}"}))
                            for mid_str in mids_c1:
                                await ws1.send(json.dumps({"type":"subscribe","channel":f"order_book/{mid_str}"}))
                                self._ob_pending.add(mid_str)
                            # conn2/conn3: 이 conn1 세션에서 한 번만 생성
                            if not _ext_spawned:
                                _ext_spawned = True
                                def _suppress_exc(t):
                                    try: t.result()
                                    except (Exception, BaseException): pass
                                if mids_c2:
                                    _t2 = asyncio.ensure_future(self._ws_loop_ext(mids_c2, label="conn2"))
                                    _t2.add_done_callback(_suppress_exc)
                                    _ext_tasks.append(_t2)
                                if mids_c3:
                                    _t3 = asyncio.ensure_future(self._ws_loop_ext(mids_c3, label="conn3"))
                                    _t3.add_done_callback(_suppress_exc)
                                    _ext_tasks.append(_t3)
                        elif mtype == "ping":
                            await ws1.send(json.dumps({"type": "pong"}))
                        elif mtype == "subscribed/account_all":
                            self._parse_account(msg, is_update=False)
                            self._parse_account_trades(msg)   # trades 필드도 처리
                        elif mtype == "update/account_all":
                            self._parse_account(msg, is_update=True)
                            self._parse_account_trades(msg)   # 체결 즉시 큐에 추가
                        elif mtype in ("subscribed/account_all_trades", "update/account_all_trades"):
                            self._parse_account_trades(msg)   # 전용 채널 처리
                        elif mtype == "subscribed/order_book":
                            self._parse_ob_subscribed(msg)
                        elif mtype == "update/order_book":
                            self._parse_ob_update(msg)
                        elif "market_stats" in mtype:
                            self._parse_market_stats(msg)
                        elif "user_stats" in mtype:
                            self._parse_user_stats(msg)

            except Exception as e:
                self._connected = False
                self._ws = None
                _cancel_ext_tasks()  # conn1 끊기면 conn2/conn3도 즉시 취소
                log.warning(f"🌐 WS conn1 끊김: {e} — {backoff:.0f}초 후 재연결")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _ws_loop_ext(self, mids: list, label: str = "conn2"):
        """conn2/conn3: OB 구독 전용 (자동 재연결 없음 — _ws_loop에서 재생성)"""
        import websockets as _ws_lib
        import ssl as _ssl_mod
        try:
            log.info(f"🌐 WS {label} 연결 중 (OB {len(mids)}개)")
            _ssl_ctx2 = _ssl_mod.create_default_context()
            _ssl_ctx2.check_hostname = False
            _ssl_ctx2.verify_mode = _ssl_mod.CERT_NONE
            async with _ws_lib.connect(
                self.WS_URL, ping_interval=None, close_timeout=5,
                ssl=_ssl_ctx2,
            ) as ws_ext:
                async for raw in ws_ext:
                    msg = json.loads(raw)
                    mtype = msg.get("type", "")
                    if mtype == "connected":
                        log.info(f"🌐 WS {label} 연결됨 (OB {len(mids)}개)")
                        for mid_str in mids:
                            await ws_ext.send(json.dumps({"type":"subscribe","channel":f"order_book/{mid_str}"}))
                            self._ob_pending.add(mid_str)
                    elif mtype == "ping":
                        await ws_ext.send(json.dumps({"type": "pong"}))
                    elif mtype == "subscribed/order_book":
                        self._parse_ob_subscribed(msg)
                    elif mtype == "update/order_book":
                        self._parse_ob_update(msg)
        except asyncio.CancelledError:
            log.info(f"🌐 WS {label} 취소됨 (conn1 재연결)")
        except Exception as e:
            log.warning(f"🌐 WS {label} 끊김: {e}")

    async def start(self):
        """백그라운드 WS 루프 시작"""
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._ws_loop())
            # 첫 account 데이터 수신까지 대기 (최대 10초)
            for _ in range(100):
                if self._acc_ready:
                    break
                await asyncio.sleep(0.1)
            if self._acc_ready and self._rest_collateral > 0:
                log.info(f"🌐 WS 준비 완료: equity=${self._equity:.2f} pos={len([p for p in self._positions.values() if abs(float(p.get('position',0)))>0])}개")
            elif self._acc_ready:
                log.info(f"🌐 WS 포지션 수신됨 — REST collateral 복구 대기 중 (equity 미확정)")
            elif self._positions:
                log.info(f"🌐 WS 포지션 데이터 수신됨 — REST equity 복구 대기 중")
            else:
                log.warning("🌐 WS account 데이터 수신 대기 중 (타임아웃)")

# 전역 싱글턴
WS_MANAGER = LighterWSManager()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LighterAPI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class LighterAPI:
    def __init__(self):
        self._cache:    Optional[Dict] = None
        self._cache_ts: float          = 0.0

    # ── Rate-limit 추적 ──────────────────────────────────────────
    _rl_hit_at:   float = 0.0
    _rl_backoff:  float = 0.0
    _rl_hit_cnt:  int   = 0
    _rest_sync_ts: float = -60.0  # 마지막 REST 동기화 시각 (-60 → 시작 60초 후 첫 교차검증)
    REST_SYNC_INTERVAL = 300.0   # WS 우선 — 5분마다 REST 교차검증 (충분)

    async def account(self, force: bool = False) -> Dict:
        """
        계좌 조회 — WS 완전 우선 (REST 호출 없음)
        1순위: WS_MANAGER (실시간, rate limit 0회)
          - user_stats: collateral/equity/available (primary)
          - account_all: positions/uPnL (primary)
          - liq_price: REST 5분 교차검증으로만 주입 (최소화)
        2순위: REST API (WS 미준비/재연결 중만, TTL 캐시 포함)
        """
        now = time.time()

        # ── 1순위: WebSocket 실시간 데이터 ─────────────────────
        if WS_MANAGER.is_ready():
            ws_data = WS_MANAGER.get_account()
            self._cache    = ws_data
            self._cache_ts = now
            return ws_data

        # ── 2순위: REST API (WS 미준비/재연결 중만) ────────────
        if self._cache and (self._rl_backoff > 0 or now - self._cache_ts < ACCOUNT_TTL_IDLE):
            return self._cache
        return await self._fetch_rest_with_cache(now, force)

    async def _fetch_rest_with_cache(self, now: float, force: bool = False) -> Dict:
        """REST API 조회 (백오프 + TTL 캐시 포함)"""
        # 백오프 중이면 캐시 반환
        if self._rl_backoff > 0 and now - self._rl_hit_at < self._rl_backoff:
            remain = int(self._rl_backoff - (now - self._rl_hit_at))
            if remain % 30 == 0 or remain <= 5:
                log.warning(f"Account REST: 백오프 중 ({remain}초 남음)")
            return self._cache or {"equity": 0.0, "available": 0.0, "positions": []}

        # 백오프 해제
        if self._rl_backoff > 0 and now - self._rl_hit_at >= self._rl_backoff:
            self._rl_backoff = 0.0
            self._rl_hit_cnt = 0
            log.info("Account REST: 백오프 해제 → 정상 조회 재개")

        # TTL 캐시
        ttl = ACCOUNT_TTL if (self._cache and self._cache.get("positions")) else ACCOUNT_TTL_IDLE
        if not force and self._cache and now - self._cache_ts < ttl:
            return self._cache
        fallback = self._cache or {"equity": 0.0, "available": 0.0, "positions": []}
        try:
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    "https://mainnet.zklighter.elliot.ai/api/v1/account?by=index&value=1058",
                    headers={"accept": "application/json"},
                    verify=False, timeout=8,
                )
            )
            d = resp.json()
            if "accounts" not in d:
                # Rate limit 또는 오류 → 지수 백오프
                self._rl_hit_cnt += 1
                self._rl_hit_at   = now
                self._rl_backoff  = min(30 * (2 ** (self._rl_hit_cnt - 1)), 300)  # 최소 30s, 최대 5분
                # 백오프 이후 TTL도 연장 (캐시 강제 유지)
                if self._cache:
                    self._cache_ts = now  # TTL 리셋 → 백오프 중 재호출 방지
                log.warning(f"Account API: {d.get('message','rate_limit')} — 백오프 {self._rl_backoff:.0f}s")
                return fallback
            a          = d["accounts"][0]
            # BUG-1 FIX: .get()으로 KeyError 방지
            collateral = float(a.get("collateral") or 0)
            # total_asset_value = equity (collateral + uPnL), API가 직접 제공
            equity_api = float(a.get("total_asset_value") or 0)
            api_avail  = float(a.get("available_balance") or 0)
            total_upnl  = 0.0
            total_margin = 0.0
            positions   = []
            for p in a.get("positions", []):
                pos_size = float(p.get("position", 0))
                if abs(pos_size) < 1e-9:
                    continue
                mid   = p["market_id"]
                sym   = p.get("symbol") or MID_TO_SYMBOL.get(mid, str(mid))
                # 포지션 방향: sign 필드 우선 (1=LONG, -1=SHORT), fallback: position 부호
                _sign_val = p.get("sign")
                if _sign_val is not None:
                    side = "LONG" if int(_sign_val) > 0 else "SHORT"
                else:
                    side = "LONG" if pos_size > 0 else "SHORT"
                entry = float(p.get("avg_entry_price") or 0)
                upnl  = float(p.get("unrealized_pnl") or 0)
                pval  = float(p.get("position_value") or 0)
                # FIX: REST에 "initial_margin" 필드 없음 → "initial_margin_fraction" 사용
                imf   = float(p.get("initial_margin_fraction") or 0)
                lev   = MARKET_LEVERAGE.get(mid, 10)
                im    = abs(pval) * (imf / 100) if imf > 0 else abs(pval) / lev
                total_upnl   += upnl
                total_margin += im
                positions.append({
                    "symbol": sym, "market_id": mid,
                    "side": side, "entry_price": entry,
                    "size": abs(pos_size),
                    "unrealized_pnl": upnl,
                    "position_value": abs(pval),
                    "liquidation_price": float(p.get("liquidation_price") or 0),
                })
            equity = equity_api if equity_api > 0 else (collateral + total_upnl)
            # BUG-2 FIX: available 임계값 0.001 → 0 (소액 정확도)
            available = api_avail if api_avail > 0 else max(0.0, equity - total_margin)
            col = collateral if collateral > 0 else max(0.0, equity - total_upnl)
            data   = {"equity": equity, "available": available, "collateral": col, "positions": positions}
            self._cache    = data
            self._cache_ts = now
            return data
        except Exception as e:
            log.error(f"Account error: {e}")
            return fallback

    async def _fetch_rest(self) -> Optional[Dict]:
        """REST 직접 조회 (캐시/백오프 무시 — 교차검증 전용)"""
        try:
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    "https://mainnet.zklighter.elliot.ai/api/v1/account?by=index&value=1058",
                    headers={"accept": "application/json"},
                    verify=False, timeout=8,
                )
            )
            d = resp.json()
            if "accounts" not in d:
                return None
            # ── REST 교차검증: 레버리지만 갱신 (심볼은 WS primary) ──
            # WHY: WS market_stats가 심볼 primary source
            #      REST는 initial_margin_fraction → 레버리지 교차검증 전용
            try:
                for _p in d["accounts"][0].get("positions", []):
                    _mid = int(_p["market_id"])
                    _imf = float(_p.get("initial_margin_fraction", 0) or 0)
                    if _imf > 0:
                        _lev = int(100 / _imf)
                        if MARKET_LEVERAGE.get(_mid) != _lev:
                            log.debug(f"📋 레버리지 교차검증: mid={_mid} {MARKET_LEVERAGE.get(_mid,'?')}x→{_lev}x")
                            MARKET_LEVERAGE[_mid] = _lev
                    # ── liq_price: REST가 유일한 정확한 소스 ──
                    # WHY: WS account_all의 liquidation_price = "" (항상 빈 문자열)
                    #      entry × (1-imf/100) 공식은 Cross margin에서 틀림 (오차 40~340%)
                    #      REST API가 계좌 전체 collateral 기반으로 정확히 계산
                    _rest_liq = float(_p.get("liquidation_price", 0) or 0)
                    _mid_str  = str(_mid)
                    if _rest_liq > 0 and _mid_str in self._positions:
                        self._positions[_mid_str]["liquidation_price"] = _rest_liq
                        self._positions[_mid_str]["_liq_from_rest"] = True  # REST sync 신뢰 플래그
                        log.debug(f"📋 liq_price REST 갱신: mid={_mid} liq={_rest_liq:.4f}")
            except Exception as _me:
                log.debug(f"레버리지 교차검증 실패: {_me}")
            return self._parse_rest_response(d)
        except Exception:
            return None

    def _parse_rest_response(self, d: dict) -> Dict:
        """REST 응답 파싱 (account() 내부 로직 재사용)"""
        a = d["accounts"][0]
        # BUG-1 FIX: a["collateral"] → .get() (필드 없을 때 KeyError 방지)
        collateral  = float(a.get("collateral") or 0)
        equity_api  = float(a.get("total_asset_value") or 0)
        # BUG-2 FIX: available_balance 0.001 임계값 제거 (소액 잔고 정확도)
        api_avail   = float(a.get("available_balance") or 0)
        total_upnl  = 0.0
        total_margin= 0.0
        positions   = []
        for p in a.get("positions", []):
            pos_size = float(p.get("position", 0))
            if abs(pos_size) < 1e-9: continue
            mid   = p["market_id"]
            sym   = p.get("symbol") or MID_TO_SYMBOL.get(mid, str(mid))
            # 포지션 방향: sign 필드 우선 (1=LONG, -1=SHORT), fallback: position 부호
            _sign_val2 = p.get("sign")
            if _sign_val2 is not None:
                side = "LONG" if int(_sign_val2) > 0 else "SHORT"
            else:
                side = "LONG" if pos_size > 0 else "SHORT"
            entry = float(p.get("avg_entry_price") or 0)
            upnl  = float(p.get("unrealized_pnl") or 0)
            pval  = float(p.get("position_value") or 0)
            # FIX: REST에 "initial_margin" 필드 없음 → "initial_margin_fraction" 사용
            imf   = float(p.get("initial_margin_fraction") or 0)
            lev   = MARKET_LEVERAGE.get(mid, 10)
            im    = abs(pval) * (imf / 100) if imf > 0 else abs(pval) / lev
            total_upnl   += upnl
            total_margin += im
            positions.append({
                "symbol": sym, "market_id": mid, "side": side,
                "entry_price": entry, "size": abs(pos_size),
                "unrealized_pnl": upnl, "position_value": abs(pval),
                "liquidation_price": float(p.get("liquidation_price") or 0),
            })
        # equity: total_asset_value 우선, 없으면 collateral+uPnL
        equity = equity_api if equity_api > 0 else (collateral + total_upnl)
        # available: api_avail 우선, 없으면 역산 (임계값 제거)
        available = api_avail if api_avail > 0 else max(0.0, equity - total_margin)
        # collateral: 실제 예치금 (주문 한도 기준) — uPnL 제외
        col = collateral if collateral > 0 else max(0.0, equity - total_upnl)
        return {"equity": equity, "available": available, "collateral": col, "positions": positions}

    # ── aiohttp 세션 (재사용 — 연결 풀) ──────────────────────────
    _aio_session: Optional[object] = None   # aiohttp.ClientSession

    async def _get_session(self):
        """aiohttp 세션 싱글턴 — 연결 재사용으로 오버헤드 최소화"""
        if self._aio_session is None or self._aio_session.closed:
            connector = aiohttp.TCPConnector(
                limit=200,            # FIX: 100→200 (156마켓×8TF 동시 커버)
                limit_per_host=100,   # FIX: 50→100 (로컬 Flask 과부하 완화)
                keepalive_timeout=30,
            )
            self._aio_session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=5),  # FIX: 2→5초 (큐 대기 시간 포함)
            )
        return self._aio_session

    async def get_candles_async(self, market_id: int, tf: str, n: int = 50) -> List[Dict]:
        """
        비동기 캔들 조회 (aiohttp) — run_in_executor 불필요
        WS Builder 로컬 HTTP → 연결 풀 재사용으로 지연 최소화
        """
        try:
            session = await self._get_session()
            async with session.get(f"{WS_URL}/api/candles/{market_id}/{tf}") as r:
                if r.status == 200:
                    d = (await r.json()).get("candles", [])
                    if d:
                        return d[-n:] if len(d) > n else d
        except Exception:
            pass
        return []

    # 하위 호환 (sync wrapper — run_in_executor에서 호출되는 경우 대비)
    def get_candles(self, market_id: int, tf: str, n: int = 40) -> List[Dict]:
        try:
            r = requests.get(f"{WS_URL}/api/candles/{market_id}/{tf}", timeout=2)
            if r.status_code == 200:
                d = r.json().get("candles", [])
                if d:
                    return d[-n:] if len(d) > n else d
        except:
            pass
        return []

    async def _run_order(self, payload: Dict) -> bool:
        """execute_order 직접 await — 싱글턴 SignerClient 사용
        
        Rate Limit 최적화 (주문 1건당 API req):
          - 이전: nonce(1) + orderbook(1) + send_tx(1) = 3 req
          - 현재: send_tx(1) = 1 req  (싱글턴 + ideal_price 직접전달)
          
        Rate limit 응답 시: 2s 대기 후 1회 재시도
        nonce 오류 시: execute_order_final에서 자동 리셋
        """
        from execute_order_final import execute_order as _exec_order

        for attempt in range(2):
            try:
                resp = await _exec_order(payload)
                if resp.get("success"):
                    return True
                err = resp.get("error", "")
                if "Too Many Requests" in err or "429" in err:
                    wait = 3 if attempt == 0 else 0
                    if wait:
                        log.warning(f"⚠️ Rate limit — {wait}s 대기 후 재시도")
                        payload.pop("ideal_price", None)
                        await asyncio.sleep(wait)
                        continue
                    log.warning("⚠️ Rate limit 재시도 실패")
                    return False
                elif "nonce" in err.lower():
                    if attempt == 0:
                        await asyncio.sleep(0.3)
                        continue
                    return False
                elif "volume_quota_exhausted" in err or "volume quota" in err.lower():
                    # volume quota 소진 → 봇 레벨에서 60초 진입 차단 (반복 시도 방지)
                    log.warning(f"⚠️ volume quota 소진 — 60초 진입 차단 (SDK 호출 차단)")
                    # TradingBot 인스턴스를 직접 참조할 수 없어 전역 변수 사용
                    import builtins
                    setattr(builtins, '_QUOTA_BLOCK_UNTIL', time.time() + 60)
                    return False
                elif err:
                    log.error(f"Order rejected: {err[:100]}")
                    return False
                else:
                    # success=False이지만 에러 메시지 없음 → resp 전체 로깅
                    log.warning(f"Order failed (no error msg): {str(resp)[:150]}")
                    return False
            except Exception as e:
                log.error(f"Order exception: {e}")
                return False
        return False

    async def open_order(self, market_id: int, side: str, size: float,
                         price: float = 0, ideal_price_int: int = None) -> bool:
        payload = {
            "marketId": market_id, "side": side.upper(),
            "size": size, "price": price, "reduce_only": False,
        }
        if ideal_price_int:
            payload["ideal_price"] = ideal_price_int
        return await self._run_order(payload)

    async def close_order(self, market_id: int, side: str, size: float,
                          price: float = 0, ideal_price_int: int = None) -> bool:
        # side = 포지션 방향 (LONG/SHORT) — execute_order_final에서 reduce_only 맥락에 맞게 is_ask 결정
        # LONG 포지션 청산 → is_ask=True (sell), SHORT 포지션 청산 → is_ask=False (buy)
        payload = {
            "marketId": market_id, "side": side,  # 원래 포지션 방향 그대로 전달
            "size": size, "price": price, "reduce_only": True,
        }
        if ideal_price_int:
            payload["ideal_price"] = ideal_price_int
        return await self._run_order(payload)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SignalEngine - 쿼드 타임프레임
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class SignalEngine:
    """
    v4.1 스캘핑 신호 엔진 — 3-Layer 전략
    ─────────────────────────────────────
    Layer 1: 15m 추세 필터  → 반추세 진입 차단 (LONG 편향 문제 해결)
    Layer 2: RSI 극값 반전  → 과매도/과매수 반전 포착 (스캘핑 핵심)
    Layer 3: 볼린저밴드 + EMA 확인 → 진입 타이밍 정밀화

    스코어 구성 (100점):
      - 추세 정렬       : 25pt  (15m 방향 일치)
      - RSI 반전 강도   : 30pt  (극값일수록 고점)
      - BB 포지션       : 20pt  (밴드 이탈/터치)
      - EMA 방향        : 15pt  (1m+5m 정렬)
      - 거래량 확인     : 10pt  (surge)
    """

    @staticmethod
    def _ema(data: List[float], p: int) -> float:
        if not data: return 0.0
        k = 2.0 / (p + 1)
        e = data[0]
        for v in data[1:]:
            e = v * k + e * (1 - k)
        return e

    @staticmethod
    def _rsi(data: List[float], p: int = 14) -> float:
        if len(data) < p + 1: return 50.0
        gains, losses = [], []
        for i in range(1, len(data)):
            d = data[i] - data[i-1]
            gains.append(max(d, 0)); losses.append(max(-d, 0))
        ag = sum(gains[-p:]) / p
        al = sum(losses[-p:]) / p
        if al == 0: return 100.0
        return 100 - 100 / (1 + ag / al)

    @staticmethod
    def _bb(data: List[float], p: int = 20, k: float = 2.0):
        """볼린저밴드 반환 (upper, mid, lower)"""
        if len(data) < p:
            m = data[-1]
            return m, m, m
        window = data[-p:]
        m  = sum(window) / p
        sd = (sum((x - m) ** 2 for x in window) / p) ** 0.5
        return m + k * sd, m, m - k * sd

    @staticmethod
    def _vol_avg(candles: List[Dict], n: int = 10) -> float:
        vols = [float(c.get("volume", 0)) for c in candles[-n-1:-1]]
        return sum(vols) / len(vols) if vols else 0.0

    @staticmethod
    def _atr(candles: List[Dict], p: int = 14) -> float:
        """Average True Range"""
        if len(candles) < 2: return 0.0
        trs = []
        for i in range(1, len(candles)):
            h = float(candles[i].get("high", candles[i]["close"]))
            l = float(candles[i].get("low",  candles[i]["close"]))
            pc= float(candles[i-1]["close"])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        subset = trs[-p:]
        return sum(subset) / len(subset) if subset else 0.0

    @staticmethod
    def _supertrend(candles: List[Dict], period: int = 10, mult: float = 3.0) -> Optional[str]:
        """
        Supertrend 방향 계산 (UP / DOWN)
        - candles: high/low/close 필드 포함한 OHLC 리스트
        - return: "UP" or "DOWN" or None (데이터 부족)
        """
        if len(candles) < period + 2:
            return None
        closes = [float(c["close"]) for c in candles]
        highs  = [float(c.get("high", c["close"])) for c in candles]
        lows   = [float(c.get("low",  c["close"])) for c in candles]

        # ATR 계산
        trs = []
        for i in range(1, len(candles)):
            trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))

        # Supertrend band 계산 (마지막 period 기간)
        n = len(closes)
        st_dir = "UP"   # 디폴트
        upper_band = lower_band = 0.0

        for i in range(period, n):
            # ATR (simple avg)
            atr = sum(trs[i-period:i]) / period
            hl2 = (highs[i] + lows[i]) / 2
            basic_upper = hl2 + mult * atr
            basic_lower = hl2 - mult * atr

            # Final bands (with continuity)
            if i == period:
                upper_band = basic_upper
                lower_band = basic_lower
            else:
                upper_band = basic_upper if basic_upper < upper_band or closes[i-1] > upper_band else upper_band
                lower_band = basic_lower if basic_lower > lower_band or closes[i-1] < lower_band else lower_band

            # Direction
            if closes[i] > upper_band:
                st_dir = "UP"
            elif closes[i] < lower_band:
                st_dir = "DOWN"
            # else: 이전 방향 유지

        return st_dir

    @staticmethod
    def _adx(candles: List[Dict], p: int = 14) -> Tuple[float, float, float]:
        """
        ADX (Average Directional Index) 계산
        Returns: (adx, plus_di, minus_di)
          adx      : 추세 강도 (0~100, >25=추세장, <20=횡보장)
          plus_di  : +DI (상승 방향 강도)
          minus_di : -DI (하락 방향 강도)
        """
        if len(candles) < p + 2:
            return 0.0, 0.0, 0.0

        highs  = [float(c.get("high",  c["close"])) for c in candles]
        lows   = [float(c.get("low",   c["close"])) for c in candles]
        closes = [float(c["close"]) for c in candles]
        n = len(candles)

        # True Range, +DM, -DM
        trs, pdms, mdms = [], [], []
        for i in range(1, n):
            tr  = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            pdm = max(highs[i]-highs[i-1], 0) if (highs[i]-highs[i-1]) > (lows[i-1]-lows[i]) else 0
            mdm = max(lows[i-1]-lows[i], 0)   if (lows[i-1]-lows[i]) > (highs[i]-highs[i-1]) else 0
            trs.append(tr); pdms.append(pdm); mdms.append(mdm)

        # Wilder's smoothing: 초기값 = 첫 period개의 평균 (정규화)
        def wilder_smooth(data, period):
            if len(data) < period:
                return []
            smooth = [sum(data[:period]) / period]  # 평균으로 시작
            for i in range(period, len(data)):
                smooth.append(smooth[-1] - smooth[-1]/period + data[i]/period)
            return smooth

        atr_s  = wilder_smooth(trs,  p)
        pdm_s  = wilder_smooth(pdms, p)
        mdm_s  = wilder_smooth(mdms, p)

        if not atr_s: return 0.0, 0.0, 0.0

        # +DI, -DI 시리즈
        di_plus  = [100 * pdm_s[i] / atr_s[i] if atr_s[i] > 0 else 0 for i in range(len(atr_s))]
        di_minus = [100 * mdm_s[i] / atr_s[i] if atr_s[i] > 0 else 0 for i in range(len(atr_s))]

        # DX 시리즈
        dx_list = []
        for i in range(len(di_plus)):
            denom = di_plus[i] + di_minus[i]
            dx_list.append(100 * abs(di_plus[i]-di_minus[i]) / denom if denom > 0 else 0)

        # ADX = Wilder smooth of DX
        if len(dx_list) < p:
            return 0.0, di_plus[-1] if di_plus else 0.0, di_minus[-1] if di_minus else 0.0

        adx_s = wilder_smooth(dx_list, p)
        adx   = adx_s[-1] if adx_s else 0.0
        pdi   = di_plus[-1]
        mdi   = di_minus[-1]
        return adx, pdi, mdi

    def score(self, c1m, c3m, c5m, c15m=None, c30s=None, c15s=None,
              c1h=None, c4h=None, funding_rate: Optional[float] = None) -> Optional[Dict]:
        """
        v4.6 — 5m 메인 + 3m 중간 필터 + 1m/30s/15s 타이밍
        ════════════════════════════════════════════════════════
        TF 계층:
          4h      → 거시 추세 (EMA20/200) — 방향 바이어스
          1h      → 주요 추세 (EMA20/100/200)
          15m     → 중기 추세 (EMA20/100)
          ────────────────────────────────────────────────────
          5m [MAIN]  RSI14, ADX14, BB, Supertrend, EMA5/20
                     → 핵심 진입 판단 (노이즈 최소화)
          3m [FILTER] EMA5/20, RSI14
                     → 5m과 1m 사이 중간 확인 필터
          ────────────────────────────────────────────────────
          1m      → 타이밍 확인 (EMA5/20 방향)
          30s     → 모멘텀 bonus (+5pt)
          15s     → 즉시성 bonus (+4pt)

        스코어 구성 (최대 120pt):
          거시추세 (4h/1h/15m)  : 30pt
          5m RSI 반전           : 25pt
          5m BB 이탈            : 20pt
          5m ADX/추세 정렬      : 20pt
          3m EMA 방향 일치      : +10pt bonus (중간 필터)
          1m 타이밍 정렬        : +8pt bonus
          거래량 (5m)           : 10pt
          30s/15s bonus         : +9pt
        ════════════════════════════════════════════════════════
        """
        if len(c1m) < 1 or len(c5m) < 3:
            return None

        cl1 = [float(c["close"]) for c in c1m]
        cl5 = [float(c["close"]) for c in c5m]
        cur = cl5[-1]   # 현재가: 5m 기준 (노이즈 감소)

        # ── EMA 계산 ───────────────────────────────────────────
        # 5m (메인)
        e5_5   = self._ema(cl5, min(5,   len(cl5)))
        e20_5  = self._ema(cl5, min(20,  len(cl5)))
        e100_5 = self._ema(cl5, min(100, len(cl5)))

        # 3m (중간 필터)
        e5_3 = e20_3 = None
        rsi3 = None
        has_3m = c3m and len(c3m) >= 5
        if has_3m:
            cl3 = [float(c["close"]) for c in c3m]
            e5_3  = self._ema(cl3, min(5,  len(cl3)))
            e20_3 = self._ema(cl3, min(20, len(cl3)))
            rsi3  = self._rsi(cl3, 14) if len(cl3) >= 14 else None

        # 1m (타이밍)
        e5_1  = self._ema(cl1, min(5,  len(cl1)))
        e20_1 = self._ema(cl1, min(20, len(cl1)))

        # 15m
        e20_15m = e100_15m = None
        if c15m and len(c15m) >= 3:
            cl15 = [float(c["close"]) for c in c15m]
            e20_15m  = self._ema(cl15, min(20,  len(cl15)))
            e100_15m = self._ema(cl15, min(100, len(cl15)))

        # 1h
        e20_1h = e100_1h = e200_1h = None
        if c1h and len(c1h) >= 2:
            cl1h = [float(c["close"]) for c in c1h]
            e20_1h  = self._ema(cl1h, min(20,  len(cl1h)))
            e100_1h = self._ema(cl1h, min(100, len(cl1h)))
            e200_1h = self._ema(cl1h, min(200, len(cl1h)))

        # 4h
        e20_4h = e200_4h = None
        if c4h and len(c4h) >= 2:
            cl4h = [float(c["close"]) for c in c4h]
            e20_4h  = self._ema(cl4h, min(20,  len(cl4h)))
            e200_4h = self._ema(cl4h, min(200, len(cl4h)))

        # ── Layer 1: 거시 추세 (4h > 1h > 15m) ───────────────
        bull_score = 0
        bear_score = 0

        if e20_4h and e200_4h:
            if cur > e20_4h and e20_4h > e200_4h: bull_score += 3
            elif cur > e20_4h:                     bull_score += 2
            elif cur < e20_4h and e20_4h < e200_4h: bear_score += 3
            elif cur < e20_4h:                     bear_score += 2

        if e20_1h and e100_1h:
            if cur > e20_1h and e20_1h > e100_1h: bull_score += 2
            elif cur > e20_1h:                     bull_score += 1
            elif cur < e20_1h and e20_1h < e100_1h: bear_score += 2
            elif cur < e20_1h:                     bear_score += 1
            if e200_1h:
                if e100_1h > e200_1h: bull_score += 1
                elif e100_1h < e200_1h: bear_score += 1

        if e20_15m and e100_15m:
            if cur > e20_15m and e20_15m > e100_15m: bull_score += 2
            elif cur > e20_15m:                       bull_score += 1
            elif cur < e20_15m and e20_15m < e100_15m: bear_score += 2
            elif cur < e20_15m:                       bear_score += 1
        else:
            # 15m 없으면 5m으로 대체
            if cur > e20_5 and e20_5 > e100_5: bull_score += 1
            elif cur < e20_5 and e20_5 < e100_5: bear_score += 1

        trend_up      = bull_score > bear_score
        trend_dn      = bear_score > bull_score
        trend_neutral = bull_score == bear_score

        # ── Layer 2: 5m 메인 신호 ──────────────────────────────
        # RSI (5m 기준 — 70분 데이터, 훨씬 안정적)
        rsi5 = self._rsi(cl5, 14) if len(cl5) >= 14 else 50.0
        rsi1 = self._rsi(cl1, 14) if len(cl1) >= 14 else rsi5  # 표시용

        rsi_extreme_long  = rsi5 < 30
        rsi_extreme_short = rsi5 > 70

        if trend_up:
            long_rsi_ok  = rsi5 < 56   # OPT: 58→56 (Optimizer EV +0.2092→+0.2337%)
            short_rsi_ok = rsi5 > 62   # FIX: 70→62 (SHORT 진입 기회 확대)
        elif trend_dn:
            long_rsi_ok  = rsi5 < 35   # 하락추세 LONG: RSI 35 미만 유지
            short_rsi_ok = rsi5 > 32   # OPT: 42→32 (하락추세 RSI 30~42 SHORT 차단 해제)
        else:
            long_rsi_ok  = rsi5 < 48   # FIX: 52→48 (중립 LONG 강화)
            short_rsi_ok = rsi5 > 52   # FIX: 48→52 (중립 SHORT 강화)

        # ADX (5m 기준 — 횡보/추세 판단 훨씬 신뢰)
        adx5, plus_di5, minus_di5 = self._adx(c5m, 14)   # 캔들 딕셔너리 전달
        adx_regime = "trend" if adx5 >= ADX_TREND_THRESH else ("range" if adx5 < ADX_RANGE_THRESH else "transition")
        trend_aligned5 = (side_hint := ("LONG" if (50-rsi5)>(rsi5-50) else "SHORT"),
                          (side_hint == "LONG"  and plus_di5 > minus_di5) or
                          (side_hint == "SHORT" and minus_di5 > plus_di5))[1]
        # Supertrend (5m 기준)
        st_dir = self._supertrend(c5m, period=10, mult=3.0)  # 캔들 딕셔너리 전달

        # 방향 결정 (5m RSI + 거시추세)
        mom5 = (cl5[-1] - cl5[-4]) / cl5[-4] if len(cl5) >= 4 and cl5[-4] > 0 else 0

        can_long  = long_rsi_ok  and (trend_up or rsi_extreme_long  or e5_5 > e20_5 or trend_neutral)
        can_short = short_rsi_ok and (trend_dn or rsi_extreme_short or e5_5 < e20_5 or trend_neutral)

        # 4h 역추세: 완전 차단 아님 — 5m 우선, 4h는 보조 (점수 패널티만 부여)
        # 4h 역추세 강도에 따라 sc에 패널티 적용 (bonus 변수에 누적)
        _4h_penalty = 0
        if e20_4h and bear_score - bull_score >= 6 and not rsi_extreme_long:
            _4h_penalty = -15  # LONG 시 4h 강한 하락추세 → -15pt 패널티
        if e20_4h and bull_score - bear_score >= 6 and not rsi_extreme_short:
            _4h_penalty = -15  # SHORT 시 4h 강한 상승추세 → -15pt 패널티

        # RSI 극값 시 반전 방향 우선 (거시추세 override 방지)
        # RSI<30 과매도 → 반등 LONG / RSI>70 과매수 → 하락 SHORT
        if rsi5 < 30:
            side = "LONG"   # 과매도 반등
        elif rsi5 > 70:
            side = "SHORT"  # 과매수 하락
        elif can_long and not can_short: side = "LONG"
        elif can_short and not can_long: side = "SHORT"
        elif can_long and can_short:
            side = "LONG" if (50 - rsi5) > (rsi5 - 50) else "SHORT"
        else:
            return None

        # trend_aligned (방향 결정 후)
        trend_aligned = (side == "LONG"  and plus_di5 > minus_di5) or \
                        (side == "SHORT" and minus_di5 > plus_di5)

        # ── Layer 3: 스코어링 ──────────────────────────────────
        sc = 0

        # ══════════════════════════════════════════════════════════
        # v4.8 TRI-MODE: 횡보장/추세장/하락장 각각 최적 전략
        # ══════════════════════════════════════════════════════════
        bb_upper, bb_mid, bb_lower = self._bb(cl5, min(20, len(cl5)), 2.0)
        bb_width_pct = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0.02
        margin_mult = 1.0

        # ── 공통: EMA 위치 점수 (방향 확인용) ──────────────────
        # 5m EMA5 vs EMA20 기울기
        ema_slope_up   = e5_5 > e20_5 * 1.001   # 명확한 상승
        ema_slope_down = e5_5 < e20_5 * 0.999   # 명확한 하락
        ema_flat       = not ema_slope_up and not ema_slope_down

        # 현재가의 BB 밴드 내 위치 (0=하단, 0.5=중앙, 1=상단)
        bb_pos_ratio = (cur - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5

        # ── 거시추세 (공통 기반점수) ────────────────────────────
        trend_diff = bull_score - bear_score if side == "LONG" else bear_score - bull_score

        if adx_regime == "range":
            # ════════════════════════════════════════════════════
            # 횡보장 모드 (ADX < 20): 오실레이터 + BB 반전
            # ════════════════════════════════════════════════════
            # 핵심: 밴드 내에서 RSI 위치로 방향 결정, BB 위치로 강도 결정
            margin_mult = 0.9

            # 거시추세 (횡보장에서 낮은 가중치)
            if   trend_diff >= 3: sc += 8
            elif trend_diff >= 1: sc += 4
            elif trend_diff == 0: sc += 3
            elif trend_diff == -1: sc += 0
            elif trend_diff <= -2: sc -= 5

            # RSI 오실레이터 (핵심, 최대 30pt)
            # 횡보장 RSI는 30~70 사이에서 반복 → 더 넓은 구간 점수 부여
            if side == "LONG":
                if   rsi5 < 20: sc += 30
                elif rsi5 < 25: sc += 24
                elif rsi5 < 30: sc += 18
                elif rsi5 < 35: sc += 13
                elif rsi5 < 40: sc += 9
                elif rsi5 < 45: sc += 6
                elif rsi5 < 50: sc += 3   # ← 핵심: 50 미만도 점수
            else:
                if   rsi5 > 80: sc += 30
                elif rsi5 > 75: sc += 24
                elif rsi5 > 70: sc += 18
                elif rsi5 > 65: sc += 13
                elif rsi5 > 60: sc += 9
                elif rsi5 > 55: sc += 6
                elif rsi5 > 50: sc += 3   # ← 핵심: 50 초과도 점수

            # BB 내 위치 (최대 20pt) — 밴드 내에서도 작동
            if side == "LONG":
                if   cur < bb_lower * 0.998:  sc += 25  # 강한 이탈
                elif cur < bb_lower:          sc += 20  # 이탈
                elif cur < bb_lower * 1.005:  sc += 15  # 근접
                elif bb_pos_ratio < 0.25:     sc += 10  # 하단 1/4 구간
                elif bb_pos_ratio < 0.40:     sc += 5   # 하단부
                elif bb_pos_ratio > 0.75:     sc -= 5   # 상단부 LONG = 불리
            else:
                if   cur > bb_upper * 1.002:  sc += 25  # 강한 이탈
                elif cur > bb_upper:          sc += 20  # 이탈
                elif cur > bb_upper * 0.995:  sc += 15  # 근접
                elif bb_pos_ratio > 0.75:     sc += 10  # 상단 1/4 구간
                elif bb_pos_ratio > 0.60:     sc += 5   # 상단부
                elif bb_pos_ratio < 0.25:     sc -= 5   # 하단부 SHORT = 불리

            # BB 폭 좁을수록 반전 신뢰도 상승
            if   bb_width_pct < 0.012: sc += 8
            elif bb_width_pct < 0.020: sc += 4
            elif bb_width_pct < 0.030: sc += 2

            # Supertrend (횡보장에서 약한 가중치)
            if st_dir is not None:
                if   (side == "LONG"  and st_dir == "UP"):   sc += 5
                elif (side == "SHORT" and st_dir == "DOWN"):  sc += 5
                elif (side == "LONG"  and st_dir == "DOWN"):  sc -= 3
                elif (side == "SHORT" and st_dir == "UP"):    sc -= 3

            # 5m EMA 방향 일치 시 보너스 (횡보장에서 확인용)
            if (side == "LONG" and ema_slope_up) or (side == "SHORT" and ema_slope_down):
                sc += 4
            elif ema_flat:
                sc += 2  # 횡보 EMA도 OK

        elif adx_regime == "transition":
            # ════════════════════════════════════════════════════
            # 전환기 모드 (ADX 20~25): 혼합 전략
            # ════════════════════════════════════════════════════
            # 추세 형성 초입 — 방향 확인 후 진입
            margin_mult = 0.95
            sc += 5  # 전환기 보너스 (움직임 시작)

            # 거시추세
            if   trend_diff >= 4: sc += 25
            elif trend_diff >= 3: sc += 18
            elif trend_diff >= 2: sc += 12
            elif trend_diff >= 1: sc += 6
            elif trend_diff == 0: sc += 2
            elif trend_diff <= -1: sc -= 6

            # RSI (전환기: 중립 근방도 허용)
            if side == "LONG":
                if   rsi5 < 25: sc += 22
                elif rsi5 < 30: sc += 16
                elif rsi5 < 38: sc += 10
                elif rsi5 < 45: sc += 6
                elif rsi5 < 52: sc += 3
            else:
                if   rsi5 > 75: sc += 22
                elif rsi5 > 70: sc += 16
                elif rsi5 > 62: sc += 10
                elif rsi5 > 55: sc += 6
                elif rsi5 > 48: sc += 3

            # BB (전환기: 이탈=돌파 가능성)
            if side == "LONG":
                if cur < bb_lower:           sc += 18
                elif cur < bb_lower * 1.005: sc += 10
                elif bb_pos_ratio < 0.30:    sc += 5
            else:
                if cur > bb_upper:           sc += 18
                elif cur > bb_upper * 0.995: sc += 10
                elif bb_pos_ratio > 0.70:    sc += 5

            # Supertrend
            if st_dir is not None:
                if   (side == "LONG"  and st_dir == "UP"):   sc += 10
                elif (side == "SHORT" and st_dir == "DOWN"):  sc += 10
                elif (side == "LONG"  and st_dir == "DOWN"):  sc -= 6
                elif (side == "SHORT" and st_dir == "UP"):    sc -= 6

            # EMA 정렬
            if (side == "LONG" and ema_slope_up) or (side == "SHORT" and ema_slope_down):
                sc += 6

        else:
            # ════════════════════════════════════════════════════
            # 추세장 모드 (ADX ≥ 25): 모멘텀 추종
            # ════════════════════════════════════════════════════
            margin_mult = 1.0
            sc += 8  # 추세장 진입 보너스

            # 거시추세 (추세장에서 최고 가중치)
            if   trend_diff >= 4: sc += 30
            elif trend_diff >= 3: sc += 22
            elif trend_diff >= 2: sc += 14
            elif trend_diff >= 1: sc += 7
            elif trend_diff == 0: sc += 2
            elif trend_diff <= -1: sc -= 10
            if not e20_4h:
                sc = min(sc, 90)

            # RSI (추세장: 과매도/과매수 반전)
            if side == "LONG":
                if   rsi5 < 20: sc += 25
                elif rsi5 < 30: sc += 18
                elif rsi5 < 40: sc += 10
                elif rsi5 < 50: sc += 4
                elif rsi5 < 58: sc += 1
            else:
                if   rsi5 > 80: sc += 25
                elif rsi5 > 70: sc += 18
                elif rsi5 > 60: sc += 10
                elif rsi5 > 50: sc += 4
                elif rsi5 > 42: sc += 1

            # BB (추세장: 이탈 = 강한 추세 신호)
            if side == "LONG":
                if cur < bb_lower:           sc += 22
                elif cur < bb_lower * 1.005: sc += 12
            else:
                if cur > bb_upper:           sc += 22
                elif cur > bb_upper * 0.995: sc += 12

            # Supertrend (추세장에서 핵심)
            if st_dir is not None:
                if   (side == "LONG"  and st_dir == "UP"):   sc += 15
                elif (side == "SHORT" and st_dir == "DOWN"):  sc += 15
                elif (side == "LONG"  and st_dir == "DOWN"):  sc -= 10
                elif (side == "SHORT" and st_dir == "UP"):    sc -= 10

            # ADX 추세 정렬 보너스
            if trend_aligned:
                sc += 10
            # EMA 정렬 (추세장에서 중요)
            if (side == "LONG" and ema_slope_up) or (side == "SHORT" and ema_slope_down):
                sc += 6

        # 5m 거래량 (10pt) ← 5m 기준
        vol5_cur = float(c5m[-1].get("volume", 0))
        vol5_avg = self._vol_avg(c5m)
        if vol5_avg > 0:
            if   vol5_cur > vol5_avg * 2.0: sc += 10
            elif vol5_cur > vol5_avg * 1.5: sc += 7
            elif vol5_cur > vol5_avg * 1.2: sc += 4

        # 1h EMA200 구조 보너스
        if e200_1h:
            if side == "LONG"  and cur > e200_1h: sc += 5
            if side == "SHORT" and cur < e200_1h: sc += 5

        # ── Layer 4: 3m 중간 필터 + 1m 타이밍 ────────────────
        bonus = 0

        # 3m EMA 방향 확인 (+10pt / -5pt)
        if has_3m and e5_3 is not None and e20_3 is not None:
            ema_3m_ok = (side == "LONG"  and e5_3 > e20_3) or \
                        (side == "SHORT" and e5_3 < e20_3)
            if ema_3m_ok:
                bonus += 10  # 3m 정렬 확인 → 신뢰도 상승
                # 3m RSI 추가 확인 (더 강한 신호)
                if rsi3 is not None:
                    if side == "LONG"  and rsi3 < 50: bonus += 2
                    if side == "SHORT" and rsi3 > 50: bonus += 2
            else:
                sc -= 5   # 3m 역방향 → 작은 패널티 (5m과 1m 사이 불일치)

        # 1m EMA 타이밍 확인 (+8pt)
        ema_1m_ok = (side == "LONG"  and e5_1 > e20_1) or \
                    (side == "SHORT" and e5_1 < e20_1)
        if ema_1m_ok:
            bonus += 8   # 1m 타이밍 확인

        # 30s 모멘텀 확인 (+5pt)
        if c30s and len(c30s) >= 3:
            cl30 = [float(c["close"]) for c in c30s]
            e5_30  = self._ema(cl30, min(5,  len(cl30)))
            e20_30 = self._ema(cl30, min(20, len(cl30)))
            if side == "LONG"  and e5_30 > e20_30: bonus += 5
            if side == "SHORT" and e5_30 < e20_30: bonus += 5

        # 15s 즉시성 확인 (+4pt)
        if c15s and len(c15s) >= 2:
            cl15s = [float(c["close"]) for c in c15s]
            e3_15 = self._ema(cl15s, min(3, len(cl15s)))
            e5_15 = self._ema(cl15s, min(5, len(cl15s)))
            if side == "LONG"  and e3_15 > e5_15: bonus += 4
            if side == "SHORT" and e3_15 < e5_15: bonus += 4

        # ── ATR 기반 Adaptive TP/SL (5m 기준) + Spread 반영 ─
        # spread는 scan_all()에서 OF_ENGINE.analyze() 후 sig에 주입됨
        # score() 단계에서는 아직 OF 미적용이므로 sig["of_spread"]는 없음
        # → 대신 HIGH_SPREAD_SYMBOLS 여부로 spread 추정 적용
        atr_5m  = self._atr(c5m, 14)
        atr_pct = atr_5m / cur if cur > 0 else 0.0
        # Spread 추정: Tier3(High Spread) 심볼은 최소 TP를 spread×2 이상으로 보장
        # 실제 spread는 scan_all에서 OF 결과로 보정 (아래 _apply_spread_to_sig 참조)
        adaptive_tp = min(max(atr_pct * 1.2, TP_PCT), ADAPTIVE_TP_MAX)       # 하한선 TP_PCT (기존 0.8× 제거)
        adaptive_sl = min(max(atr_pct * 0.6, SL_PCT), ADAPTIVE_SL_MAX)       # 하한선 SL_PCT (기존 0.8× 제거)

        # 4h 역추세 보조 패널티 반영 (5m 우선, 4h는 보조)
        bonus += _4h_penalty

        # ── 펀딩비 시그널 ─────────────────────────────────────
        # FR 극단값 시 역방향 진입 패널티 / 일치 보너스
        # FR > 0.03%: 롱 비용 과다 → LONG 진입 패널티, SHORT 보너스
        # FR < -0.03%: 숏 비용 과다 → SHORT 진입 패널티, LONG 보너스
        fr_signal = 0.0
        if funding_rate is not None:
            if side == "LONG":
                if funding_rate > FR_HIGH_THRESH:
                    bonus += FR_PENALTY  # 롱 비용 과다 → LONG 패널티
                    fr_signal = funding_rate
                elif funding_rate < FR_LOW_THRESH:
                    bonus += FR_BONUS    # 숏 비용 과다 → LONG 유리 보너스
                    fr_signal = funding_rate
            else:  # SHORT
                if funding_rate < FR_LOW_THRESH:
                    bonus += FR_PENALTY  # 숏 비용 과다 → SHORT 패널티
                    fr_signal = funding_rate
                elif funding_rate > FR_HIGH_THRESH:
                    bonus += FR_BONUS    # 롱 비용 과다 → SHORT 유리 보너스
                    fr_signal = funding_rate

        raw_before_adx = sc
        final_score = min(sc + bonus, 120)

        block_reasons = []
        block_reasons.append(f"모드:{adx_regime[:3]}(ADX={adx5:.0f})")
        if trend_diff >= 2:   block_reasons.append(f"추세+{trend_diff}")
        if trend_diff <= -2:  block_reasons.append(f"역추세{trend_diff}")

        return {
            "side": side, "score": final_score,
            "raw_score": raw_before_adx,
            "block_reasons": block_reasons,
            "price": cur, "rsi": round(rsi5, 1),          # 5m RSI 표시
            "rsi3m": round(rsi3, 1) if rsi3 else None,    # 3m RSI 참고용
            "rsi1m": round(rsi1, 1),                       # 1m RSI 참고용
            "mom": round(mom5 * 100, 4), "bonus": bonus,
            "trend_up": trend_up, "trend_dn": trend_dn,
            "bull_score": bull_score, "bear_score": bear_score,
            "bb_pos": "below" if cur < bb_lower else ("above" if cur > bb_upper else "mid"),
            "bb_pos_ratio": round(bb_pos_ratio, 4),  # 0.0~1.0 수치 (Optimizer용)
            "bb_upper":     round(bb_upper, 6),       # BB 상단 절대값 (이탈 기준 탐색용)
            "bb_lower":     round(bb_lower, 6),       # BB 하단 절대값
            "bb_mid":       round(bb_mid, 6),         # BB 중간선 (기준가)
            "bb_width_pct": round(bb_width_pct * 100, 4),  # BB 폭 % (변동성 proxy)
            "adx_trend_thresh": ADX_TREND_THRESH,    # 분류 기준값 (재현성 보장)
            "adx_range_thresh": ADX_RANGE_THRESH,    # 기준값 바뀌어도 과거 재분류 가능
            "has_4h": e20_4h is not None, "has_1h": e20_1h is not None,
            "supertrend": st_dir,
            "adx": round(adx5, 1), "adx_regime": adx_regime,  # 5m ADX
            "funding_rate": round(funding_rate * 100, 6) if funding_rate is not None else None,  # % 단위
            "fr_signal": round(fr_signal * 100, 6) if fr_signal else None,
            "margin_mult": margin_mult,
            "adaptive_tp": round(adaptive_tp, 5),
            "adaptive_sl": round(adaptive_sl, 5),
            "atr_pct": round(atr_pct * 100, 4),
        }

    # 마켓별 캔들 캐시: {mid: {tf: (ts, candles)}}
    _candle_cache: Dict[int, Dict[str, tuple]] = {}
    _last_prices:  Dict[int, float]            = {}   # 마켓별 최신가 (신호 무관)
    _CANDLE_TTL = 12.0  # FIX: 4→12초 — 156마켓×8TF 동시 요청 타임아웃 방지

    async def scan_all(self, api: LighterAPI) -> List[Dict]:
        """
        v4.4 최적화:
        - run_in_executor 완전 제거 → aiohttp 직접 비동기 호출
        - 54마켓 × 7TF = 378 요청이 진짜 동시(asyncio) 실행
        - _candle_cache: TF별 (ts, data) 저장 → check_tpsl/rebalance 재사용
        - _last_prices: 모든 마켓(신호 여부 무관) 현재가 저장
        """
        async def get_tf(mid: int, tf: str, n: int) -> List[Dict]:
            now    = time.time()
            cached = self._candle_cache.get(mid, {}).get(tf)
            if cached and now - cached[0] < self._CANDLE_TTL:
                return cached[1]
            data = await api.get_candles_async(mid, tf, n)
            self._candle_cache.setdefault(mid, {})[tf] = (now, data)
            return data

        async def fetch(mkt: Dict) -> Optional[Dict]:
            sym = mkt["symbol"]
            # 정적 + 동적 블랙리스트 모두 체크
            if sym in BLACKLIST_SYMBOLS or DYNAMIC_BL.is_blocked(sym):
                return None
            mid = mkt["id"]
            try:
                c1m, c3m, c5m, c15m, c30s, c15s, c1h, c4h = await asyncio.gather(
                    get_tf(mid, "1m",  50),
                    get_tf(mid, "3m",  80),
                    get_tf(mid, "5m",  50),
                    get_tf(mid, "15m", 120),
                    get_tf(mid, "30s", 30),
                    get_tf(mid, "15s", 15),
                    get_tf(mid, "1h",  200),
                    get_tf(mid, "4h",  100),
                )
                # ── 캔들 신선도 경고 (차단하지 않음 — score=0으로 자연 필터) ───
                # WS Builder rate limit 복구까지 기존 캔들로 계속 분석
                # score=0이면 어차피 진입 차단 (최소 score 요구)
                _now_ms   = time.time() * 1000
                _c1m_ts   = c1m[-1].get("timestamp", 0) if c1m else 0
                if _c1m_ts > 0 and _now_ms - _c1m_ts > 30 * 60 * 1000:
                    _stale_age_m = int((_now_ms - _c1m_ts) / 60000)
                    log.debug(f"⚠️ {mkt['symbol']} 캔들 {_stale_age_m}분 stale (분석 계속)")

                # 현재가 캐시 — check_tpsl용
                # FIX: WS mark_price 우선 (캔들 close보다 정확) → 폴백: 캔들 close
                _ws_pos = WS_MANAGER._positions.get(str(mid), {})
                _mark = float(_ws_pos.get("mark_price") or 0)
                if _mark > 0:
                    self._last_prices[mid] = _mark
                elif c5m:
                    self._last_prices[mid] = float(c5m[-1]["close"])
                elif c1m:
                    self._last_prices[mid] = float(c1m[-1]["close"])
                sig = self.score(c1m, c3m, c5m, c15m or [], c30s or [], c15s or [],
                                 c1h or [], c4h or [],
                                 funding_rate=get_funding_rate(mid))
                if not sig:
                    # 신호 없어도 score_cache TOP20에 포함되도록 최소 정보 반환
                    cur = self._last_prices.get(mid, 0)
                    sig = {
                        "side": "LONG", "score": 0, "raw_score": 0, "bonus": 0,
                        "of_dir": "NEUTRAL", "of_imb": 0.5, "of_bonus": 0,
                        "score_with_of": 0,
                        "adx": 0, "adx_regime": "range", "rsi": 50,
                        "supertrend": "?", "price": cur,
                        "adaptive_tp": TP_PCT, "adaptive_sl": SL_PCT,
                        "atr_pct": 0, "bb_pos": "mid",
                        "bull_score": 0, "bear_score": 0,
                        "has_4h": False, "has_1h": False,
                    }
                if sig:
                    sig["symbol"]    = mkt["symbol"]
                    sig["market_id"] = mid
                    # ── OrderFlow 분석 통합 ──────────────────────────
                    # 기술 신호가 있는 마켓만 오더북 조회 (API 부하 최소화)
                    try:
                        of_result = await OF_ENGINE.analyze(mid)
                        sig = OF_ENGINE.apply_to_signal(sig, of_result)
                        # ── Spread 반영 Adaptive TP 보정 ──────────────
                        # 실시간 spread를 TP에 반영: TP ≥ spread × 2.5 보장
                        # (수수료+슬리피지 커버, 최대 1.5% 클램프 유지)
                        # adaptive TP/SL 하한선: 가격 기준 고정값 (레버리지 미반영)
                        sig["adaptive_tp"] = max(sig.get("adaptive_tp", TP_PCT), TP_PCT)
                        sig["adaptive_sl"] = max(sig.get("adaptive_sl", SL_PCT), SL_PCT)
                        real_spread = of_result.get("spread_pct", 0.0)
                        if real_spread > 0:
                            spread_min_tp = real_spread * 2.5
                            old_tp = sig.get("adaptive_tp", TP_PCT)
                            new_tp = min(max(old_tp, spread_min_tp, TP_PCT), ADAPTIVE_TP_MAX)
                            if new_tp != old_tp:
                                sig["adaptive_tp"] = round(new_tp, 5)
                                log.debug(f"Spread TP {sym}: {old_tp*100:.3f}%→{new_tp*100:.3f}% (spread={real_spread*100:.4f}%)")
                    except Exception as of_err:
                        log.debug(f"OF skip {mkt['symbol']}: {of_err}")
                        sig["score_with_of"] = sig["score"]
                        sig["of_bonus"]      = 0
                        sig["of_dir"]        = "NEUTRAL"
                        sig["of_conflict"]   = False
                    return sig
            except Exception as e:
                log.debug(f"Scan err {mkt['symbol']}: {e}")
            return None

        # 전체 마켓 동시 스캔 (156개 전체 — 외환/주식/원자재 포함)
        all_markets = [{"id": mid, "symbol": MID_TO_SYMBOL.get(mid, f"mid{mid}"), "leverage": lev}
                       for mid, lev in MARKET_LEVERAGE.items()]
        results = await asyncio.gather(*[fetch(m) for m in all_markets])
        # 중복 제거: market_id 기준 (gather 결과에 같은 마켓이 두 번 올 수 없지만 방어)
        seen_mids = set()
        signals   = []
        for r in sorted([r for r in results if r], key=lambda x: x["score"], reverse=True):
            if r["market_id"] not in seen_mids:
                seen_mids.add(r["market_id"])
                signals.append(r)
        return signals

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PositionManager v4 (Atomic Close + Smart Rebal)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class PositionManager:
    def __init__(self, api: LighterAPI, learner: SelfLearner, engine=None):
        self.api      = api
        self.learner  = learner
        self.engine   = engine  # SignalEngine 참조 (캐시 접근용)
        # _pos key = f"{market_id}_{side}" (예: "1_LONG")
        self._pos:     Dict[str, Dict]   = {}
        self._closing: set               = set() # 청산 진행 중 (중복방지)
        self._closing_ts: Dict[str, float] = {}  # _closing 등록 시각 (60초 후 자동 해제)
        self._cooldown: Dict[int, float] = {}
        self._startup_sync_done: bool    = False  # 재시작 후 첫 sync 완료 여부 (EXT/REBAL 차단용)
        self._startup_entry_allowed: bool = False # 재시작 후 신규 진입 허용 여부
        # WHY: 재시작 직후 포지션 sync 전 진입 → 중복 주문 / 의도치 않은 포지션 생성 가능
        #      첫 sync 완료 → 거래소 포지션 확인 완료 → 그 이후부터만 신규 진입 허용
        # 근본 수정: 청산 성공한 (mid, entry) → orphan 재복구 완전 차단
        # KEY: {mid_int}_{side}, VALUE: set of entry_prices closed today
        # closed_entries.json 전용 파일에서 복원 (pos_state.json race condition 방지)
        self._closed_entries: Dict[str, Dict[float, float]] = {}  # {key: {entry: expire_ts}}
        self._released_this_loop: set = set()  # PROB-7: 이번 sync에서 해제된 key
        self._load_closed_entries()  # 별도 파일에서 복원

        # ── strategy meta: 재시작 후 복원을 위한 단일 저장소 ────────
        # WHY: _pos는 sync()마다 WS/acc 기반으로 재구성됨 → score 등 전략 메타가 사라짐
        #      _strategy_meta는 진입 시 설정 → 청산 확정 시 제거 (파일과 1:1 매핑)
        # KEY: "mid_side" (예: "24_LONG")
        # VALUE: {"score", "entry_ts", "entry_price", "peak_pnl", "add_count",
        #         "adaptive_tp", "adaptive_sl", "rsi", "adx", ...}
        self._strategy_meta: Dict[str, Dict] = {}
        self._meta_dirty: bool = False  # _strategy_meta 변경됨 → 다음 저장 주기에 flush
        self._close_lock: asyncio.Lock   = asyncio.Lock()  # 청산 중복 주문 방지 (async race condition)
        self._close_tasks: Dict[str, asyncio.Task] = {}   # key → 진행 중인 close 태스크 (EXT 감지 시 취소용)
        # ── 중복 주문 방지 상태 ──────────────────────────────────────
        # _pending_order: 이번 루프에서 이미 주문 실행된 마켓 (루프 내 중복 차단)
        #   → 매 루프 시작 시 초기화
        # _add_signal_armed: 포지션 있는 마켓에서 "추가 신호 1회" 대기 상태
        #   key="mid_side" → True(추가 신호 확인됨, 다음 루프에 추가 주문 가능)
        self._pending_order: set              = set()        # mid (루프 내 중복 차단)
        # armed: 추가 신호 1회 감지됨 (다음 루프에서 추가 진입 가능)
        # armed 진입 후엔 해당 마켓에 ADD_COOLDOWN 쿨다운 → 반복 방지
        self._add_signal_armed: Dict[str,bool]  = {}         # "mid_side" → True=대기중
        self._add_cooldown: Dict[str, float]    = {}         # "mid_side" → 추가진입 쿨타임
        # 주문 직후 score 임시 보관 (체인 반영 전 sync에서 복원)
        # key -> {"score": int, "ts": float}
        self._order_scores: Dict[str, Dict[str, float]] = {}

        # 재시작 후 오늘자 거래 통계 복원
        # FIX: EXT/REBAL/SC0_CLEANUP 제외, pnl=0 제외, 중복 제거 후 카운트
        _today = datetime.now().strftime("%Y-%m-%d")
        _tp0 = _sl0 = 0
        try:
            _seen_rec = set()
            for _t in json.load(open(TRADE_HIST_FILE)):
                if _t.get("ts", "")[:10] != _today: continue
                if _t.get("score", 0) <= 0: continue
                _et = _t.get("exit_type", "")
                if _et in ("EXT", "REBAL", "SC0_CLEANUP", "LIQ_DANGER"): continue
                if _t.get("pnl") in (0.0, None): continue
                # 중복 제거
                _rk = (_t.get("symbol"), round(_t.get("pnl",0),6), _et)
                if _rk in _seen_rec: continue
                _seen_rec.add(_rk)
                if _et == "TP" or _et.startswith("TSL"): _tp0 += 1
                elif _et == "SL": _sl0 += 1
        except: pass
        self.stats = {"tp": _tp0, "sl": _sl0, "opened":0, "rebalanced":0}

        # ── 재시작 후 _strategy_meta + _pos 복원 ─────────────────────────
        # 신규 구조:
        #   _strategy_meta = pos_state에서 meta(score/entry_ts/peak 등)만 로드
        #   _pos는 sync()에서 WS 기반으로 재구성 → meta를 _strategy_meta에서 병합
        # WHY: pos_state에 거래소 데이터까지 저장 → 불일치 가능
        #      _strategy_meta만 저장 → WS가 거래소 데이터 담당 (분리 원칙)
        # 구버전 호환: pos_state에 market_id/side 있으면 _strategy_meta로 마이그레이션
        try:
            _ps = self._load_pos_state()
            _restored = 0
            for _k, _v in _ps.items():
                if _k.startswith("__"):  # 메타 키 제외
                    continue
                _mid = _v.get("market_id")
                _side = _v.get("side")
                _entry = _v.get("entry_price", 0)
                if not _mid or not _side or not _entry:
                    continue
                # closed_entries에 이미 있으면 복원 안 함 (오늘 이미 청산됨)
                # WHY 예외: WS에 해당 마켓 포지션이 실제로 살아있으면 복원 허용
                _ce_map = self._closed_entries.get(_k, {})
                _entry_r = round(float(_entry), 5)
                if _entry_r in _ce_map and _ce_map[_entry_r] > time.time():
                    _ws_sz_restore = abs(float(WS_MANAGER._positions.get(str(_mid), {}).get("position", 0)))
                    if _ws_sz_restore > 1e-9:
                        log.info(f"♻️ pos_state 복원: {_v.get('symbol')} {_side} (closed_entries 차단이나 WS size={_ws_sz_restore:.4g} → 복원 허용)")
                        self._closed_entries[_k].pop(_entry_r, None)
                        self._save_closed_entries()
                    else:
                        log.info(f"♻️ pos_state 복원 스킵: {_v.get('symbol')} {_side} (closed_entries 차단, WS size=0)")
                        continue
                # _strategy_meta에 전략 메타만 저장 (거래소 데이터 제외)
                self._strategy_meta[_k] = {
                    "score":        _v.get("score", 0),
                    "entry_price":  _entry,
                    "entry_ts":     _v.get("entry_ts"),
                    "entry_hour_kst": _v.get("entry_hour_kst"),
                    "opened_at":    _v.get("opened_at", time.time()),
                    "peak_pnl":     _v.get("peak_pnl", 0.0),
                    "add_count":    _v.get("add_count", 0),
                    "adaptive_tp":  _v.get("adaptive_tp", TP_PCT),
                    "adaptive_sl":  _v.get("adaptive_sl", SL_PCT),
                    "rsi":          _v.get("rsi"),
                    "adx":          _v.get("adx"),
                    "adx_regime":   _v.get("adx_regime"),
                    "supertrend":   _v.get("supertrend"),
                    "bb_pos":       _v.get("bb_pos"),
                    "of_dir":       _v.get("of_dir"),
                    "of_bonus":     _v.get("of_bonus"),
                    "of_imb":       _v.get("of_imb"),
                    "leverage":     _v.get("leverage"),
                }
                # _pos도 복원 (EXT 감지 목적: 첫 sync에서 거래소와 비교)
                self._pos[_k] = dict(_v)
                self._pos[_k]["_restored_from_state"] = True
                _restored += 1
            if _restored:
                log.info(f"♻️ pos_state → _strategy_meta/_pos 복원: {_restored}개")
        except Exception as _e_restore:
            log.warning(f"pos_state 복원 실패: {_e_restore}")

    def _key(self, mid: int, side: str) -> str:
        return f"{mid}_{side}"

    def has(self, mid: int, side: str) -> bool:
        return self._key(mid, side) in self._pos

    def get_position(self, mid: int) -> Optional[Dict]:
        """마켓의 현재 포지션 반환 (없으면 None)"""
        for p in self._pos.values():
            if p["market_id"] == mid:
                return p
        return None

    def in_cooldown(self, mid: int) -> bool:
        return time.time() < self._cooldown.get(mid, 0)

    def set_cooldown(self, mid: int):
        self._cooldown[mid] = time.time() + COOLDOWN_SEC

    def clear_loop_pending(self):
        """루프 시작 시 루프 내 중복 방지 세트 초기화 — 단, ORDER_MIN_GAP 이내 주문은 유지"""
        # _pending_order는 cooldown 기반으로 관리 → 루프마다 clear 하지 않음
        # (clear는 _open_signals 시작 시에만 호출 — 루프당 1회 보장)
        self._pending_order.clear()

    def is_add_cooldown(self, mid: int, side: str) -> bool:
        return time.time() < self._add_cooldown.get(self._key(mid, side), 0)

    def set_add_cooldown(self, mid: int, side: str, secs: float = 120):
        """추가 진입 후 쿨다운 (기본 120초 — 같은 방향 연속 추가 방지)"""
        self._add_cooldown[self._key(mid, side)] = time.time() + secs

    def reset_add_signal(self, mid: int):
        """청산 후 추가 신호 대기 상태 + 추가 쿨다운 초기화"""
        for side in ("LONG", "SHORT"):
            self._add_signal_armed.pop(self._key(mid, side), None)
            self._add_cooldown.pop(self._key(mid, side), None)

    def _save_pos_state(self):
        """v8: 역할 분리 — pos_state = strategy meta만, ws_positions = WS 실시간 스냅샷

        설계 원칙:
        1. pos_state_v4.json: _strategy_meta만 저장 (score/entry_ts/peak/of_dir 등)
           - 거래소 데이터(size/pnl/pos_value) 제외 → WS가 담당
           - _meta_dirty일 때만 flush → 불필요한 I/O 최소화
        2. ws_positions.json: WS_MANAGER._positions 실시간 스냅샷
           - 항상 최신 (WS 수신 기반)
           - 계좌 정보(__equity__ 등) 포함
        3. 중복 마켓 방어: WS 기준으로 wrong-direction 키 제거
        4. 파일 I/O는 이 함수에서만 (EXT/atomic_close 내 직접 접근 금지)
        """
        try:
            # ── 1. ws_positions.json: WS 실시간 스냅샷 (항상 쓰기) ────
            try:
                ws_snap = {
                    "__ts__":           time.time(),
                    "__equity__":       round(WS_MANAGER._ws_portfolio, 4),
                    "__collateral__":   round(WS_MANAGER._ws_collateral, 4),
                    "__available__":    round(WS_MANAGER._ws_available, 4),
                    "__margin_usage__": round(WS_MANAGER._ws_margin_usage, 2),
                    "__upnl__":         round(WS_MANAGER._ws_portfolio - WS_MANAGER._ws_collateral, 4),
                }
                for mid_str, p in WS_MANAGER._positions.items():
                    sz = float(p.get("position", 0) or 0)
                    if abs(sz) < 1e-9:
                        continue
                    pval = abs(float(p.get("position_value", 0) or 0))
                    mark = pval / abs(sz) if abs(sz) > 1e-9 else 0
                    liq_r = str(p.get("liquidation_price", "") or "").strip()
                    liq_from_rest = p.get("_liq_from_rest", False)
                    # sign=1 → LONG, sign=-1 → SHORT (position 필드는 항상 양수)
                    side_ws = "LONG" if int(p.get("sign", 1)) > 0 else "SHORT"
                    ws_snap[mid_str] = {
                        "market_id":        int(mid_str) if mid_str.isdigit() else mid_str,
                        "symbol":           p.get("symbol", ""),
                        "side":             side_ws,
                        "size":             abs(sz),
                        "position_value":   pval,
                        "mark_price":       round(mark, 8) if mark > 0 else 0,
                        "unrealized_pnl":   float(p.get("unrealized_pnl", 0) or 0),
                        "liquidation_price": liq_r,
                        "liq_from_rest":    liq_from_rest,
                        "initial_margin_fraction": float(p.get("initial_margin_fraction", 0) or 0),
                    }
                with open("logs/ws_positions.json", "w") as f:
                    json.dump(ws_snap, f)
            except Exception as _e:
                log.debug(f"ws_positions.json 저장 실패: {_e}")

            # ── 2. pos_state_v4.json: _strategy_meta만 저장 ──────────
            # _meta_dirty일 때만 flush + _pos sync 시 (새 포지션 감지)
            # WHY: 불필요한 파일 I/O 최소화 → rate limit 없는 디스크지만 블로킹 방지
            try:
                # 중복 마켓 방어: WS 기준으로 wrong-direction 키를 _strategy_meta에서 제거
                mid_seen: Dict[int, str] = {}
                for k in list(self._strategy_meta.keys()):
                    parts = k.split("_")
                    mid_k2 = int(parts[0]) if parts[0].isdigit() else 0
                    if mid_k2 == 0:
                        continue
                    ws_p2   = WS_MANAGER._positions.get(str(mid_k2), {})
                    ws_sz2  = float(ws_p2.get("position", 0) or 0)
                    if abs(ws_sz2) > 1e-9:
                        ws_side2 = "LONG" if int(ws_p2.get("sign", 1)) > 0 else "SHORT"
                        if mid_k2 in mid_seen and mid_seen[mid_k2] != ws_side2:
                            wrong_key = f"{mid_k2}_{mid_seen[mid_k2]}"
                            if wrong_key in self._strategy_meta:
                                log.warning(f"⚠️ 중복 마켓 감지: {wrong_key} meta 제거")
                                del self._strategy_meta[wrong_key]
                                self._meta_dirty = True
                        mid_seen[mid_k2] = ws_side2

                # _strategy_meta에서 WS에 없는 키(청산됨) 정리
                # 단, _closing 중인 것은 유지 (score 복원 위해 60초 유지)
                for k in list(self._strategy_meta.keys()):
                    mid_str_k = k.split("_")[0]
                    ws_sz_k = abs(float(WS_MANAGER._positions.get(mid_str_k, {}).get("position", 0)))
                    if ws_sz_k < 1e-9 and k not in self._closing:
                        # WS에 포지션 없고 청산 중도 아님 → 이미 청산됨 → meta 제거
                        del self._strategy_meta[k]
                        self._meta_dirty = True

                if self._meta_dirty:
                    # 기존 pos_state 로드 (opened_at/entry_ts 보존용)
                    # WHY: 외부진입 포지션은 opened_at=time.now()로 등록되지만
                    #      이미 파일에 더 이른 시각이 있으면 그 값을 유지 (재시작 후 hold 리셋 방지)
                    _prev_state = {}
                    try:
                        with open(POS_STATE_FILE) as _psf:
                            _prev_state = json.load(_psf)
                    except:
                        pass

                    state = {
                        "__ws_equity__":       round(WS_MANAGER._ws_portfolio, 4),
                        "__ws_collateral__":   round(WS_MANAGER._ws_collateral, 4),
                        "__ws_available__":    round(WS_MANAGER._ws_available, 4),
                        "__ws_margin_usage__": round(WS_MANAGER._ws_margin_usage, 2),
                        "__last_collateral__": round(WS_MANAGER._ws_collateral, 4),
                        "__ws_ts__":           time.time(),
                    }
                    for k, m in self._strategy_meta.items():
                        # _pos에서 현재 entry_price 동기화 (진입 직후 갱신)
                        pos_entry = self._pos.get(k, {}).get("entry_price")
                        if pos_entry:
                            m["entry_price"] = pos_entry
                        # None 값 제거
                        state[k] = {kk: vv for kk, vv in m.items() if vv is not None}
                        # opened_at/entry_ts: 기존 파일 값이 더 이르면 보존 (hold 리셋 방지)
                        _prev_k = _prev_state.get(k, {})
                        for _ts_field in ("opened_at", "entry_ts"):
                            _prev_ts = _prev_k.get(_ts_field, 0) or 0
                            _curr_ts = state[k].get(_ts_field, 0) or 0
                            if _prev_ts > 0 and (_curr_ts <= 0 or _prev_ts < _curr_ts):
                                state[k][_ts_field] = _prev_ts  # 더 이른 시각 유지
                    # market_id/side/symbol 추가 (재시작 복원용 최소 식별자)
                    for k, v in self._pos.items():
                        if k in state:
                            state[k]["market_id"] = v.get("market_id")
                            state[k]["side"]      = v.get("side")
                            # symbol: _pos → _strategy_meta → WS_MANAGER 순 fallback
                            _sym = (v.get("symbol")
                                    or self._strategy_meta.get(k, {}).get("symbol")
                                    or WS_MANAGER._positions.get(k.split("_")[0], {}).get("symbol"))
                            state[k]["symbol"] = _sym
                    with open(POS_STATE_FILE, "w") as f:
                        json.dump(state, f)
                    self._meta_dirty = False
            except Exception as _e2:
                log.warning(f"pos_state 저장 실패: {_e2}")

            # closed_entries는 별도 파일에 저장 (race condition 방지)
            self._save_closed_entries()
        except Exception as _e_top:
            log.warning(f"_save_pos_state 오류: {_e_top}")

    def _load_pos_state(self) -> Dict:
        try:
            with open(POS_STATE_FILE) as f:
                return json.load(f)
        except:
            return {}

    def _load_closed_entries(self):
        """closed_entries.json 전용 파일에서 복원 (race condition 방지)"""
        try:
            with open(CLOSED_ENTRIES_FILE) as f:
                raw = json.load(f)
            today = datetime.now().strftime("%Y-%m-%d")
            if raw.get("date", "") != today:
                return  # 어제 것은 무시
            now = time.time()
            for k, v in raw.get("entries", {}).items():
                if isinstance(v, dict):
                    valid = {float(entry): float(exp) for entry, exp in v.items() if float(exp) > now}
                    if valid:
                        self._closed_entries[k] = valid
        except:
            pass
        if self._closed_entries:
            log.info(f"🔒 closed_entries 복원: {list(self._closed_entries.keys())}")

    def _save_closed_entries(self):
        """closed_entries.json 전용 파일에 저장 (원자적, pos_state.json과 분리)"""
        try:
            data = {
                "date":    datetime.now().strftime("%Y-%m-%d"),
                "entries": {k: {str(entry): exp for entry, exp in v.items()}
                            for k, v in self._closed_entries.items() if v}
            }
            with open(CLOSED_ENTRIES_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            log.warning(f"closed_entries 저장 실패: {e}")

    async def sync(self, acc: Optional[Dict] = None):
        """
        Exchange 포지션 → 로컬 동기화
        ─────────────────────────────────────────
        규칙:
        - _closing 중인 포지션만 제외 (청산 중)
        - 매 루프 account() 기반으로 완전 재구성
        """
        if acc is None:
            acc = await self.api.account()
        # 재시작 후 score 복원 (파일에서) — 항상 로드하여 race condition 방지
        # v8: _strategy_meta 사용 → _load_pos_state() 호출 제거 (sync() 내 I/O 없음)
        new_pos: Dict[str, Dict] = {}
        self._released_this_loop = set()  # PROB-7: 이번 sync 루프 _closing 해제 key 초기화
        now_t = time.time()
        # _order_scores TTL(60s) 초과 정리 → 120s 쿨다운 적용
        for k, v in list(self._order_scores.items()):
            if now_t - v.get("ts", 0) > 60.0:
                try:
                    mid_exp = int(k.split("_")[0])
                except:
                    mid_exp = 0
                if mid_exp:
                    log.warning(f"⚠️ order score TTL 만료: {k} → 120s 쿨다운 적용")
                    self._cooldown[mid_exp] = time.time() + EXT_COOLDOWN_SEC
                self._order_scores.pop(k, None)
        for pos in acc["positions"]:
            # size=0 or entry=0인 포지션은 무효 → 스킵 (잘못된 포지션 등록 방지)
            _sz  = float(pos.get("size") or pos.get("position") or 0)
            _ep  = float(pos.get("entry_price") or pos.get("avg_entry_price") or 0)
            if abs(_sz) < 1e-9 or _ep <= 0:
                continue
            # side 정규화: "LONG"/"SHORT"만 허용
            _raw_side = str(pos.get("side", "")).upper()
            if _raw_side not in ("LONG", "SHORT"):
                continue
            pos["side"] = _raw_side  # 정규화 적용

            mid = pos["market_id"]
            k   = self._key(mid, pos["side"])

            # 청산 중인 포지션은 new_pos에서 제외 (재SL 방지)
            if k in self._closing:
                continue

            # 오늘 이미 청산된 entry와 동일하면 복구 차단 (_closed_entries 체크)
            # WHY: REST acc가 청산 직후에도 포지션을 반환 → sync에서 재등록 → orphan 반복
            _pos_entry_ce = round(float(pos.get("entry_price") or pos.get("avg_entry_price") or 0), 5)
            _ce_map = self._closed_entries.get(k, {})
            _ce_expire = _ce_map.get(_pos_entry_ce, 0)
            if _pos_entry_ce > 0 and _ce_expire > time.time():
                # WS에 여전히 포지션이 있으면 → 청산 실패 → 차단 해제, 재등록하여 재청산
                _ws_sz_ce = abs(float(WS_MANAGER._positions.get(str(mid), {}).get("position", 0)))
                if _ws_sz_ce > 1e-9:
                    log.warning(f"⚠️ sync: {k} entry={_pos_entry_ce} closed_entries 있지만 WS size={_ws_sz_ce:.4g} → 재등록 (재청산 시도)")
                    # _closed_entries에서 제거 → 재등록 허용
                    self._closed_entries.get(k, {}).pop(_pos_entry_ce, None)
                    self._save_closed_entries()
                    # fall through → new_pos에 등록
                else:
                    log.info(f"✅ sync: {k} entry={_pos_entry_ce} → 청산 차단 중 (TTL {int(_ce_expire-time.time())}s)")
                    continue

            prev  = self._pos.get(k, {})
            # ── _strategy_meta = 단일 전략 메타 원천 ──────────────────
            # 우선순위: _order_scores(신규 주문) > _strategy_meta(기존/재시작) > prev(루프 내 연속성)
            # WHY: _saved_state(파일) 읽기 제거 → sync() 내 불필요한 I/O 없음
            meta  = self._strategy_meta.get(k, {})
            pval  = pos.get("position_value", 0) or 0.0
            lev   = pos.get("leverage") or MARKET_LEVERAGE.get(mid, 10)

            # _order_scores: 신규 주문 직후 score 임시 보관 (1회 소비)
            order_sc    = 0
            order_meta  = {}
            order_entry = self._order_scores.pop(k, None)
            if order_entry:
                order_sc = int(order_entry.get("score", 0))
                for _mk in TRADE_META_FIELDS + ("entry_ts", "adaptive_tp", "adaptive_sl", "peak_pnl"):
                    if order_entry.get(_mk) is not None:
                        order_meta[_mk] = order_entry[_mk]
                # _order_scores → _strategy_meta로 승격 (이후 sync에서도 유지)
                if order_sc > 0 and k not in self._strategy_meta:
                    self._strategy_meta[k] = dict(order_meta)
                    self._strategy_meta[k]["score"] = order_sc
                    self._meta_dirty = True

            # score 복원: _order_scores > _strategy_meta > prev(루프연속) > min_score
            score = order_sc or meta.get("score") or prev.get("score") or 0
            sym_name = prev.get("symbol") or meta.get("symbol") or MID_TO_SYMBOL.get(mid, pos.get("symbol", f"mid{mid}"))
            if score == 0:
                score = self.learner.min_score
                log.warning(f"⚠️ sync: {sym_name} {pos['side']} score=0 → min_score({score})로 복원하여 TP/SL 관리")
                # WHY: _strategy_meta에 없으면 매 루프마다 score=0 재발 → _strategy_meta에 즉시 등록
                # 봇 외부 진입 포지션(수동/이전봇)도 TP/SL 관리되도록 보장
                if k not in self._strategy_meta:
                    self._strategy_meta[k] = {
                        "score":      score,
                        "entry_price": (pos.get("entry_price") or pos.get("avg_entry_price") or 0),
                        "entry_ts":   time.time(),
                        "opened_at":  time.time(),
                        "peak_pnl":   0.0,
                        "add_count":  0,
                        "adaptive_tp": TP_PCT,
                        "adaptive_sl": SL_PCT,
                        "symbol":     sym_name,
                        "side":       pos["side"],
                        "market_id":  mid,
                        "leverage":   MARKET_LEVERAGE.get(mid, 10),
                        "note":       "외부진입/재시작후_score복원",
                    }
                    self._meta_dirty = True
                    log.info(f"📝 _strategy_meta 등록 (외부진입): {sym_name} {pos['side']} sc={score}")

            # ── liq_price: WS주입값 > acc값 > prev ────────────────────
            _liq = (pos.get("liquidation_price") or 0)
            if not _liq and prev.get("liq_price"):
                _liq = prev["liq_price"]

            # ── margin: imf 기반 계산 (pval/lev보다 정확) ──────────────
            imf_k  = pos.get("initial_margin_fraction") or 0
            margin = abs(pval) * (imf_k / 100) if imf_k > 0 else (abs(pval) / lev if lev > 0 else 0)

            # ── entry_price: 거래소(acc/WS) > order_meta > meta(재시작) > prev ──
            _ep_new = (pos.get("entry_price") or pos.get("avg_entry_price") or
                       order_meta.get("entry_price") or meta.get("entry_price") or
                       prev.get("entry_price") or 0)

            # ── peak_pnl / add_count: None 체크로 복원 (0이 유효값) ────
            # 우선순위: prev(루프연속) > meta(재시작/파일) > 기본값
            _peak    = (prev["peak_pnl"] if "peak_pnl" in prev
                        else meta.get("peak_pnl") if meta.get("peak_pnl") is not None
                        else 0.0)
            _add_cnt = (prev["add_count"] if "add_count" in prev
                        else meta.get("add_count") if meta.get("add_count") is not None
                        else 0)

            new_pos[k] = {
                # ── 거래소 기준 필드 (WS/acc에서 직접) ─────────────────
                "market_id":  mid,
                "symbol":     sym_name,
                "side":       pos["side"],
                "size":       pos["size"],
                "entry_price": _ep_new,
                "liq_price":  _liq,
                "pos_value":  pval,
                "margin":     margin,
                # ── 전략 상태: prev(루프연속) > meta(재시작) > 기본값 ──
                "score":      score,
                "opened_at":  prev.get("opened_at") or meta.get("opened_at") or time.time(),
                "add_count":  _add_cnt,
                "peak_pnl":   _peak,
                "adaptive_tp": prev.get("adaptive_tp") or order_meta.get("adaptive_tp") or meta.get("adaptive_tp") or TP_PCT,
                "adaptive_sl": prev.get("adaptive_sl") or order_meta.get("adaptive_sl") or meta.get("adaptive_sl") or SL_PCT,
                # ── 진입 메타: prev(루프연속) > order_meta(신규) > meta(재시작) ──
                "entry_ts":   prev.get("entry_ts") or order_meta.get("entry_ts") or meta.get("entry_ts") or time.time(),
                "entry_hour_kst": prev.get("entry_hour_kst") or order_meta.get("entry_hour_kst") or meta.get("entry_hour_kst"),
                "rsi":        prev.get("rsi")        or order_meta.get("rsi")        or meta.get("rsi"),
                "adx":        prev.get("adx")        or order_meta.get("adx")        or meta.get("adx"),
                "adx_regime": prev.get("adx_regime") or order_meta.get("adx_regime") or meta.get("adx_regime"),
                "supertrend": prev.get("supertrend") or order_meta.get("supertrend") or meta.get("supertrend"),
                "bb_pos":     prev.get("bb_pos")     or order_meta.get("bb_pos")     or meta.get("bb_pos"),
                "of_dir":     prev.get("of_dir")     or order_meta.get("of_dir")     or meta.get("of_dir") or "UNKNOWN",
                "of_bonus":   prev.get("of_bonus")   or order_meta.get("of_bonus")   or meta.get("of_bonus") or 0,
                "of_imb":     prev.get("of_imb")     or order_meta.get("of_imb")     or meta.get("of_imb") or 0.5,
            }
            # ── peak_pnl 루프 업데이트 → _strategy_meta에도 반영 ─────
            # sync()에서 peak_pnl을 업데이트하는 코드가 _pos에만 쓰이므로
            # _strategy_meta도 동기화 (flush 주기에 파일 저장)
            if k in self._strategy_meta:
                self._strategy_meta[k]["peak_pnl"] = _peak
                self._strategy_meta[k]["score"] = score  # score도 동기화

        # ── _closing 해제: WS position=0 확인 기반 (근본 해결 v2) ─────────
        # 원칙: WS(거래소) 포지션이 진실.
        #   WS size=0 → 체결 완료 → _closing 해제
        #   WS size>0 → 아직 포지션 있음 → _closing 유지 (절대 강제 해제 안 함)
        #
        # WHY 강제 해제 제거: 60초 후 강제 해제 → WS size>0 → sync에서 재등록 → SL 루프
        # (HYPE 사례 2026-03-14: SL 15회 반복, -$261 손실)
        #
        # 대신: 주기적으로 force_close 재시도 (별도 _retry_close_loop에서 처리)
        _now_t = time.time()
        for k in self._closing:
            self._closing_ts.setdefault(k, _now_t)

        to_release = set()
        for k in list(self._closing):
            mid_str_k = k.split("_")[0]
            ws_pos_k  = WS_MANAGER._positions.get(mid_str_k, {})
            ws_size_k = abs(float(ws_pos_k.get("position", 0)))
            elapsed   = _now_t - self._closing_ts.get(k, _now_t)

            if ws_size_k < 1e-9:
                # WS position=0 → 거래소 체결 완료 → 해제
                to_release.add(k)
                log.debug(f"_closing {k} WS=0 확인 → 해제")
                # closed_entries 등록 (아직 미등록인 경우)
                _ws_ep_rel = round(float(WS_MANAGER._positions.get(mid_str_k, {}).get("avg_entry_price", 0) or
                                         self.pm._strategy_meta.get(k, {}).get("entry_price", 0) or 0), 5)
                if _ws_ep_rel > 0:
                    if k not in self._closed_entries:
                        self._closed_entries[k] = {}
                    self._closed_entries[k][_ws_ep_rel] = time.time() + 86400
                    self._save_closed_entries()
            else:
                # WS size>0 → 거래소에 아직 포지션 있음 → _closing 유지
                # 30초마다 경고 로그 (무한 대기 방지 목적 모니터링)
                if elapsed > 30 and int(elapsed) % 30 < 5:
                    log.warning(f"⏳ _closing {k} {elapsed:.0f}s 대기 중 (WS size={ws_size_k:.4g}) — 거래소 체결 대기")

        self._closing -= to_release
        self._closing_ts = {k: v for k, v in self._closing_ts.items() if k in self._closing}
        # PROB-7: 이번 루프에서 해제된 key 기록 → orphan 복구 차단
        self._released_this_loop = to_release
        # ── 외부 청산 감지: 기존 포지션이 사라졌는데 _closing에 없으면 → 기록 ──
        for k, old_pos in self._pos.items():
            # 체인 반영 대기 중인 주문 key는 EXT 처리 제외
            if k in self._order_scores:
                continue
            if k not in new_pos and k not in self._closing:
                # 진입 후 5초 이내는 체인 반영 지연일 수 있음 → 무시
                opened_at = old_pos.get("opened_at") or 0
                if time.time() - opened_at < 5.0:
                    new_pos[k] = old_pos   # 잠시 유지
                    continue
                # WS 준비 안 됐거나 첫 sync 전이면 EXT 처리 완전 차단
                if (not WS_MANAGER.is_ready() or not self._startup_sync_done):
                    new_pos[k] = old_pos
                    continue
                # acc 포지션 0개면 WS/REST 불일치 가능 → 보류
                if len(acc.get("positions", [])) == 0:
                    new_pos[k] = old_pos
                    continue
                # WS _positions에 해당 포지션이 있으면 EXT 보류 (체인 반영 지연)
                _mid_check = old_pos.get("market_id", 0)
                _ws_size_check = abs(float(WS_MANAGER._positions.get(str(_mid_check), {}).get("position", 0)))
                if _ws_size_check > 1e-9:
                    new_pos[k] = old_pos   # WS에 포지션 있음 → acc 불일치, 보류
                    continue
                # 외부 청산 (수동, 청산 등) — pnl 알 수 없으므로 "EXT" 타입으로 기록
                sym   = old_pos.get("symbol", k.split("_")[0])
                side  = old_pos.get("side", "LONG")
                entry = old_pos.get("entry_price", 0)
                score = old_pos.get("score", 0)
                cur_p = self.engine._last_prices.get(old_pos.get("market_id"), entry) if self.engine else entry
                pnl   = (cur_p - entry) / entry if side == "LONG" else (entry - cur_p) / entry
                log.info(f"⚡ EXT 청산 감지: {sym} {side} pnl={pnl*100:+.3f}% → 쿨다운 120s")
                # EXT 거래도 trade_history에 기록 (pnl은 추정치 — cur_p 기준)
                try:
                    entry_ts = old_pos.get("entry_ts")
                    hold_secs = int(time.time() - entry_ts) if entry_ts else None
                    _ext_meta = {k2: old_pos[k2] for k2 in ("rsi","adx","adx_regime","supertrend",
                                                              "bb_pos","of_dir","of_bonus","of_imb",
                                                              "entry_hour_kst") if k2 in old_pos}
                    if hold_secs is not None:
                        _ext_meta["hold_secs"] = hold_secs
                    _ext_meta["leverage"]    = old_pos.get("leverage") or MARKET_LEVERAGE.get(old_pos.get("market_id",0), 10)
                    _ext_meta["adaptive_tp"] = old_pos.get("adaptive_tp", TP_PCT)
                    _ext_meta["adaptive_sl"] = old_pos.get("adaptive_sl", SL_PCT)
                    _ext_meta["peak_pnl"]    = old_pos.get("peak_pnl", 0.0)
                    _ext_meta["entry_price"] = entry
                    _ext_meta["params_ver"]  = _get_params_ver()
                    _ext_meta["note"]        = "EXT: cur_p 추정치"
                    _ext_pnl_usd = pnl * entry * old_pos.get("size", 0) * _ext_meta["leverage"]
                    self.learner.record(sym, side, entry, cur_p, score, pnl, "EXT", _ext_pnl_usd,
                                       meta=_ext_meta)
                except Exception as _e:
                    log.warning(f"⚠️ EXT record 실패: {_e}")
                # 수동/외부 청산 후 재진입 방지: 120초 쿨다운
                mid_ext = old_pos.get("market_id", 0)
                self._cooldown[mid_ext] = time.time() + EXT_COOLDOWN_SEC
                # EXT 확정 후 _closing에 등록 → orphan 복구 루프 차단
                self._closing.add(k)
                self._closing_ts[k] = time.time()
                # EXT 감지 시 진행 중인 force_close 태스크 즉시 취소
                _ext_task = self._close_tasks.pop(k, None)
                if _ext_task and not _ext_task.done():
                    _ext_task.cancel()
                    log.info(f"✂️ EXT 감지 → force_close 태스크 취소: {sym} {side}")
                # EXT 시 _closed_entries에도 등록 → 재등록 차단 (24시간)
                # WHY WS 교차검증: EXT 감지는 acc 기준 — WS에 포지션 있으면 오판일 수 있음
                #   WS에 실제로 없을 때만 등록 (방향전환 재진입 차단 방지)
                _ext_entry_r = round(entry, 5)
                if _ext_entry_r > 0:
                    _ext_mid_str  = str(old_pos.get("market_id", 0))
                    _ext_ws_size  = abs(float(WS_MANAGER._positions.get(_ext_mid_str, {}).get("position", 0)))
                    if _ext_ws_size < 1e-9:
                        if k not in self._closed_entries:
                            self._closed_entries[k] = {}
                        self._closed_entries[k][_ext_entry_r] = time.time() + 86400
                        self._save_closed_entries()
                    else:
                        log.debug(f"EXT closed_entries 등록 보류: {k} WS size={_ext_ws_size:.4g} 아직 존재")
                # EXT 시 _strategy_meta에서 제거 → _save_pos_state()에서 자동 제거됨
                # WHY: 직접 파일 I/O 제거 → sync() 내 블로킹 없음
                self._strategy_meta.pop(k, None)
                self._meta_dirty = True

        self._pos = new_pos
        # 첫 sync 완료 → 이후부터 EXT 판단 + 신규 진입 허용
        if not self._startup_sync_done:
            self._startup_sync_done = True
            self._startup_entry_allowed = True  # 포지션 확인 완료 → 신규 진입 허용
            log.info(f"✅ 시작 후 첫 sync 완료 — pos={len(new_pos)}개 확인, EXT/REBAL/신규진입 활성화")
        # sync 완료 후 파일에 반영
        self._save_pos_state()

    async def _atomic_close(self, key: str, exit_type: str, cur: float = 0.0) -> bool:
        """
        Atomic 청산 v4.4 (시장가, async Lock으로 중복 주문 완전 차단):
        - cur: 호출자가 이미 알고있는 현재가 (캔들 재조회 없음)
        - cur=0이면 캐시에서 조회
        """
        # BUG-4 FIX: Lock 획득 전 빠른 체크 (불필요한 lock 경합 최소화)
        if key in self._closing:
            return False
        # asyncio.Lock으로 동시 청산 시도 차단 (race condition 완전 제거)
        async with self._close_lock:
            # Lock 내부에서 다시 체크 (다른 코루틴이 먼저 청산했을 수 있음)
            if key in self._closing:
                return False
            pos = self._pos.get(key)
            if not pos:
                return False
            self._closing.add(key)

        pos = self._pos.get(key)
        if not pos:
            self._closing.discard(key)
            return False
        mid  = pos["market_id"]
        side = pos["side"]
        sym  = MID_TO_SYMBOL.get(mid, str(mid))

        # ── WS 포지션 사전 확인 (청산 주문 전) ─────────────────────────
        # WHY: 이미 외부에서 청산된 포지션에 청산 주문 → 오류/역방향 주문 위험
        #      WS _positions 확인으로 실제 포지션 존재 여부 검증 (REST 호출 없음)
        _ws_pos_pre = WS_MANAGER._positions.get(str(mid), {})
        _ws_sz_pre  = abs(float(_ws_pos_pre.get("position", 0)))
        if _ws_sz_pre < 1e-9:
            # WS에 포지션 없음 → 이미 청산된 상태 (EXT/수동 등)
            log.info(f"✅ {sym} {side}: WS 포지션 없음 → 청산 주문 불필요 (이미 청산됨)")
            self._closing.discard(key)
            # EXT로 기록 (아직 미기록 상태일 수 있음)
            if key in self._pos:
                _p2 = self._pos.pop(key, {})
                _e2 = _p2.get("entry_price", 0)
                _s2 = _p2.get("score", 0) or self.learner.min_score
                _pnl2 = (cur - _e2) / _e2 if side == "LONG" and _e2 > 0 and cur > 0 else 0
                if _e2 > 0:
                    _sz2 = _p2.get("size", 0)
                    _lev2 = _p2.get("leverage") or MARKET_LEVERAGE.get(mid, 10)
                    _pnl_usd2 = _pnl2 * _e2 * _sz2 * _lev2
                    _ext2_meta = {k2: _p2[k2] for k2 in TRADE_META_FIELDS if k2 in _p2}
                    _ext2_meta["leverage"] = _lev2
                    _ext2_meta["note"] = "WS_no_pos_at_close"
                    self.learner.record(sym, side, _e2, cur or _e2, _s2, _pnl2,
                                        "EXT", _pnl_usd2, meta=_ext2_meta)
            return False

        # 현재가: 호출자 제공 → _last_prices → _candle_cache → entry_price
        if cur <= 0:
            if self.engine:
                cur = (self.engine._last_prices.get(mid)
                       or (lambda c: float(c[1][-1]["close"]) if c and c[1] else None)(
                               self.engine._candle_cache.get(mid, {}).get("1m"))
                       or pos["entry_price"])
            else:
                cur = pos["entry_price"]

        _entry_p = pos["entry_price"]
        if _entry_p <= 0:
            log.error(f"🚨 {sym} {side} entry_price=0 → pnl 계산 불가, 청산 중단 (포지션 유지)")
            self._closing.discard(key)
            return False
        pnl = (cur - _entry_p) / _entry_p if side == "LONG" else (_entry_p - cur) / _entry_p

        # ── 청산 주문 전송 ─────────────────────────────────────────
        # SL/LIQ_DANGER: force_close (절대 실패 불가, 10회 재시도 + nonce 리셋)
        # 기타: 일반 3회 재시도
        ideal_int = price_to_int(mid, cur) if cur > 0 else None
        sent = False
        is_critical = exit_type in ("SL", "LIQ_DANGER", "TSL")
        if is_critical:
            from execute_order_final import force_close as _fc
            _payload = {"marketId": mid, "symbol": sym, "side": side, "size": pos["size"],
                        "price": 0, "reduce_only": True}
            if ideal_int:
                _payload["ideal_price"] = ideal_int
            # 현재 태스크를 _close_tasks에 등록 (EXT 감지 시 취소용)
            _cur_task = asyncio.current_task()
            if _cur_task:
                self._close_tasks[key] = _cur_task
            _resp = await _fc(_payload, max_attempts=15)
            self._close_tasks.pop(key, None)  # 완료 시 제거
            sent = _resp.get("success", False)
            if not sent:
                _err_msg = _resp.get('error','')
                # WHY: volume_quota 소진 시 SL 포기 → 과대손실 (2026-03-13 NEAR 사례 근본 수정)
                # execute_order_final.py에서 이미 15회 재시도 + quota 소진 시 대기 후 재시도 처리
                # 여기까지 온 경우 = 15회 모두 실패 → 네트워크/거래소 장애 → 로그만 남김
                log.error(f"🚨 force_close 완전 실패 {sym} {side} exit={exit_type}: {_err_msg}")
        else:
            for attempt in range(3):
                sent = await self.api.close_order(mid, side, pos["size"], ideal_price_int=ideal_int)
                if sent:
                    break
                log.warning(f"⚠️ Close {attempt+1}/3 실패 {sym} — 1s 후 재시도")
                await asyncio.sleep(1)
                _retry_cur = self.engine._last_prices.get(mid, cur) if self.engine else cur
                ideal_int = price_to_int(mid, _retry_cur) if _retry_cur > 0 else ideal_int

        if not sent:
            # ── 청산 실패 → 로컬 포지션 유지 ───────────────────────
            self._closing.discard(key)
            _fail_cd = 30 if is_critical else EXT_COOLDOWN_SEC
            log.error(f"🚨 Close 실패 {sym} {side} exit={exit_type} → {_fail_cd}s 후 재시도")
            self._cooldown[mid] = time.time() + _fail_cd
            return False

        # ── 청산 성공 → 로컬 정리 ──────────────────────────────────
        # FIX: 청산 성공 후 _closing에서 즉시 제거하지 않고 60초 유지
        # → orphan 복구 루프가 방금 청산한 포지션을 재복원하는 것 방지
        self._closing_ts[key] = time.time()  # 타임스탬프 갱신 (60초 유지)
        # _closing에는 유지 (discard 안 함) — sync()에서 60초 후 자동 제거

        # ── score 임시 보존 → WS 반영 지연 동안 score=0 방지 ──────
        # WHY: _pos.pop() 후 WS가 아직 포지션 반환 중이면 next sync에서
        #      prev/saved 모두 없음 → score=0 → min_score로 대체되는 문제
        #      _order_scores에 60초 TTL로 score 보관 → sync()에서 복원 가능
        _closing_score = pos.get("score", 0)
        if _closing_score > 0:
            self._order_scores[key] = {
                "score": _closing_score,
                "ts": time.time(),
                "entry_price": pos.get("entry_price", 0),
                "rsi": pos.get("rsi"), "adx": pos.get("adx"),
                "adx_regime": pos.get("adx_regime"), "supertrend": pos.get("supertrend"),
                "bb_pos": pos.get("bb_pos"), "of_dir": pos.get("of_dir"),
                "of_bonus": pos.get("of_bonus"), "of_imb": pos.get("of_imb"),
                "entry_hour_kst": pos.get("entry_hour_kst"),
                "adaptive_tp": pos.get("adaptive_tp", TP_PCT),
                "adaptive_sl": pos.get("adaptive_sl", SL_PCT),
                "_closing_score": True,  # 청산 중 score 보존 마킹
            }

        # closed_entries: _closing 해제 시점(WS size=0 확인)에 등록됨
        # WHY: force_close 직후 WS 미반영 상태에서 등록하면 오등록 가능
        #      _retry_close_loop + _closing WS=0 해제 시 자동 등록 (아래 참조)
        _closed_entry = pos.get("entry_price", 0)
        _ce_mid_str   = str(mid)
        _ce_entry_r   = round(_closed_entry, 5)
        # 즉시 WS 확인 (이미 체결됐을 경우 빠른 등록)
        _ce_ws_size_now = abs(float(WS_MANAGER._positions.get(_ce_mid_str, {}).get("position", 0)))
        if _closed_entry > 0 and _ce_ws_size_now < 1e-9:
            if key not in self._closed_entries:
                self._closed_entries[key] = {}
            self._closed_entries[key][_ce_entry_r] = time.time() + 86400
            log.debug(f"closed_entries 등록: {key} entry={_ce_entry_r} (즉시 WS 확인)")
        self._pos.pop(key, None)
        # _strategy_meta: _closing 동안(60초) 유지 → WS에서 포지션 사라지면 자동 정리
        # WHY: 즉시 제거하면 재시작 시 score 복원 불가 (WS 반영 지연 중 재시작 케이스)
        #      _save_pos_state()의 WS 교차검증에서 WS size=0 + _closing 해제 후 자동 제거
        self._meta_dirty = True  # 다음 _save_pos_state()에서 flush
        # closed_entries 전용 파일에 즉시 저장 (race condition 없음)
        self._save_closed_entries()
        self.set_cooldown(mid)
        self.reset_add_signal(mid)
        # ★ 청산 후 account 캐시 무효화 — 백오프 중이면 스킵 (rate limit 보호)
        if self.api._rl_backoff <= 0:
            self.api._cache_ts = 0.0

        # ── 기록 ───────────────────────────────────────────────────
        # pos_value = size × entry_price (레버리지 포함 명목 포지션 크기)
        # pnl_usd   = pos_value × pnl_pct (실제 달러 손익)
        pos_value_calc = pos["size"] * pos["entry_price"]
        pnl_usd        = pos_value_calc * pnl
        # SC0_CLEANUP / EXT / LIQ_DANGER / REBAL → WR 통계 완전 제외
        if exit_type in ("SC0_CLEANUP", "EXT", "LIQ_DANGER", "REBAL"):
            log.info(f"🧹 {exit_type}: {sym} {side} {pnl*100:+.3f}%")
        else:
            # ── 진입 지표 완전 보존 (Optimizer 활용) ──────────────────
            # WHY: pos에 지표가 없는 경우(재시작 후 WS만 복원, 또는 초기 버전)
            #      _strategy_meta → _order_scores 순으로 fallback 보강
            #      → 모든 청산(SL/TSL/TP) 시 rsi/adx/supertrend 등 완전 기록 보장
            entry_ts = pos.get("entry_ts")
            hold_secs = int(time.time() - entry_ts) if entry_ts else None

            # 1순위: pos (sync()에서 병합된 최신 값)
            meta = {k: pos[k] for k in TRADE_META_FIELDS if k in pos}

            # 2순위: _strategy_meta (재시작 후 복원된 진입 시 지표)
            _smeta = self._strategy_meta.get(key, {})
            for _k in TRADE_META_FIELDS:
                if meta.get(_k) is None and _smeta.get(_k) is not None:
                    meta[_k] = _smeta[_k]

            # 3순위: _order_scores (청산 직전 score 보존 목적으로 저장된 지표)
            _oscores = self._order_scores.get(key, {})
            for _k in TRADE_META_FIELDS:
                if meta.get(_k) is None and _oscores.get(_k) is not None:
                    meta[_k] = _oscores[_k]

            if hold_secs is not None:
                meta["hold_secs"] = hold_secs
            # 파라미터 영향 추적용 필드 (Strategy Optimizer 분석 재료)
            # leverage: pos에 없으면 MARKET_LEVERAGE에서 fallback
            meta["leverage"]     = pos.get("leverage") or MARKET_LEVERAGE.get(pos.get("market_id", 0), 10)
            meta["adaptive_tp"]  = pos.get("adaptive_tp", TP_PCT)
            meta["adaptive_sl"]  = pos.get("adaptive_sl", SL_PCT)
            meta["peak_pnl"]     = pos.get("peak_pnl", 0.0)
            meta["entry_price"]  = pos.get("entry_price", 0.0)
            meta["entry_ts"]     = entry_ts  # 진입 시각 → trade_history에 저장
            meta["params_ver"]   = _get_params_ver()
            self.learner.record(sym, side, pos["entry_price"], cur,
                                pos.get("score", 0), pnl, exit_type, pnl_usd,
                                meta=meta if meta else None)
            if exit_type == "TP" or exit_type.startswith("TSL"):
                self.stats["tp"] += 1
                log.info(f"✅ {exit_type}: {sym} {side} +{pnl*100:.3f}%")
                # TP/TSL 시 streak 리셋
                DYNAMIC_BL._sl_streak.pop(sym, None)
                self._consecutive_wins = getattr(self, '_consecutive_wins', 0) + 1
            else:
                self.stats["sl"] += 1
                log.info(f"❌ SL: {sym} {side} {pnl*100:.3f}%")
                # SL 기록 → 연속 2회 시 1시간 블랙리스트
                DYNAMIC_BL.record_sl(sym)
                self._consecutive_wins = 0
        return True

    async def check_tpsl(self) -> int:
        """
        v5.8: scan_all _last_prices 캐시 100% 재사용
        - 캐시 히트: 추가 HTTP 요청 0건
        - 캐시 미스(포지션 마켓이 54개 외 혹은 데이터 없음): aiohttp 폴백
        - SL 배치 청산: 동시 SL HIT → batch_close로 1번 API 호출 (rate limit 절약)
        """
        closed      = 0
        price_cache = self.engine._last_prices   # scan_all에서 모든 마켓 저장
        # v5.8: SL 배치 청산용 큐
        sl_batch_queue: list = []   # {"key": key, "pos": pos, "cur": cur} — SL HIT 포지션 모음

        async def get_cur(mid: int) -> Optional[float]:
            # FIX: WS mark_price 최우선 (캔들 close보다 정확, 1~1.5% 오차 방지)
            # 1) WS mark_price (가장 정확)
            ws_p = WS_MANAGER._positions.get(str(mid), {})
            mp = float(ws_p.get("mark_price") or 0)
            if mp > 0:
                return mp
            # 2) 캐시 (mark_price가 없으면 scan_all 캔들 close)
            v = price_cache.get(mid)
            if v is not None:
                return v
            # 3) 캔들 캐시
            cached = self.engine._candle_cache.get(mid, {}).get("1m")
            if cached and cached[1]:
                return float(cached[1][-1]["close"])
            # 4) 폴백: aiohttp
            data = await self.api.get_candles_async(mid, "1m", 1)
            return float(data[-1]["close"]) if data else None

        # orphan 복구는 sync()가 단독 담당 → check_tpsl에서 제거 (중복/경합 방지)

        for key, pos in list(self._pos.items()):
            if key in self._closing: continue
            mid = pos["market_id"]

            # ── WS 포지션 실시간 확인 (모든 주문의 1순위 원칙) ────────────
            # WHY: _pos는 로컬 캐시 — WS(거래소)가 진실
            #      WS size=0이면 이미 청산됨 → SL/TSL 시도 불필요 (중복 주문 방지)
            _ws_check = WS_MANAGER._positions.get(str(mid), {})
            _ws_sz_check = abs(float(_ws_check.get("position", 0)))
            if _ws_sz_check < 1e-9:
                # WS에 포지션 없음 → 이미 청산됨 (수동/EXT/기타)
                # sync()에서 EXT로 처리될 것 — 여기서는 스킵만
                log.debug(f"check_tpsl: {pos.get('symbol')} WS size=0 → 스킵 (이미 청산됨)")
                continue

            cur = await get_cur(mid)
            if cur is None:
                # cur 완전 실패 시: WS unrealized_pnl + margin으로 pnl 직접 계산
                ws_p  = WS_MANAGER._positions.get(str(mid), {})
                ws_upnl  = float(ws_p.get("unrealized_pnl") or 0)
                pos_pval = abs(float(ws_p.get("position_value") or 0))
                pos_imf  = float(ws_p.get("initial_margin_fraction") or 0)
                pos_margin = pos_pval * (pos_imf/100) if pos_imf > 0 else pos.get("margin", 0)
                if pos_pval > 0 and ws_upnl != 0:
                    # pnl_price = upnl / position_value (가격 기준 수익률)
                    # WHY: upnl/margin은 레버리지 마진 기준 → 가격 변동률 아님
                    # upnl/pval이 가격 변동률 = TP/SL 기준과 일치
                    direct_pnl = ws_upnl / pos_pval
                    entry_p2 = pos.get("entry_price", 0) or 0
                    if entry_p2 > 0:
                        cur = entry_p2 * (1 + direct_pnl) if pos.get("side") == "LONG" \
                               else entry_p2 * (1 - direct_pnl)
                        log.debug(f"check_tpsl: {pos.get('symbol')} cur=None → WS upnl 폴백 pnl={direct_pnl:.4%}")
                    else:
                        continue
                else:
                    continue

            entry_p = pos.get("entry_price", 0) or 0
            if entry_p <= 0:
                log.warning(f"⚠️  check_tpsl: {pos.get('symbol','?')} entry_price=0 → TP/SL 스킵")
                continue
            # ── pnl 계산: WS uPnL primary → cur 폴백 (심플화 v5.7) ──
            # WS uPnL이 있으면 → mark_price = entry ± uPnL/size (가장 정확)
            # WS uPnL 없으면 → cur(캔들/캐시) 사용
            _ws_p2   = WS_MANAGER._positions.get(str(mid), {})
            _ws_upnl = float(_ws_p2.get("unrealized_pnl") or 0)
            _pos_sz  = float(_ws_p2.get("position") or pos.get("size") or 0)
            if abs(_pos_sz) > 1e-9:
                # WS mark_price 직접 역산 (가장 정확, 캔들 오차 없음)
                _mark = entry_p + _ws_upnl / abs(_pos_sz)
                if _mark > 0:
                    cur = _mark  # WS uPnL 기반 mark_price 최우선
            pnl = (cur - entry_p) / entry_p if pos["side"] == "LONG" \
                  else (entry_p - cur) / entry_p

            sym  = pos.get("symbol", "")
            side = pos.get("side", "LONG")
            # ── 레버리지: WS 실시간 → pos 저장값 → leverage_mapping 순서로 우선 ──
            # WHY: 노이반님 지적 — 마켓마다 레버리지 다름, WS에서 이미 imf 수집 중
            _ws_pos_data = WS_MANAGER._positions.get(str(mid), {})
            _ws_imf = float(_ws_pos_data.get("initial_margin_fraction", 0) or 0)
            if _ws_imf > 0:
                leverage = int(100 / _ws_imf)   # WS imf 우선 (가장 정확)
            else:
                leverage = pos.get("leverage") or MARKET_LEVERAGE.get(mid, 10)
            # ── TP/SL: 레버리지 무관, 순수 가격 기준 % ──────────────────────
            # WHY: pnl = (cur-entry)/entry → 가격 기준 소수
            #      TP/SL/TSL도 동일하게 가격 기준으로 통일해야 비교 가능
            #      레버리지는 포지션 사이징에만 반영, TP/SL 트리거에는 미반영
            base_tp = TP_PCT   # 가격 기준 고정값 (레버리지 무관)
            base_sl = SL_PCT   # 가격 기준 고정값 (레버리지 무관)
            # ATR Adaptive override (진입 시 저장, 이미 가격 기준)
            adaptive_tp_raw = pos.get("adaptive_tp")
            adaptive_sl_raw = pos.get("adaptive_sl")
            tp = adaptive_tp_raw if adaptive_tp_raw else base_tp
            sl = adaptive_sl_raw if adaptive_sl_raw else base_sl
            # 안전 클램프: 기본값 최소 보장 + ADAPTIVE 상한 (모두 가격 기준)
            tp = max(min(tp, ADAPTIVE_TP_MAX), base_tp)
            sl = max(min(sl, ADAPTIVE_SL_MAX), base_sl)

            # ── margin_usage 기반 LIQ DANGER 제거 (2026-03-14 노이반님 승인) ──
            # WHY: Cross margin에서 margin_usage는 단순 마진 사용률 (포지션 많으면 90%+ 정상)
            #      청산 위험과 직접 관계 없음 — 실제 청산은 equity < maintenance_margin 시 거래소가 처리
            #      margin_usage >= 90% 조건 → 포지션 전부 순환 LIQ DANGER → _atomic_close 무한루프 → hang
            #      liq_dist 기반 위험 판정은 REST sync 후 아래 별도 로직으로 처리

            # ── Trailing Stop 로직 ──────────────────────────────
            # 단계별 이익 보호:
            #  pnl < tp        → 기본 SL 유지 (손절)
            #  pnl >= tp       → Trailing 시작: peak_pnl 추적, SL = peak×0.5
            #  pnl >= tp×2     → SL = peak×0.6 (더 타이트하게)
            #  pnl >= tp×3     → SL = peak×0.7 (더더 타이트하게)
            peak_pnl = pos.get("peak_pnl", 0.0)
            if pnl > peak_pnl:
                pos["peak_pnl"] = pnl
                peak_pnl = pnl

            if peak_pnl >= tp:
                # ── TSL: TP 도달 즉시 시작 → 수익 추적 + 되돌림 보호 ──────
                # WHY: TP 즉시확정보다 TSL이 유리 (큰 추세에서 수익 극대화)
                # 단계별 보호 강화 + 슬리피지 고려 절대 손실 방지
                if peak_pnl >= tp * 4:
                    preserve = TSL_PRESERVE_T4   # 92% 보호
                elif peak_pnl >= tp * 3:
                    preserve = TSL_PRESERVE_T3   # 85% 보호
                elif peak_pnl >= tp * 2:
                    preserve = TSL_PRESERVE_T2   # 70% 보호
                else:
                    preserve = TSL_PRESERVE_T1   # 50% 보호 (TP 1배~2배 구간)

                trail_floor = peak_pnl * preserve
                # 슬리피지 고려: floor는 최소 SLIPPAGE_GUARD 이상 (절대 손실 없음)
                trail_floor = max(trail_floor, TSL_SLIPPAGE_GUARD)
                # trail_sl: 현재 pnl이 floor 아래로 내려오면 청산
                trail_sl = trail_floor  # pnl <= trail_sl 조건

                if pnl <= trail_sl:
                    label = f"TSL(peak={peak_pnl:.3%},floor={trail_floor:.3%})"
                    log.info(f"🎯 TSL 청산: {sym} pnl={pnl:.3%} floor={trail_floor:.3%} (peak={peak_pnl:.3%})")
                    if await self._atomic_close(key, label, cur=cur):
                        closed += 1
                        self._save_pos_state()   # 즉시 ws_positions.json 갱신
                        await asyncio.sleep(1.0)
            elif pnl <= -sl:
                # MIN_HOLD_SEC 체크: 진입 후 30초 이내면 SL 비활성화
                entry_ts = pos.get("entry_ts", 0)   # FIX: entry_time → entry_ts
                hold_secs = time.time() - entry_ts if entry_ts else 999
                if hold_secs < MIN_HOLD_SEC:
                    continue  # 30초 미만 홀드는 SL 무시 (슬리피지 노이즈)

                # v5.8: SL 배치 큐에 추가 (즉시 청산 대신 모아서 batch_close)
                sl_batch_queue.append({"key": key, "pos": pos, "cur": cur})

        # ── v5.8: SL 배치 청산 ──────────────────────────────────────
        # WHY batch_close: 여러 SL 동시 HIT → 각각 force_close → rate limit 폭발
        # batch_close = 서명 N개 로컬 수집 → send_tx_batch 1번 API 호출
        if sl_batch_queue:
            if len(sl_batch_queue) == 1:
                # 단건: 기존 _atomic_close (force_close 10회 재시도)
                item = sl_batch_queue[0]
                if await self._atomic_close(item["key"], "SL", cur=item["cur"]):
                    closed += 1
                    self._save_pos_state()   # 즉시 ws_positions.json 갱신
            else:
                # 다건: batch_close (1 API 호출)
                log.info(f"🔄 SL 배치 청산 {len(sl_batch_queue)}건 → batch_close")
                from execute_order_final import batch_close as _batch_close

                # _closing 등록 (중복 방지)
                batch_keys = []
                batch_orders = []
                for item in sl_batch_queue:
                    k = item["key"]
                    p = item["pos"]
                    if k in self._closing:
                        continue
                    async with self._close_lock:
                        if k in self._closing:
                            continue
                        self._closing.add(k)
                    batch_keys.append(k)
                    mid = p["market_id"]
                    sym = MID_TO_SYMBOL.get(mid, str(mid))
                    cur_p = item["cur"]
                    ideal_int = price_to_int(mid, cur_p) if cur_p > 0 else None
                    batch_orders.append({
                        "marketId": mid, "symbol": sym,
                        "side": p["side"], "size": p["size"],
                        "ideal_price": ideal_int, "reduce_only": True,
                    })

                if batch_orders:
                    try:
                        result = await _batch_close(batch_orders)
                        results = result.get("results", [])
                        batch_ok = result.get("batch_success", False)
                        quota = result.get("volume_quota_remaining", -1)

                        for i, item in enumerate(sl_batch_queue):
                            k = item["key"]
                            if k not in batch_keys:
                                continue
                            p = item["pos"]
                            cur_p = item["cur"]
                            mid = p["market_id"]
                            sym = MID_TO_SYMBOL.get(mid, str(mid))
                            r = results[i] if i < len(results) else {}
                            r_ok = r.get("success", False)

                            if r_ok:
                                # 성공 → 로컬 정리 (_atomic_close 후처리 모방)
                                self._closing_ts[k] = time.time()
                                _entry_p = p.get("entry_price", 0)
                                if _entry_p > 0:
                                    _ce_entry_r = round(_entry_p, 5)
                                    if k not in self._closed_entries:
                                        self._closed_entries[k] = {}
                                    self._closed_entries[k][_ce_entry_r] = time.time() + 86400
                                self._pos.pop(k, None)
                                self._save_closed_entries()
                                self.set_cooldown(mid)
                                self.reset_add_signal(mid)
                                if self.api._rl_backoff <= 0:
                                    self.api._cache_ts = 0.0
                                # 기록
                                _entry_p = p.get("entry_price", 0) or 0
                                pnl_b = (cur_p - _entry_p) / _entry_p if p["side"] == "LONG" else (_entry_p - cur_p) / _entry_p
                                pos_val = p["size"] * _entry_p
                                pnl_usd = pos_val * pnl_b
                                entry_ts2 = p.get("entry_ts")
                                hold_s = int(time.time() - entry_ts2) if entry_ts2 else None
                                meta2 = {kk: p[kk] for kk in TRADE_META_FIELDS if kk in p}
                                if hold_s is not None: meta2["hold_secs"] = hold_s
                                meta2["leverage"] = p.get("leverage") or MARKET_LEVERAGE.get(p.get("market_id",0), 10)
                                meta2["adaptive_tp"] = p.get("adaptive_tp", TP_PCT)
                                meta2["adaptive_sl"] = p.get("adaptive_sl", SL_PCT)
                                meta2["peak_pnl"]    = p.get("peak_pnl", 0.0)
                                meta2["entry_price"] = _entry_p
                                self.learner.record(sym, p["side"], _entry_p, cur_p, p.get("score",0), pnl_b, "SL", pnl_usd, meta=meta2)
                                self.stats["sl"] += 1
                                log.info(f"❌ SL(batch): {sym} {p['side']} {pnl_b*100:.3f}%")
                                DYNAMIC_BL.record_sl(sym)
                                self._consecutive_wins = 0
                                closed += 1
                            else:
                                # 실패 → _closing 해제, 재시도 예약
                                self._closing.discard(k)
                                err_msg = r.get("error", "")
                                log.error(f"🚨 batch SL 실패 {sym}: {err_msg[:80]} → force_close 폴백")
                                # 폴백: 개별 force_close
                                if await self._atomic_close(k, "SL", cur=cur_p):
                                    closed += 1

                        if quota >= 0:
                            log.info(f"📊 volume_quota_remaining: {quota}")

                    except Exception as _be:
                        log.error(f"batch_close 예외: {_be} → 개별 force_close 폴백")
                        # 전체 폴백: 각각 _atomic_close
                        for item in sl_batch_queue:
                            k = item["key"]
                            self._closing.discard(k)
                            if await self._atomic_close(k, "SL", cur=item["cur"]):
                                closed += 1
                            await asyncio.sleep(1.5)

        return closed

    async def rebalance(self, signals: List[Dict], avail: float, cap: float = 0) -> int:
        """
        v5.0 리밸런싱 (스캘핑 스노우볼 최적화)
        ══════════════════════════════════════════
        트리거 (OR 조건):
          A. avail 부족 + 더 강한 신호 존재  →  약한 포지션 교체
          B. 현재 포지션 중 score가 현저히 낮은 것 존재
             + 신규 신호가 REBAL_FORCE_DIFF 이상 강함  →  무조건 교체

        교체 우선순위 (낮을수록 먼저):
          priority = pos_score
                   + pnl_shield     # TSL/수익 중인 포지션 강하게 보호
                   - loss_penalty   # 손실 중일수록 교체 촉진

          pnl_shield: peak_pnl >= TP의 2배 이상 → +50 (TSL 진행 중, 절대 교체 금지)
                      pnl > TP_PCT → +30 (TP 도달 직전, 보호)
                      pnl > 0      → +10 (수익 중, 약간 보호)
          loss_penalty: pnl < -SL의 50% → -20 (SL 임박, 교체 우선)
                        pnl < 0         → -5  (손실 중, 교체 선호)

        교체 불가 조건:
          - _closing 중인 포지션
          - score == 0 (sync 복원 포지션)
          - TSL 진행 중 (peak_pnl >= TP_PCT)  →  pnl_shield +50으로 자동 보호
          - 신규 score - 기존 score < REBAL_SCORE_DIFF  →  점수 차 부족
        """
        if not signals or not self._pos: return 0
        # 쿨다운은 TradingBot._loop()에서 REBAL_COOLDOWN_SEC 단일 관리
        # (PM 내부 이중관리 제거)

        existing_mids = {p["market_id"] for p in self._pos.values()}
        new_sigs = [s for s in signals
                    if s["market_id"] not in existing_mids
                    and s["score"] >= self.learner.min_score]
        if not new_sigs: return 0

        best_new = new_sigs[0]   # score 내림차순 정렬됨

        # ── 트리거 판정 ────────────────────────────────────────────
        # A: avail 부족 (신규 진입 불가 수준)
        # MIN_MARGIN_USD($3) + MIN_AVAIL($1) = $4 이하일 때만 avail_starved로 판정
        # 기존: cap × ratio 계산 → 과도하게 트리거됨 (200회 이상 리밸 원인)
        _min_avail_dyn = get_min_avail(cap)  # equity≈cap 기준 동적 MIN_AVAIL
        avail_starved = avail < MIN_MARGIN_USD + _min_avail_dyn

        # B: 강제 교체 — 기존 포지션 중 score가 훨씬 낮은 것 있음
        weak_exists = any(
            p.get("score", 0) > 0
            and best_new["score"] - p.get("score", 0) >= REBAL_FORCE_DIFF
            for k, p in self._pos.items() if k not in self._closing
        )

        if not avail_starved and not weak_exists:
            return 0

        # ── 교체 대상 선정 ─────────────────────────────────────────
        worst_key      = None
        worst_priority = 9999.0

        for k, pos in list(self._pos.items()):
            if k in self._closing:     continue
            pos_score = pos.get("score", 50)
            # pos_score==0 skip 제거: sync에서 항상 min_score로 복원되므로 비교 가능
            if best_new["score"] - pos_score < REBAL_SCORE_DIFF: continue  # 점수 차 부족

            mid   = pos["market_id"]
            side  = pos["side"]
            entry = pos.get("entry_price", 0)
            # entry_price=0 포지션: 우선순위 최저로 처리 (교체 우선 대상)
            if entry <= 0:
                raw_pnl = -SL_PCT  # SL 임박처럼 취급 → 교체 우선
                log.warning(f"⚠️  rebalance: {MID_TO_SYMBOL.get(mid, str(mid))} entry_price=0 → 교체 우선 처리")
            else:
                cur_p = self.engine._last_prices.get(mid, entry) if self.engine else entry
                if cur_p > 0:
                    raw_pnl = (cur_p - entry) / entry if side == "LONG" else (entry - cur_p) / entry
                else:
                    raw_pnl = 0.0

            peak_pnl = pos.get("peak_pnl", 0.0)
            atp      = pos.get("adaptive_tp") or TP_PCT

            # pnl_shield: TSL 진행 중이면 강하게 보호
            if peak_pnl >= atp:          pnl_shield = 50   # TSL 진행 중 → 절대 교체 금지 수준
            elif raw_pnl >= atp * 0.5:   pnl_shield = 30   # TP 50% 이상 도달 → 보호
            elif raw_pnl > 0:            pnl_shield = 10   # 수익 중 → 약간 보호
            else:                        pnl_shield = 0

            # loss_penalty: 손실 중이면 교체 우선
            if raw_pnl < -SL_PCT * 0.5:  loss_penalty = 20  # SL 임박
            elif raw_pnl < 0:            loss_penalty = 5   # 손실 중
            else:                        loss_penalty = 0

            priority = pos_score + pnl_shield - loss_penalty

            if priority < worst_priority:
                worst_priority = priority
                worst_key      = k

        if worst_key is None: return 0

        pos     = self._pos[worst_key]
        old_sym = MID_TO_SYMBOL.get(pos["market_id"], str(pos["market_id"]))
        old_sc  = pos.get("score", 0)
        reason  = "avail부족" if avail_starved else "score격차"
        closed  = await self._atomic_close(worst_key, "REBAL")
        if not closed: return 0

        log.info(f"🔄 Rebal[{reason}]: {old_sym}(sc={old_sc},pri={worst_priority:.0f}) → {best_new['symbol']}(sc={best_new['score']})")
        self.stats["rebalanced"] += 1
        # 쿨다운은 TradingBot._loop()에서 _last_rebal 갱신으로 단일 관리
        # 캐시 무효화 — 백오프 중이면 스킵
        if self.api._rl_backoff <= 0:
            self.api._cache_ts = 0.0
        # 청산 후 다음 루프에서 자연스럽게 신규 진입
        # (즉시 재진입 제거 — Rebal→재진입→avail부족→Rebal 무한루프 방지)
        return 1

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TradingBot v4
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class TradingBot:
    def __init__(self):
        self.api          = LighterAPI()
        self.engine       = SignalEngine()
        self.learner      = SelfLearner()
        self.paper_trader = PaperTrader()   # 차단 신호 가상 포지션 백테스팅
        self.pm           = PositionManager(self.api, self.learner, self.engine)
        self._last_signals: List[Dict] = []   # /score 명령어용 캐시
        # (리밸 후 즉시진입 제거됨 — _open_signals가 다음 루프에서 처리)
        self._loop_cnt       = 0
        self._daily_eq_saved = ""   # daily_equity.json 마지막 저장 날짜 (KST)
        self._start_ts       = time.time()
        self._last_rebal     = time.time() + 180  # 재시작 후 180초간 REBAL 차단
        self._start_cap      = 0.0
        self._last_margin    = 0.0
        self._last_sig: Dict = {}          # 진입 직전 신호 임시 저장 (getattr 제거)
        self._score_cache_ts: float = 0.0  # score_cache 저장 시각 (getattr 제거)
        self._quota_block_until: float = 0.0  # volume quota 소진 시 진입 차단 타임스탬프
        self._liq_sync_ts: float = 0.0    # REST liq_price 마지막 갱신 시각

    def _calc_margin(self, score: int, cap: float, avail: float, leverage: int) -> float:
        """
        v5.2 score-driven 동적 사이즈 (가용자산 기준, 슬롯 제한 없음):

        score_ratio  = score / MAX_SCORE              # 0.75(sc=90) ~ 1.0(sc=120)
        cap_ratio    = 자본 단계별 배분율              # 공격성 조절
                       cap < $500  → 25% (소액 공격적)
                       cap < $2000 → 20%
                       cap < $10000→ 15%
                       cap 이상    → 10% (대형 분산)
        lev_discount = 1 / sqrt(lev / 5)              # 레버리지 보정
        최종 margin  = spendable × cap_ratio × score_ratio × lev_discount
        포지션 수 제한 없음 — 가용금이 MIN_MARGIN_USD 이상이면 계속 진입
        """
        import math
        spendable = min(avail - get_min_avail(cap), cap * MAX_CAPITAL_USAGE)
        if spendable <= 0:
            return 0.0

        # 자본 규모별 포지션당 배분율 (스노우볼 복리 구조)
        # 현재 $416 → 30% = $125/거래, 복리로 자본이 클수록 더 큰 포지션
        if cap < 500:
            cap_ratio = 0.30   # 소자본: 공격적 30% (빠른 복리 시작)
        elif cap < 1000:
            cap_ratio = 0.28
        elif cap < 2000:
            cap_ratio = 0.25
        elif cap < 5000:
            cap_ratio = 0.20
        elif cap < 10000:
            cap_ratio = 0.15
        else:
            cap_ratio = 0.10

        score_ratio  = min(score / MAX_SCORE, 1.0)
        lev_discount = 1.0 / math.sqrt(max(leverage, LEV_DISCOUNT_BASE) / LEV_DISCOUNT_BASE)

        margin = spendable * cap_ratio * score_ratio * lev_discount

        # 예비금 보호
        if margin > spendable:
            margin = spendable

        # 최소 마진 하한선
        if margin < MIN_MARGIN_USD and spendable >= MIN_MARGIN_USD:
            margin = MIN_MARGIN_USD

        # 최소 포지션 보장
        min_margin = MIN_POSITION_VAL / leverage
        if margin < min_margin:
            return 0.0

        return margin

    async def run(self):
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.info("💀 DEATH MODE v7.4 - min_score=90 | bad_hours 차단 비활성(검토 대기)")
        log.info(f"⚡ TP: {TP_PCT*100}%(기본) / {TP_PCT_HIGH_SPREAD*100}%(Tier3) | SL: {SL_PCT*100}% | Cooldown: {COOLDOWN_SEC}s")
        log.info(f"🎯 Min Score: {self.learner.min_score} (adaptive)")
        log.info(f"💰 Score-driven: avail×cap_ratio(25~10%)×(sc/120)×lev_discount | 최소 ${MIN_MARGIN_USD:.0f} | 최대 {MAX_CAPITAL_USAGE:.0%} 활용 | 예비금 ${MIN_AVAIL:.0f}")
        log.info(f"📡 TF: 15s+30s+1m+5m+15m+1h+4h (EMA5/20/100/200)")
        log.info(f"📊 Score: 거시추세30+RSI25+BB20+Vol10+bonus10 (max120)")
        log.info(f"🔄 Rebal: 유연화 — 가용부족 즉시교체 + 강한신호 우선")
        log.info(f"🏪 Markets: {len(TOP_20_MARKETS)}개")
        log.info(f"🚫 Static BL: {sorted(BLACKLIST_SYMBOLS)}")
        log.info(f"🚫 Dynamic BL: TTL={BLACKLIST_TTL_SEC//3600}h, 연속SL {BLACKLIST_SL_STREAK}회 시 자동 차단")
        log.info(f"📱 TG Commander: 비활성화 (메인 에이전트가 직접 처리)")
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        async def _bot_loop():
            while True:
                t0 = time.time()
                try:
                    await self._loop()
                except Exception as e:
                    log.error(f"Loop error: {e}", exc_info=True)
                elapsed = time.time() - t0
                await asyncio.sleep(max(0, CHECK_INTERVAL - elapsed))

        # WS Manager 시작 (account_all만 구독 — OB 구독 제거로 WS 안정성 확보)
        await WS_MANAGER.start()
        log.info(f"📡 WS: account_all만 구독 (OB는 REST 캐시 60s TTL)")

        async def _tpsl_loop():
            """독립 TP/SL 체크 루프 — scan_all과 완전 분리, 2초마다 실행
            WHY: scan_all이 60~90초 소요 → 그 동안 SL 미실행 → 손실 누적
            근본 해결: check_tpsl을 별도 태스크로 분리하여 항상 2초 주기 보장
            """
            await asyncio.sleep(5)  # 봇 초기화 완료 대기 (10→5초 단축: 재시작 후 SL 보호 강화)
            while True:
                t0 = time.time()
                try:
                    if self.pm._pos:
                        await self.pm.check_tpsl()
                    # 가상 포지션 TP/SL 체크 (paper_trader)
                    if self.paper_trader._positions:
                        self.paper_trader.check_all(self.engine._last_prices)
                except Exception as _e:
                    log.error(f"_tpsl_loop error: {_e}", exc_info=True)
                elapsed = time.time() - t0
                await asyncio.sleep(max(0, CHECK_INTERVAL - elapsed))

        async def _retry_close_loop():
            """_closing 포지션 재청산 루프 — WS size>0인 채 60초 이상 대기 중인 포지션 재시도
            WHY: force_close 전송 후 거래소 미체결 시 _closing 유지되지만 재시도 없음
                 → 60초마다 WS 확인 후 아직 포지션 있으면 force_close 재전송
                 (강제 해제 후 재등록→SL 루프 방지 — HYPE 사례 2026-03-14)
            """
            await asyncio.sleep(30)
            while True:
                try:
                    _now_r = time.time()
                    for k in list(self.pm._closing):
                        mid_str_r = k.split("_")[0]
                        ws_r = WS_MANAGER._positions.get(mid_str_r, {})
                        ws_sz_r = abs(float(ws_r.get("position", 0)))
                        elapsed_r = _now_r - self.pm._closing_ts.get(k, _now_r)
                        if ws_sz_r > 1e-9 and elapsed_r >= 60:
                            # WS에 아직 포지션 있고 60초 이상 대기 → 재시도
                            pos_r = self.pm._pos.get(k) or self.pm._strategy_meta.get(k)
                            if pos_r:
                                mid_r = int(mid_str_r)
                                side_r = k.split("_")[1] if "_" in k else pos_r.get("side","LONG")
                                sym_r = MID_TO_SYMBOL.get(mid_r, str(mid_r))
                                log.warning(f"🔄 _retry_close: {sym_r} {side_r} WS size={ws_sz_r:.4g} {elapsed_r:.0f}s 대기 → force_close 재시도")
                                from execute_order_final import force_close as _fc_r
                                _payload_r = {"marketId": mid_r, "symbol": sym_r,
                                              "side": side_r, "size": ws_sz_r,
                                              "price": 0, "reduce_only": True}
                                _resp_r = await _fc_r(_payload_r, max_attempts=5)
                                if _resp_r.get("success"):
                                    log.info(f"✅ _retry_close 성공: {sym_r} {side_r}")
                                    # _closing_ts 갱신 → 다음 WS 확인에서 해제
                                    self.pm._closing_ts[k] = time.time()
                                else:
                                    log.error(f"❌ _retry_close 실패: {sym_r} {side_r} {_resp_r.get('error','')}")
                except asyncio.CancelledError:
                    break
                except Exception as _e:
                    log.error(f"_retry_close_loop error: {_e}")
                await asyncio.sleep(60)

        async def _ob_snapshot_loop():
            """백그라운드 OB 스냅샷 수집 루프 — 전체 구독 마켓 주기적 분석/저장"""
            await asyncio.sleep(15)  # 봇 초기화 후 대기
            while True:
                try:
                    mids = sorted(MARKET_LEVERAGE.keys())
                    for mid in mids:
                        ws_ob = WS_MANAGER.get_ob(mid)
                        if ws_ob and (ws_ob.get("bids") or ws_ob.get("asks")):
                            result = OF_ENGINE._analyze_ob_data(
                                ws_ob["bids"], ws_ob["asks"], mid)
                            OF_ENGINE._record_ob_snapshot(mid, result)
                        await asyncio.sleep(0.05)  # 마켓 간 50ms — CPU 과부하 방지
                    # 1사이클 완료 후 저장
                    OF_ENGINE._save_ob_history()
                    log.info(f"📊 OB snapshot 수집: {len(OF_ENGINE._ob_history)}마켓 {sum(len(v) for v in OF_ENGINE._ob_history.values())}건")
                    await asyncio.sleep(60)  # 60초마다 전체 사이클
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    log.debug(f"OB snapshot loop error: {e}")
                    await asyncio.sleep(30)

        async def _pos_state_sync_loop():
            """pos_state.json 주기적 갱신 — WS uPnL 실시간 반영
            WHY: _save_pos_state()가 WS 이벤트 수신 시에만 호출 →
                 uPnL 변동이 없으면 pos_state 정체
            FIX: 10초마다 강제 저장 → /score, heartbeat에서 항상 최신값 확인 가능
            """
            await asyncio.sleep(15)
            while True:
                try:
                    if self.pm._pos:
                        # _meta_dirty 강제 설정: cmd_handler /pos에서 score/hold가
                        # 항상 최신값으로 표시되도록 주기적으로 flush
                        self.pm._meta_dirty = True
                        self.pm._save_pos_state()
                except Exception as _e:
                    log.debug(f"pos_state sync error: {_e}")
                await asyncio.sleep(10)

        # 봇 루프 실행 (TelegramCommander 비활성화 — 메인 에이전트가 직접 처리)
        try:
            await asyncio.gather(
                _bot_loop(),
                _tpsl_loop(),
                _retry_close_loop(),
                _ob_snapshot_loop(),
                _pos_state_sync_loop(),
                return_exceptions=True,
            )
        finally:
            # 종료 시 aiohttp 세션 + connector 정리 (Unclosed client session/connector 경고 방지)
            # SIGTERM 등 비정상 종료 시에도 finally 블록은 실행됨
            try:
                if hasattr(self.engine, '_aio_session') and self.engine._aio_session:
                    if not self.engine._aio_session.closed:
                        await self.engine._aio_session.close()
                    # connector 별도 종료 (aiohttp 3.x: session.close()가 connector까지 닫지 않는 경우)
                    conn = getattr(self.engine._aio_session, '_connector', None)
                    if conn and not conn.closed:
                        await conn.close()
                    self.engine._aio_session = None
                    log.info("✅ aiohttp 세션+connector 정상 종료")
            except Exception as _cleanup_err:
                log.debug(f"aiohttp 종료 중 오류(무시): {_cleanup_err}")

    async def _loop(self):
        self._loop_cnt += 1

        # ── 1) 계좌 ──────────────────────────────────────────────────
        # 포지션 없을 때: ACCOUNT_TTL_IDLE(10분) TTL 적용으로 API 호출 최소화
        # 포지션 있을 때: ACCOUNT_TTL(120s) TTL 적용
        # 백오프 중: 캐시 반환 (rate limit 보호)
        acc   = await self.api.account()

        # ── liq_price REST 주기 갱신 (5분마다) ──────────────────────────
        # WHY: WS account_all의 liquidation_price = "" (항상 빈 문자열)
        #      entry × (1-imf/100) 공식은 Cross margin에서 오차 40~340%
        #      REST _fetch_rest()가 WS is_ready()로 우회되므로 별도 루프 필요
        _now_liq = time.time()
        if _now_liq - self._liq_sync_ts >= 300:  # 5분마다
            try:
                _rd = await self.api._fetch_rest()  # REST 직접 조회 (liq_price 주입 포함)
                if _rd:
                    self._liq_sync_ts = _now_liq
                    log.debug(f"📋 liq_price REST 갱신 완료")
            except Exception as _le:
                log.debug(f"liq_price REST 갱신 실패: {_le}")

        # ── 사이징 기준: WS user_stats 실시간 값 우선 (TTL 캐시 지연 제거)
        # WHY: api.account()는 최대 120s 캐시 → 손실/TP 직후 자본 반영 지연
        #      WS user_stats는 거래 즉시 갱신 → 항상 현재 자본 기준 사이징
        _ws_col   = WS_MANAGER._ws_collateral   # WS user_stats 실시간 collateral
        _ws_avail = WS_MANAGER._ws_available    # WS user_stats 실시간 available
        if _ws_col > 0:
            cap   = _ws_col
            avail = _ws_avail
        else:
            # WS 미수신 시 REST 캐시 폴백
            cap   = acc.get("collateral", acc.get("equity", 0))
            avail = acc.get("available", 0)
        if self._start_cap == 0 and cap > 0:
            self._start_cap = cap
        # 계좌 조회 실패 시 이전 캐시 자본으로 유지
        if cap == 0 and self.api._cache:
            cap   = self.api._cache.get("collateral", self.api._cache.get("equity", 0))
            avail = self.api._cache.get("available", 0)

        # ── 2) 포지션 동기화 (acc 재사용 — 추가 API 호출 없음) ───────
        await self.pm.sync(acc)

        # ── 2-1) WS 체결 이벤트 소비 → trade_history 실시간 기록 ────
        # WHY: account_all_trades WS에서 실시간으로 체결 이벤트 수신
        #      force_close 실패/봇 재시작 중 청산 → EXT 처리로 누락되던 문제 근본 해결
        #      REST API 호출 없이 WS 이벤트 기반으로 100% 기록
        _ws_t_queue = WS_MANAGER._ws_trades_queue
        _processed_wt = 0
        while _ws_t_queue:
            try:
                wt = _ws_t_queue.popleft()
            except IndexError:
                break
            try:
                # trade 필드 파싱 (Order 모델 기준)
                _wt_mid  = int(wt.get("market_index") or wt.get("market_id") or 0)
                _wt_sym  = MID_TO_SYMBOL.get(_wt_mid, wt.get("symbol") or f"mid{_wt_mid}")
                _wt_side_raw = str(wt.get("side", "")).upper()
                _wt_is_ask   = wt.get("is_ask")  # True=매도=SHORT
                if _wt_side_raw in ("LONG", "SHORT"):
                    _wt_side = _wt_side_raw
                elif _wt_is_ask is not None:
                    _wt_side = "SHORT" if _wt_is_ask else "LONG"
                else:
                    continue  # side 불명 → 스킵
                _wt_reduce  = bool(wt.get("reduce_only", False))
                _wt_price   = float(wt.get("base_price") or wt.get("price") or 0)
                _wt_size    = float(wt.get("base_size") or wt.get("size") or 0)
                _wt_status  = str(wt.get("status", "")).lower()
                # 미체결/취소 주문은 무시
                if _wt_status and _wt_status not in ("filled", "partial_filled", ""):
                    continue
                if _wt_price <= 0 or _wt_size <= 0:
                    continue

                # 청산 주문(reduce_only)만 trade_history에 기록
                # 진입 주문은 _execute_open → _order_scores에서 이미 처리
                if not _wt_reduce:
                    continue  # 신규 진입 주문은 스킵

                # 로컬 _pos에서 진입 정보 복원
                _pm_key = self.pm._key(_wt_mid, _wt_side)
                _pm_pos = self.pm._pos.get(_pm_key) or {}
                _entry  = float(_pm_pos.get("entry_price") or 0)
                _score  = int(_pm_pos.get("score") or 0) or self.learner.min_score
                _lev    = int(_pm_pos.get("leverage") or MARKET_LEVERAGE.get(_wt_mid, 10))
                _entry_ts = _pm_pos.get("entry_ts")

                if _entry <= 0:
                    log.debug(f"WS trade {_wt_sym} {_wt_side}: entry 없음 → 스킵")
                    continue

                # pnl 계산 (가격 기준)
                if _wt_side == "LONG":
                    _wt_pnl_pct = (_wt_price - _entry) / _entry
                else:
                    _wt_pnl_pct = (_entry - _wt_price) / _entry
                _wt_pnl_usd = _wt_pnl_pct * _entry * _wt_size * _lev

                # exit_type 추론: pnl 기준
                _atp = float(_pm_pos.get("adaptive_tp") or TP_PCT)
                _asl = float(_pm_pos.get("adaptive_sl") or SL_PCT)
                if _wt_pnl_pct >= _atp:
                    _exit_type = "TP"
                elif _wt_pnl_pct < -_asl * 1.5:
                    _exit_type = "SL"  # SL 초과 손실
                elif _wt_pnl_pct < -_asl * 0.5:
                    _exit_type = "SL"
                else:
                    _exit_type = "EXT"  # 기타 청산 (TSL 포함 확인 불가)

                # meta 구성
                _wt_meta = {k2: _pm_pos[k2] for k2 in TRADE_META_FIELDS if k2 in _pm_pos}
                if _entry_ts:
                    _wt_meta["hold_secs"] = int(time.time() - _entry_ts)
                _wt_meta["leverage"] = _lev
                _wt_meta["note"]     = "WS_account_all_trades"

                self.learner.record(
                    _wt_sym, _wt_side, _entry, _wt_price,
                    _score, _wt_pnl_pct, _exit_type, _wt_pnl_usd,
                    meta=_wt_meta
                )
                log.info(f"📝 WS체결 기록: {_wt_sym} {_wt_side} {_exit_type} "
                         f"pnl={_wt_pnl_pct*100:+.3f}% ${_wt_pnl_usd:+.2f}")
                _processed_wt += 1
            except Exception as _wte:
                log.warning(f"WS trade 처리 오류: {_wte}")
        if _processed_wt > 0:
            log.info(f"📝 WS체결 {_processed_wt}건 → trade_history 기록")

        # ── 3) 신호 스캔 (7TF 병렬, 캔들 캐시 생성) ─────────────────
        signals = await self.engine.scan_all(self.api)
        self._last_signals = signals   # /score 명령어용 캐시

        # /score 명령어용 캐시 파일 저장 (30초마다)
        _now_sc = time.time()
        if _now_sc - self._score_cache_ts > 30:
            try:
                _score_cache = []
                for _s in sorted(signals, key=lambda x: x.get("score_with_of", x.get("score",0)), reverse=True):
                    _score_cache.append({
                        "symbol":    _s.get("symbol","?"),
                        "side":      _s.get("side","?"),
                        "score":     _s.get("score_with_of", _s.get("score",0)),
                        "raw_score": _s.get("raw_score", _s.get("score",0)),
                        "bonus":     _s.get("bonus", 0),
                        "of_dir":    _s.get("of_dir","NEUTRAL"),
                        "of_imb":    round(float(_s.get("of_imb",0.5)),3),
                        "adx_regime":_s.get("adx_regime","?"),
                        "adx":       _s.get("adx",0),
                        "rsi":       _s.get("rsi",50),
                        "supertrend":_s.get("supertrend","?"),
                        "market_id": _s.get("market_id",0),
                        "bull_score":_s.get("bull_score",0),
                        "bear_score":_s.get("bear_score",0),
                        "has_4h":    _s.get("has_4h", False),
                        "has_1h":    _s.get("has_1h", False),
                        "bb_pos":    _s.get("bb_pos","?"),
                        "atr_pct":   _s.get("atr_pct", 0),
                        "adaptive_tp": _s.get("adaptive_tp", 0),
                        "adaptive_sl": _s.get("adaptive_sl", 0),
                        "cur_price": self.engine._last_prices.get(_s.get("market_id",0), 0),  # 현재가 (cmd_handler uPnL 계산용)
                    })
                import pathlib as _pl
                _sc_path = _pl.Path("logs/score_cache.json")
                # acc에서 equity/avail/collateral 추출 (cmd_handler REST 없이 포지션 표시용)
                _acc_sc = acc or {}
                # acc는 get_account() 반환: equity/available/collateral 키
                _eq_sc  = float(_acc_sc.get("equity") or _acc_sc.get("total_asset_value") or 0)
                _av_sc  = float(_acc_sc.get("available") or _acc_sc.get("available_balance") or 0)
                _col_sc = float(_acc_sc.get("collateral") or 0)
                # WS uPnL 합산
                _upnl_sc = sum(
                    float(wp.get("unrealized_pnl") or 0)
                    for wp in WS_MANAGER._positions.values()
                    if abs(float(wp.get("position",0))) > 1e-9
                )
                _sc_path.write_text(json.dumps({
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "total": len(signals),
                    "equity": _eq_sc,
                    "avail":  _av_sc,
                    "collateral": _col_sc,
                    "upnl": _upnl_sc,
                    "signals": _score_cache,
                }, ensure_ascii=False))
                self._score_cache_ts = _now_sc
            except Exception as _e:
                pass

        # 4h 상태 플래그만 learner에 전달 (min_score 자동 변경 없음 — 노이반님 직접 지시)
        has_4h = any(s.get("has_4h") for s in signals) if signals else False
        self.learner._has_4h = has_4h

        # ── 4) TP/SL — 독립 _tpsl_loop()에서 처리 (scan_all과 완전 분리)

        n_pos = len(self.pm._pos)

        # ── 5) 상태 로그 (10루프마다) ────────────────────────────────
        if self._loop_cnt % 10 == 0:
            wr          = self.learner.win_rate(50)
            util_pct    = ((cap - avail) / cap * 100) if cap > 0 else 0
            session_roi = ((cap - self._start_cap) / self._start_cap * 100) if self._start_cap else 0
            bl_status = DYNAMIC_BL.status()
            bl_str    = f" 🚫BL:{list(bl_status.keys())}" if bl_status else ""
            log.info(
                f"💰 ${cap:.2f}({session_roi:+.2f}%) avail=${avail:.2f} util={util_pct:.0f}% "
                f"pos={n_pos} | W:{self.pm.stats['tp']} L:{self.pm.stats['sl']} "
                f"WR:{wr:.0f}% | sc≥{self.learner.min_score} R:R=3:1{bl_str}"
            )

            # ── Daily Equity 저장 (KST 00:00 기준 — /pos 일일 수익률용) ──
            # WHY: /pos 수익률을 START_CAP(누적) 기준이 아닌 오늘 00:00 KST 기준으로 표시
            import datetime as _dt
            _KST = _dt.timezone(_dt.timedelta(hours=9))
            _today = _dt.datetime.now(_KST).strftime('%Y-%m-%d')
            if self._daily_eq_saved != _today and cap > 0:
                _deq_file = "logs/daily_equity.json"
                try:
                    _deq = json.loads(open(_deq_file).read())
                except:
                    _deq = {}
                if _today not in _deq:
                    _deq[_today] = round(cap, 2)
                    open(_deq_file, 'w').write(json.dumps(_deq, indent=2))
                    log.info(f"📅 daily_equity 저장: {_today} = ${cap:.2f}")
                self._daily_eq_saved = _today

        # ── 6) 리밸런싱 ──────────────────────────────────────────────
        now = time.time()
        has_positions = len(self.pm._pos) > 0

        # 리밸 트리거: avail 부족 + 포지션 있을 때 + REBAL 쿨다운 경과 + 첫 sync 완료 후
        # FIX: 5초 → REBAL_COOLDOWN_SEC(120초) 통일 (_rebal_last_ts 이중관리 제거)
        if signals and has_positions and now - self._last_rebal >= REBAL_COOLDOWN_SEC and self.pm._startup_sync_done:
            rebal_cnt = await self.pm.rebalance(signals, avail, cap)
            self._last_rebal = now
            if rebal_cnt:
                # 리밸 후 즉시 avail 갱신 — 캐시 무효화 대신 ACCOUNT_TTL 단축
                # (백오프 중이면 캐시 그대로 사용 — rate limit 보호 우선)
                if self.api._rl_backoff <= 0:
                    self.api._cache_ts = now - ACCOUNT_TTL + 5  # 5초 후 갱신 허용
                    acc2 = await self.api.account()
                    if acc2 and acc2.get("available", 0) > 0:
                        avail = acc2["available"]

        # ── 7) 신규 진입 ─────────────────────────────────────────────
        # util 체크: (collateral - avail) / collateral >= MAX_CAPITAL_USAGE이면 차단
        # WHY: MAX_CAPITAL_USAGE=0.85는 포지션 사이즈 계산에만 쓰이고
        #      총 util 상한 체크가 없어서 98%까지 올라가는 버그 수정
        _collateral_now = cap  # cap = collateral 기준
        _util_now = (_collateral_now - avail) / _collateral_now if _collateral_now > 0 else 1.0
        if _util_now >= MAX_CAPITAL_USAGE:
            log.debug(f"⛔ 신규 진입 차단: util={_util_now:.1%} >= MAX_CAPITAL_USAGE={MAX_CAPITAL_USAGE:.0%}")
        elif signals and avail > get_min_avail(cap):
            await self._open_signals(signals, cap, avail)

        # ── 8) 데이터 분석 (5분마다, 파라미터 변경 없음) ────────────
        if self.learner.should_analyse():
            self.learner.analyse()

        # ── 8-1) Auto-Upgrade (15분마다) ─────────────────────────────
        if self.learner.should_upgrade():
            self.learner.auto_upgrade()
            # Paper trade stats 리포트 (업그레이드 타이밍에 함께 출력)
            ps = self.paper_trader.stats()
            if ps.get("total", 0) >= 3:
                log.info(f"📄 Paper Stats: {ps['total']}건 WR={ps['wr']:.0f}% "
                         f"({ps['wins']}W/{ps['losses']}L)")
                for reason, d in ps.get("by_reason", {}).items():
                    c = d['w']+d['l']
                    wr_r = d['w']/c*100 if c else 0
                    mark = "✅" if wr_r > 40 else "❌"
                    log.info(f"  {mark} [{reason}]: {c}건 WR={wr_r:.0f}% "
                             f"→ {'차단 재검토 필요!' if wr_r > 40 else '차단 유효'}")

        # ── 8-1) 외부 min_score 변경 감지 (파일 → 메모리 동기화) ─────
        if self._loop_cnt % 10 == 0:  # 30초마다 체크
            try:
                with open(LEARNED_FILE) as _f:
                    d = json.load(_f)
                file_ms = int(d.get("min_score", DEFAULT_MIN_SCORE))
                if file_ms != self.learner.min_score:
                    log.info(f"🎯 min_score 외부 변경 감지: {self.learner.min_score} → {file_ms}")
                    self.learner.min_score = file_ms
            except Exception:
                pass

        # ── 9) 성과 기록 (20루프마다) ────────────────────────────────
        if self._loop_cnt % 20 == 0:
            self._save_perf(cap)

    async def _open_signals(self, signals: List[Dict], cap: float, avail: float):
        # 6초마다 신호 현황 로그
        if self._loop_cnt % 3 == 0:
            enterable_trend = [s for s in signals
                               if s.get("adx_regime","trend")=="trend"
                               and (s.get("of_dir","NEUTRAL")!="NEUTRAL"
                                    or s.get("score_with_of", s.get("score",0)) >= 95)
                               and s.get("score_with_of", s.get("score",0)) >= self.learner.min_score]
            enterable_transition = [s for s in signals
                               if s.get("adx_regime","trend")=="transition"
                               and s.get("score_with_of", s.get("score",0)) >= self.learner.min_score + 5]
            enterable_range = [s for s in signals
                               if s.get("adx_regime","trend")=="range"
                               and s.get("score",0) >= RANGE_MIN_SCORE]   # OF=NEUTRAL 허용
            enterable = enterable_trend + enterable_transition + enterable_range
            top3 = sorted(signals, key=lambda x: x.get("score_with_of", x.get("score",0)), reverse=True)[:3]
            top3_str = " | ".join(f"{s['symbol']}({s.get('side','?')},sc={s.get('score_with_of',s.get('score'))},OF={s.get('of_dir','?')},reg={s.get('adx_regime','?')})" for s in top3)
            log.info(f"📡 scan={len(signals)}개 진입가능={len(enterable)}개 | {top3_str}")
        """
        ★ 중복 주문 방지 규칙 (노이반님 v2 설계):

        [포지션 없는 경우]
          신호 1회 → 즉시 진입 (루프 내 동일 마켓 중복만 차단)

        [포지션 있는 경우 - 같은 방향]
          신호 감지 → _add_signal_armed[key] = True 세팅 (대기)
          다음 루프에서 armed=True & 신호 재확인 → 추가 진입

        [포지션 있는 경우 - 반대 방향]
          기존 포지션 즉시 청산 → 같은 루프에서 신규 방향 진입
        """
        # avail은 _loop()에서 이미 받은 값 사용 (추가 API 호출 없음)
        # 루프 내 중복 방지: _pending_order는 ORDER_MIN_GAP(1.6s) 보다 긴 쿨다운으로 대체
        # → clear_loop_pending() 제거 (루프 간 중복을 cooldown이 차단)

        # 이미 진입한 마켓 추적 (이번 _open_signals 호출 내 중복 차단)
        entered_mids: set = set()

        for sig in signals:
            if avail <= get_min_avail(cap): break

            # ── util 상한 재체크 (매 진입마다) ──────────────────────
            # WHY: _loop() 진입부에서 1회만 체크하면 연속 진입 시 util이 85% 초과됨
            #      avail은 진입 성공 시 -= self._last_margin으로 갱신되므로 정확함
            _util_realtime = (cap - avail) / cap if cap > 0 else 1.0
            if _util_realtime >= MAX_CAPITAL_USAGE:
                log.debug(f"⛔ _open_signals util 초과: {_util_realtime:.1%} >= {MAX_CAPITAL_USAGE:.0%}")
                break

            mid   = sig["market_id"]
            side  = sig["side"]
            score = sig["score"]
            price = sig["price"]
            sym   = sig["symbol"]

            # ── OrderFlow 통합 점수 (score_with_of) 사용 ──────────
            # score_with_of = score + of_bonus (오더플로우 방향 일치 시 +N, 역방향 시 -20)
            # 기존 min_score 기준은 score_with_of 기준으로 판단
            # (학습 데이터 오염 방지: learner.record에는 원본 score 저장)
            score_effective = sig.get("score_with_of", score)
            of_conflict = sig.get("of_conflict", False)
            of_dir = sig.get("of_dir", "NEUTRAL")

            # OF NEUTRAL: REST API rate limit으로 OB 조회 불가 시 진입 허용 (패널티 없음)
            # 이유: REST Too Many Requests → OF 항상 NEUTRAL → 진입 완전 차단 방지
            min_threshold = self.learner.min_score

            # 공통 차단
            if mid in entered_mids:                  continue   # 이번 open_signals 내 중복
            # ADX regime별 전략 분기
            # trend: 추세 추종 (기존), range: BB반전 (신규), transition: 혼합
            sig_regime = sig.get("adx_regime", "trend")
            if sig_regime == "range":
                # Range 모드: 별도 min_score + OF 선택적 허용
                min_threshold = RANGE_MIN_SCORE
                # Range에서 OF=NEUTRAL이어도 BB/RSI 조건 충분하면 진입 허용
                # (횡보장에서 OF 신호 약함 → NEUTRAL이 정상)
                of_dir_ok = True  # Range에서 OF 체크 완화
            elif sig_regime == "transition":
                score_effective = max(0, score_effective - 10)  # 10pt 패널티
                # transition: 점수 높으면 OF=NEUTRAL도 허용
                of_dir_ok = of_dir != "NEUTRAL" or score_effective >= 85
            else:
                # trend: 점수 95이상이면 OF=NEUTRAL도 허용 (강한 신호)
                of_dir_ok = of_dir != "NEUTRAL" or score_effective >= 95
            if self.pm.in_cooldown(mid):             continue
            if score <= 0:                           continue   # score=0 절대 진입 금지
            if score_effective < min_threshold:
                # 점수 미달 차단 → paper_trader에 등록 (score가 충분히 있을 때만)
                if score >= 70:
                    self.paper_trader.open(sig, f"score_low({score_effective}<{min_threshold})")
                continue
            # bad_hours 차단: 데이터 부족으로 비활성 (노이반님 결정 18:53 KST 03/14)
            # 실거래 클린 데이터 충분히 쌓이면 재활성화 예정
            # OF 역방향 차단 (Range는 완화: OF 역방향도 신호 약하면 허용)
            if of_conflict and of_dir != "NEUTRAL" and sig_regime != "range":
                self.paper_trader.open(sig, f"of_conflict({of_dir})")
                continue
            # Range/Trend OF 방향 체크
            if sig_regime == "trend" and not of_dir_ok:
                self.paper_trader.open(sig, f"of_neutral_trend(sc={score_effective})")
                continue

            # ── ST=UP+trend 차단 비활성 (2026-03-13 노이반님 지시) ──────
            # 오염된 데이터 기반 차단이었을 가능성 → 해제 후 신규 데이터로 재검증
            # (이전: ST=UP+trend 11건 WR=0% → 차단했으나 데이터 신뢰성 의문)
            st_dir = sig.get("supertrend", "?")
            existing = self.pm.get_position(mid)

            # ── Case A: 포지션 없음 → 즉시 진입 ──────────────────
            if existing is None:
                # ── WS 역방향 포지션 사전 차단 (교차 포지션 방지) ──────────
                # WHY: _pos에 없어도 WS(거래소 실제)에 반대 방향이 있으면 교차 포지션 발생
                #      JUP/AAVE 사례: WS에 LONG 있는데 SHORT 진입 → 양방향 SL 폭주
                # 해결: Case A 진입 전 WS 역방향 체크 → 감지 시 차단 (sync가 방향전환 처리)
                _ws_opp = WS_MANAGER._positions.get(str(mid), {})
                _ws_opp_sz = abs(float(_ws_opp.get("position", 0)))
                if _ws_opp_sz > 1e-9:
                    _ws_opp_side = "LONG" if int(_ws_opp.get("sign", 1)) > 0 else "SHORT"
                    if _ws_opp_side != side:
                        log.warning(f"⛔ [Case A] {sym} {side}: WS에 반대 방향 {_ws_opp_side}({_ws_opp_sz:.4g}) 감지 → 교차 포지션 방지 차단")
                        entered_mids.discard(mid)
                        self.pm._cooldown.pop(mid, None)
                        continue
                # 재시작 후 첫 sync 완료 전까지 신규 진입 차단
                # WHY: sync 전 포지션 파악 불완전 → 중복 주문 / 의도치 않은 포지션 생성 방지
                if not self.pm._startup_entry_allowed:
                    if self._loop_cnt % 5 == 0:
                        log.info(f"⏳ 재시작 후 sync 대기 중 — 신규 진입 차단: {sym} {side}")
                    continue
                entered_mids.add(mid)   # 이번 _open_signals 내 중복 차단
                # 루프 간 중복 차단: 진입 시도 전 cooldown 선점
                self.pm._cooldown[mid] = time.time() + COOLDOWN_SEC
                atp  = sig.get("adaptive_tp")
                asl  = sig.get("adaptive_sl")
                # Range 모드: 전용 TP/SL 적용 (좁은 목표, 높은 승률)
                if sig_regime == "range":
                    atp = RANGE_TP_PCT
                    asl = RANGE_SL_PCT
                mmlt = sig.get("margin_mult", 1.0)
                # 🏆 스노우볼: 자본 성장에 따른 포지션 비중 자동 확대 (복리 구조)
                # WHY: 연승 기반은 SL 맞으면 큰 손실 → 자본 비례가 올바른 복리
                # cap_ratio는 _calc_margin()에서 이미 자본별 단계적으로 적용됨
                # 여기서는 자본이 목표 구간 넘을 때 추가 부스트만 적용
                _snowball_mult = 1.0
                if cap >= 1000:   _snowball_mult = 1.2   # $1K 돌파: 20% 부스트
                if cap >= 2000:   _snowball_mult = 1.3   # $2K 돌파: 30% 부스트
                if cap >= 5000:   _snowball_mult = 1.5   # $5K 돌파: 50% 부스트
                if cap >= 10000:  _snowball_mult = 2.0   # $10K 돌파: 2배 부스트
                if _snowball_mult > 1.0:
                    mmlt = min(mmlt * _snowball_mult, 3.0)
                    log.info(f"🏆 스노우볼 ${cap:.0f} → margin_mult={mmlt:.1f}x")
                self._last_sig = sig   # ← 진입 지표 임시 저장 (execute_open → order_scores)
                # OF 정보 로깅
                of_bonus = sig.get("of_bonus", 0)
                of_d     = sig.get("of_dir", "NEUTRAL")
                of_imb   = sig.get("of_imb", 0.5)
                if of_d != "NEUTRAL":
                    log.info(f"📊 OF[{of_d}] imb={of_imb:.2f} bonus={of_bonus:+d} sc:{score}→{score_effective}")
                # ── ADX/ST 버그 수정: _execute_open 전에 sig를 미리 저장 ──
                # _last_sig는 여러 심볼 연속 처리 시 덮어써질 수 있으므로,
                # 진입 key를 미리 계산해 order_scores에 직접 저장
                _pre_key = self.pm._key(mid, side)
                _now_entry_ts = time.time()
                _entry_hour_kst = (__import__('datetime').datetime.now(
                    __import__('datetime').timezone(
                        __import__('datetime').timedelta(hours=9)
                    )
                ).hour)
                _order_meta_new = {
                    "score":              score_effective,  # OF 포함 최종 점수 (score_with_of)
                    "score_base":         sig.get("score", score_effective),  # OF 적용 전 원점수
                    "score_with_of":      score_effective,  # 명시적 저장 (Optimizer 재계산용)
                    "ts":                 _now_entry_ts,
                    # ── RSI ──────────────────────────────────────────────
                    "rsi":                sig.get("rsi"),
                    # ── ADX / Regime ─────────────────────────────────────
                    "adx":                sig.get("adx"),
                    "adx_regime":         sig.get("adx_regime"),
                    "adx_trend_thresh":   sig.get("adx_trend_thresh", ADX_TREND_THRESH),
                    "adx_range_thresh":   sig.get("adx_range_thresh", ADX_RANGE_THRESH),
                    # ── Supertrend ───────────────────────────────────────
                    "supertrend":         sig.get("supertrend"),
                    # ── BB ───────────────────────────────────────────────
                    "bb_pos":             sig.get("bb_pos"),
                    "bb_pos_ratio":       sig.get("bb_pos_ratio"),
                    "bb_upper":           sig.get("bb_upper"),
                    "bb_lower":           sig.get("bb_lower"),
                    "bb_mid":             sig.get("bb_mid"),
                    "bb_width_pct":       sig.get("bb_width_pct"),
                    # ── EMA 다중 TF 정렬 (bull/bear_score → trend_diff) ─
                    "bull_score":         sig.get("bull_score"),   # EMA 다중TF 상승 정렬점수
                    "bear_score":         sig.get("bear_score"),   # EMA 다중TF 하락 정렬점수
                    "has_4h":             sig.get("has_4h"),       # 4h 데이터 유무
                    "has_1h":             sig.get("has_1h"),       # 1h 데이터 유무
                    # ── OF ───────────────────────────────────────────────
                    "of_dir":             sig.get("of_dir", "UNKNOWN"),
                    "of_bonus":           sig.get("of_bonus", 0),
                    "of_imb":             sig.get("of_imb", 0.5),
                    "entry_ts":           _now_entry_ts,
                    "entry_hour_kst":     _entry_hour_kst,
                }
                self.pm._order_scores[_pre_key] = _order_meta_new
                # ── _strategy_meta 즉시 등록 (주문 성공 전이지만 임시로) ──
                # WHY: 주문 후 즉시 재시작되어도 score 복원 가능
                #      주문 실패 시 sync()의 WS 교차검증으로 자동 제거됨
                self.pm._strategy_meta[_pre_key] = {
                    "score":              score_effective,
                    "score_base":         sig.get("score", score_effective),
                    "score_with_of":      score_effective,
                    "entry_ts":           _now_entry_ts,
                    "entry_hour_kst":     _entry_hour_kst,
                    "opened_at":          _now_entry_ts,
                    "peak_pnl":           0.0,
                    "add_count":          0,
                    "adaptive_tp":        atp,
                    "adaptive_sl":        asl,
                    "rsi":                sig.get("rsi"),
                    "adx":                sig.get("adx"),
                    "adx_regime":         sig.get("adx_regime"),
                    "adx_trend_thresh":   sig.get("adx_trend_thresh", ADX_TREND_THRESH),
                    "adx_range_thresh":   sig.get("adx_range_thresh", ADX_RANGE_THRESH),
                    "supertrend":         sig.get("supertrend"),
                    "bb_pos":             sig.get("bb_pos"),
                    "bb_pos_ratio":       sig.get("bb_pos_ratio"),
                    "bb_upper":           sig.get("bb_upper"),
                    "bb_lower":           sig.get("bb_lower"),
                    "bb_mid":             sig.get("bb_mid"),
                    "bb_width_pct":       sig.get("bb_width_pct"),
                    "bull_score":         sig.get("bull_score"),
                    "bear_score":         sig.get("bear_score"),
                    "has_4h":             sig.get("has_4h"),
                    "has_1h":             sig.get("has_1h"),
                    "of_dir":             sig.get("of_dir", "UNKNOWN"),
                    "of_bonus":           sig.get("of_bonus", 0),
                    "of_imb":         sig.get("of_imb", 0.5),
                }
                self.pm._meta_dirty = True
                ok = await self._execute_open(mid, side, score_effective, price, sym, cap, avail,
                                              adaptive_tp=atp, adaptive_sl=asl,
                                              margin_mult=mmlt,
                                              min_score_gate=min_threshold)  # 주문 직전 score 재확인
                # 진입 성공 시 OF 캐시 무효화 (즉각적인 포지션 반영)
                if ok:
                    OF_ENGINE.invalidate(mid)
                if ok:
                    avail -= self._last_margin
                else:
                    entered_mids.discard(mid)
                    self.pm._cooldown.pop(mid, None)
                continue

            existing_side = existing["side"]

            # ── Case B: 포지션 있음 + 같은 방향 ─────────────────
            if existing_side == side:
                key = self.pm._key(mid, side)
                add_count = existing.get("add_count", 0)

                # 재시작 후 sync 전 추가진입도 차단
                if not self.pm._startup_entry_allowed:
                    continue

                # 추가 진입은 마켓당 최대 1회만 허용
                if add_count >= 1:
                    continue

                # 추가 진입 쿨다운 중이면 차단
                if self.pm.is_add_cooldown(mid, side):
                    continue

                # ── 추가 진입 (피라미딩) 조건 ────────────────────────
                # 수익 구간에 따라 진입 기준 차별화:
                #
                #  [수익 우대 케이스] TP의 30%+ 수익 중 → 스노우볼 가속
                #    - score 기준 완화 (min_score 이상이면 허용)
                #    - 1루프 확인만으로 즉시 진입 (2루프 대기 생략)
                #    - 추가 진입 마진을 기존의 1.2배로 확대
                #
                #  [중립 케이스] 본전 근처 (-0.06% ~ +TP*30%)
                #    - score >= min_score + 5 필요
                #    - 2루프 연속 확인 후 진입 (허위 신호 필터)
                #
                #  [차단 케이스] 손실 -0.06% 초과
                #    - 물타기 절대 차단

                entry_price = existing.get("entry_price") or 0
                atp = existing.get("adaptive_tp") or TP_PCT

                # entry_price=0 이면 추가진입 절대 차단 (SL 판단 불가)
                if entry_price <= 0:
                    self.pm._add_signal_armed[key] = False
                    log.warning(f"⛔ 추가진입 차단 (entry_price 없음): {sym} {side}")
                    continue

                cur_pnl = (price - entry_price) / entry_price if side == "LONG" else (entry_price - price) / entry_price

                # 손실 중 물타기 절대 차단 (SL_PCT*0.15 = -0.06% 이상 손실이면 차단)
                if cur_pnl < -SL_PCT * 0.15:
                    self.pm._add_signal_armed[key] = False
                    if self._loop_cnt % 5 == 0:
                        log.info(f"⛔ 추가진입 차단: {sym} {side} pnl={cur_pnl*100:+.3f}%")
                    continue

                profit_riding = cur_pnl >= atp * 0.3   # TP의 30% 이상 수익 중

                if profit_riding:
                    # ── 수익 우대: 즉시 추가 진입 (1루프 확인으로 충분) ──
                    if score < self.learner.min_score:
                        self.pm._add_signal_armed[key] = False
                        continue
                    self.pm._add_signal_armed[key] = False
                    self.pm.set_add_cooldown(mid, side, secs=ADD_COOLDOWN_SEC)
                    log.info(f"🚀🚀 추가진입[수익중+{cur_pnl*100:+.2f}%]: {sym} {side} sc={score}")
                    ok = await self._execute_open(mid, side, score, price, sym, cap, avail,
                                                  label="비중↑", is_add=True, margin_mult=1.2)
                    if ok:
                        avail -= self._last_margin
                        self.pm._pending_order.add(mid)
                        # add_count 증가 (최대 1회 제한 보장)
                        key2 = self.pm._key(mid, side)
                        if key2 in self.pm._pos:
                            self.pm._pos[key2]["add_count"] = self.pm._pos[key2].get("add_count", 0) + 1
                            self.pm._save_pos_state()
                            log.info(f"📌 add_count={self.pm._pos[key2]['add_count']} ({sym} {side})")
                else:
                    # ── 중립: 2루프 확인 후 진입 ──────────────────────
                    add_min_score = self.learner.min_score + 5
                    if score < add_min_score:
                        self.pm._add_signal_armed[key] = False
                        continue

                    if not self.pm._add_signal_armed.get(key, False):
                        self.pm._add_signal_armed[key] = True
                        log.info(f"📶 {sym} {side} 추가신호 sc={score} 감지 → 다음 루프 추가진입 대기")
                    else:
                        self.pm._add_signal_armed[key] = False
                        self.pm.set_add_cooldown(mid, side, secs=ADD_COOLDOWN_FAIL_SEC)
                        ok = await self._execute_open(mid, side, score, price, sym, cap, avail,
                                                      label="추가", is_add=True)
                        if ok:
                            avail -= self._last_margin
                            self.pm._pending_order.add(mid)
                            # add_count 증가 (최대 1회 제한 보장)
                            key2 = self.pm._key(mid, side)
                            if key2 in self.pm._pos:
                                self.pm._pos[key2]["add_count"] = self.pm._pos[key2].get("add_count", 0) + 1
                                self.pm._save_pos_state()
                                log.info(f"📌 add_count={self.pm._pos[key2]['add_count']} ({sym} {side})")
                continue

            # ── Case C: 포지션 있음 + 반대 방향 → 청산 후 신규 진입
            opp_key = self.pm._key(mid, existing_side)
            log.info(f"🔀 {sym} 방향전환: {existing_side}→{side} (청산 후 신규)")
            closed = await self.pm._atomic_close(opp_key, "SL")
            if closed:
                await asyncio.sleep(0.5)
                # 청산 직후 쿨다운 없이 신규 방향 진입 (방향 전환이므로)
                self.pm._cooldown.pop(mid, None)
                ok = await self._execute_open(mid, side, score, price, sym, cap, avail, label="전환")
                if ok:
                    avail -= self._last_margin
                    self.pm._pending_order.add(mid)

    async def _execute_open(self, mid: int, side: str, score: int, price: float,
                            sym: str, cap: float, avail: float,
                            label: str = "", is_add: bool = False,
                            adaptive_tp: float = None, adaptive_sl: float = None,
                            margin_mult: float = 1.0,
                            min_score_gate: int = 0) -> bool:
        """실제 주문 실행 공통 함수. 성공 시 _last_margin 갱신."""
        # volume quota 블록 중이면 진입 즉시 차단 (SDK 호출 자체 안 함 → quota 보호)
        import builtins as _bi
        _quota_until = getattr(_bi, '_QUOTA_BLOCK_UNTIL', 0)
        if time.time() < _quota_until:
            remain = int(_quota_until - time.time())
            if remain % 20 == 0 or remain <= 5:
                log.warning(f"⏸️ volume quota 블록 중 ({remain}초) — {sym} SDK 호출 차단")
            return False
        if not score or score < DEFAULT_MIN_SCORE:
            log.warning(f"⛔ score 미달 주문 차단: {sym} {side} sc={score} < {DEFAULT_MIN_SCORE} (절대 하한선)")
            return False
        # ── 주문 직전 score 재확인 (Race Condition 방지) ────────────────
        # WHY: _open_signals에서 score 체크 후 신호값이 바뀔 수 있음
        #      (scan 주기와 진입 결정 사이 타이밍 차이)
        # min_score_gate: 호출자가 전달한 진입 당시 min_threshold
        if min_score_gate > 0 and score < min_score_gate:
            log.warning(f"⛔ {sym} {side} score 재확인 차단: sc={score} < gate={min_score_gate} (신호 변경 감지)")
            return False

        leverage = MARKET_LEVERAGE.get(mid, 10)
        margin   = self._calc_margin(score, cap, avail, leverage)
        if margin <= 0:
            return False
        # ── get_confidence() 실제 적용: score_bucket WR기반 사이즈 조절 ──
        # confidence: 데이터 부족 시 0.85(보수적), 고WR 버킷 1.3(공격적)
        conf = self.learner.get_confidence(score)
        if conf != 0.85:  # 기본값 아닐 때만 적용 (데이터 있을 때)
            margin = margin * conf
            log.debug(f"📊 confidence={conf:.2f} → margin 조정 ${margin:.2f}")
        # ADX 전환기 margin 축소 (0.7x)
        if margin_mult != 1.0:
            margin = margin * margin_mult
        # 최소 마진 $3 미만 진입 차단 (소액 포지션 방지)
        if margin < MIN_MARGIN_USD:
            log.warning(f"⛔ {sym} 마진 ${margin:.2f} < ${MIN_MARGIN_USD:.0f} (MIN_MARGIN_USD) → 진입 차단")
            return False

        pos_value = margin * leverage
        size      = pos_value / price
        if size <= 0:
            return False

        # ── WS 포지션 사전 확인 (주문 전 최신 상태 검증) ────────────
        # WHY: _pos는 로컬 상태 — WS와 불일치 시 중복 주문 발생 가능
        #      WS _positions를 최종 권위로 사용 (REST 호출 없음)
        _ws_pre = WS_MANAGER._positions.get(str(mid), {})
        _ws_pre_sz = abs(float(_ws_pre.get("position", 0)))
        if _ws_pre_sz > 1e-9 and not is_add:
            _ws_pre_side = "LONG" if int(_ws_pre.get("sign", 1)) > 0 else "SHORT"
            if _ws_pre_side == side:
                log.warning(f"⛔ {sym} {side}: WS에 이미 포지션({_ws_pre_sz:.4g}) 있음 → 신규 진입 차단 (중복 방지)")
                return False
            else:
                # ── 반대 방향 포지션 있음 → 교차 포지션 방지 차단 ──────────────
                # WHY: WS에 LONG 있는데 SHORT 진입 → 교차 포지션 → 양방향 SL 동시 발동
                #      volume quota 폭발 + SL 실패 패턴의 근본 원인
                # 해결: 진입 차단 → sync()에서 반대 방향 감지 → Case C 방향전환으로 처리
                log.warning(f"⛔ {sym} {side}: WS에 반대 방향 {_ws_pre_side} 포지션({_ws_pre_sz:.4g}) 있음 → 교차 포지션 방지 차단")
                return False

        # 오더북 유동성 체크 (WS OB 기반) — 빈 오더북은 진입 차단
        ob = WS_MANAGER.get_ob(mid)
        if ob is not None:
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            if len(bids) == 0 and len(asks) == 0:
                log.warning(f"⛔ {sym} 오더북 비어있음 → 진입 차단 (유동성 없음)")
                DYNAMIC_BL.manual_add(sym, BLACKLIST_TTL_SEC)
                return False
            # 스프레드 체크: 5% 초과 시 차단
            if bids and asks:
                best_bid = float(bids[0]["price"])
                best_ask = float(asks[0]["price"])
                if best_bid > 0:
                    spread_pct = (best_ask - best_bid) / best_bid
                    if spread_pct > 0.05:  # 5% 초과 스프레드
                        log.warning(f"⛔ {sym} 스프레드 {spread_pct*100:.1f}% 초과 → 진입 차단")
                        return False

        # ideal_price 직접 전달 → get_best_price API 호출 절감
        ideal_int = price_to_int(mid, price) if price > 0 else None
        ok = await self.api.open_order(mid, side, size, price, ideal_price_int=ideal_int)
        if ok:
            import math as _math
            sc_ratio = min(score / MAX_SCORE, 1.0)
            lev_disc = 1.0 / _math.sqrt(max(leverage, LEV_DISCOUNT_BASE) / LEV_DISCOUNT_BASE)
            tag    = f"[{label}] " if label else ""
            atp_s  = f" atp={adaptive_tp*100:.2f}%" if adaptive_tp else ""
            asl_s  = f" asl={adaptive_sl*100:.2f}%" if adaptive_sl else ""
            mmlt_s = f" [ADX×{margin_mult:.1f}]" if margin_mult != 1.0 else ""
            conf_s = f" conf={conf:.2f}" if conf != 0.85 else ""
            log.info(
                f"🚀 {sym} {side} {tag}sc={score} "
                f"sr={sc_ratio:.2f}×ld={lev_disc:.2f} val=${pos_value:.1f}({leverage}x) "
                f"m=${margin:.2f} avail→${avail-margin:.2f}{atp_s}{asl_s}{mmlt_s}{conf_s}"
            )
            key_chk = self.pm._key(mid, side)
            self.pm._order_scores[key_chk] = {
                "score":       score,
                "ts":          time.time(),
                "adaptive_tp": adaptive_tp,   # sync()에서 포지션으로 복원
                "adaptive_sl": adaptive_sl,   # sync()에서 포지션으로 복원
                "entry_price": price,          # sync() 전 entry_price 보험
            }
            # 진입 지표 임시 저장 → sync() 에서 포지션에 복원
            sig = self._last_sig or {}
            if sig:
                # FIX: of_dir/of_bonus/of_imb/entry_hour_kst 누락 → 통계 오염 수정
                from datetime import timezone as _tz, timedelta as _td
                _kst_h = datetime.now(_tz(_td(hours=9))).hour
                self.pm._order_scores[key_chk].update({
                    "rsi":          sig.get("rsi"),
                    "adx":          sig.get("adx"),
                    "adx_regime":   sig.get("adx_regime"),
                    "supertrend":   sig.get("supertrend"),
                    "bb_pos":       sig.get("bb_pos"),
                    "entry_ts":     time.time(),
                    "of_dir":       sig.get("of_dir", "UNKNOWN"),  # 진입 시 OF 방향 보존
                    "of_bonus":     sig.get("of_bonus", 0),
                    "of_imb":       sig.get("of_imb", 0.5),
                    "entry_hour_kst": _kst_h,
                    # OB 수치 (진입 시점 스냅샷 — Optimizer 피처)
                    "ob_bid_vol":    sig.get("ob_bid_vol", 0.0),
                    "ob_ask_vol":    sig.get("ob_ask_vol", 0.0),
                    "ob_spread_pct": sig.get("ob_spread_pct", 0.0),
                    "ob_best_bid":   sig.get("ob_best_bid", 0.0),
                    "ob_best_ask":   sig.get("ob_best_ask", 0.0),
                })
            # FIX: 진입 성공 즉시 _pos에 임시 등록 (WS sync 전 orphan 루프 방지)
            # WS에서 포지션 반영까지 ~10초 걸림 → 그 사이 orphan 오감지 차단
            key_pos = self.pm._key(mid, side)
            if key_pos not in self.pm._pos:
                # 진입 시 신호 지표 완전 보존 (Optimizer 활용)
                # WHY: WS sync 전 청산 시에도 지표가 누락되지 않도록 _order_scores에서 복원
                _tmp_oscores = self.pm._order_scores.get(key_chk, {})
                self.pm._pos[key_pos] = {
                    "market_id":   mid,
                    "symbol":      sym,
                    "side":        side,
                    "size":        size,
                    "entry_price": price,
                    "liq_price":   0.0,  # WS sync 후 갱신
                    "score":       score,
                    "opened_at":   time.time(),
                    "add_count":   0,
                    "peak_pnl":    0.0,
                    "adaptive_tp": adaptive_tp or TP_PCT,
                    "adaptive_sl": adaptive_sl or SL_PCT,
                    "entry_ts":    time.time(),
                    # 진입 신호 지표 전체 복사 (모든 TRADE_META_FIELDS)
                    "rsi":            _tmp_oscores.get("rsi"),
                    "adx":            _tmp_oscores.get("adx"),
                    "adx_regime":     _tmp_oscores.get("adx_regime"),
                    "supertrend":     _tmp_oscores.get("supertrend"),
                    "bb_pos":         _tmp_oscores.get("bb_pos"),
                    "bb_pos_ratio":   _tmp_oscores.get("bb_pos_ratio"),
                    "bb_upper":       _tmp_oscores.get("bb_upper"),
                    "bb_lower":       _tmp_oscores.get("bb_lower"),
                    "bb_mid":         _tmp_oscores.get("bb_mid"),
                    "bb_width_pct":   _tmp_oscores.get("bb_width_pct"),
                    "bull_score":     _tmp_oscores.get("bull_score"),
                    "bear_score":     _tmp_oscores.get("bear_score"),
                    "has_4h":         _tmp_oscores.get("has_4h"),
                    "has_1h":         _tmp_oscores.get("has_1h"),
                    "of_dir":         _tmp_oscores.get("of_dir", "UNKNOWN"),
                    "of_bonus":       _tmp_oscores.get("of_bonus", 0),
                    "of_imb":         _tmp_oscores.get("of_imb", 0.5),
                    "ob_bid_vol":     _tmp_oscores.get("ob_bid_vol", 0.0),
                    "ob_ask_vol":     _tmp_oscores.get("ob_ask_vol", 0.0),
                    "ob_spread_pct":  _tmp_oscores.get("ob_spread_pct", 0.0),
                    "ob_best_bid":    _tmp_oscores.get("ob_best_bid", 0.0),
                    "ob_best_ask":    _tmp_oscores.get("ob_best_ask", 0.0),
                    "score_base":     _tmp_oscores.get("score_base"),
                    "score_with_of":  _tmp_oscores.get("score_with_of"),
                    "entry_hour_kst": _tmp_oscores.get("entry_hour_kst"),
                    "params_ver":     _tmp_oscores.get("params_ver"),
                }
                log.debug(f"📌 {sym} {side} _pos 임시 등록 (WS sync 대기 중, 지표 완전 보존)")

            # ── trade_history 진입 레코드 저장 (entry_only) ──────────
            # WHY: 청산 시 entry_ts/score 복원을 위해 진입 시점 즉시 기록
            #      exit_ts=None → cmd_handler에서 open 포지션 식별 가능
            try:
                _entry_ts_now = datetime.now().isoformat()
                _entry_rec = {
                    "ts":            _entry_ts_now,
                    "exit_ts":       None,
                    "symbol":        sym,
                    "side":          side,
                    "entry":         price,
                    "entry_price":   price,
                    "entry_ts":      _entry_ts_now,
                    "exit":          None,
                    "exit_price":    None,
                    "score":         score,
                    "pnl_pct":       None,
                    "pnl":           None,
                    "win":           None,
                    "exit_type":     None,
                    "adaptive_tp":   adaptive_tp or TP_PCT,
                    "adaptive_sl":   adaptive_sl or SL_PCT,
                    "leverage":      leverage,
                    "adx_regime":    sig.get("adx_regime") if sig else None,
                    "entry_hour_kst": datetime.now(timezone(timedelta(hours=9))).hour,
                }
                self.learner.trade_hist.append(_entry_rec)
                self.learner._save()
            except Exception as _e_entry_rec:
                log.debug(f"entry_rec 저장 실패: {_e_entry_rec}")

            self.pm.set_cooldown(mid)   # 진입 후 COOLDOWN_SEC 동안 재진입 차단
            self.pm.stats["opened"] += 1
            self._last_margin = margin
            return True
        self._last_margin = 0.0
        return False

    def _save_perf(self, cap: float):
        tot = self.pm.stats["tp"] + self.pm.stats["sl"]
        wr  = (self.pm.stats["tp"] / tot * 100) if tot > 0 else 0.0
        pct = (cap - self._start_cap) / self._start_cap * 100 if self._start_cap else 0
        tgt = self._start_cap * 1.195 if self._start_cap else 0
        icon = "🟢" if pct >= 0 else "🔴"
        log.info(f"{icon} Capital: ${cap:.2f} ({pct:+.2f}%) | Target: ${tgt:.2f} | WR:{wr:.0f}%")
        try:
            with open(PERF_FILE, "w") as f:
                json.dump({
                    "ts":         datetime.now().isoformat(),
                    "capital":    round(cap, 2),
                    "start":      round(self._start_cap, 2),
                    "pct":        round(pct, 2),
                    "target":     round(tgt, 2),
                    "wr":         round(wr, 1),
                    "tp":         self.pm.stats["tp"],
                    "sl":         self.pm.stats["sl"],
                    "opened":     self.pm.stats["opened"],
                    "min_score":  self.learner.min_score,
                }, f, indent=2)
        except: pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Entry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _update_market_map_from_rest():
    """REST 레버리지 교차검증 — 심볼은 WS primary, 레버리지만 REST에서 보정
    
    WHY: 시작 시 REST 호출 금지 (429 유발)
         WS market_stats가 심볼의 primary source (0~169 전 마켓 구독)
         REST는 레버리지(initial_margin_fraction) 교차검증 전용
         → 이 함수는 봇 시작 시 호출하지 않음
         → REST 교차검증(_fetch_rest) 성공 시 레버리지만 갱신
    """
    pass  # 시작 시 REST 호출 제거 — WS market_stats가 primary


def main():
    Path(PID_FILE).write_text(str(os.getpid()))
    log.info(f"PID {os.getpid()} → {PID_FILE}")
    # 마켓 심볼: WS market_stats/0~169 구독으로 수집 (시작 시 REST 호출 없음)
    # 레버리지: REST 5분 교차검증 시 initial_margin_fraction에서 갱신

    # ── 시작 시 불량 심볼 동적 블랙리스트 등록 ──
    # 재시작해도 TTL이 초기화되지 않도록 파일로 시작 시간 보존
    import os as _os
    _BL_TS_FILE = "logs/.startup_bl_ts.json"
    _bl_now = time.time()
    try:
        with open(_BL_TS_FILE) as _f:
            _bl_ts = json.load(_f)
    except:
        _bl_ts = {}
    _registered = []
    for sym in STARTUP_BLACKLIST:
        if sym not in _bl_ts:
            _bl_ts[sym] = _bl_now  # 첫 등록 시각
        _elapsed = _bl_now - _bl_ts[sym]
        _remain  = BLACKLIST_TTL_SEC - _elapsed
        if _remain > 0:
            DYNAMIC_BL.manual_add(sym, ttl_sec=int(_remain))
            _registered.append(f"{sym}({_remain/3600:.1f}h)")
        else:
            log.info(f"✅ STARTUP_BL 해제: {sym} (TTL 만료)")
    # 만료된 심볼 제거
    _bl_ts = {k: v for k, v in _bl_ts.items() if _bl_now - v < BLACKLIST_TTL_SEC}
    try:
        with open(_BL_TS_FILE, "w") as _f:
            json.dump(_bl_ts, _f)
    except: pass
    if _registered:
        log.info(f"🚫 STARTUP_BL {len(_registered)}개 (잔여TTL 유지): {', '.join(_registered)}")
    else:
        log.info(f"✅ STARTUP_BL 전부 만료 — 모든 심볼 해제됨")

    def _exit(sig, frame):
        log.info("Shutdown signal")
        sys.exit(0)
    signal.signal(signal.SIGTERM, _exit)
    signal.signal(signal.SIGINT, _exit)

    asyncio.run(TradingBot().run())

if __name__ == "__main__":
    main()
