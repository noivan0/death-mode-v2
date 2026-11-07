"""Load DEATH MODE secrets from .env (preferred) or secrets.env (legacy)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_LOADED: Optional[Path] = None


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key:
            out[key] = val
    return out


def project_root() -> Path:
    return Path(os.environ.get("PROJ_DIR", Path(__file__).resolve().parents[1]))


def env_candidates(root: Optional[Path] = None) -> list[Path]:
    root = root or project_root()
    return [root / ".env", root / "secrets.env"]


def find_env_file(root: Optional[Path] = None) -> Optional[Path]:
    for path in env_candidates(root):
        if path.is_file():
            return path.resolve()
    return None


def load_env(root: Optional[Path] = None, *, override: bool = False) -> Optional[Path]:
    """Load key=value pairs into os.environ. Returns the file used, if any."""
    global _LOADED
    path = find_env_file(root)
    if path is None:
        return None

    parsed = _parse_env_file(path)
    for key, val in parsed.items():
        if override or key not in os.environ or os.environ.get(key, "") == "":
            os.environ[key] = val

    # Keep exchange_patch / ccxt aliases aligned.
    # If only one side was present in the file, mirror it (even when the other
    # key already exists in the process environment from a prior load).
    secret = os.environ.get("LIGHTER_API_SECRET", "")
    priv = os.environ.get("LIGHTER_PRIVATE_KEY", "")
    if "LIGHTER_PRIVATE_KEY" not in parsed and secret:
        os.environ["LIGHTER_PRIVATE_KEY"] = secret
    elif "LIGHTER_API_SECRET" not in parsed and priv:
        os.environ["LIGHTER_API_SECRET"] = priv
    elif not priv and secret:
        os.environ["LIGHTER_PRIVATE_KEY"] = secret
    elif not secret and priv:
        os.environ["LIGHTER_API_SECRET"] = priv

    os.environ["DM_ENV_FILE"] = str(path)
    _LOADED = path
    return path


def env_status() -> dict:
    """Masked status for ops — never returns secret values."""
    path = find_env_file()
    load_env()
    keys = [
        "LIGHTER_ACCOUNT_INDEX",
        "LIGHTER_API_KEY_INDEX",
        "LIGHTER_CHAIN_ID",
        "LIGHTER_API_KEY",
        "LIGHTER_API_SECRET",
        "LIGHTER_PRIVATE_KEY",
        "LIGHTER_SKIP_SSL_VERIFY",
        "OKX_API_KEY",
        "OKX_API_SECRET",
        "OKX_API_PASSPHRASE",
    ]
    status = {}
    secret_keys = {
        "LIGHTER_API_KEY",
        "LIGHTER_API_SECRET",
        "LIGHTER_PRIVATE_KEY",
        "HYPERLIQUID_PRIVATE_KEY",
        "OKX_API_KEY",
        "OKX_API_SECRET",
        "OKX_API_PASSPHRASE",
    }
    for k in keys:
        v = os.environ.get(k, "")
        if not v:
            status[k] = "empty"
        elif k in secret_keys:
            status[k] = f"set(len={len(v)})"
        else:
            status[k] = v
    return {
        "env_file": str(path) if path else None,
        "keys": status,
    }
