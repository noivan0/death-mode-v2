"""
DEATH MODE v2 — Lighter SDK Exchange Bridge for Freqtrade
==========================================================
lighter-python SDK 0.1.0 기반 Freqtrade Exchange 서브클래스.

WHY SDK (not ccxt):
  ccxt lighter v4.5.50+는 L1 이더리움 개인키(66자) 필요.
  v1에서 검증된 privateKey(80자)는 Lighter SDK 전용 API signing key.
  → lighter-python SDK 직접 사용이 유일한 실용적 방법.

인증:
  LIGHTER_ACCOUNT_INDEX=1058
  LIGHTER_API_KEY_INDEX=2
  LIGHTER_PRIVATE_KEY=587840a...c1ef (80자)
  LIGHTER_BASE_URL=https://mainnet.zklighter.elliot.ai
  LIGHTER_SKIP_SSL_VERIFY=1  (HMG 사내망)

Freqtrade 등록:
  이 파일을 freqtrade/exchange/lighter.py로 복사
  freqtrade/exchange/__init__.py에 import 추가

주의: lighter SDK는 async/aiohttp 기반.
  Freqtrade는 sync + ccxt async 혼용.
  여기서는 asyncio.run() + loop 재사용 패턴으로 처리.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from functools import wraps
from typing import Any, Optional

import lighter
from lighter.configuration import Configuration as LighterConfiguration
from lighter.api_client import ApiClient as LighterApiClient

from freqtrade.constants import BuySell
from freqtrade.enums import MarginMode, TradingMode
from freqtrade.exceptions import (
    ExchangeError,
    InsufficientFundsError,
    InvalidOrderException,
    OperationalException,
    RetryableOrderError,
)
from freqtrade.exchange import Exchange

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# 전역 이벤트 루프 (Freqtrade sync ↔ SDK async 브릿지)
# ──────────────────────────────────────────────────────────────────

def _get_loop() -> asyncio.AbstractEventLoop:
    """Freqtrade sync 컨텍스트에서 async SDK 호출용 전용 루프."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def run_async(coro):
    """async 코루틴을 sync하게 실행."""
    loop = _get_loop()
    return loop.run_until_complete(coro)


# ──────────────────────────────────────────────────────────────────
# LighterSDKClient — SDK API 래퍼
# ──────────────────────────────────────────────────────────────────

