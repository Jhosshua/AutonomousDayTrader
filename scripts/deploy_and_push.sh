#!/usr/bin/env bash
# deploy_and_push.sh - Git commit, remote upstream synchronization, and process hygiene verification
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "🚀 AutonomousDayTrader: Executing Deployment and Upstream Push..."
cd "${PROJECT_ROOT}"

# 1. Enforce process and port hygiene before deployment
echo "🔍 1. Verifying process and port hygiene..."
"${PROJECT_ROOT}/scripts/verify_port_hygiene.sh"

# 2. Build/test gates: nothing ships unless the backend suite and the UI build pass
echo "🧪 2a. Running backend pytest gate..."
python3 -m pytest -q

echo "🏗️ 2b. Running frontend static-export build gate..."
(cd "${PROJECT_ROOT}/frontend" && npm run build)

# 3. Check Git initialization
echo "📦 3. Checking Git repository state..."
if [ ! -d ".git" ]; then
  echo "   Initializing local Git repository..."
  git init -b main
fi

# Ensure branch is main
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
if [ "$CURRENT_BRANCH" != "main" ]; then
  git checkout -B main
fi

# 4. Verify GitHub authentication
echo "🔑 4. Checking GitHub authentication..."
gh auth status

# 5. Stage files and create structured commit if needed
echo "📝 5. Staging files and creating commit..."
git add -A

if git diff --staged --quiet; then
  echo "   No staged changes detected. Working tree is clean."
else
  COMMIT_MSG="${1:-feat: deliver AutonomousDayTrader production release with 4 strategies, Apple Music UI, and certified Monday dry run}"
  git commit -m "$COMMIT_MSG"
fi

# 6. Check / configure remote origin
echo "🌐 6. Checking remote origin configuration..."
REMOTE_EXISTS=$(git remote get-url origin 2>/dev/null || echo "")

if [ -z "$REMOTE_EXISTS" ]; then
  echo "   Remote 'origin' not configured. Checking GitHub for repo AutonomousDayTrader..."
  if gh repo view AutonomousDayTrader >/dev/null 2>&1; then
    echo "   Existing GitHub repository found. Adding remote origin..."
    git remote add origin https://github.com/Jhosshua/AutonomousDayTrader.git
  else
    echo "   Creating new GitHub repository via gh CLI..."
    gh repo create AutonomousDayTrader --public --source=. --remote=origin --push
  fi
fi

# 7. Push to remote main
echo "⬆️ 7. Pushing to GitHub upstream (origin/main)..."
git push -u origin main

# 8. Post-push production health verification (Railway redeploys on push)
echo "🏥 8. Verifying production deployment health..."
PROD_HEALTH_URL="https://autonomousdaytrader-production.up.railway.app/health"
HEALTHY=0
for _ in $(seq 1 18); do
  if curl -fsS "$PROD_HEALTH_URL" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  echo "   Waiting for Railway redeploy to become healthy..."
  sleep 10
done

if [ "$HEALTHY" -eq 1 ]; then
  echo "✅ Production health check PASSED: $PROD_HEALTH_URL"
else
  echo "❌ Production health check FAILED after 180s: $PROD_HEALTH_URL" >&2
  exit 1
fi

# 9. Final status verification
echo "📊 9. Final Git Status & Remote Log:"
git status
git log -3 --oneline

# 10. Post-push port hygiene audit
echo "🧹 10. Post-deployment port hygiene certification..."
"${PROJECT_ROOT}/scripts/verify_port_hygiene.sh"

echo "✨ Upstream push and delivery hygiene verification COMPLETE!"
