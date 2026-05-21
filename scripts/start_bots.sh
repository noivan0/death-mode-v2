#!/bin/bash
# DEATH MODE v2 — 봇 안전 시작 스크립트
# 1. 데이터 갱신 확인
# 2. HL + Lighter 순차 시작 (30초 간격)
# 3. heartbeat 모니터링

set -e
PROJ=/root/.hermes/projects/death-mode-v2
FT=/root/.hermes/hermes-agent/venv/bin/freqtrade
LOG=$PROJ/user_data/logs

echo "=== DEATH MODE v2 시작 $(date -u) ==="

# 0. 기존 봇 정리
pkill -f freqtrade 2>/dev/null || true
sleep 3

# 1. 데이터 갭 확인
GAP=$(python3 -c "
import json, datetime, os
f = '$PROJ/user_data/data/hyperliquid/futures/BTC_USDC_USDC-1h-futures.json'
with open(f) as fp: d=json.load(fp)
now=datetime.datetime.now(tz=datetime.timezone.utc)
last=datetime.datetime.fromtimestamp(d[-1][0]/1000, tz=datetime.timezone.utc)
gap=(now-last).total_seconds()/3600
print(f'{gap:.1f}')
" 2>/dev/null)
echo "BTC/1h 데이터 갭: ${GAP}h"
if [ "$(echo "$GAP > 3" | bc -l 2>/dev/null)" = "1" ]; then
    echo "⚠️ 갭이 큽니다. freqtrade download-data로 갱신 권장"
fi

# 2. HL 페이퍼봇 시작
echo ""
echo "▶ HL 봇 시작 (포트 8095)..."
rm -f $LOG/paper_nfi.log
nohup $FT trade \
  --config $PROJ/config/hl_paper_ict.json \
  --strategy DeathModeV2_NFI \
  --strategy-path $PROJ/user_data/strategies/ \
  >> $LOG/paper_nfi.log 2>&1 &
HL_PID=$!
echo "HL PID: $HL_PID"
sleep 30

# HL heartbeat 확인
if grep -q "RUNNING" $LOG/paper_nfi.log 2>/dev/null; then
    echo "✅ HL 봇 RUNNING"
else
    echo "⚠️ HL 봇 상태 확인 필요"
    tail -5 $LOG/paper_nfi.log
fi

# 3. Lighter 봇 시작 (30초 후)
echo ""
echo "▶ Lighter 봇 시작 (포트 8092)..."
rm -f $LOG/lighter_paper_nfi.log
nohup $FT trade \
  --config $PROJ/config/lighter_futures.json \
  --strategy DeathModeV2_NFI \
  --strategy-path $PROJ/user_data/strategies/ \
  >> $LOG/lighter_paper_nfi.log 2>&1 &
LT_PID=$!
echo "Lighter PID: $LT_PID"
sleep 120

# 4. 최종 확인
echo ""
echo "=== 최종 상태 ==="
grep "heartbeat" $LOG/paper_nfi.log 2>/dev/null | tail -2
grep "heartbeat" $LOG/lighter_paper_nfi.log 2>/dev/null | tail -2

echo ""
echo "API 확인:"
curl -s http://127.0.0.1:8095/api/v1/ping 2>/dev/null && echo " ← HL OK" || echo "HL 8095 미응답"
curl -s http://127.0.0.1:8092/api/v1/ping 2>/dev/null && echo " ← Lighter OK" || echo "Lighter 8092 미응답"

echo ""
echo "=== 시작 완료 ==="
