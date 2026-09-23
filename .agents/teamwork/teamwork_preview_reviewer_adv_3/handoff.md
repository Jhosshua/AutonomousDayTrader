# Handoff Report — Reviewer 3 (Adversarial Pass 3: Execution Timing & Order Lifecycle Audit)

## Review Summary

**Verdict**: **REQUEST_CHANGES**

**Integrity Audit**: **PASS** (Zero hardcoded test cheats, zero facade or mock implementations, zero fabricated verification outputs).

**Overall Risk Assessment**: **HIGH** (Severe execution timing race condition at 09:30 open, inverted candle evaluation on entry, and off-by-one holding days time stop delay).

---

## 1. Observation

### Observation 1: 09:30 Open Execution Race Condition & Stale Price Fill
In `backend/app/main.py` lines 1260–1269:
```python
    # 09:30 ET Market Open Execution for Staged Swing Orders
    bar_et = bar.timestamp.astimezone(ET_TZ) if bar.timestamp.tzinfo else bar.timestamp
    if bar_et.time().hour == 9 and bar_et.time().minute == 30:
        if swing_staged_order_manager.get_staged_orders():
            open_prices = {bar.symbol.upper(): bar.open}
            for sym, p in latest_market_prices.items():
                if sym not in open_prices:
                    open_prices[sym] = p
            swing_strategy_engine.execute_market_open(open_prices, bar.timestamp)
```
When bars arrive sequentially at 09:30 ET:
- If an intraday watchlist symbol (e.g. `NVDA` or `AAPL`) arrives first, `open_prices` contains `{ "AAPL": 225.0 }`.
- For any staged swing symbol (e.g. `LRCX` or `KLAC`) whose 09:30 bar has not yet arrived, `open_prices` falls back to `latest_market_prices[sym]`.
- At 09:30:00, `latest_market_prices[sym]` is yesterday's 15:59 close price.
- In `backend/app/strategies/swing_panic_dip.py` lines 426–443:
```python
    open_price = open_prices.get(sym)
    if not open_price or open_price <= 0.0:
        errors.append(f"Missing open price for {sym}; cannot execute staged entry")
        continue

    # Calculate integer shares: floor($25,000 / P_open)
    qty = int(math.floor(self.slot_notional / open_price))
```
Reproduction command:
```bash
python3 -c '
from datetime import date, datetime, timezone
from backend.app.core.account import PaperTradingAccount
from backend.app.core.engine import ExecutionEngine
from backend.app.strategies.swing_panic_dip import SwingStrategyEngine, SwingStagedOrderManager

acct = PaperTradingAccount(initial_cash=50000.0)
engine = ExecutionEngine(account=acct)
mgr = SwingStagedOrderManager()
swing_engine = SwingStrategyEngine(account=acct, execution_engine=engine, staged_manager=mgr)

mgr.stage_buy("KLAC", target_notional=25000.0, daily_atr=5.0, signal_date=date(2026, 9, 22), reason="PANIC_DIP")
# Stale price in latest_market_prices is 700.0 (yesterday close), but actual open is 750.0
stale_open_prices = {"AAPL": 225.0, "KLAC": 700.0}
res = swing_engine.execute_market_open(stale_open_prices, datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc))
print("KLAC filled at:", res["entries"][0]["price"], "shares:", res["entries"][0]["shares"])
'
```
Result: `KLAC filled at: 700.0 shares: 35`.
When KLAC actually opens at $750.00, it is prematurely filled at yesterday's close ($700.00) with 35 shares instead of 33 shares ($\lfloor 25000 / 750 \rfloor = 33$), and its stop price is set to $687.50 instead of $737.50 ($50.00 too low). The order is purged from `staged_orders`, so when KLAC's actual 09:30 open bar arrives, it cannot execute properly.
Conversely, if `latest_market_prices` is empty or missing the symbol, `open_prices.get(sym)` returns `None`, logging `Missing open price for LRCX; cannot execute staged entry`.

