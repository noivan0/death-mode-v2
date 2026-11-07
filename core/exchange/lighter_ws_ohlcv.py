"""
LighterOHLCVWs — Lighter WebSocket OHLCV (캔들) 구독 모듈
===========================================================
서버 지원 확인된 채널:
  candle/{market_id}/{resolution}
  예: candle/1/5m → BTC 5분 캔들

지원 Resolution:
  1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d

마켓 ID:
  ETH=0, BTC=1, SOL=2, XRP=7, TON=12, HYPE=24, SUI=16,
  VVV=69, ZEC=90, LIT=120

사용법:
  ws = LighterOHLCVWs(
      market_ids=[0, 1, 2],       # ETH, BTC, SOL
      resolution="5m",
      on_candle=my_callback,      # fn(market_id, candle_dict)
      on_candle_closed=my_cb,     # fn(market_id, candle_dict) — 봉 완성 시
  )
  asyncio.run(ws.run_async())

Freqtrade 통합:
  LighterFreqtradeDataFeed — exchange.watchOHLCV() 대체
"""

import asyncio
import json
import logging
import ssl
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────────────────────────────
WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"

MARKET_ID_MAP = {
    "ETH":  0,
    "BTC":  1,
    "SOL":  2,
    "XRP":  7,
    "TON":  12,
    "SUI":  16,
    "HYPE": 24,
    "VVV":  69,
    "ZEC":  90,
    "LIT":  120,
}

RESOLUTION_SECONDS = {
    "1m":  60,
    "5m":  300,
    "15m": 900,
    "30m": 1800,
    "1h":  3600,
    "4h":  14400,
    "12h": 43200,
    "1d":  86400,
}

# ── 데이터 클래스 ─────────────────────────────────────────────────────────────
@dataclass
class OHLCVCandle:
    """단일 캔들 데이터"""
    timestamp: int          # ms epoch
    open:  float
    high:  float
    low:   float
    close: float
    volume: float
    is_closed: bool = False

    def to_list(self) -> List:
        """Freqtrade 표준 [ts, o, h, l, c, v] 형식"""
        return [self.timestamp, self.open, self.high, self.low, self.close, self.volume]


