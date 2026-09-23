## 2026-09-23T04:10:04Z
You are Challenger 2: Adversarial Bracket & Risk Geometry Tester.

Your working directory is:
/Users/mo/AutonomousDayTrader/.agents/teamwork/challenger_2
All your test scripts, reports, and handoffs must be written to your working directory.

Authoritative source of truth:
You MUST read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also inspect:
- /Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_3/PLAN.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_remediation/handoff.md
- backend/app/core/bracket.py
- backend/app/core/risk.py
- backend/app/core/engine.py
- backend/app/main.py

Your Mission:
1. Empirically and adversarially challenge the dynamic bracket management, target scaling, and risk guardrails.
2. Write and execute stress tests that actively test:
   - Target 1 scaling at 0.8R and Target 2 at 1.8R on both BUY and SELL sides.
   - Trailing stop monotonicity and gating: verify that on an ACTIVE bracket (before Target 1 is hit), a price rally does NOT move the stop loss into entry noise.
   - Verify that once Target 1 is hit, the 50% scale-out occurs, stop ratchets to breakeven + dynamic buffer (max(0.04, entry * 0.0005)), and ATR trailing stop begins.
   - Boundary tests at the exact risk engine limits ($1500 daily loss, $25,000 position notional cap, 0.40% to 4.00% stop range with EPS = 1e-6 float tolerance).
   - Test partial fills, whipsaw quotes, and fast micro-crashes.
3. Write your detailed adversarial challenge report to:
   /Users/mo/AutonomousDayTrader/.agents/teamwork/challenger_2/challenge_report.md
   and handoff report to:
   /Users/mo/AutonomousDayTrader/.agents/teamwork/challenger_2/handoff.md
   Include your clear gate verdict: APPROVE or FAIL.
4. Send completion message to parent when done.
