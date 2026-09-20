# Task Dispatch: Release Engineer — Documentation, Git Push, Railway CI/CD & Hygiene

## Objective
Execute Requirement R6 to finalize and release AutonomousDayTrader:

1. **Document Audit Findings & Outcomes**:
   - Update `/Users/mo/AutonomousDayTrader/MEMORY.md` with:
     - New decisions:
       - Mathematical floating-point risk clamp: interior stop clamping `[0.0042, 0.0380]` in `orb.py`, `news_momentum.py`, and `vwap_pullback.py` with `EPS = 1e-6` in `risk.py`.
       - Bracket lifecycle: `manual_tighten_stop` strictly guarded for `ACTIVE` / `TARGET_1_HIT` brackets, with `activate_bracket_on_fill` in tests.
       - Post-publish telemetry counting in `stock_ws.py` and `news_ws.py`.
       - Flat-book session boundary reset: clearing `account.positions` and cancelling lingering working orders.
     - Session log: documenting the comprehensive architectural audit (10 issues remediated), complete de-themification (0 music terms across repo), 163 backend tests, 318 E2E tests passing 100%, Monday market open simulation dry-run certified ($50,398.30 equity, +$398.30 PnL), mobile/desktop visual UI verification (17/17 tests passing, clean Next.js build), and Railway deployment verification.
   - Update `/Users/mo/AutonomousDayTrader/PROJECT.md` with:
     - Updated milestones and test counts (163 backend tests, 318 E2E tests).
     - De-themified documentation and verified deployment status.

2. **Commit and Push to GitHub**:
   - Inspect `git status` and `git diff`.
   - Stage all project files: `git add -A`.
   - Commit with descriptive message:
     `git commit -m "fix(core): complete architectural audit remediation, terminology de-themification, and QA hardening"`
   - Push to upstream: `git push origin main`.

3. **Verify Railway Auto-Deploy**:
   - Check Railway build status via Railway CLI (`railway status`, `railway deployment list`, or `railway logs`).
   - Confirm Railway automatically detects the push and finishes a successful build with active status `SUCCESS`.

4. **Verify Remote Live Production Health Endpoint**:
   - Query remote health endpoint:
     `curl -fsSL https://autonomousdaytrader-production.up.railway.app/health`
   - Assert HTTP 200 and JSON response `{"status":"ok"}`.

5. **Enforce Strict Process & Port Hygiene**:
   - Terminate all test servers, mock feeds, or background processes.
   - Run `./scripts/verify_port_hygiene.sh` and confirm ports 3005, 8005, 8080 are released.

## Mandatory Integrity Warning
DO NOT CHEAT. All deployments, health checks, and git operations must be genuine.

Write your report to:
`/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_worker_release_1/handoff.md`