# ── 핵심 WS 클라이언트 ────────────────────────────────────────────────────────
class LighterOHLCVWs:
    """
    Lighter WebSocket OHLCV 구독 클라이언트

    - 자동 재연결 (exponential backoff, 최대 60초)
    - 봉 완성 감지 (다음 봉 시작 시 이전 봉 closed=True 콜백)
    - 메모리 관리: 페어당 최대 max_candles 유지
    - ping/pong 자동 처리
    """

    def __init__(
        self,
        market_ids: List[int],
        resolution: str = "5m",
        on_candle: Optional[Callable] = None,
        on_candle_closed: Optional[Callable] = None,
        max_candles: int = 500,
        verify_ssl: bool = True,    # [SECURITY FIX] 기본값 True — HMG 환경만 False
    ):
        if resolution not in RESOLUTION_SECONDS:
            raise ValueError(f"지원하지 않는 resolution: {resolution}. 지원: {list(RESOLUTION_SECONDS.keys())}")

        self.market_ids = market_ids
        self.resolution = resolution
        self.on_candle = on_candle
        self.on_candle_closed = on_candle_closed
        self.max_candles = max_candles
        self.verify_ssl = verify_ssl

        self.res_secs = RESOLUTION_SECONDS[resolution]

        # 내부 상태
        self._candles: Dict[int, List[OHLCVCandle]] = defaultdict(list)
        self._current_candle: Dict[int, Optional[OHLCVCandle]] = defaultdict(lambda: None)
        self._ws = None
        self._running = False
        self._reconnect_delay = 1.0
        self._stop_event: asyncio.Event = asyncio.Event()  # stop() 즉시 반응용

    # ── 퍼블릭 API ───────────────────────────────────────────────────────────

    def get_candles(self, market_id: int) -> List[List]:
        """[ts, o, h, l, c, v] 리스트 반환 (Freqtrade 표준)"""
        return [c.to_list() for c in self._candles[market_id]]

    def get_latest_candle(self, market_id: int) -> Optional[List]:
        """현재 진행중인 캔들 반환"""
        c = self._current_candle[market_id]
        return c.to_list() if c else None

    async def run_async(self):
        """무한 실행 루프 (재연결 포함)"""
        self._running = True
        self._stop_event.clear()
        while self._running:
            try:
                await self._connect_and_run()
                self._reconnect_delay = 1.0  # 성공 시 리셋
            except Exception as e:
                if not self._running:
                    break
                log.warning(f"[LighterOHLCVWs] 연결 오류: {e}. {self._reconnect_delay:.1f}초 후 재연결...")
                # [BUG FIX] asyncio.sleep 대신 Event 대기 → stop() 즉시 반응
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._reconnect_delay,
                    )
                    break  # stop() 호출로 Event 설정됨
                except asyncio.TimeoutError:
                    pass   # 정상 backoff 경과 → 재연결
                self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)

    async def stop(self):
        self._running = False
        self._stop_event.set()   # [BUG FIX] 재연결 sleep 즉시 해제
        if self._ws:
            await self._ws.close()

    # ── 내부 연결 로직 ────────────────────────────────────────────────────────

    async def _connect_and_run(self):
        import websockets

        ssl_ctx = None
        if not self.verify_ssl:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            log.critical("[LighterOHLCVWs] ⚠️ SSL 검증 비활성화 — HMG 환경 한정 사용")

        log.info(f"[LighterOHLCVWs] 연결 시도: {WS_URL}")
        async with websockets.connect(WS_URL, ssl=ssl_ctx, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            log.info("[LighterOHLCVWs] 연결 성공")
            self._reconnect_delay = 1.0

            # 초기화 대기 (connected 메시지)
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") != "connected":
                log.warning(f"[LighterOHLCVWs] 예상치 못한 첫 메시지: {msg}")

            # 구독
            for mid in self.market_ids:
                sub = {"type": "subscribe", "channel": f"candle/{mid}/{self.resolution}"}
                await ws.send(json.dumps(sub))
                log.info(f"[LighterOHLCVWs] 구독: candle/{mid}/{self.resolution}")

            # 메시지 루프
            async for raw_msg in ws:
                if not self._running:
                    break
                try:
                    await self._handle_message(json.loads(raw_msg))
                except Exception as e:
                    log.error(f"[LighterOHLCVWs] 메시지 처리 오류: {e} | raw={raw_msg[:200]}")

    async def _handle_message(self, msg: dict):
        mtype = msg.get("type", "")

        if mtype == "ping":
            if self._ws:
                await self._ws.send(json.dumps({"type": "pong"}))
            return

        # API update (Oct 2026): rolling deploys emit graceful shutdown
        # {"type":"shutdown","close_in_ms":7350} then close code 1012
        if mtype == "shutdown":
            close_in_ms = float(msg.get("close_in_ms") or 5000)
            wait_s = max(0.0, close_in_ms / 1000.0)
            log.warning(
                f"[LighterOHLCVWs] server shutdown in {wait_s:.2f}s — "
                "closing for reconnect (parallel connections recommended)"
            )
            await asyncio.sleep(min(wait_s, 30.0))
            if self._ws:
                await self._ws.close()
            return

        if mtype in ("subscribed/candle", "update/candle"):
            await self._process_candle_msg(msg)
        elif mtype == "error":
            log.error(f"[LighterOHLCVWs] 서버 에러: {msg}")
        else:
            log.debug(f"[LighterOHLCVWs] 미처리 메시지: {mtype}")

    async def _process_candle_msg(self, msg: dict):
        """
        서버 응답 형식 (실측):
          구독: {"type":"subscribe","channel":"candle/1/5m"}  → 슬래시
          응답: channel="candle:1:5m"                        → 콜론!
          subscribed/candle:
            { type, channel: "candle:1:5m",
              candles: [{t, o, h, l, c, v, V, i}, ...] }
          update/candle:
            { type, channel: "candle:1:5m",
              candles: [{t, o, h, l, c, v, V, i}] }  ← candles 배열 (candle 단수 아님)
        """
        channel = msg.get("channel", "")
        # 콜론(:) 또는 슬래시(/) 모두 지원
        sep = ":" if ":" in channel else "/"
        parts = channel.split(sep)
        if len(parts) < 3:
            return
        market_id = int(parts[1])

        # subscribed — 초기 캔들 히스토리 로드
        if msg["type"] == "subscribed/candle":
            candles_data = msg.get("candles", [])
            log.info(f"[LighterOHLCVWs] market_id={market_id} 초기 캔들 {len(candles_data)}개 수신")
            # [BUG FIX] 마지막 봉(현재 진행 중)은 _current_candle로 설정
            # 히스토리에 넣으면 update/candle 첫 전환 시 중복 저장됨
            for raw_c in candles_data[:-1]:
                candle = self._parse_candle(raw_c)
                self._add_candle(market_id, candle)
            if candles_data:
                last = self._parse_candle(candles_data[-1])
                self._current_candle[market_id] = last

        # update — 현재 봉 업데이트 (candles 배열 or candle 단수)
        elif msg["type"] == "update/candle":
            # 실측: candles 배열로 옴
            candles_data = msg.get("candles", [])
            if not candles_data:
                raw_c = msg.get("candle", {})
                candles_data = [raw_c] if raw_c else []
            if not candles_data:
                return
            new_candle = self._parse_candle(candles_data[-1])  # 최신 캔들
            await self._update_current_candle(market_id, new_candle)

    def _parse_candle(self, raw: dict) -> OHLCVCandle:
        """서버 raw candle → OHLCVCandle"""
        # t: timestamp (서버에 따라 초 또는 ms)
        ts = int(raw.get("t", raw.get("timestamp", 0)))
        if ts < 1e12:  # 초 단위면 ms로 변환
            ts = ts * 1000

        return OHLCVCandle(
            timestamp=ts,
            open=float(raw.get("o", raw.get("open", 0))),
            high=float(raw.get("h", raw.get("high", 0))),
            low=float(raw.get("l", raw.get("low", 0))),
            close=float(raw.get("c", raw.get("close", 0))),
            volume=float(raw.get("v", raw.get("volume", 0))),
        )

    async def _update_current_candle(self, market_id: int, new_candle: OHLCVCandle):
        """현재 봉 업데이트 + 봉 완성 감지"""
        prev = self._current_candle[market_id]

        if prev is None:
            # 첫 번째 캔들
            self._current_candle[market_id] = new_candle
            if self.on_candle:
                self.on_candle(market_id, new_candle)
            return

        # 새 봉 시작 감지 (timestamp 변경)
        if new_candle.timestamp != prev.timestamp:
            # 이전 봉 완성
            prev.is_closed = True
            self._add_candle(market_id, prev)
            log.debug(f"[LighterOHLCVWs] market_id={market_id} 봉 완성: ts={prev.timestamp} close={prev.close}")
            if self.on_candle_closed:
                self.on_candle_closed(market_id, prev)

        # 현재 봉 업데이트
        self._current_candle[market_id] = new_candle
        if self.on_candle:
            self.on_candle(market_id, new_candle)

    def _add_candle(self, market_id: int, candle: OHLCVCandle):
        """히스토리에 추가, max_candles 초과 시 오래된 것 제거"""
        self._candles[market_id].append(candle)
        if len(self._candles[market_id]) > self.max_candles:
            self._candles[market_id] = self._candles[market_id][-self.max_candles:]


# ── Freqtrade DataFeed 통합 ───────────────────────────────────────────────────
class LighterFreqtradeDataFeed:
    """
    Freqtrade exchange.watchOHLCV() 대체 레이어

    사용법 (LighterExchange에 통합):
      feed = LighterFreqtradeDataFeed(pairs=["BTC/USDC:USDC", ...], timeframe="5m")
      await feed.start()
      candles = await feed.watch_ohlcv("BTC/USDC:USDC", "5m")
    """

    # Freqtrade pair → Lighter market_id 매핑
    PAIR_TO_MARKET_ID = {
        f"{sym}/USDC:USDC": mid
        for sym, mid in MARKET_ID_MAP.items()
    }

    def __init__(
        self,
        pairs: List[str],
        timeframe: str = "5m",
        verify_ssl: bool = True,    # [SECURITY FIX] 기본값 True
    ):
        self.pairs = pairs
        self.timeframe = timeframe

        # pair → market_id 해석
        self.market_ids = []
        self._pair_to_mid: Dict[str, int] = {}
        for pair in pairs:
            mid = self.PAIR_TO_MARKET_ID.get(pair)
            if mid is None:
                # 심볼로 fallback 시도 (BTC/USDC:USDC → BTC)
                sym = pair.split("/")[0]
                mid = MARKET_ID_MAP.get(sym)
            if mid is not None:
                self.market_ids.append(mid)
                self._pair_to_mid[pair] = mid
            else:
                log.warning(f"[LighterFreqtradeDataFeed] 미지원 페어: {pair}")

        self._mid_to_pair: Dict[int, str] = {v: k for k, v in self._pair_to_mid.items()}

        # WS 클라이언트
        self._ws_client = LighterOHLCVWs(
            market_ids=self.market_ids,
            resolution=timeframe,
            on_candle_closed=self._on_closed,
            verify_ssl=verify_ssl,
        )

        # watchOHLCV 큐 (pair → asyncio.Queue)
        # [BUG FIX] maxsize=10 지정 → QueueFull 예외 활성화 (maxsize=0이면 put_nowait에서 QueueFull 절대 미발생)
        self._queues: Dict[str, asyncio.Queue] = {p: asyncio.Queue(maxsize=10) for p in pairs}
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """백그라운드 WS 태스크 시작"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._ws_client.run_async())
            log.info(f"[LighterFreqtradeDataFeed] 시작: {self.pairs}")

    async def stop(self):
        await self._ws_client.stop()
        if self._task:
            self._task.cancel()

    async def watch_ohlcv(
        self,
        pair: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 200,
    ) -> List[List]:
        """
        Freqtrade exchange.watchOHLCV() 시그니처 호환

        봉 완성 이벤트 대기 → [ts, o, h, l, c, v] 리스트 반환
        """
        mid = self._pair_to_mid.get(pair)
        if mid is None:
            raise ValueError(f"미지원 페어: {pair}")

        # 큐에서 완성된 봉 가져오기 (timeout 6분 — 5m봉 완성 대기)
        try:
            candle = await asyncio.wait_for(
                self._queues[pair].get(),
                timeout=360,
            )
        except asyncio.TimeoutError:
            # [IMPROVEMENT] 타임아웃 시 미완성 봉 반환 금지 → 빈 리스트 반환
            # 미완성 봉을 Freqtrade에 전달하면 잘못된 신호 계산 가능
            log.warning(f"[LighterFreqtradeDataFeed] {pair} watchOHLCV timeout — 빈 리스트 반환")
            return []

        # 히스토리 포함
        history = self._ws_client.get_candles(mid)[-limit:]
        return history

    def _on_closed(self, market_id: int, candle: OHLCVCandle):
        """봉 완성 콜백 → 큐에 추가"""
        pair = self._mid_to_pair.get(market_id)
        if pair and pair in self._queues:
            try:
                self._queues[pair].put_nowait(candle.to_list())
            except asyncio.QueueFull:
                log.warning(f"[LighterFreqtradeDataFeed] {pair} 큐 가득참 — 오래된 항목 제거")
                try:
                    self._queues[pair].get_nowait()
                    self._queues[pair].put_nowait(candle.to_list())
                except Exception:
                    pass


# ── 테스트 / 독립 실행 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )

    async def demo():
        print("=== Lighter WS OHLCV 테스트 ===")
        print(f"BTC(1), ETH(0), SOL(2) 5m 캔들 구독 시작...")

        received = []

        def on_candle(mid, c):
            sym = {0:"ETH",1:"BTC",2:"SOL"}.get(mid, str(mid))
            print(f"  [{sym}] 업데이트: ts={c.timestamp} O={c.open:.2f} H={c.high:.2f} L={c.low:.2f} C={c.close:.2f} V={c.volume:.4f}")

        def on_closed(mid, c):
            sym = {0:"ETH",1:"BTC",2:"SOL"}.get(mid, str(mid))
            print(f"✅ [{sym}] 봉 완성: ts={c.timestamp} close={c.close:.2f}")
            received.append((mid, c))

        ws = LighterOHLCVWs(
            market_ids=[0, 1, 2],
            resolution="5m",
            on_candle=on_candle,
            on_candle_closed=on_closed,
            verify_ssl=False,  # 테스트 전용 — 프로덕션에서는 True
        )

        # 30초 테스트
        task = asyncio.create_task(ws.run_async())
        await asyncio.sleep(30)
        await ws.stop()
        task.cancel()

        print(f"\n수신된 완성 봉: {len(received)}개")
        for mid, c in received:
            sym = {0:"ETH",1:"BTC",2:"SOL"}.get(mid, str(mid))
            print(f"  {sym}: {c.to_list()}")

    asyncio.run(demo())
