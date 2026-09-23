# Handoff Report — Forensic & Adversarial Audit Remediation Plan (Milestone M9D)

**Agent**: Remediation Explorer (`teamwork_preview_explorer_remediation_1`)  
**Mission**: Formulate an exact, file-by-file, function-by-function remediation plan addressing all 10 identified defects from the Forensic Integrity Audit and Adversarial Reviews (M9D).  
**Authoritative Documents**:
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/GATE_STATUS.md`
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_auditor_1/handoff.md` (Forensic Audit Report)
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_reviewer_adv_1/handoff.md` (Pass 1 Review)
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_reviewer_adv_2/handoff.md` (Pass 2 Review)
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_reviewer_adv_3/handoff.md` (Pass 3 Review)
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_challenger_1/handoff.md` (Challenger 1 Stress Report)
- `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_challenger_2/handoff.md` (Challenger 2 Concurrency Report)  
**Status**: Comprehensive Blueprint Ready for Immediate Implementation  

---

## Executive Summary & Defect Catalog

| Defect ID | Severity | File & Location | Summary | Target Fix |
|---|:---:|---|---|---|
| **D1** | **CRITICAL** | `backend/app/strategies/swing_panic_dip.py:823-826` | `AttributeError` on `to_ui_dict()` when active swing position exists | Map `exit_eval` fields to `exit_5_sma`, `exit_rsi2_overbought`, `exit_time_stop`, `exit_earnings`; add alias properties on `SwingExitResult`; add active UI test |
| **D2** | **CRITICAL** | `backend/app/main.py:1254-1269`, `swing_panic_dip.py:369-429` | 09:30 open bar arrival race condition fills staged orders on stale yesterday close | Reorder execution in `main.py` so staged order for symbol executes strictly on THAT symbol's confirmed 09:30 open bar (`bar.open`) before `on_bar` evaluation |
| **D3** | **CRITICAL** | `backend/app/main.py:128-141, 250-262` | AMD symbol mutual exclusion bypass allows concurrent working orders | Check both `account.positions` and `engine.working_orders` across intraday and swing arms in `pre_trade_risk_validator` and `is_symbol_reserved_for_swing` |
| **D4** | **CRITICAL** | `backend/app/strategies/swing_panic_dip.py:195, 340` | Missing concurrency lock in `execute_market_open` allows double-buying ($75k capital breach) | Add `threading.RLock()` to `SwingStrategyEngine` and wrap `execute_market_open` in mutex; atomically dequeue orders |
| **D5** | **MAJOR** | `backend/app/main.py:935-940` | Weekend session boundary rollover increments `holding_days` on non-trading Saturday/Sunday | Guard `pos.holding_days` increment with `if session_date.weekday() < 5:` |
| **D6** | **MAJOR** | `backend/app/strategies/swing_panic_dip.py:499, 819` | Holding days lifecycle off-by-one delays Rule 7c time stop by 1 trading session | Initialize `pos.holding_days = 1` upon fill on Day 1 so Friday close after 5 trading days reaches `holding_days = 5` and triggers time stop |
| **D7** | **MAJOR** | `backend/app/strategies/earnings_calendar.py:177-192` | False earnings blackout for past BMO morning reports on Day T at 16:00 close | Verify `diff_seconds >= 0` and skip past reports (`diff_seconds < 0` or same-day BMO at close); add Friday weekend forward coverage |
| **D8** | **MAJOR** | `backend/app/strategies/swing_panic_dip.py:280-286` | Simultaneous exit and entry staging collision on the same symbol | Exclude `active_positions` and `exiting_symbols` from candidate entry screening at 16:00 close |
| **D9** | **MAJOR** | `backend/app/main.py:1142-1175` | Intraday capacity starvation in `execute_strategy_signal` (swing holdings count towards intraday cap) | Pass `arm=TradingArm.INTRADAY` into `_get_effective_committed_portfolio` and `evaluate_order_request` |
| **D10** | **MAJOR** | `backend/app/core/runtime_state.py`, `backend/app/main.py:370, 495` | Staged swing orders and symbol reservations evaporate on overnight process restart | Persist `swing_staged_orders` and `swing_reserved_symbols` in SQLite checkpoint capture and restore routines |

---

## 1. Observation

### 1.1 Defect 1: Crashing `AttributeError` in `to_ui_dict()` on Active Swing Positions
- **Source Code Location**: `backend/app/strategies/swing_panic_dip.py`, lines 822–827:
  ```python
  822:                 "exit_triggers": {
  823:                     "sma_5_cross": exit_eval.rule_7a_sma5_exit,
  824:                     "rsi_70_cross": exit_eval.rule_7b_rsi_exit,
  825:                     "time_stop_day_5": exit_eval.rule_7c_time_exit,
  826:                     "earnings_tomorrow": exit_eval.rule_4_earnings_exit,
  827:                 },
  ```
