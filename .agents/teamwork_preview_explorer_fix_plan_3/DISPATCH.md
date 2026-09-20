# Task Dispatch: Explorer 3 — E2E Test Suite Full Pass Strategy & Telemetry Fix

## Mandatory Context
MANDATORY FIRST STEP: Read `/Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md` before starting work.
Full Forensic Auditor report: `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_auditor_forensics_1/handoff.md`
Challenger 1 report: `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_challenger_stress_1/handoff.md`

## Problem Statement
The Forensic Auditor requires that `scripts/run_e2e_tests.sh` pass with 100% (293/293 tests).
Challenger 1 also identified:
1. `StockWebSocketClient._process_queue_loop` (`backend/app/ingestion/stock_ws.py`): telemetry counters (`bars_received`, `quotes_received`, `trades_received`) were incremented before parsing and validation, leading to potential telemetry drift on malformed items.
2. `tests/e2e/runner.py` port management: ensure all test runs cleanly isolate ports and terminate any sub-processes.

## Objective
Analyze `scripts/run_e2e_tests.sh`, `tests/e2e/runner.py`, and `backend/app/ingestion/stock_ws.py`.
Recommend a concrete strategy to guarantee that:
1. All 293 tests pass cleanly under `scripts/run_e2e_tests.sh`.
2. Telemetry counter increments in `stock_ws.py` only occur after successful event publication.
3. Zero port leakage or process hanging occurs.

Write your report to:
`/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_3/strategy_report.md`
and `handoff.md`.

## 2026-09-20T13:36:08Z
You are Explorer 3 (E2E Test Runner & Telemetry Explorer) for AutonomousDayTrader.

Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_3
Project root: /Users/mo/AutonomousDayTrader

MANDATORY FIRST STEP: Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also read:
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_3/DISPATCH.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_auditor_forensics_1/handoff.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_challenger_stress_1/handoff.md

Task:
Analyze `scripts/run_e2e_tests.sh`, `tests/e2e/runner.py`, and `backend/app/ingestion/stock_ws.py` (telemetry counters incrementing before validation).
Formulate a strategy to guarantee that all 293 E2E tests pass, telemetry metrics are accurate, and ports are always freed.
Do NOT modify implementation code directly. Formulate recommendations with exact code snippets.

Write:
- `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_3/strategy_report.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_3/handoff.md`
Send a message when complete.
