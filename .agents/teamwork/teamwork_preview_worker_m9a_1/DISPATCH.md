# Dispatch: Worker M9A (Core Swing Models, Flattening Exemption & Account Margin Coordination)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9a_1`

## Authoritative Documents
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_1/handoff.md`

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Objective & Tasks
Implement Milestone M9A (`core_flattening_exemption`):
1. **Define `TradingArm` Enum & Tagging**:
   - In `backend/app/core/account.py`: Add `TradingArm(str, Enum)` with values `INTRADAY = "INTRADAY"`, `SWING = "SWING"`.
   - Add `arm: TradingArm = TradingArm.INTRADAY` and `holding_days: int = 0` to `Position`.
   - In `backend/app/core/engine.py`: Add `arm: TradingArm = TradingArm.INTRADAY` to `Order`.
   - In `backend/app/core/bracket.py`: Add `arm: TradingArm = TradingArm.INTRADAY` to `BracketOrder`.
   - Ensure backward-compatible serialization with `persistence.py` and `runtime_state.py`.
2. **4-Phase EOD Auto-Flattening Exemption**:
   - In `backend/app/core/flattening.py`:
     - Update `execute_phase_4_audit` to only check for unclosed `INTRADAY` positions and `INTRADAY` working orders. Swing positions and swing working orders must not fail the zero-audit.
   - In `backend/app/main.py`:
     - In `handle_flattening_directive`:
       - `directive.cancel_all_orders`: Cancel only working orders where `order.arm == TradingArm.INTRADAY`.
       - `directive.liquidate_all_positions`: Liquidate only positions where `pos.arm == TradingArm.INTRADAY`.
3. **Hidden 5th Phase: Session Boundary Exemption**:
   - In `backend/app/main.py` (`_check_session_boundary`):
     - `engine.cancel_all_orders("SESSION_BOUNDARY_PURGE")`: Only cancel `INTRADAY` orders. Keep `SWING` orders intact.
     - `if account.positions:`: Only liquidate positions where `pos.arm == TradingArm.INTRADAY`. Do NOT liquidate swing positions! Swing positions must be preserved overnight across calendar days.
     - Do not purge swing brackets or swing staged orders.
4. **Risk Engine Arm-Aware Routing & Margin Coordination**:
   - In `backend/app/core/risk.py`:
     - Add `max_concurrent_swing_positions: int = 2` and `swing_slot_notional: float = 25000.0`.
     - In `evaluate_order_request`:
       - If `order.strategy_id == "swing_panic_dip"` or `order.arm == TradingArm.SWING`:
         - Bypass intraday 4.0% max stop ceiling (`STOP_DISTANCE_TOO_WIDE`). Swing stops are `2.5 * ATR(14)` (typically 4.5%–12%). Only require stop price < entry price for longs.
         - Enforce swing concurrency cap: max 2 active swing positions.
         - Enforce swing notional cap: $25,000 per slot.
         - Bypass intraday 3-position concurrency cap (swing positions do not count against the 3 intraday position limit).
   - In `backend/app/main.py` (`pre_trade_risk_validator`):
     - Route swing orders with arm-aware risk evaluation.
5. **Symbol Mutual Exclusion (Symbol Reservation for `AMD`)**:
   - `AMD` belongs to both intraday watchlist and swing universe.
   - If `AMD` has an active swing position or staged swing order, lock out intraday strategies from opening new positions in `AMD`.
6. **Unit Tests & Verification**:
   - Write comprehensive pytest tests in `backend/tests/test_swing_flattening_exemption.py`:
     - Test EOD flattening Phase 1 to Phase 4 leaves swing positions and swing protective stops 100% intact.
     - Test session boundary rollover preserves swing positions and does not liquidate them.
     - Test swing 2.5x ATR stops (> 4.0%) are accepted while intraday stops > 4.0% are still rejected.
     - Test max 2 concurrent swing positions enforced.
     - Test `AMD` symbol reservation locks out intraday entry.
     - Run `pytest backend/tests -q` to guarantee 100% pass rate and zero regressions.

## Output Requirements
Write your detailed implementation report to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9a_1/handoff.md`.
Include: exact files touched, diffs summary, build and test commands run, test pass output, and verification results.
When done, send a message back to the caller with your status and summary.

## 2026-09-23T21:33:04Z
You are Worker M9A (Backend Core Worker).
Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9a_1.
You MUST read the authoritative user request at: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md.
Also read your full dispatch instructions at: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9a_1/DISPATCH.md, /Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md, and /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_1/handoff.md.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Your objective is to implement Milestone M9A:
1. Define TradingArm enum and tag Position, Order, BracketOrder.
2. Implement 4-phase EOD auto-flattening exemption in flattening.py and main.py.
3. Implement session boundary rollover exemption in main.py.
4. Implement arm-aware risk evaluation in risk.py and main.py (bypass 4.0% stop ceiling for swing ATR stops, max 2 concurrent swing positions, $25k notional cap).
5. Implement symbol reservation for AMD to prevent intraday collisions.
6. Write backend/tests/test_swing_flattening_exemption.py and run all pytest tests to ensure 100% pass rate.

Write your handoff report to /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9a_1/handoff.md following the Handoff Protocol.
When done, send a message to the caller with your summary and test results.

