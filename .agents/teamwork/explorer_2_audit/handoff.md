# Handoff Report: Forensic Audit of Session Rollover, Mutual Exclusion & SQLite Round-Trip Persistence

**Agent**: Explorer 2 (`teamwork_preview_explorer`)  
**Mission**: Forensic audit of Session Rollover, Mutual Exclusion (`AMD`), and SQLite Persistence Round-Trip for Swing Positions.  
**Destination**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_audit/handoff.md`  
**Reference Analysis**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_2_audit/analysis.md`  

---

## 1. Observation

1. **Session Rollover & State Integrity (`backend/app/main.py:892–1020`)**:
   - `_check_session_boundary(now_dt: datetime)` calculates Eastern Time date via `now_dt.astimezone(ET_TZ).date()` (line 897).
   - In lines 916–924, working order purge filters out swing orders:
     ```python
     intraday_working = [
         o for o in engine.working_orders.values()
         if getattr(o, "arm", None) != TradingArm.SWING and getattr(o, "strategy_id", "") != "swing_panic_dip"
     ]
     ```
   - In lines 928–931, boundary liquidation explicitly exempts swing positions:
     ```python
     intraday_positions = {
         sym: pos for sym, pos in account.positions.items()
         if getattr(pos, "arm", None) != TradingArm.SWING and getattr(pos, "strategy_id", "") != "swing_panic_dip"
     }
     ```
   - In lines 966–970, `holding_days` increments across session boundaries only on weekdays:
     ```python
     if session_date.weekday() < 5:
         for sym, pos in account.positions.items():
             if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
                 pos.holding_days += 1
     ```
   - Staged swing orders are stored in `SwingStagedOrderManager._staged` (`swing_panic_dip.py:114`) and are untouched by `_check_session_boundary`.

2. **Flattening Exemption (`backend/app/core/flattening.py:231–241` & `backend/app/main.py:1517–1600`)**:
   - Phase 1 (15:45): `main.py:291` sets `is_lockout = False` for swing orders.
   - Phase 2 (15:50): `main.py:1528–1529` skips orders where `arm == TradingArm.SWING` or `strategy_id == "swing_panic_dip"`.
   - Phase 3 (15:55): `main.py:1547–1548` skips positions where `arm == TradingArm.SWING` or `strategy_id == "swing_panic_dip"`.
   - Phase 4 (15:58): `flattening.py:233–241` filters out swing positions/orders before verifying clean book.
   - Session Close (16:00): `main.py:1590–1595` ensures `account.status` is NOT set to `EOD_FLAT` if active swing positions exist overnight.

3. **Mutual Exclusion for Shared Symbol `AMD` (`backend/app/main.py:115–154, 262–284`)**:
   - `swing_reserved_symbols: Set[str]` tracks swing reservations.
   - `pre_trade_risk_validator(order)` blocks intraday orders if `is_symbol_reserved_for_swing(sym)` is True (lines 280–283).
   - `pre_trade_risk_validator(order)` blocks swing orders if an intraday position or working order exists (lines 265–279).
   - In `swing_panic_dip.py:365`, `reserve_symbol_cb("AMD")` is called at 16:00 staging time.
   - `swing_reserved_symbols` persists across overnight boundaries in `capture_runtime_state` (line 130) and `restore_runtime_state` (lines 226–228).

4. **CRITICAL DEFECT: Symbol-by-Symbol Market-Open Arrival Race (`swing_panic_dip.py:453–463` & `main.py:1296–1298`)**:
   - In production, `main.py:1298` executes `swing_strategy_engine.execute_market_open({bar_sym: bar.open}, bar.timestamp)`.
   - In `swing_panic_dip.py:453–463`:
     ```python
     active_count = len(self.get_active_swing_positions())
     if active_count >= self.max_concurrent_positions:
         log.warning(...)
         self.staged_manager.remove_staged_order(entry_order.order_id)
         if self.release_symbol_cb:
             self.release_symbol_cb(sym)
         continue
     ```
   - If an entry bar arrives before an exit bar, the entry order is permanently deleted and its symbol reservation released, even though an exit is pending.

