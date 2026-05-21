#!/bin/bash
##############################################################
# DEATH MODE v2 — Freqtrade Entrypoint with ccxt Patch
#
# [BLOCK-01] Python 버전 동적 감지 → ccxt lighter.py 패치
# python3.14 하드코딩 제거. 어떤 Python 버전에서도 동작.
#
# 패치 대상 파일:
#   /ft_patches/ccxt_lighter.py           → ccxt/lighter.py
#   /ft_patches/ccxt_async_support_lighter.py → ccxt/async_support/lighter.py
#   /ft_patches/ccxt_pro_lighter.py       → ccxt/pro/lighter.py
##############################################################

set -euo pipefail

# Python 버전 자동 감지
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[entrypoint] Python version: ${PYVER}"

# ccxt 설치 경로 후보 (user-local 우선, system fallback)
CCXT_PATHS=(
  "/home/ftuser/.local/lib/python${PYVER}/site-packages/ccxt"
  "/root/.local/lib/python${PYVER}/site-packages/ccxt"
  "/usr/local/lib/python${PYVER}/dist-packages/ccxt"
  "/usr/lib/python${PYVER}/dist-packages/ccxt"
  "/usr/local/lib/python3/dist-packages/ccxt"
)

CCXT_DIR=""
for candidate in "${CCXT_PATHS[@]}"; do
  if [ -d "$candidate" ]; then
    CCXT_DIR="$candidate"
    break
  fi
done

if [ -z "$CCXT_DIR" ]; then
  # python import로 실제 경로 탐지
  CCXT_DIR=$(python3 -c "import ccxt, os; print(os.path.dirname(ccxt.__file__))" 2>/dev/null || echo "")
fi

if [ -z "$CCXT_DIR" ]; then
  echo "[entrypoint] WARNING: ccxt directory not found — patch skipped!"
else
  echo "[entrypoint] ccxt path: ${CCXT_DIR}"

  # lighter.py 패치 적용
  if [ -f "/ft_patches/ccxt_lighter.py" ]; then
    cp /ft_patches/ccxt_lighter.py "${CCXT_DIR}/lighter.py"
    echo "[entrypoint] Patched: ${CCXT_DIR}/lighter.py"
  fi

  if [ -f "/ft_patches/ccxt_async_support_lighter.py" ] && [ -d "${CCXT_DIR}/async_support" ]; then
    cp /ft_patches/ccxt_async_support_lighter.py "${CCXT_DIR}/async_support/lighter.py"
    echo "[entrypoint] Patched: ${CCXT_DIR}/async_support/lighter.py"
  fi

  if [ -f "/ft_patches/ccxt_pro_lighter.py" ] && [ -d "${CCXT_DIR}/pro" ]; then
    cp /ft_patches/ccxt_pro_lighter.py "${CCXT_DIR}/pro/lighter.py"
    echo "[entrypoint] Patched: ${CCXT_DIR}/pro/lighter.py"
  fi

  # 패치 적용 검증
  python3 -c "
import ccxt
l = ccxt.lighter()
assert l.has.get('fetchOrder') == True, 'ccxt patch FAILED — fetchOrder not True!'
print('[entrypoint] ccxt lighter patch: VERIFIED (fetchOrder=True)')
" || echo "[entrypoint] WARNING: ccxt patch verification failed!"
fi

# SSL verify 설정 로그
if [ "${LIGHTER_SKIP_SSL_VERIFY:-0}" = "1" ]; then
  echo "[entrypoint] ⚠️  SSL verification DISABLED (HMG internal network mode)"
else
  echo "[entrypoint] ✅ SSL verification ENABLED (production mode)"
fi

# Freqtrade 실행 (원래 command 인수 그대로 전달)
echo "[entrypoint] Starting freqtrade: $*"
exec freqtrade "$@"
