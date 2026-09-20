#!/usr/bin/env bash
# run_production_stack.sh - Mirrors the Dockerfile CMD path locally: static-export
# the Next.js UI to frontend/out, then run uvicorn serving backend + mounted UI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -z "${RELAY_TOKEN:-}" ]]; then
  echo "RELAY_TOKEN is required for production-stack mode; refusing to start an unconfigured trading feed." >&2
  exit 1
fi

echo "🔍 Verifying port hygiene before production-stack launch..."
"${PROJECT_ROOT}/scripts/verify_port_hygiene.sh"

# 1. Static-export the dashboard (same artifact the Dockerfile produces)
echo "▶ Building Next.js static export (frontend/out)..."
cd "${PROJECT_ROOT}/frontend"
npm run build

# 2. Run the backend exactly as the container CMD does; it mounts frontend/out via StaticFiles
cd "${PROJECT_ROOT}"
echo "▶ Starting uvicorn on http://127.0.0.1:8005 (serving API, /ws/ui, and frontend/out)..."
python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8005 &
BACKEND_PID=$!

cleanup() {
  echo ""
  echo "🛑 Shutting down production stack..."
  kill "$BACKEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
  echo "✨ Production stack stopped cleanly."
}
trap cleanup EXIT INT TERM

# 3. Readiness poll against /health instead of a fixed sleep
echo "⏳ Waiting for backend readiness on /health..."
READY=0
for _ in $(seq 1 30); do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "❌ Backend process exited before becoming ready." >&2
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

echo "======================================================================"
echo " ✅ Production stack is live!"
echo "   - Dashboard + API:   http://127.0.0.1:8005"
echo "   - UI WebSocket:      ws://127.0.0.1:8005/ws/ui"
echo "   - Press Ctrl+C to terminate cleanly."
echo "======================================================================"

wait "$BACKEND_PID"
