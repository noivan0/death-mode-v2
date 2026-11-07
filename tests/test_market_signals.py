"""Unit tests for market signal helpers (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.exchange.lighter_marketdata import (
    BookLevel,
    Candle,
    MarketBundle,
    analyze_bundle,
    atr,
    book_imbalance,
    ema,
    session_vwap,
)


def _candles_uptrend(n: int = 80, start: float = 100.0) -> list[Candle]:
    out = []
    px = start
    for i in range(n):
        # gentle uptrend with small pullbacks
        drift = 0.15 if i % 7 else -0.4
        o = px
        c = px + drift
        h = max(o, c) + 0.3
        l = min(o, c) - 0.3
        out.append(Candle(ts_ms=1_700_000_000_000 + i * 300_000, open=o, high=h, low=l, close=c, volume=10 + i % 5))
        px = c
    return out


def test_ema_and_vwap_monotonic_helpers():
    vals = [1, 2, 3, 4, 5.0]
    e = ema(vals, 3)
    assert len(e) == 5
    assert e[-1] > e[0]
    candles = [Candle(i, v, v + 1, v - 1, v, 2.0) for i, v in enumerate(vals, 1)]
    vw = session_vwap(candles)
    assert len(vw) == 5
    assert vw[-1] > 0


def test_book_imbalance():
    bids = [BookLevel(100, 5), BookLevel(99, 5)]
    asks = [BookLevel(101, 2), BookLevel(102, 2)]
    imb = book_imbalance(bids, asks)
    assert imb > 0.3


def test_analyze_pullback_longish():
    c5 = _candles_uptrend()
    # force last close near EMA by flattening last bars a bit
    last = c5[-1]
    c5[-1] = Candle(last.ts_ms, last.open, last.high, last.low, last.open, last.volume)
    bundle = MarketBundle(
        symbol="BTC",
        market_id=1,
        details=None,
        candles_5m=c5,
        candles_1h=c5[::12],
        mark_5m=c5,
        fundings=[],
        venue_funding_rate=-0.0001,
        bids=[BookLevel(c5[-1].close - 1, 3)],
        asks=[BookLevel(c5[-1].close + 1, 1)],
        trades=[],
        oracle={"index": {"stale": False}, "mark": {"stale": False}},
    )
    report = analyze_bundle(bundle)
    assert report.entry > 0
    assert report.stop < report.entry
    assert report.target > report.entry
    assert atr(c5, 14) > 0
    # confidence should be reasonable; side may be long if pullback detected
    assert 0.0 <= report.confidence <= 1.0
