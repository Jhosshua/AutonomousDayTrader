# Task Dispatch: QA, Monday Dry-Run Simulation & Visual UI Specialist

## Objectives
Execute Requirements R4 and R5:
1. **R4: Deterministic Market Open Dry-Run Simulation**:
   - Run the deterministic Monday market open simulation dry run:
     `python3 scripts/run_monday_dry_run.py --speed 10.0`
   - Verify:
     - Full signal ingestion through mock relay
     - Order book execution and dynamic bracket lifecycle (targets, stops, trailing ATR)
     - Mark-to-market PnL tracking ($50,000 initial equity)
     - Institutional risk engine circuit breaker monitoring
     - 4-phase end-of-day flattening routines
     - UI WebSocket serialization (port 8005)
     - Clean logs and zero unhandled exceptions
   - Document simulation execution results, trade count, realized PnL, and final equity in `MONDAY_SIMULATION_REPORT.md`.

2. **R5: Mobile & Desktop Visual UI Audit**:
   - Verify frontend builds cleanly: `npm --prefix frontend run build`.
   - Perform visual UI inspection across both:
     - Mobile viewport: `390x844`
     - Desktop viewport: `1440x900`
   - Verify rendering:
     - Strategy cards (ORB, VWAP, News Momentum, Mean Reversion)
     - Active Position bottom tray and expansion drawer
     - Live chart, bracket level visualization, manual intervention buttons
     - Risk badges and time-of-day phase indicators
     - Zero text truncation, zero element overlapping, zero horizontal overflow
   - Run `pytest tests/e2e/test_challenger_mobile.py -v` to certify mobile interactions.
   - Run visual inspection / Playwright scripts if desired to capture and verify mobile/desktop layouts.

3. **Port & Process Hygiene**:
   - Immediately terminate any running servers, background mocks, or test daemons.
   - Run `./scripts/verify_port_hygiene.sh` and ensure ports 3005, 8005, and 8080 are released.

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations and simulations must be genuine. A forensic auditor will independently verify your work.

Write your comprehensive report to:
`/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_worker_qa_sim_visual_1/handoff.md`

## 2026-09-20T13:49:49Z
You are the QA, Dry-Run Simulation & Visual UI Specialist for AutonomousDayTrader.

Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_worker_qa_sim_visual_1
Project root: /Users/mo/AutonomousDayTrader

MANDATORY FIRST STEP: Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also read:
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_worker_qa_sim_visual_1/DISPATCH.md
- /Users/mo/AutonomousDayTrader/PROJECT.md
- /Users/mo/AutonomousDayTrader/TEST_INFRA.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All simulations and checks must be genuine.

Execute Requirements R4 and R5:
1. Run the deterministic Monday market open dry-run simulation: `python3 scripts/run_monday_dry_run.py --speed 10.0`.
   Verify clean execution, 0 unhandled exceptions, valid bracket fills, PnL tracking, and update/verify `MONDAY_SIMULATION_REPORT.md`.
2. Verify visual UI on mobile (390x844) and desktop (1440x900) viewports:
   - Run `npm --prefix frontend run build` (assert 0 errors).
   - Run `pytest tests/e2e/test_challenger_mobile.py -v`.
   - Inspect components (ActivePositionTray, Strategy Cards, manual controls, live chart) for zero truncation or horizontal overflow.
3. Clean up all processes, confirm ports 3005, 8005, and 8080 are released with `./scripts/verify_port_hygiene.sh`.

Write your report to `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_worker_qa_sim_visual_1/handoff.md` and send a message when complete.
