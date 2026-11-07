"""OKX private account read. No network."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.exchange.okx_private import account_from_env, read_account, redact


class _FakeOkx:
    def privateGetAccountBalance(self):
        return {
            "data": [
                {
                    "totalEq": "80",
                    "details": [
                        {"ccy": "USDT", "availBal": "80", "eq": "80", "cashBal": "80"},
                        {"ccy": "ETH", "availBal": "0", "eq": "0", "cashBal": "0"},
                    ],
                }
            ]
        }

    def privateGetAssetBalances(self):
        return {"data": [{"ccy": "SOL", "availBal": "0.01", "bal": "0.01"}]}

    def privateGetAccountConfig(self):
        return {"data": [{"posMode": "long_short_mode", "acctLv": "2"}]}

    def fetch_positions(self):
        return [{"symbol": "BTC/USDT:USDT", "contracts": 0, "side": "long", "notional": 0}]


def test_read_account_keeps_nonzero_balances():
    account = read_account(_FakeOkx())
    assert account["authenticated"] is True
    assert account["total_eq"] == "80"
    assert account["usdt_free"] == "80"
    assert account["trading_nonzero"] == [
        {"ccy": "USDT", "avail": "80", "bal": "80", "eq": "80"}
    ]
    assert account["funding_nonzero"][0]["ccy"] == "SOL"
    assert account["open_positions"] == []
    assert account["pos_mode"] == "long_short_mode"


def test_redact_strips_secret_material():
    text = redact("bad passphrase desk-secret", {"OKX_API_PASSPHRASE": "desk-secret"})
    assert "desk-secret" not in text
    assert "[redacted]" in text


def test_missing_env_file_does_not_authenticate(tmp_path, monkeypatch):
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_API_SECRET", raising=False)
    monkeypatch.delenv("OKX_API_PASSPHRASE", raising=False)
    (tmp_path / ".env").write_text("OKX_API_KEY=\nOKX_API_SECRET=\nOKX_API_PASSPHRASE=\n")
    account = account_from_env(tmp_path)
    assert account["authenticated"] is False
