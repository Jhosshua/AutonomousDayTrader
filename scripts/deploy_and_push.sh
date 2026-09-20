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

# 2. Check Git initialization
echo "📦 2. Checking Git repository state..."
if [ ! -d ".git" ]; then
  echo "   Initializing local Git repository..."
  git init -b main
fi

# Ensure branch is main
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
if [ "$CURRENT_BRANCH" != "main" ]; then
  git checkout -B main
fi

# 3. Verify GitHub authentication
echo "🔑 3. Checking GitHub authentication..."
gh auth status

# 4. Stage files and create structured commit if needed
echo "📝 4. Staging files and creating commit..."
git add -A

if git diff --staged --quiet; then
  echo "   No staged changes detected. Working tree is clean."
else
  COMMIT_MSG="${1:-feat: deliver AutonomousDayTrader production release with 4 strategies, Apple Music UI, and certified Monday dry run}"
  git commit -m "$COMMIT_MSG"
fi

# 5. Check / configure remote origin
echo "🌐 5. Checking remote origin configuration..."
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

# 6. Push to remote main
echo "⬆️ 6. Pushing to GitHub upstream (origin/main)..."
git push -u origin main

# 7. Final status verification
echo "📊 7. Final Git Status & Remote Log:"
git status
git log -3 --oneline

# 8. Post-push port hygiene audit
echo "🧹 8. Post-deployment port hygiene certification..."
"${PROJECT_ROOT}/scripts/verify_port_hygiene.sh"

echo "✨ Upstream push and delivery hygiene verification COMPLETE!"