### Observation 2: Execution Order in `handle_bar_event`: `on_bar` Precedes `execute_market_open`
In `backend/app/main.py` lines 1254–1269:
```python
    # Swing Data Aggregation & Real-Time Emergency Stop Check
    swing_set = set(settings.SWING_SYMBOLS) | {settings.SWING_BENCHMARK}
    if bar.symbol.upper() in swing_set:
        daily_bar_aggregator.on_minute_bar(bar)
    swing_strategy_engine.on_bar(bar)

    # 09:30 ET Market Open Execution for Staged Swing Orders
    bar_et = bar.timestamp.astimezone(ET_TZ) if bar.timestamp.tzinfo else bar.timestamp
    if bar_et.time().hour == 9 and bar_et.time().minute == 30:
        if swing_staged_order_manager.get_staged_orders():
            ...
            swing_strategy_engine.execute_market_open(open_prices, bar.timestamp)
```
On the opening 09:30:00 bar of a newly entered position:
- `swing_strategy_engine.on_bar(bar)` runs at line 1258, before the position exists. It finds no active position and exits immediately.
- `execute_market_open` runs at line 1268, creating the position at `bar.open` and setting `stop_loss_price = bar.open - 2.5 * ATR`.
- `on_bar(bar)` is never invoked for this 09:30:00 bar again.
- If the 09:30 candle experiences an opening volatility flush where `bar.low <= stop_loss_price`, the breach is completely unmonitored during the opening candle until the 09:31:00 bar arrives.

### Observation 3: Off-By-One In Holding Days Accounting & Time Stop Delay
In `backend/app/strategies/swing_panic_dip.py` line 499:
```python
pos.holding_days = 0
```
In `backend/app/main.py` lines 936–939 (`_check_session_boundary`):
```python
for sym, pos in account.positions.items():
    if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
        pos.holding_days += 1
```
In `backend/app/strategies/swing_indicators.py` lines 380:
```python
exit_time = (holding_days >= 5)
```
In `backend/app/strategies/swing_panic_dip.py` line 819:
```python
"holding_progress": f"Day {min(max(holding_days, 1), self.time_stop_days)} of {self.time_stop_days}"
```
In `frontend/components/ActiveSwingPositionsTable.tsx` line 183:
```typescript
const holdingDays = Math.max(1, pos.holding_days ?? 1);
```
Reproduction command:
```bash
python3 -c '
from backend.app.strategies.swing_indicators import evaluate_swing_exit
# Trade entered Monday 09:30 ET: Mon (Day 1), Tue (Day 2), Wed (Day 3), Thu (Day 4), Fri (Day 5).
# On Friday 16:00 close, holding_days is 4:
exit_fri = evaluate_swing_exit("GS", [], holding_days=4, earnings_tomorrow=False)
print("Friday close (Day 5, holding_days=4) exit_time_stop:", exit_fri.exit_time_stop, "should_exit:", exit_fri.should_exit)

exit_mon = evaluate_swing_exit("GS", [], holding_days=5, earnings_tomorrow=False)
print("Monday close (Day 6, holding_days=5) exit_time_stop:", exit_mon.exit_time_stop, "should_exit:", exit_mon.should_exit)
'
```
Result:
`Friday close (Day 5, holding_days=4) exit_time_stop: False should_exit: False`
`Monday close (Day 6, holding_days=5) exit_time_stop: True should_exit: True`
Rule 7c specifies that a position held for 5 trading days must exit at the next open (Monday 09:30 ET). Because `pos.holding_days` begins at 0 on entry day and increments only at session rollover, it reaches only 4 on Friday close. It fails to exit on Friday close, is held through the weekend and Monday close, and only exits Tuesday open (Day 7 morning / 6 trading days held).
Additionally, `max(1, pos.holding_days)` causes the UI to display "Day 1 of 5" for both Monday and Tuesday.

