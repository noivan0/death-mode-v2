"""OKX public parser and Cardinal ticket sizing. No network."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.exchange.lighter_marketdata import SignalReport
from core.exchange.okx_public import OkxPublicClient, parse_candles, parse_swap_spec
from core.risk.risk_gate import RiskConfig, RiskGate
from core.risk.tilt_guard import TiltConfig, TiltGuard
from core.strategy.desk import cardinal_plan, evaluate_symbol, fit_swap_size


def test_parse_candles_sorts_oldest_first():
    rows = [
        ["2000", "2", "3", "1", "2.5", "10", "0", "25", "1"],
        ["1000", "1", "2", "0.5", "1.5", "8", "0", "12", "1"],
        ["2000", "2", "3", "1", "2.5", "10", "0", "25", "1"],
    ]
    candles = parse_candles(rows)
    assert [c.ts_ms for c in candles] == [1000, 2000]
    assert candles[0].close == 1.5
    assert candles[1].quote_volume == 25


def test_parse_swap_spec_pair():
    spec = parse_swap_spec(
        {
            "instId": "DOGE-USDT-SWAP",
            "ctVal": "1000",
            "minSz": "0.01",
            "lotSz": "0.01",
            "tickSz": "0.00001",
            "state": "live",
            "ctValCcy": "DOGE",
        }
    )
    assert spec.pair == "DOGE/USDT:USDT"
    assert spec.live
    assert spec.ct_val == 1000


def _client_pages() -> OkxPublicClient:
    pages = {
        "/api/v5/market/candles": [
            ["3000", "3", "4", "2", "3", "1", "0", "3", "0"],
            ["2000", "2", "3", "1", "2", "1", "0", "2", "1"],
        ],
        "/api/v5/market/history-candles": [
            ["1000", "1", "2", "0.5", "1.5", "1", "0", "1", "1"],
        ],
    }

    def get_json(url: str) -> dict:
        path = url.split("?", 1)[0]
        if path.endswith("/api/v5/market/history-candles"):
            return {"code": "0", "data": pages["/api/v5/market/history-candles"]}
        if path.endswith("/api/v5/market/candles"):
            return {"code": "0", "data": pages["/api/v5/market/candles"]}
        raise AssertionError(url)

    return OkxPublicClient(get_json=get_json)


def test_candles_paginates_history():
    candles = _client_pages().candles("BTC-USDT-SWAP", "5m", 3)
    assert [c.ts_ms for c in candles] == [1000, 2000, 3000]


def test_fit_keeps_min_contract_inside_budget():
    entry = 76000.0
    stop = entry * 0.97
    fit = fit_swap_size(
        base_qty=13.3333 / entry,
        entry=entry,
        stop=stop,
        risk_budget=0.40,
        ct_val=0.01,
        min_sz=0.01,
        lot_sz=0.01,
    )
    assert fit.allowed
    assert fit.contracts == 0.01
    assert fit.risk_amount <= 0.40
    assert fit.stake < 13.34


def test_fit_blocks_min_size_over_budget():
    entry = 76000.0
    fit = fit_swap_size(
        base_qty=13.3333 / entry,
        entry=entry,
        stop=entry * 0.97,
        risk_budget=0.40,
        ct_val=0.01,
        min_sz=1,
        lot_sz=1,
    )
    assert not fit.allowed
    assert fit.reason == "min size exceeds risk budget"


def test_evaluate_blocks_without_pullback_signal():
    signal = SignalReport("BTC", "none", 100.0, 97.0, 106.0, 0.4, ["no_pullback"])
    gate = RiskGate(RiskConfig(state_path="/tmp/dm_okx_desk_gate.json"))
    gate._state = {
        "day": None,
        "day_pnl": 0.0,
        "killed": False,
        "consecutive_losses": 0,
        "consecutive_wins": 0,
        "open_risk": 0.0,
    }
    tilt = TiltGuard(TiltConfig(state_path="/tmp/dm_okx_desk_tilt.json"))
    decision = evaluate_symbol(
        pair="BTC/USDT:USDT",
        signal=signal,
        equity=80,
        gate=gate,
        tilt=tilt,
        ct_val=0.01,
        min_sz=0.01,
        lot_sz=0.01,
        open_risk=0.0,
        slots_left=3,
        select=True,
    )
    assert decision.action == "block"
    assert decision.selected is False
    assert decision.plan is None


def test_eighty_dollar_book_selects_min_btc_contract():
    signal = SignalReport("BTC", "long", 76000.0, 70000.0, 90000.0, 0.8, ["pullback_ema_or_vwap"])
    gate = RiskGate(RiskConfig(max_risk=0.005, min_rr=2.0, state_path="/tmp/dm_okx_desk_btc.json"))
    gate._state = {
        "day": "2099-01-01",
        "day_pnl": 0.0,
        "killed": False,
        "consecutive_losses": 0,
        "consecutive_wins": 0,
        "open_risk": 0.0,
    }
    tilt = TiltGuard(TiltConfig(state_path="/tmp/dm_okx_desk_btc_tilt.json"))
    decision = evaluate_symbol(
        pair="BTC/USDT:USDT",
        signal=signal,
        equity=80,
        gate=gate,
        tilt=tilt,
        ct_val=0.01,
        min_sz=0.01,
        lot_sz=0.01,
        open_risk=0.0,
        slots_left=1,
        select=True,
    )
    assert decision.selected
    assert decision.fit is not None
    assert decision.fit.contracts == 0.01
    assert decision.fit.risk_amount <= 0.40
    assert decision.plan.stop == 76000.0 * 0.97


def test_cardinal_plan_uses_three_percent_stop():
    signal = SignalReport("BTC", "long", 100.0, 90.0, 120.0, 0.8, ["pullback_ema_or_vwap"])
    plan = cardinal_plan(signal, "BTC/USDT:USDT", min_rr=2.0)
    assert plan is not None
    assert plan.stop == 97.0
    assert plan.target > 106.0
