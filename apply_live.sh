#!/usr/bin/env bash
# One-shot LIVE apply (must run on Lighter-allowed network — not US Cloud VM).
# WSL example: cd /mnt/d/death-mode-v2 && bash apply_live.sh
set -euo pipefail
cd "$(dirname "$0")"
git pull --ff-only origin cursor/cardinal-risk-autonomy-88bb || git pull --ff-only || true
export PROJ_DIR="$PWD" PYTHONPATH="$PWD"
# shellcheck disable=SC1091
source scripts/load_env.sh
bash scripts/manage_env.sh expand || true
bash scripts/start_live.sh
python3 scripts/autonomy_monitor.py --once || true
echo ""
echo "LIVE API: http://127.0.0.1:8091  (death / deathlive)"
