#!/usr/bin/env bash
# Load project env into the current shell.
# Prefer .env; fall back to secrets.env (legacy / symlink).
# Usage:  source scripts/load_env.sh
#         # or from other scripts after setting PROJ

_dm_proj="${PROJ:-${PROJ_DIR:-}}"
if [[ -z "${_dm_proj}" ]]; then
  _dm_proj="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

_dm_loaded=""
for _dm_f in "${_dm_proj}/.env" "${_dm_proj}/secrets.env"; do
  if [[ -f "${_dm_f}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${_dm_f}"
    set +a
    _dm_loaded="${_dm_f}"
    break
  fi
done

if [[ -n "${_dm_loaded}" ]]; then
  # Keep exchange_patch alias in sync when only one is set
  if [[ -z "${LIGHTER_PRIVATE_KEY:-}" && -n "${LIGHTER_API_SECRET:-}" ]]; then
    export LIGHTER_PRIVATE_KEY="${LIGHTER_API_SECRET}"
  fi
  if [[ -z "${LIGHTER_API_SECRET:-}" && -n "${LIGHTER_PRIVATE_KEY:-}" ]]; then
    export LIGHTER_API_SECRET="${LIGHTER_PRIVATE_KEY}"
  fi
  export DM_ENV_FILE="${_dm_loaded}"
else
  unset DM_ENV_FILE 2>/dev/null || true
fi

unset _dm_proj _dm_f _dm_loaded
