#!/bin/bash
##############################################################
# DEATH MODE v2 — 완전 자동 설정 스크립트
# 사용법: bash setup.sh [--dry-run]
#
# 이 스크립트가 하는 일:
#   1. 템플릿 → 실제 config 파일 생성 (envsubst)
#   2. secrets.env 샘플 생성
#   3. 필요한 디렉토리 생성
#   4. Freqtrade 설치 확인
#   5. Lighter Exchange 패치 적용
#   6. 백테스트 결과 압축 해제
##############################################################
set -euo pipefail
PROJ="$(cd "$(dirname "$0")" && pwd)"
DRY_RUN="${1:-}"

echo "💀 DEATH MODE v2 — Setup"
echo "   PROJ=$PROJ"
echo ""

# ── 1. 템플릿 → 실제 config ──────────────────────────────────────
echo "=== 1. Config 템플릿 복사 ==="
for tmpl in "$PROJ"/config/*.json.template; do
    base="$(basename "$tmpl" .template)"
    target="$PROJ/config/$base"
    if [ ! -f "$target" ]; then
        echo "  cp $base.template → $base"
        [ -z "${DRY_RUN}" ] && cp "$tmpl" "$target"
    else
        echo "  ✅ $base 이미 존재"
    fi
done

# ── 2. secrets.env ───────────────────────────────────────────────
echo ""
echo "=== 2. secrets.env ==="
if [ ! -f "$PROJ/secrets.env" ]; then
    cp "$PROJ/secrets.env.template" "$PROJ/secrets.env"
    echo "  ✅ secrets.env 생성됨 (편집 필요)"
    echo "  ⚠️  nano $PROJ/secrets.env 에서 API 키를 입력하세요"
else
    echo "  ✅ secrets.env 이미 존재"
fi

# ── 3. 디렉토리 생성 ─────────────────────────────────────────────
echo ""
echo "=== 3. 디렉토리 생성 ==="
for d in user_data/data/hyperliquid/futures user_data/data/lighter/futures user_data/logs; do
    mkdir -p "$PROJ/$d"
    echo "  ✅ $d"
done

# ── 4. Freqtrade 확인 ────────────────────────────────────────────
echo ""
echo "=== 4. Freqtrade 확인 ==="
if command -v freqtrade &>/dev/null; then
    FT_VER=$(freqtrade --version 2>/dev/null || echo "unknown")
    echo "  ✅ Freqtrade $FT_VER"
else
    echo "  ⚠️  freqtrade 명령어를 찾을 수 없습니다"
    echo "     설치: git clone https://github.com/freqtrade/freqtrade.git && cd freqtrade && ./setup.sh -i"
fi

# ── 5. Lighter Exchange 패치 ──────────────────────────────────────
echo ""
echo "=== 5. Lighter Exchange 패치 ==="
if command -v freqtrade &>/dev/null; then
    FT_DIR=$(python3 -c "import freqtrade; import os; print(os.path.dirname(freqtrade.__file__))" 2>/dev/null || echo "")
    if [ -n "$FT_DIR" ] && [ -f "$PROJ/exchange_patch/lighter.py" ]; then
        echo "  Freqtrade 경로: $FT_DIR"
        if [ ! -f "$FT_DIR/exchange/lighter.py" ]; then
            echo "  cp exchange_patch/lighter.py → $FT_DIR/exchange/lighter.py"
            [ -z "${DRY_RUN}" ] && cp "$PROJ/exchange_patch/lighter.py" "$FT_DIR/exchange/lighter.py"
        else
            echo "  ✅ lighter.py 이미 패치됨"
        fi
        # __init__.py에 등록
        INIT="$FT_DIR/exchange/__init__.py"
        if ! grep -q "from freqtrade.exchange.lighter import Lighter" "$INIT" 2>/dev/null; then
            echo "  Lighter 등록 추가"
            [ -z "${DRY_RUN}" ] && echo "from freqtrade.exchange.lighter import Lighter" >> "$INIT"
        fi
    fi
fi

# ── 6. 백테스트 결과 압축 해제 ──────────────────────────────────
echo ""
echo "=== 6. 백테스트 결과 ==="
BT="$PROJ/user_data/backtest_results"
count=$(ls -1 "$BT"/*.zip 2>/dev/null | wc -l)
echo "  📊 $count 개의 백테스트 ZIP 파일"
for z in "$BT"/*.zip; do
    base="$(basename "$z" .zip)"
    dir="$BT/$base"
    if [ ! -d "$dir" ]; then
        echo "  압축 해제: $base"
        if command -v unzip &>/dev/null; then
            [ -z "${DRY_RUN}" ] && unzip -o "$z" -d "$dir" > /dev/null 2>&1
        else
            echo "  ⚠️  unzip 미설치 — 별도 해제 필요"
        fi
    fi
done

# ── 7. 완료 ──────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "✅ DEATH MODE v2 설정 완료"
echo "========================================"
echo ""
echo "다음 단계:"
echo "  1. nano $PROJ/secrets.env   (API 키 입력)"
echo "  2. bash $PROJ/scripts/start_bots.sh  (페이퍼 트레이딩 시작)"
echo "  3. docker compose up -d              (Docker 실행)"
echo ""
echo "자세한 내용: README.md"
