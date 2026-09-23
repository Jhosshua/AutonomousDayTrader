# Handoff Report — Adversarial Pass 2: State Machine & Flattening Exemption Audit

**Reviewer**: Reviewer 2 (`teamwork_preview_reviewer_adv_2`)  
**Roles**: Reviewer, Adversarial Critic  
**Mission**: Adversarial Pass 2 (State Machine & Flattening Exemption Audit across `backend/app/core/flattening.py`, `backend/app/core/account.py`, `backend/app/core/risk.py`, `backend/app/main.py`, and `backend/app/strategies/swing_panic_dip.py`).  
**Authoritative Documents**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md`, `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/SCOPE.md`, `/Users/mo/AutonomousDayTrader/.agents/teamwork/teamwork_preview_reviewer_adv_2/DISPATCH.md`  
**Formal Verdict**: `REQUEST_CHANGES` (1 Major Behavioral Defect Identified in Session Boundary Weekend Rollover)

---

## 1. Observation

### 1.1 Integrity Audit (System Prompt Compliance)
- Examined implementation across `backend/app/core/flattening.py`, `backend/app/core/account.py`, `backend/app/core/risk.py`, `backend/app/main.py`, and `backend/app/strategies/swing_panic_dip.py`.
- **Integrity Findings**:
  - Zero hardcoded test results or fabricated outputs embedded in source code.
  - Zero dummy, facade, or placeholder implementations.
  - Zero shortcuts bypassing required tasks.
  - Test suites (`test_swing_flattening_exemption.py`, `test_swing_indicators.py`, `test_swing_strategy.py`) execute genuine logic without self-certifying mocks or mocked bypasses.
  - **Integrity Gate**: **PASSED** (No integrity violations).

### 1.2 4-Phase EOD Flattening Engine Observations (`flattening.py`, `main.py`)
1. **Phase 1 (15:45 ET `ENTRY_LOCKOUT`)**:
   - `flattening.py:180-188`: `execute_phase_1_lockout()` issues `FlatteningDirective(phase=ENTRY_LOCKOUT, lock_new_entries=True, cancel_all_orders=False, liquidate_all_positions=False)`.
   - `main.py:268`: In `pre_trade_risk_validator`, if `is_swing` is true, `is_lockout = False`.
   - `risk.py:203-270`: Swing orders route through `if is_swing:` block and return before line 275 (`if is_entry_lockout_active:`). Swing entries and staged orders are strictly immune to Phase 1 lockout.
2. **Phase 2 (15:50 ET `ORDER_PURGE`)**:
   - `main.py:1489-1491`: In `handle_flattening_directive`:
     ```python
     for order_id, order in list(engine.working_orders.items()):
         if getattr(order, "arm", None) == TradingArm.SWING or getattr(order, "strategy_id", "") == "swing_panic_dip":
             continue
     ```
   - Swing orders in `working_orders` (such as active 2.5x ATR stop loss orders) are explicitly skipped and preserved. Staged swing orders reside in `SwingStagedOrderManager` outside of `working_orders`.
3. **Phase 3 (15:55 ET `MANDATORY_LIQUIDATION`)**:
   - `main.py:1503`: `engine.cancel_all_orders("FLATTENING_DIRECTIVE", arm=TradingArm.INTRADAY)` purges only intraday working orders.
   - `main.py:1509-1510`:
     ```python
     for sym, pos in list(account.positions.items()):
         if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
             continue
     ```
     Swing positions are explicitly bypassed from liquidation.
4. **Phase 4 (15:58 ET `ZERO_AUDIT`)**:
   - `flattening.py:231-245`: In `execute_phase_4_audit`:
     ```python
     intraday_positions = {
         sym: pos for sym, pos in open_positions.items()
         if getattr(pos, "arm", None) not in ("SWING", TradingArm.SWING)
         and getattr(pos, "strategy_id", "") != "swing_panic_dip"
     }
     intraday_working_orders = [
         order for order in working_orders
         if getattr(order, "arm", None) not in ("SWING", TradingArm.SWING)
         and getattr(order, "strategy_id", "") != "swing_panic_dip"
     }
     ```
     When only swing positions/orders remain, `len(unclosed) == 0 and len(intraday_working_orders) == 0` evaluates to True; audit passes with `AUDIT_PASSED_CLEAN_BOOK`.
   - `main.py:1552-1558`:
     ```python
     if audit_res.audit_passed:
         has_swing_open = any(
             getattr(p, "arm", None) == TradingArm.SWING or getattr(p, "strategy_id", "") == "swing_panic_dip"
             for p in account.positions.values()
         )
         if not has_swing_open:
             account.status = account.status.__class__.EOD_FLAT
     ```
     `account.status` is NOT overwritten to `EOD_FLAT` when swing positions are open; it remains `ACTIVE`.

### 1.3 Major Defect Discovered: Weekend Session Boundary Rollover (`main.py:863-940`)
1. In `main.py:1570-1600`, `_runtime_clock_loop()` runs continuously every 1 second:
   ```python
   while True:
       ...
       now_dt = flattening_engine.clock.now()
       _check_session_boundary(now_dt)
   ```
2. In `main.py:863-875`:
   ```python
   def _check_session_boundary(now_dt: datetime) -> None:
       """Reset daily risk, flattening, and account metrics when the ET session date changes."""
       global last_session_date
       if now_dt.tzinfo is None:
           now_dt = now_dt.replace(tzinfo=timezone.utc)
       session_date = now_dt.astimezone(ET_TZ).date()
       if last_session_date == session_date:
           return
   ```
3. In `main.py:936-940`:
   ```python
   # Advance holding_days counter for active swing positions across session boundary
   for sym, pos in account.positions.items():
       if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
           pos.holding_days += 1
           log.info("Advanced swing position %s holding_days to %d", sym, pos.holding_days)
   ```
4. **Empirical Reproduction of Weekend Rollover Bug**:
   Ran the following verification test against `main._check_session_boundary`:
   ```bash
   python3 -c '
   from datetime import datetime, date
   from zoneinfo import ZoneInfo
   from backend.app.core.account import Position, PositionSide, TradingArm
   from backend.app import main

   main.account.positions.clear()
   main.last_session_date = date(2026, 9, 25) # Friday
   pos = Position(symbol="MU", side=PositionSide.LONG, shares=100, avg_entry_price=100.0,
                  market_price=100.0, arm=TradingArm.SWING, strategy_id="swing_panic_dip", holding_days=0)
   main.account.positions["MU"] = pos

   ET_TZ = ZoneInfo("America/New_York")
   sat_dt = datetime(2026, 9, 26, 9, 30, 0, tzinfo=ET_TZ) # Saturday
   main._check_session_boundary(sat_dt)
   print("After Saturday:", main.account.positions["MU"].holding_days)

   sun_dt = datetime(2026, 9, 27, 9, 30, 0, tzinfo=ET_TZ) # Sunday
   main._check_session_boundary(sun_dt)
   print("After Sunday:", main.account.positions["MU"].holding_days)

   mon_dt = datetime(2026, 9, 28, 9, 30, 0, tzinfo=ET_TZ) # Monday
   main._check_session_boundary(mon_dt)
   print("After Monday:", main.account.positions["MU"].holding_days)
   '
   ```
   **Verbatim Command Output**:
   ```
   INFO:AutonomousDayTrader:New ET session 2026-09-26 detected; resetting daily session state
   INFO:AutonomousDayTrader:Advanced swing position MU holding_days to 1
   After Saturday: 1
   INFO:AutonomousDayTrader:New ET session 2026-09-27 detected; resetting daily session state
   INFO:AutonomousDayTrader:Advanced swing position MU holding_days to 2
   After Sunday: 2
   INFO:AutonomousDayTrader:New ET session 2026-09-28 detected; resetting daily session state
   INFO:AutonomousDayTrader:Advanced swing position MU holding_days to 3
   After Monday: 3
   ```
   **Observed Reality**: A position entered on Friday with `holding_days = 0` is incremented to `holding_days = 3` by Monday morning before the market even opens for its second trading session.

### 1.4 AMD Symbol Reservation & Mutual Exclusion Observations
1. In `main.py:115-141`: `swing_reserved_symbols` set, `reserve_symbol_for_swing()`, `release_symbol_for_swing()`, `is_symbol_reserved_for_swing()`.
2. In `main.py:259-261`: `pre_trade_risk_validator` rejects any intraday order for AMD if `is_symbol_reserved_for_swing("AMD", acct)` is True with rejection reason `"SYMBOL_RESERVED_FOR_SWING: Intraday entry for AMD rejected because symbol is reserved/held by Swing Engine"`.
3. In `main.py:251-257`: `pre_trade_risk_validator` rejects any swing order for AMD if an intraday position currently exists with reason `"SWING_REJECTED: Symbol AMD is currently held by Intraday strategy"`.
4. In `swing_panic_dip.py:288-301`: `evaluate_market_close` checks both open intraday positions and working intraday orders before qualifying AMD.
5. In `swing_panic_dip.py:327-328`: Reserving AMD occurs immediately upon signal qualification at 16:00 ET close, locking out any morning intraday orders for AMD.
6. Reservation release occurs cleanly in `execute_market_open` on exits, in `check_intraday_emergency_stops`, and in `execute_immediate_exit`.

### 1.5 Shared $50,000 Margin & Risk Pool Coordination Observations
1. In `risk.py:203-270`: Swing orders enforce:
   - Concurrency cap: max 2 active swing positions (`active_swing_positions_count >= 2`).
   - Slot notional cap: $25,000 max committed notional per symbol (`order_notional > max_notional + 50.00`).
   - Sizing: `authorized_qty = min(requested_qty, q_slot, q_bp)`.
2. In `risk.py:275-318`: Intraday orders enforce:
   - Concurrency cap: max 3 active intraday positions (calculated via `arm=TradingArm.INTRADAY` so swing positions do not consume intraday slots).
   - Single-position cap: 50% equity ($25,000).
   - Stop distance: $[0.0040, 0.0400]$ (40 to 400 bps).
   - Hard daily loss limit: $1,500 circuit breaker halting all new entries across both arms if tripped.
3. In `account.py:407-455`: `_recompute_account_state()` correctly tracks equity, maintenance margin (25% for long positions), margin excess, and Day Trading Buying Power (DTBP) with $25k PDT compliance.

### 1.6 Independent Test Verification
- Ran `pytest backend/tests/test_swing_flattening_exemption.py -v`: 11 passed in 0.16s.
- Ran full backend test suite `pytest backend/tests/ -q`: 398 passed in 4.15s.

---

## 2. Logic Chain

1. **Isolation of EOD Flattening Engine (Observations 1.2, 1.6)**:
   - By tagging positions, orders, and brackets with `arm: TradingArm` and checking `getattr(..., "arm", None) == TradingArm.SWING` as well as `strategy_id == "swing_panic_dip"`, Phases 1, 2, 3, and 4 in `flattening.py` and `main.py` explicitly exempt swing objects.
   - At Phase 4 (15:58 ET), `execute_phase_4_audit` checks only intraday positions and orders. Open swing positions or working swing stop-loss orders do not fail the audit. Furthermore, `account.status` is preserved as `ACTIVE` when swing positions are open.
   - Conclusion: The 15:45–15:58 ET intraday auto-flattening engine cannot liquidate or cancel swing positions or orders under any tested condition.

2. **Analysis of Session Boundary Weekend Defect (Observation 1.3)**:
   - Requirement R1 Rule 7c specifies: *"The position has been held for 5 trading days (time stop)."*
   - In `backend/app/main.py:863-940`, `_check_session_boundary` increments `pos.holding_days += 1` whenever `session_date != last_session_date`.
   - In live production, `_runtime_clock_loop` runs 24/7/365 on Railway, calling `_check_session_boundary` on wall-clock calendar ticks.
   - When a weekend occurs, Saturday (weekday 5) and Sunday (weekday 6) each trigger `_check_session_boundary`, incrementing `pos.holding_days` by 1 on Saturday and 1 on Sunday.
   - By Monday morning at 09:30 ET, `pos.holding_days` has advanced by 3 days instead of 1 trading day.
   - Consequently, Rule 7c triggers after only 3 to 4 trading days, prematurely liquidating swing positions and cutting the intended holding window by up to 40%.
   - In historical replay tests, weekend bars do not exist, so the replay leaped directly from Friday to Monday, masking this live production defect.
   - Conclusion: `_check_session_boundary` contains a major defect requiring remediation before deployment.

3. **Analysis of AMD Mutual Exclusion (Observation 1.4)**:
   - AMD is present in both `WATCHLIST_SYMBOLS` (intraday) and `SWING_SYMBOLS` (swing).
   - When AMD qualifies at 16:00 ET close, `reserve_symbol_for_swing("AMD")` adds `"AMD"` to `swing_reserved_symbols`.
   - `pre_trade_risk_validator` queries `is_symbol_reserved_for_swing("AMD", acct)` and rejects any intraday order.
   - Conversely, if an intraday trade is currently active or has working orders, swing qualification and pre-trade validation reject swing entry.
   - Conclusion: Mutual exclusion between intraday and swing for AMD is completely enforced in both directions.

4. **Analysis of Shared $50,000 Margin Pool (Observation 1.5)**:
   - Swing trading is bounded to max 2 positions of $25,000 notional ($50,000 total commitment).
   - Intraday positions are bounded to max 3 positions, capped at 50% equity each, and governed by FINRA 4:1 DTBP.
   - If either arm suffers drawdowns, the $1,500 daily loss circuit breaker halts all new order submissions.
   - FINRA 4210 maintenance margin (25% on long positions = $12,500) leaves substantial margin excess ($37,500), preventing false margin calls under normal volatility.
   - Conclusion: Shared account pool margin and concurrency coordination is sound.

---

## 3. Caveats

- **Market Exchange Holidays**: While weekend days (`weekday() >= 5`) can be easily filtered via `session_date.weekday() < 5`, US exchange holidays falling on weekdays (e.g. Good Friday, Memorial Day, Labor Day, Thanksgiving) will also trigger calendar rollover in live wall-clock mode if holiday calendars are not integrated into `_check_session_boundary`. Incorporating a holiday check or advancing `holding_days` only during active market sessions is recommended.
- **Review-Only Constraint**: In strict adherence to reviewer constraints, no implementation files were modified. The defect is documented below with actionable instructions for the remediation worker.

---

## 4. Conclusion & Findings

### Verdict
**`REQUEST_CHANGES`**

### Findings Summary

#### [Major] Finding 1: Weekend Wall-Clock Session Boundary Rollover Desynchronization
- **What**: `_check_session_boundary` in `backend/app/main.py:936-940` increments `pos.holding_days` unconditionally on calendar date changes, including non-trading weekend days (Saturday and Sunday).
- **Where**: `backend/app/main.py`, lines 868–875 and lines 936–940.
- **Why**: In live production on Railway (where `_runtime_clock_loop` runs 24/7), Saturday and Sunday each increment `pos.holding_days`. A position entered on Friday has `holding_days = 3` by Monday morning instead of `holding_days = 1`. This causes Rule 7c (5-day time stop) to trigger after only 3 to 4 trading days, truncating holding periods by up to 40% and violating Requirement R1 Rule 7c.
- **Suggested Fix Direction**:
  In `backend/app/main.py`:
  Option A (Preferred): Guard `_check_session_boundary` against non-trading weekend days:
  ```python
  def _check_session_boundary(now_dt: datetime) -> None:
      ...
      session_date = now_dt.astimezone(ET_TZ).date()
      if session_date.weekday() >= 5:
          return  # Weekend: not a trading session
  ```
  Option B: Guard `pos.holding_days` increment specifically:
  ```python
  if session_date.weekday() < 5:
      for sym, pos in account.positions.items():
          if getattr(pos, "arm", None) == TradingArm.SWING or getattr(pos, "strategy_id", "") == "swing_panic_dip":
              pos.holding_days += 1
  ```
  And add a unit test in `test_swing_flattening_exemption.py` verifying that advancing across Saturday and Sunday does NOT increment `pos.holding_days`.

### Verified Claims Matrix

| Claim | Verification Method | Status | Notes |
|---|---|---|---|
| Phase 1-4 EOD flattening exempts swing positions | Code inspection + pytest `test_handle_flattening_directive_preserves_swing_positions` | PASS | Swing positions & orders preserved through all phases |
| Phase 4 zero-audit passes with open swing positions | Code inspection + pytest `test_flattening_phase_4_audit_exempts_swing_positions_and_orders` | PASS | Passes with clean book; `account.status` stays `ACTIVE` |
| AMD symbol reservation prevents intraday collisions | Code inspection + pytest `test_amd_symbol_reservation_locks_out_intraday` | PASS | Intraday orders rejected with `SYMBOL_RESERVED_FOR_SWING` |
| Two-way mutual exclusion prevents swing from buying held AMD | Code inspection + pytest `test_swing_locked_out_when_intraday_holds_symbol` | PASS | Swing rejected with `SWING_REJECTED` |
| Shared $50k account tracks margin & concurrency | Code inspection + pytest `test_swing_concurrency_and_notional_limits`, `test_intraday_bypasses_swing_positions_concurrency` | PASS | Max 2 swing positions ($25k each) strictly binding |
| Session boundary preserves swing positions across days | Code inspection + pytest `test_session_boundary_preserves_swing_positions_and_increments_holding_days` | PASS | Swing positions & protective stops survive session rollover |
| Holding days increments accurately across weekends/holidays | Adversarial Python script reproducing Saturday/Sunday increments | **FAIL** | Sat/Sun falsely increment `holding_days` by +2 |

---

## 5. Verification Method

To independently verify this review and reproduce the finding:

1. **Reproduce the Weekend Rollover Defect**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   python3 -c '
   from datetime import datetime, date
   from zoneinfo import ZoneInfo
   from backend.app.core.account import Position, PositionSide, TradingArm
   from backend.app import main

   main.account.positions.clear()
   main.last_session_date = date(2026, 9, 25) # Friday
   pos = Position(symbol="MU", side=PositionSide.LONG, shares=100, avg_entry_price=100.0,
                  market_price=100.0, arm=TradingArm.SWING, strategy_id="swing_panic_dip", holding_days=0)
   main.account.positions["MU"] = pos

   ET_TZ = ZoneInfo("America/New_York")
   sat_dt = datetime(2026, 9, 26, 9, 30, 0, tzinfo=ET_TZ) # Saturday
   main._check_session_boundary(sat_dt)
   print("After Saturday:", main.account.positions["MU"].holding_days)

   sun_dt = datetime(2026, 9, 27, 9, 30, 0, tzinfo=ET_TZ) # Sunday
   main._check_session_boundary(sun_dt)
   print("After Sunday:", main.account.positions["MU"].holding_days)

   mon_dt = datetime(2026, 9, 28, 9, 30, 0, tzinfo=ET_TZ) # Monday
   main._check_session_boundary(mon_dt)
   print("After Monday:", main.account.positions["MU"].holding_days)
   '
   ```
   *Expected defect behavior*: Outputs `After Saturday: 1`, `After Sunday: 2`, `After Monday: 3`.

2. **Run Existing Test Suite**:
   ```bash
   cd /Users/mo/AutonomousDayTrader
   pytest backend/tests/test_swing_flattening_exemption.py -v
   pytest backend/tests/ -q
   ```
   *Result*: 398 passed in 4.15s.

3. **Invalidation Condition for Requested Change**:
   The `REQUEST_CHANGES` verdict is resolved when:
   - `_check_session_boundary` is updated to exclude weekend days (`session_date.weekday() >= 5`).
   - A regression test is added demonstrating that advancing from Friday through Saturday and Sunday to Monday increments `pos.holding_days` by exactly 1 (for Monday's session), not 3.
   - All 398 existing tests continue to pass with 0 regressions.
