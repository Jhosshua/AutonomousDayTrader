#!/usr/bin/env bash
# run_dev.sh - Local development launcher for AutonomousDayTrader
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "🚀 Starting AutonomousDayTrader Development Environment..."

# Fail fast if any project port is already occupied
"${PROJECT_ROOT}/scripts/verify_port_hygiene.sh"

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
python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8005 --log-level info &
BACKEND_PID=$!

# Poll /health for readiness; fail fast if uvicorn dies (e.g. bind error)
echo "⏳ Waiting for backend readiness on /health..."
READY=0
for _ in $(seq 1 30); do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "❌ Backend process exited before becoming ready (check logs above for bind errors)." >&2
    exit 1
  fi
  if curl -fsS "http://127.0.0.1:8005/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done

if [ "$READY" -ne 1 ]; then
  echo "❌ Backend did not become healthy within 30s." >&2
  exit 1
fi

# 2. Start Next.js Mobile Trading UI (Port 3005)
echo "▶ Starting Next.js Mobile UI on http://localhost:3005..."
cd "${PROJECT_ROOT}/frontend"
npm run dev &
FRONTEND_PID=$!

echo "======================================================================"
echo " ✅ AutonomousDayTrader is running!"
echo "   - Mobile Trading UI: http://localhost:3005"
echo "   - Backend Core API:  http://127.0.0.1:8005"
echo "   - UI WebSocket:      ws://127.0.0.1:8005/ws/ui"
echo "   - Press Ctrl+C to terminate cleanly."
echo "======================================================================"

wait
