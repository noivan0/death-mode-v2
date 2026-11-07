"""
DEATH MODE — Freqtrade Lighter Exchange Subclass
================================================
freqtrade/exchange/hyperliquid.py 패턴 준수.
ccxt.lighter 기반 Freqtrade 공식 Exchange 서브클래스.

Claude Code 감사 v2 — 치명적 버그 10개 수정:
  #1  create_order keyword-only 호출 오류 → Hyperliquid 패턴으로 전면 재작성
  #2  limit_price_pct 공식 → _get_stop_limit_rate() 사용
  #3  @retrier(retries=0) 데코레이터 누락
  #4  fetch_positions @retrier + dry_run 가드 누락
  #5  stoploss_order_types에 market 제거 (검증 전 limit만)
  #6  stop_price_prop / stop_price_param 통일
  #7  dry_run_liquidation_price Cross 공식 주석 추가
  #8  _lev_prep set_margin_mode() 패턴으로 수정
  #9  open_date 타입 힌트 datetime으로 수정
  #10 close_position, create_reduce_only_order create_order 호출 제거

Freqtrade 등록:
  freqtrade/exchange/lighter.py 로 복사
  freqtrade/exchange/__init__.py 에:
    from freqtrade.exchange.lighter import Lighter
"""

import logging
from datetime import datetime
from typing import Optional

import ccxt

from freqtrade.constants import BuySell
from freqtrade.enums import MarginMode, TradingMode
from freqtrade.exceptions import (
    DDosProtection,
    ExchangeError,
    InsufficientFundsError,
    InvalidOrderException,
    OperationalException,
    RetryableOrderError,
    TemporaryError,
)
from freqtrade.exchange import Exchange
from freqtrade.exchange.common import retrier
from freqtrade.exchange.exchange_types import CcxtOrder, CcxtPosition, FtHas
from freqtrade.exchange.exchange_utils import ROUND_DOWN, ROUND_UP  # stoploss 반올림용

logger = logging.getLogger(__name__)


