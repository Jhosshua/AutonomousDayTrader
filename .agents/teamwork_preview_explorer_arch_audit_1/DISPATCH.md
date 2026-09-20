# Task Dispatch: Backend Architectural Audit

## Objective
Conduct a thorough, exhaustive architectural audit across the entire AutonomousDayTrader backend codebase:
- Ingestion adapters (`src/adapters/` - stock ws, news ws, vix client)
- Risk engine (`src/risk/` - daily loss limit, position limits, sizing multiplier)
- Order execution and bracket lifecycle (`src/execution/` - order routing, stop-loss, take-profit 1 & 2 scale-out, trailing stop, orphan bracket prevention, duplicate signal rejection)
- State machines and portfolio accounting (`src/portfolio/`, `src/state/` - cash, equity, buying power, PnL tracking, ET date change session reset)
- 4 trading strategies (`src/strategies/` - ORB, VWAP Pullback, News Momentum, Mean Reversion)
- Dynamic adaptation engine (`src/adaptation/` - VIX regime scaling 15/25/35, time-of-day session phases)
- UI WebSocket server and streaming (`src/streaming/`, `src/server/`)
- End-of-Day auto-flattening engine (15:45 lockout, 15:50 order purge, 15:55 market liquidation, 15:58 flat audit)

## Instructions
1. Inspect the codebase thoroughly for:
   - Missed connections or wiring gaps between modules
   - Unhandled edge cases, exceptions, or race conditions in async loops
   - Inverted risk or boundary conditions
   - Orphaned bracket orders or leaked state
   - Dead code paths or inconsistent configuration handling
   - Inconsistencies between `PROJECT.md`, `MEMORY.md`, and actual implementation
2. Read `/Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md`, `/Users/mo/AutonomousDayTrader/PROJECT.md`, and `/Users/mo/AutonomousDayTrader/MEMORY.md`.
3. Produce a structured, actionable report detailing:
   - Identified bugs/issues (Severity: Critical, Major, Minor) with precise file, line numbers, and root cause
   - Concrete fix proposals for each issue
   - Confirmation of what is already working well
4. Write your report to `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_arch_audit_1/audit_report.md` and write a standard `handoff.md`.
5. Send a completion message back when done.

## 2026-09-20T13:16:14Z
You are the Backend Architectural Auditor for AutonomousDayTrader.

Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_arch_audit_1
Project root: /Users/mo/AutonomousDayTrader

MANDATORY FIRST STEP: Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also read:
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_arch_audit_1/DISPATCH.md
- /Users/mo/AutonomousDayTrader/PROJECT.md
- /Users/mo/AutonomousDayTrader/MEMORY.md

Task:
Conduct a rigorous audit across the entire backend codebase:
- Ingestion adapters (`src/adapters/`: stock_ws, news_ws, vix_client)
- Risk engine (`src/risk/`: risk.py, limits, drawdown, sizing)
- Execution and order lifecycle (`src/execution/`: engine.py, brackets, order state machine, stops, take profits, duplicate rejection)
- Portfolio state and accounting (`src/portfolio/`: account, positions, session reset, PnL)
- 4 Trading strategies (`src/strategies/`: orb.py, vwap_pullback.py, news_momentum.py, mean_reversion.py)
- Adaptation & Regimes (`src/adaptation/`: regime.py, session phases)
- Streaming & WebSockets (`src/streaming/`, `src/server/`)
- End-of-Day auto-flattening engine

Inspect thoroughly for:
1. Missed connections or unhandled wiring
2. Edge cases, race conditions, or unhandled exceptions
3. Inverted risk boundaries or sizing discrepancies
4. Orphaned bracket orders or leaked state
5. Dead code paths or inconsistent configuration handling
6. State machine gaps across session dates

Produce:
- `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_arch_audit_1/audit_report.md` (detailed findings with file, line numbers, root cause, and concrete fix proposals)
- `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_arch_audit_1/handoff.md`

Send a message back when completed.
