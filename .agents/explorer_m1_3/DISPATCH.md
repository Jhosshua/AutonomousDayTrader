# Task Assignment
Agent: explorer_m1_3
Role: Explorer 3 - Institutional Risk Guardrails, Circuit Breakers & 4-Phase Auto-Flattening
Working Directory: /Users/mo/AutonomousDayTrader/.agents/explorer_m1_3

## 2026-09-19T23:44:00Z
You are explorer_m1_3, the explorer for Institutional Risk Guardrails, Circuit Breakers, and Auto-Flattening for Milestone 1 (engine_ingestion).
Your identity: explorer_m1_3
Your working directory: /Users/mo/AutonomousDayTrader/.agents/explorer_m1_3
You report to: parent orchestrator (conversation ID: f9df3e28-501d-4830-bf1f-140b6216f49e)

Mandatory inputs:
- Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md
- Read /Users/mo/AutonomousDayTrader/PROJECT.md
- Read /Users/mo/AutonomousDayTrader/.agents/explorer_strategies_survey/survey_report.md

Objective:
Investigate and design the technical implementation architecture for the Risk Engine, Dynamic Brackets, and Zero-Overnight Flattening:
1. Institutional Risk Engine & Circuit Breakers (backend/app/core/risk.py):
   - Hard maximum daily loss limit: $1,500 / 3.0% of starting equity ($50,000).
   - Real-time circuit breaker trigger: When daily realized + unrealized drawdown >= $1,500, trigger emergency halt: immediately reject all incoming order requests, purge all pending/unfilled orders, and market-liquidate open positions.
   - Per-position risk sizing: 1–2% equity risk ($500–$1,000 per trade), calculating order quantity q = floor(Risk$ / |Entry - Stop|).
2. Dynamic Bracket Orders & Trailing Stops (backend/app/core/bracket.py):
   - Multi-tier profit targets: Target 1 at 1.5R (scale out 50% + ratchet stop to breakeven), Target 2 at 2.5R (scale out remaining or trail ATR stop).
   - OCO (One-Cancels-Other) bracket order management and trailing stop updates.
3. Automated 4-Phase Zero-Overnight Flattening (backend/app/core/flattening.py):
   - Phase 1 (15:45 ET): Entry Lockout (block new entries).
   - Phase 2 (15:50 ET): Working Order Purge (cancel open limit/stop orders).
   - Phase 3 (15:55 ET): Mandatory Market Liquidation (close open positions).
   - Phase 4 (15:58 ET): Zero-Overnight Audit (verify zero position exposure before 16:00 ET).

Scope boundaries:
Do NOT write application source code. Formulate concrete file-by-file implementation blueprints with class definitions, method signatures, state machine logic, and unit test specifications.
Write your findings to:
/Users/mo/AutonomousDayTrader/.agents/explorer_m1_3/survey_report.md
and handoff to:
/Users/mo/AutonomousDayTrader/.agents/explorer_m1_3/handoff.md
Send a completion message to the parent orchestrator when finished.
