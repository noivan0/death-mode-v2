#!/usr/bin/env bash
# DEATH MODE v2 — start Lighter LIVE futures (Cardinal)
# Requires allowed jurisdiction (preflight). Uses .env credentials.
set -euo pipefail
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ"
export PATH="${HOME}/.local/bin:${PATH}"
export PYTHONPATH="${PROJ}${PYTHONPATH:+:$PYTHONPATH}"
export PROJ_DIR="${PROJ_DIR:-$PROJ}"
export PROJ="${PROJ:-$PROJ}"
export DM_STRATEGY="${DM_STRATEGY:-DeathModeV2_Cardinal}"

# shellcheck disable=SC1091
source "$PROJ/scripts/load_env.sh"

echo "=== LIVE preflight ==="
set +e
python3 "$PROJ/scripts/preflight_live.py"
rc=$?
set -e
case "$rc" in
  0) echo "✅ preflight OK" ;;
  3) echo "❌ collateral too low"; exit 3 ;;
  4) echo "❌ restricted jurisdiction — run this host from an allowed network (not Cloud VM)"; exit 4 ;;
  *) echo "❌ preflight failed rc=$rc"; exit "$rc" ;;
esac

# Expand live config from template + .env
python3 - <<'PY'
import os, re
from pathlib import Path
root = Path(os.environ["PROJ_DIR"])
tmpl = (root / "config/lighter_live_cardinal.json.template").read_text()
pat = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
def repl(m):
    var, default = m.group(1), m.group(2)
    val = os.environ.get(var)
    if val is None or val == "":
        return default if default is not None else ""
    return val
out = pat.sub(repl, tmpl)
path = root / "config/lighter_live_cardinal.json"
path.write_text(out)
assert '"dry_run": false' in out
assert "${" not in out
print(f"wrote {path}")
PY

python3 scripts/ensure_lighter_ccxt_patch.py

CONFIG="config/lighter_live_cardinal.json"
SESSION="lighter-live"

# Stop paper if running on same port/config family
if pgrep -f "freqtrade trade --config config/lighter_paper_cardinal.json" >/dev/null; then
  echo "Stopping paper bot..."
  pkill -f "freqtrade trade --config config/lighter_paper_cardinal.json" || true
  sleep 2
fi
if pgrep -f "freqtrade trade --config ${CONFIG}" >/dev/null; then
  echo "already running: ${CONFIG}"
  pgrep -af "freqtrade trade --config ${CONFIG}"
  exit 0
fi

mkdir -p user_data/logs .pids

if ! command -v freqtrade >/dev/null; then
  echo "freqtrade not found"
  exit 1
fi

echo "🚀 starting LIVE Cardinal on Lighter (dry_run=false)"
if command -v tmux >/dev/null; then
  TMUX_CFG=(-f /exec-daemon/tmux.portal.conf)
  tmux "${TMUX_CFG[@]}" has-session -t "=${SESSION}" 2>/dev/null || \
    tmux "${TMUX_CFG[@]}" new-session -d -s "${SESSION}" -c "${PROJ}" -- "${SHELL:-bash}" -l
  tmux "${TMUX_CFG[@]}" send-keys -t "${SESSION}:0.0" \
    "export PATH=\"\$HOME/.local/bin:\$PATH\"; export PYTHONPATH=${PROJ}; export PROJ_DIR=${PROJ}; set -a; source ${PROJ}/.env; set +a; freqtrade trade --config ${CONFIG} --strategy ${DM_STRATEGY} --strategy-path user_data/strategies --userdir user_data 2>&1 | tee -a user_data/logs/lighter_live_cardinal.console.log" C-m
  sleep 4
  pid="$(pgrep -f "freqtrade trade --config ${CONFIG}" | head -1 || true)"
  if [[ -n "${pid}" ]]; then
    echo "${pid}" > .pids/lighter.pid
    echo "✅ LIVE bot started pid=${pid} session=${SESSION} config=${CONFIG}"
  else
    echo "❌ failed to start — check tmux ${SESSION} / logs"
    exit 1
  fi
else
  set -a; source "$PROJ/.env"; set +a
  nohup freqtrade trade \
    --config "${CONFIG}" \
    --strategy "${DM_STRATEGY}" \
    --strategy-path user_data/strategies \
    --userdir user_data \
    >> user_data/logs/lighter_live_cardinal.console.log 2>&1 &
  echo $! > .pids/lighter.pid
  echo "✅ LIVE bot started pid=$(cat .pids/lighter.pid)"
fi
