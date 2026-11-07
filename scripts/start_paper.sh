#!/usr/bin/env bash
# DEATH MODE v2 — start Lighter dry-run paper bot (Cardinal)
set -euo pipefail
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ"
export PATH="${HOME}/.local/bin:${PATH}"
export PYTHONPATH="${PROJ}${PYTHONPATH:+:$PYTHONPATH}"
export PROJ_DIR="${PROJ_DIR:-$PROJ}"
export PROJ="${PROJ:-$PROJ}"
export DM_STRATEGY="${DM_STRATEGY:-DeathModeV2_Cardinal}"

# Prefer .env (secrets.env is legacy / symlink)
# shellcheck disable=SC1091
source "$PROJ/scripts/load_env.sh"

python3 scripts/ensure_lighter_ccxt_patch.py

CONFIG="${1:-config/lighter_paper_cardinal.json}"
SESSION="${2:-lighter-paper}"

if ! command -v freqtrade >/dev/null; then
  echo "freqtrade not found. pip install --user freqtrade"
  exit 1
fi

mkdir -p user_data/logs .pids

if pgrep -f "freqtrade trade --config ${CONFIG}" >/dev/null; then
  echo "already running: ${CONFIG}"
  pgrep -af "freqtrade trade --config ${CONFIG}"
  exit 0
fi

if command -v tmux >/dev/null; then
  TMUX_CFG=(-f /exec-daemon/tmux.portal.conf)
  tmux "${TMUX_CFG[@]}" has-session -t "=${SESSION}" 2>/dev/null || \
    tmux "${TMUX_CFG[@]}" new-session -d -s "${SESSION}" -c "${PROJ}" -- "${SHELL:-bash}" -l
  tmux "${TMUX_CFG[@]}" send-keys -t "${SESSION}:0.0" \
    "export PATH=\"\$HOME/.local/bin:\$PATH\"; export PYTHONPATH=${PROJ}; export PROJ_DIR=${PROJ}; freqtrade trade --config ${CONFIG} --strategy ${DM_STRATEGY} --strategy-path user_data/strategies --userdir user_data 2>&1 | tee -a user_data/logs/lighter_paper_cardinal.console.log" C-m
  sleep 3
  pid="$(pgrep -f "freqtrade trade --config ${CONFIG}" | head -1 || true)"
  if [[ -n "${pid}" ]]; then
    echo "${pid}" > .pids/lighter.pid
    echo "✅ paper bot started pid=${pid} session=${SESSION} config=${CONFIG}"
  else
    echo "❌ failed to start — check tmux ${SESSION}"
    exit 1
  fi
else
  nohup freqtrade trade \
    --config "${CONFIG}" \
    --strategy "${DM_STRATEGY}" \
    --strategy-path user_data/strategies \
    --userdir user_data \
    >> user_data/logs/lighter_paper_cardinal.console.log 2>&1 &
  echo $! > .pids/lighter.pid
  echo "✅ paper bot started pid=$(cat .pids/lighter.pid)"
fi
