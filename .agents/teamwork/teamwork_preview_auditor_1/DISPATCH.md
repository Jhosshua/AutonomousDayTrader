# Dispatch: Forensic Auditor 1 (Forensic Integrity Audit)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_auditor_1`

## Authoritative Documents
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md`

## Mission: Forensic Integrity Audit
Conduct a zero-tolerance forensic integrity audit across the codebase:
1. **Static Analysis & Anti-Cheating**:
   - Check for hardcoded test results, mock short-circuits in production code paths, or fake implementations.
   - Verify that all 7 quantitative rules (Macro Floor >200 SMA, 60d RS vs QQQ, Panic RSI(2)<10, 48h earnings veto, 16:00 close qualification -> 09:30 open buy execution, $25,000/slot sizing with max 2 concurrent swing positions, 2.5x ATR(14) emergency stop, and 5-day SMA / RSI(2)>70 / 5-day time stop exits) are genuinely implemented with real mathematical logic.
2. **Zero Lookahead Audit**:
   - Inspect data access patterns: Ensure no `future`, `t+1`, or unclosed bar access in any indicator or signal module.
3. **Execution & Port Audit**:
   - Run tests and static analysis. Verify zero lingering processes on ports 3005, 8000, 8005, 8080.

## Output Requirements
Write your detailed audit report to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_auditor_1/handoff.md`.
Conclude with a formal verdict: `CLEAN` or `INTEGRITY VIOLATION`.
Send a message back to the caller when complete.
