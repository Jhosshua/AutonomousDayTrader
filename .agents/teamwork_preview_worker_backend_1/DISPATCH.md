# Task Dispatch: Backend Architectural Remediation

## Objective
Remediate all 10 architectural defects identified in the Backend Architectural Audit (`/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_arch_audit_1/audit_report.md`) while ensuring 100% backend test pass rate.

## Exclusive Write Ownership
You exclusively own and may edit only the following files:
- `backend/app/core/bracket.py`
- `backend/app/core/risk.py`
- `backend/app/core/flattening.py`
- `backend/app/strategies/orb.py`
- `backend/app/strategies/news_momentum.py`
- `backend/app/ingestion/stock_ws.py`
- `backend/app/ingestion/sentiment.py`
- `backend/app/main.py`
- `backend/app/config.py`
- `backend/tests/` (unit tests)

DO NOT modify frontend files or e2e test files (another worker owns those).

## Tasks to Implement
1. [CRITICAL] `backend/app/core/bracket.py`: Target 2 partial fill must NOT mark the bracket completed or cancel stop loss if `bracket.remaining_qty > 0`. Resize the working stop order to `bracket.remaining_qty`.
2. [MAJOR] `backend/app/core/bracket.py`: Prune child orders from `order_to_bracket` upon bracket completion and EOD flattening (`cancel_bracket_for_flattening`). Add an early guard in `on_child_order_fill` for already-completed/cancelled brackets.
3. [MAJOR] `backend/app/core/bracket.py`: In `manual_tighten_stop`, guard for `bracket.status in (BracketStatus.ACTIVE, BracketStatus.TARGET_1_HIT)` and only emit `MODIFY_ORDER` if the stop was actually tightened.
4. [MAJOR] `backend/app/strategies/orb.py` and `backend/app/strategies/news_momentum.py`: Clamp stop distance to the institutional floor/ceiling `[0.004 * entry_price, 0.040 * entry_price]` to prevent pre-trade risk engine rejection.
5. [MAJOR] `backend/app/ingestion/stock_ws.py`: Move `self._queue.task_done()` to a `finally:` block in `_process_queue_loop` and add per-item try/catch so corrupted items do not discard subsequent items or deadlock `join()`.
6. [MAJOR] `backend/app/main.py`: In `_check_session_boundary`, cancel and clear any lingering `engine.working_orders`.
7. [MINOR] `backend/app/core/risk.py`: Update `RiskEngineConfig` default `max_position_equity_pct = 1.000` ($50k) to match config.
8. [MINOR] `backend/app/main.py` and `backend/app/config.py`: Clean up residual docstrings and comments referencing Apple Music.
9. [MINOR] `backend/app/core/flattening.py`: Return `FlatteningPhase.PRE_MARKET` when time is before 09:30 ET instead of `NORMAL_TRADING`.
10. [MINOR] `backend/app/core/risk.py`: Compute `estimated_risk_dollars` based on `min(requested_qty, authorized_qty)`.
11. Add/update backend tests in `backend/tests/` to verify these fixes (especially the Target 2 partial fill behavior, session boundary working order purge, and stop distance clamping).
12. Run `pytest backend/tests` to verify 100% pass rate.

## Deliverable
Write `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_worker_backend_1/handoff.md` with:
- Exact changes made per file
- Build/test verification results (test count, duration, passing status)
- Any caveats or edge cases handled

## 2026-09-20T13:22:22Z
You are the Backend Architectural Remediation Worker for AutonomousDayTrader.
Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_worker_backend_1
Project root: /Users/mo/AutonomousDayTrader
MANDATORY FIRST STEP: Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Exclusive file ownership:
- backend/app/core/bracket.py
- backend/app/core/risk.py
- backend/app/core/flattening.py
- backend/app/strategies/orb.py
- backend/app/strategies/news_momentum.py
- backend/app/ingestion/stock_ws.py
- backend/app/ingestion/sentiment.py
- backend/app/main.py
- backend/app/config.py
- backend/tests/
Implement all 10 architectural remediations described in DISPATCH.md and audit_report.md.
Run pytest backend/tests to verify 100% pass rate.
Write /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_worker_backend_1/handoff.md and send a message when complete.
