## 2026-09-23T03:49:42Z

You are the Project Orchestrator for AutonomousDayTrader.

Your working directory is:
/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_3

The project root is:
/Users/mo/AutonomousDayTrader

The authoritative user request is documented in:
/Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md
and mirrored at:
/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md

Please review the latest request entry (dated 2026-09-23T03:48:51Z) immediately:
Empirically diagnose and remediate underperformance in AutonomousDayTrader using live paper execution data, quantitative literature, and market microstructure analysis. Implement robust structural improvements across strategy triggers and bracket geometry, verify through independent multi-agent audit and integrated simulation, update documentation, and deploy to Railway.

Key Production Facts & Context:
- Starting Equity: $50,000.00
- Current Production Equity: $49,798.32 (-$201.68 realized loss)
- Production Win Rate: 0.00% across 7 trades (0 wins, 7 losses/scratches).
- Target 1 (1.5R) Hit Rate: 0.00% (0 of 7 trades hit Target 1).
- Observed Failures:
  - 2026-09-21: 4 trades scratched within 3 minutes by trailing stop ratchets walking into entry noise; 1 trade clipped (TSLA long +$18.39, capturing only 23% of intended target) before reversing.
  - 2026-09-22: 2 trades stopped out at full loss: TSLA SHORT (news_momentum) @ 09:31 ET (-$68.30) and AAPL SHORT (orb) @ 10:09 ET (-$112.04).
  - Context blindness: Strategies trigger on individual stock bars without checking broader index beta (SPY/QQQ trend), shorting stocks into market-wide morning bid.
  - Unrealistic profit geometry: Target 1 at 1.5R and Target 2 at 2.5R are mathematically unachievable for intraday 1m/5m bars before noise stops out the trade.

Your Mission & Requirements:
1. R1: Quantitative Forensic Analysis & Research:
   - Deep quantitative and market-microstructure research into ORB failures without SPY/QQQ trend confirmation, optimal intraday profit target scaling (banking partial profits at 0.75R–1.0R instead of 1.5R), preventing breakout exhaustion fills, and hardening news sentiment scoring beyond crude regex token-matching.
   - Base all diagnoses on real trade logs and code mechanics, never synthetic replay fixtures.
2. R2: Strategy & Execution Architecture Remediation:
   - Implement causal market index / trend filter (SPY/QQQ VWAP or EMA directional alignment) preventing counter-trend individual setups.
   - Restructure profit target & bracket management in backend/app/core/bracket.py (realistic scaling 0.8R-1.0R to de-risk trades quickly; trailing stop must not walk into noise before breakeven).
   - Refine entry conditions in backend/app/strategies/orb.py and backend/app/strategies/news_momentum.py (prevent climax entries).
   - Calibrate mean_reversion.py for moderate VIX (14-16).
3. R3: Unbiased Adversarial Multi-Agent Review:
   - Deploy independent, unbiased subagents (explorers/researchers, workers, reviewers/challengers) at each phase.
   - Audit for lookahead bias, parameter curve-fitting, floating-point/boundary cases, and risk engine invariant violations ($1500 circuit breaker, $25,000 cap, 0.4%-4.0% stop guardrails).
4. R4: Deterministic Verification & Integrated Dry Run:
   - 100% test pass rate in pytest backend/tests.
   - Execute python scripts/run_integrated_monday_dry_run.py exercising production main.py wiring.
   - Zero orphaned processes or listening ports on 8005, 3005, 8080.
5. R5: Documentation, Git Commit, and Remote Railway Deployment:
   - Update MEMORY.md, ERRORS.md, and PROJECT.md.
   - Push commits to origin main.
   - Verify Railway remote build & deployment succeeds, and https://autonomousdaytrader-production.up.railway.app/health returns healthy.

Operational Requirements:
- Immediately create and update BRIEFING.md and progress.md in your working directory (/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_3).
- Update progress.md regularly with every major phase and milestone.
- Orchestrate subagents using the .agents/teamwork/<agent_name> convention.
- When all tasks and acceptance criteria are completed, deliver a comprehensive handoff report (handoff.md) and report completion to the Sentinel.
