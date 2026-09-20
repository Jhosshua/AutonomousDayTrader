# Task Dispatch: Explorer 2 — Floating-Point Stop Distance Clamping Strategy

## Mandatory Context
MANDATORY FIRST STEP: Read `/Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md` before starting work.
Full Forensic Auditor report: `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_auditor_forensics_1/handoff.md`
Reviewer 1 report: `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_reviewer_diff_1/handoff.md`

## Problem Statement
Reviewer 1 identified that clamping stop distance to exact boundary floats:
`min_dist = round(entry_price * 0.004, 4)` and `max_dist = round(entry_price * 0.040, 4)`
causes IEEE 754 floating-point inaccuracies in `InstitutionalRiskEngine.evaluate_order_request`, where `stop_dist / entry_price < 0.004` evaluates to true for ~50% of equity prices ($150 AAPL produces 0.003999999999999962 < 0.004), rejecting valid trades.

## Objective
Analyze `backend/app/core/risk.py` lines 217-238, `backend/app/strategies/orb.py`, and `backend/app/strategies/news_momentum.py`.
Recommend a mathematically rigorous, dual-sided fix:
1. Adding an epsilon buffer (`EPS = 1e-6`) to `InstitutionalRiskEngine.evaluate_order_request` inequality checks (`stop_dist_pct < self.config.min_stop_distance_pct - EPS`).
2. Clamping strategies with a safe interior buffer (e.g. `0.0042` / `0.0380` or `round(..., 4)` with safe floor) so that even without risk changes, orders never land right on the razor edge.
3. Formulate the exact code edits.

Write your report to:
`/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_2/strategy_report.md`
and `handoff.md`.

## 2026-09-20T13:36:08Z
You are Explorer 2 (Risk Boundary & Clamping Explorer) for AutonomousDayTrader.

Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_2
Project root: /Users/mo/AutonomousDayTrader

MANDATORY FIRST STEP: Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also read:
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_2/DISPATCH.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_reviewer_diff_1/handoff.md

Task:
Analyze the IEEE 754 floating-point boundary issue in `backend/app/core/risk.py` and strategy stop clamping in `backend/app/strategies/orb.py` and `backend/app/strategies/news_momentum.py`.
Formulate a mathematically bulletproof fix strategy using safe interior clamping in strategies and epsilon tolerance in `InstitutionalRiskEngine`.
Do NOT modify implementation code directly. Formulate recommendations with exact code snippets.

Write:
- `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_2/strategy_report.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_fix_plan_2/handoff.md`
Send a message when complete.
