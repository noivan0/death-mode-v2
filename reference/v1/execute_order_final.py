#!/usr/bin/env python3
"""
Order Executor - Singleton SignerClient (DEATH MODE v4.7)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
주문 방식: create_market_order_limited_slippage()
  - ORDER_TYPE_MARKET + IOC = 순수 시장가 주문
  - ideal_price 전달 시 OB API 호출 없음 → 빠름
  - SDK 내부에서 worst_price = ideal_price × (1 ± max_slippage) 자동 계산
  - standard 수수료 = 무료, 슬리피지 2%만 관리
  - ideal_price 없으면 SDK가 OB API로 자동 조회 (폴백, 안전)

Rate Limit 최적화 (v4.7):
  - SignerClient 싱글턴: 주문당 nonce API 호출 제거 (3→1 req)
  - ideal_price 직접 전달: OB API 호출 제거 (1→0 req)
  - 결과: 주문 1건당 Lighter API = send_tx 1회만
  - 글로벌 Rate Limiter: 60초 40req → 35req 안전 마진 (청산 우선)
  - 청산 큐 직렬화: 동시 다중 SL HIT 시 순서대로 처리 (rate limit 폭발 방지)
"""
import sys, os, json, asyncio, time, random, warnings, logging
from collections import deque

# SSL Patch
import requests, functools, ssl
original_get, original_post = requests.get, requests.post
@functools.wraps(original_get)
def patched_get(*args, **kwargs):
    kwargs['verify'] = False
    return original_get(*args, **kwargs)
@functools.wraps(original_post)
def patched_post(*args, **kwargs):
    kwargs['verify'] = False
    return original_post(*args, **kwargs)
requests.get, requests.post = patched_get, patched_post
ssl._create_default_https_context = ssl._create_unverified_context

import aiohttp
original_connector = aiohttp.TCPConnector
class PatchedConnector(original_connector):
    def __init__(self, *args, **kwargs):
        kwargs['ssl'] = False
        super().__init__(*args, **kwargs)
aiohttp.TCPConnector = PatchedConnector

import lighter
warnings.filterwarnings('ignore')
logging.getLogger('aiohttp').setLevel(logging.CRITICAL)
os.environ['PYTHONWARNINGS'] = 'ignore'

# .env 로드
from pathlib import Path
env_file = Path(__file__).parent / '.env'
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

DECIMALS_FILE = os.path.join(os.path.dirname(__file__), 'decimals_map.json')
with open(DECIMALS_FILE) as f:
    DECIMALS_MAP = json.load(f)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SignerClient 싱글턴
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_client: lighter.SignerClient = None
_client_lock = asyncio.Lock() if False else None  # 런타임에 생성

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WS 주문 전송 싱글턴 (v4.8)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공식 문서: wss://mainnet.zklighter.elliot.ai/stream
#   jsonapi/sendtx      → 단건 주문
#   jsonapi/sendtxbatch → 배치 주문 (rate limit 0: 50개를 1번에!)
# WHY WS > REST: volume quota 동일하게 소진되지만
#   HTTP rate limit(40req/60s) 적용 안됨 → 청산 429 완전 해소
import websockets as _ws_lib
import ssl as _ssl_mod

_ws_order_conn = None      # WS 연결 객체
_ws_order_lock = None      # 연결 생성 락 (재연결 보호)
_ws_order_recv_task = None # 수신 태스크
_ws_pending: dict = {}     # {request_id: asyncio.Future}
_ws_req_counter = 0
_WS_ORDER_URL = "wss://mainnet.zklighter.elliot.ai/stream"

# ── WS 전송 직렬화 락 ─────────────────────────────────────────
# WHY: Lighter WS 응답에 request_id echo 없음 → 순서 기반 매칭
#      동시 전송 시 pending에 N개 등록 → 응답 잘못 라우팅 → 주문 충돌
#      batch_close + force_close 동시 실행 → JUP/AAVE 사례 원인
# 해결: 전송 자체를 직렬화 (한 번에 1개 전송 → pending 항상 1개)
#       청산(close) 우선 처리 위해 별도 close 락 큐 구조 대신
#       단순 전송 락으로 순서 보장 (asyncio는 단일 스레드 → 공정 스케줄)
_ws_send_lock = None       # WS 전송 직렬화 락

def _get_ws_lock():
    global _ws_order_lock
    if _ws_order_lock is None:
        _ws_order_lock = asyncio.Lock()
    return _ws_order_lock

def _get_ws_send_lock():
    global _ws_send_lock
    if _ws_send_lock is None:
        _ws_send_lock = asyncio.Lock()
    return _ws_send_lock

async def _ws_recv_loop(ws):
    """WS 수신 루프 — sendtx 응답 처리 (직렬화 전제: pending 항상 0~1개)"""
    global _ws_pending, _ws_order_conn
    _log = logging.getLogger(__name__)
    try:
        async for raw in ws:
            msg = json.loads(raw)
            if not _ws_pending:
                continue  # 대기 중인 주문 없음 → 무시 (account_all 등 다른 채널 메시지)
            # ① request_id로 정확 매칭 (서버가 echo하는 경우)
            rid = msg.get("request_id")
            if rid and rid in _ws_pending:
                fut = _ws_pending.pop(rid)
                if not fut.done():
                    fut.set_result(msg)
                continue
            # ② data.id로 매칭 시도 (당사 id echo 여부 확인)
            data_id = (msg.get("data") or {}).get("id") if isinstance(msg.get("data"), dict) else None
            if data_id:
                try:
                    rid_int = int(data_id)
                    if rid_int in _ws_pending:
                        fut = _ws_pending.pop(rid_int)
                        if not fut.done():
                            fut.set_result(msg)
                        continue
                except (ValueError, TypeError):
                    pass
            # ③ 직렬화 락 전제: pending에 항상 1개 → 순서 기반 매칭 안전
            #    (전송 락 덕분에 동시 전송 없음 → 잘못 매칭될 pending 없음)
            oldest_rid = next(iter(_ws_pending))
            fut = _ws_pending.pop(oldest_rid)
            if not fut.done():
                fut.set_result(msg)
    except Exception as e:
        _log.debug(f"WS recv loop 종료: {e}")
        _ws_order_conn = None

