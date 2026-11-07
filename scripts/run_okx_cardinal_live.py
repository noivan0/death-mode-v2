#!/usr/bin/env python3
"""Start DeathModeV2_Cardinal live on OKX.

Credentials are read from .env and written only to the gitignored runtime
config. This script does not delete .env and does not call withdraw or transfer.

The $1,000,000 goal stops new entries. It does not change position size.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.env_loader import load_env

CONFIG_PATH = ROOT / "config" / "okx_live_cardinal.json"
PAIRS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "ADA/USDT:USDT",
    "AVAX/USDT:USDT",
    "LINK/USDT:USDT",
]


def build_config() -> dict:
    load_env(ROOT)
    key = os.environ.get("OKX_API_KEY", "")
    secret = os.environ.get("OKX_API_SECRET", "")
    password = os.environ.get("OKX_API_PASSPHRASE", "")
    if not key or not secret or not password:
        raise SystemExit("OKX credentials missing from .env")
    return {
        "max_open_trades": 3,
        "stake_currency": "USDT",
        "stake_amount": "unlimited",
        "tradable_balance_ratio": 0.99,
        "fiat_display_currency": "USD",
        "dry_run": False,
        "cancel_open_orders_on_exit": True,
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "liquidation_buffer": 0.05,
        "candle_type_def": "futures",
        "entry_pricing": {"price_side": "same", "use_order_book": True, "order_book_top": 1},
        "exit_pricing": {"price_side": "same", "use_order_book": True, "order_book_top": 1},
        "order_types": {
            "entry": "limit",
            "exit": "limit",
            "stoploss": "market",
            "stoploss_on_exchange": True,
            "stoploss_on_exchange_interval": 60,
        },
        "order_time_in_force": {"entry": "GTC", "exit": "GTC"},
        "unfilledtimeout": {"entry": 10, "exit": 10, "unit": "minutes"},
        "exchange": {
            "name": "okx",
            "key": key,
            "secret": secret,
            "password": password,
            "ccxt_config": {
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
                "urls": {"api": {"ws": "wss://ws.okx.com:443/ws/v5"}},
            },
            "ccxt_async_config": {
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
                "urls": {"api": {"ws": "wss://ws.okx.com:443/ws/v5"}},
            },
            "pair_whitelist": PAIRS,
            "pair_blacklist": [],
        },
        "pairlists": [{"method": "StaticPairList"}],
        "telegram": {"enabled": False, "token": "", "chat_id": ""},
        "api_server": {
            "enabled": True,
            "listen_ip_address": "127.0.0.1",
            "listen_port": 8095,
            "verbosity": "error",
            "jwt_secret_key": "death-mode-okx-live-local-jwt-key-32",
            "username": "death",
            "password": "deathpaper",
            "ws_token": "death-okx-live-ws",
        },
        "bot_name": "death-okx-cardinal-live",
        "initial_state": "running",
        "force_entry_enable": False,
        "internals": {"process_throttle_secs": 5},
        "db_url": "sqlite:///user_data/tradesv3_okx_cardinal.sqlite",
        "logfile": "user_data/logs/okx_cardinal_live.log",
        "user_data_dir": "user_data",
        "strategy": "DeathModeV2_Cardinal",
        "strategy_path": "user_data/strategies",
    }


def main() -> int:
    os.environ.setdefault("DM_GOAL_EQUITY", "1000000")
    os.environ.setdefault("DM_MAX_RISK", "0.005")
    os.environ.setdefault("DM_MAX_DAY_LOSS", "0.02")
    os.environ.setdefault("DM_MIN_RR", "2.0")
    config = build_config()
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    CONFIG_PATH.chmod(0o600)
    freqtrade = shutil.which("freqtrade")
    if not freqtrade:
        raise SystemExit("freqtrade not on PATH")
    os.chdir(ROOT)
    os.execv(
        freqtrade,
        [
            freqtrade,
            "trade",
            "--config",
            str(CONFIG_PATH),
            "--strategy",
            "DeathModeV2_Cardinal",
            "--strategy-path",
            str(ROOT / "user_data" / "strategies"),
            "--userdir",
            str(ROOT / "user_data"),
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
