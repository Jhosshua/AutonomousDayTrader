# Dispatch Briefing: Worker 3 (Exhaustive Multi-Day End-to-End Dry Run)

## Objective
Build and execute a comprehensive, full-scale multi-day end-to-end dry run simulating consecutive trading days with both the Intraday Day Trading arm (ORB, VWAP, News, Mean Reversion across 12 tickers) and the Swing Trading arm (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`) executing simultaneously from the shared $50,000 account pool.

## Authoritative Reference
- ORIGINAL_REQUEST: `/Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md` (R3, Acceptance Criteria)
- PROJECT: `/Users/mo/AutonomousDayTrader/PROJECT.md`
- Working Directory: `/Users/mo/AutonomousDayTrader/.agents/teamwork/worker_3_multiday_simulation`
- Output Report: `/Users/mo/AutonomousDayTrader/SWING_FULL_E2E_DRY_RUN_REPORT.md` (at project root)

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations and simulations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or bypass event paths. Realistic market-replay and order execution must be simulated.

## Scope & Simulation Architecture
1. **Simulation Harness**:
   - Create an executable script `scripts/run_concurrent_multiday_e2e_dry_run.py` (or execute via pytest).
   - Simulate at least 5 consecutive trading days (e.g. Day 1 Monday through Day 5 Friday).
   - Ingest real or synthetic minute bars through `main.handle_bar_event()`, `handle_quote_event()`, and news events.
   - Run all 4 Intraday strategies concurrently across the 12-ticker watchlist: `["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]`.
   - Run the Swing Trading arm concurrently across `["LRCX", "KLAC", "MU", "AMD", "GS"]`.
2. **Key Verifications Across Sessions**:
   - **Shared Capital Management**: Account starts at $50,000. Cash, equity, and buying power are tracked without double-spending or margin overdrafts.
   - **16:00 ET Close Qualification**: Swing signals evaluated, staged in `SwingStagedOrderManager` with 2-slot cap enforced.
   - **09:30 ET Open Fills**: Staged swing orders execute with realistic slippage; Rule 6 ATR stops establish immediately at `fill.price - 2.5 * ATR`.
   - **15:45–15:58 ET Intraday Flattening Exemption**: Verify that by 15:58 ET, ALL intraday positions are 100% flattened (zero overnight intraday holds), while active swing positions and stops survive completely untouched.
   - **Swing Exit Triggers**: Exercise and verify all 3 exit triggers:
     - Rule 7a: Prior close > 5-day SMA
     - Rule 7b: Prior RSI(2) > 70.0
     - Rule 7c: 5-day time stop (`holding_days >= 5`)
   - **Mutual Exclusion for `AMD`**: Verify that if `AMD` is held or staged by swing, intraday trades on `AMD` are strictly denied, and vice-versa.
3. **Report Generation**:
   - Generate `/Users/mo/AutonomousDayTrader/SWING_FULL_E2E_DRY_RUN_REPORT.md` logging:
     - Multi-day session timeline and daily summaries
     - Full transaction ledger (timestamps, arm, symbol, side, qty, fill price, slippage, PnL)
     - Daily beginning/ending equity, cash, and drawdown curves
     - Explicit verification table confirming 0 overnight intraday positions and 0 liquidated swing positions
     - Confirmation of all 3 swing exit triggers and mutual exclusion
4. **Port & Process Hygiene**:
   - Terminate all test servers and verify ports 3005, 8000, 8005, 8080 are clean and free.

## Output Requirements
Write your simulation code, test execution logs, and handoff report to:
`/Users/mo/AutonomousDayTrader/.agents/teamwork/worker_3_multiday_simulation/handoff.md`
And the master report to:
`/Users/mo/AutonomousDayTrader/SWING_FULL_E2E_DRY_RUN_REPORT.md`
Use `send_message` to communicate completion back to parent.

## 2026-09-24T00:57:11Z
You are Worker 3 (teamwork_preview_worker).
Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_3_multiday_simulation
Your identity: Multi-Day Concurrent Simulation Engineer.

Read your dispatch instructions in:
/Users/mo/AutonomousDayTrader/.agents/teamwork/worker_3_multiday_simulation/DISPATCH.md
Read the authoritative user request at:
/Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md
Also refer to:
/Users/mo/AutonomousDayTrader/PROJECT.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations and simulations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Your mission:
Build and run an exhaustive multi-day end-to-end dry run (5+ consecutive trading sessions) simulating both the Intraday Day Trading arm (4 strategies across 12 tickers) and the Swing Trading arm (5 tickers) executing simultaneously from the shared $50,000 account pool.
Verify:
1. Shared account balance, cash, buying power, and PnL.
2. 0 intraday positions held overnight; 0 swing positions liquidated by 15:58 ET flattening.
3. Realistic slippage and Rule 6 stop-loss anchoring on all swing fills.
4. All 3 Rule 7 swing exit triggers (5 SMA, RSI > 70, 5-day time stop) and mutual exclusion for AMD.
5. Generate the authoritative report: /Users/mo/AutonomousDayTrader/SWING_FULL_E2E_DRY_RUN_REPORT.md.
6. Verify clean port hygiene (ports 3005, 8000, 8005, 8080 free).
Write your handoff report to /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_3_multiday_simulation/handoff.md.
Use send_message to report completion back to parent (ID: b067f9cf-98b6-4f32-8f6e-4a86f7057623).