async def _hard_refresh_nonce_bg():
    """WS 재연결 후 백그라운드 nonce 동기화 (REST /nextNonce 호출)"""
    _log = logging.getLogger(__name__)
    try:
        await asyncio.sleep(0.5)  # WS 연결 안정화 대기
        client = await _get_client()
        api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '2'))
        # 동기 hard_refresh_nonce → run_in_executor로 비동기 래핑
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, client.nonce_manager.hard_refresh_nonce, api_key_index
        )
        new_nonce = client.nonce_manager.nonce.get(api_key_index, '?')
        _log.info(f"🔄 WS 재연결 → nonce REST 동기화 완료: key_idx={api_key_index} nonce={new_nonce}")
    except Exception as e:
        _log.warning(f"⚠️ WS 재연결 nonce 동기화 실패 (무해, 자동 재시도): {e}")

async def _get_ws_conn():
    """WS 주문 연결 싱글턴 (자동 재연결)"""
    global _ws_order_conn, _ws_order_recv_task
    lock = _get_ws_lock()
    async with lock:
        # websockets 16.x: ClientConnection은 .closed 없음 → .state로 확인 (OPEN=1)
        from websockets.connection import State as _WsState
        _ws_not_open = (_ws_order_conn is None or
                        getattr(_ws_order_conn, 'state', _WsState.CLOSED) != _WsState.OPEN)
        if _ws_not_open:
            _was_reconnect = (_ws_order_conn is not None)  # 재연결 여부 추적
            try:
                ssl_ctx = _ssl_mod.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = _ssl_mod.CERT_NONE
                _ws_order_conn = await _ws_lib.connect(
                    _WS_ORDER_URL, ping_interval=20, close_timeout=5, ssl=ssl_ctx
                )
                # connected 수신
                _connected = json.loads(await asyncio.wait_for(_ws_order_conn.recv(), timeout=5))
                # 수신 루프 시작
                if _ws_order_recv_task:
                    _ws_order_recv_task.cancel()
                _ws_order_recv_task = asyncio.ensure_future(_ws_recv_loop(_ws_order_conn))
                logging.getLogger(__name__).info("✅ WS 주문 채널 연결됨")
                # ── WS 재연결 시 nonce hard_refresh (invalid nonce 방지) ──
                # WHY: WS 끊김 후 재연결 시 서버 nonce와 로컬 nonce 불일치
                #      → "invalid tx info" / nonce 오류 → SL 실패 패턴 원인
                # 해결: hard_refresh_nonce() → REST /nextNonce로 서버 nonce 동기화
                #       (_client=None 방식은 다음 주문 시 0부터 재시작 → 동일 문제 반복)
                if _was_reconnect:
                    asyncio.ensure_future(_hard_refresh_nonce_bg())
            except Exception as e:
                _ws_order_conn = None
                raise
    return _ws_order_conn

async def _ws_send_tx(tx_type: int, tx_info: str, timeout: float = 8.0) -> dict:
    """WS로 단건 주문 전송 → 응답 반환 (직렬화 보장)"""
    global _ws_req_counter, _ws_pending
    # ── 전송 직렬화 락 획득 ────────────────────────────────────
    # WHY: Lighter WS 응답에 request_id echo 없음 → 순서 기반 매칭
    #      동시 전송 시 pending에 N개 → 응답 잘못 라우팅 → 주문 충돌
    # 락 타임아웃 = timeout + 연결 타임아웃(5s) + 여유(2s)
    send_lock = _get_ws_send_lock()
    try:
        await asyncio.wait_for(send_lock.acquire(), timeout=timeout + 7)
    except asyncio.TimeoutError:
        return {"error": {"code": -1, "message": "WS send lock timeout"}}
    rid = None
    try:
        ws = await asyncio.wait_for(_get_ws_conn(), timeout=5)
        _ws_req_counter += 1
        rid = _ws_req_counter
        fut = asyncio.get_running_loop().create_future()
        _ws_pending[rid] = fut
        # 공식 예제: tx_info는 json.loads()로 객체로 파싱해서 전달
        # (문자열로 보내면 invalid tx info 오류)
        msg = {
            "type": "jsonapi/sendtx",
            "data": {
                "id": str(rid),
                "tx_type": tx_type,
                "tx_info": json.loads(tx_info) if isinstance(tx_info, str) else tx_info,
            }
        }
        await ws.send(json.dumps(msg))
        resp = await asyncio.wait_for(fut, timeout=timeout)
        return resp
    except asyncio.TimeoutError:
        if rid is not None:
            _ws_pending.pop(rid, None)
        return {"error": {"code": -1, "message": "WS timeout"}}
    except Exception as e:
        if rid is not None:
            _ws_pending.pop(rid, None)
        return {"error": {"code": -1, "message": str(e)}}
    finally:
        if send_lock.locked():
            send_lock.release()

