## 2026-09-22T23:55:00Z

You are Worker 1: Strategy & Execution Architecture Remediator.

Your working directory is:
/Users/mo/AutonomousDayTrader/.agents/teamwork/worker_remediation
All your metadata, logs, and handoff must be written to your working directory.

Authoritative source of truth:
You MUST read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also thoroughly study:
- /Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_3/PLAN.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_1_forensics/handoff.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_1_forensics/analysis.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_market_filter/handoff.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_market_filter/analysis.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_3_strategies/handoff.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_3_strategies/analysis.md
- /Users/mo/AutonomousDayTrader/MEMORY.md
- /Users/mo/AutonomousDayTrader/ERRORS.md
- /Users/mo/AutonomousDayTrader/PROJECT.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Exclusive Write Ownership:
You own and may edit or create the following files:
- backend/app/core/market_filter.py (new)
- backend/app/core/bracket.py
- backend/app/main.py
- backend/app/strategies/adaptation.py
- backend/app/strategies/orb.py
- backend/app/strategies/news_momentum.py
- backend/app/strategies/mean_reversion.py
- backend/tests/unit/test_market_filter.py (new)
- backend/tests/unit/ (unit test files for bracket, strategies, adaptation)

Tasks to Implement:
1. Market Trend Filter (backend/app/core/market_filter.py):
   - Ingest closed 1-minute bars for SPY and QQQ.
   - Maintain Anchored VWAP (anchored to 09:30 ET market open) and EMA 9 / EMA 21 for both SPY and QQQ.
   - Classify market regime: BULLISH, BEARISH, NEUTRAL, UNKNOWN.
   - Edge cases: Fail-closed to UNKNOWN during pre-market, missing data, or if data is stale (> 120s). Zero lookahead bias (closed bars only).
2. Wire MarketTrendFilter into main.py and adaptation.py:
   - In main.py: instantiate MarketTrendFilter, feed SPY and QQQ bars to it on ingestion.
   - In adaptation.py (evaluate_signal_admission): check MarketTrendFilter regime.
     - ORB & VWAP Pullback: BUY requires BULLISH, SELL requires BEARISH. Reject in NEUTRAL/UNKNOWN or counter-trend.
     - News Momentum: Require index alignment unless extreme catalyst (|S| >= 0.85, volume >= 5.0x).
     - Mean Reversion: Permit counter-trend exhaustion fades, avoid trading into unconfirmed runaway trend.
3. Bracket Geometry & Target Scaling (backend/app/core/bracket.py & backend/app/main.py):
   - Restructure default Target 1 from 1.5R to 0.80R (or 1.00R) and Target 2 to 1.80R (or 2.00R) or trailing ATR.
   - Fix main.py:958-959: Pass target_1_override=signal.take_profit_1 and target_2_override=signal.take_profit_2 for ALL strategies so strategy-defined realistic targets are respected.
   - Maintain trailing stop strictly gated to TARGET_1_HIT only. Ensure breakeven buffer scales with price: max(0.04, round(entry_price * 0.0005, 2)).
4. Refine Strategy Entry Triggers:
   - orb.py: Add Close Location Value (CLV = (close - low) / (high - low); require CLV >= 0.65 for BUY, <= 0.35 for SELL). Add Bar Range Cap (bar.high - bar.low <= 2.2 * ATR) and Extension Cap (close <= breakout + 1.0 * ATR). Set default targets to 0.8R T1, 1.8R T2.
   - news_momentum.py: Use word-boundary regex r'\b' + re.escape(w) + r'\b' in score_news_sentiment to prevent false substring matches (e.g. miss in emission). Enforce candle direction confirmation (close > open for BUY, close < open for SELL). Fix 09:31 volume baseline (if < 5 bars, use 500k volume floor or open flush lockout). Set default targets to 0.8R T1, 1.8R T2.
   - mean_reversion.py: Calibrate for moderate VIX (14-16): Z-score threshold 2.0, RSI 70/30, volume surge multiplier 1.75x, rejection wick ratio 35%, stop placement extreme +- 0.15 * ATR, R:R hurdle >= 1.0.
5. Testing & Verification:
   - Run pytest on all backend tests: pytest backend/tests -v
   - Ensure 100% test pass rate with zero regression. If existing unit tests fail because they assert outdated 1.5R targets or lack the new market filter mock, update them thoughtfully while preserving their verification intent.
   - Write comprehensive tests in backend/tests/unit/test_market_filter.py verifying VWAP/EMA math, regime transitions, and staleness fail-closed.
   - Ensure zero orphaned processes and ports cleanly freed.

Deliverables:
- Write complete, robust code to the target files.
- Run test suites and verify 100% pass rate.
- Write handoff report to /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_remediation/handoff.md following Handoff Protocol (Observation, Logic Chain, Caveats, Conclusion, Verification Method). Include full pytest test outputs and commands run.
- Send message to parent upon completion.
