# Dispatch: Explorer 3 (Frontend Obsidian Dark UI & E2E Replay Testing)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_3`

## Authoritative User Request
Read `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md` (and `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`).

## Mission & Scope
Investigate the frontend UI and the E2E test/replay infrastructure:
1. Analyze `frontend/` (Next.js 15, React 19, Tailwind CSS, Framer Motion):
   - Current layout, navigation, and WebSocket state consumption (`frontend/src/`).
   - How WebSocket broadcasts from backend (`Port 8005`) are serialized and updated in UI.
   - Requirements for R4: Unified Obsidian Dark Operator Interface:
     a) Segmented toggle between "Intraday Day Trader" and "Swing Mean-Reversion".
     b) Candidate watchlist status (200 SMA, 60d RS, RSI(2), earnings check, trigger status).
     c) Active swing positions table (entry price, current price, unrealized PnL, 2.5x ATR stop line, holding day counter "Day X of 5", exit trigger conditions).
     d) Operator manual override controls (manual position exit at next open or emergency market exit).
     e) Mobile (390x844) and desktop (1440x900) layout compatibility.
2. Analyze E2E Test & Replay Infrastructure (`tests/`, `scripts/`):
   - How `tests/e2e/runner.py`, `scripts/run_e2e_tests.sh`, and `scripts/run_integrated_monday_dry_run.py` are structured.
   - How to build a deterministic multi-day historical replay test covering:
     - 16:00 ET qualification, 09:30 ET entries.
     - 2.5x ATR emergency stop execution.
     - 5-day SMA exit, RSI(2) > 70 exit, 5-day time stop exit.
     - Earnings veto (entry block & holding exit).
     - Exemption from 15:58 ET intraday auto-flattening.
3. Process hygiene and Railway deployment setup (`railway.json`, `Dockerfile`, procfile, etc.).
4. Document specific file paths, class names, component structures, and verification commands.
5. Provide concrete architectural recommendations for implementing R4 & R6 from the dispatch.

## Output Requirements
Write your detailed findings and architectural analysis to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_3/handoff.md`.
Follow the Handoff Protocol: Observation, Logic Chain, Caveats, Conclusion, Verification Method.
When done, send a message back to the caller with a summary and link to your handoff.md.