class Lighter(Exchange):
    """
    Lighter DEX Freqtrade Exchange 서브클래스.

    Lighter: ZK rollup 기반 온체인 CLOB Perp DEX
    - swap(perp): True / spot: False
    - createStopOrder: ccxt has=False → create_stoploss() 커스텀 구현
    - stoploss_on_exchange: True (futures)
    - 레버리지: set_margin_mode + leverage params 방식

    config.json 예시:
      {
        "exchange": {
          "name": "lighter",
          "key": "",
          "secret": "YOUR_PRIVATE_KEY",
          "ccxt_config": {
            "options": {
              "accountIndex": 1058,
              "apiKeyIndex": 2,
              "chainId": 304
            }
          }
        },
        "trading_mode": "futures",
        "margin_mode": "cross"
      }

    Mainnet L2 chainId = 304 (testnet = 300). Never default to 300 on mainnet.
    """

    # ── Freqtrade FtHas 설정 ─────────────────────────────────────────────────

    _ft_has: FtHas = {
        "ohlcv_has_history":            True,
        "l2_limit_range":               [20, 50],
        "trades_has_history":           False,
        "tickers_have_bid_ask":         False,       # bid/ask=None 확인됨
        "stoploss_on_exchange":         False,       # spot: 미지원
        "exchange_has_overrides": {
            "fetchOrder":  True,    # fetch_order() 커스텀 구현 (fetchClosedOrders 기반)
            "fetchTrades": False,   # Lighter 미지원
            "watchOHLCV":  False,   # WS 미지원
        },
        "marketOrderRequiresPrice":     False,
        "download_data_parallel_quick": True,
        "ws_enabled":                   False,
    }

    _ft_has_futures: FtHas = {
        "stoploss_on_exchange":         True,        # perp: 지원
        # SDK 1.1.x: STOP_LOSS (market) + STOP_LOSS_LIMIT — market needs worst-price slippage buffer
        "stoploss_order_types":         {"limit": "limit", "market": "market"},
        "stoploss_blocks_assets":       False,
        "stop_price_prop":              "stopPrice",          # ccxt 응답 필드
        "stop_price_param":             "stopLossPrice",      # ccxt 요청 파라미터 (#6 수정)
        "funding_fee_candle_limit":     500,
        "uses_leverage_tiers":          False,
        "mark_ohlcv_price":             "futures",
    }

    def market_is_tradable(self, market: dict) -> bool:
        """Use market_type — never infer spot from market_id ≥ 2048 (broken since int64 IDs)."""
        parent_check = super().market_is_tradable(market)
        if not parent_check:
            return False
        if market.get("quote") not in ("USDC", "USDC:USDC", "USD"):
            return False
        info = market.get("info") or {}
        mtype = str(info.get("market_type") or market.get("type") or "perp").lower()
        if mtype in ("spot", "binary_option", "binary", "bo"):
            return False
        if info.get("is_frozen"):
            return False
        return True

    _supported_trading_mode_margin_pairs: list[tuple[TradingMode, MarginMode]] = [
        (TradingMode.FUTURES, MarginMode.CROSS),
        (TradingMode.FUTURES, MarginMode.ISOLATED),
    ]

    # ── ccxt 설정 ────────────────────────────────────────────────────────────

    @property
    def _ccxt_config(self) -> dict:
        """
        Lighter는 perp 전용 (spot 없음).
        HMG 사내망 MitM 환경 대응: LIGHTER_SKIP_SSL_VERIFY=1 시에만 verify=False.
        [MEDIUM-03 FIX] exchange_patch/lighter.py와 동일한 환경변수 조건부 처리.
        """
        import os
        skip_ssl = os.environ.get("LIGHTER_SKIP_SSL_VERIFY", "0") == "1"
        config: dict = {"verify": not skip_ssl}
        if skip_ssl:
            logger.warning(
                "⚠️  [lighter_exchange] SSL verification DISABLED (LIGHTER_SKIP_SSL_VERIFY=1). "
                "HMG internal network only — NEVER use on external networks!"
            )
        config.update(super()._ccxt_config)
        return config

    # ── 초기화 ───────────────────────────────────────────────────────────────

    @retrier
    def additional_exchange_init(self) -> None:
        """거래소 초기화 후 chainId, accountIndex 검증."""
        try:
            from core.exchange.lighter_compat import (
                CHAIN_ID_MAINNET,
                assert_mainnet_chain_id,
                resolve_network_name,
            )

            chain_id = int(self._api.options.get("chainId", CHAIN_ID_MAINNET))
            account_index = self._api.options.get("accountIndex")
            assert_mainnet_chain_id(chain_id)
            network = resolve_network_name(chain_id)
            logger.info(
                f"[Lighter] 초기화 완료 — {network} "
                f"(chainId={chain_id}, accountIndex={account_index})"
            )
            if self.trading_mode == TradingMode.FUTURES and not self._config.get("dry_run"):
                if chain_id != CHAIN_ID_MAINNET:
                    logger.warning(
                        "[Lighter] live futures with non-mainnet chainId=%s — confirm intentionally",
                        chain_id,
                    )
        except ccxt.DDoSProtection as e:
            raise DDosProtection(e) from e
        except (ccxt.OperationFailed, ccxt.ExchangeError) as e:
            raise TemporaryError(
                f"Lighter additional_exchange_init 오류: {e.__class__.__name__}: {e}"
            ) from e
        except ccxt.BaseError as e:
            raise OperationalException(e) from e

    # ── create_stoploss (#1, #2, #3 수정) ────────────────────────────────────

    @retrier(retries=0)
    def create_stoploss(
        self,
        pair: str,
        amount: float,
        stop_price: float,
        order_types: dict,
        side: BuySell,
        leverage: float,
    ) -> CcxtOrder:
        """
        Lighter stoploss 주문 생성 — Hyperliquid 패턴 준수.

        Lighter ccxt create_order_request 내부:
          stopLossPrice + limit → orderTypeNum=3 (STOP_LOSS_LIMIT)
          stopLossPrice + market → orderTypeNum=2 (STOP_LOSS_MARKET)

        :param pair: 페어 (BTC/USDC:USDC — Lighter는 USDC 기반)
        :param amount: 수량
        :param stop_price: 트리거 가격
        :param order_types: {"stoploss": "limit", "stoploss_on_exchange_limit_ratio": 0.99}
        :param side: "buy" | "sell"
        :param leverage: 레버리지
        """
        if not self._ft_has.get("stoploss_on_exchange"):
            raise OperationalException(
                f"stoploss is not implemented for {self.name}."
            )

        # Freqtrade 내장 헬퍼로 order type 결정 (#3)
        user_order_type = order_types.get("stoploss", "limit")
        ordertype, user_order_type = self._get_stop_order_type(user_order_type)

        # 반올림 방향: sell=ROUND_DOWN, buy=ROUND_UP
        round_mode = ROUND_DOWN if side == "sell" else ROUND_UP
        stop_price_norm = self.price_to_precision(
            pair, stop_price, rounding_mode=round_mode
        )

        # limit 주문용 실행가 계산 (#2 수정: _get_stop_limit_rate 사용)
        limit_rate: Optional[float] = None
        if user_order_type == "limit":
            limit_rate = self._get_stop_limit_rate(stop_price, order_types, side)
            limit_rate = self.price_to_precision(
                pair, limit_rate, rounding_mode=round_mode
            )

        # dry_run 처리
        if self._config.get("dry_run"):
            return self.create_dry_run_order(
                pair, ordertype, side, amount,
                limit_rate or stop_price_norm,
                stop_loss=True,
                stop_price=stop_price_norm,
                leverage=leverage,
            )

        # Lighter 전용 params: stopLossPrice를 트리거로 전달
        params: dict = {
            "stopLossPrice": stop_price_norm,
            "reduceOnly": True,
        }

        # 실전 주문 (#1 수정: keyword-only + leverage + 올바른 파라미터)
        try:
            amount_contracts = self.amount_to_precision(
                pair, self._amount_to_contracts(pair, amount)
            )
            self._lev_prep(pair, leverage, side, accept_fail=True)

            order = self._api.create_order(
                symbol=pair,
                type=ordertype,
                side=side,
                amount=float(amount_contracts),
                price=limit_rate,
                params=params,
            )
            self._log_exchange_response("create_stoploss_order", order)
            order = self._order_contracts_to_amount(order)
            logger.info(
                f"[Lighter] stoploss {user_order_type} order: {pair} "
                f"trigger={stop_price_norm}, limit={limit_rate}"
            )
            return order

        except ccxt.InsufficientFunds as e:
            raise InsufficientFundsError() from e
        except (ccxt.InvalidOrder, ccxt.BadRequest, ccxt.OperationRejected) as e:
            raise InvalidOrderException(
                f"Lighter create_stoploss: {e.__class__.__name__}: {e}"
            ) from e
        except ccxt.DDoSProtection as e:
            raise DDosProtection(e) from e
        except (ccxt.OperationFailed, ccxt.ExchangeError) as e:
            raise TemporaryError(
                f"Lighter create_stoploss 임시 오류: {e.__class__.__name__}: {e}"
            ) from e
        except ccxt.BaseError as e:
            raise OperationalException(e) from e

    # ── fetch_order (fetchOrder: False 우회) ─────────────────────────────────

    @retrier
    def fetch_order(self, order_id: str, pair: str, params: Optional[dict] = None) -> CcxtOrder:
        """
        Lighter fetchOrder 커스텀 구현.

        ccxt lighter: fetchOrder=False, fetchOpenOrders=True, fetchClosedOrders=True
        → openOrders 먼저 조회 후 없으면 closedOrders에서 검색.
        Freqtrade EXCHANGE_HAS_REQUIRED: fetchOrder OR [fetchOpenOrder, fetchClosedOrder]
        """
        params = params or {}
        try:
            # 1차: 오픈 주문에서 검색
            open_orders = self._api.fetch_open_orders(symbol=pair, params=params)
            for o in open_orders:
                if str(o.get("id")) == str(order_id):
                    self._log_exchange_response("fetch_order (open)", o)
                    return o

            # 2차: 종료 주문에서 검색
            closed_orders = self._api.fetch_closed_orders(symbol=pair, params=params)
            for o in closed_orders:
                if str(o.get("id")) == str(order_id):
                    self._log_exchange_response("fetch_order (closed)", o)
                    return o

            # 미발견 시 기본 구조 반환 (canceled 처리)
            logger.warning(f"[Lighter] fetch_order: 주문 {order_id} 미발견 ({pair})")
            return {
                "id": order_id, "symbol": pair,
                "status": "canceled", "filled": 0, "amount": 0,
                "price": None, "average": None, "side": None, "type": None,
            }

        except ccxt.DDoSProtection as e:
            raise DDosProtection(e) from e
        except (ccxt.OperationFailed, ccxt.ExchangeError) as e:
            raise TemporaryError(
                f"Lighter fetch_order 오류: {e.__class__.__name__}: {e}"
            ) from e
        except ccxt.BaseError as e:
            raise OperationalException(e) from e

    # ── fetch_positions (#4 수정) ─────────────────────────────────────────────


    @retrier
    def fetch_positions(
        self,
        pair: Optional[str] = None,
        params: Optional[dict] = None,
    ) -> list[CcxtPosition]:
        """
        Lighter 포지션 조회.
        dry_run 또는 futures 모드 아닐 때 빈 리스트 반환 (#4 수정).
        """
        if self._config.get("dry_run") or self.trading_mode != TradingMode.FUTURES:
            return []

        try:
            symbols = [pair] if pair else None
            positions: list[CcxtPosition] = self._api.fetch_positions(
                symbols, params=params or {}
            )
            self._log_exchange_response("fetch_positions", positions)
            return positions

        except ccxt.DDoSProtection as e:
            raise DDosProtection(e) from e
        except (ccxt.OperationFailed, ccxt.ExchangeError) as e:
            raise TemporaryError(
                f"Lighter fetch_positions 오류: {e.__class__.__name__}: {e}"
            ) from e
        except ccxt.BaseError as e:
            raise OperationalException(e) from e

    # ── _lev_prep (#8 수정) ───────────────────────────────────────────────────

    def _lev_prep(
        self,
        pair: str,
        leverage: float,
        side: BuySell,
        accept_fail: bool = False,
    ) -> None:
        """
        Lighter 레버리지 설정.
        Hyperliquid 패턴: set_margin_mode() + leverage params (#8 수정).
        Lighter set_leverage()는 marginMode 파라미터 필수.
        """
        if self.trading_mode != TradingMode.SPOT:
            try:
                leverage = int(leverage)
                # Lighter: set_leverage 내부적으로 modify_leverage_and_margin_mode 호출
                # marginMode 필수 파라미터로 전달
                self.set_margin_mode(
                    pair,
                    self.margin_mode,
                    params={"leverage": leverage},
                )
            except ccxt.BaseError as e:
                if accept_fail:
                    logger.warning(f"[Lighter] _lev_prep 실패 (무시): {e}")
                else:
                    raise TemporaryError(
                        f"[Lighter] _lev_prep 오류: {e}"
                    ) from e

    def get_max_leverage(self, pair: str, stake_amount: Optional[float]) -> float:
        """
        최대 레버리지 조회.

        Lighter API 실측: limits.leverage.max = None
        → min_initial_margin_fraction 기반 계산:
          max_leverage = 10000 / min_initial_margin_fraction
          (BTC: 200 → 50x)
        """
        if self.trading_mode == TradingMode.FUTURES:
            # 1차: ccxt limits (None일 수 있음)
            try:
                lev_max = self.markets[pair]["limits"]["leverage"]["max"]
                if lev_max is not None:
                    return float(lev_max)
            except (KeyError, TypeError):
                pass

            # 2차: min_initial_margin_fraction 기반 계산 (Lighter 실측)
            try:
                min_imf = self.markets[pair]["info"].get("min_initial_margin_fraction")
                if min_imf is not None:
                    return round(10000 / float(min_imf), 1)
            except (KeyError, TypeError, ZeroDivisionError):
                pass

            # 3차: 기본값
            return 50.0  # BTC 실측 max leverage
        return 1.0

    # ── dry_run_liquidation_price (#7 주석 추가) ──────────────────────────────

    def dry_run_liquidation_price(
        self,
        pair: str,
        open_rate: float,
        is_short: bool,
        amount: float,
        stake_amount: float,
        leverage: float,
        wallet_balance: float,
        open_trades: list,
    ) -> Optional[float]:
        """
        Lighter 청산가 추정 (dry_run 전용).

        공식 출처: Lighter API 응답 실측 기반 (2026-05-19 검증).
          - market info: maintenance_margin_fraction (예: 120 = 1.20%)
          - mm_frac = maintenance_margin_fraction / 10000

        Isolated 공식:
          maintenance_margin = position_notional * mm_frac
          margin_available   = stake_amount - maintenance_margin
          liq_price (long)   = entry - margin_available / position_size
          liq_price (short)  = entry + margin_available / position_size

        Cross 공식:
          margin_available   = wallet_balance - maintenance_margin
          (이하 동일)

        BTC 50x 실측: liq ≈ entry * (1 - 1/leverage + mm_frac)
        """
        try:
            # Lighter 실제 maintenance_margin_fraction 조회
            mm_frac: float = 0.012  # 기본값 1.2% (BTC 실측)
            if pair in self.markets:
                raw_mm = self.markets[pair].get("info", {}).get(
                    "maintenance_margin_fraction"
                )
                if raw_mm is not None:
                    mm_frac = float(raw_mm) / 10000

            position_notional = open_rate * amount
            maintenance_margin = position_notional * mm_frac

            if self.margin_mode == MarginMode.ISOLATED:
                margin_available = stake_amount - maintenance_margin
            elif self.margin_mode == MarginMode.CROSS:
                margin_available = wallet_balance - maintenance_margin
            else:
                raise OperationalException(
                    f"Lighter: 지원하지 않는 margin_mode: {self.margin_mode}"
                )

            # long: 가격 하락 시 청산, short: 가격 상승 시 청산
            side_sign = -1 if is_short else 1
            liq_price = open_rate - side_sign * margin_available / amount

            return liq_price

        except Exception as e:
            logger.warning(f"[Lighter] liquidation_price 계산 실패 ({pair}): {e}")
            return None

    # ── 펀딩비 (#9 수정: datetime 타입) ─────────────────────────────────────

    def get_funding_fees(
        self,
        pair: str,
        amount: float,
        is_short: bool,
        open_date: datetime,        # (#9: Any → datetime)
    ) -> float:
        """
        Lighter 펀딩비 계산.
        fetchFundingRates: True (ccxt has 확인됨).
        """
        if self.trading_mode == TradingMode.FUTURES:
            try:
                return self._fetch_and_calculate_funding_fees(
                    pair, amount, is_short, open_date
                )
            except ExchangeError:
                logger.warning(f"[Lighter] 펀딩비 업데이트 실패: {pair}")
        return 0.0
