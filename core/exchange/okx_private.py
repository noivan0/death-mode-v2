"""Authenticated OKX account read. Credentials stay in .env.

This module does not place orders and does not call transfer or withdraw.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
import os

import ccxt

from ..env_loader import load_env

OKX_ENV_KEYS = ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE")


def credentials_from_env(root: Optional[Path] = None) -> Optional[dict[str, str]]:
    """Load OKX credentials. Returns None when any of the three is missing."""
    load_env(root)
    values = {name: os.environ.get(name, "") for name in OKX_ENV_KEYS}
    if any(not values[name] for name in OKX_ENV_KEYS):
        return None
    return values


def redact(text: str, secrets: Optional[dict[str, str]] = None) -> str:
    cleaned = text
    for value in (secrets or {}).values():
        if value:
            cleaned = cleaned.replace(value, "[redacted]")
    return cleaned


def make_client(credentials: dict[str, str]) -> ccxt.okx:
    return ccxt.okx(
        {
            "apiKey": credentials["OKX_API_KEY"],
            "secret": credentials["OKX_API_SECRET"],
            "password": credentials["OKX_API_PASSPHRASE"],
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
    )


def _nonzero_details(rows: list[dict]) -> list[dict]:
    kept = []
    for row in rows:
        amounts = []
        for key in ("availBal", "bal", "eq", "cashBal", "availEq"):
            raw = row.get(key)
            if raw not in (None, ""):
                amounts.append(float(raw))
        if any(abs(amount) > 0 for amount in amounts):
            kept.append(
                {
                    "ccy": row.get("ccy"),
                    "avail": row.get("availBal", row.get("availEq")),
                    "bal": row.get("bal", row.get("cashBal")),
                    "eq": row.get("eq"),
                }
            )
    return kept


def read_account(exchange: Any) -> dict:
    """Balance, funding dust, and open positions. No order payload."""
    trading = exchange.privateGetAccountBalance()
    funding = exchange.privateGetAssetBalances()
    config = exchange.privateGetAccountConfig()
    positions = exchange.fetch_positions()
    account = (trading.get("data") or [{}])[0]
    details = []
    for row in trading.get("data") or []:
        details.extend(row.get("details") or [])
    cfg = (config.get("data") or [{}])[0]
    open_positions = []
    for pos in positions:
        contracts = float(pos.get("contracts") or 0)
        if contracts:
            open_positions.append(
                {
                    "symbol": pos.get("symbol"),
                    "side": pos.get("side"),
                    "contracts": contracts,
                    "notional": pos.get("notional"),
                }
            )
    usdt = next((row for row in details if row.get("ccy") == "USDT"), {})
    return {
        "authenticated": True,
        "total_eq": account.get("totalEq"),
        "usdt_free": usdt.get("availBal") or "0",
        "usdt_total": usdt.get("eq") or usdt.get("cashBal") or "0",
        "pos_mode": cfg.get("posMode"),
        "acct_lv": cfg.get("acctLv"),
        "trading_nonzero": _nonzero_details(details),
        "funding_nonzero": _nonzero_details(funding.get("data") or []),
        "open_positions": open_positions,
    }


def account_from_env(root: Optional[Path] = None) -> dict:
    credentials = credentials_from_env(root)
    if credentials is None:
        return {"authenticated": False, "reason": "missing okx credentials in .env"}
    try:
        return read_account(make_client(credentials))
    except Exception as exc:
        return {
            "authenticated": False,
            "reason": redact(f"{type(exc).__name__}: {exc}", credentials),
        }
