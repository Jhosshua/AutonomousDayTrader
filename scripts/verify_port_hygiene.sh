#!/usr/bin/env bash
# verify_port_hygiene.sh - Reports whether designated project ports are free.
# This script is intentionally read-only: it must not kill an unrelated process.
set -euo pipefail

PORTS=(3005 8000 8005 8080)
VIOLATIONS=0

echo "🔍 Auditing port hygiene across project ports: ${PORTS[*]}..."

for port in "${PORTS[@]}"; do
  PIDS=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$PIDS" ]; then
    echo "❌ INTEGRITY VIOLATION: Port $port is still occupied by PID(s): $PIDS"
    for pid in $PIDS; do
      CMD=$(ps -p "$pid" -o command= 2>/dev/null || echo "unknown")
      echo "   PID $pid details: $CMD"
    done
    VIOLATIONS=$((VIOLATIONS + 1))
  else
    echo "✅ Port $port is clean and liberated."
  fi
done

if [ "$VIOLATIONS" -gt 0 ]; then
  echo "⚠️ Ports are occupied. Stop only the project-owned process before retrying."
  exit 1
fi

echo "✨ All ports verified clean. Zero lingering daemons."
exit 0
