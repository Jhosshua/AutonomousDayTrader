# Task Dispatch: Explorer 1 — Test UI Stream Resilience & Bracket Lifecycle Fix Strategy

## Mandatory Context
MANDATORY FIRST STEP: Read `/Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md` before starting work.
Full Forensic Auditor report: `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_auditor_forensics_1/handoff.md`
Reviewer 1 report: `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_reviewer_diff_1/handoff.md`
Challenger 1 report: `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_challenger_stress_1/handoff.md`

## Problem Statement
The Forensic Auditor reported an **INTEGRITY VIOLATION** because `scripts/run_e2e_tests.sh` failed with 1 failure (292 passed, 1 failed):
```
FAILED tests/e2e/test_ui_stream_resilience.py::test_high_frequency_broadcast_and_receipt
assert 148.0 == 148.01
```
Root Cause identified by Auditor and Reviewer 1:
In `backend/app/core/bracket.py`, `manual_tighten_stop` was correctly hardened to disallow stop tightening on un-filled orders:
`if bracket.status not in (BracketStatus.ACTIVE, BracketStatus.TARGET_1_HIT): return NO_ACTION`
In `tests/e2e/test_ui_stream_resilience.py:38-59`, the test mock setup creates a bracket using `bracket_manager.create_bracket("brk_hf_test", "AAPL", "LONG", 100, 150.0, 148.0)` (initial status `PENDING_ENTRY`), but never activates it via fill before sending `TIGHTEN_STOP`.

## Objective
Analyze `tests/e2e/test_ui_stream_resilience.py` and `backend/app/core/bracket.py`.
Provide a concrete, robust fix strategy so that:
1. The test accurately activates the bracket or exercises stop tightening on a validly active bracket.
2. The trading engine and bracket manager maintain their institutional safety invariants.
3. No integrity rules are violated.

Write your report to:
`/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_1/strategy_report.md`
and `handoff.md`.

## 2026-09-20T13:36:08Z
You are Explorer 1 (Bracket & Test Resilience Explorer) for AutonomousDayTrader.

Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_1
Project root: /Users/mo/AutonomousDayTrader

MANDATORY FIRST STEP: Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also read:
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_1/DISPATCH.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_auditor_forensics_1/handoff.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_reviewer_diff_1/handoff.md

Task:
Analyze the E2E failure in `tests/e2e/test_ui_stream_resilience.py::test_high_frequency_broadcast_and_receipt` and the bracket lifecycle in `backend/app/core/bracket.py`.
Provide a concrete fix strategy to ensure `manual_tighten_stop` and `test_ui_stream_resilience.py` work harmoniously with genuine bracket activation and 100% test pass rate.
Do NOT modify implementation code directly (you are read-only). Formulate recommendations with exact code snippets.

Write:
- `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_1/strategy_report.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_1/handoff.md`
Send a message when complete.
