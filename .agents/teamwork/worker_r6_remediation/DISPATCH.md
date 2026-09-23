# Dispatch: Worker R6 Remediation (Systematic Remediation & Mutation Testing)

## Identity
- Role: Worker (Implementation, Bug Fixes & Mutation Testing)
- Working Directory: /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_r6_remediation
- Project Root: /Users/mo/AutonomousDayTrader
- Authoritative User Request: /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md
- Scope Document: /Users/mo/AutonomousDayTrader/PROJECT.md

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Objective
Implement production-grade fixes for all confirmed defects across the 3 explorer reports, write comprehensive deterministic mutation tests verifying each fix, verify 100% test pass on backend unit tests and E2E runner, verify clean dry run, and ensure clean port hygiene.

## Inputs
- Explorer 1 Report: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_1_concurrency_memory/analysis.md`
- Explorer 2 Report: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_2_indicators_causality/analysis.md`
- Explorer 3 Report: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_3_risk_api_ui/analysis.md`

## Specific Remediation Tasks
1. **Risk Engine & Concurrency Limit Breach (CRITICAL)**:
   - In `backend/engine/risk.py` and `backend/engine/main.py`: Include `engine.working_orders` / `PENDING_ENTRY` brackets in `active_symbols` and sector allocations when validating pre-trade risk and arbitrating signals. Prevent simultaneous signal collisions across the 12 symbols from exceeding max 3 total positions or max 2 per sector.
   - In `backend/engine/risk.py`: In `evaluate_order_request`, verify cumulative drawdown against `$1,500` daily breaker and budget remaining loss capacity against order risk. Also net existing position and working order notional against the `$25,000` single-position cap.
2. **EOD 4-Phase Auto-Flattening Hygiene (CRITICAL)**:
   - In `backend/engine/main.py`: In Phase 2 (15:50 ET), only cancel unfilled entry orders / working buy limits; DO NOT cancel protective stop-loss/take-profit brackets for existing open positions until Phase 3 (15:55 ET) executes market liquidation.
3. **Mid-Minute News Catalyst Causality (CRITICAL)**:
   - In `backend/strategies/news_momentum.py`: Fix catalyst pruning around lines 215-219 so news arriving mid-minute ($c.ts > bar.ts$) is not purged as "future data" before reaction bars can process it. Preserve catalysts within the active TTL window (`0 <= (now - c.ts) <= catalyst_ttl_seconds`).
   - Capping: Only track catalyst queues for universe symbols, preventing memory leaks for non-watchlist tickers.
4. **Microsecond Skew Tolerance & Session Monotonicity (MAJOR)**:
   - In `backend/strategies/market_filter.py`: Replace zero-tolerance `elapsed < 0` with microsecond epsilon tolerance (e.g. `-1.0 <= elapsed <= 300.0` or `-0.5s`) so single-stock bars arriving microseconds before/after index bars are not falsely rejected.
   - In `backend/engine/main.py`: Guard session reset with monotonic timestamp progression ($t_k \ge t_{prev}$) to prevent out-of-order packets from triggering inadvertent backward session resets.
5. **Volume Baseline Dilution & Pre-Market Hygiene (MAJOR)**:
   - In `backend/strategies/vwap_pullback.py`: Exclude candidate bar from its own 10-period volume SMA baseline (`bars[:-1][-10:]`).
   - In `backend/strategies/orb.py`: Ensure pre-market bars do not contaminate regular-hours rolling volume/ATR baselines.
6. **Stock WS Priority Queue / Selective Shedding (CRITICAL/MAJOR)**:
   - In `backend/ingestion/stock_ws.py` / `stock_stream.py`: Under quote flood across 12 tickers, prioritize 1-min bars (`b`) and trades (`t`), selectively shedding quotes (`q`) rather than dropping bars indiscriminately via FIFO drops.
7. **SQLite WAL Checkpointing & Lifecycle Cleanup (MAJOR/MINOR)**:
   - In `backend/persistence/ledger.py` / `persistence.py`: Add periodic WAL checkpointing (`PRAGMA wal_checkpoint(PASSIVE)` or `TRUNCATE`).
   - In `backend/engine/event_bus.py`: Prevent duplicate registrations and ensure `event_bus.clear()` on shutdown.
8. **Manual Tighten Stop Distance Bounds (MAJOR)**:
   - In `backend/engine/execution.py` / `bracket_manager.py`: Enforce the 40 bps minimum stop distance check `[0.0040, 0.0400]` on manual tighten stop requests.
9. **WebSocket Serialization NaN/Infinity Safety (MAJOR/MINOR)**:
   - In `backend/api/serializer.py` / `ws.py` / `server.py`: Sanitize floats to replace `NaN`, `Infinity`, `-Infinity` with `0.0` or `None` prior to `json.dumps`.
10. **Frontend Null Safety & Responsiveness (MINOR)**:
    - Fix any potential NaN/null rendering crashes in Header/Page and Drawer.

## Mutation Testing Requirement
Implement deterministic mutation tests in `backend/tests/stress/test_challenger_r6_remediation.py` testing each fix against reintroduction of the bug. Verify that all mutation tests pass on remediated code and would fail if mutants were active.

## Verification Requirements
- `pytest backend/tests -q` (100% pass)
- `python3 tests/e2e/runner.py` (100% pass)
- `python scripts/run_integrated_monday_dry_run.py` (pass, 0 errors, flat book)
- Port hygiene: clean ports 8000, 8005, 8080, 3005 with 0 lingering processes.

## Output
Deliver `handoff.md` with:
- Detailed list of modified files and applied changes
- Test commands run and exact outputs
- Verification that all risk invariants ($1,500 daily breaker, $25,000 position cap, 4-phase EOD auto-flattening) are preserved
- Port hygiene verification

## 2026-09-23T20:18:15Z
You are Worker R6 Remediation.
Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_r6_remediation
Read your dispatch file at: /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_r6_remediation/DISPATCH.md
Read the authoritative user request at: /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md
Read the project document at: /Users/mo/AutonomousDayTrader/PROJECT.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Your task is to implement production-grade fixes for all confirmed defects identified by Explorers R6-1, R6-2, and R6-3, implement deterministic mutation tests in backend/tests/stress/test_challenger_r6_remediation.py, run full backend test suite, run E2E runner, run Monday dry run, verify port hygiene, and document all changes in handoff.md.

Check all inputs:
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_1_concurrency_memory/analysis.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_2_indicators_causality/analysis.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_3_risk_api_ui/analysis.md

Deliver handoff.md in your working directory and notify the parent orchestrator via send_message when complete.

