#!/usr/bin/env bash
# Run the same two-process local stack used for production-like QA.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -z "${RELAY_TOKEN:-}" ]]; then
  echo "RELAY_TOKEN is required for production-stack mode; refusing to start an unconfigured trading feed." >&2
  exit 1
fi

exec "${PROJECT_ROOT}/scripts/run_dev.sh"
