import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.env_loader import env_status, find_env_file, load_env


def test_find_env_prefers_dotenv(tmp_path: Path, monkeypatch):
    dotenv = tmp_path / ".env"
    legacy = tmp_path / "secrets.env"
    dotenv.write_text("LIGHTER_ACCOUNT_INDEX=1\nLIGHTER_API_SECRET=abc\n")
    legacy.write_text("LIGHTER_ACCOUNT_INDEX=2\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PROJ_DIR", str(tmp_path))
    path = find_env_file(tmp_path)
    assert path == dotenv.resolve()
    load_env(tmp_path, override=True)
    import os

    assert os.environ["LIGHTER_ACCOUNT_INDEX"] == "1"
    assert os.environ["LIGHTER_PRIVATE_KEY"] == "abc"  # alias sync


def test_env_status_masks_secrets():
    st = env_status()
    assert "keys" in st
    secret_keys = {
        "LIGHTER_API_KEY",
        "LIGHTER_API_SECRET",
        "LIGHTER_PRIVATE_KEY",
        "HYPERLIQUID_PRIVATE_KEY",
    }
    for k, v in st["keys"].items():
        if k in secret_keys:
            assert v == "empty" or v.startswith("set(len=")
        elif k == "LIGHTER_API_KEY_INDEX":
            assert v in ("empty", "0") or v.isdigit()
