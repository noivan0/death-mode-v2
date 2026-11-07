#!/usr/bin/env bash
# DEATH MODE v2 — .env manager
# Usage:
#   bash scripts/manage_env.sh status   # which keys are set (masked)
#   bash scripts/manage_env.sh expand   # envsubst templates → config/*.json
#   bash scripts/manage_env.sh path     # print active env file
#   bash scripts/manage_env.sh sync-alias  # ensure PRIVATE_KEY ↔ API_SECRET
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ"

ENV_FILE=""
if [[ -f "$PROJ/.env" ]]; then
  ENV_FILE="$PROJ/.env"
elif [[ -f "$PROJ/secrets.env" ]]; then
  ENV_FILE="$PROJ/secrets.env"
fi

load() {
  # shellcheck disable=SC1091
  source "$PROJ/scripts/load_env.sh"
}

cmd_path() {
  if [[ -z "$ENV_FILE" ]]; then
    echo "MISSING: no .env or secrets.env — cp .env.template .env"
    exit 1
  fi
  if [[ -L "$ENV_FILE" ]]; then
    echo "$ENV_FILE -> $(readlink -f "$ENV_FILE")"
  else
    echo "$ENV_FILE"
  fi
  ls -la "$ENV_FILE" "$PROJ/secrets.env" 2>/dev/null || true
}

cmd_status() {
  if [[ -z "$ENV_FILE" ]]; then
    echo "MISSING: no .env — cp .env.template .env && chmod 600 .env"
    exit 1
  fi
  load
  echo "env_file: ${DM_ENV_FILE:-$ENV_FILE}"
  echo "mode: $([[ -n "${LIGHTER_PRIVATE_KEY:-}${LIGHTER_API_SECRET:-}" ]] && echo credentials-present || echo credentials-empty)"
  printf "%-28s %s\n" "KEY" "STATUS"
  printf "%-28s %s\n" "----------------------------" "------"
  for k in \
    LIGHTER_ACCOUNT_INDEX LIGHTER_API_KEY_INDEX LIGHTER_CHAIN_ID \
    LIGHTER_API_KEY LIGHTER_API_SECRET LIGHTER_PRIVATE_KEY \
    LIGHTER_SKIP_SSL_VERIFY LIGHTER_BASE_URL \
    HYPERLIQUID_WALLET_ADDRESS HYPERLIQUID_PRIVATE_KEY \
    DM_STRATEGY DM_MAX_RISK DM_MAX_DAY_LOSS
  do
    v="${!k-}"
    if [[ -z "${v}" ]]; then
      printf "%-28s %s\n" "$k" "empty"
    elif [[ "$k" == LIGHTER_API_KEY || "$k" == LIGHTER_API_SECRET || "$k" == LIGHTER_PRIVATE_KEY || "$k" == HYPERLIQUID_PRIVATE_KEY ]]; then
      printf "%-28s %s\n" "$k" "set(len=${#v})"
    else
      printf "%-28s %s\n" "$k" "$v"
    fi
  done
}

cmd_sync_alias() {
  if [[ -z "$ENV_FILE" ]]; then
    echo "no env file"; exit 1
  fi
  # Resolve symlink target for edits
  local target
  target="$(readlink -f "$ENV_FILE")"
  load
  local secret="${LIGHTER_API_SECRET:-}"
  local priv="${LIGHTER_PRIVATE_KEY:-}"
  if [[ -z "$secret" && -z "$priv" ]]; then
    echo "both LIGHTER_API_SECRET and LIGHTER_PRIVATE_KEY empty — nothing to sync"
    return 0
  fi
  if [[ -z "$secret" && -n "$priv" ]]; then
    if grep -q '^LIGHTER_API_SECRET=' "$target"; then
      sed -i "s|^LIGHTER_API_SECRET=.*|LIGHTER_API_SECRET=${priv}|" "$target"
    else
      echo "LIGHTER_API_SECRET=${priv}" >> "$target"
    fi
    echo "synced LIGHTER_API_SECRET from LIGHTER_PRIVATE_KEY"
  elif [[ -z "$priv" && -n "$secret" ]]; then
    if grep -q '^LIGHTER_PRIVATE_KEY=' "$target"; then
      sed -i "s|^LIGHTER_PRIVATE_KEY=.*|LIGHTER_PRIVATE_KEY=${secret}|" "$target"
    else
      echo "LIGHTER_PRIVATE_KEY=${secret}" >> "$target"
    fi
    echo "synced LIGHTER_PRIVATE_KEY from LIGHTER_API_SECRET"
  elif [[ "$secret" != "$priv" ]]; then
    echo "WARN: LIGHTER_API_SECRET and LIGHTER_PRIVATE_KEY differ — leaving as-is"
    exit 2
  else
    echo "aliases already in sync"
  fi
}

expand_file() {
  local tmpl="$1"
  local out="$2"
  python3 - "$tmpl" "$out" <<'PY'
import os, re, sys
from pathlib import Path
tmpl, out = Path(sys.argv[1]), Path(sys.argv[2])
text = tmpl.read_text()
pat = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
def repl(m):
    var, default = m.group(1), m.group(2)
    val = os.environ.get(var)
    if val is None or val == "":
        return default if default is not None else ""
    return val
out.write_text(pat.sub(repl, text))
print(f"OK: {out}")
PY
}

cmd_expand() {
  load
  if [[ -z "${LIGHTER_ACCOUNT_INDEX:-}" ]]; then
    echo "WARN: LIGHTER_ACCOUNT_INDEX empty — expanding with defaults"
  fi
  mkdir -p "$PROJ/config"
  for tmpl in "$PROJ"/config/*.json.template; do
    [[ -f "$tmpl" ]] || continue
    base="$(basename "$tmpl" .template)"
    # paper configs are committed JSON — only expand futures / templated runtime files
    case "$base" in
      *_paper_*|pairlist-*) continue ;;
    esac
    expand_file "$tmpl" "$PROJ/config/$base"
  done
}

usage() {
  echo "Usage: $0 {status|expand|path|sync-alias}"
  exit 1
}

case "${1:-}" in
  status) cmd_status ;;
  expand) cmd_expand ;;
  path) cmd_path ;;
  sync-alias) cmd_sync_alias ;;
  *) usage ;;
esac
