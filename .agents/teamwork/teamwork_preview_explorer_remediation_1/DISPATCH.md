# Dispatch: Remediation Explorer (Adversarial & Forensic Audit Remediation)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_remediation_1`

## Authoritative Documents
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/GATE_STATUS.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_auditor_1/handoff.md` (FULL EVIDENCE REPORT)
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_reviewer_adv_1/handoff.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_reviewer_adv_2/handoff.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_reviewer_adv_3/handoff.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_challenger_1/handoff.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_challenger_2/handoff.md`

## Forensic Audit Full Evidence & Remediation Mission
The Forensic Auditor reported INTEGRITY VIOLATION, and all 3 Reviewers and 2 Challengers reported REQUEST_CHANGES.
You MUST read the Forensic Auditor's FULL report at `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_auditor_1/handoff.md` and formulate concrete code-level fix specifications for all 10 findings:

1. **Defect 1 (CRITICAL - Output Verification / AttributeError)**:
   - In `backend/app/strategies/swing_panic_dip.py` lines 823–826, `to_ui_dict()` accesses `rule_7a_sma5_exit`, `rule_7b_rsi_exit`, `rule_7c_time_exit`, `rule_4_earnings_exit` which do not exist on `SwingExitResult`. Map to `exit_5_sma`, `exit_rsi2_overbought`, `exit_time_stop`, `exit_earnings`.
   - Specify unit test in `backend/tests/test_swing_ui_api.py` that populates `account.positions` with active swing positions and calls `to_ui_dict()` and `broadcast_ui_state()` to ensure it never crashes.
2. **Defect 2 (CRITICAL - 09:30 Open Bar Timing Race)**:
   - In `backend/app/main.py` lines 1260–1269, opening order execution is triggered by ANY 09:30 bar, falling back to yesterday's close for other symbols. Specify fix so that staged orders for a symbol execute strictly when THAT symbol's 09:30 bar prints, or when market open bar arrives.
3. **Defect 3 (CRITICAL - AMD Symbol Mutual Exclusion on Working Orders)**:
   - In `backend/app/main.py` lines 250–262, `pre_trade_risk_validator` only checks `account.positions`. Add check for `engine.working_orders` for AMD across both arms to prevent order collisions.
4. **Defect 4 (CRITICAL - Missing Concurrency Lock in `execute_market_open`)**:
   - In `backend/app/strategies/swing_panic_dip.py`, add execution guard/in-flight flag to prevent concurrent duplicate executions of staged orders.
5. **Defect 5 (MAJOR - Weekend Session Boundary Rollover)**:
   - In `backend/app/main.py` lines 863–940, `_check_session_boundary` increments `pos.holding_days` on non-trading weekend days (Saturday and Sunday). Fix to only increment on trading days (`weekday < 5`).
6. **Defect 6 (MAJOR - Holding Days Off-by-One Time Stop)**:
   - In `backend/app/strategies/swing_panic_dip.py` line 499, initialize `pos.holding_days = 1` upon fill on Day 1, so that Friday close after 5 full trading sessions has `holding_days = 5` and triggers Rule 7c time stop.
7. **Defect 7 (MAJOR - False Earnings Blackout on BMO Morning Reports)**:
   - In `backend/app/strategies/earnings_calendar.py` lines 180–183, fix blackout logic so that past morning earnings on Day T do not veto afternoon entries on Day T.
8. **Defect 8 (MAJOR - Simultaneous Exit/Entry Staging Collision)**:
   - In `backend/app/strategies/swing_panic_dip.py` lines 264–286, exclude `exiting_symbols` from candidate entry screening at 16:00 close.
9. **Defect 9 (MAJOR - Intraday Concurrency Capacity Starvation)**:
   - In `backend/app/main.py` line 1142, pass `arm=TradingArm.INTRADAY` into `_get_effective_committed_portfolio` in `execute_strategy_signal`.
10. **Defect 10 (MAJOR - Staged Orders & Symbol Reservation Persistence)**:
    - In `backend/app/core/runtime_state.py`, include staged swing orders and swing reserved symbols in runtime state capture and restore.

## Output Requirements
Write your comprehensive remediation blueprint to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_remediation_1/handoff.md`.
When done, send a message back to the caller with a summary.

## 2026-09-23T22:16:28Z
You are the Remediation Explorer (Adversarial & Forensic Audit Remediation Explorer).
Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_remediation_1.
You MUST read the authoritative user request at: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md.
Also read your full dispatch instructions at: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_remediation_1/DISPATCH.md, /Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/GATE_STATUS.md, and the Forensic Auditor's FULL report at: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_auditor_1/handoff.md.

Formulate an exact, file-by-file, function-by-function remediation plan addressing all 10 identified defects:
1. AttributeError in to_ui_dict() on active swing positions
2. 09:30 open bar arrival race condition
3. AMD symbol mutual exclusion on working orders
4. execute_market_open concurrency lock
5. Weekend session boundary holding days rollover
6. Holding days lifecycle off-by-one
7. False blackout on BMO morning earnings reports
8. Simultaneous exit/entry staging collision on the same symbol
9. Intraday capacity starvation in execute_strategy_signal
10. Staged orders and symbol reservation persistence in runtime_state.py

Write your detailed remediation plan to /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_remediation_1/handoff.md following the Handoff Protocol.
When done, send a message to the caller with your summary.