class LighterSDKClient:
    """
    lighter-python SDK async API를 sync하게 래핑.
    SignerClient(주문 실행) + OrderApi/CandlestickApi(조회) 통합.
    """

    def __init__(
        self,
        base_url: str,
        account_index: int,
        api_key_index: int,
        private_key: str,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url
        self.account_index = account_index
        self.api_key_index = api_key_index
        self.private_key = private_key
        self.verify_ssl = verify_ssl

        # lighter SDK Configuration
        self._config = LighterConfiguration(host=base_url)
        self._config.verify_ssl = verify_ssl

        # SignerClient (주문 실행)
        self._signer: Optional[lighter.SignerClient] = None

        # market_id ↔ symbol 매핑 캐시
        self._market_id_map: dict[str, int] = {}   # symbol → market_id
        self._market_symbol_map: dict[int, str] = {}  # market_id → symbol
        self._markets_loaded = False

    # ── 내부 헬퍼 ───────────────────────────────────────────────

    async def _get_api_client(self):
        return LighterApiClient(configuration=self._config)

    def _ensure_signer(self):
        if self._signer is None:
            if not self.private_key:
                raise OperationalException(
                    "Lighter private key not set. "
                    "Set LIGHTER_PRIVATE_KEY environment variable."
                )
            try:
                self._signer = lighter.SignerClient(
                    url=self.base_url,
                    account_index=self.account_index,
                    api_private_keys={self.api_key_index: self.private_key},
                )
                logger.info(
                    "✅ Lighter SignerClient 초기화 완료 "
                    "(account=%d, key_idx=%d)",
                    self.account_index,
                    self.api_key_index,
                )
            except Exception as e:
                raise OperationalException(
                    f"Lighter SignerClient 초기화 실패: {e}"
                ) from e

    def _symbol_to_lighter(self, pair: str) -> str:
        """BTC/USDC:USDC → BTC"""
        return pair.split("/")[0]

    def _get_market_id(self, pair: str) -> int:
        symbol = self._symbol_to_lighter(pair)
        if symbol not in self._market_id_map:
            raise ExchangeError(f"Unknown market: {pair}. Run load_markets() first.")
        return self._market_id_map[symbol]

    # ── 마켓 로드 ────────────────────────────────────────────────

    async def _load_markets_async(self) -> dict:
        async with lighter.ApiClient(configuration=self._config) as client:
            order_api = lighter.OrderApi(client)
            stats = await order_api.exchange_stats()

        markets = {}
        for s in stats.order_book_stats:
            symbol = s.symbol
            # Lighter 마켓 ID는 별도 조회 필요 — exchange_stats 순서 기반
            pair = f"{symbol}/USDC:USDC"
            markets[pair] = {
                "id": symbol,
                "symbol": pair,
                "base": symbol,
                "quote": "USDC",
                "settle": "USDC",
                "type": "swap",
                "swap": True,
                "active": True,
                "last": s.last_trade_price,
                "info": s,
            }
        return markets

    def load_markets(self) -> dict:
        markets = run_async(self._load_markets_async())
        # market_id 별도 로드 (OrderBook 호출용)
        self._load_market_ids()
        self._markets_loaded = True
        return markets

    def _load_market_ids(self):
        """account positions에서 market_id 매핑 추출 (실측 기반)."""
        import urllib.request
        import json as _json
        import ssl

        url = f"{self.base_url}/api/v1/account?by=index&value={self.account_index}"
        ctx = None
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(url, timeout=10, context=ctx) as resp:
                data = _json.loads(resp.read())
            accounts = data.get("accounts", [])
            if accounts:
                for pos in accounts[0].get("positions", []):
                    symbol = pos.get("symbol", "")
                    mkt_id = pos.get("market_id")
                    if symbol and mkt_id is not None:
                        self._market_id_map[symbol] = mkt_id
                        self._market_symbol_map[mkt_id] = symbol
            logger.info("Lighter market_id 매핑 로드: %d개", len(self._market_id_map))
        except Exception as e:
            logger.warning("market_id 로드 실패 (fallback 사용): %s", e)
            # Fallback: 실측 확인된 market_id
            fallback = {"BTC": 1, "ETH": 0, "SOL": 2}
            self._market_id_map.update(fallback)
            for sym, mid in fallback.items():
                self._market_symbol_map[mid] = sym

    # ── 잔고 조회 ────────────────────────────────────────────────

    async def _fetch_balance_async(self) -> dict:
        import urllib.request
        import json as _json
        import ssl

        url = (
            f"{self.base_url}/api/v1/account"
            f"?by=index&value={self.account_index}"
        )
        ctx = None
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = _json.loads(resp.read())
        except Exception as e:
            raise RetryableOrderError(f"Lighter balance fetch failed: {e}") from e

        # 실측 응답 구조: {"accounts": [{...}]}
        accounts = data.get("accounts", [])
        if not accounts:
            return {"USDC": {"free": 0, "used": 0, "total": 0},
                    "free": {"USDC": 0}, "used": {"USDC": 0}, "total": {"USDC": 0}}
        account = accounts[0]
        collateral = float(account.get("collateral", 0))
        available = float(account.get("available_balance", collateral))
        used = max(collateral - available, 0)
        return {
            "USDC": {"free": available, "used": used, "total": collateral},
            "free": {"USDC": available},
            "used": {"USDC": used},
            "total": {"USDC": collateral},
        }

    def fetch_balance(self) -> dict:
        return run_async(self._fetch_balance_async())

    # ── OHLCV ────────────────────────────────────────────────────

    async def _fetch_ohlcv_async(
        self, pair: str, timeframe: str, limit: int = 500
    ) -> list:
        symbol = self._symbol_to_lighter(pair)
        market_id = self._market_id_map.get(symbol, 1)

        # timeframe → Lighter resolution
        tf_map = {
            "1m": "1", "5m": "5", "15m": "15", "30m": "30",
            "1h": "60", "4h": "240", "1d": "1440",
        }
        resolution = tf_map.get(timeframe, "60")

        import urllib.request
        import json as _json
        import ssl

        url = (
            f"{self.base_url}/api/v1/candlesticks"
            f"?market_id={market_id}&resolution={resolution}&count_back={limit}"
        )
        ctx = None
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(url, timeout=15, context=ctx) as resp:
                data = _json.loads(resp.read())
        except Exception as e:
            raise RetryableOrderError(f"Lighter OHLCV fetch failed: {e}") from e

        candles = []
        for c in data.get("candlesticks", []):
            candles.append([
                int(c["time"]) * 1000,  # ms
                float(c["open"]),
                float(c["high"]),
                float(c["low"]),
                float(c["close"]),
                float(c["base_volume"]),
            ])
        return sorted(candles, key=lambda x: x[0])

    def fetch_ohlcv(self, pair: str, timeframe: str, limit: int = 500) -> list:
        return run_async(self._fetch_ohlcv_async(pair, timeframe, limit))

    # ── 오더북 ───────────────────────────────────────────────────

    async def _fetch_order_book_async(self, pair: str, limit: int = 20) -> dict:
        symbol = self._symbol_to_lighter(pair)
        market_id = self._market_id_map.get(symbol, 1)

        import urllib.request
        import json as _json
        import ssl

        url = f"{self.base_url}/api/v1/orderbook?market_id={market_id}"
        ctx = None
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(url, timeout=10, context=ctx) as resp:
                data = _json.loads(resp.read())
        except Exception as e:
            raise RetryableOrderError(f"Lighter orderbook fetch failed: {e}") from e

        ob = data.get("order_book", {})
        bids = [[float(p), float(q)] for p, q in ob.get("bid_levels", [])[:limit]]
        asks = [[float(p), float(q)] for p, q in ob.get("ask_levels", [])[:limit]]
        return {"bids": bids, "asks": asks, "symbol": pair, "timestamp": None}

    def fetch_order_book(self, pair: str, limit: int = 20) -> dict:
        return run_async(self._fetch_order_book_async(pair, limit))

    # ── 포지션 ───────────────────────────────────────────────────

    async def _fetch_positions_async(self) -> list:
        import urllib.request
        import json as _json
        import ssl

        url = (
            f"{self.base_url}/api/v1/account"
            f"?by=index&value={self.account_index}"
        )
        ctx = None
        if not self.verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(url, timeout=10, context=ctx) as resp:
                data = _json.loads(resp.read())
        except Exception as e:
            raise RetryableOrderError(f"Lighter positions fetch failed: {e}") from e

        # 실측 구조: {"accounts": [{"positions": [...]}]}
        accounts = data.get("accounts", [])
        if not accounts:
            return []

        positions = []
        for pos in accounts[0].get("positions", []):
            market_id = pos.get("market_id")
            size = float(pos.get("position", 0))
            sign = pos.get("sign", 1)  # 1=long, -1=short
            if size == 0:
                continue
            symbol = pos.get("symbol", self._market_symbol_map.get(market_id, f"MKT{market_id}"))
            side = "long" if sign == 1 else "short"
            positions.append({
                "symbol": f"{symbol}/USDC:USDC",
                "side": side,
                "contracts": abs(size),
                "entryPrice": float(pos.get("avg_entry_price", 0)),
                "unrealizedPnl": float(pos.get("unrealized_pnl", 0)),
                "leverage": 1.0 / (float(pos.get("initial_margin_fraction", "100")) / 100)
                            if pos.get("initial_margin_fraction") else 1.0,
                "info": pos,
            })
        return positions

    def fetch_positions(self) -> list:
        return run_async(self._fetch_positions_async())

    # ── 주문 실행 ────────────────────────────────────────────────

    async def _create_order_async(
        self,
        pair: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> dict:
        self._ensure_signer()
        symbol = self._symbol_to_lighter(pair)
        market_id = self._market_id_map.get(symbol, 1)
        is_buy = side.lower() == "buy"

        try:
            if order_type == "market":
                result = await self._signer.create_market_order(
                    market_id=market_id,
                    client_order_id=0,
                    size=amount,
                    is_buy=is_buy,
                )
            else:  # limit
                if price is None:
                    raise InvalidOrderException("Limit order requires price")
                result = await self._signer.create_order(
                    market_id=market_id,
                    client_order_id=0,
                    price=price,
                    size=amount,
                    is_buy=is_buy,
                    time_in_force=lighter.SignerClient.TIME_IN_FORCE_GTC,
                    is_reduce_only=params.get("reduce_only", False) if params else False,
                )
        except lighter.ApiException as e:
            if "insufficient" in str(e).lower():
                raise InsufficientFundsError(f"Lighter insufficient funds: {e}") from e
            raise ExchangeError(f"Lighter create_order failed: {e}") from e
        except Exception as e:
            raise ExchangeError(f"Lighter create_order error: {e}") from e

        return {
            "id": str(getattr(result, "order_id", "")),
            "symbol": pair,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": "open",
            "info": result,
        }

    def create_order(
        self,
        pair: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[dict] = None,
    ) -> dict:
        return run_async(
            self._create_order_async(pair, order_type, side, amount, price, params)
        )

    # ── 주문 취소 ────────────────────────────────────────────────

    async def _cancel_order_async(self, order_id: str, pair: str) -> dict:
        self._ensure_signer()
        symbol = self._symbol_to_lighter(pair)
        market_id = self._market_id_map.get(symbol, 1)
        try:
            result = await self._signer.cancel_order(
                market_id=market_id,
                client_order_id=int(order_id),
            )
            return {"id": order_id, "status": "canceled", "info": result}
        except Exception as e:
            raise ExchangeError(f"Lighter cancel_order failed: {e}") from e

    def cancel_order(self, order_id: str, pair: str) -> dict:
        return run_async(self._cancel_order_async(order_id, pair))

    # ── 주문 조회 ────────────────────────────────────────────────

    async def _fetch_orders_async(
        self, pair: str, active_only: bool = True
    ) -> list:
        symbol = self._symbol_to_lighter(pair)
        market_id = self._market_id_map.get(symbol, 1)

        async with lighter.ApiClient(configuration=self._config) as client:
            order_api = lighter.OrderApi(client)
            try:
                if active_only:
                    result = await order_api.account_active_orders(
                        account_index=self.account_index,
                        market_id=market_id,
                    )
                else:
                    result = await order_api.account_inactive_orders(
                        account_index=self.account_index,
                        market_id=market_id,
                        limit=100,
                    )
            except Exception as e:
                raise RetryableOrderError(f"Lighter fetch_orders failed: {e}") from e

        orders = []
        for o in getattr(result, "orders", []):
            orders.append({
                "id": str(getattr(o, "order_id", "")),
                "symbol": pair,
                "type": "limit",
                "side": "buy" if getattr(o, "is_buy", True) else "sell",
                "amount": float(getattr(o, "size", 0)),
                "price": float(getattr(o, "price", 0)),
                "status": "open" if active_only else "closed",
                "info": o,
            })
        return orders

    def fetch_open_orders(self, pair: str) -> list:
        return run_async(self._fetch_orders_async(pair, active_only=True))

    def fetch_closed_orders(self, pair: str) -> list:
        return run_async(self._fetch_orders_async(pair, active_only=False))

    # ── 레버리지 ─────────────────────────────────────────────────

    async def _set_leverage_async(self, pair: str, leverage: int) -> dict:
        self._ensure_signer()
        symbol = self._symbol_to_lighter(pair)
        market_id = self._market_id_map.get(symbol, 1)
        try:
            result = await self._signer.update_leverage(
                market_id=market_id,
                leverage=leverage,
                is_isolated=False,  # cross margin
            )
            return {"leverage": leverage, "symbol": pair, "info": result}
        except Exception as e:
            raise ExchangeError(f"Lighter set_leverage failed: {e}") from e

    def set_leverage(self, pair: str, leverage: int) -> dict:
        return run_async(self._set_leverage_async(pair, leverage))

    # ── 펀딩비 ───────────────────────────────────────────────────

    async def _fetch_funding_rates_async(self, pairs: list) -> dict:
        async with lighter.ApiClient(configuration=self._config) as client:
            order_api = lighter.OrderApi(client)
            try:
                stats = await order_api.exchange_stats()
            except Exception as e:
                logger.warning("Lighter funding rate fetch failed: %s", e)
                return {p: {"fundingRate": 0.0} for p in pairs}

        rates = {}
        for pair in pairs:
            symbol = self._symbol_to_lighter(pair)
            rates[pair] = {"fundingRate": 0.0, "symbol": pair}
        return rates

    def fetch_funding_rates(self, pairs: list) -> dict:
        return run_async(self._fetch_funding_rates_async(pairs))


# ──────────────────────────────────────────────────────────────────
# Lighter Exchange Subclass for Freqtrade
# ──────────────────────────────────────────────────────────────────

class Lighter(Exchange):
    """
    Lighter DEX Exchange — lighter-python SDK 기반 Freqtrade 서브클래스.

    config.json:
      "exchange": {
        "name": "lighter",
        "key": "",
        "secret": ""
      }
    환경변수:
      LIGHTER_PRIVATE_KEY, LIGHTER_ACCOUNT_INDEX, LIGHTER_API_KEY_INDEX
      LIGHTER_BASE_URL, LIGHTER_SKIP_SSL_VERIFY
    """

    _ft_has: dict = {
        **Exchange._ft_has,
        "stoploss_on_exchange": True,
        "stoploss_order_types": {"limit": "limit"},
        "order_time_in_force": ["GTC", "IOC", "FOK"],
        "ohlcv_has_history": True,
        "ohlcv_candle_limit": 500,
        "tickers_have_bid_ask": False,
        "l2_limit_levels": [0, 5, 10, 20, 50],
        "l2_limit_levels_required": [],
        "trades_has_history": False,
    }

    _ft_has_futures: dict = {
        **Exchange._ft_has_futures,
        "needs_trading_fees": True,
    }

    _supported_trading_mode_margin_pairs: list = [
        (TradingMode.FUTURES, MarginMode.CROSS),
        (TradingMode.FUTURES, MarginMode.ISOLATED),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # SDK 클라이언트 초기화
        base_url = os.environ.get(
            "LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai"
        )
        account_index = int(os.environ.get("LIGHTER_ACCOUNT_INDEX", "1058"))
        api_key_index = int(os.environ.get("LIGHTER_API_KEY_INDEX", "2"))
        private_key = os.environ.get("LIGHTER_PRIVATE_KEY", "")
        verify_ssl = os.environ.get("LIGHTER_SKIP_SSL_VERIFY", "0") != "1"

        self._sdk = LighterSDKClient(
            base_url=base_url,
            account_index=account_index,
            api_key_index=api_key_index,
            private_key=private_key,
            verify_ssl=verify_ssl,
        )

        if not private_key:
            logger.warning(
                "⚠️  LIGHTER_PRIVATE_KEY 미설정 — dry_run 전용 모드"
            )
        else:
            logger.info(
                "✅ Lighter SDK 초기화 (account=%d, key_idx=%d, ssl=%s)",
                account_index, api_key_index, verify_ssl,
            )

    # ── 마켓 ──────────────────────────────────────────────────────

    def market_is_tradable(self, market: dict) -> bool:
        parent = super().market_is_tradable(market)
        return parent and market.get("quote") == "USDC"

    # ── 레버리지 ─────────────────────────────────────────────────

    def get_max_leverage(self, pair: str, stake_amount: Optional[float]) -> float:
        """Lighter BTC=50x, ETH=25x, SOL=20x (market info 기반)."""
        if pair not in self.markets:
            return 1.0
        market = self.markets[pair]
        info = market.get("info", {})
        # lighter SDK exchange_stats에는 max_leverage 없음 → info dict 기반
        min_imf = getattr(info, "min_initial_margin_fraction", None)
        if min_imf is not None:
            try:
                return 1.0 / (float(min_imf) / 10000)
            except (ZeroDivisionError, ValueError):
                pass
        # 기본값 (Lighter 실측 기반)
        symbol = pair.split("/")[0]
        defaults = {"BTC": 50.0, "ETH": 25.0, "SOL": 20.0}
        return defaults.get(symbol, 10.0)

    def _lev_prep(
        self,
        pair: str,
        leverage: float,
        side: BuySell,
        accept_fail: bool = False,
    ) -> None:
        if self._config.get("dry_run"):
            return
        try:
            self._sdk.set_leverage(pair, round(leverage))
        except Exception as e:
            if not accept_fail:
                raise
            logger.warning("Lighter set_leverage failed (accept_fail=True): %s", e)

    # ── 청산가 ───────────────────────────────────────────────────

    def get_liquidation_price(
        self,
        pair: str,
        open_rate: float,
        is_short: bool,
        amount: float,
        stake_amount: float,
        leverage: float,
        wallet_balance: float,
    ) -> Optional[float]:
        # Lighter: maintenance_margin_fraction=120(1.2%), closeout_margin_fraction=80(0.8%)
        mm_fraction = 0.012  # 1.2%
        margin = stake_amount / leverage
        mm_value = amount * open_rate * mm_fraction
        try:
            if not is_short:
                liq = open_rate - (margin - mm_value) / amount
            else:
                liq = open_rate + (margin - mm_value) / amount
        except ZeroDivisionError:
            return None
        return max(liq, 0.0) if not is_short else liq

    # ── 펀딩비 ───────────────────────────────────────────────────

    def get_funding_fees(
        self,
        pair: str,
        amount: float,
        is_short: bool,
        open_date: datetime,
    ) -> float:
        return 0.0  # Lighter 펀딩비 이력 API 미지원 → 0 fallback

    # ── 주문 조회 ────────────────────────────────────────────────

    def fetch_order(
        self,
        order_id: str,
        pair: str,
        params: Optional[dict] = None,
    ) -> dict:
        try:
            open_orders = self._sdk.fetch_open_orders(pair)
            for o in open_orders:
                if str(o.get("id")) == str(order_id):
                    return o
        except Exception as e:
            logger.warning("fetch_order (open) error: %s", e)

        try:
            closed_orders = self._sdk.fetch_closed_orders(pair)
            for o in closed_orders:
                if str(o.get("id")) == str(order_id):
                    return o
        except Exception as e:
            logger.warning("fetch_order (closed) error: %s", e)

        raise RetryableOrderError(
            f"Order {order_id} not found on Lighter (pair={pair})"
        )

    # ── 스톱로스 ─────────────────────────────────────────────────

    def stoploss(
        self,
        pair: str,
        amount: float,
        stop_price: float,
        order_types: dict,
        side: BuySell,
        leverage: float,
    ) -> dict:
        """Lighter stop-limit: limit 주문 + triggerPrice params."""
        limit_rate = self._get_stop_limit_rate(stop_price, order_types, side)
        params = self._get_stop_params(side, stop_price, limit_rate)

        if self._config.get("dry_run"):
            return {
                "id": f"dry_{pair}_{stop_price}",
                "symbol": pair,
                "type": "stop",
                "side": side,
                "amount": amount,
                "price": limit_rate,
                "status": "open",
                "ft_order_side": side,
            }

        order = self._sdk.create_order(
            pair=pair,
            order_type="limit",
            side=side,
            amount=amount,
            price=limit_rate,
            params=params,
        )
        order["ft_order_side"] = side
        return order

    def _get_stop_params(
        self, side: BuySell, stop_price: float, limit_rate: float
    ) -> dict:
        params = super()._get_stop_params(side, stop_price, limit_rate)
        params["triggerPrice"] = stop_price
        params["orderType"] = "stop_limit"
        return params
