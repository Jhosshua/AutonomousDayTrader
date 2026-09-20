## 2026-09-19T23:57:21Z
You are worker_m1_remediate, the implementation worker tasked with executing the Milestone 1 remediation plan for AutonomousDayTrader.
Your identity: worker_m1_remediate
Your working directory: /Users/mo/AutonomousDayTrader/.agents/worker_m1_remediate
You report to: parent orchestrator (conversation ID: f9df3e28-501d-4830-bf1f-140b6216f49e)

Mandatory inputs:
- Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md
- Read /Users/mo/AutonomousDayTrader/PROJECT.md
- Read /Users/mo/AutonomousDayTrader/.agents/explorer_m1_fix/remediation_plan.md
- Read /Users/mo/AutonomousDayTrader/.agents/challenger_m1_1/handoff.md
- Read /Users/mo/AutonomousDayTrader/.agents/challenger_m1_2/handoff.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

File write ownership:
- backend/app/core/account.py
- backend/app/core/risk.py
- backend/app/main.py
- backend/tests/stress/test_m1_empirical_stress.py
- backend/tests/unit/test_empirical_stress_m1.py

Objective:
Apply the exact line-by-line modifications specified in /Users/mo/AutonomousDayTrader/.agents/explorer_m1_fix/remediation_plan.md:
1. Liquidation Order Pass-Through:
   - In backend/app/core/account.py: update can_afford() so position-reducing / closing orders are permitted even under CIRCUIT_HALTED.
   - In backend/app/core/risk.py: update evaluate_order_request() with is_exit flag to bypass CIRCUIT_BREAKER_HALTED and ENTRY_LOCKOUT_ACTIVE for closing orders.
   - In backend/app/main.py: update pre_trade_risk_validator() to determine if an order is closing/reducing a position and pass is_exit=True.
2. Position-Flip DTBP & Concentration Checks:
   - In backend/app/core/account.py: update can_afford() when an order opposes an existing position (e.g. SELL on a LONG), if order_qty > pos.shares, compute delta_q_flip = order_qty - pos.shares and validate against the $50k concentration limit and 4:1 DTBP requirements.
3. Short Opening Fee Accounting:
   - In backend/app/core/account.py: in apply_fill(), on short cover (BUY order), deduct the prorated entry fee from realized_delta so realized_pnl accounts for the opening fee and equity identity (E = E0 + rPnL + uPnL) is strictly preserved.
4. Circuit Breaker Premature Rounding:
   - In backend/app/core/risk.py: in evaluate_account_state(), replace 4-decimal round-up with exact dollar comparison dd_dollars >= 1500.00.
5. Phase 4 Emergency Sweep Dispatch:
   - In backend/app/main.py: in handle_flattening_directive(), capture execute_phase_4_audit() directive and dispatch emergency sweep market orders if positions linger at 15:58 ET.
6. Verification & Process Hygiene:
   - Run: pytest backend/tests/unit/ -v
   - Run: pytest backend/tests/stress/test_m1_empirical_stress.py -v
   - Run: pytest backend/tests/unit/test_empirical_stress_m1.py -v
   - Run: python3 tests/e2e/runner.py
   - Ensure 100% passing tests and verify all ports (8005, 8080, 3005) are clean and free!

Deliverables:
- Write handoff report to /Users/mo/AutonomousDayTrader/.agents/worker_m1_remediate/handoff.md.
- Send completion message to parent orchestrator.
