#!/bin/bash
##############################################################
# DEATH MODE v2 — Freqtrade Entrypoint with Template Expansion
#
# 1. ccxt lighter.py 패치
# 2. config 템플릿 → 실제 config 자동 변환 (envsubst)
# 3. Freqtrade 실행
##############################################################
set -euo pipefail

# ── Python 버전 감지 ────────────────────────────────────────────
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[entrypoint] Python: ${PYVER}"

# ── ccxt 패치 ──────────────────────────────────────────────────
CCXT_DIR=""
for candidate in \
  "/home/ftuser/.local/lib/python${PYVER}/site-packages/ccxt" \
  "/root/.local/lib/python${PYVER}/site-packages/ccxt" \
  "/usr/local/lib/python${PYVER}/dist-packages/ccxt" \
  "/usr/lib/python${PYVER}/dist-packages/ccxt"; do
  if [ -d "$candidate" ]; then CCXT_DIR="$candidate"; break; fi
done

if [ -z "$CCXT_DIR" ]; then
  CCXT_DIR=$(python3 -c "import ccxt, os; print(os.path.dirname(ccxt.__file__))" 2>/dev/null || echo "")
fi

if [ -n "$CCXT_DIR" ] && [ -d "/ft_patches" ]; then
  echo "[entrypoint] ccxt: ${CCXT_DIR}"
  for f in ccxt_lighter.py ccxt_async_support_lighter.py ccxt_pro_lighter.py; do
    src="/ft_patches/$f"
    if [ -f "$src" ]; then
      case "$f" in
        ccxt_lighter.py)                dst="${CCXT_DIR}/lighter.py" ;;
        ccxt_async_support_lighter.py)  dst="${CCXT_DIR}/async_support/lighter.py" ;;
        ccxt_pro_lighter.py)            dst="${CCXT_DIR}/pro/lighter.py" ;;
      esac
      mkdir -p "$(dirname "$dst")"
      cp "$src" "$dst"
      echo "  patched: $f → $dst"
    fi
  done
  # 검증
  python3 -c "
import ccxt
l = ccxt.lighter()
assert l.has.get('fetchOrder') == True, 'ccxt patch FAILED!'
print('[entrypoint] ✅ ccxt lighter patch verified')
" 2>&1 || echo "[entrypoint] ⚠️  ccxt patch verification skipped (may need freight)")

  # Exchange __init__.py에 Lighter 등록
  EXCH_INIT="${CCXT_DIR%ccxt}/freqtrade/exchange/__init__.py"
  if [ -f "$EXCH_INIT" ] && ! grep -q "from freqtrade.exchange.lighter import Lighter" "$EXCH_INIT"; then
    echo "from freqtrade.exchange.lighter import Lighter" >> "$EXCH_INIT"
    echo "[entrypoint] ✅ Lighter registered in exchange/__init__.py"
  fi
fi

# ── Config 템플릿 → 실제 파일 변환 ────────────────────────────
CONFIG_DIR="/freqtrade/config"
if [ -d "$CONFIG_DIR" ]; then
  for tmpl in "$CONFIG_DIR"/*.json.template; do
    [ -f "$tmpl" ] || continue
    base=$(basename "$tmpl" .template)
    target="$CONFIG_DIR/$base"
    if [ ! -f "$target" ]; then
      echo "[entrypoint] config: $base.template → $base (envsubst)"
      # 기본 envsubst (없으면 그대로 복사)
      if command -v envsubst &>/dev/null; then
        envsubst < "$tmpl" > "$target"
      else
        cp "$tmpl" "$target"
      fi
    fi
  done
fi

# ── SSL 설정 로그 ──────────────────────────────────────────────
if [ "${LIGHTER_SKIP_SSL_VERIFY:-0}" = "1" ]; then
  echo "[entrypoint] ⚠️  SSL verification DISABLED (HMG internal network)"
else
  echo "[entrypoint] ✅ SSL verification ENABLED"
fi

# ── Freqtrade 실행 ──────────────────────────────────────────────
echo "[entrypoint] Starting freqtrade: $*"
exec freqtrade "$@"
