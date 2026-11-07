#!/usr/bin/env python3
"""DEATH MODE v2 — Trading Bot Controller

사용법:
  python bot.py start [--exchange hl|lighter|all]
  python bot.py stop [--exchange hl|lighter|all]
  python bot.py status
  python bot.py health
  python bot.py risk
  python bot.py lighter-audit
  python bot.py verify-data
  python bot.py logs [exchange]
  python bot.py data-download [--days 30]
  python bot.py backtest [--strategy DeathModeV2_Cardinal]
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJ = Path(os.environ.get("PROJ_DIR", Path(__file__).parent))
PID_DIR = PROJ / ".pids"
LOG_DIR = PROJ / "user_data" / "logs"
STRATEGY_DIR = PROJ / "user_data" / "strategies"
CONFIG_DIR = PROJ / "config"

os.environ.setdefault("PROJ_DIR", str(PROJ))
# Ensure core.risk is importable for Cardinal strategy / child processes
_pp = str(PROJ)
if _pp not in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    os.environ["PYTHONPATH"] = _pp + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")

os.makedirs(PID_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# 자율운영 기본 전략: Cardinal (Risk Gate + Tilt + 비대칭 청산)
# 오버라이드: --strategy DeathModeV2_NFI | DeathModeV2_Base | ...
DEFAULT_STRATEGY = os.environ.get("DM_STRATEGY", "DeathModeV2_Cardinal")

EXCHANGES = {
    "lighter": {
        # dry-run paper default (Cardinal). Override via DM_LIGHTER_CONFIG.
        "config": os.environ.get("DM_LIGHTER_CONFIG", "lighter_paper_cardinal.json"),
        "port": 8091,
        "strategy": DEFAULT_STRATEGY,
    },
    "hyperliquid": {
        "config": os.environ.get("DM_HL_CONFIG", "hl_paper_cardinal.json"),
        "port": 8092,
        "strategy": DEFAULT_STRATEGY,
    },
    "bitget": {
        "config": "bitget_futures.json",
        "port": 8093,
        "strategy": DEFAULT_STRATEGY,
    },
    "bybit": {
        "config": "bybit_futures.json",
        "port": 8094,
        "strategy": DEFAULT_STRATEGY,
    },
    "okx": {
        "config": "okx_futures.json",
        "port": 8095,
        "strategy": DEFAULT_STRATEGY,
    },
}


def find_freqtrade() -> str:
    env = os.environ.get("FREQTRADE_BIN")
    if env:
        return env
    import shutil
    return shutil.which("freqtrade") or "freqtrade"


def is_running(name: str) -> bool:
    pidfile = PID_DIR / f"{name}.pid"
    if pidfile.exists():
        try:
            pid = int(pidfile.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, OSError):
            pidfile.unlink(missing_ok=True)
    return False


def start_exchange(name: str):
    if name not in EXCHANGES:
        print(f"❌ 알 수 없는 거래소: {name}")
        return False

    if is_running(name):
        print(f"⚠️  {name} 이미 실행 중")
        return True

    # Lighter: ensure ccxt.has.fetchOrder=True so Freqtrade validate_exchange passes
    if name == "lighter":
        patch = PROJ / "scripts" / "ensure_lighter_ccxt_patch.py"
        if patch.exists():
            subprocess.run([sys.executable, str(patch)], cwd=str(PROJ), check=False)

    ex = EXCHANGES[name]
    config_path = CONFIG_DIR / ex["config"]
    if not config_path.exists():
        print(f"❌ {name}: config 없음 ({config_path})")
        tmpl = CONFIG_DIR / (ex["config"] + ".template")
        if tmpl.exists():
            import shutil
            shutil.copy(tmpl, config_path)
            print(f"   템플릿에서 생성: {config_path}")
        else:
            return False

    ft = find_freqtrade()
    logfile = LOG_DIR / f"pm_{name}.log"
    pidfile = PID_DIR / f"{name}.pid"

    cmd = [
        ft, "trade",
        "--config", str(config_path),
        "--strategy", ex["strategy"],
        "--strategy-path", str(STRATEGY_DIR),
        "--userdir", str(PROJ / "user_data"),
    ]

    print(f"🚀 {name} 시작 (port={ex['port']}, strategy={ex['strategy']})...")
    with open(logfile, "a") as lf:
        proc = subprocess.Popen(
            cmd,
            stdout=lf,
            stderr=subprocess.STDOUT,
            cwd=str(PROJ),
        )
    pidfile.write_text(str(proc.pid))
    time.sleep(3)

    if is_running(name):
        print(f"✅ {name} 실행 중 (PID={proc.pid})")
        return True
    else:
        print(f"❌ {name} 시작 실패 — 로그 확인: {logfile}")
        try:
            with open(logfile) as f:
                for line in list(f)[-5:]:
                    print(f"   {line.rstrip()}")
        except Exception:
            pass
        return False


def stop_exchange(name: str):
    pidfile = PID_DIR / f"{name}.pid"
    if not pidfile.exists():
        print(f"ℹ️  {name} 실행 중 아님")
        return True

    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        for _ in range(15):
            time.sleep(1)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
        print(f"🛑 {name} 종료됨 (PID={pid})")
    except Exception as e:
        print(f"⚠️  {name} 종료 중 오류: {e}")
    pidfile.unlink(missing_ok=True)
    return True


def status_all():
    print(f"💀 DEATH MODE v2 — 상태")
    print(f"   Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   Freqtrade: {find_freqtrade()}")
    print()
    print(f"  {'거래소':<15} {'상태':<10} {'PID':<7} {'포트':<6} {'전략':<22}")
    print(f"  {'─'*15} {'─'*10} {'─'*7} {'─'*6} {'─'*22}")
    for name, ex in EXCHANGES.items():
        running = is_running(name)
        pidfile = PID_DIR / f"{name}.pid"
        pid = pidfile.read_text().strip() if pidfile.exists() else "---"
        status = "✅ RUNNING" if running else "⏹️  STOPPED"
        pid_str = pid if running else "---"
        print(f"  {name:<15} {status:<10} {pid_str:<7} {ex['port']:<6} {ex['strategy']:<22}")
    print()


def show_logs(name: str):
    logfile = LOG_DIR / f"pm_{name}.log"
    if not logfile.exists():
        print(f"ℹ️  {name}: 로그 없음")
        return
    with open(logfile) as f:
        content = f.read()
    lines = content.strip().split("\n")
    print(f"=== {name} 로그 (최근 30줄) ===")
    for line in lines[-30:]:
        print(f"  {line}")


def run_health():
    """health_monitor.py 실행"""
    hm = PROJ / "scripts" / "health_monitor.py"
    if hm.exists():
        subprocess.run([sys.executable, str(hm), "--check-once"], cwd=str(PROJ))
    else:
        print("⚠️  health_monitor.py 없음")


def run_risk():
    """Cardinal risk / tilt status"""
    rs = PROJ / "scripts" / "risk_status.py"
    if rs.exists():
        subprocess.run([sys.executable, str(rs)], cwd=str(PROJ))
    else:
        print("⚠️  risk_status.py 없음")


def run_lighter_audit():
    """Live Lighter API compatibility audit"""
    script = PROJ / "scripts" / "lighter_api_audit.py"
    if script.exists():
        subprocess.run([sys.executable, str(script)], cwd=str(PROJ))
    else:
        print("⚠️  lighter_api_audit.py 없음")


def run_verify_data():
    """Live market-data → signal → RiskGate verification"""
    script = PROJ / "scripts" / "verify_lighter_trading_data.py"
    if script.exists():
        raise SystemExit(subprocess.run([sys.executable, str(script)], cwd=str(PROJ)).returncode)
    print("⚠️  verify_lighter_trading_data.py 없음")


def main():
    parser = argparse.ArgumentParser(description="DEATH MODE v2 Bot Controller")
    parser.add_argument(
        "action",
        choices=[
            "start", "stop", "restart", "status", "health", "risk",
            "lighter-audit", "verify-data", "logs", "data-download",
        ],
    )
    parser.add_argument("--exchange", "-e", nargs="+", default=None,
                        help="거래소 이름 또는 'all' (기본: hyperliquid,lighter)")
    parser.add_argument("--strategy", "-s", default=None, help="전략명 override")
    parser.add_argument("--days", type=int, default=30, help="데이터 다운로드 기간")

    args = parser.parse_args()

    if args.action == "status":
        status_all()
        return

    if args.action == "health":
        run_health()
        return

    if args.action == "risk":
        run_risk()
        return

    if args.action == "lighter-audit":
        run_lighter_audit()
        return

    if args.action == "verify-data":
        run_verify_data()
        return

    if args.action == "logs":
        if args.exchange:
            for ex in args.exchange:
                show_logs(ex)
        else:
            show_logs("hyperliquid")
        return

    # 거래소 목록 결정
    if args.exchange:
        targets = []
        for ex in args.exchange:
            if ex == "all":
                targets = list(EXCHANGES.keys())
                break
            elif ex in EXCHANGES:
                targets.append(ex)
            else:
                print(f"⚠️  알 수 없는 거래소: {ex}")
    else:
        targets = ["hyperliquid", "lighter"]

    if args.strategy:
        for t in targets:
            if t in EXCHANGES:
                EXCHANGES[t]["strategy"] = args.strategy

    if args.action in ("start", "restart"):
        for t in targets:
            if args.action == "restart":
                stop_exchange(t)
                time.sleep(1)
            start_exchange(t)
        status_all()

    elif args.action == "stop":
        for t in targets:
            stop_exchange(t)
        status_all()


if __name__ == "__main__":
    main()
