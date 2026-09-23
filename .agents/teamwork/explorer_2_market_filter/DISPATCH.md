## 2026-09-23T03:50:32Z
You are Explorer 2: Market Index Filter & Microstructure Specialist.

Your working directory is:
/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_market_filter
All metadata, analysis, and handoffs must be written to your working directory.

Authoritative source of truth:
You MUST read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also inspect:
- /Users/mo/AutonomousDayTrader/PROJECT.md
- /Users/mo/AutonomousDayTrader/MEMORY.md
- /Users/mo/AutonomousDayTrader/backend/app/core/engine.py
- /Users/mo/AutonomousDayTrader/backend/app/ingestion/stock_ws.py
- /Users/mo/AutonomousDayTrader/backend/app/strategies/base.py
- /Users/mo/AutonomousDayTrader/backend/app/strategies/adaptation.py
- /Users/mo/AutonomousDayTrader/backend/app/config.py

Your Mission:
1. Research why single-stock intraday strategies (e.g., ORB, News Momentum) suffer from context blindness when triggered without broader index beta (SPY/QQQ trend confirmation):
   - Cite quantitative literature and microstructure principles (e.g. market-wide liquidity tides, morning bid momentum, high intraday beta of mega-caps AAPL/TSLA/NVDA to SPY/QQQ).
   - Diagnose why 2026-09-22 trades (TSLA SHORT @ 09:31 ET, AAPL SHORT @ 10:09 ET) failed due to shorting into a market-wide morning rally.
2. Design a causal, non-lookahead market trend filter:
   - Evaluate SPY / QQQ VWAP alignment (e.g., Price > VWAP = bullish, Price < VWAP = bearish).
   - Evaluate EMA directional alignment (e.g. EMA9 > EMA21 or EMA20 slope).
   - Determine how market data for SPY and QQQ should be ingested, tracked, and stored in the engine without introducing lag, race conditions, or lookahead bias (e.g., tracking bar closes or real-time VWAP).
   - Specify the exact interface contract for how strategies (ORB, VWAP Pullback, News Momentum, Mean Reversion) query market trend to filter or reject contradictory signals.
   - Address edge cases: what happens during pre-market, at market open (09:30-09:35), or when index data is stale or missing.

Deliverables:
- Write comprehensive report to /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_market_filter/analysis.md
- Write handoff to /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_market_filter/handoff.md
- Send completion message to parent when done.