- **Dataclass Definition**: `backend/app/strategies/swing_indicators.py`, lines 234–249:
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
- **Verbatim Error Reproduction**:
  ```bash
  python3 -c "
  from backend.app.main import swing_strategy_engine
  from backend.app.core.account import Position, PositionSide, TradingArm
  from datetime import date
  pos = Position('MU', PositionSide.LONG, 100, 105.0, 110.0, arm=TradingArm.SWING,
                 strategy_id='swing_panic_dip', stop_loss_price=98.0, entry_date=date.today(), holding_days=2)
  swing_strategy_engine.account.positions['MU'] = pos
  swing_strategy_engine.to_ui_dict()
  "
  ```
  **Output**: `AttributeError: 'SwingExitResult' object has no attribute 'rule_7a_sma5_exit'`
- **Test Gap**: `backend/tests/test_swing_ui_api.py:17-31` called `to_ui_dict()` only with empty `account.positions`, masking the runtime crash.

### 1.2 Defect 2: 09:30 Open Bar Timing Race and Stale Yesterday Close Fill
- **Source Code Location**: `backend/app/main.py`, lines 1254–1269:
  ```python
  1254:     # Swing Data Aggregation & Real-Time Emergency Stop Check
  1255:     swing_set = set(settings.SWING_SYMBOLS) | {settings.SWING_BENCHMARK}
  1256:     if bar.symbol.upper() in swing_set:
  1257:         daily_bar_aggregator.on_minute_bar(bar)
  1258:     swing_strategy_engine.on_bar(bar)
  1259: 
  1260:     # 09:30 ET Market Open Execution for Staged Swing Orders
  1261:     bar_et = bar.timestamp.astimezone(ET_TZ) if bar.timestamp.tzinfo else bar.timestamp
  1262:     if bar_et.time().hour == 9 and bar_et.time().minute == 30:
  1263:         if swing_staged_order_manager.get_staged_orders():
  1264:             open_prices = {bar.symbol.upper(): bar.open}
  1265:             for sym, p in latest_market_prices.items():
  1266:                 if sym not in open_prices:
  1267:                     open_prices[sym] = p
  1268:             swing_strategy_engine.execute_market_open(open_prices, bar.timestamp)
  ```
