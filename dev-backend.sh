#!/usr/bin/env bash
# 本地 dev 启动脚本（后端）
set -euo pipefail

cd "$(dirname "$0")"

# Resume the optional Example gateway on backend restart. Its native profile
# owns a separate home and credentials; the default Hermes gateway stays intact.
example_profile="$PWD/backend/data/hermes-example-flash"
if [[ -f "$example_profile/config.yaml" ]] && ! lsof -nP -iTCP:8643 -sTCP:LISTEN >/dev/null 2>&1; then
  nohup bash dev-hermes-flash.sh >"$example_profile/gateway.log" 2>&1 </dev/null &
fi

exec "${ORCHESTRA_LAUNCHER_PYTHON:-python3}" scripts/orchestra.py backend "$@"
