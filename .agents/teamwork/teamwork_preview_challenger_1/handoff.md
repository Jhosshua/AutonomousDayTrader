# Handoff Report — Challenger 1: Mathematical, Lookahead & Boundary Stress Audit

**Agent**: Challenger 1 (`teamwork_preview_challenger_1`)  
**Mission**: Milestone M9D (Pass 1: Mathematical & Lookahead Audit) — Adversarial Stress Testing of `swing_indicators.py`, `earnings_calendar.py`, and `swing_panic_dip.py`.  
**Formal Verdict**: `REQUEST_CHANGES`  
**Overall Risk Assessment**: `CRITICAL`  

---

## 1. Observation

### 1.1 Direct Code Observations & Verbatim Code Quotes

#### Observation O1: Critical Field Name Mismatch in UI Serialization (`swing_panic_dip.py`)
In `backend/app/strategies/swing_panic_dip.py` (lines 822–827), `to_ui_dict()` serializes `exit_triggers` by accessing fields on `exit_eval`:
```python
822:                 "exit_triggers": {
823:                     "sma_5_cross": exit_eval.rule_7a_sma5_exit,
824:                     "rsi_70_cross": exit_eval.rule_7b_rsi_exit,
825:                     "time_stop_day_5": exit_eval.rule_7c_time_exit,
826:                     "earnings_tomorrow": exit_eval.rule_4_earnings_exit,
827:                 },
```
However, in `backend/app/strategies/swing_indicators.py` (lines 234–250), `SwingExitResult` defines the fields as:
```python
234: @dataclass
235: class SwingExitResult:
...
241:     exit_5_sma: bool                    # Today's close > 5 SMA
...
243:     exit_rsi2_overbought: bool          # Today's RSI(2) > 70.0
244:     holding_days: int
245:     exit_time_stop: bool                # holding_days >= 5
246:     earnings_tomorrow: bool
247:     exit_earnings: bool                 # Earnings report tomorrow
248:     should_exit: bool
```
The attributes `rule_7a_sma5_exit`, `rule_7b_rsi_exit`, `rule_7c_time_exit`, and `rule_4_earnings_exit` do NOT exist on `SwingExitResult`. When any active swing position exists in `account.positions`, calling `to_ui_dict()` crashes with:
`AttributeError: 'SwingExitResult' object has no attribute 'rule_7a_sma5_exit'`.

#### Observation O2: False Earnings Blackout for Past Morning Earnings (`earnings_calendar.py`)
In `backend/app/strategies/earnings_calendar.py` (lines 177–183):
```python
177:                 diff_seconds = (report_dt - as_of).total_seconds()
178:                 if 0 <= diff_seconds <= horizon_hours * 3600.0:
179:                     return True
180:                 # If report date is within 2 calendar days, enforce safe-side blackout
181:                 diff_days = (ev.report_date - as_of.date()).days
182:                 if 0 <= diff_days <= 2:
183:                     return True
```
When a company reports earnings Before Market Open (BMO, 08:30 ET) on Day $T$, and signal qualification evaluates at 16:00 ET close on Day $T$, `diff_seconds = -7.5 \times 3600 < 0` (event has already passed). However, `diff_days = (date_T - date_T).days = 0`. Because `0 <= 0 <= 2` evaluates to `True`, line 182 immediately returns `True` (blackout active). A company that reported earnings in the morning and drops to RSI(2) < 10 by close is falsely vetoed.

#### Observation O3: 48-Hour Horizon Overridden by Calendar Day Check (`earnings_calendar.py`)
In `backend/app/strategies/earnings_calendar.py` (lines 180–183), the check `0 <= diff_days <= 2` overrides `horizon_hours`. If an event is scheduled on Day $T+2$ at 16:30 AMC, and evaluated on Day $T$ at 09:00 ET, `diff_seconds = 55.5 \times 3600 > 48.0 \times 3600`. Yet because `diff_days = 2`, `is_blackout_active` returns `True`, enforcing a ~55-to-60-hour blackout rather than the required 48.0-hour window.

