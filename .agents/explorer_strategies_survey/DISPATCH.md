# Task Assignment
Agent: explorer_strategies_survey
Role: Explorer - Strategies, Risk Engine & Execution Mechanics
Working Directory: /Users/mo/AutonomousDayTrader/.agents/explorer_strategies_survey

## 2026-09-19T23:39:54Z

<USER_REQUEST>
You are explorer_strategies_survey, an exploration and algorithmic analysis agent for the AutonomousDayTrader project.
Your identity: explorer_strategies_survey
Your working directory: /Users/mo/AutonomousDayTrader/.agents/explorer_strategies_survey
You report to: parent orchestrator (conversation ID: f9df3e28-501d-4830-bf1f-140b6216f49e)

Mandatory input: Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md in its entirety before proceeding.

Objective:
Formulate, research, and document the architectural and mathematical specifications for the trading engine and the 4 dynamic intraday trading strategies:
1. $50,000 Paper Trading Account & Risk Engine:
   - State machine: Cash, Equity, Buying Power (e.g. 4x intraday margin for pattern day trading or cash basis), Open Positions, Realized/Unrealized PnL, Order Lifecycle (Pending, Filled, Partially Filled, Cancelled, Rejected).
   - Institutional Risk Guardrails: Hard maximum daily loss circuit breaker (e.g., $1,500 / 3% drawdown halting all trading), per-position risk limit (e.g., 1-2% account equity risk per trade), dynamic stop-loss/take-profit brackets (ATR or tick-based), and automated zero-overnight flattening starting at 15:45-15:55 ET, guaranteed flat before 16:00 ET.
2. 4 High Sharpe-Ratio Intraday Strategies:
   - Strategy 1: Opening Range Breakout (ORB) - 5-min or 15-min opening range calculation, volume surge confirmation, breakout entry triggers, trailing stops.
   - Strategy 2: VWAP Trend Pullback & Continuation - Anchored VWAP, standard deviation bands, trend confirmation (EMA20/EMA50), pullback entry onto VWAP with high-volume bounce.
   - Strategy 3: Catalyst News Momentum Breakout - Ingestion of AlpacaRelay real-time news headlines, NLP/keyword sentiment scoring, instant surge detection, breakout execution, news contradiction circuit breakers.
   - Strategy 4: Statistical Mean Reversion / Exhaustion Fades - Bollinger bands / Z-score exhaustion on 1-min bars, RSI divergence, exhaustion volume spike fade back to mean.
3. Dynamic Self-Adaptation:
   - Real-time VIX adaptation from /vix: Volatility regimes (Low <15, Normal 15-25, Elevated 25-35, Crisis >35). How position size scales inversely with VIX, widening stops in high volatility, tightening thresholds in low volatility.
   - Time-of-Day Dynamics:
     * Pre-market (08:00–09:30 ET): Gap scanner, news watch list.
     * Open Volatility Flush (09:30–10:00 ET): ORB establishment, high spread avoidance, fade traps.
     * Trend Continuation (10:00–11:30 ET): Primary momentum & VWAP trend execution window.
     * Midday Chop Defense (11:30–14:00 ET): Lower sizing, higher threshold, mean-reversion focus or trade pause.
     * Power Hour & Flattening (15:00–16:00 ET): Momentum scalp window until 15:45, mandatory position liquidation and order cancellation to ensure 0 overnight holds.

Scope boundaries:
Do NOT write application source code.
Write your detailed report to:
/Users/mo/AutonomousDayTrader/.agents/explorer_strategies_survey/survey_report.md
Include progress updates in /Users/mo/AutonomousDayTrader/.agents/explorer_strategies_survey/progress.md.
When finished, send a message to the parent orchestrator with your findings and path to the report.
</USER_REQUEST>
