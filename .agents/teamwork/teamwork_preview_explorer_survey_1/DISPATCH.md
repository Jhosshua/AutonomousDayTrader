# Dispatch: Explorer 1 (Backend Core, Account & Flattening Exemption)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_1`

## Authoritative User Request
Read `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md` (and `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`).

## Mission & Scope
Investigate the existing backend architecture in `backend/app/` to design the integration of the autonomous swing trading engine ("2-Day Panic Dip"):
1. Analyze `backend/app/core/` (paper account, cash/buying power management, risk engine, order book, execution, and EOD auto-flattening engine).
2. Examine the 4-phase EOD auto-flattening mechanism (15:45 lockout, 15:50 cancel, 15:55 liquidation, 15:58 flat audit). How are positions and orders currently tracked, tagged, and liquidated? How can swing positions, bracket stops, and staged orders be explicitly tagged and exempt from EOD liquidation so overnight holds operate uninterrupted?
3. Analyze account capital allocation: The virtual account has a shared $50,000 pool. The swing engine needs $25,000 notional per slot with a hard cap of max 2 concurrent swing positions. How does the account/risk engine coordinate margin and buying power across intraday day trading and swing positions without double-spending or margin collision?
4. Document specific file paths, class names, method signatures, data structures, and edge cases.
5. Provide concrete architectural recommendations for implementing R1 & R2 from the dispatch.

## Output Requirements
Write your detailed findings and architectural analysis to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_1/handoff.md`.
Follow the Handoff Protocol: Observation, Logic Chain, Caveats, Conclusion, Verification Method.
When done, send a message back to the caller with a summary and link to your handoff.md.

## 2026-09-23T21:26:25Z
You are Explorer 1 (Backend Core, Account & Flattening Exemption Architecture Explorer).
Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_1.
You MUST read the authoritative user request at: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md.
Also read your full dispatch instructions at: /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_1/DISPATCH.md and /Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md.

Your mission is to explore backend/app/core/ (paper account, risk engine, order book, execution, and EOD auto-flattening engine) to design:
1. The 4-phase EOD auto-flattening exemption mechanism for swing positions, bracket stops, and staged orders.
2. Sizing and capital allocation ($25,000 per slot, max 2 concurrent swing positions) coordinated with the shared $50,000 virtual account pool without double-spending or margin collisions.
3. State models, order tagging, position classification, and durable SQLite ledger compatibility.

Follow the Handoff Protocol: write your detailed report to /Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_1/handoff.md.
Update progress.md in your working directory as you work.
When complete, use send_message to report your findings back to the caller.