async def _ws_send_tx_batch(tx_types: list, tx_infos: list, timeout: float = 10.0) -> dict:
    """WS로 배치 주문 전송 → 응답 반환 (N개를 1번 API로, 직렬화 보장)"""
    global _ws_req_counter, _ws_pending
    # ── 전송 직렬화 락 획득 ────────────────────────────────────
    send_lock = _get_ws_send_lock()
    try:
        await asyncio.wait_for(send_lock.acquire(), timeout=timeout + 7)
    except asyncio.TimeoutError:
        return {"error": {"code": -1, "message": "WS batch send lock timeout"}}
    rid = None
    try:
        ws = await asyncio.wait_for(_get_ws_conn(), timeout=5)
        _ws_req_counter += 1
        rid = _ws_req_counter
        fut = asyncio.get_running_loop().create_future()
        _ws_pending[rid] = fut
        # 공식 예제: tx_types/tx_infos는 json.dumps(list) 문자열로, id는 선택
        # tx_infos의 각 항목은 json.loads로 파싱해서 객체 리스트로 만든 뒤 다시 dumps
        parsed_infos = [json.loads(ti) if isinstance(ti, str) else ti for ti in tx_infos]
        msg = {
            "type": "jsonapi/sendtxbatch",
            "data": {
                "id": str(rid),
                "tx_types": json.dumps(tx_types),
                "tx_infos": json.dumps(parsed_infos),
            }
        }
        await ws.send(json.dumps(msg))
        resp = await asyncio.wait_for(fut, timeout=timeout)
        return resp
    except asyncio.TimeoutError:
        if rid is not None:
            _ws_pending.pop(rid, None)
        return {"error": {"code": -1, "message": "WS batch timeout"}}
    except Exception as e:
        if rid is not None:
            _ws_pending.pop(rid, None)
        return {"error": {"code": -1, "message": str(e)}}
    finally:
        if send_lock.locked():
            send_lock.release()

def _get_pk():
    pk = os.getenv('LIGHTER_API_PRIVATE_KEY', '')
    if pk.startswith('0x'):
        pk = pk[2:]
    return pk

async def get_client() -> lighter.SignerClient:
    """싱글턴 SignerClient — 세션당 1회 생성, nonce 로컬 관리"""
    global _client, _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    async with _client_lock:
        if _client is None:
            pk = _get_pk()
            if not pk:
                raise RuntimeError('No API key')
            api_key_index  = int(os.getenv('LIGHTER_API_KEY_INDEX', '2'))
            account_index  = int(os.getenv('LIGHTER_ACCOUNT_INDEX', '1058'))
            _client = lighter.SignerClient(
                url="https://mainnet.zklighter.elliot.ai",
                api_private_keys={api_key_index: pk},
                account_index=account_index,
                # OPTIMISTIC: nonce를 로컬 카운터로 관리 (API 호출 없음)
                nonce_management_type=lighter.nonce_manager.NonceManagerType.OPTIMISTIC,
            )
        return _client

async def reset_client():
    """nonce 오류 시 클라이언트 재초기화 + REST nonce 재동기화"""
    global _client
    # ── hard_refresh_nonce: REST /nextNonce API로 정확한 nonce 취득 ──
    # WHY: _client = None 후 재생성 시 OPTIMISTIC nonce는 0에서 재시작
    #      서버 nonce > 0이면 다음 주문도 "invalid nonce" 반복
    # 해결: 기존 client가 있으면 hard_refresh_nonce 호출로 서버 동기화
    #       없으면 새 client 생성 후 refresh
    try:
        if _client is not None:
            api_key_index = int(os.getenv('LIGHTER_API_KEY_INDEX', '2'))
            _client.nonce_manager.hard_refresh_nonce(api_key_index)
            logging.getLogger(__name__).info(f"🔄 nonce hard_refresh 완료: key_idx={api_key_index} "
                f"nonce={_client.nonce_manager.nonce.get(api_key_index, '?')}")
            return  # 클라이언트 유지, nonce만 갱신
    except Exception as e:
        logging.getLogger(__name__).warning(f"⚠️ hard_refresh_nonce 실패 → client 재생성: {e}")
    _client = None  # 재생성 fallback

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 글로벌 Rate Limiter (v4.7 핵심 수정)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Lighter: 60초에 40req 허용 → 35req 안전 마진 (버퍼 5req)
# 청산(reduce_only) 우선: 슬롯 부족 시 진입 주문보다 우선 획득
_rl_lock: asyncio.Lock = None
_rl_timestamps: deque = deque()          # 최근 주문 타임스탬프
_rl_close_queue: asyncio.Queue = None    # 청산 전용 직렬화 큐
RATE_LIMIT_MAX = 35     # 60초 안전 한도 (40 - 5 버퍼)
RATE_LIMIT_WINDOW = 60  # 초

def _get_rl_lock():
    global _rl_lock
    if _rl_lock is None:
        _rl_lock = asyncio.Lock()
    return _rl_lock