#### Observation O4: Holding Days Counter Lifecycle Off-by-One Time Stop (`swing_panic_dip.py` & `main.py`)
In `backend/app/strategies/swing_panic_dip.py` (line 499):
```python
499:                     pos.holding_days = 0
```
In `backend/app/main.py` (lines 936–939), `pos.holding_days` is incremented exclusively during session boundary processing (`_check_session_boundary`):
```python
936:     for sym, pos in account.positions.items():
937:         if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
938:             pos.holding_days += 1
```
For a position entered Monday at 09:30 open:
- Monday 16:00 close: 1 full session held, but `pos.holding_days == 0`.
- Tuesday 16:00 close: 2 sessions held, `pos.holding_days == 1`.
- Wednesday 16:00 close: 3 sessions held, `pos.holding_days == 2`.
- Thursday 16:00 close: 4 sessions held, `pos.holding_days == 3`.
- Friday 16:00 close: 5 full sessions held, but `pos.holding_days == 4`.
In `evaluate_swing_exit` (line 380): `exit_time = (holding_days >= 5)`. On Friday close, `4 >= 5` is `False`. The position is held over the weekend and through Monday, reaching `holding_days == 5` on Monday close, and exiting Tuesday 09:30 open (Day 7, 6 full sessions held).

#### Observation O5: Simultaneous Exit and Entry Staging Collision (`swing_panic_dip.py`)
In `backend/app/strategies/swing_panic_dip.py` (lines 264–286):
```python
264:         exiting_symbols = {e.symbol for e in staged_exits}
265:         surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
...
280:                 # Skip if already held (and not exiting)
281:                 if sym in surviving_positions:
282:                     continue
283: 
284:                 # Skip if already staged
285:                 if self.staged_manager.is_staged_for_entry(sym):
286:                     continue
```
If an active position reaches holding days == 5 (time stop exit), it is placed into `staged_exits` and removed from `surviving_positions`. If that stock also experienced a sharp dip (RSI(2) < 10.0, close > 200 SMA, RS >= QQQ), `evaluate_market_close` stages a BUY entry for the stock while it is ALREADY staged for SELL. At 09:30 open, the engine sells the stock and immediately re-buys it.

#### Observation O6: Negative Stop Price Disables Emergency Stop (`swing_panic_dip.py`)
In `backend/app/strategies/swing_panic_dip.py` (line 442):
```python
442:             stop_price = round(open_price - stop_distance, 2)
```
In `check_intraday_emergency_stops` (line 543):
```python
543:             if stop_price is None or stop_price <= 0.0:
544:                 continue
```
If a high-volatility stock has $2.5 \times ATR_{14} \ge P_{\text{open}}$, `stop_price` becomes $\le 0.0$. Line 543 silently skips the position, permanently disabling the emergency stop.

#### Observation O7: Stop Tightening Allows Widening (`swing_panic_dip.py`)
In `backend/app/strategies/swing_panic_dip.py` (lines 753–771):
`tighten_stop(symbol, new_stop)` checks `if new_stop >= pos.market_price: return False`, but does NOT verify `new_stop > old_stop`. An operator can widen the stop from $95 to $90.

---

## 2. Logic Chain

1. **Impact of O1 on Runtime Stability**:
   - `main.py` schedules `broadcast_ui_state` to stream UI metrics over WebSockets to the frontend.
   - `to_ui_dict()` iterates through `active_positions`. For each position, it attempts to read `exit_eval.rule_7a_sma5_exit`.
   - Because `rule_7a_sma5_exit` does not exist on `SwingExitResult` (O1), any fill of a swing position will cause `to_ui_dict()` to throw an unhandled `AttributeError`. This crashes WebSocket broadcasts and GET `/api/swing/state`.
2. **Impact of O2 on Trading Fidelity**:
   - R1.4 and R3 dictate a 48-hour blackout window: "If the company reports earnings within the next 48 hours, do not enter."
   - When a company announces earnings at 08:30 BMO, earnings uncertainty has resolved. If the stock drops 10% on market reaction, RSI(2) falls below 10.0, and close remains above 200 SMA, it represents a valid panic-dip setup.
   - Because `0 <= diff_days <= 2` matches `diff_days == 0` (O2), `is_blackout_active` returns `True` despite the event being in the past (`diff_seconds < 0`), falsely rejecting valid trade entries.
3. **Impact of O4 on Holding Period Fidelity**:
   - Rule 7c specifies a 5-day time stop exit: "The position has been held for 5 trading days (time stop)."
   - Because `holding_days` is initialized to 0 upon fill on Day 1 and only increments across the overnight session boundary (O4), at the end of Day 5's session (Friday close), `holding_days` is 4.
   - The exit does not fire on Friday close. The position is held over the weekend and all through Monday (Day 6), exiting on Tuesday open (Day 7). This represents an off-by-one holding period error of 1 full trading day and 2 extra calendar days.
