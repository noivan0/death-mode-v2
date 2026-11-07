#!/usr/bin/env python3
"""Ensure ccxt.lighter reports fetchOrder=True for Freqtrade validate_exchange.

Freqtrade validates against ccxt.pro / ccxt.async_support.has before loading the
Death Mode Lighter subclass. Our subclass implements fetch_order via open+closed
fallback, so the capability flag must be True or download-data/trade refuse to start.
"""
from __future__ import annotations

from pathlib import Path
import sys


def _patch_file(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text()
    if "'fetchOrder': True" in text or '"fetchOrder": True' in text:
        print(f"OK: {path.name} already True")
        return False
    if "'fetchOrder': False," not in text:
        print(f"SKIP: {path} (no False flag)")
        return False
    path.write_text(
        text.replace(
            "'fetchOrder': False,",
            "'fetchOrder': True,  # death-mode: open+closed fetch_order fallback",
            1,
        )
    )
    print(f"PATCHED: {path}")
    return True


def patch() -> int:
    import ccxt
    root = Path(ccxt.__file__).parent
    changed = 0
    for rel in ("lighter.py", "async_support/lighter.py", "pro/lighter.py"):
        if _patch_file(root / rel):
            changed += 1
    return changed


if __name__ == "__main__":
    n = patch()
    sys.exit(0)
