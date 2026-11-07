"""Lighter public market-data client for analysis / trading verification.

Uses unauthenticated REST (docs.lighter.xyz):
  - /api/v1/candles            trade OHLCV (max 500/call)
  - /api/v1/markPriceCandles   mark OHLCV
  - /api/v1/fundings           funding history
  - /api/v1/funding-rates      multi-venue current rates
  - /api/v1/orderBookOrders    L2 book
  - /api/v1/recentTrades       tape
  - /api/v1/orderBookDetails   OI / fees / frozen
  - /api/v1/priceOracleInfo    oracle staleness

Designed to feed Cardinal pullback + RiskGate without going through ccxt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import math
import time
import urllib.parse

from .lighter_compat import LighterEndpoints, MarketSnapshot, _http_get_json, parse_market


RESOLUTION_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "12h": 43200,
    "1d": 86400,
}

# Known core perps (orderBookDetails remains source of truth)
DEFAULT_SYMBOL_IDS = {"ETH": 0, "BTC": 1, "SOL": 2, "XRP": 7, "BNB": 25}


@dataclass
class Candle:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float = 0.0

    def as_ohlcv(self) -> list[float]:
        return [float(self.ts_ms), self.open, self.high, self.low, self.close, self.volume]


@dataclass
class BookLevel:
    price: float
    size: float


@dataclass
class TradeTape:
    trade_id: str
    price: float
    size: float
    usd_amount: float
    is_buyer_maker: Optional[bool] = None
    ts_us: Optional[int] = None


@dataclass
class FundingPoint:
    ts: int
    rate: float
    value: float
    direction: str


@dataclass
class MarketBundle:
    """One-symbol snapshot used by verification / signal layer."""

    symbol: str
    market_id: int
    details: Optional[MarketSnapshot]
    candles_5m: list[Candle] = field(default_factory=list)
    candles_1h: list[Candle] = field(default_factory=list)
    mark_5m: list[Candle] = field(default_factory=list)
    fundings: list[FundingPoint] = field(default_factory=list)
    venue_funding_rate: Optional[float] = None
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)
    trades: list[TradeTape] = field(default_factory=list)
    oracle: dict[str, Any] = field(default_factory=dict)


class LighterMarketData:
    def __init__(
        self,
        endpoints: Optional[LighterEndpoints] = None,
        verify_ssl: Optional[bool] = None,
    ):
        import os
        self.endpoints = endpoints or LighterEndpoints.from_env()
        if verify_ssl is None:
            verify_ssl = os.environ.get("LIGHTER_SKIP_SSL_VERIFY", "0") != "1"
        self.verify_ssl = verify_ssl
        self._symbol_ids: dict[str, int] = dict(DEFAULT_SYMBOL_IDS)

    def _get(self, path: str, params: dict) -> dict:
        qs = urllib.parse.urlencode(params)
        url = f"{self.endpoints.rest}{path}?{qs}"
        return _http_get_json(url, verify_ssl=self.verify_ssl)

    def refresh_symbol_map(self) -> dict[str, int]:
        data = _http_get_json(
            f"{self.endpoints.rest}/api/v1/orderBookDetails",
            verify_ssl=self.verify_ssl,
        )
        mapping = {}
        for row in data.get("order_book_details") or []:
            if str(row.get("market_type") or "") != "perp":
                continue
            sym = str(row.get("symbol") or "")
            mid = int(row.get("market_id") or 0)
            if sym:
                mapping[sym] = mid
        if mapping:
            self._symbol_ids = mapping
        return self._symbol_ids

    def market_id(self, symbol: str) -> int:
        sym = symbol.upper().replace("/USDC:USDC", "").replace("/USDC", "")
        if sym not in self._symbol_ids:
            self.refresh_symbol_map()
        if sym not in self._symbol_ids:
            raise KeyError(f"Unknown Lighter symbol: {symbol}")
        return self._symbol_ids[sym]

    # ── candles ─────────────────────────────────────────────────
    def candles(
        self,
        market_id: int,
        resolution: str = "5m",
        *,
        lookback: int = 120,
        end_ts: Optional[int] = None,
    ) -> list[Candle]:
        if resolution not in RESOLUTION_SECONDS:
            raise ValueError(f"unsupported resolution {resolution}")
        end = int(end_ts or time.time())
        start = end - lookback * RESOLUTION_SECONDS[resolution]
        data = self._get(
            "/api/v1/candles",
            {
                "market_id": market_id,
                "resolution": resolution,
                "start_timestamp": start,
                "end_timestamp": end,
                "count_back": lookback,
            },
        )
        out: list[Candle] = []
        for row in data.get("c") or []:
            out.append(
                Candle(
                    ts_ms=int(row["t"]),
                    open=float(row["o"]),
                    high=float(row["h"]),
                    low=float(row["l"]),
                    close=float(row["c"]),
                    volume=float(row.get("v") or 0),
                    quote_volume=float(row.get("V") or 0),
                )
            )
        return out

    def mark_candles(
        self,
        market_id: int,
        resolution: str = "5m",
        *,
        lookback: int = 120,
        end_ts: Optional[int] = None,
    ) -> list[Candle]:
        end = int(end_ts or time.time())
        start = end - lookback * RESOLUTION_SECONDS[resolution]
        data = self._get(
            "/api/v1/markPriceCandles",
            {
                "market_id": market_id,
                "resolution": resolution,
                "start_timestamp": start,
                "end_timestamp": end,
                "count_back": lookback,
            },
        )
        out: list[Candle] = []
        for row in data.get("c") or []:
            out.append(
                Candle(
                    ts_ms=int(row["t"]),
                    open=float(row["o"]),
                    high=float(row["h"]),
                    low=float(row["l"]),
                    close=float(row["c"]),
                    volume=0.0,
                )
            )
        return out

    def fundings(
        self,
        market_id: int,
        *,
        lookback_hours: int = 24,
        resolution: str = "1h",
    ) -> list[FundingPoint]:
        end = int(time.time())
        start = end - lookback_hours * 3600
        data = self._get(
            "/api/v1/fundings",
            {
                "market_id": market_id,
                "resolution": resolution,
                "start_timestamp": start,
                "end_timestamp": end,
                "count_back": lookback_hours,
            },
        )
        out = []
        for row in data.get("fundings") or []:
            out.append(
                FundingPoint(
                    ts=int(row.get("timestamp") or 0),
                    rate=float(row.get("rate") or 0),
                    value=float(row.get("value") or 0),
                    direction=str(row.get("direction") or ""),
                )
            )
        return out

    def funding_rates(self) -> dict[str, float]:
        """Multi-venue current rates keyed by SYMBOL@exchange (Lighter rows preferred)."""
        data = _http_get_json(
            f"{self.endpoints.rest}/api/v1/funding-rates",
            verify_ssl=self.verify_ssl,
        )
        out: dict[str, float] = {}
        for row in data.get("funding_rates") or []:
            sym = str(row.get("symbol") or "")
            ex = str(row.get("exchange") or "").lower()
            rate = float(row.get("rate") or 0)
            key = f"{sym}@{ex}"
            out[key] = rate
            if ex in ("lighter", "zklighter", ""):
                out[sym] = rate
        return out

    def order_book(self, market_id: int, limit: int = 20) -> tuple[list[BookLevel], list[BookLevel]]:
        data = self._get(
            "/api/v1/orderBookOrders",
            {"market_id": market_id, "limit": limit},
        )
        bids = [
            BookLevel(float(x["price"]), float(x.get("remaining_base_amount") or x.get("initial_base_amount") or 0))
            for x in (data.get("bids") or [])
        ]
        asks = [
            BookLevel(float(x["price"]), float(x.get("remaining_base_amount") or x.get("initial_base_amount") or 0))
            for x in (data.get("asks") or [])
        ]
        return bids, asks

    def recent_trades(self, market_id: int, limit: int = 50) -> list[TradeTape]:
        data = self._get(
            "/api/v1/recentTrades",
            {"market_id": market_id, "limit": limit},
        )
        out = []
        for row in data.get("trades") or []:
            out.append(
                TradeTape(
                    trade_id=str(row.get("trade_id_str") or row.get("trade_id") or ""),
                    price=float(row.get("price") or 0),
                    size=float(row.get("size") or 0),
                    usd_amount=float(row.get("usd_amount") or 0),
                    ts_us=int(row["transaction_time"]) if row.get("transaction_time") else None,
                )
            )
        return out

    def details(self, symbol: str) -> Optional[MarketSnapshot]:
        data = _http_get_json(
            f"{self.endpoints.rest}/api/v1/orderBookDetails",
            verify_ssl=self.verify_ssl,
        )
        for row in data.get("order_book_details") or []:
            if str(row.get("symbol") or "").upper() == symbol.upper():
                return parse_market(row)
        return None

    def oracle_for(self, symbol: str) -> dict[str, Any]:
        data = _http_get_json(
            f"{self.endpoints.rest}/api/v1/priceOracleInfo",
            verify_ssl=self.verify_ssl,
        )
        for row in data.get("markets") or []:
            if str(row.get("symbol") or "").upper() == symbol.upper():
                return row
        return {}

    def fetch_bundle(
        self,
        symbol: str,
        *,
        candle_5m: int = 120,
        candle_1h: int = 48,
        book_limit: int = 15,
        trades: int = 40,
    ) -> MarketBundle:
        mid = self.market_id(symbol)
        bids, asks = self.order_book(mid, limit=book_limit)
        rates = self.funding_rates()
        lighter_rate = rates.get(symbol.upper())
        if lighter_rate is None:
            # fall back to any venue average for the symbol
            vals = [v for k, v in rates.items() if k.startswith(symbol.upper() + "@") or k == symbol.upper()]
            lighter_rate = sum(vals) / len(vals) if vals else None
        return MarketBundle(
            symbol=symbol.upper(),
            market_id=mid,
            details=self.details(symbol),
            candles_5m=self.candles(mid, "5m", lookback=candle_5m),
            candles_1h=self.candles(mid, "1h", lookback=candle_1h),
            mark_5m=self.mark_candles(mid, "5m", lookback=min(candle_5m, 120)),
            fundings=self.fundings(mid, lookback_hours=24),
            venue_funding_rate=lighter_rate,
            bids=bids,
            asks=asks,
            trades=self.recent_trades(mid, limit=trades),
            oracle=self.oracle_for(symbol),
        )


# ── signal helpers (Cardinal / Martin Luk) ───────────────────────

def ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def session_vwap(candles: list[Candle]) -> list[float]:
    out = []
    cum_pv = 0.0
    cum_v = 0.0
    for c in candles:
        typical = (c.high + c.low + c.close) / 3.0
        vol = c.volume if c.volume > 0 else 0.0
        cum_pv += typical * vol
        cum_v += vol
        out.append(cum_pv / cum_v if cum_v > 0 else c.close)
    return out


def atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        prev = candles[i - 1].close
        c = candles[i]
        tr = max(c.high - c.low, abs(c.high - prev), abs(c.low - prev))
        trs.append(tr)
    window = trs[-period:]
    return sum(window) / len(window) if window else 0.0


def book_imbalance(bids: list[BookLevel], asks: list[BookLevel], depth: int = 10) -> float:
    bid_sz = sum(b.size for b in bids[:depth])
    ask_sz = sum(a.size for a in asks[:depth])
    tot = bid_sz + ask_sz
    if tot <= 0:
        return 0.0
    return (bid_sz - ask_sz) / tot


@dataclass
class SignalReport:
    symbol: str
    side: str                 # long|short|none
    entry: float
    stop: float
    target: float
    confidence: float
    reasons: list[str]
    meta: dict[str, Any] = field(default_factory=dict)

    def as_plan_dict(self) -> dict:
        return {
            "symbol": f"{self.symbol}/USDC:USDC",
            "side": self.side,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "confidence": self.confidence,
        }


def analyze_bundle(
    bundle: MarketBundle,
    *,
    min_rr: float = 2.0,
    pullback_tol: float = 0.008,
    stop_atr_mult: float = 1.2,
) -> SignalReport:
    """Martin Luk style: prefer EMA/VWAP pullback in uptrend; size later via RiskGate."""
    c5 = bundle.candles_5m
    if len(c5) < 30:
        return SignalReport(bundle.symbol, "none", 0, 0, 0, 0.0, ["insufficient candles"])

    closes = [c.close for c in c5]
    ema_fast = ema(closes, 9)
    ema_slow = ema(closes, 21)
    vwap = session_vwap(c5)
    last = c5[-1]
    price = last.close
    ef, es, vw = ema_fast[-1], ema_slow[-1], vwap[-1]
    uptrend = ef >= es
    near_ema = min(abs(price - ef), abs(price - es)) / price <= pullback_tol
    near_vwap = abs(price - vw) / price <= pullback_tol
    pullback = (near_ema or near_vwap) and uptrend

    a = atr(c5, 14)
    if a <= 0:
        a = price * 0.01
    stop = price - stop_atr_mult * a
    risk = price - stop
    # slight buffer so float rounding never fails min_rr at the gate
    target = price + (min_rr + 1e-4) * risk

    imb = book_imbalance(bundle.bids, bundle.asks)
    reasons = []
    confidence = 0.45

    if uptrend:
        reasons.append("ema_fast>=ema_slow")
        confidence += 0.1
    if pullback:
        reasons.append("pullback_ema_or_vwap")
        confidence += 0.2
    else:
        reasons.append("no_pullback")

    if imb > 0.15:
        reasons.append(f"bid_heavy:{imb:.2f}")
        confidence += 0.08
    elif imb < -0.15:
        reasons.append(f"ask_heavy:{imb:.2f}")
        confidence -= 0.08

    # Funding: positive rate → longs pay → slightly fade aggressive longs
    fr = bundle.venue_funding_rate
    if fr is None and bundle.fundings:
        fr = bundle.fundings[-1].rate
    if fr is not None:
        reasons.append(f"funding:{fr:.5f}")
        if fr > 0.0005:
            confidence -= 0.05
        elif fr < -0.0005:
            confidence += 0.05

    # Oracle / freeze / OI gates
    details = bundle.details
    if details and details.is_frozen:
        return SignalReport(bundle.symbol, "none", price, stop, target, 0.0, ["frozen"])
    if details and details.oi_utilization and details.oi_utilization >= 0.85:
        reasons.append("oi_util_high")
        confidence -= 0.2

    idx = (bundle.oracle.get("index") or {})
    mark = (bundle.oracle.get("mark") or {})
    if idx.get("stale") or mark.get("stale") or idx.get("awaiting_sources") or mark.get("awaiting_sources"):
        reasons.append("oracle_stale")
        confidence -= 0.25

    # Trade vs mark divergence
    if bundle.mark_5m and c5:
        mark_px = bundle.mark_5m[-1].close
        div = abs(price - mark_px) / price if price else 0
        reasons.append(f"trade_mark_div:{div:.4f}")
        if div > 0.003:
            confidence -= 0.1

    confidence = max(0.0, min(1.0, confidence))
    side = "long" if pullback and uptrend and confidence >= 0.55 else "none"
    if side == "none" and not pullback:
        # still emit plan levels for journal / dry gate testing when trend ok
        pass

    return SignalReport(
        symbol=bundle.symbol,
        side=side,
        entry=round(price, 6),
        stop=round(stop, 6),
        target=round(target, 6),
        confidence=round(confidence, 3),
        reasons=reasons,
        meta={
            "atr": a,
            "ema_fast": ef,
            "ema_slow": es,
            "vwap": vw,
            "book_imbalance": imb,
            "funding": fr,
            "oi_util": details.oi_utilization if details else None,
            "oi_usd": details.open_interest_usd if details else None,
            "mark": bundle.mark_5m[-1].close if bundle.mark_5m else None,
            "candles_5m": len(c5),
            "candles_1h": len(bundle.candles_1h),
            "trades": len(bundle.trades),
            "rr": min_rr,
        },
    )