### Observation 4: Unratcheted `tighten_stop` Allows Widening Downside Risk
In `backend/app/strategies/swing_panic_dip.py` lines 760–770:
```python
        if new_stop <= 0:
            log.warning(f"Cannot tighten stop for {sym}: invalid stop price {new_stop}")
            return False
        if pos.market_price > 0 and new_stop >= pos.market_price:
            log.warning(f"Cannot tighten stop for {sym}: stop {new_stop} >= market price {pos.market_price}")
            return False
        old_stop = getattr(pos, "stop_loss_price", None)
        pos.stop_loss_price = round(new_stop, 2)
        log.info(f"Tightened swing stop for {sym}: {old_stop} -> {pos.stop_loss_price}")
        return True
```
Reproduction command:
```bash
python3 -c '
from backend.app.core.account import PaperTradingAccount, Position, PositionSide, TradingArm
from backend.app.core.engine import ExecutionEngine
from backend.app.strategies.swing_panic_dip import SwingStrategyEngine

acct = PaperTradingAccount(initial_cash=50000.0)
engine = ExecutionEngine(account=acct)
swing_engine = SwingStrategyEngine(account=acct, execution_engine=engine)
pos = Position(symbol="AMD", side=PositionSide.LONG, shares=100, avg_entry_price=150.0, market_price=150.0, arm=TradingArm.SWING, stop_loss_price=140.0)
acct.positions["AMD"] = pos

success = swing_engine.tighten_stop("AMD", 120.0)
print("Loosen stop result:", success, "New stop:", pos.stop_loss_price)
'
```
Result: `Loosen stop result: True New stop: 120.0`.
The method allows widening the stop downward, contrary to risk ratcheting standards.

### Observation 5: Symbol Reservation Prematurely Released on Exit Failure
In `backend/app/strategies/swing_panic_dip.py` lines 404–408:
```python
            except Exception as e:
                log.error(f"Error executing swing exit for {sym}: {e}")
                errors.append(f"Exit error for {sym}: {e}")
            finally:
                self.staged_manager.remove_staged_order(exit_order.order_id)
                if self.release_symbol_cb:
                    self.release_symbol_cb(sym)
```
If an exception occurs during order fill execution (e.g. broker error, ledger failure), the position remains open in `account.positions`. However, `finally:` releases the symbol from `swing_reserved_symbols`. Intraday day trading can then enter positions in that symbol while an unclosed swing position exists, violating mutual exclusion.

### Observation 6: Simultaneous Exit and Re-entry Staged for Same Symbol
In `backend/app/strategies/swing_panic_dip.py` lines 264–285:
```python
        exiting_symbols = {e.symbol for e in staged_exits}
        surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
        available_slots = self.max_concurrent_positions - len(surviving_positions)
...
        if available_slots > 0:
            for sym in self.symbols:
                if sym in surviving_positions:
                    continue
```
Reproduction command:
```bash
python3 -c '
from datetime import date, timedelta
from backend.app.core.account import PaperTradingAccount, Position, PositionSide, TradingArm
from backend.app.core.engine import ExecutionEngine
from backend.app.strategies.swing_indicators import DailyBar, DailyBarStore
from backend.app.strategies.swing_panic_dip import SwingStrategyEngine, SwingStagedOrderManager

store = DailyBarStore()
base_d = date(2026, 9, 23)
for i in range(205):
    d = base_d - timedelta(days=204 - i)
    store.append_bar(DailyBar("QQQ", d, 400.0, 401.0, 399.0, 400.0 - (204 - i)*0.5, 1000000, True))
    c = 100.0 + i * 0.8
    store.append_bar(DailyBar("LRCX", d, c, c+1, c-1, c, 1000000, True))

bars = store.get_bars("LRCX")
bars[-2].close = round(bars[-3].close - 10.0, 2)
bars[-2].high = round(bars[-3].close - 8.0, 2)
bars[-2].low = round(bars[-3].close - 12.0, 2)
bars[-1].close = round(bars[-2].close - 15.0, 2)
bars[-1].high = round(bars[-2].close - 12.0, 2)
bars[-1].low = round(bars[-2].close - 18.0, 2)

acct = PaperTradingAccount(initial_cash=50000.0)
pos = Position("LRCX", PositionSide.LONG, 30, 250.0, bars[-1].close, arm=TradingArm.SWING, holding_days=5)
acct.positions["LRCX"] = pos
engine = ExecutionEngine(account=acct)
mgr = SwingStagedOrderManager()
swing_engine = SwingStrategyEngine(account=acct, execution_engine=engine, bar_store=store, staged_manager=mgr, symbols=["LRCX"])
res = swing_engine.evaluate_market_close(base_d)
print("Staged exits:", [e["symbol"] for e in res["staged_exits"]])
print("Staged entries:", [e["symbol"] for e in res["staged_entries"]])
'
```
Result:
`Staged exits: ['LRCX']`
`Staged entries: ['LRCX']`
If a position reaches Day 5 time stop on a day when it also plunges into panic dip territory (RSI(2) < 10), it stages both a SELL and a BUY for next open, selling and immediately repurchasing the same stock at the same open tick.

