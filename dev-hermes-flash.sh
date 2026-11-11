#!/usr/bin/env bash
# Run the Example's native Hermes profile without changing the user's default.
set -euo pipefail

example_root="$(cd "$(dirname "$0")" && pwd)"
example_profile="$example_root/backend/data/hermes-example-flash"
example_runtime="${HERMES_BIN:-$HOME/.local/bin/hermes}"

if [[ ! -f "$example_profile/config.yaml" || ! -x "$example_runtime" ]]; then
  echo "The Example Hermes profile or local Hermes runtime is unavailable." >&2
  exit 1
fi

exec env -u WEBUI_SECRET_KEY -u SESSION_SECRET HERMES_HOME="$example_profile" \
  "$example_runtime" gateway run