async def _acquire_rate_slot(is_close: bool = False) -> None:
    """Rate limit 슬롯 획득 — 초과 시 대기 (청산 우선)"""
    global _rl_timestamps
    lock = _get_rl_lock()
    _log = logging.getLogger(__name__)

    while True:
        async with lock:
            now = time.time()
            # 60초 이전 타임스탬프 제거
            while _rl_timestamps and _rl_timestamps[0] < now - RATE_LIMIT_WINDOW:
                _rl_timestamps.popleft()

            if len(_rl_timestamps) < RATE_LIMIT_MAX:
                _rl_timestamps.append(now)
                return  # 슬롯 획득 성공

            # 슬롯 부족 → 가장 오래된 타임스탬프 기준 대기
            oldest = _rl_timestamps[0]
            wait_s = (oldest + RATE_LIMIT_WINDOW) - now + 0.1
            if wait_s > 0:
                kind = "청산" if is_close else "진입"
                _log.warning(f"⏳ Rate limit 대기 {wait_s:.1f}s [{kind}] (window={len(_rl_timestamps)}/{RATE_LIMIT_MAX})")

        # lock 밖에서 대기 (다른 coroutine이 슬롯 해제 가능)
        await asyncio.sleep(min(wait_s, 1.0))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 주문 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def execute_order(order_data: dict) -> dict:
    reduce_only = order_data.get('reduce_only', order_data.get('reduceOnly', False))

    # 최소 포지션 금액 체크 (진입만)
    if not reduce_only:
        size  = order_data['size']
        price = order_data.get('price', 0)
        if price > 0 and size * price < 5.0:
            return {'success': False, 'error': f'Position value ${size*price:.2f} below minimum $5'}

    market_id     = str(order_data['marketId'])
    size_decimals = DECIMALS_MAP.get(market_id, {}).get('size_decimals', 4)
    base_amount   = int(order_data['size'] * (10 ** size_decimals))

    is_long  = order_data['side'].upper() in ('BUY', 'LONG')
    if reduce_only:
        is_ask = is_long       # LONG 청산 → sell
    else:
        is_ask = not is_long   # LONG 진입 → buy

    idx = (int(time.time() * 1_000_000) + random.randint(0, 9999)) % (2**31)

    # ideal_price 취득 (integer, SDK 내부에서 슬리피지 계산에 사용)
    # 우선순위: 1) 호출자가 직접 전달한 ideal_price(int)
    #           2) 호출자가 전달한 price(float) → int 변환
    #           3) WS Candle Builder 최근 close → int 변환
    #           4) None → SDK가 OB API로 자동 조회 (느리지만 안전)
    market_id_str  = str(order_data['marketId'])
    price_decimals = DECIMALS_MAP.get(market_id_str, {}).get('price_decimals', 6)

    ideal_price_int = order_data.get('ideal_price')  # 이미 정수형이면 그대로

    if ideal_price_int is None and order_data.get('price', 0) > 0:
        ideal_price_int = int(float(order_data['price']) * (10 ** price_decimals))

    if ideal_price_int is None:
        import urllib.request as _ur, json as _json
        try:
            _mid = order_data['marketId']
            _req = _ur.Request(f"http://127.0.0.1:5001/api/candles/{_mid}/1m", headers={"User-Agent":"bot"})
            with _ur.urlopen(_req, timeout=2) as _r:
                _c = _json.loads(_r.read()).get('candles', [])
                if _c:
                    ideal_price_int = int(float(_c[-1]['close']) * (10 ** price_decimals))
        except:
            pass
    # ideal_price_int이 None이면 SDK가 OB API로 자동 조회 (폴백)

    # SL(reduce_only) 최대 15회 재시도 / 진입(open) 최대 2회 재시도
    # WHY 15회: volume_quota 소진 시 10+13+16+...s 대기하며 반드시 체결 (2026-03-13 NEAR 사례)
    max_attempts = 15 if reduce_only else 2
    last_err = "unknown"

    # Rate limit 슬롯 획득 (청산 우선, 진입은 슬롯 여유 있을 때만)
    # WS 전송도 volume quota는 소진되므로 슬롯 관리 유지
    await _acquire_rate_slot(is_close=reduce_only)

    for attempt in range(max_attempts):
        # 재시도마다 새 client_order_index (중복 주문 방지)
        if attempt > 0:
            idx = (int(time.time() * 1_000_000) + random.randint(0, 9999)) % (2**31)
            await _acquire_rate_slot(is_close=reduce_only)

        try:
            client = await get_client()

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # WS 전송 경로 (v4.8): sign → WS send
            # WHY: HTTP 429 rate limit 완전 우회
            #      volume quota는 WS도 동일하게 소진
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            if ideal_price_int is not None:
                # worst_price = ideal_price × (1 ± 2% slippage)
                max_slippage = 0.02
                worst_price = round(ideal_price_int * (1 + max_slippage * (-1 if is_ask else 1)))

                # nonce 명시 전달 → nextNonce REST 호출 없음 (TLS 오류 방지)
                # order_expiry=0 (DEFAULT_IOC_EXPIRY) → IOC 시장가 주문 필수
                _api_key_idx = int(os.getenv('LIGHTER_API_KEY_INDEX', '2'))
                _cur_nonce = client.nonce_manager.nonce.get(_api_key_idx, 0)
                _nonce = _cur_nonce + 1

                tx_type, tx_info, tx_hash_local, sign_err = client.sign_create_order(
                    market_index=order_data['marketId'],
                    client_order_index=idx,
                    base_amount=base_amount,
                    price=worst_price,
                    is_ask=int(is_ask),
                    order_type=lighter.SignerClient.ORDER_TYPE_MARKET,
                    time_in_force=lighter.SignerClient.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
                    reduce_only=reduce_only,
                    order_expiry=lighter.SignerClient.DEFAULT_IOC_EXPIRY,
                    nonce=_nonce,
                    api_key_index=_api_key_idx,
                )
                if tx_type is not None:
                    # 서명 성공 시 로컬 nonce 반영
                    client.nonce_manager.nonce[_api_key_idx] = _nonce
                if sign_err:
                    # 서명 실패 → REST 폴백
                    logging.getLogger(__name__).debug(f"WS sign 실패 → REST 폴백: {sign_err}")
                    ideal_price_int = None  # REST 폴백 시 OB 조회
                else:
                    # WS로 전송
                    ws_resp = await _ws_send_tx(tx_type, tx_info)
                    ws_err = ws_resp.get("error")
                    ws_result = ws_resp.get("result", {})
                    if ws_err:
                        ws_code = ws_err.get("code", -1)
                        ws_msg = ws_err.get("message", "")
                        last_err = f"WS tx error code={ws_code} msg={ws_msg}"
                        # volume quota 판정 (2026-03-14 Premium 계정 오파싱 수정)
                        # WHY: Premium 계정 정상 응답 = {"ratelimit": "10197 volume quota remained"}
                        #      Standard 계정 정상 응답 = {"ratelimit": "didn't use volume quota"}
                        #      → 'ratelimit' or 'quota' 단순 포함만으로 소진 판정은 오파싱
                        # 실제 소진 = "0 volume quota" or "exhausted" or quota_remaining=0
                        _ws_msg_l = ws_msg.lower()
                        _is_quota_exhausted = (
                            'exhausted' in _ws_msg_l or
                            ('quota' in _ws_msg_l and _ws_msg_l.strip().startswith('0 '))
                        )
                        if _is_quota_exhausted:
                            if not reduce_only:
                                logging.getLogger(__name__).warning(f"⚠️ WS volume quota 소진 → 진입 스킵")
                                return {'success': False, 'error': f'volume_quota_exhausted: {last_err}'}
                            # 청산(SL)은 quota 소진돼도 반드시 재시도 — 포기 금지
                            wait = 10 + attempt * 3
                            logging.getLogger(__name__).warning(f"⚠️ WS volume quota → {wait}s 대기 후 SL 재시도 (청산 포기 금지)")
                            await asyncio.sleep(wait)
                            continue
                        # 'ratelimit' 메시지 = quota 잔여 정보 (정상) → 로그만
                        if 'ratelimit' in _ws_msg_l:
                            logging.getLogger(__name__).debug(f"WS ratelimit 정보: {ws_msg}")
                        # nonce 오류
                        if 'nonce' in ws_msg.lower() or ws_code in (21502, 21503):
                            await reset_client()
                            await asyncio.sleep(0.3)
                            continue
                        # 기타 WS 오류 → REST 폴백
                        if attempt == 0:
                            logging.getLogger(__name__).debug(f"WS 오류 → REST 폴백: {ws_msg}")
                            ideal_price_int = None
                            # REST로 재시도 (continue 대신 fall through)
                        elif reduce_only and attempt < max_attempts - 1:
                            await asyncio.sleep(1)
                            continue
                        else:
                            return {'success': False, 'error': last_err}
                    else:
                        # WS 성공
                        tx_hash_str = (ws_result.get("hash") or
                                       ws_result.get("tx_hash") or
                                       ws_result.get("txHash") or "ws_ok")
                        logging.getLogger(__name__).debug(f"✅ WS 주문 성공: {tx_hash_str}")
                        # ── 청산 주문: ws_ok 후 포지션 실제 소멸 확인 ──────────
                        # WHY: ws_ok = WS 전송 성공이지 체결 보장 아님
                        #      nonce 불일치 상태에서 ws_ok 찍히고 실제 미체결 사례 발생 (JUP/AAVE)
                        # 해결: reduce_only 주문 성공 시 2초 후 WS 포지션 잔존 확인
                        #       잔존 시 REST 폴백으로 재시도
                        if reduce_only:
                            await asyncio.sleep(2.0)
                            try:
                                from bot_v4_death import WS_MANAGER as _wsm
                                _mid_chk = str(order_data.get('marketId', ''))
                                _ws_sz_chk = abs(float(_wsm._positions.get(_mid_chk, {}).get('position', 0)))
                                if _ws_sz_chk > 1e-9:
                                    logging.getLogger(__name__).warning(
                                        f"⚠️ ws_ok 후 포지션 잔존({_ws_sz_chk:.4g}) 감지 → REST 폴백 재시도"
                                    )
                                    ideal_price_int = None  # REST 폴백
                                    continue
                            except Exception:
                                pass  # WS_MANAGER 미접근 시 무시
                        return {
                            'success': True,
                            'txHash': tx_hash_str,
                            'clientOrderIndex': idx,
                            'via': 'websocket',
                        }

            # REST 폴백 (WS 실패 or ideal_price 없음)
            tx, tx_hash, err = await client.create_market_order_limited_slippage(
                market_index=order_data['marketId'],
                client_order_index=idx,
                base_amount=base_amount,
                max_slippage=0.02,
                is_ask=is_ask,
                reduce_only=reduce_only,
                ideal_price=ideal_price_int,  # None이면 SDK가 OB API로 자동 조회
            )
            if err:
                err_str = str(err)
                last_err = err_str
                if 'nonce' in err_str.lower():
                    await reset_client()
                    await asyncio.sleep(0.3)
                    continue  # 즉시 재시도
                if '429' in err_str or 'too many' in err_str.lower():
                    # Rate limit: 글로벌 limiter가 이미 관리하므로 짧게 대기 후 재시도
                    # 청산은 반드시 성공해야 → 재시도 계속
                    wait = 3 + attempt * 2 + random.uniform(0, 1)
                    logging.getLogger(__name__).warning(f"⚠️ 429 → {wait:.1f}s 대기 [attempt {attempt+1}]")
                    await asyncio.sleep(wait)
                    continue
                # 기타 오류 → reduce_only면 재시도, 아니면 종료
                if reduce_only and attempt < max_attempts - 1:
                    await asyncio.sleep(1)
                    continue
                return {'success': False, 'error': err_str}
            # Lighter CODE_OK = 200 → code != 200이면 거래소에서 reject됨
            # WHY: Lighter SDK CODE_OK=200 (not 0). code==0 판정은 버그
            #      lighter/signer_client.py: CODE_OK = 200
            tx_code = getattr(tx_hash, 'code', 200)  # 기본값 200 (성공으로 간주)
            if tx_code != 200:
                tx_msg = str(getattr(tx_hash, 'message', '') or '')
                last_err = f"tx rejected code={tx_code} msg={tx_msg}"
                # volume quota 판정 (2026-03-14 Premium 정상 응답 오파싱 수정)
                # 실제 소진 = "exhausted" or "0 volume quota" — 숫자가 있으면 정상
                _tx_msg_l = tx_msg.lower()
                _is_tx_quota_exhausted = (
                    'exhausted' in _tx_msg_l or
                    ('quota' in _tx_msg_l and _tx_msg_l.strip().startswith('0 '))
                )
                if _is_tx_quota_exhausted:
                    if not reduce_only:
                        logging.getLogger(__name__).warning(f"⚠️ volume quota 소진 → 진입 스킵 (청산 quota 보호)")
                        return {'success': False, 'error': f'volume_quota_exhausted: {last_err}'}
                    wait = 10 + attempt * 3
                    logging.getLogger(__name__).warning(f"⚠️ volume quota → {wait}s 대기 후 SL 재시도 (청산 포기 금지)")
                    await asyncio.sleep(wait)
                    continue
                # ratelimit 메시지 = quota 잔여 정보 (정상) — 재시도 없이 성공 처리
                if 'ratelimit' in _tx_msg_l or 'volume quota' in _tx_msg_l:
                    logging.getLogger(__name__).debug(f"volume quota 정보 메시지 (정상): {tx_msg}")
                    return {'success': True, 'txHash': '', 'clientOrderIndex': idx}
                if reduce_only and attempt < max_attempts - 1:
                    await asyncio.sleep(1)
                    continue
                return {'success': False, 'error': last_err}
            return {
                'success': True,
                'txHash': str(tx_hash.tx_hash) if hasattr(tx_hash, 'tx_hash') else str(tx_hash),
                'clientOrderIndex': idx,
            }
        except Exception as e:
            err_str = str(e)
            last_err = err_str
            if 'nonce' in err_str.lower():
                await reset_client()
            if attempt < max_attempts - 1:
                await asyncio.sleep(1)
                continue
            return {'success': False, 'error': f"{type(e).__name__}: {err_str}"}

    return {'success': False, 'error': f"max_attempts({max_attempts}) 초과: {last_err}"}


