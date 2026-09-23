# Dispatch: Remediation Worker (Adversarial & Forensic Audit Remediation)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_remediation_1`

## Authoritative Documents
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/GATE_STATUS.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_remediation_1/handoff.md` (BLUEPRINT)
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_auditor_1/handoff.md` (AUDIT REPORT)

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Objective & Implementation Tasks
Execute the exact 10-point remediation blueprint from `teamwork_preview_explorer_remediation_1/handoff.md`:
1. **Defect 1**: In `backend/app/strategies/swing_panic_dip.py` lines 823–826, fix attribute names in `to_ui_dict()` to match `SwingExitResult` (`exit_5_sma`, `exit_rsi2_overbought`, `exit_time_stop`, `exit_earnings`). Add backward-compatible property aliases on `SwingExitResult` in `swing_indicators.py`. In `backend/tests/test_swing_ui_api.py`, add test with active swing positions verifying `to_ui_dict()` and `broadcast_ui_state()` execute cleanly.
2. **Defect 2**: In `backend/app/main.py` lines 1254–1275 and `swing_panic_dip.py`, fix 09:30 open bar execution so staged orders execute when THAT symbol's confirmed 09:30 bar prints, using its actual opening price (`bar.open`), before evaluating intraday stop triggers.
3. **Defect 3**: In `backend/app/main.py` lines 128–141 and 250–262, harden AMD symbol mutual exclusion by checking both `account.positions` and `engine.working_orders` across intraday and swing arms.
4. **Defect 4**: In `backend/app/strategies/swing_panic_dip.py`, add a reentrant lock (`threading.RLock`) to `SwingStrategyEngine` and wrap `execute_market_open` to prevent concurrent duplicate executions.
5. **Defect 5**: In `backend/app/main.py` lines 935–940, guard session boundary holding days increment with `if session_date.weekday() < 5:` so weekend calendar days do not advance holding days.
6. **Defect 6**: In `backend/app/strategies/swing_panic_dip.py` line 499, initialize `pos.holding_days = 1` upon fill on Day 1, so 5 trading sessions reached at Friday 16:00 close triggers Rule 7c time stop.
7. **Defect 7**: In `backend/app/strategies/earnings_calendar.py` lines 177–192, fix blackout logic to ensure past morning BMO reports on Day T do not veto afternoon entries on Day T, while ensuring Friday entries blackout Monday earnings.
8. **Defect 8**: In `backend/app/strategies/swing_panic_dip.py` lines 264–286, exclude `active_positions` (including exiting symbols) from candidate entry screening at 16:00 close to prevent simultaneous exit/entry staging collisions.
9. **Defect 9**: In `backend/app/main.py` line 1142, pass `arm=TradingArm.INTRADAY` into `_get_effective_committed_portfolio` in `execute_strategy_signal` so swing holdings do not consume intraday capacity.
10. **Defect 10**: In `backend/app/core/runtime_state.py`, serialize and restore `swing_staged_orders` and `swing_reserved_symbols` in SQLite state checkpoints.

## Verification Tasks
- Run `pytest backend/tests/test_swing_ui_api.py -v` (confirm active position test passes).
- Run `pytest backend/tests/test_swing_strategy.py backend/tests/test_swing_indicators.py backend/tests/test_swing_flattening_exemption.py -v`.
- Run `pytest backend/tests/stress/test_challenger_concurrency_margin_races.py -v` (confirm all 4 challenger stress tests now pass 100%).
- Run `pytest backend/tests/test_adversarial_challenger_1.py -v` (confirm all 21 challenger tests pass).
- Run full backend test suite: `pytest backend/tests/ -q` (100% pass).
- Run E2E runner: `python3 tests/e2e/runner.py` (100% pass).
- Verify port hygiene: `bash scripts/verify_port_hygiene.sh` (ports 3005, 8000, 8005, 8080 clean).

## Output Requirements
Write your detailed implementation report to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_remediation_1/handoff.md`.
Conclude with exact commands run, test pass counts, and verification logs.
Send a message back to the caller when complete.

## 2026-09-23T22:21:11Z
User Request:
You are the Remediation Worker (Adversarial & Forensic Audit Remediation Worker).
Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_remediation_1.
You MUST read the authoritative user request at: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md.
Also read your full dispatch instructions at: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_remediation_1/DISPATCH.md, /Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md, and the Remediation Explorer's comprehensive blueprint at: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_remediation_1/handoff.md.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Execute the exact 10-point remediation blueprint:
1. Fix AttributeError in to_ui_dict() on active swing positions; add aliases on SwingExitResult; add active position test in test_swing_ui_api.py.
2. Fix 09:30 open bar arrival race condition in main.py.
3. Fix AMD symbol mutual exclusion on working orders in main.py.
4. Add threading.RLock() concurrency lock to execute_market_open.
5. Guard session boundary holding_days increment against non-trading weekend days.
6. Initialize holding_days = 1 on Day 1 upon fill.
7. Fix earnings calendar blackout for past morning BMO reports.
8. Exclude active_positions from entry candidate screening to prevent same-symbol exit/entry collisions.
9. Pass arm=TradingArm.INTRADAY in execute_strategy_signal to prevent intraday capacity starvation.
10. Persist staged orders and swing reservations in runtime_state.py SQLite checkpoints.

Verify that all test suites pass (including challenger stress tests and full backend tests).
Write your handoff report to /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_remediation_1/handoff.md following the Handoff Protocol.
When done, send a message to the caller with your status and test results.

