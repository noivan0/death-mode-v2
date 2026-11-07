"""Lighter API compatibility unit tests (no live network required)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.exchange.lighter_compat import (
    CHAIN_ID_MAINNET,
    CHAIN_ID_TESTNET,
    assert_mainnet_chain_id,
    parse_market,
    resolve_network_name,
    should_reconnect_on_ws_message,
)


def test_chain_ids():
    assert CHAIN_ID_MAINNET == 304
    assert CHAIN_ID_TESTNET == 300
    assert resolve_network_name(304) == "mainnet"
    assert resolve_network_name(300) == "testnet"
    assert_mainnet_chain_id(304)
    assert_mainnet_chain_id(300)


def test_assert_rejects_bogus_chain():
    try:
        assert_mainnet_chain_id(1)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Unsupported" in str(e)


def test_parse_market_uses_market_type_not_id():
    # New-style perp with market_id >= 4095 must still parse as perp
    m = parse_market({
        "symbol": "STONK",
        "market_id": 4095,
        "market_type": "perp",
        "mark_price": "1.0",
        "index_price": "1.0",
        "open_interest": 100,
        "open_interest_usd": "1000",
        "open_interest_limit": 200,
        "is_frozen": False,
        "min_base_amount": "1",
        "maker_fee": "0",
        "taker_fee": "0",
    })
    assert m.is_perp
    assert m.market_id == 4095
    assert abs((m.oi_utilization or 0) - 0.5) < 1e-9


def test_ws_shutdown_event():
    close, wait = should_reconnect_on_ws_message({"type": "shutdown", "close_in_ms": 7350})
    assert close is True
    assert abs(wait - 7.35) < 1e-6
    close2, wait2 = should_reconnect_on_ws_message({"type": "update/candle"})
    assert close2 is False
    assert wait2 == 0.0
