# Dispatch Briefing: Worker 1 (Production Remediation & Hardening)

## Objective
Remediate all 10 verified defects (5 Critical, 5 Major) identified in the forensic audit across `AutonomousDayTrader` swing trading engine and intraday integration.

## Authoritative Reference
- ORIGINAL_REQUEST: `/Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md`
- PROJECT: `/Users/mo/AutonomousDayTrader/PROJECT.md`
- Audit Findings: `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_8/AUDIT_FINDINGS.md`
- Explorer Reports:
  - `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_1_audit/analysis.md`
  - `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_audit/analysis.md`
  - `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_3_audit/analysis.md`

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Owned Target Files (Exclusive Write Ownership)
- `backend/app/main.py`
- `backend/app/strategies/swing_panic_dip.py`
- `backend/app/strategies/earnings_calendar.py`
- `backend/app/strategies/swing_indicators.py`
- `backend/app/config.py`
- `backend/app/models/events.py`
- `backend/app/core/account.py`
- `backend/app/core/runtime_state.py`
- `backend/tests/` (unit and integration regression tests)

## Scope of Remediation Tasks

1. **Defect 1: 09:30 ET Open Window Tolerance & Marooning Prevention (`main.py`)**:
   - Broaden open window trigger from exact `minute == 30` to tolerance window `(hour == 9 and 30 <= minute <= 45)` or first regular trading hours bar on/after 09:30:00 ET.
   - Add clean expiration at 09:45:00 ET: cancel unexecuted staged orders and release their symbol reservations.

2. **Defect 2: Market-Open Race Condition & Deferred Entries (`swing_panic_dip.py`, `main.py`)**:
   - In `execute_market_open`: If `active_count >= max_concurrent_positions` and there are pending staged exits (`len(self.staged_manager.get_staged_exits()) > 0`), do NOT delete the staged entry order; defer it (`continue`).
   - In `main.py` (or within `execute_market_open`): Whenever a staged exit fills, immediately trigger execution for any deferred staged entries against the newly opened slot.

3. **Defect 3: Staged Order Idempotency & 2-Position Cap Enforcement (`swing_panic_dip.py`)**:
   - In `evaluate_market_close`: Deduct `len(self.staged_manager.get_staged_entries())` from `available_slots`.
   - Break if `available_slots <= 0`.
   - Prevent duplicate symbol staging across repeated scans, clock ticks, or post-restart close evaluations.

4. **Defect 4: Eliminate Blocking I/O (`earnings_calendar.py`, `config.py`)**:
   - Replace blocking `urllib.request.urlopen` in `refresh_from_remote()` with non-blocking async HTTP using `httpx.AsyncClient(timeout=3.0)` or `asyncio.to_thread`.
   - Ensure clean try/except logging and fallback to local cache.

5. **Defect 5: Cross-Arm Circuit Breaker Contamination (`main.py`)**:
   - In `main.py:880–887`, add check to skip swing positions:
     `if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip": continue`

6. **Defect 6: Realistic Slippage & Rule 6 Stop Anchoring (`swing_panic_dip.py`)**:
   - Calculate and pass realistic slippage to `_execute_fill` (using `ExecutionEngine.calculate_slippage` or basis-point model).
   - Anchor Rule 6 stop-loss strictly to `fill.fill_price - 2.5 * daily_atr`.

7. **Defect 7: PositionState Schema Fidelity (`events.py`, `account.py`)**:
   - Add `entry_atr: Optional[float] = None` and `entry_date: Optional[str] = None` to `PositionState`.
   - Map them in `Position.to_state()`.

8. **Defect 8: Durable Earnings Calendar Cache (`earnings_calendar.py`, `config.py`, `main.py`)**:
   - Implement atomic cache write in `EarningsCalendar` when remote updates arrive.
   - Add `EARNINGS_CALENDAR_REMOTE_URL` to `config.py` and pass to `EarningsCalendar` in `main.py`.

9. **Defect 9: DailyBarStore SQLite Checkpointing (`runtime_state.py`, `swing_indicators.py`)**:
   - Persist recent aggregated daily bars in `capture_runtime_state` and restore into `DailyBarStore` in `restore_runtime_state`.

10. **Test Coverage & Verification**:
    - Add comprehensive unit tests in `backend/tests/unit/` covering all 10 fixes.
    - Run `pytest backend/tests/` and verify that all 430+ tests pass with zero failures or regressions.

## Output Requirements
Document all changes and test outputs in:
`/Users/mo/AutonomousDayTrader/.agents/teamwork/worker_1_remediation/changes.md`
And summary handoff in:
`/Users/mo/AutonomousDayTrader/.agents/teamwork/worker_1_remediation/handoff.md`
Use `send_message` to communicate completion back to parent.
