#!/usr/bin/env bash
# run_monday_dry_run.sh - AutonomousDayTrader Monday Market Open Live Simulation Runner
set -euo pipefail

# Trap signals for process hygiene
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT INT TERM

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "========================================================================"
echo " 🌅 AUTONOMOUS DAY TRADER: MONDAY MARKET OPEN LIVE SIMULATION DRY RUN"
echo "========================================================================"

cd "${PROJECT_ROOT}"

# Run simulation dry run script with all arguments passed
python3 "${PROJECT_ROOT}/scripts/run_monday_dry_run.py" "$@"
SIM_EXIT=$?

if [ ${SIM_EXIT} -ne 0 ]; then
  echo "❌ Monday Market Open Dry Run failed with exit code ${SIM_EXIT}!"
  exit ${SIM_EXIT}
fi

echo ""
echo "========================================================================"
echo " 🔍 AUDITING PROCESS HYGIENE & LIBERATED PORTS"
echo "========================================================================"
"${PROJECT_ROOT}/scripts/verify_port_hygiene.sh"

echo ""
echo "✨ Monday Market Open Dry Run Certification Complete!"
echo "📄 Report available at: ${PROJECT_ROOT}/MONDAY_SIMULATION_REPORT.md"

exit 0