- **Observed Behavior**: If `AAPL` (intraday stock) arrives at 09:30:00 before `KLAC`, `open_prices` contains `{"AAPL": 225.0}` and falls back to `latest_market_prices["KLAC"]` (yesterday's 15:59 close, e.g. $700.00). `KLAC` is executed at $700.00 with 35 shares instead of its actual opening price ($750.00, 33 shares), and its 2.5x ATR stop is mispriced by $50.00. Furthermore, `on_bar` runs before `execute_market_open`, ignoring any stop breach on the opening candle.

### 1.3 Defect 3: AMD Mutual Exclusion Bypass Between In-Flight Orders and Swing Entry
- **Source Code Location**: `backend/app/main.py`, lines 250–262:
  ```python
  250:     if not is_exit:
  251:         if is_swing:
  252:             if existing_pos is not None and (
  253:                 getattr(existing_pos, "arm", None) != TradingArm.SWING
  254:                 and getattr(existing_pos, "strategy_id", "") != "swing_panic_dip"
  255:             ):
  256:                 return False, f"SWING_REJECTED: Symbol {sym} is currently held by Intraday strategy"
  257:         else:
  258:             if is_symbol_reserved_for_swing(sym, acct):
  259:                 return False, f"SYMBOL_RESERVED_FOR_SWING: Intraday entry for {sym} rejected..."
  ```
- **Observed Behavior**: An intraday limit entry order working in `engine.working_orders` for `AMD` leaves `existing_pos` as `None`. A concurrent Swing order for `AMD` is approved by `pre_trade_risk_validator`. Both orders sit in `working_orders` and fill simultaneously, causing wash trades and corrupted share accounting.
- **Verbatim Failure**: `backend/tests/stress/test_challenger_concurrency_margin_races.py:599`: `MUTUAL EXCLUSION LEAK CONFIRMED: Intraday order ord_... is actively working on AMD. pre_trade_risk_validator approved concurrent Swing order ord_...`

### 1.4 Defect 4: Missing Concurrency Lock in `execute_market_open` Leading to $75,000 Capital Breach
- **Source Code Location**: `backend/app/strategies/swing_panic_dip.py`, lines 340–527:
  No synchronization primitive exists. Staged orders are only deleted in the `finally` block after order creation and execution.
- **Observed Behavior**: When 10 concurrent threads invoke `execute_market_open` at 09:30 open, multiple threads evaluate `active_count < self.max_concurrent_positions` simultaneously before fills complete. Both threads create 250-share orders for `MU` ($50,000 notional) and commit $75,000 total across the shared $50,000 account pool.
- **Verbatim Failure**: `backend/tests/stress/test_challenger_concurrency_margin_races.py:76`: `assert 75000.0 <= 50050.0`

### 1.5 Defect 5: Weekend Session Boundary Rollover Advances `holding_days`
- **Source Code Location**: `backend/app/main.py`, lines 863–940:
  ```python
  935:     # Advance holding_days counter for active swing positions across session boundary
  936:     for sym, pos in account.positions.items():
  937:         if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
  938:             pos.holding_days += 1
  939:             log.info("Advanced swing position %s holding_days to %d", sym, pos.holding_days)
  ```
- **Observed Behavior**: On Saturday (weekday 5) and Sunday (weekday 6), wall-clock ticks advance `last_session_date`, executing lines 936–939. A swing position opened Friday with `holding_days = 0` advances to 1 on Saturday, 2 on Sunday, and 3 on Monday morning before trading begins.

### 1.6 Defect 6: Holding Days Lifecycle Off-by-One Delays Time-Stop Exit
- **Source Code Location**: `backend/app/strategies/swing_panic_dip.py`, line 499:
  ```python
  499:                     pos.holding_days = 0
  ```
- **Observed Behavior**: A position entered Monday 09:30 open has `holding_days = 0`. At Friday 16:00 close (5 full trading sessions held), `holding_days` is 4. In `evaluate_swing_exit`, `holding_days >= 5` is `False`. The position is held over the weekend and through Monday, exiting only on Tuesday open (Day 7 / 6 sessions held). UI also renders "Day 1 of 5" for two consecutive days.

### 1.7 Defect 7: False Earnings Blackout on Same-Day BMO Reports
- **Source Code Location**: `backend/app/strategies/earnings_calendar.py`, lines 180–183:
  ```python
  180:                 # If report date is within 2 calendar days, enforce safe-side blackout
  181:                 diff_days = (ev.report_date - as_of.date()).days
  182:                 if 0 <= diff_days <= 2:
  183:                     return True
  ```
- **Observed Behavior**: A stock reporting BMO at 08:30 on Day T has `diff_seconds = -7.5h < 0` at 16:00 close evaluation. However, `diff_days = 0`, causing line 182 to return `True`, falsely vetoing entry on valid post-earnings panic dips where earnings uncertainty has completely cleared.

### 1.8 Defect 8: Simultaneous Exit and Entry Staging Collision on Same Symbol
- **Source Code Location**: `backend/app/strategies/swing_panic_dip.py`, lines 264–286:
  ```python
  264:         exiting_symbols = {e.symbol for e in staged_exits}
  265:         surviving_positions = {sym for sym in active_positions if sym not in exiting_symbols}
  ...
  280:                 # Skip if already held (and not exiting)
  281:                 if sym in surviving_positions:
  282:                     continue
  ```
- **Observed Behavior**: If `LRCX` reaches Day 5 time stop, it is in `exiting_symbols` and excluded from `surviving_positions`. If `LRCX` also has RSI(2) < 10, the candidate loop stages a `BUY` for `LRCX`. At 09:30 open, the engine executes a SELL and immediately a BUY for $25,000 on `LRCX` (churn/wash sale).

### 1.9 Defect 9: Intraday Capacity Starvation in `execute_strategy_signal`
- **Source Code Location**: `backend/app/main.py`, line 1142:
  ```python
  1142:     committed_symbols, committed_sectors, committed_count, notional_map = _get_effective_committed_portfolio(account)
  ```
- **Observed Behavior**: Calling `_get_effective_committed_portfolio(account)` without `arm=TradingArm.INTRADAY` counts active swing positions towards `committed_count`. When 2 swing positions are open and 1 intraday position is held, `committed_count = 3`. Line 1144 passes `current_positions_count = 3` to `adaptation_engine`, which denies the legitimate 2nd intraday trade with `CONCURRENCY_GATE_DENIED: Max concurrent positions (3) reached`.
- **Verbatim Failure**: `backend/tests/stress/test_challenger_concurrency_margin_races.py:311`: `VULNERABILITY CONFIRMED: execute_strategy_signal calls _get_effective_committed_portfolio without arm=TradingArm.INTRADAY, causing committed_count=3 to count 2 swing positions.`

### 1.10 Defect 10: In-Memory Staged Orders Evaporate on Overnight Process Restart
- **Source Code Location**: `backend/app/core/runtime_state.py`, lines 97–125 (`capture_runtime_state`) and lines 150–210 (`restore_runtime_state`); `backend/app/main.py`, lines 370–388 and lines 495–515.
- **Observed Behavior**: Staged swing orders reside purely in `SwingStagedOrderManager._staged` and reserved symbols in `main.swing_reserved_symbols`. Neither is captured in `TradingStateStore` SQLite checkpoints. An overnight Railway container restart destroys all staged buy/sell orders and clears AMD reservations.
- **Verbatim Failure**: `backend/tests/stress/test_challenger_concurrency_margin_races.py:692`: `assert "swing_staged_orders" not in state`

---

## 2. Logic Chain

1. **Defect 1**: `to_ui_dict()` is executed every second by `broadcast_ui_state()`. The field names `rule_7a_sma5_exit`, `rule_7b_rsi_exit`, `rule_7c_time_exit`, and `rule_4_earnings_exit` do not exist on `SwingExitResult`. When a swing position exists, Python raises `AttributeError`, crashing the WebSocket broadcast task. Mapping to `exit_5_sma`, `exit_rsi2_overbought`, `exit_time_stop`, and `exit_earnings` and adding backward-compatible property aliases guarantees serialization safety.
2. **Defect 2**: Bars stream per symbol asynchronously. Executing staged orders for all symbols upon the arrival of an arbitrary symbol's 09:30 bar causes non-arrived symbols to use yesterday's close. Restricting execution strictly to `bar.symbol.upper()` when that symbol's 09:30 bar arrives ensures trades execute at that symbol's exact opening auction price (`bar.open`). Reordering execution before `on_bar` ensures opening candle stop breaches are detected immediately.
3. **Defect 3**: A working limit order in `engine.working_orders` is not yet an open position in `account.positions`. Checking only `account.positions` creates a race window where Intraday and Swing submit concurrent orders for `AMD`. Extending `is_symbol_reserved_for_swing` and `pre_trade_risk_validator` to check `engine.working_orders` guarantees mutual exclusion.
4. **Defect 4**: `execute_market_open` checks `active_count < max_concurrent_positions` before creating orders. Without synchronization, concurrent threads read the same state simultaneously and exceed position and notional limits. Enclosing the method in `with self._execution_lock:` serializes execution deterministically.
5. **Defect 5**: Wall-clock ticks call `_check_session_boundary` on weekends. Weekends are non-trading days. Guarding the `holding_days` increment with `session_date.weekday() < 5` ensures holding days only advance across valid trading sessions.
6. **Defect 6**: A trade entered at 09:30 open on Day 1 is held during Day 1's trading session. Initializing `pos.holding_days = 1` aligns the lifecycle so that after 5 full trading sessions (Mon–Fri), `pos.holding_days == 5`, correctly triggering Rule 7c time stop at Friday close.
7. **Defect 7**: An earnings release before market open (08:30 BMO) has resolved all binary risk before 16:00 close evaluation. Enforcing `diff_seconds >= 0` ensures past events are ignored, preventing false vetoes.
8. **Defect 8**: Excluding `exiting_symbols` from `surviving_positions` made them eligible for entry candidate evaluation. Explicitly checking `if sym in active_positions or sym in exiting_symbols: continue` eliminates simultaneous exit/entry staging collisions.
9. **Defect 9**: Intraday and Swing are distinct trading arms with separate concurrency allocations (Intraday: 3, Swing: 2). Omitting `arm=TradingArm.INTRADAY` in `execute_strategy_signal` starved intraday trading. Passing `arm=TradingArm.INTRADAY` restores independent capacity.
10. **Defect 10**: Staged orders are queued overnight (16:00 to 09:30). Incorporating `swing_staged_orders` and `swing_reserved_symbols` into `capture_runtime_state` and `restore_runtime_state` preserves all staged orders across server restarts.

---

## 3. Caveats

- **Seed Bar Fixture Range**: Seed data provides daily bars up to 2026-09-22. In live production, `DailyBarAggregator` must continuously accumulate intraday minute bars into daily bars at each session close.
- **US Exchange Holidays**: Filtering `weekday() < 5` covers all weekends. If US exchange holidays falling on weekdays (e.g. Good Friday, Labor Day) occur, `_check_session_boundary` would advance unless an exchange holiday calendar is integrated. Adding a holiday check or advancing on first trade bar of the session is recommended as a future enhancement.
- **Read-Only Scope**: In strict compliance with the Explorer role, this document provides the exact code modifications without directly writing to production code files.

---

## 4. Conclusion & Concrete Remediation Plan

### Remediation Blueprint: File-by-File, Function-by-Function

#### Fix 1: Resolve `AttributeError` in `to_ui_dict()` and Add Property Aliases
1. **File**: `backend/app/strategies/swing_panic_dip.py`
   - **Function**: `SwingStrategyEngine.to_ui_dict(self)`
   - **Line Numbers**: 822–827
   - **Replacement**:
     ```python
     "exit_triggers": {
         "sma_5_cross": exit_eval.exit_5_sma,
         "rsi_70_cross": exit_eval.exit_rsi2_overbought,
         "time_stop_day_5": exit_eval.exit_time_stop,
         "earnings_tomorrow": exit_eval.exit_earnings,
     },
     ```
2. **File**: `backend/app/strategies/swing_indicators.py`
   - **Dataclass**: `SwingExitResult`
   - **Line Numbers**: 234–250
   - **Add Property Aliases**:
     ```python
     @property
     def rule_7a_sma5_exit(self) -> bool:
         return self.exit_5_sma

     @property
     def rule_7b_rsi_exit(self) -> bool:
         return self.exit_rsi2_overbought

     @property
     def rule_7c_time_exit(self) -> bool:
         return self.exit_time_stop

     @property
     def rule_4_earnings_exit(self) -> bool:
         return self.exit_earnings
     ```
3. **File**: `backend/tests/test_swing_ui_api.py`
   - **Add Unit Test**:
     ```python
     @pytest.mark.asyncio
     async def test_swing_engine_to_ui_dict_with_active_positions(client):
         """Verify to_ui_dict and broadcast_ui_state serialize cleanly when active swing positions exist."""
         pos = Position(
             symbol="MU", side=PositionSide.LONG, shares=100, avg_entry_price=105.0,
             market_price=110.0, arm=TradingArm.SWING, strategy_id="swing_panic_dip",
             stop_loss_price=98.0, entry_date=date.today(), holding_days=2,
         )
         swing_strategy_engine.account.positions["MU"] = pos
         try:
             ui_dict = swing_strategy_engine.to_ui_dict()
             assert len(ui_dict["positions"]) == 1
             pos_ui = ui_dict["positions"][0]
             assert pos_ui["symbol"] == "MU"
             assert "exit_triggers" in pos_ui
             assert "sma_5_cross" in pos_ui["exit_triggers"]
             assert "rsi_70_cross" in pos_ui["exit_triggers"]
             assert "time_stop_day_5" in pos_ui["exit_triggers"]
             assert "earnings_tomorrow" in pos_ui["exit_triggers"]
             await broadcast_ui_state(force=True)
         finally:
             swing_strategy_engine.account.positions.pop("MU", None)
     ```
4. **File**: `backend/tests/test_adversarial_challenger_1.py`
   - **Line 414**: Update `test_to_ui_dict_attribute_error_on_active_position` to assert successful dictionary return instead of expecting `AttributeError`.

---

#### Fix 2: 09:30 Open Bar Arrival Race Condition
1. **File**: `backend/app/main.py`
   - **Function**: `handle_bar_event(bar: BarEvent)`
   - **Line Numbers**: 1254–1269
   - **Replacement**:
     ```python
     bar_sym = bar.symbol.upper()
     bar_et = bar.timestamp.astimezone(ET_TZ) if bar.timestamp.tzinfo else bar.timestamp

     # 09:30 ET Market Open Execution for Staged Swing Orders
     # Execute open orders strictly for this symbol when THAT symbol's 09:30 open bar arrives
     if bar_et.time().hour == 9 and bar_et.time().minute == 30:
         if swing_staged_order_manager.is_staged_for_entry(bar_sym) or swing_staged_order_manager.is_staged_for_exit(bar_sym):
             swing_strategy_engine.execute_market_open({bar_sym: bar.open}, bar.timestamp)

     # Swing Data Aggregation & Real-Time Emergency Stop Check
     # Evaluated after execute_market_open so new positions are monitored on their opening candle
     swing_set = set(settings.SWING_SYMBOLS) | {settings.SWING_BENCHMARK}
     if bar_sym in swing_set:
         daily_bar_aggregator.on_minute_bar(bar)
     swing_strategy_engine.on_bar(bar)
     ```
2. **File**: `backend/app/strategies/swing_panic_dip.py`
   - **Function**: `execute_market_open`
   - **Line Numbers**: 369–373, 426–430
   - **Modification**:
     In exit processing:
     ```python
     open_price = open_prices.get(sym)
     if open_price is None or open_price <= 0.0:
         continue  # Await this symbol's open bar
     ```
     In entry processing:
     ```python
     open_price = open_prices.get(sym)
     if not open_price or open_price <= 0.0:
         continue  # Await this symbol's open bar
     ```

---

#### Fix 3: AMD Symbol Mutual Exclusion on Working Orders
1. **File**: `backend/app/main.py`
   - **Function**: `is_symbol_reserved_for_swing`
   - **Line Numbers**: 128–142
   - **Replacement**:
     ```python
     def is_symbol_reserved_for_swing(
         symbol: str,
         acct: Optional[PaperTradingAccount] = None,
         eng: Optional[Any] = None,
     ) -> bool:
         """Check if a symbol is reserved, actively held, or has working orders in the swing arm."""
         sym = symbol.upper()
         if sym in swing_reserved_symbols:
             return True
         target_acct = acct or globals().get("account")
         if target_acct and hasattr(target_acct, "positions"):
             pos = target_acct.positions.get(sym)
             if pos is not None and (
                 getattr(pos, "arm", None) == TradingArm.SWING
                 or getattr(pos, "strategy_id", "") == "swing_panic_dip"
             ):
                 return True
         target_engine = eng or globals().get("engine")
         if target_engine and hasattr(target_engine, "working_orders"):
             for w_order in target_engine.working_orders.values():
                 if w_order.symbol.upper() == sym and (
                     getattr(w_order, "arm", None) == TradingArm.SWING
                     or getattr(w_order, "strategy_id", "") == "swing_panic_dip"
                 ):
                     return True
         return False
     ```
   - **Function**: `pre_trade_risk_validator`
   - **Line Numbers**: 250–262
   - **Replacement**:
     ```python
     # Symbol reservation / mutual exclusion check:
     if not is_exit:
         target_engine = globals().get("engine")
         if is_swing:
             # Swing cannot enter if an INTRADAY position is currently open for this symbol
             if existing_pos is not None and (
                 getattr(existing_pos, "arm", None) != TradingArm.SWING
                 and getattr(existing_pos, "strategy_id", "") != "swing_panic_dip"
             ):
                 return False, f"SWING_REJECTED: Symbol {sym} is currently held by Intraday strategy"
             # Swing cannot enter if an INTRADAY order is currently working for this symbol
             if target_engine and hasattr(target_engine, "working_orders"):
                 for w_order in target_engine.working_orders.values():
                     if w_order.symbol.upper() == sym and (
                         getattr(w_order, "arm", None) != TradingArm.SWING
                         and getattr(w_order, "strategy_id", "") != "swing_panic_dip"
                     ):
                         return False, f"SWING_REJECTED: Symbol {sym} has active working order in Intraday strategy"
         else:
             # Intraday cannot enter if symbol is reserved, held, or has working orders in Swing
             if is_symbol_reserved_for_swing(sym, acct, target_engine):
                 return False, f"SYMBOL_RESERVED_FOR_SWING: Intraday entry for {sym} rejected because symbol is reserved/held by Swing Engine"
     ```
2. **File**: `backend/app/strategies/swing_panic_dip.py`
   - **Function**: `execute_market_open` (lines 404–408)
   - **Modification**: Only call `self.release_symbol_cb(sym)` if exit order fill succeeded and position was completely closed (`pos.shares <= 0`).

---

#### Fix 4: Mutex Concurrency Lock in `execute_market_open`
1. **File**: `backend/app/strategies/swing_panic_dip.py`
   - **Function**: `SwingStrategyEngine.__init__`
   - **Line Numbers**: 195–211
   - **Add**:
     ```python
     import threading
     ...
     self._execution_lock = threading.RLock()
     ```
   - **Function**: `SwingStrategyEngine.execute_market_open`
   - **Line Numbers**: 340–360
   - **Wrap with Lock**:
     ```python
     def execute_market_open(
         self,
         open_prices: Dict[str, float],
         open_time: datetime,
     ) -> Dict[str, Any]:
         with self._execution_lock:
             # Existing logic continues inside lock...
     ```
   - In addition, immediately remove or atomically claim each staged entry from `self.staged_manager` prior to order submission so duplicate execution cannot occur even if re-entered.

---

#### Fix 5: Weekend Session Boundary Rollover Desynchronization
1. **File**: `backend/app/main.py`
   - **Function**: `_check_session_boundary(now_dt: datetime)`
   - **Line Numbers**: 935–940
   - **Replacement**:
     ```python
     # Advance holding_days counter for active swing positions across session boundary
     # Strictly on trading days (Monday=0 through Friday=4). Non-trading weekend days (Saturday=5, Sunday=6) never increment.
     if session_date.weekday() < 5:
         for sym, pos in account.positions.items():
             if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
                 pos.holding_days += 1
                 log.info("Advanced swing position %s holding_days to %d", sym, pos.holding_days)
     ```
2. **File**: `backend/tests/test_swing_flattening_exemption.py`
   - **Add Test**: `test_weekend_session_boundary_does_not_increment_holding_days()` verifying advancing Friday -> Saturday -> Sunday leaves `holding_days` unchanged, and Monday increments by 1.

---

#### Fix 6: Holding Days Lifecycle Off-by-One Time Stop
1. **File**: `backend/app/strategies/swing_panic_dip.py`
   - **Function**: `execute_market_open`
   - **Line Number**: 499
   - **Replacement**:
     ```python
     pos.holding_days = 1  # Day 1 of the swing trade upon fill
     ```
2. **File**: `backend/tests/test_adversarial_challenger_1.py`
   - **Function**: `test_holding_days_lifecycle_off_by_one_time_stop`
   - **Line Numbers**: 421–446
   - **Update**: Assert that starting at Day 1 and advancing through 5 sessions reaches `holding_days == 5` on Friday close, triggering `exit_time_stop is True`.

---

#### Fix 7: False Earnings Blackout on Same-Day BMO Reports
1. **File**: `backend/app/strategies/earnings_calendar.py`
   - **Function**: `is_blackout_active`
   - **Line Numbers**: 177–192
   - **Replacement**:
     ```python
     if isinstance(as_of, datetime):
         as_of_dt = as_of
         for ev in events:
             if ev.report_time == "bmo":
                 report_dt = datetime.combine(ev.report_date, datetime.min.time()).replace(hour=8, minute=30)
             elif ev.report_time == "amc":
                 report_dt = datetime.combine(ev.report_date, datetime.min.time()).replace(hour=16, minute=30)
             else:
                 report_dt = datetime.combine(ev.report_date, datetime.min.time()).replace(hour=9, minute=30)

             if as_of_dt.tzinfo is not None:
                 report_dt = report_dt.replace(tzinfo=as_of_dt.tzinfo)

             diff_seconds = (report_dt - as_of_dt).total_seconds()
             # Past reports (e.g. BMO 08:30 evaluated at 16:00 close) NEVER trigger a blackout
             if diff_seconds < 0:
                 continue

             # Friday evaluation horizon extends across weekend to Monday/Tuesday earnings
             effective_horizon = horizon_hours
             if as_of_dt.weekday() == 4:
                 effective_horizon = max(horizon_hours, 96.0)

             if 0 <= diff_seconds <= effective_horizon * 3600.0:
                 return True
     else:
         as_of_date = as_of
         for ev in events:
             diff_days = (ev.report_date - as_of_date).days
             if diff_days < 0:
                 continue
             if diff_days == 0 and ev.report_time == "bmo":
                 continue
             max_days = 4 if as_of_date.weekday() == 4 else 2
             if 0 <= diff_days <= max_days:
                 return True
     ```
2. **File**: `backend/tests/test_adversarial_challenger_1.py`
   - **Line 356**: Update `test_false_earnings_blackout_for_past_morning_earnings` to `assert is_blackout is False`.

---

#### Fix 8: Simultaneous Exit and Entry Staging Collision on Same Symbol
1. **File**: `backend/app/strategies/swing_panic_dip.py`
   - **Function**: `evaluate_market_close`
   - **Line Numbers**: 280–286
   - **Replacement**:
     ```python
     # Skip if already held or scheduled to exit at next open (cannot enter and exit simultaneously)
     if sym in active_positions or sym in exiting_symbols:
         continue

     # Skip if already staged for entry
     if self.staged_manager.is_staged_for_entry(sym):
         continue
     ```
2. **File**: `backend/tests/test_adversarial_challenger_1.py`
   - **Lines 505–508**: Update `test_simultaneous_exit_and_entry_same_symbol_collision` to assert `collision_detected is False` and `assert "LRCX" in staged_exit_syms and "LRCX" not in staged_entry_syms`.

---

#### Fix 9: Intraday Capacity Starvation in `execute_strategy_signal`
1. **File**: `backend/app/main.py`
   - **Function**: `execute_strategy_signal`
   - **Line Numbers**: 1142–1175
   - **Replacement**:
     ```python
     latest_market_prices[sym] = signal.entry_price if bar is None else bar.close
     adapted_stop = adaptation_engine.calculate_adapted_stop(signal)
     committed_symbols, committed_sectors, committed_count, notional_map = _get_effective_committed_portfolio(
         account, arm=TradingArm.INTRADAY
     )
     is_active = sym in committed_symbols
     approved, reason, qty = adaptation_engine.evaluate_signal_admission(
         signal=signal,
         equity=account.equity,
         current_positions_count=committed_count,
         is_symbol_active=is_active,
     )
     if not approved or qty <= 0:
         if signal.strategy_id == "orb":
             orb_strategy.notify_signal_rejected(sym)
         return

     risk_preview = risk_engine.evaluate_order_request(
         symbol=sym,
         side="BUY" if signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY" else "SELL",
         requested_qty=qty,
         entry_price=signal.entry_price,
         stop_price=adapted_stop,
         account_equity=account.equity,
         buying_power=account.buying_power,
         active_positions_count=committed_count,
         active_symbols=committed_symbols,
         active_sectors=committed_sectors,
         vix_multiplier=adaptation_engine.current_sizing_multiplier,
         is_entry_lockout_active=flattening_engine.current_phase != FlatteningPhase.NORMAL_TRADING,
         is_exit=False,
         existing_position_notional=notional_map.get(sym, 0.0),
         arm=TradingArm.INTRADAY,
         strategy_id=signal.strategy_id,
     )
     ```

---

#### Fix 10: Staged Orders & Symbol Reservation Persistence in `runtime_state.py`
1. **File**: `backend/app/strategies/swing_panic_dip.py`
   - **Dataclass**: `StagedSwingOrder`
   - **Line 82**: Add `from_dict`:
     ```python
     @classmethod
     def from_dict(cls, d: Dict[str, Any]) -> StagedSwingOrder:
         sig_d = d["signal_date"]
         if isinstance(sig_d, str):
             sig_d = date.fromisoformat(sig_d)
         creat_d = d.get("created_at")
         if isinstance(creat_d, str):
             creat_d = datetime.fromisoformat(creat_d)
         elif creat_d is None:
             creat_d = datetime.now(timezone.utc)
         return cls(
             order_id=d["order_id"],
             symbol=d["symbol"],
             action=d["action"],
             target_notional=float(d.get("target_notional", 0.0)),
             shares=d.get("shares"),
             daily_atr=float(d.get("daily_atr", 0.0)),
             signal_date=sig_d,
             reason=d.get("reason", ""),
             created_at=creat_d,
             stop_loss_price=d.get("stop_loss_price"),
         )
     ```
   - **Class**: `SwingStagedOrderManager`
   - **Line 140**: Add `load_staged_orders`:
     ```python
     def load_staged_orders(self, orders: List[StagedSwingOrder]) -> None:
         self._staged.clear()
         for o in orders:
             self._staged[o.order_id] = o
     ```
2. **File**: `backend/app/core/runtime_state.py`
   - **Function**: `capture_runtime_state`
   - **Add Parameters**:
     ```python
     swing_staged_orders: Optional[List[Any]] = None,
     swing_reserved_symbols: Optional[Set[str]] = None,
     ```
   - **In Dictionary**:
     ```python
     "swing_staged_orders": [o.to_dict() if hasattr(o, "to_dict") else o for o in (swing_staged_orders or [])],
     "swing_reserved_symbols": list(swing_reserved_symbols or []),
     ```
   - **Function**: `restore_runtime_state`
   - **Add Parameters**:
     ```python
     swing_staged_order_manager: Optional[Any] = None,
     swing_reserved_symbols: Optional[Set[str]] = None,
     ```
   - **In Restoration Body**:
     ```python
     if swing_staged_order_manager is not None:
         raw_staged = decoded.get("swing_staged_orders", [])
         from backend.app.strategies.swing_panic_dip import StagedSwingOrder
         restored_orders = [StagedSwingOrder.from_dict(o) if isinstance(o, dict) else o for o in raw_staged]
         swing_staged_order_manager.load_staged_orders(restored_orders)
     if swing_reserved_symbols is not None:
         swing_reserved_symbols.clear()
         swing_reserved_symbols.update(decoded.get("swing_reserved_symbols", []))
     ```
3. **File**: `backend/app/main.py`
   - In `_capture_checkpoint()`: Pass `swing_staged_orders=swing_staged_order_manager.get_staged_orders(), swing_reserved_symbols=swing_reserved_symbols`.
   - In `restore_runtime_state(...)`: Pass `swing_staged_order_manager=swing_staged_order_manager, swing_reserved_symbols=swing_reserved_symbols`.
4. **File**: `backend/tests/stress/test_challenger_concurrency_margin_races.py`
   - Lines 692–700: Update `test_overnight_restart_evaporates_staged_orders` to assert that `swing_staged_orders` and `swing_reserved_symbols` are present in state and restored into `new_staged_mgr`.

---

## 5. Verification Method

### 5.1 Commands to Verify Remediation

```bash
cd /Users/mo/AutonomousDayTrader

# 1. Run Challenger 1 Adversarial Suite (Verifies Fixes 1, 6, 7, 8):
pytest backend/tests/test_adversarial_challenger_1.py -v

# 2. Run Challenger 2 Concurrency & Margin Races Suite (Verifies Fixes 3, 4, 9, 10):
pytest backend/tests/stress/test_challenger_concurrency_margin_races.py -v

# 3. Run Swing Flattening Exemption Suite (Verifies Fix 5):
pytest backend/tests/test_swing_flattening_exemption.py -v

# 4. Run Swing UI and WebSocket Serialization Suite (Verifies Fix 1):
pytest backend/tests/test_swing_ui_api.py -v

# 5. Run Full Backend Test Suite (Zero Regressions across all 400+ tests):
pytest backend/tests/ -q

# 6. Verify Process and Port Hygiene:
bash scripts/verify_port_hygiene.sh
lsof -i :3005 -i :8000 -i :8005 -i :8080
```

### 5.2 Invalidation Conditions
- Any occurrence of `AttributeError` when `to_ui_dict()` is called with active swing positions.
- Any execution where `execute_market_open` fills a staged symbol using yesterday's close instead of `bar.open`.
- Any execution where an intraday limit order on `AMD` permits a concurrent swing entry on `AMD`.
- Any multi-threaded invocation of `execute_market_open` resulting in more than 2 positions or >$50,000 capital.
- Any scenario where Saturday or Sunday calendar rollover advances `holding_days`.
- Any scenario where 5 full trading sessions held does not trigger Rule 7c time stop on Friday close.
- Any scenario where a past BMO morning earnings report triggers a blackout at 16:00 close.
- Any scenario where an exiting symbol is simultaneously staged for entry at the same open.
- Any scenario where holding 2 swing positions restricts intraday trading capacity to 1 position.
- Any server restart where staged orders or symbol reservations evaporate from durable state.