5. **Brittle Open Execution Window (`main.py:1296`)**:
   - Trigger is locked to `bar_et.time().hour == 9 and bar_et.time().minute == 30`. If the 09:30:00 bar is illiquid or arrives at 09:31:00, the staged order hangs indefinitely.

6. **SQLite Persistence Fidelity & Schema Degradation (`runtime_state.py:101–136, 160–236`, `events.py:272–292`, `account.py:83–102`)**:
   - Automated round-trip test confirmed: `Position` fields (`entry_date`, `entry_atr`, `stop_loss_price`, `holding_days`, `arm`, `strategy_id`) survive SQLite serialization and restoration.
   - However, `PositionState` in `events.py` and `Position.to_state()` omit `entry_atr` and `entry_date`, degrading UI WebSocket payloads and API snapshots.
   - `DailyBarStore` does not persist newly aggregated bars into SQLite checkpoint; daily bars aggregated during live trading are lost if the server restarts.

---

## 2. Logic Chain

1. **Session Rollover Isolation**:
   - From Observation 1: `_check_session_boundary` cancels only intraday working orders and liquidates only intraday positions.
   - Staged swing orders reside in `SwingStagedOrderManager` (in-memory dict), which is never cleared by `_check_session_boundary`.
   - Therefore, session rollover does **not** wipe or disrupt staged swing orders.

2. **Holding Days Progression**:
   - From Observation 1: On fill at 09:30 open, `pos.holding_days = 1`.
   - Overnight rollover on Monday night (Tuesday 00:00 ET) checks `session_date.weekday() < 5` (Tuesday is 1 < 5) and increments `pos.holding_days` to 2.
   - On Friday night, it reaches 5. At Friday 16:00 close, `evaluate_swing_exit` checks `holding_days >= 5`, which evaluates to `True`, staging exit for Monday open.
   - On Saturday and Sunday, `weekday() < 5` is `False`, preventing weekend increments.
   - On intra-day restarts, `last_session_date == session_date` causes `_check_session_boundary` to return immediately, preventing double increments.

3. **Mutual Exclusion Invariant**:
   - From Observation 3: `AMD` is locked at 16:00 close via `reserve_symbol_cb("AMD")` into `swing_reserved_symbols`.
   - Every intraday order must pass `pre_trade_risk_validator`, which calls `is_symbol_reserved_for_swing("AMD")`.
   - Because `swing_reserved_symbols` is serialized to SQLite and restored on restart, and because `account.positions["AMD"].arm == TradingArm.SWING` persists across boundaries, intraday cannot enter `AMD` while swing is staged or active.

4. **Market-Open Race Vulnerability**:
   - From Observation 4: In `main.py:1298`, incoming 1m bars call `execute_market_open` symbol-by-symbol (`{bar_sym: bar.open}`).
   - If `AMD` (staged BUY) arrives before `LRCX` (staged SELL), `open_prices` lacks `LRCX`, so `LRCX` cannot exit yet.
   - `execute_market_open` checks `active_count >= 2`. Since `LRCX` is still open, `active_count == 2`.
   - `execute_market_open` executes `self.staged_manager.remove_staged_order` and `release_symbol_cb("AMD")`.
   - Therefore, `AMD` is permanently dropped before `LRCX` can exit, breaking the execution of valid swing trades.

---

## 3. Caveats

- **Exchange Holiday Handling**: `session_date.weekday() < 5` checks only weekdays (Monday–Friday). If external feed events arrive on exchange holidays (e.g. Good Friday or Labor Day), `holding_days` will increment. Exchange holiday calendar integration is recommended for perfection.
- **Multi-Day Offline Scenario**: If the system is offline for 2+ trading days, `_check_session_boundary` increments `holding_days` by 1 upon reboot rather than calculating `trading_days_between(pos.entry_date, session_date)`.
- No other areas within the audit scope were omitted or unverified.

---

## 4. Conclusion

