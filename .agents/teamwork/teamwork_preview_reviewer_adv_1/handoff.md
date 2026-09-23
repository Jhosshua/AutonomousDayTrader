# Handoff Report — Reviewer 1 (Adversarial Pass 1: Mathematical & Zero-Lookahead Audit)

**Agent**: Reviewer 1 (`teamwork_preview_reviewer_adv_1`)  
**Mission**: Adversarial Pass 1: Mathematical precision, causality, zero-lookahead bias, Wilder smoothing accuracy, earnings blackout window, and execution timing audit across `backend/app/strategies/swing_indicators.py`, `backend/app/strategies/earnings_calendar.py`, and `backend/app/strategies/swing_panic_dip.py`.  
**Formal Verdict**: **`REQUEST_CHANGES`**  

---

## Review Summary

**Verdict**: **`REQUEST_CHANGES`**  
The core mathematical formulas for the indicators (200 SMA, 60d RS vs QQQ, Connors RSI-2, and 14-day ATR) and the zero-lookahead architecture (16:00 ET close qualification staging orders for 09:30 ET open execution without querying tomorrow's open price) are rigorously designed and strictly causal. However, adversarial auditing uncovered **two Critical defects**, **two Major defects**, and **one Minor defect** that must be remediated before production readiness:
1. **[CRITICAL] Runtime Crash (`AttributeError`) in `to_ui_dict()` on Active Swing Positions**: Field name mismatch between `SwingExitResult` and `to_ui_dict()` crashes the WebSocket broadcast loop whenever a swing position is open.
2. **[CRITICAL] Weekend Blackout Bypass in 48-Hour Earnings Calendar (`Rule 4`)**: Hardcoded 2-calendar-day window in `is_blackout_active` fails to account for weekends, allowing Friday scans to stage entries in stocks reporting earnings on Monday or Tuesday.
3. **[MAJOR] Off-by-One Error in `holding_days` Lifecycle and Time-Stop Exit (`Rule 7c`)**: Initializing `pos.holding_days = 0` causes positions to be held for 6 full trading days instead of 5, and causes the UI to display "Day 1 of 5" twice.
4. **[MAJOR] Same-Symbol Simultaneous Exit and Entry Collision at 16:00 Close**: Exiting positions are permitted to qualify for immediate re-entry at the exact same market open.
5. **[MINOR] Potential Benchmark Desynchronization in `calculate_relative_strength_60d`**: Missing benchmark bar causes relative strength to fall back to yesterday's stock close.

No integrity violations (hardcoded test results or dummy facade implementations) were detected; the codebase contains genuine mathematical implementations.

---

## 1. Observation

### 1.1 Direct Observations & Evidence

1. **`to_ui_dict()` Attribute Mismatch Crash**:
   - Location: `backend/app/strategies/swing_panic_dip.py`, lines 822–827:
     ```python
     "exit_triggers": {
         "sma_5_cross": exit_eval.rule_7a_sma5_exit,
         "rsi_70_cross": exit_eval.rule_7b_rsi_exit,
         "time_stop_day_5": exit_eval.rule_7c_time_exit,
         "earnings_tomorrow": exit_eval.rule_4_earnings_exit,
     },
     ```
   - Definition: `backend/app/strategies/swing_indicators.py`, lines 234–250:
     ```python
     @dataclass
     class SwingExitResult:
         symbol: str
         date: date
         close: float
         sma_5: float
         exit_5_sma: bool                    # Today's close > 5 SMA
         rsi_2: float
         exit_rsi2_overbought: bool          # Today's RSI(2) > 70.0
         holding_days: int
         exit_time_stop: bool                # holding_days >= 5
         earnings_tomorrow: bool
         exit_earnings: bool                 # Earnings report tomorrow
         should_exit: bool
         primary_exit_reason: Optional[str] = None
     ```
   - Verbatim Reproduction:
     ```bash
     python3 -c "from backend.app.main import swing_strategy_engine; from backend.app.core.account import Position, PositionSide, TradingArm; from datetime import date; p = Position('MU', PositionSide.LONG, 100, 100, 105, arm=TradingArm.SWING, strategy_id='swing_panic_dip', stop_loss_price=95, entry_date=date.today(), holding_days=1); swing_strategy_engine.account.positions['MU'] = p; swing_strategy_engine.to_ui_dict()"
     ```
   - Verbatim Output:
     ```
     Traceback (most recent call last):
       File "<string>", line 1, in <module>
       File "/Users/mo/AutonomousDayTrader/backend/app/strategies/swing_panic_dip.py", line 823, in to_ui_dict
         "sma_5_cross": exit_eval.rule_7a_sma5_exit,
     AttributeError: 'SwingExitResult' object has no attribute 'rule_7a_sma5_exit'
     ```

2. **Weekend Blackout Window Bypass in `is_blackout_active`**:
   - Location: `backend/app/strategies/earnings_calendar.py`, lines 185–192:
     ```python
     else:
         as_of_date = as_of
         for ev in events:
             diff_days = (ev.report_date - as_of_date).days
             # Horizon of 48h spans today, tomorrow, and day after tomorrow
             if 0 <= diff_days <= 2:
                 return True
     ```
   - Verbatim Reproduction:
     ```bash
     python3 -c "from backend.app.strategies.earnings_calendar import EarningsCalendar, EarningsEvent; from datetime import date; cal = EarningsCalendar(); cal.add_event(EarningsEvent(symbol='AMD', report_date=date(2026, 9, 21), report_time='amc')); fri = date(2026, 9, 18); print('is_blackout_active on Fri for Mon earnings:', cal.is_blackout_active('AMD', fri))"
     ```
   - Verbatim Output:
     ```
     is_blackout_active on Fri for Mon earnings: False
     ```
   - In contrast, lines 210–218 in `has_earnings_tomorrow` explicitly account for Friday:
     ```python
     weekday = as_of_date.weekday()  # Monday = 0, Friday = 4
     if weekday == 4:
         next_trading_day = as_of_date + timedelta(days=3)
     ```

3. **Holding Days Lifecycle and Time Stop Off-by-One**:
   - Location: `backend/app/strategies/swing_panic_dip.py`, lines 499 & 819; `backend/app/main.py`, line 938:
     - Line 499: `pos.holding_days = 0` (on entry day).
     - Line 938 (`_check_session_boundary`): `pos.holding_days += 1`.
     - Line 819: `"holding_progress": f"Day {min(max(holding_days, 1), self.time_stop_days)} of {self.time_stop_days}"`.
     - Location: `backend/app/strategies/swing_indicators.py`, line 380:
       `exit_time = (holding_days >= 5)`.
   - Progression:
     - Day 1 (Entry): `holding_days = 0` -> UI: `Day 1 of 5`.
     - Day 2: `holding_days = 1` -> UI: `Day 1 of 5` (duplicate!).
     - Day 3: `holding_days = 2` -> UI: `Day 2 of 5`.
     - Day 4: `holding_days = 3` -> UI: `Day 3 of 5`.
     - Day 5 (5th trading session held): `holding_days = 4` -> UI: `Day 4 of 5`. At 16:00 close, `holding_days >= 5` is `False`! Time stop does NOT trigger.
     - Day 6 (Held 6th session): `holding_days = 5` -> UI: `Day 5 of 5`. At 16:00 close, `holding_days >= 5` is `True`. Staged for exit at Day 7 open (09:30).

4. **Same-Symbol Exit & Entry Collision**:
   - Location: `backend/app/strategies/swing_panic_dip.py`, lines 264–282:
     ```python
     exiting_symbols = {e.symbol for e in staged_exits}
     surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
     available_slots = self.max_concurrent_positions - len(surviving_positions)
     ...
     for sym in self.symbols:
         if sym in surviving_positions:
             continue
     ```
   - An exiting symbol is excluded from `surviving_positions`, making it eligible to be qualified for entry during the same 16:00 close scan, staging both a SELL and a BUY for the next morning.

5. **Mathematical Precision & Zero-Lookahead Invariants**:
   - `calculate_sma(prices, period)`: Strictly trailing window `prices[-period:]`. Sum / period.
   - `calculate_rsi2(prices)`: Wilder's smoothed RSI-2 on daily closes with recursion $(AvgGain_{k-1} + Gain_k)/2$.
   - `calculate_daily_atr(bars, period=14)`: Wilder's smoothed ATR $(ATR_{k-1} \times 13 + TR_k)/14$.
   - 16:00 ET close qualification: Consumes finalized daily bars `date <= session_date`. Sizing is not calculated at 16:00 (`shares = None`, only `target_notional = 25000.0`).
   - 09:30 ET open execution: Evaluates $\lfloor 25000 / P_{\text{open}} \rfloor$ strictly using live 09:30 open bar. Stop loss placed at $P_{\text{open}} - 2.5 \times ATR_{14}(t)$ using closed ATR from session $t$.

---

## 2. Logic Chain

1. **Integrity Assessment (Observations 1.1, 1.5)**:
   The mathematical functions in `swing_indicators.py` calculate true arithmetic values across historical seed data (`daily_bars_seed.json`), and empirical mutation tests confirmed that introducing future data does not alter past outputs. No hardcoded or dummy mocks were detected.
2. **WebSocket & UI Fragility (Observation 1.1)**:
   `main.py` broadcasts UI state every second via `broadcast_ui_state()`, which serializes `"swing": swing_strategy_engine.to_ui_dict()`. Because `to_ui_dict()` accesses attributes that do not exist on `SwingExitResult`, the first fill of a swing order causes all subsequent state broadcasts to crash with `AttributeError`, knocking out telemetry to the Obsidian dark dashboard.
3. **Earnings Blackout Window Incompleteness (Observation 1.2)**:
   Rule 4 mandates that no position be entered if earnings fall within 48 hours. Because trades qualify at 16:00 ET Friday to execute at 09:30 ET Monday, an earnings announcement on Monday or Tuesday falls within 0 to 31 hours of entry execution. Checking only `0 <= diff_days <= 2` calendar days ignores the weekend gap, allowing entries right before earnings releases.
4. **Time Stop Specification Violation (Observation 1.3)**:
   Rule 7c specifies an exit when a position has been held for 5 trading days. Because `holding_days` begins at 0 and increments at session boundaries, `holding_days` evaluates to 4 at the end of the 5th trading session, delaying the exit until Day 7 open (a 6-day hold) and confusing operators with duplicate "Day 1 of 5" UI meters.
5. **Execution Churn Risk (Observation 1.4)**:
   Allowing an exiting symbol to be evaluated as an entry candidate in the same cycle can stage an exit and an entry simultaneously, resulting in a liquidation and immediate re-entry at 09:30 open.

---

## 3. Findings

### [Critical] Finding 1: `AttributeError` in `to_ui_dict()` on Active Swing Positions
- **Where**: `backend/app/strategies/swing_panic_dip.py`, lines 823–826
- **What**: Attribute names mismatch `SwingExitResult` dataclass:
  - `exit_eval.rule_7a_sma5_exit` -> should be `exit_eval.exit_5_sma`
  - `exit_eval.rule_7b_rsi_exit` -> should be `exit_eval.exit_rsi2_overbought`
  - `exit_eval.rule_7c_time_exit` -> should be `exit_eval.exit_time_stop`
  - `exit_eval.rule_4_earnings_exit` -> should be `exit_eval.exit_earnings`
- **Why**: Crashes `broadcast_ui_state` and `GET /api/swing/state` whenever an active swing position is held.
- **Suggestion**: Update `to_ui_dict()` in `swing_panic_dip.py` to use `exit_eval.exit_5_sma`, `exit_eval.exit_rsi2_overbought`, `exit_eval.exit_time_stop`, and `exit_eval.exit_earnings`. Alternatively, add alias properties to `SwingExitResult`.

### [Critical] Finding 2: Weekend Calendar Gap in 48-Hour Earnings Blackout Window
- **Where**: `backend/app/strategies/earnings_calendar.py`, lines 185–192
- **What**: `is_blackout_active` uses `0 <= diff_days <= 2` without weekend or trading-day awareness.
- **Why**: Evaluating on Friday at 16:00 close for Monday 09:30 open fails to detect Monday earnings (`diff_days == 3`) or Tuesday earnings (`diff_days == 4`), directly violating Rule 4.
- **Suggestion**: Add weekday adjustment in `is_blackout_active` similar to `has_earnings_tomorrow`: if `as_of.weekday() == 4` (Friday), blackout should cover through Tuesday (+4 calendar days).

### [Major] Finding 3: Off-by-One in `holding_days` Counter and Time-Stop Exit
- **Where**: `backend/app/strategies/swing_panic_dip.py`, lines 499 & 819; `backend/app/main.py`, line 938
- **What**: Initializing `pos.holding_days = 0` upon 09:30 entry causes `pos.holding_days` to be 4 at Friday close (5th trading session held).
- **Why**: Delays time-stop exit to the 7th trading day (6-day hold) and displays "Day 1 of 5" for two consecutive days in the operator UI.
- **Suggestion**: Initialize `pos.holding_days = 1` on entry day (representing Day 1 of the hold), or adjust exit condition to `holding_days >= 4` if 0-indexed. Initializing to 1 aligns cleanly with the UI display `Day 1 of 5` through `Day 5 of 5`.

### [Major] Finding 4: Simultaneous Exit and Re-Entry Staging for Same Symbol
- **Where**: `backend/app/strategies/swing_panic_dip.py`, lines 264–282
- **What**: In `evaluate_market_close`, exiting symbols are not excluded from entry candidates.
- **Why**: An oversold stock hitting a time-stop exit stages both a SELL and a BUY for 09:30 open, instantly churning the position.
- **Suggestion**: In `evaluate_market_close`, add `if sym in exiting_symbols or sym in active_positions: continue` to candidate evaluation.

### [Minor] Finding 5: Potential Benchmark Desynchronization in `calculate_relative_strength_60d`
- **Where**: `backend/app/strategies/swing_indicators.py`, lines 186–200
- **What**: `common_dates[-1]` silently uses yesterday's date if benchmark data is missing today.
- **Why**: Evaluates stock return without today's dip if QQQ bar is delayed or missing.
- **Suggestion**: Assert or verify `common_dates[-1] == stock_bars[-1].date`; return `(0.0, 0.0, False)` if the latest bar is not aligned.

---

## 4. Verified Claims

- **200 SMA Calculation**: Verified strictly uses trailing 200 closed daily bars (`date <= session_date`). -> **PASS**
- **Connors RSI-2 Calculation**: Verified Wilder's smoothing recursion on daily closes; immune to future data leakage. -> **PASS**
- **14-day Daily ATR**: Verified J. Welles Wilder True Range with 14-period smoothing and 0.01 floor. -> **PASS**
- **Zero-Lookahead at 16:00 ET Close**: Verified tomorrow's open price ($P_{\text{open}, t+1}$) is NEVER queried or assumed at 16:00 close; integer share count $\lfloor 25000 / P_{\text{open}} \rfloor$ evaluated only at 09:30 ET open. -> **PASS**
- **Emergency Stop Distance**: Verified uses closed $ATR_{14}(t)$ from qualification close to place stop at $P_{\text{open}} - 2.5 \times ATR_{14}$. -> **PASS**
- **Intraday Emergency Stop Monitoring**: Verified continuous monitoring against `bar.low <= stop_price` with immediate market liquidation and symbol release. -> **PASS**
- **Offline Seed & Graceful Fallback**: Verified `DailyBarStore` loads 265 bars per symbol; `EarningsCalendar` degrades gracefully on network timeout without crashing. -> **PASS**

---

## 5. Adversarial Challenge Report

### Stress-Testing Results

| Scenario | Expected Behavior | Actual Behavior | Result |
|---|---|---|---|
| Active swing position queried via `to_ui_dict()` | Serializes UI dict with exit triggers | Raises `AttributeError: 'SwingExitResult' object has no attribute 'rule_7a_sma5_exit'` | **FAIL (Finding 1)** |
| Friday close scan with Monday earnings report | `is_blackout_active` returns `True`, vetoing entry | Returns `False` due to `diff_days == 3 > 2` check | **FAIL (Finding 2)** |
| 5-day holding lifecycle evaluated for Rule 7c time exit | Exits at open of Day 6 after 5 days held | Exits at open of Day 7 after 6 days held | **FAIL (Finding 3)** |
| Position triggers time exit while RSI(2) < 10 | Exits position; suppresses re-entry on same symbol | Stages both SELL and BUY at open for same symbol | **FAIL (Finding 4)** |
| Future bars injected with extreme prices | Evaluation as of cutoff date unchanged | Output strictly identical before and after mutation | **PASS** |
| Open price exceeds $25,000 slot size | Yields 0 shares; cancels entry safely | Order dropped safely with warning logged | **PASS** |

---

## 6. Caveats

- **Seed Data Scope**: The current seed fixture covers 265 trading days ending 2026-09-22. In live deployment, `DailyBarAggregator` must continuously accumulate bars at each session close.
- **Review Boundary**: In accordance with the Review-Only constraint, this reviewer diagnosed, reproduced, and documented all issues without editing application implementation files.

---

## 7. Conclusion

Milestone M9B and M9C implementations achieve high mathematical fidelity and zero lookahead bias in their indicator algorithms. However, due to the **two Critical issues** (WebSocket runtime crash on active positions and weekend earnings blackout bypass) and **two Major issues** (time stop off-by-one lifecycle and same-symbol exit/entry collision), the formal verdict is **`REQUEST_CHANGES`**.

---

## 8. Verification Method

To independently reproduce all findings and verify remediations:

1. **Reproduce `to_ui_dict()` AttributeError (Finding 1)**:
   ```bash
   python3 -c "from backend.app.main import swing_strategy_engine; from backend.app.core.account import Position, PositionSide, TradingArm; from datetime import date; p = Position('MU', PositionSide.LONG, 100, 100, 105, arm=TradingArm.SWING, strategy_id='swing_panic_dip', stop_loss_price=95, entry_date=date.today(), holding_days=1); swing_strategy_engine.account.positions['MU'] = p; swing_strategy_engine.to_ui_dict()"
   ```
   *Expected finding*: Raises `AttributeError: 'SwingExitResult' object has no attribute 'rule_7a_sma5_exit'`.

2. **Reproduce Weekend Earnings Blackout Bypass (Finding 2)**:
   ```bash
   python3 -c "from backend.app.strategies.earnings_calendar import EarningsCalendar, EarningsEvent; from datetime import date; cal = EarningsCalendar(); cal.add_event(EarningsEvent(symbol='AMD', report_date=date(2026, 9, 21), report_time='amc')); fri = date(2026, 9, 18); print('Blackout active:', cal.is_blackout_active('AMD', fri))"
   ```
   *Expected finding*: Prints `Blackout active: False` (should be `True`).

3. **Run Full Test Suite**:
   ```bash
   pytest backend/tests/test_swing_indicators.py backend/tests/test_swing_strategy.py backend/tests/test_swing_flattening_exemption.py -v
   ```
