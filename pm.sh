#!/bin/bash
##############################################################
# DEATH MODE v2 — Process Manager
# 헤르 컨테이너에서 직접 Freqtrade 멀티 인스턴스 실행
#
# 사용법:
#   ./pm.sh start lighter        # 단일 거래소
#   ./pm.sh start all            # 전체
#   ./pm.sh start cex            # CEX만 (bitget+bybit+okx)
#   ./pm.sh start dex            # DEX만 (lighter+hyperliquid)
#   ./pm.sh stop all
#   ./pm.sh status
#   ./pm.sh logs lighter
#   ./pm.sh restart lighter
##############################################################

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FREQTRADE="/root/.hermes/hermes-agent/venv/bin/freqtrade"
CONFIG_DIR="$PROJECT_DIR/config"
LOG_DIR="$PROJECT_DIR/user_data/logs"
PID_DIR="$PROJECT_DIR/.pids"
STRATEGY_DIR="$PROJECT_DIR/user_data/strategies"

mkdir -p "$LOG_DIR" "$PID_DIR"

# ── 거래소 정의 ────────────────────────────────────────────────
declare -A EXCHANGE_CONFIG=(
    [lighter]="lighter_futures.json"
    [hyperliquid]="hyperliquid_futures.json"
    [bitget]="bitget_futures.json"
    [bybit]="bybit_futures.json"
    [okx]="okx_futures.json"
)

declare -A EXCHANGE_PORT=(
    [lighter]=8091
    [hyperliquid]=8092
    [bitget]=8093
    [bybit]=8094
    [okx]=8095
)

CEX_EXCHANGES=(bitget bybit okx)
DEX_EXCHANGES=(lighter hyperliquid)
ALL_EXCHANGES=(lighter hyperliquid bitget bybit okx)

# ── 프록시 설정 (Bybit/OKX HMG 우회) ─────────────────────────
# SOCKS5_PROXY 환경변수 세팅 시 자동 적용
# export SOCKS5_PROXY="socks5h://127.0.0.1:1080"
if [[ -n "${SOCKS5_PROXY:-}" ]]; then
    export HTTPS_PROXY="$SOCKS5_PROXY"
    export HTTP_PROXY="$SOCKS5_PROXY"
    echo "[pm] 프록시 활성화: $SOCKS5_PROXY"
fi

##############################################################
# FUNCTIONS
##############################################################

get_exchanges() {
    local target="$1"
    case "$target" in
        all)   echo "${ALL_EXCHANGES[@]}" ;;
        cex)   echo "${CEX_EXCHANGES[@]}" ;;
        dex)   echo "${DEX_EXCHANGES[@]}" ;;
        *)     echo "$target" ;;
    esac
}

