#!/usr/bin/env bash
# verify_port_hygiene.sh - Asserts no lingering trading processes on designated project ports
set -euo pipefail

PORTS=(3005 8005 8080)
VIOLATIONS=0

echo "🔍 Auditing port hygiene across project ports: ${PORTS[*]}..."

for port in "${PORTS[@]}"; do
  PIDS=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$PIDS" ]; then
    echo "❌ INTEGRITY VIOLATION: Port $port is still occupied by PID(s): $PIDS"
    for pid in $PIDS; do
      CMD=$(ps -p "$pid" -o command= 2>/dev/null || echo "unknown")
      echo "   PID $pid details: $CMD"
      echo "   Killing lingering PID $pid..."
      kill -9 "$pid" 2>/dev/null || true
    done
    VIOLATIONS=$((VIOLATIONS + 1))
  else
    echo "✅ Port $port is clean and liberated."
  fi
done

if [ "$VIOLATIONS" -gt 0 ]; then
  echo "⚠️ Remediation applied: lingering test processes were forcefully terminated."
  exit 1
fi

echo "✨ All ports verified clean. Zero lingering daemons."
exit 0