4. **Impact of O5 on Capital Efficiency & Churn**:
   - Because `surviving_positions` filters out `exiting_symbols` (O5), a stock scheduled to exit at tomorrow's open is eligible for entry evaluation at the same 16:00 close.
   - When a 5-day time stop coincides with an oversold reading (RSI(2) < 10), the engine stages both SELL and BUY orders for the same symbol at 09:30 open. This causes needless transaction fees and slippage.
5. **Robustness of Indicator Calculations**:
   - The empirical tests confirmed that `DailyBarStore.get_bars(symbol, as_of=eval_date)` strictly prevents lookahead bias. Appending 50 future volatile bars resulted in zero drift in 200 SMA, RSI(2), ATR(14), and 60d RS.
   - Edge case protection against division by zero in ATR (`max(0.01, ...)`), RSI(2) fallback (`50.0`), and SMA length check (`len < 200 -> 0.0`) are mathematically sound.

---

## 3. Caveats

- **Seed Data Granularity**: The seed fixtures provide 265 daily OHLCV bars. In live operation, daily bars are aggregated via `DailyBarAggregator` from 1-minute streams.
- **Timezone Normalization in `DailyBarAggregator`**: `bar.timestamp.date()` uses the timestamp's raw date. If `BarEvent.timestamp` is in UTC, bars between 20:00 UTC and 23:59 UTC (after-hours) will register as tomorrow's date. In regular trading hours (13:30 to 20:00 UTC), UTC date matches ET date.
- **No other caveats.**

---

## 4. Conclusion & Formal Verdict

### Formal Verdict: `REQUEST_CHANGES`

While the core indicator mathematics in `swing_indicators.py` (200 SMA, 60d RS vs QQQ, Wilder RSI(2), and 14 ATR) exhibit rigorous zero lookahead guarantees and numerical stability, the integration has **1 CRITICAL bug**, **2 HIGH severity defects**, and **3 MEDIUM severity issues** that MUST be remediated before production readiness:

1. **CRITICAL**: Fix attribute names in `backend/app/strategies/swing_panic_dip.py` lines 823–826 to match `SwingExitResult`:
   - Change `exit_eval.rule_7a_sma5_exit` $\to$ `exit_eval.exit_5_sma`
   - Change `exit_eval.rule_7b_rsi_exit` $\to$ `exit_eval.exit_rsi2_overbought`
   - Change `exit_eval.rule_7c_time_exit` $\to$ `exit_eval.exit_time_stop`
   - Change `exit_eval.rule_4_earnings_exit` $\to$ `exit_eval.exit_earnings`
2. **HIGH**: Fix `backend/app/strategies/earnings_calendar.py` line 180 to ensure past events (`report_dt < as_of` or `diff_seconds < 0`) NEVER trigger a forward earnings blackout.
3. **HIGH**: Fix `holding_days` counter in `swing_panic_dip.py` / `main.py` so that a position held from Monday 09:30 through Friday 16:00 close is recognized as `holding_days == 5`, staging the exit for Monday open.
4. **MEDIUM**: In `swing_panic_dip.py`, prevent a symbol from being evaluated for entry if `self.staged_manager.is_staged_for_exit(sym)` is True.
5. **MEDIUM**: In `swing_panic_dip.py`, ensure `stop_price = max(0.01, round(open_price - stop_distance, 2))` so a high ATR stop never becomes $\le 0.0$.
6. **LOW**: In `tighten_stop`, verify `new_stop > old_stop`.

---

## 5. Verification Method

All findings have been independently reproduced with automated tests in `backend/tests/test_adversarial_challenger_1.py`.

### Independent Verification Commands:
```bash
cd /Users/mo/AutonomousDayTrader

# 1. Run Challenger 1's adversarial stress test suite (21 empirical tests):
pytest backend/tests/test_adversarial_challenger_1.py -v

# 2. Run the full swing indicator, strategy, and flattening exemption suites:
pytest backend/tests/test_adversarial_challenger_1.py backend/tests/test_swing_indicators.py backend/tests/test_swing_strategy.py backend/tests/test_swing_flattening_exemption.py -v

# 3. Verify port hygiene:
bash scripts/verify_port_hygiene.sh
```

### Invalidation Conditions:
- If `engine.to_ui_dict()` can be executed with an active swing position in `account.positions` without raising `AttributeError`, Finding 1 is invalidated.
- If `cal.is_blackout_active("AAPL", datetime(2026, 9, 23, 16, 0), horizon_hours=48.0)` returns `False` when AAPL reported earnings BMO at 08:30 on 2026-09-23, Finding 2 is invalidated.