---

## 2. Logic Chain

1. **Stale Fill Price at Open (Observation 1)**:
   In event-driven streaming feeds, 1-minute bars arrive per symbol at asynchronous intervals. In `main.py`, invoking `execute_market_open` on the first bar that happens to have `09:30` timestamp causes all other staged swing symbols to fall back to `latest_market_prices`. Because `latest_market_prices` contains yesterday's close, the swing trade is executed using stale prices rather than the symbol's actual opening auction price.
2. **Inverted Opening Candle Risk Evaluation (Observation 2)**:
   Executing `on_bar` before `execute_market_open` in `handle_bar_event` means the opening candle is not evaluated against the newly established emergency stop price. If the market opens and immediately plunges during the opening minute, the emergency stop is blind to that price action.
3. **Off-by-One Time Stop Violation (Observation 3)**:
   A swing trade entered at Monday 09:30 ET and held through Friday 16:00 close has completed 5 full trading sessions. Because `holding_days` initializes at 0 and increments once per session rollover, `holding_days` equals 4 at Friday close. The rule `holding_days >= 5` fails to trigger, causing the position to be held across the weekend and Monday close, exiting only on Tuesday open (Day 7). This violates Rule 7c.
4. **Loosening Stop-Loss Breach (Observation 4)**:
   In `tighten_stop`, omitting `new_stop > old_stop` allows an operator to widen the stop-loss below the 2.5x ATR hard stop, defeating the purpose of an emergency risk floor.
5. **Symbol Reservation Leak (Observation 5)**:
   Unconditional symbol release in the `finally` block of `execute_market_open` when an order fill throws an error releases the reservation despite the unclosed swing position, allowing the intraday day-trading arm to trade the same symbol concurrently.
6. **Double Staging (Observation 6)**:
   Failing to filter `exiting_symbols` from candidate entry evaluation allows a symbol being exited on time stop to immediately be staged for entry if RSI(2) < 10, creating unnecessary transaction costs and potential wash sales.

---

## 3. Caveats

- The existing unit test suite (`pytest backend/tests/ -q`) passes 398/398 tests because the existing tests in `test_swing_strategy.py` explicitly mock `open_prices` with exact prices (e.g. `{"LRCX": 800.0}`) rather than simulating the asynchronous arrival of individual symbol bars through `handle_bar_event`.
- The core mathematical calculations (Wilder RSI-2, Wilder ATR-14, 200 SMA, 60d RS vs QQQ) are correctly implemented with zero lookahead bias.
- The UI components properly render telemetry, but require alignment on holding day indexing.

---

## 4. Conclusion & Findings

### Findings Catalog

