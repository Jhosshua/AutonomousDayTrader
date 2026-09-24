# Dispatch Briefing: Worker 2 Iteration 2 (Audit Remediation & Hardening)

## Objective
Implement the 3 verified remediation fixes identified in Milestone 2 Gate 1 to resolve the Forensic Auditor veto, Reviewer 1 finding, and Challenger 2 finding.

## Authoritative Reference
- ORIGINAL_REQUEST: `/Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md`
- PROJECT: `/Users/mo/AutonomousDayTrader/PROJECT.md`
- Forensic Auditor Report: `/Users/mo/AutonomousDayTrader/.agents/teamwork/auditor_1/audit_report.md`
- Explorer 1 R2 Handoff & Patch: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_1_r2/handoff.md`, `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_1_r2/fix_test_swing_multiday_replay.patch`
- Explorer 2 R2 Handoff: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_r2/handoff.md`
- Explorer 3 R2 Handoff & Patch: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_3_r2/handoff.md`, `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_3_r2/cross_arm_exclusion.patch`

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Owned Target Files (Exclusive Write Ownership)
- `tests/e2e/test_swing_multiday_replay.py`
- `backend/app/main.py`
- `backend/tests/unit/test_swing_forensic_remediation.py`
- `backend/tests/stress/test_cross_arm_isolation_persistence.py`

## Remediation Tasks

1. **Fix 1: E2E Test Stop Loss Assertion (`tests/e2e/test_swing_multiday_replay.py`)**:
   - Apply Explorer 1 R2's fix on lines 223–224:
     `expected_stop = round(lrcx_pos.avg_entry_price - 2.5 * daily_atr, 2)`
     `assert lrcx_pos.stop_loss_price == expected_stop`
   - Run `python3 tests/e2e/runner.py` and verify all 325 tests pass (100%).

2. **Fix 2: Market Open Stale Price Elimination (`backend/app/main.py`)**:
   - Implement Explorer 2 R2's recommendation:
     - Add `today_open_prices: Dict[str, float] = {}` tracking confirmed opening bar prices during the 09:30–09:45 open window.
     - Clear `today_open_prices` and `latest_market_prices` at session boundaries (`_check_session_boundary`) and on test resets (`reset_runtime_state`).
     - Staged entry orders execute only when their own today's open price has been confirmed.
   - Add regression test `test_defect_11_market_open_stale_price_prevention` to `backend/tests/unit/test_swing_forensic_remediation.py`.

3. **Fix 3: Cross-Arm Mutual Exclusion Bypass on Short Entries (`backend/app/main.py`)**:
   - Apply Explorer 3 R2's fix on lines 251–255:
     Ensure `is_exit = True` strictly requires `existing_is_swing == is_swing` (same trading arm).
     If `existing_pos.arm == TradingArm.SWING` and `order.arm == TradingArm.INTRADAY`, the order is NOT an exit; it is treated as an entry and blocked with `SYMBOL_RESERVED_FOR_SWING`.
   - Run `pytest backend/tests/stress/test_cross_arm_isolation_persistence.py` and verify 12/12 pass (100%).

4. **Full Test & Hygiene Verification**:
   - Run full backend suite: `pytest backend/tests` (verify 440+ tests pass).
   - Run full E2E runner: `python3 tests/e2e/runner.py` (verify 325/325 pass).
   - Run multi-day swing dry run: `python3 scripts/run_integrated_swing_dry_run.py` (verify 6/6 pass).
   - Verify port hygiene: confirm ports 8000, 8005, 8080, 3005 are clean.

## Output Requirements
Document all changes and test outputs in:
`/Users/mo/AutonomousDayTrader/.agents/teamwork/worker_2_remediation/changes.md`
And summary handoff in:
`/Users/mo/AutonomousDayTrader/.agents/teamwork/worker_2_remediation/handoff.md`
Use `send_message` to report completion.

## 2026-09-24T00:40:34Z
User request:
Apply the 3 verified fixes:
1. Fix test assertion in tests/e2e/test_swing_multiday_replay.py:223-224 (anchor expected stop to avg_entry_price) and verify python3 tests/e2e/runner.py passes 325/325.
2. Fix backend/app/main.py:1334-1341 to eliminate stale price fallback at market open using today_open_prices, and add unit regression test in backend/tests/unit/test_swing_forensic_remediation.py.
3. Fix backend/app/main.py:251-255 to require existing_pos.arm == order_arm for is_exit=True, sealing the cross-arm mutual exclusion bypass, and verify backend/tests/stress/test_cross_arm_isolation_persistence.py passes 12/12.
Run the complete backend test suite (pytest backend/tests), E2E runner (python3 tests/e2e/runner.py), and swing dry run (python3 scripts/run_integrated_swing_dry_run.py).
Verify clean port hygiene.
Write documentation to /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_2_remediation/changes.md and /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_2_remediation/handoff.md.
Use send_message to report completion back to parent (ID: b067f9cf-98b6-4f32-8f6e-4a86f7057623).

