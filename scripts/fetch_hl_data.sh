#!/bin/bash
# HL 10페어 × 5TF 로컬 데이터 다운로드
# rate limit: 500ms 간격, 1 pair at a time
set -e

FT=/root/.hermes/hermes-agent/venv/bin/freqtrade
DIR=/root/.hermes/projects/death-mode-v2
CFG=$DIR/config/hl_paper_ict.json

PAIRS=(
  "BTC/USDC:USDC"
  "ETH/USDC:USDC"
  "SOL/USDC:USDC"
  "XRP/USDC:USDC"
  "SUI/USDC:USDC"
  "HYPE/USDC:USDC"
  "ZEC/USDC:USDC"
  "LIT/USDC:USDC"
  "TON/USDC:USDC"
  "VVV/USDC:USDC"
)

TFS=(5m 15m 1h 4h 1d)

# 최근 800캔들이면 충분 (startup_candle_count=800 대응)
# 5m×800 = 2.7일, 1d×800 = 2.2년
DAYS_MAP=( "5m:3" "15m:9" "1h:35" "4h:140" "1d:1000" )

declare -A TF_DAYS
TF_DAYS[5m]=3
TF_DAYS[15m]=9
TF_DAYS[1h]=35
TF_DAYS[4h]=140
TF_DAYS[1d]=1000

echo "=== HL 데이터 다운로드 시작 $(date) ==="

for PAIR in "${PAIRS[@]}"; do
  for TF in "${TFS[@]}"; do
    DAYS=${TF_DAYS[$TF]}
    echo "→ $PAIR $TF (${DAYS}일)"
    
    $FT download-data \
      --config $CFG \
      --pairs "$PAIR" \
      --timeframes $TF \
      --days $DAYS \
      --trading-mode futures \
      --datadir $DIR/user_data/data/hyperliquid \
      2>&1 | tail -3
    
    sleep 0.5  # rate limit 방지
  done
  echo "✅ $PAIR 완료"
  sleep 1
done

echo ""
echo "=== 다운로드 완료 $(date) ==="
echo "데이터 확인:"
for PAIR in "${PAIRS[@]}"; do
  SYM=${PAIR%%/*}
  FILE="$DIR/user_data/data/hyperliquid/futures/${SYM}_USDC_USDC-5m-futures.json"
  if [ -f "$FILE" ]; then
    SIZE=$(wc -c < "$FILE")
    echo "  $SYM/5m: ${SIZE} bytes"
  fi
done