| Finding ID | Severity | File & Lines | Description | Fix Direction |
|------------|----------|--------------|-------------|---------------|
| **F-ADV3-01** | **CRITICAL** | `backend/app/main.py:1260–1269`, `swing_panic_dip.py:426–430` | 09:30 open execution triggered on arbitrary first symbol uses stale yesterday close for other staged symbols | Only execute open orders for `sym` when `bar.symbol == sym` or when confirmed open price for `sym` is present; do not fall back to yesterday's close |
| **F-ADV3-02** | **MAJOR** | `backend/app/main.py:1258–1269` | `swing_strategy_engine.on_bar(bar)` runs before `execute_market_open`, ignoring opening candle stop breaches | In `handle_bar_event`, execute staged open orders for the symbol before evaluating `on_bar` on that symbol's opening candle |
| **F-ADV3-03** | **MAJOR** | `swing_panic_dip.py:499, 819`, `main.py:938`, `swing_indicators.py:380`, `ActiveSwingPositionsTable.tsx:183` | Off-by-one holding days accounting: `holding_days=0` on entry day causes 5-day time stop to trigger on Day 6 close instead of Day 5 close | Initialize `holding_days = 1` on entry day (or trigger exit when `holding_days >= 5` with 1-based days) so Friday close is recognized as Day 5 |
| **F-ADV3-04** | **MINOR** | `swing_panic_dip.py:760–770` | `tighten_stop` does not require `new_stop > old_stop`, allowing operators to loosen stops | Require `if old_stop and new_stop <= old_stop: return False` |
| **F-ADV3-05** | **MINOR** | `swing_panic_dip.py:404–408` | `execute_market_open` releases symbol reservation in `finally` even if fill raises an exception | Only release symbol reservation if the exit order was successfully filled and position shares reached 0 |
| **F-ADV3-06** | **MINOR** | `swing_panic_dip.py:280–285` | A symbol exiting on Day 5 can simultaneously be staged for entry if RSI(2) < 10 | Exclude `exiting_symbols` when screening candidate symbols in `evaluate_market_close` |

---

## 5. Verification Method

1. **Verify Existing Tests**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   pytest backend/tests/test_swing_strategy.py -v
   pytest backend/tests/ -q
   ```
   *Baseline*: All 398 tests currently pass.

2. **Verify Critical Race Condition (F-ADV3-01)**:
   ```bash
   python3 -c '
   from datetime import date, datetime, timezone
   from backend.app.core.account import PaperTradingAccount
   from backend.app.core.engine import ExecutionEngine
   from backend.app.strategies.swing_panic_dip import SwingStrategyEngine, SwingStagedOrderManager

   acct = PaperTradingAccount(initial_cash=50000.0)
   engine = ExecutionEngine(account=acct)
   mgr = SwingStagedOrderManager()
   swing_engine = SwingStrategyEngine(account=acct, execution_engine=engine, staged_manager=mgr)
   mgr.stage_buy("KLAC", target_notional=25000.0, daily_atr=5.0, signal_date=date(2026, 9, 22), reason="PANIC_DIP")
   res = swing_engine.execute_market_open({"AAPL": 225.0, "KLAC": 700.0}, datetime(2026, 9, 23, 9, 30, tzinfo=timezone.utc))
   assert res["entries"][0]["price"] == 700.0, "KLAC filled at stale yesterday price"
   print("F-ADV3-01 Verified")
   '
   ```

3. **Verify Holding Days Off-by-One (F-ADV3-03)**:
   ```bash
   python3 -c '
   from backend.app.strategies.swing_indicators import evaluate_swing_exit
   res = evaluate_swing_exit("GS", [], holding_days=4, earnings_tomorrow=False)
   assert res.exit_time_stop is False, "Friday close fails to exit on Day 5"
   print("F-ADV3-03 Verified")
   '
   ```

4. **Verify Stop Loosening (F-ADV3-04)**:
   ```bash
   python3 -c '
   from backend.app.core.account import PaperTradingAccount, Position, PositionSide, TradingArm
   from backend.app.core.engine import ExecutionEngine
   from backend.app.strategies.swing_panic_dip import SwingStrategyEngine

   acct = PaperTradingAccount(initial_cash=50000.0)
   engine = ExecutionEngine(account=acct)
   swing_engine = SwingStrategyEngine(account=acct, execution_engine=engine)
   pos = Position(symbol="AMD", side=PositionSide.LONG, shares=100, avg_entry_price=150.0, market_price=150.0, arm=TradingArm.SWING, stop_loss_price=140.0)
   acct.positions["AMD"] = pos
   assert swing_engine.tighten_stop("AMD", 120.0) is True, "Allowed loosening stop"
   print("F-ADV3-04 Verified")
   '
   ```

5. **Invalidation Conditions**:
   - Remediation fixes must resolve F-ADV3-01 through F-ADV3-06 without breaking the 398 existing test passes.
   - All open executions must strictly occur at the symbol's actual opening auction price ($P_{\text{open}}$).
   - Time stop exits must execute at the open of Day 6 following 5 complete trading days held.