is_running() {
    local exchange="$1"
    local pidfile="$PID_DIR/${exchange}.pid"
    [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

start_exchange() {
    local exchange="$1"
    local config="${EXCHANGE_CONFIG[$exchange]}"
    local config_path="$CONFIG_DIR/$config"
    local pidfile="$PID_DIR/${exchange}.pid"
    local logfile="$LOG_DIR/pm_${exchange}.log"

    if ! [[ -f "$config_path" ]]; then
        echo "[pm] ❌ config 없음: $config_path"
        return 1
    fi

    if is_running "$exchange"; then
        echo "[pm] ⚠️  $exchange 이미 실행 중 (PID=$(cat "$pidfile"))"
        return 0
    fi

    echo "[pm] 🚀 $exchange 시작..."
    nohup "$FREQTRADE" trade \
        --config "$config_path" \
        --strategy NostalgiaForInfinityX7 \
        --strategy-path "$STRATEGY_DIR" \
        --userdir "$PROJECT_DIR/user_data" \
        >> "$logfile" 2>&1 &

    local pid=$!
    echo "$pid" > "$pidfile"
    sleep 2

    if kill -0 "$pid" 2>/dev/null; then
        echo "[pm] ✅ $exchange 시작됨 (PID=$pid, API=:${EXCHANGE_PORT[$exchange]})"
    else
        echo "[pm] ❌ $exchange 시작 실패 — 로그 확인: $logfile"
        rm -f "$pidfile"
        tail -20 "$logfile"
        return 1
    fi
}

stop_exchange() {
    local exchange="$1"
    local pidfile="$PID_DIR/${exchange}.pid"

    if ! is_running "$exchange"; then
        echo "[pm] ℹ️  $exchange 실행 중 아님"
        rm -f "$pidfile"
        return 0
    fi

    local pid
    pid=$(cat "$pidfile")
    echo "[pm] 🛑 $exchange 종료 (PID=$pid)..."
    kill -SIGTERM "$pid" 2>/dev/null || true

    local i=0
    while kill -0 "$pid" 2>/dev/null && [[ $i -lt 15 ]]; do
        sleep 1; ((i++))
    done

    if kill -0 "$pid" 2>/dev/null; then
        kill -SIGKILL "$pid" 2>/dev/null || true
        echo "[pm] SIGKILL 전송"
    fi

    rm -f "$pidfile"
    echo "[pm] ✅ $exchange 종료됨"
}

status_all() {
    echo ""
    echo "┌────────────────┬──────────┬───────┬──────┐"
    echo "│ Exchange       │ Status   │ PID   │ Port │"
    echo "├────────────────┼──────────┼───────┼──────┤"
    for ex in "${ALL_EXCHANGES[@]}"; do
        local pidfile="$PID_DIR/${ex}.pid"
        if is_running "$ex"; then
            local pid; pid=$(cat "$pidfile")
            # API 헬스체크
            local api_status="?"
            if command -v curl &>/dev/null; then
                local resp
                resp=$(curl -sk --max-time 2 -u death:mode2026 \
                    "http://127.0.0.1:${EXCHANGE_PORT[$ex]}/api/v1/ping" 2>/dev/null || echo "")
                [[ "$resp" == *"pong"* ]] && api_status="✅" || api_status="⚠️"
            fi
            printf "│ %-14s │ RUNNING  │ %-5s │ %-4s │ API:%s\n" \
                "$ex" "$pid" "${EXCHANGE_PORT[$ex]}" "$api_status"
        else
            printf "│ %-14s │ STOPPED  │  ---  │ %-4s │\n" \
                "$ex" "${EXCHANGE_PORT[$ex]}"
        fi
    done
    echo "└────────────────┴──────────┴───────┴──────┘"
    echo ""
}

show_logs() {
    local exchange="$1"
    local logfile="$LOG_DIR/pm_${exchange}.log"
    local ftlog="$LOG_DIR/freqtrade_${exchange}.log"

    echo "=== PM log: $logfile ==="
    tail -30 "$logfile" 2>/dev/null || echo "(없음)"
    echo ""
    echo "=== Freqtrade log: $ftlog ==="
    tail -30 "$ftlog" 2>/dev/null || echo "(없음)"
}

freqtrade_cmd() {
    local exchange="$1"
    shift
    local config="${EXCHANGE_CONFIG[$exchange]}"
    "$FREQTRADE" "$@" --config "$CONFIG_DIR/$config" --strategy NostalgiaForInfinityX7
}

##############################################################
# MAIN
##############################################################

ACTION="${1:-status}"
TARGET="${2:-all}"

case "$ACTION" in
    start)
        for ex in $(get_exchanges "$TARGET"); do
            start_exchange "$ex"
        done
        sleep 2
        status_all
        ;;
    stop)
        for ex in $(get_exchanges "$TARGET"); do
            stop_exchange "$ex"
        done
        status_all
        ;;
    restart)
        for ex in $(get_exchanges "$TARGET"); do
            stop_exchange "$ex"
            sleep 1
            start_exchange "$ex"
        done
        status_all
        ;;
    status)
        status_all
        ;;
    logs)
        show_logs "$TARGET"
        ;;
    api-start)
        # 실행 중인 봇에 /start 전송
        for ex in $(get_exchanges "$TARGET"); do
            if is_running "$ex"; then
                resp=$(curl -sk -u death:mode2026 -X POST \
                    "http://127.0.0.1:${EXCHANGE_PORT[$ex]}/api/v1/start" 2>/dev/null)
                echo "[pm] $ex /start → $resp"
            fi
        done
        ;;
    api-stop)
        for ex in $(get_exchanges "$TARGET"); do
            if is_running "$ex"; then
                resp=$(curl -sk -u death:mode2026 -X POST \
                    "http://127.0.0.1:${EXCHANGE_PORT[$ex]}/api/v1/stop" 2>/dev/null)
                echo "[pm] $ex /stop → $resp"
            fi
        done
        ;;
    download)
        # 데이터 다운로드
        for ex in $(get_exchanges "$TARGET"); do
            echo "[pm] $ex 데이터 다운로드 시작..."
            "$FREQTRADE" download-data \
                --config "$CONFIG_DIR/${EXCHANGE_CONFIG[$ex]}" \
                --timeframe 5m 1h 4h \
                --days 30 &
        done
        wait
        echo "[pm] 다운로드 완료"
        ;;
    backtest)
        exchange="${TARGET}"
        echo "[pm] $exchange 백테스트..."
        "$FREQTRADE" backtesting \
            --config "$CONFIG_DIR/${EXCHANGE_CONFIG[$exchange]}" \
            --strategy NostalgiaForInfinityX7 \
            --timerange 20250101-20250501 \
            --enable-protections
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs|api-start|api-stop|download|backtest} [exchange|all|cex|dex]"
        echo ""
        echo "Exchanges: lighter hyperliquid bitget bybit okx"
        echo "Groups:    all cex dex"
        exit 1
        ;;
esac
