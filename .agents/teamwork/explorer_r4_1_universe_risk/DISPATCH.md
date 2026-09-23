# Dispatch for Explorer 1 (Universe & Risk)
Role: Universe & Risk Explorer
Working directory: /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_1_universe_risk
Authoritative request: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md (under ## 2026-09-23T19:09:59Z)

## 2026-09-23T19:12:15Z
You are Explorer 1 (Universe & Risk Explorer) for AutonomousDayTrader.
Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_1_universe_risk
Your parent is: orchestrator_5 (Conversation ID: 5cdb7319-1240-43a6-9073-f74cd8e19cf8)
Authoritative request: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md (under ## 2026-09-23T19:09:59Z)

Your Mission:
Investigate and survey the codebase for Requirement R1 (Universe Expansion & Sector Mapping) and related risk invariants:
1. Examine `backend/app/config.py` (specifically `WATCHLIST_SYMBOLS` and related config).
2. Examine `backend/app/core/risk.py` (sector mappings, sector concentration caps, portfolio concentration logic, max 2 positions per sector, max 3 positions total, single-position cap $25,000, $1,500 daily breaker, stop guardrails).
3. Check how symbol subscription and ingestion work across the expanded list: ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"].
4. Identify how sector mapping should be structured across sectors:
   - Semiconductors: NVDA, AMD
   - Software: MSFT, PLTR
   - Discretionary: TSLA, AMZN
   - Communication Services: GOOGL, META
   - Fintech/Crypto: COIN
   - Index/ETF: SPY, QQQ (or how benchmark symbols are categorized)
5. Check existing tests in `backend/tests/` (e.g. `test_risk.py`, `test_engine.py`, etc.) for sector limits and watchlist assumptions.
6. Address sector starvation: ensure portfolio concentration checks do not lock out diverse opportunities unfairly while strictly capping at max 2 positions per sector and max 3 concurrent positions total.

Deliverables:
- Write comprehensive analysis to: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_1_universe_risk/analysis.md`
- Write your final handoff report to: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_1_universe_risk/handoff.md`
- Send a completion message via send_message to orchestrator_5 with a concise summary and path to your handoff.
