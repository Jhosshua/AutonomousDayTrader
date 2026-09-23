# Dispatch: Worker M9B (Causal Daily Indicators, Earnings Calendar & Swing Strategy Engine)

## Working Directory
`/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9b_1`

## Authoritative Documents
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_explorer_survey_2/handoff.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9a_1/handoff.md`

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Objective & Tasks
Implement Milestone M9B (`indicators_calendar_signals`):
1. **Data Seed Fixtures & Storage**:
   - Create `backend/app/data/daily_bars_seed.json`: 250+ historical daily bars for `LRCX`, `KLAC`, `MU`, `AMD`, `GS`, and `QQQ`. Include realistic OHLCV bars ensuring 200 SMA, 5 SMA, 60d RS, and 14 ATR can be computed causally.
   - Create `backend/app/data/earnings_calendar.json`: Seed calendar for the 5 certified stocks.
   - Implement `DailyBarStore` / `DailyBarAggregator` to load seed data and update daily bars upon 16:00 ET close.
2. **Causal Rolling Daily Indicators (`backend/app/strategies/swing_indicators.py`)**:
   - Rule 1 (Macro Floor): Today's daily close > 200-day SMA.
   - Rule 2 (60d Relative Strength vs QQQ): $\Delta_{\text{stock}, 60d} \ge \Delta_{\text{QQQ}, 60d}$.
   - Rule 3 (Panic Trigger): 2-day Connors RSI (`RSI(2)`) < 10.0 on daily close.
   - Rule 6 (Emergency Stop): 14-day Daily ATR: Emergency stop price = $P_{\text{fill}} - 2.5 \times \text{Daily ATR(14)}$.
   - Rule 7 (Exits):
     a) Prior daily close > 5-day SMA.
     b) Prior daily RSI(2) > 70.0.
     c) Holding days >= 5 (time stop).
   - ZERO LOOKAHEAD GUARANTEE: Mathematical calculations must only use closed sessions.
3. **48-Hour Earnings Calendar (`backend/app/strategies/earnings_calendar.py`)**:
   - Rule 4: No entry if earnings within 48 hours.
   - If holding an active position and earnings report tomorrow, trigger sell at 09:30 open.
   - Graceful cached fallback if remote provider is unavailable.
4. **Swing Execution Engine & Staged Orders (`backend/app/strategies/swing_panic_dip.py`)**:
   - Scan at 16:00 ET close: Evaluate 5 certified stocks. Stage qualified buy orders in `SwingStagedOrderManager` for 09:30 open.
   - Execution at 09:30 ET open:
     - Process exit orders first (prior close > 5 SMA, RSI(2) > 70, 5 days held, or earnings tomorrow).
     - Process entry orders: $25,000 notional per slot ($\lfloor 25000 / P_{\text{open}} \rfloor$), enforce max 2 concurrent swing positions.
     - Establish hard emergency stop at $P_{\text{fill}} - 2.5 \times \text{Daily ATR(14)}$ immediately upon fill.
     - Tag orders and positions with `arm=TradingArm.SWING`, `strategy_id="swing_panic_dip"`.
5. **System Wiring in `main.py`**:
   - Integrate `SwingStrategyEngine` into application lifecycle and event loops.
   - Subscribe `SWING_SYMBOLS = ["LRCX", "KLAC", "MU", "AMD", "GS"]` on stock data feeds alongside `WATCHLIST_SYMBOLS`.
6. **Testing**:
   - Write comprehensive tests in `backend/tests/test_swing_indicators.py` and `backend/tests/test_swing_strategy.py`.
   - Run `pytest backend/tests/ -q` to guarantee 100% pass rate.

## Output Requirements
Write your detailed report to `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_worker_m9b_1/handoff.md`.
Include: exact files touched, diffs summary, build and test commands run, test pass output, and verification results.
When done, send a message to the caller with your status and summary.

## 2026-09-23T21:47:30Z
User Request received for Milestone M9B.
Target deliverables:
1. Data seed fixtures: backend/app/data/daily_bars_seed.json and backend/app/data/earnings_calendar.json
2. Causal rolling daily indicators in backend/app/strategies/swing_indicators.py: 200 SMA, 60d RS vs QQQ, Connors RSI(2), 14 Daily ATR, 5-day SMA exit. Zero lookahead guarantee!
3. 48-hour earnings calendar lookup with graceful fallback in backend/app/strategies/earnings_calendar.py
4. SwingStrategyEngine and SwingStagedOrderManager in backend/app/strategies/swing_panic_dip.py: 16:00 close qualification -> 09:30 open buy execution, $25,000 notional sizing, max 2 concurrent swing positions, 2.5x ATR emergency stop, 5-SMA / RSI(2)>70 / 5-day time stop / earnings exits.
5. Wire swing engine and symbol subscriptions into backend/app/main.py.
6. Write unit tests in backend/tests/test_swing_indicators.py and backend/tests/test_swing_strategy.py, and ensure 100% pass across all pytest tests.