async def force_close(order_data: dict, max_attempts: int = 10) -> dict:
    """SL/LIQ_DANGER 전용 — 절대 실패 불가 청산 (v4.7)
    - 글로벌 rate limiter 연동: 429 전에 미리 슬롯 관리
    - nonce 리셋, ideal_price None 폴백 포함
    - 429 발생 시: 짧은 대기 + rate limiter window 소진 대기
    """
    _log = logging.getLogger(__name__)
    sym = order_data.get('symbol', str(order_data.get('marketId', '?')))
    order_data = dict(order_data)  # 복사본 (원본 수정 방지)
    order_data['reduce_only'] = True  # 청산 강제 표시

    for attempt in range(max_attempts):
        try:
            result = await execute_order(order_data)
            if result.get('success'):
                if attempt > 0:
                    _log.info(f"✅ force_close {sym} 성공 [{attempt+1}/{max_attempts}]")
                return result
            err = result.get('error', '')
            _log.warning(f"⚠️ force_close {sym} [{attempt+1}/{max_attempts}] 실패: {err[:100]}")

            if 'nonce' in err.lower():
                _log.info(f"  → nonce 에러 → 클라이언트 리셋")
                await reset_client()
                await asyncio.sleep(0.3)
                continue

            if '429' in err or 'Too Many' in err or 'ratelimit' in err.lower():
                # 글로벌 limiter가 이미 관리 중 — 짧은 대기 후 limiter에 맡김
                wait = 5 + attempt + random.uniform(0, 2)
                _log.warning(f"  → 429 rate limit → {wait:.1f}s 대기 후 재시도")
                await asyncio.sleep(wait)
                continue

            if 'volume quota' in err.lower() or 'volume_quota' in err.lower():
                wait = 15 + attempt * 2
                _log.warning(f"  → volume quota → {wait}s 대기")
                await asyncio.sleep(wait)
                continue

            # 3회 시도 후 ideal_price 제거 → SDK OB 자동 조회 폴백
            if attempt == 2:
                _log.info(f"  → ideal_price 제거 후 재시도 (SDK OB 자동 조회)")
                order_data.pop('ideal_price', None)
                order_data['price'] = 0

            # 5회 이상 실패 → 클라이언트 완전 리셋
            if attempt >= 4:
                _log.warning(f"  → {attempt+1}회 실패 → 클라이언트 강제 리셋")
                await reset_client()
                await asyncio.sleep(2)
                continue

            await asyncio.sleep(1 + attempt * 0.5)

        except Exception as e:
            _log.warning(f"⚠️ force_close {sym} [{attempt+1}/{max_attempts}] 예외: {e}")
            await reset_client()
            await asyncio.sleep(1)

    return {'success': False, 'error': f'force_close: {max_attempts}회 전부 실패'}


