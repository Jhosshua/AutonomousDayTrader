#!/usr/bin/env bash
# run_e2e_tests.sh - Execute AutonomousDayTrader Opaque-Box E2E Test Suite with Process Hygiene
set -euo pipefail

# Ensure clean termination of any background jobs
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT INT TERM

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "▶ Running AutonomousDayTrader E2E Test Suite..."
cd "${PROJECT_ROOT}"

# Run test runner with passed arguments (or default to all)
EXIT_CODE=0
python3 "${PROJECT_ROOT}/tests/e2e/runner.py" "$@" || EXIT_CODE=$?

# Multi-layered post-flight port audit
"${PROJECT_ROOT}/scripts/verify_port_hygiene.sh" || HYGIENE_CODE=$?
if [ "${HYGIENE_CODE:-0}" -ne 0 ]; then
  echo "❌ Port hygiene check failed following test run."
  EXIT_CODE=1
fi

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "✅ All E2E tests executed and passed successfully."
else
  echo "❌ E2E test run reported failures (exit code ${EXIT_CODE})."
fi

exit ${EXIT_CODE}