1. **Session Rollover**: Fully preserves swing orders and active holdings. 4-phase flattening strictly bypasses swing positions.
2. **Mutual Exclusion**: Robustly locks shared symbol `AMD` across overnight boundaries and restarts.
3. **Critical Defect Identified**: The symbol-by-symbol bar arrival at 09:30 ET prematurely discards staged entries when pending exits have not yet filled. Must be remediated by deferring entry execution instead of purging staged orders when pending exits exist.
4. **Persistence Round-Trip**: Core `Position` fields survive SQLite checkpointing; `PositionState` snapshot model and `DailyBarStore` restart persistence require schema additions provided in `analysis.md`.

---

## 5. Verification Method

To independently verify these findings:

1. **Verify Backend Unit & Regression Test Suite**:
   ```bash
   pytest backend/tests -q
   ```
   *Expected*: All 432 unit tests pass.

2. **Verify E2E Multiday & Swing Suite**:
   ```bash
   pytest tests/e2e -q
   ```
   *Expected*: All 325 E2E tests pass.

3. **Verify SQLite Round-Trip Persistence of Swing Attributes**:
   Run the following Python verification snippet:
   ```bash
   python3 -c "
   from datetime import date, datetime, timezone
   from backend.app.core.account import PaperTradingAccount, TradingArm
   from backend.app.core.engine import ExecutionEngine
   from backend.app.core.bracket import DynamicBracketManager
   from backend.app.core.risk import InstitutionalRiskEngine
   from backend.app.core.flattening import ZeroOvernightFlatteningEngine
   from backend.app.strategies.adaptation import DynamicAdaptationEngine
   from backend.app.core.runtime_state import capture_runtime_state, restore_runtime_state

   acct = PaperTradingAccount(initial_cash=50000.0)
   eng = ExecutionEngine(account=acct)
   bm = DynamicBracketManager()
   risk = InstitutionalRiskEngine()
   flat = ZeroOvernightFlatteningEngine()
   adapt = DynamicAdaptationEngine()

   now = datetime.now(timezone.utc)
   d = date(2026, 9, 21)
   acct.apply_fill('fill1', 'AMD', 'BUY', 100, 150.0, 0.0, now, arm=TradingArm.SWING, strategy_id='swing_panic_dip', stop_loss_price=140.0)
   pos = acct.positions['AMD']
   pos.entry_date = d
   pos.entry_atr = 4.25
   pos.holding_days = 2

   cap = capture_runtime_state(
       account=acct, engine=eng, bracket_manager=bm, risk_engine=risk,
       flattening_engine=flat, adaptation_engine=adapt, strategies=[],
       entry_order_to_bracket={}, bracket_realized_pnl={}, completed_brackets_recorded=set(),
       latest_market_prices={'AMD': 150.0}, market_history={}, recent_news=[],
       last_session_date=d, last_vix_print=None, ledger_revision=1
   )

   new_acct = PaperTradingAccount(initial_cash=50000.0)
   new_eng = ExecutionEngine(account=new_acct)
   new_bm = DynamicBracketManager()

   restore_runtime_state(
       cap, account=new_acct, engine=new_eng, bracket_manager=new_bm,
       risk_engine=risk, flattening_engine=flat, adaptation_engine=adapt,
       strategies=[], entry_order_to_bracket={}, bracket_realized_pnl={},
       completed_brackets_recorded=set(), latest_market_prices={},
       market_history={}, recent_news=[]
   )

   rpos = new_acct.positions['AMD']
   assert rpos.entry_date == d
   assert rpos.entry_atr == 4.25
   assert rpos.stop_loss_price == 140.0
   assert rpos.holding_days == 2
   assert rpos.arm == TradingArm.SWING
   assert rpos.strategy_id == 'swing_panic_dip'
   print('VERIFICATION PASSED: Swing position fields survived serialization round-trip!')
   "
   ```

4. **Verify Race Condition Invalidation**:
   Inspect `backend/app/strategies/swing_panic_dip.py:453-463` and observe that `self.staged_manager.remove_staged_order(entry_order.order_id)` executes unconditionally when `active_count >= self.max_concurrent_positions` without checking if `self.staged_manager.get_staged_exits()` contains pending exits.
