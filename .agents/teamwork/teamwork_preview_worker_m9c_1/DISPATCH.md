# Dispatch: Worker M9C (Unified Obsidian Dark Operator Interface & WebSocket Integration)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9c_1`

## Authoritative Documents
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_3/handoff.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9b_1/handoff.md`

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Objective & Tasks
Implement Milestone M9C (`obsidian_dark_ui`):
1. **Backend UI WebSocket Integration (`backend/app/main.py`)**:
   - In `broadcast_ui_state`: Serialize `"swing": swing_engine.to_ui_dict()` into the broadcast payload.
   - In `ui_websocket_endpoint`: Handle incoming WebSocket action messages:
     - `SWING_EXIT_NEXT_OPEN`: Stages manual exit at 09:30 market open.
     - `SWING_EXIT_IMMEDIATE`: Executes immediate emergency market exit for symbol.
     - `SWING_TIGHTEN_STOP`: Adjusts stop price.
2. **Frontend Type Definitions (`frontend/types/trading.ts`)**:
   - Define TypeScript interfaces: `SwingCandidate`, `SwingPosition`, `SwingEngineState`.
   - Update `UIState` / `AppState` to include `swing?: SwingEngineState`.
3. **Frontend Next.js Components**:
   - Implement `frontend/components/SegmentedModeToggle.tsx`: Segmented toggle between "Intraday Day Trader" and "Swing Mean-Reversion" with Apple Obsidian glassmorphism and Framer Motion layoutId animation.
   - Implement `frontend/components/SwingTelemetryBar.tsx`: Strategy badge, slot utilization (e.g. `1 of 2 Slots Used ($25,000 / $50,000 Notional)`), overnight exemption badge (`OVERNIGHT EXEMPT (Multi-Day Hold)`).
   - Implement `frontend/components/SwingCandidateWatchlist.tsx`: Real-time table of the 5 certified stocks (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`): 200 SMA Floor, 60d RS vs QQQ, RSI(2) panic badge (< 10.0), 48h earnings blackout check, trigger state badge.
   - Implement `frontend/components/ActiveSwingPositionsTable.tsx`: Active swing positions table with entry/current price, unrealized PnL, 2.5x ATR stop line with distance % meter, visual holding day counter (`[● ● ○ ○ ○] Day 2 of 5`), armed exit triggers, and manual override controls.
   - Integrate into `frontend/app/page.tsx` with responsive layout for mobile (390x844) and desktop (1440x900).
4. **Verification**:
   - Run `npm --prefix frontend test` and `npm --prefix frontend run build` (must pass with 0 errors).
   - Verify `pytest backend/tests/ -q` (must pass 100%).
   - Verify zero port lingering.

## Output Requirements
Write your detailed report to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9c_1/handoff.md`.
Include: exact files touched, diffs summary, build and test commands run, test pass output, and verification results.
When done, send a message to the caller with your status and summary.
