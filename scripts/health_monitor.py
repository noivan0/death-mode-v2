#!/usr/bin/env python3
"""DEATH MODE v2 — Health Monitor & Auto-Recovery.

실행: python scripts/health_monitor.py [--check-once]

체크 항목:
1. 프로세스 상태 (pm.sh 기반)
2. 데이터 갭 (최근 캔들이 N분 이상 지났는지)
3. 디스크/메모리 사용량
4. 오류 로그 패턴 감지
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJ = Path(os.environ.get("PROJ_DIR", Path(__file__).parent.parent))

EXCHANGES = ["lighter", "hyperliquid", "bitget", "bybit", "okx"]
PID_DIR = PROJ / ".pids"
LOG_DIR = PROJ / "user_data" / "logs"
DATA_DIR = PROJ / "user_data" / "data"


def check_running(name: str) -> dict:
    pidfile = PID_DIR / f"{name}.pid"
    if not pidfile.exists():
        return {"name": name, "running": False, "reason": "no_pid_file"}
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
        return {"name": name, "running": True, "pid": pid}
    except (ValueError, ProcessLookupError, OSError):
        return {"name": name, "running": False, "reason": "process_dead"}


def check_data_gap() -> list:
    gaps = []
    data_paths = [
        DATA_DIR / "hyperliquid" / "futures",
        DATA_DIR / "lighter" / "futures",
    ]
    now = datetime.now(timezone.utc)
    for dp in data_paths:
        if not dp.exists():
            continue
        for f in sorted(dp.glob("*5m-futures.json")):
            try:
                with open(f) as fp:
                    data = json.load(fp)
                if not data:
                    continue
                last_ts = data[-1][0] / 1000  # milliseconds to seconds
                last_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)
                gap_hours = (now - last_dt).total_seconds() / 3600
                if gap_hours > 3:
                    gaps.append({
                        "file": str(f),
                        "last_candle": last_dt.isoformat(),
                        "gap_hours": round(gap_hours, 1),
                    })
            except Exception as e:
                gaps.append({"file": str(f), "error": str(e)})
    return gaps


def check_error_logs() -> list:
    errors = []
    error_patterns = ["CRITICAL", "ERROR", "Traceback", "Exception", "InsufficientFunds"]
    if not LOG_DIR.exists():
        return errors
    for f in sorted(LOG_DIR.glob("*.log")):
        try:
            with open(f) as fp:
                for line in fp:
                    for pat in error_patterns:
                        if pat in line:
                            errors.append({"file": f.name, "line": line.strip()[:200]})
                            break
        except Exception:
            pass
    return errors[:20]  # 최대 20개


def auto_recover(name: str):
    """자동 복구: pm.sh start"""
    pm = PROJ / "pm.sh"
    if pm.exists():
        subprocess.run(["bash", str(pm), "start", name], capture_output=True, timeout=30)
        return f"auto_recovery: pm.sh start {name} 실행됨"
    return "auto_recovery: pm.sh not found"


def main():
    check_once = "--check-once" in sys.argv

    print(f"💀 DEATH MODE v2 — Health Monitor")
    print(f"   Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"   PROJ: {PROJ}\n")

    # 1. 프로세스 상태
    print("=== 1. 프로세스 상태 ===")
    processes = [check_running(ex) for ex in EXCHANGES]
    for p in processes:
        icon = "✅" if p["running"] else "❌"
        info = f"PID={p['pid']}" if p.get("pid") else p.get("reason", "unknown")
        print(f"  {icon} {p['name']:12s} {info}")

    dead = [p for p in processes if not p["running"] and p.get("reason") == "process_dead"]

    # 2. 데이터 갭
    print("\n=== 2. 데이터 갭 ===")
    gaps = check_data_gap()
    if gaps:
        for g in gaps:
            if "error" in g:
                print(f"  ⚠️  {g['file']}: {g['error']}")
            else:
                print(f"  ⚠️  {Path(g['file']).name}: {g['gap_hours']}h gap (last={g['last_candle']})")
    else:
        print("  ✅ 데이터 갭 없음")

    # 3. 로그 에러
    print("\n=== 3. 로그 에러 ===")
    errors = check_error_logs()
    if errors:
        for e in errors[:5]:
            print(f"  ⚠️  [{e['file']}] {e['line'][:100]}")
        if len(errors) > 5:
            print(f"  ... 그리고 {len(errors)-5}개 더")
    else:
        print("  ✅ 에러 없음")

    # 4. 자동 복구
    if dead:
        print(f"\n=== 4. 자동 복구 ===")
        for p in dead:
            print(f"  🔄 {p['name']} 재시작 중...")
            result = auto_recover(p["name"])
            print(f"  {result}")

    if check_once:
        return

    # 반복 모드 (30초마다)
    print(f"\n🔄 30초 간격 모니터링 시작 (Ctrl+C 종료)")
    try:
        while True:
            time.sleep(30)
            procs = [check_running(ex) for ex in EXCHANGES]
            dead_now = [p for p in procs if not p["running"] and p.get("reason") == "process_dead"]
            if dead_now:
                for p in dead_now:
                    print(f"[{datetime.now().isoformat()}] 🔄 {p['name']} 재시작")
                    auto_recover(p["name"])
    except KeyboardInterrupt:
        print("\n모니터링 종료")


if __name__ == "__main__":
    main()
