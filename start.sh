#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec "${ORCHESTRA_LAUNCHER_PYTHON:-python3}" scripts/orchestra.py start "$@"
