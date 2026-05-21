# flake8: noqa: F401
# isort: off
from freqtrade.exchange.common import MAP_EXCHANGE_CHILDCLASS
from freqtrade.exchange.exchange import Exchange

# isort: on

# ── 공식 지원 거래소 (Freqtrade 내장) ─────────────────────────────
from freqtrade.exchange.binance import Binance
from freqtrade.exchange.bybit import Bybit
from freqtrade.exchange.gate import Gate
from freqtrade.exchange.htx import Htx
from freqtrade.exchange.hyperliquid import Hyperliquid
from freqtrade.exchange.kraken import Kraken
from freqtrade.exchange.okx import Okx

# ── DEATH MODE 커스텀: Lighter DEX ───────────────────────────────
# [FAIL-04] __init__.py 전체 교체 대신 Lighter만 추가 등록
from freqtrade.exchange.lighter import Lighter

MAP_EXCHANGE_CHILDCLASS.update(
    {
        "lighter": Lighter,
        # 공식 지원 거래소는 Freqtrade가 자동 등록하지만 명시적으로 보존
        "binance": Binance,
        "bybit": Bybit,
        "gate": Gate,
        "gateio": Gate,
        "htx": Htx,
        "hyperliquid": Hyperliquid,
        "kraken": Kraken,
        "okx": Okx,
    }
)

__all__ = [
    "Exchange",
    "Binance",
    "Bybit",
    "Gate",
    "Htx",
    "Hyperliquid",
    "Kraken",
    "Lighter",
    "Okx",
]