async def batch_close(orders: list) -> dict:
    """
    다중 청산 배치 전송 (v4.7 핵심 기능)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    여러 SL/TP 청산을 1번 API 호출로 처리:
      1) 각 주문 로컬 서명 (sign_create_order, 동기, nonce 자동 증가)
      2) send_tx_batch → 1회 API 호출
      3) 개별 결과 파싱 후 반환

    orders: [
      {"marketId": int, "symbol": str, "side": "LONG"|"SHORT",
       "size": float, "ideal_price": int|None},
      ...
    ]

    returns: {
      "results": [{"symbol": str, "success": bool, "tx_hash": str, "error": str}, ...]
      "batch_success": bool,
      "volume_quota_remaining": int,
    }
    WHY send_tx_batch:
      - 직렬 force_close N개 → N번 API 호출 (rate limit 폭발)
      - batch_close N개 → 1번 API 호출 (rate limit 절약)
    """
    _log = logging.getLogger(__name__)
    if not orders:
        return {"results": [], "batch_success": True, "volume_quota_remaining": -1}

    client = await get_client()
    tx_types = []
    tx_infos = []
    order_metas = []  # 결과 매핑용

    for o in orders:
        mid = o["marketId"]
        sym = o.get("symbol", str(mid))
        side = o.get("side", "LONG")
        size = o["size"]
        ideal_price = o.get("ideal_price")

        market_id_str = str(mid)
        size_dec  = DECIMALS_MAP.get(market_id_str, {}).get("size_decimals", 4)
        price_dec = DECIMALS_MAP.get(market_id_str, {}).get("price_decimals", 6)

        base_amount = int(size * (10 ** size_dec))
        is_long = side.upper() in ("BUY", "LONG")
        is_ask = is_long   # 청산: LONG → sell(ask), SHORT → buy(bid)

        # ideal_price 없으면 WS candle에서 조회
        if ideal_price is None:
            try:
                import urllib.request as _ur, json as _j
                _req = _ur.Request(f"http://127.0.0.1:5001/api/candles/{mid}/1m", headers={"User-Agent": "bot"})
                with _ur.urlopen(_req, timeout=2) as _r:
                    _c = _j.loads(_r.read()).get("candles", [])
                    if _c:
                        ideal_price = int(float(_c[-1]["close"]) * (10 ** price_dec))
            except:
                pass

        if ideal_price is None:
            _log.warning(f"batch_close: {sym} ideal_price 없음 → SDK OB 자동 조회 (느림)")
            try:
                ideal_price = await client.get_best_price(mid, is_ask)
            except:
                pass

        # acceptable_price: 슬리피지 2% (create_market_order_limited_slippage와 동일 로직)
        max_slippage = 0.02
        if ideal_price:
            acceptable_price = round(ideal_price * (1 + max_slippage * (-1 if is_ask else 1)))
        else:
            acceptable_price = 0

        idx = (int(time.time() * 1_000_000) + random.randint(0, 9999)) % (2**31)

        # 로컬 서명 (nonce 명시 → nextNonce REST 호출 없음, order_expiry=0 IOC 필수)
        _bk_idx = int(os.getenv('LIGHTER_API_KEY_INDEX', '2'))
        _bk_nonce = client.nonce_manager.nonce.get(_bk_idx, 0) + 1
        tx_type, tx_info, tx_hash_local, err = client.sign_create_order(
            market_index=mid,
            client_order_index=idx,
            base_amount=base_amount,
            price=acceptable_price,
            is_ask=is_ask,
            order_type=lighter.SignerClient.ORDER_TYPE_MARKET,
            time_in_force=lighter.SignerClient.ORDER_TIME_IN_FORCE_IMMEDIATE_OR_CANCEL,
            reduce_only=True,
            order_expiry=lighter.SignerClient.DEFAULT_IOC_EXPIRY,
            nonce=_bk_nonce,
            api_key_index=_bk_idx,
        )
        if tx_type is not None:
            client.nonce_manager.nonce[_bk_idx] = _bk_nonce
        if err:
            _log.warning(f"batch_close: {sym} 서명 실패 → {err}")
            order_metas.append({"symbol": sym, "success": False, "error": err, "tx_hash": ""})
            continue

        tx_types.append(tx_type)
        tx_infos.append(tx_info)
        order_metas.append({"symbol": sym, "success": None, "error": "", "tx_hash": ""})

    if not tx_types:
        return {"results": order_metas, "batch_success": False, "volume_quota_remaining": -1}

    # Rate limit 슬롯 획득 (1번만 — batch는 1 API 호출)
    await _acquire_rate_slot(is_close=True)

    try:
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # WS batch 전송 (v4.8): HTTP 429 완전 우회
        # WHY: jsonapi/sendtxbatch = N개 청산을 1 WS 메시지로
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        ws_resp = await _ws_send_tx_batch(tx_types, tx_infos)
        ws_err = ws_resp.get("error")
        ws_result = ws_resp.get("result", {})

        if not ws_err:
            # WS batch 성공
            hashes = []
            if isinstance(ws_result, dict):
                hashes = ws_result.get("tx_hashes") or ws_result.get("hashes") or []
            elif isinstance(ws_result, list):
                hashes = [r.get("hash","") for r in ws_result]
            quota = ws_result.get("volume_quota_remaining", -1) if isinstance(ws_result, dict) else -1
            sign_idx = 0
            for meta in order_metas:
                if meta["success"] is None:
                    meta["success"] = True
                    meta["tx_hash"] = hashes[sign_idx] if sign_idx < len(hashes) else "ws_batch_ok"
                    sign_idx += 1
            _log.info(f"✅ WS batch_close {len(tx_types)}건 성공")
            return {"results": order_metas, "batch_success": True, "volume_quota_remaining": quota}

        # WS batch 실패 → REST 폴백
        ws_msg = ws_err.get("message", "")
        _log.warning(f"⚠️ WS batch 실패 ({ws_msg}) → REST 폴백")

        # WS 연결 자체가 429 (rate limit on WS handshake) → 연결 리셋 후 REST로만 시도
        if '429' in ws_msg or 'rejected' in ws_msg.lower():
            global _ws_order_conn
            _ws_order_conn = None   # 다음 호출 시 재연결 강제
            _log.info("🔄 WS 429 감지 → 연결 리셋, REST 폴백 진행")

        # invalid tx info → WS 채널 상태 불량 → 채널 리셋 + nonce REST 동기화
        if 'invalid tx' in ws_msg.lower() or 'invalid tx info' in ws_msg.lower():
            _ws_order_conn = None   # WS 채널 강제 재연결
            asyncio.ensure_future(_hard_refresh_nonce_bg())  # nonce REST 동기화
            _log.warning("🔄 invalid tx info → WS 채널 강제 리셋 + nonce REST 동기화 예약")

        # volume quota 판정 (2026-03-14 Premium 정상 응답 오파싱 수정)
        # 'ratelimit' + 'quota' = 잔여 정보 메시지 (정상), 실제 소진 = 'exhausted' or '0 quota'
        _bws_msg_l = ws_msg.lower()
        _is_batch_quota_exhausted = (
            'exhausted' in _bws_msg_l or
            ('quota' in _bws_msg_l and _bws_msg_l.strip().startswith('0 '))
        )
        if _is_batch_quota_exhausted:
            for meta in order_metas:
                if meta["success"] is None:
                    meta["success"] = False
                    meta["error"] = f"volume_quota_exhausted: {ws_msg}"
            return {"results": order_metas, "batch_success": False, "volume_quota_remaining": 0}

        # REST 폴백
        resp = await client.send_tx_batch(tx_types=tx_types, tx_infos=tx_infos)
        # ── Lighter SDK CODE_OK = 200 (성공 코드) ──────────────────────────
        # WHY: Lighter API는 code=200이 성공, code=0이 아님
        #      lighter/signer_client.py: CODE_OK = 200
        #      기존 code==0 판정 → 200을 실패로 처리 → SL 실패 근본 원인
        # 참고: message 필드 = "N volume quota remained" → 성공 시 quota 정보 포함 (정상)
        batch_ok = (resp.code == 200)
        quota = resp.volume_quota_remaining
        hashes = resp.tx_hash or []
        sign_idx = 0
        for meta in order_metas:
            if meta["success"] is None:
                if batch_ok:
                    meta["success"] = True
                    meta["tx_hash"] = hashes[sign_idx] if sign_idx < len(hashes) else ""
                else:
                    meta["success"] = False
                    meta["error"] = f"batch code={resp.code} msg={resp.message}"
                sign_idx += 1

        if batch_ok:
            _log.info(f"✅ REST batch_close {len(tx_types)}건 성공 | quota_remaining={quota}")
        else:
            _log.warning(f"⚠️ REST batch_close 실패 code={resp.code} msg={resp.message}")

        return {"results": order_metas, "batch_success": batch_ok, "volume_quota_remaining": quota}

    except Exception as e:
        _log.error(f"batch_close 예외: {e}")
        for meta in order_metas:
            if meta["success"] is None:
                meta["success"] = False
                meta["error"] = str(e)
        return {"results": order_metas, "batch_success": False, "volume_quota_remaining": -1}


