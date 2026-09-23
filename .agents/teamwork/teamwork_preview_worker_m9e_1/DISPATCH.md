# Dispatch: Worker M9E (Replay Verification, Visual QA, Documentation & Railway Deployment)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9e_1`

## Authoritative Documents
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/GATE_STATUS.md`

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Objective & Execution Tasks
Execute Milestone M9E (`replay_qa_deployment`):
1. **Deterministic Multi-Day Replay Test Suite & Integrated Dry Run**:
   - Implement `scripts/run_integrated_swing_dry_run.py` and/or `tests/e2e/test_swing_multiday_replay.py`.
   - Run the simulation dry run through the real production event path (ingestion, event bus, risk engine, swing strategy engine, staged orders, 09:30 execution, brackets/stops, 15:58 EOD flattening exemption, and UI state serialization).
   - Verify that:
     - 16:00 close signals qualify and stage for 09:30 open.
     - 09:30 open buy execution fills at $25,000 notional with hard cap of 2 concurrent positions.
     - 2.5x ATR hard stop protects positions.
     - Exits trigger on 5-SMA cross, RSI(2) > 70, 5-day time stop, and earnings veto.
     - Swing positions and stops survive 15:45–15:58 ET auto-flattening.
2. **Visual QA on Next.js Trading Dashboard**:
   - Verify the Next.js UI renders cleanly in desktop (1440x900) and mobile (390x844) viewports.
   - Verify `SegmentedModeToggle`, `SwingTelemetryBar`, `SwingCandidateWatchlist`, and `ActiveSwingPositionsTable`.
   - Verify `npm --prefix frontend test` and `npm --prefix frontend run build` pass with 0 errors.
3. **Documentation Updates**:
   - Update `PROJECT.md` to document Milestone M9, features F24–F29, architecture, and verification results.
   - Update `MEMORY.md` with complete session log, audit findings, 10-point remediation, and dry-run metrics.
   - Update `README.md` to document the swing trading engine, the 7 quantitative rules, and operator controls.
4. **Process Hygiene & Remote Railway Cloud Deployment**:
   - Run `bash scripts/verify_port_hygiene.sh` and ensure zero lingering test processes or occupied ports (3005, 8000, 8005, 8080).
   - Add all changes to git, commit with descriptive message, and push to GitHub `origin main` (`git push origin main`).
   - Verify Railway auto-build succeeds.
   - Query remote live health endpoint `https://autonomousdaytrader-production.up.railway.app/health` and verify HTTP 200 OK (`status: healthy`).
   - Run post-deploy port hygiene check to ensure local ports remain completely clean and liberated.

## Output Requirements
Write your detailed report to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9e_1/handoff.md`.
Include: exact test outputs, dry-run logs, git commit hash, Railway deployment status, remote curl response, and port hygiene verification.
Send a message back to the caller when complete.

## 2026-09-23T22:37:28Z
Execute Milestone M9E:
1. Implement and run deterministic multi-day replay dry run in scripts/run_integrated_swing_dry_run.py / tests/e2e/test_swing_multiday_replay.py through production paths.
2. Conduct desktop (1440x900) and mobile (390x844) visual QA on frontend/.
3. Update PROJECT.md, MEMORY.md, and README.md.
4. Verify port hygiene (3005, 8000, 8005, 8080 clean).
5. Commit and push to git origin main. Verify Railway cloud deployment build and verify live production health endpoint GET https://autonomousdaytrader-production.up.railway.app/health returns HTTP 200 OK.
6. Verify local port hygiene again.
