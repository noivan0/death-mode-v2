"""OKX public market data for the Cardinal desk.

No API key. No order, transfer, or withdraw endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional
import json
import urllib.parse
import urllib.request

from .lighter_compat import MarketSnapshot
from .lighter_marketdata import (
    BookLevel,
    Candle,
    FundingPoint,
    MarketBundle,
    TradeTape,
)

OKX_REST = "https://www.okx.com"

DEFAULT_SWAP_IDS = (
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "BNB-USDT-SWAP",
    "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "ADA-USDT-SWAP",
    "AVAX-USDT-SWAP",
    "LINK-USDT-SWAP",
)


@dataclass
class SwapSpec:
    inst_id: str
    base: str
    quote: str
    ct_val: float
    min_sz: float
    lot_sz: float
    tick_sz: float
    state: str
    ct_val_ccy: str

    @property
    def pair(self) -> str:
        return f"{self.base}/{self.quote}:{self.quote}"

    @property
    def live(self) -> bool:
        return self.state == "live"


def parse_swap_spec(row: dict) -> SwapSpec:
    inst_id = str(row.get("instId") or "")
    parts = inst_id.split("-")
    base = parts[0] if parts else ""
    quote = parts[1] if len(parts) > 1 else "USDT"
    return SwapSpec(
        inst_id=inst_id,
        base=base,
        quote=quote,
        ct_val=float(row.get("ctVal") or 0),
        min_sz=float(row.get("minSz") or 0),
        lot_sz=float(row.get("lotSz") or 0),
        tick_sz=float(row.get("tickSz") or 0),
        state=str(row.get("state") or ""),
        ct_val_ccy=str(row.get("ctValCcy") or base),
    )


def parse_candles(rows: list[list[Any]]) -> list[Candle]:
    """OKX returns newest first. Output is oldest first and de-duplicated."""
    by_ts: dict[int, Candle] = {}
    for row in rows:
        if len(row) < 6:
            continue
        ts = int(row[0])
        quote = float(row[7]) if len(row) > 7 and row[7] not in (None, "") else 0.0
        by_ts[ts] = Candle(
            ts_ms=ts,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5] or 0),
            quote_volume=quote,
        )
    return [by_ts[ts] for ts in sorted(by_ts)]


class OkxPublicClient:
    def __init__(self, base: str = OKX_REST, get_json: Optional[Callable[[str], dict]] = None):
        self.base = base.rstrip("/")
        self._get_json = get_json or _http_get_json

    def _get(self, path: str, params: dict) -> dict:
        query = urllib.parse.urlencode(params)
        payload = self._get_json(f"{self.base}{path}?{query}")
        code = str(payload.get("code", "0"))
        if code != "0":
            raise RuntimeError(f"okx {code} {payload.get('msg') or path}")
        return payload

    def instruments(self, inst_ids: tuple[str, ...] = DEFAULT_SWAP_IDS) -> list[SwapSpec]:
        wanted = set(inst_ids)
        payload = self._get("/api/v5/public/instruments", {"instType": "SWAP"})
        specs = [parse_swap_spec(row) for row in payload.get("data") or []]
        return [spec for spec in specs if spec.inst_id in wanted]

    def candles(self, inst_id: str, bar: str, limit: int) -> list[Candle]:
        first = min(300, limit)
        payload = self._get(
            "/api/v5/market/candles",
            {"instId": inst_id, "bar": bar, "limit": first},
        )
        rows: list[list[Any]] = list(payload.get("data") or [])
        seen_oldest: Optional[str] = None
        while len(parse_candles(rows)) < limit:
            if not rows:
                break
            oldest = str(min(int(row[0]) for row in rows))
            if oldest == seen_oldest:
                break
            seen_oldest = oldest
            need = min(100, limit - len(parse_candles(rows)))
            older = self._get(
                "/api/v5/market/history-candles",
                {"instId": inst_id, "bar": bar, "after": oldest, "limit": need},
            )
            batch = older.get("data") or []
            if not batch:
                break
            rows.extend(batch)
        parsed = parse_candles(rows)
        if len(parsed) > limit:
            parsed = parsed[-limit:]
        return parsed

    def fetch_bundle(self, spec: SwapSpec, *, candles_5m: int = 800, candles_1h: int = 120) -> MarketBundle:
        trade_5m = self.candles(spec.inst_id, "5m", candles_5m)
        trade_1h = self.candles(spec.inst_id, "1H", candles_1h)
        mark_payload = self._get(
            "/api/v5/market/mark-price-candles",
            {"instId": spec.inst_id, "bar": "5m", "limit": 100},
        )
        mark_5m = parse_candles(mark_payload.get("data") or [])
        funding_now = self._get("/api/v5/public/funding-rate", {"instId": spec.inst_id})
        funding_hist = self._get(
            "/api/v5/public/funding-rate-history",
            {"instId": spec.inst_id, "limit": 30},
        )
        book = self._get("/api/v5/market/books", {"instId": spec.inst_id, "sz": 20})
        trades = self._get("/api/v5/market/trades", {"instId": spec.inst_id, "limit": 50})
        oi_payload = self._get(
            "/api/v5/public/open-interest",
            {"instType": "SWAP", "instId": spec.inst_id},
        )
        ticker = self._get("/api/v5/market/ticker", {"instId": spec.inst_id})
        index = self._get("/api/v5/market/index-tickers", {"instId": f"{spec.base}-{spec.quote}"})

        current = (funding_now.get("data") or [{}])[0]
        rate = float(current.get("fundingRate") or 0)
        fundings = []
        for row in funding_hist.get("data") or []:
            point_rate = float(row.get("fundingRate") or 0)
            fundings.append(
                FundingPoint(
                    ts=int(row.get("fundingTime") or 0),
                    rate=point_rate,
                    value=float(row.get("realizedRate") or point_rate),
                    direction="long_pays" if point_rate > 0 else "short_pays",
                )
            )
        fundings.sort(key=lambda point: point.ts)

        book_row = (book.get("data") or [{}])[0]
        bids = [BookLevel(float(level[0]), float(level[1])) for level in book_row.get("bids") or []]
        asks = [BookLevel(float(level[0]), float(level[1])) for level in book_row.get("asks") or []]
        tape = []
        for row in trades.get("data") or []:
            px = float(row.get("px") or 0)
            sz = float(row.get("sz") or 0)
            tape.append(
                TradeTape(
                    trade_id=str(row.get("tradeId") or ""),
                    price=px,
                    size=sz,
                    usd_amount=px * sz * spec.ct_val,
                    is_buyer_maker=(row.get("side") == "sell"),
                    ts_us=int(row.get("ts") or 0) * 1000,
                )
            )

        oi_row = (oi_payload.get("data") or [{}])[0]
        tick = (ticker.get("data") or [{}])[0]
        idx = (index.get("data") or [{}])[0]
        mark_px = mark_5m[-1].close if mark_5m else float(tick.get("last") or 0)
        index_px = float(idx.get("idxPx") or 0)
        oi_contracts = float(oi_row.get("oi") or 0)
        oi_usd = float(oi_row.get("oiUsd") or 0)
        details = MarketSnapshot(
            symbol=spec.base,
            market_id=0,
            market_type="perp",
            mark_price=mark_px,
            index_price=index_px,
            open_interest=oi_contracts,
            open_interest_usd=oi_usd,
            open_interest_limit=0.0,
            is_frozen=not spec.live,
            min_base_amount=spec.min_sz * spec.ct_val,
            maker_fee=0.0,
            taker_fee=0.0,
            raw={
                "instId": spec.inst_id,
                "ctVal": spec.ct_val,
                "minSz": spec.min_sz,
                "lotSz": spec.lot_sz,
                "tickSz": spec.tick_sz,
                "state": spec.state,
                "bid": tick.get("bidPx"),
                "ask": tick.get("askPx"),
                "last": tick.get("last"),
            },
        )
        stale = index_px <= 0 or mark_px <= 0
        return MarketBundle(
            symbol=spec.base,
            market_id=0,
            details=details,
            candles_5m=trade_5m,
            candles_1h=trade_1h,
            mark_5m=mark_5m,
            fundings=fundings,
            venue_funding_rate=rate,
            bids=bids,
            asks=asks,
            trades=tape,
            oracle={
                "index": {"price": index_px, "stale": stale},
                "mark": {"price": mark_px, "stale": stale},
            },
        )


def _http_get_json(url: str, timeout: float = 20.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "death-mode-v2/okx-public"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())
