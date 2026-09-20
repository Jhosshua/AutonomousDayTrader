#!/usr/bin/env bash
# run_dev.sh - Local development launcher for AutonomousDayTrader
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "🚀 Starting AutonomousDayTrader Development Environment..."

# Clean up any lingering processes on project ports first
"${PROJECT_ROOT}/scripts/verify_port_hygiene.sh" || true

cleanup() {
  echo ""
  echo "🛑 Shutting down AutonomousDayTrader dev processes..."
  kill $(jobs -p) 2>/dev/null || true
  sleep 1
  "${PROJECT_ROOT}/scripts/verify_port_hygiene.sh" || true
  echo "✨ Development environment stopped cleanly."
}
trap cleanup EXIT INT TERM

cd "${PROJECT_ROOT}"

# 1. Start FastAPI Core Trading Engine (Port 8005)
echo "▶ Starting Backend Trading Engine & WebSocket Server on http://127.0.0.1:8005..."
uvicorn backend.app.main:app --host 127.0.0.1 --port 8005 --log-level info &
BACKEND_PID=$!

# Wait for backend to be responsive
sleep 2

# 2. Start Next.js Apple Music Mobile UI (Port 3005)
echo "▶ Starting Next.js Mobile UI on http://localhost:3005..."
cd "${PROJECT_ROOT}/frontend"
npm run dev &
FRONTEND_PID=$!

echo "======================================================================"
echo " ✅ AutonomousDayTrader is running!"
echo "   - Apple Music UI:    http://localhost:3005"
echo "   - Backend Core API:  http://127.0.0.1:8005"
echo "   - UI WebSocket:      ws://127.0.0.1:8005/ws/ui"
echo "   - Press Ctrl+C to terminate cleanly."
echo "======================================================================"

wait
