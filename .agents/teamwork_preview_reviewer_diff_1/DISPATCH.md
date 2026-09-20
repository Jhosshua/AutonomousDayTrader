# Task Dispatch: Reviewer 1 — Backend & Risk Diff Review

## Objective
Perform an independent, adversarial code review of all backend changes introduced in the remediation phase (`backend/app/core/bracket.py`, `backend/app/core/risk.py`, `backend/app/core/flattening.py`, `backend/app/strategies/orb.py`, `backend/app/strategies/news_momentum.py`, `backend/app/ingestion/stock_ws.py`, `backend/app/main.py`, `backend/app/config.py`, and `backend/tests/`).

## Review Criteria
1. **Correctness & Robustness**:
   - Verify Target 2 partial fill logic: Does it properly protect remaining shares and avoid premature stop cancellation?
   - Verify `order_to_bracket` child order pruning: Are all orders pruned without KeyError or dangling references?
   - Verify `manual_tighten_stop`: Does it properly reject loosened stops and only modify when active?
   - Verify stop distance clamping: Are the boundaries mathematically sound across low-priced and high-priced equities?
   - Verify ingestion queue handling: Does `finally: task_done()` prevent queue deadlocks under all exception branches?
   - Verify session boundary: Does it cleanly purge working orders?
2. **Contract Preservation**:
   - Ensure all public interfaces and event signatures (`BarEvent`, `SignalEvent`, `OrderEvent`, `FillEvent`, `BracketUpdateDirective`) remain intact with zero contract drift.
3. **Run Verification Commands**:
   - Run `pytest backend/tests -v`
   - Document commands, test counts, execution time, and outcome.
4. **Verdict**:
   - Provide an explicit verdict: `APPROVE` or `REQUEST_CHANGES` in `handoff.md`.

Write your report and handoff to:
`/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_reviewer_diff_1/handoff.md`

## 2026-09-20T13:30:53Z
You are Reviewer 1 (Backend & Risk Diff Reviewer) for AutonomousDayTrader.

Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_reviewer_diff_1
Project root: /Users/mo/AutonomousDayTrader

MANDATORY FIRST STEP: Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also read:
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_reviewer_diff_1/DISPATCH.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_worker_backend_1/handoff.md
- /Users/mo/AutonomousDayTrader/PROJECT.md

Task:
Perform an adversarial diff review of the backend code changes (`git diff` on backend files).
Verify correctness of:
- Target 2 partial fill logic and stop resizing in `bracket.py`
- `order_to_bracket` pruning across completion and flattening
- `manual_tighten_stop` validation
- Stop distance clamping in `orb.py` and `news_momentum.py`
- Queue handling in `stock_ws.py`
- Session boundary order purge in `main.py`
- Risk config default and estimated risk calculation in `risk.py`
- Flattening pre-market phase handling

Run `pytest backend/tests -v`.
Provide an explicit verdict (`APPROVE` or `REQUEST_CHANGES`) in `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_reviewer_diff_1/handoff.md`.
Send a message when complete.