if __name__ == '__main__':
    # ── 외부 직접 실행 시 봇 프로세스와 WS 채널 충돌 경고 ────────────
    # WHY: 봇 프로세스가 이미 같은 계정으로 WS 주문 채널을 사용 중
    #      여기서 별도 채널 열면 같은 계정 nonce 동시 증가 → 주문 충돌
    # 권고: 봇이 실행 중일 때는 직접 실행 대신 봇 내부 큐를 통해 처리
    import subprocess as _sp
    _bot_running = bool(_sp.run(["pgrep", "-f", "bot_v4_death"], capture_output=True).stdout.strip())
    if _bot_running:
        import sys as _sys
        _sys.stderr.write(
            "[WARNING] bot_v4_death 실행 중 — WS 채널 충돌 위험!\n"
            "          봇이 처리할 수 없는 긴급 상황에서만 사용하세요.\n"
        )
    if len(sys.argv) < 2:
        print(json.dumps({'success': False, 'error': 'No order data provided'}))
        sys.exit(1)
    result = asyncio.run(execute_order(json.loads(sys.argv[1])))
    print(json.dumps(result))

# 봇 로그 핸들러 연동 (모듈 로딩 시)
def _setup_logger():
    import logging, sys
    _l = logging.getLogger(__name__)
    if not _l.handlers:
        _l.setLevel(logging.DEBUG)
        _h = logging.StreamHandler(sys.stdout)
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        _l.addHandler(_h)
        _l.propagate = False

_setup_logger()
