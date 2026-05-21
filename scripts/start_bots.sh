#!/bin/bash
# DEATH MODE v2 — 봇 안전 시작 스크립트
# 어느 로컬에서도 동작하도록 PROJ_DIR 자동 감지
set -e

# ── 경로 자동 감지 ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="${PROJ_DIR:-$(dirname "$SCRIPT_DIR")}"

# Freqtrade: 환경변수 우선, 없으면 PATH에서 검색
FT="${FREQTRADE_BIN:-$(command -v freqtrade 2>/dev/null || true)}"
if [ -z "$FT" ]; then
    # venv 자동 탐색
    for candidate in \
        "$PROJ/.venv/bin/freqtrade" \
        "$PROJ/venv/bin/freqtrade" \
        "$HOME/.hermes/hermes-agent/venv/bin/freqtrade"; do
        [ -x "$candidate" ] && FT="$candidate" && break
    done
fi
if [ -z "$FT" ]; then
    echo "❌ freqtrade 실행 파일을 찾을 수 없습니다."
    echo "   FREQTRADE_BIN=/path/to/freqtrade $0 으로 직접 지정하세요."
    exit 1
fi

LOG="$PROJ/user_data/logs"
mkdir -p "$LOG"

echo "=== DEATH MODE v2 시작 $(date -u) ==="
echo "   PROJ=$PROJ"
echo "   FT=$FT"
echo ""

# 0. 기존 봇 정리
pkill -f freqtrade 2>/dev/null || true
sleep 3

# 1. 데이터 갭 확인
DATA_FILE="$PROJ/user_data/data/hyperliquid/futures/BTC_USDC_USDC-1h-futures.json"
if [ -f "$DATA_FILE" ]; then
    GAP=$(python3 - "$DATA_FILE" << 'PYEOF'
import json, datetime, sys
with open(sys.argv[1]) as fp:
    d = json.load(fp)
now  = datetime.datetime.now(tz=datetime.timezone.utc)
last = datetime.datetime.fromtimestamp(d[-1][0]/1000, tz=datetime.timezone.utc)
print(f"{(now-last).total_seconds()/3600:.1f}")
PYEOF
    )
    echo "BTC/1h 데이터 갭: ${GAP}h"
    if [ "$(python3 -c "print(1 if float('$GAP') > 3 else 0)" 2>/dev/null)" = "1" ]; then
        echo "⚠️  갭이 큽니다. freqtrade download-data로 갱신 권장"
    fi
else
    echo "⚠️  BTC 1h 데이터 없음 — scripts/download_hl_data.py 먼저 실행하세요"
fi

# 2. HL 페이퍼봇 시작
echo ""
echo "▶ HL 봇 시작 (포트 8095)..."
rm -f "$LOG/paper_nfi.log"
nohup "$FT" trade \
    --config "$PROJ/config/hl_paper_ict.json" \
    --strategy DeathModeV2_NFI \
    --strategy-path "$PROJ/user_data/strategies/" \
    >> "$LOG/paper_nfi.log" 2>&1 &
HL_PID=$!
echo "HL PID: $HL_PID"
sleep 30

if grep -q "RUNNING" "$LOG/paper_nfi.log" 2>/dev/null; then
    echo "✅ HL 봇 RUNNING"
else
    echo "⚠️  HL 봇 상태 확인 필요"
    tail -5 "$LOG/paper_nfi.log"
fi

# 3. Lighter 봇 시작
echo ""
echo "▶ Lighter 봇 시작 (포트 8092)..."
rm -f "$LOG/lighter_paper_nfi.log"
nohup "$FT" trade \
    --config "$PROJ/config/lighter_futures.json" \
    --strategy DeathModeV2_NFI \
    --strategy-path "$PROJ/user_data/strategies/" \
    >> "$LOG/lighter_paper_nfi.log" 2>&1 &
LT_PID=$!
echo "Lighter PID: $LT_PID"
sleep 120

# 4. 최종 확인
echo ""
echo "=== 최종 상태 ==="
grep "heartbeat" "$LOG/paper_nfi.log"         2>/dev/null | tail -2
grep "heartbeat" "$LOG/lighter_paper_nfi.log"  2>/dev/null | tail -2

echo ""
echo "API 확인:"
curl -s http://127.0.0.1:8095/api/v1/ping 2>/dev/null && echo " ← HL OK"      || echo "HL 8095 미응답"
curl -s http://127.0.0.1:8092/api/v1/ping 2>/dev/null && echo " ← Lighter OK" || echo "Lighter 8092 미응답"

echo ""
echo "=== 시작 완료 ==="
