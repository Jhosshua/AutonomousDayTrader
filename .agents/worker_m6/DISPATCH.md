# DISPATCH: Milestone 6 (delivery_hygiene)

## Mission
You are worker_m6, the delivery and hygiene worker for Milestone 6 (delivery_hygiene) of AutonomousDayTrader.

## Identity & Context
- Identity: worker_m6
- Working directory: /Users/mo/AutonomousDayTrader/.agents/worker_m6
- Parent orchestrator conversation ID: f9df3e28-501d-4830-bf1f-140b6216f49e
- Project directory: /Users/mo/AutonomousDayTrader
- Authoritative user request: /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md
- Project scope & architecture: /Users/mo/AutonomousDayTrader/PROJECT.md

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Operating Requirements
1. Remote Deployment & Upstream Git Push Mandate:
   - Committing locally to git is NOT sufficient.
   - You MUST ensure code is committed with clear, structured messages and pushed to the upstream repository (`git push origin main`).
   - If GitHub remote `origin` is not yet configured, use `gh` CLI (user `Jhosshua`) to create/link the remote repo and push `main`.
2. Process Hygiene Mandate:
   - Cleanly terminate all background test scripts, mock servers, or daemons.
   - Audit and confirm that ports 3005, 8005, and 8080 are 100% free with zero lingering processes.
   - Run `./scripts/verify_port_hygiene.sh` and `lsof -i:3005 -i:8005 -i:8080`.

## Detailed Tasks
1. Git Configuration & Clean Commits:
   - Check `git status` in `/Users/mo/AutonomousDayTrader`.
   - Ensure a robust `.gitignore` exists ignoring temporary caches (`.next/`, `node_modules/`, `__pycache__/`, `.pytest_cache/`, `*.pyc`).
   - Stage files and commit with descriptive commit messages covering the project milestones.
   - Verify/configure upstream remote `origin` and push: `git push -u origin main` (or `git push origin main`).
   - Check `git status` and `git log -5` to confirm working tree is clean and up to date with `origin/main`.
2. Port Hygiene & Verification:
   - Run `./scripts/verify_port_hygiene.sh`.
   - Verify `lsof -i:3005 -i:8005 -i:8080` returns clean.
3. Handoff:
   - Write comprehensive report to `/Users/mo/AutonomousDayTrader/.agents/worker_m6/handoff.md`.
   - Send completion message to parent orchestrator.

## 2026-09-19T21:07:19-04:00
User Request received for worker_m6 execution.
