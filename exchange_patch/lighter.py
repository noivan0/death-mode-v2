"""
DEATH MODE — Freqtrade Lighter Exchange Subclass
================================================
freqtrade/exchange/hyperliquid.py 패턴 준수.
ccxt.lighter 기반 Freqtrade 공식 Exchange 서브클래스.

수정 이력:
  v5: verify=False 추가 (HMG SSL 우회)
  v6: [CRITICAL-01] stoploss limit_rate NameError 수정
      [CRITICAL-02] verify=False 환경변수화 (LIGHTER_SKIP_SSL_VERIFY)
      [CRITICAL-03] triggerPrice 지원 여부 런타임 검증 추가
      [FAIL-01~06] 에러핸들링/펀딩비/fetchOrder 수정
      [WARN-01~06] 각종 코드 정리
  v7 (감사 정밀수정):
      [BUG-01] closeout_fraction → closeout_margin_fraction (실제 key)
      [BUG-02] price_to_precision override 제거 — base가 TICK_SIZE(precisionMode=4) 직접 처리
               is_stop 파라미터가 base rounding_mode와 충돌 → Freqtrade 내부 stoploss 호출 깨짐
      [BUG-03] amount_to_precision override 제거 — Decimal quantize vs base TICK_SIZE 방식 불일치
      [BUG-04] Lighter privateKey credential → ccxt_config.privateKey 경유 (key/secret 미지원)
               __init__에서 self._api.privateKey 동적 주입 추가
      [BUG-05] get_funding_fees open_rate_approx → markPrice fallback 개선
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import ccxt

from freqtrade.constants import BuySell
from freqtrade.enums import MarginMode, TradingMode
from freqtrade.exceptions import OperationalException, RetryableOrderError
from freqtrade.exchange import Exchange

logger = logging.getLogger(__name__)


class Lighter(Exchange):
    """
    Lighter DEX Exchange subclass for Freqtrade.
    https://app.zklighter.com

    USDC-only perpetual futures on ZK-rollup (Lighter Network).
    stake_currency must be USDC (not USDT).

    Lighter ccxt credential: privateKey (not apiKey/secret).
    config.json 예시:
      "exchange": {
        "name": "lighter",
        "key": "",          ← 사용 안 함
        "secret": "",       ← 사용 안 함
        "ccxt_config": {
          "privateKey": "${LIGHTER_PRIVATE_KEY}",
          "options": {"accountIndex": 0, "apiKeyIndex": 0, "chainId": 300}
        }
      }
    → 실제 privateKey는 환경변수 LIGHTER_PRIVATE_KEY로 주입.

    Environment variables:
      LIGHTER_PRIVATE_KEY       — EVM 개인키 (0x...)
      LIGHTER_ACCOUNT_INDEX     — 계정 인덱스 (기본 0)
      LIGHTER_API_KEY_INDEX     — API 키 인덱스 (기본 0)
      LIGHTER_SKIP_SSL_VERIFY=1 — HMG 사내망 MitM 우회용. 외부망에서는 절대 사용 금지.

    Ref: freqtrade/exchange/hyperliquid.py
    """

    # ──────────────────────────────────────────────────────────────
    # Freqtrade 기능 선언
    # ──────────────────────────────────────────────────────────────

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
        "exchange_has_overrides": {
            "fetchOrder": True,     # open+closed fallback 구현
            "fetchTrades": False,
            "watchOHLCV": False,
        },
    }

    _ft_has_futures: dict = {
        **Exchange._ft_has_futures,
        "needs_trading_fees": True,
    }

    _supported_trading_mode_margin_pairs: list = [
        (TradingMode.FUTURES, MarginMode.CROSS),
        (TradingMode.FUTURES, MarginMode.ISOLATED),
    ]

    # ──────────────────────────────────────────────────────────────
    # 초기화
    # ──────────────────────────────────────────────────────────────

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # [BUG-04] Lighter privateKey 환경변수 동적 주입
        # ccxt.lighter는 requiredCredentials: {privateKey: True}
        # Freqtrade config의 key/secret은 apiKey/secret으로 매핑 → Lighter에서 미사용
        # 환경변수 LIGHTER_PRIVATE_KEY → self._api.privateKey 직접 주입
        private_key = os.environ.get("LIGHTER_PRIVATE_KEY", "")
        if private_key:
            self._api.privateKey = private_key
            if hasattr(self, "_api_async") and self._api_async is not None:
                self._api_async.privateKey = private_key
            logger.info("Lighter: privateKey injected from LIGHTER_PRIVATE_KEY env")
        else:
            logger.warning(
                "Lighter: LIGHTER_PRIVATE_KEY not set — "
                "trading will fail without a valid private key!"
            )

        # [CRITICAL-02] HMG 사내망 MitM SSL 우회
        if os.environ.get("LIGHTER_SKIP_SSL_VERIFY", "0") == "1":
            logger.warning(
                "⚠️  SSL verification DISABLED (LIGHTER_SKIP_SSL_VERIFY=1). "
                "HMG internal network only — NEVER use on external networks!"
            )
            try:
                if hasattr(self._api, "session") and self._api.session is not None:
                    self._api.session.verify = False
                # async session은 ccxt_async_config verify=False로 처리됨
            except Exception as e:
                logger.warning("Could not set session.verify=False: %s", e)

    # ──────────────────────────────────────────────────────────────
    # 마켓 필터
    # ──────────────────────────────────────────────────────────────

    def market_is_tradable(self, market: dict) -> bool:
        parent_check = super().market_is_tradable(market)
        # Lighter quote 필드 실측: 'USDC' (not 'USDC:USDC')
        return parent_check and market.get("quote") == "USDC"

    # ──────────────────────────────────────────────────────────────
    # 레버리지
    # ──────────────────────────────────────────────────────────────

    def get_max_leverage(self, pair: str, stake_amount: Optional[float]) -> float:
        if pair not in self.markets:
            return 1.0
        market = self.markets[pair]
        limits = market.get("limits", {})
        leverage_limits = limits.get("leverage", {})
        max_lev = leverage_limits.get("max", None)
        if max_lev is None:
            info = market.get("info", {})
            # Lighter: min_initial_margin_fraction=200 → 200/10000=0.02 → max_lev=50x
            min_imf = info.get("min_initial_margin_fraction", None)
            if min_imf is not None:
                try:
                    max_lev = 1.0 / (float(min_imf) / 10000)
                except (ZeroDivisionError, ValueError):
                    max_lev = 1.0
        return float(max_lev) if max_lev is not None else 1.0

    def _lev_prep(
        self,
        pair: str,
        leverage: float,
        side: BuySell,
        accept_fail: bool = False,
    ) -> None:
        market = self.markets.get(pair, {})
        if not market:
            return
        self._api.set_leverage(
            leverage=round(leverage),
            symbol=pair,
        )

    # ──────────────────────────────────────────────────────────────
    # 청산가 계산
    # ──────────────────────────────────────────────────────────────

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
        if pair not in self.markets:
            return None
        market = self.markets[pair]
        info = market.get("info", {})
        # [BUG-01] 수정: 실제 key는 closeout_margin_fraction (not closeout_fraction)
        mm_fraction = float(info.get("maintenance_margin_fraction", 120)) / 10000
        closeout_fraction = float(info.get("closeout_margin_fraction", 80)) / 10000
        effective_mm = max(mm_fraction, closeout_fraction)
        margin = stake_amount / leverage
        mm_value = amount * open_rate * effective_mm
        try:
            if not is_short:
                liq = open_rate - (margin - mm_value) / amount
            else:
                liq = open_rate + (margin - mm_value) / amount
        except ZeroDivisionError:
            return None
        return max(liq, 0.0) if not is_short else liq

    # ──────────────────────────────────────────────────────────────
    # 펀딩비
    # ──────────────────────────────────────────────────────────────

    def get_funding_fees(
        self,
        pair: str,
        amount: float,
        is_short: bool,
        open_date,
    ) -> float:
        """
        Lighter USDC perp 펀딩비 조회.
        fetchFundingRateHistory 미지원 시 0.0 fallback.
        rate > 0: long 지불 / short 수취
        rate < 0: long 수취 / short 지불
        """
        try:
            if not self._api.has.get("fetchFundingRateHistory", False):
                return 0.0
            since_ms = int(open_date.timestamp() * 1000)
            funding_history = self._api.fetch_funding_rate_history(
                pair, since=since_ms
            )
            total_fee = 0.0
            for entry in funding_history:
                rate = float(entry.get("fundingRate", 0))
                mark_price = float(
                    entry.get("markPrice") or entry.get("price") or open_date and 0 or 1.0
                )
                # long: rate > 0이면 지불(-), rate < 0이면 수취(+)
                # short: rate > 0이면 수취(+), rate < 0이면 지불(-)
                sign = -1.0 if not is_short else 1.0
                total_fee += amount * mark_price * rate * sign
            return total_fee
        except Exception as e:
            logger.warning("get_funding_fees failed for %s: %s", pair, e)
            return 0.0

    # ──────────────────────────────────────────────────────────────
    # 주문 조회 — fetch_order (open+closed fallback)
    # ──────────────────────────────────────────────────────────────

    def fetch_order(
        self,
        order_id: str,
        pair: str,
        params: Optional[dict] = None,
    ) -> dict:
        """
        Lighter ccxt fetchOrder → open+closed 순서 fallback.
        에러 타입별 처리:
          AuthenticationError → OperationalException (즉시 중단)
          NetworkError/Timeout → RetryableOrderError (재시도)
          RateLimitExceeded   → RetryableOrderError
          BaseError           → 로그 후 closed 조회 계속
        """
        if params is None:
            params = {}

        # open orders 조회
        try:
            open_orders = self._api.fetch_open_orders(pair)
            for o in open_orders:
                if str(o.get("id")) == str(order_id):
                    return o
        except ccxt.AuthenticationError as e:
            raise OperationalException(
                f"Lighter authentication failed (open orders): {e}"
            ) from e
        except ccxt.RateLimitExceeded as e:
            raise RetryableOrderError(
                f"Lighter rate limit exceeded (open orders, {order_id}): {e}"
            ) from e
        except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
            raise RetryableOrderError(
                f"Lighter network error (open orders, {order_id}): {e}"
            ) from e
        except ccxt.BaseError as e:
            logger.warning("fetch_order open_orders error %s/%s: %s", order_id, pair, e)

        # closed orders 조회
        try:
            closed_orders = self._api.fetch_closed_orders(pair)
            for o in closed_orders:
                if str(o.get("id")) == str(order_id):
                    return o
        except ccxt.AuthenticationError as e:
            raise OperationalException(
                f"Lighter authentication failed (closed orders): {e}"
            ) from e
        except ccxt.RateLimitExceeded as e:
            raise RetryableOrderError(
                f"Lighter rate limit exceeded (closed orders, {order_id}): {e}"
            ) from e
        except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
            raise RetryableOrderError(
                f"Lighter network error (closed orders, {order_id}): {e}"
            ) from e
        except ccxt.BaseError as e:
            logger.warning("fetch_order closed_orders error %s/%s: %s", order_id, pair, e)

        raise RetryableOrderError(
            f"Order {order_id} not found on Lighter (open+closed checked, pair={pair})"
        )

    # ──────────────────────────────────────────────────────────────
    # 스톱로스 주문
    # ──────────────────────────────────────────────────────────────

    def stoploss(
        self,
        pair: str,
        amount: float,
        stop_price: float,
        order_types: dict,
        side: BuySell,
        leverage: float,
    ) -> dict:
        """
        Lighter stop-limit 주문.
        ccxt lighter: createStopLimitOrder = False → limit + triggerPrice params 방식.
        _get_stop_limit_rate / _get_stop_params: base Exchange 메서드 활용.
        """
        has_stop = self._api.has.get("createStopLimitOrder", False)
        if not has_stop:
            logger.warning(
                "Lighter createStopLimitOrder=False. "
                "Using limit order + triggerPrice params (virtual stop)."
            )

        limit_rate = self._get_stop_limit_rate(stop_price, order_types, side)
        params = self._get_stop_params(side, stop_price, limit_rate)

        try:
            order = self._api.create_order(
                symbol=pair,
                type="limit",
                side=side,
                amount=amount,
                price=limit_rate,
                params=params,
            )
        except ccxt.BaseError as e:
            raise OperationalException(
                f"Lighter stoploss failed for {pair} @ {stop_price}: {e}"
            ) from e

        order["ft_order_side"] = side
        return order

    def _get_stop_params(
        self, side: BuySell, stop_price: float, limit_rate: float
    ) -> dict:
        params = super()._get_stop_params(side, stop_price, limit_rate)
        params["orderType"] = "stop_limit"
        params["triggerPrice"] = stop_price
        return params

    # ──────────────────────────────────────────────────────────────
    # NOTE: price_to_precision / amount_to_precision override 제거 [BUG-02, BUG-03]
    #
    # Lighter ccxt precisionMode = TICK_SIZE (4).
    # Freqtrade base Exchange.price_to_precision(pair, price, *, rounding_mode=ROUND)이
    # TICK_SIZE를 올바르게 처리하므로 override 불필요.
    #
    # 기존 custom override의 문제:
    #   - is_stop 파라미터가 base의 rounding_mode와 충돌
    #   - Freqtrade 내부에서 price_to_precision(pair, price, rounding_mode=ROUND_UP) 호출 시 TypeError
    #   - amount의 Decimal quantize와 base TICK_SIZE 처리 방식 불일치
    # ──────────────────────────────────────────────────────────────
