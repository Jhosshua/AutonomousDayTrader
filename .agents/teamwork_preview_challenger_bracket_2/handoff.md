# Challenger 2 Handoff Report: Boundary & Edge Case Verification

## 1. Observation

### Observation 1.1: Stop Distance Clamping Formula & Behavior
In `backend/app/strategies/orb.py` (lines 178–183):
```python
        # Institutional stop distance clamping [0.4%, 4.0%]
        min_dist = round(entry_price * 0.004, 4)
        max_dist = round(entry_price * 0.040, 4)
        risk = max(min_dist, min(max_dist, raw_dist))
        stop_loss = round(entry_price - risk if sig_type == "BUY" else entry_price + risk, 4)
```
In `backend/app/strategies/news_momentum.py` (lines 238–242 and 265–269):
```python
            min_dist = round(entry_price * 0.004, 4)
            max_dist = round(entry_price * 0.040, 4)
            raw_dist = max(0.10, entry_price - round(bar.low - 0.02, 4))
            risk = max(min_dist, min(max_dist, raw_dist))
            stop_loss = round(entry_price - risk, 4)
```
Empirical test results from `pytest tests/e2e/test_challenger_bracket_2.py`:
- At $5.00:
  - Tight range ORB BUY: entry=5.01, stop_loss=4.91, stop_dist=0.10, ratio=0.01996 (1.996%), within [0.004, 0.040].
  - Wide range ORB BUY: entry=6.82, stop_loss=6.5472, stop_dist=0.2728, ratio=0.04000 (4.00%), within [0.004, 0.040].
  - Bearish breakdown ORB SELL: entry=4.8755, stop_loss=4.9755, stop_dist=0.10, ratio=0.0205, within [0.004, 0.040].
  - News Momentum BUY tight: entry=5.00, stop_loss=4.90, stop_dist=0.10, ratio=0.020, within [0.004, 0.040].
  - News Momentum BUY wide: entry=5.00, stop_loss=4.80, stop_dist=0.20, ratio=0.040, within [0.004, 0.040].
  - News Momentum SELL wide: entry=5.00, stop_loss=5.20, stop_dist=0.20, ratio=0.040, within [0.004, 0.040].
- At $150.00:
  - Tight range ORB BUY: entry=150.30, stop_loss=149.6988, stop_dist=0.6012, ratio=0.0040 (0.40%), hits lower bound clamp exactly.
  - Wide range ORB BUY: entry=174.22, stop_loss=167.2512, stop_dist=6.9688, ratio=0.0400 (4.00%), hits upper bound clamp exactly.
  - News Momentum BUY tight: entry=150.00, stop_loss=149.40, stop_dist=0.60, ratio=0.0040 (0.40%), hits lower bound clamp.
  - News Momentum BUY wide: entry=150.00, stop_loss=144.00, stop_dist=6.00, ratio=0.0400 (4.00%), hits upper bound clamp.
- At $1,000.00:
  - Tight range ORB BUY: entry=1002.00, stop_loss=997.992, stop_dist=4.008, ratio=0.0040 (0.40%), hits lower bound clamp.
  - Wide range ORB BUY: entry=1161.50, stop_loss=1115.04, stop_dist=46.46, ratio=0.0400 (4.00%), hits upper bound clamp.
  - News Momentum BUY tight: entry=1000.00, stop_loss=996.00, stop_dist=4.00, ratio=0.0040 (0.40%), hits lower bound clamp.
  - News Momentum BUY wide: entry=1000.00, stop_loss=960.00, stop_dist=40.00, ratio=0.0400 (4.00%), hits upper bound clamp.
- Fuzzing & extremes:
  - Tested continuous price points from $1.00 to $5,000.00 across 40+ price/volatility pairs. 100% of signals satisfied `0.004 - 1e-6 <= stop_dist / entry_price <= 0.040 + 1e-6`.

### Observation 1.2: Session Boundary Purge in `_check_session_boundary`
In `backend/app/main.py` (lines 378–408):
```python
def _check_session_boundary(now_dt: datetime) -> None:
    """Reset daily risk, flattening, and account metrics when the ET session date changes."""
    global last_session_date
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    session_date = now_dt.astimezone(ET_TZ).date()
    if last_session_date == session_date:
        return
    is_first_observation = last_session_date is None
    last_session_date = session_date
    if is_first_observation:
        return
    log.info("New ET session %s detected; resetting daily session state", session_date)
    if engine.working_orders:
        log.warning("Session boundary detected with %d open working orders; cancelling all", len(engine.working_orders))
        engine.cancel_all_orders("SESSION_BOUNDARY_PURGE")
        engine.working_orders.clear()
    risk_engine.reset_daily_metrics(account.equity)
    flattening_engine.reset_for_new_session()
    account.reset_daily_metrics(account.equity)
    bracket_manager.brackets.clear()
    bracket_manager.symbol_to_bracket.clear()
    bracket_manager.order_to_bracket.clear()
    entry_order_to_bracket.clear()
    bracket_realized_pnl.clear()
    completed_brackets_recorded.clear()
    for strategy in strategies:
        strategy.reset_daily_stats()
```
Empirical test results from `TestSessionBoundaryPurge` and `TestSubsystemSessionBoundaryPurge`:
- Transition from 2026-09-21 to 2026-09-22 cancelled all 4 active orders (AAPL, NVDA, TSLA, MSFT).
- `o.status == OrderState.CANCELLED` for all orders, with `o.completed_at` populated.
- Partially filled orders (AMD 10/20 shares) transitioned to `CANCELLED`.
- `engine.working_orders` was cleanly emptied (`len == 0`).
- False-trigger tests:
  - Same-day tick (10:00 ET -> 15:30 ET): 0 orders cancelled (`len(working_orders) == 4`).
  - UTC midnight tick (2026-09-22 01:00 UTC = 2026-09-21 21:00 ET, same ET date): 0 orders cancelled (`len(working_orders) == 4`).
- All bracket manager tables (`brackets`, `symbol_to_bracket`, `order_to_bracket`), `entry_order_to_bracket`, `bracket_realized_pnl`, and strategy states were cleanly purged.

### Observation 1.3: Pre-Market Flattening Phase Transitions
In `backend/app/core/flattening.py` (lines 114–173):
```python
        # Pre-Market: Before 09:30:00 ET
        elif t < self.schedule.market_open_time:
            self.current_phase = FlatteningPhase.PRE_MARKET
            return None

        # Normal Trading: 09:30:00 - 15:44:59 ET
        else:
            self.current_phase = FlatteningPhase.NORMAL_TRADING
            return None
```
Empirical test results from `TestPreMarketFlatteningPhaseTransitions`:
- 04:00:00 ET: `current_phase == FlatteningPhase.PRE_MARKET`, directive is `None`.
- 08:30:00 ET: `current_phase == FlatteningPhase.PRE_MARKET`, directive is `None`.
- 09:29:59 ET: `current_phase == FlatteningPhase.PRE_MARKET`, directive is `None`.
- 09:29:59.999999 ET: `current_phase == FlatteningPhase.PRE_MARKET`, directive is `None`.
- 09:30:00.000000 ET: `current_phase == FlatteningPhase.NORMAL_TRADING`, directive is `None`.
- 10:30:00 ET: `current_phase == FlatteningPhase.NORMAL_TRADING`, directive is `None`.
- 12:30:00 ET: `current_phase == FlatteningPhase.NORMAL_TRADING`, directive is `None`.
- 15:44:59.999999 ET: `current_phase == FlatteningPhase.NORMAL_TRADING`, directive is `None`.
- 15:45:00.000000 ET: `current_phase == FlatteningPhase.ENTRY_LOCKOUT`, directive action `LOCK_NEW_ENTRIES`.
- UTC input: 13:29:59 UTC correctly maps to `PRE_MARKET`, 13:30:00 UTC correctly maps to `NORMAL_TRADING`.
- Reset for new session restores all phase execution flags and cycles back to `PRE_MARKET`.

### Observation 1.4: Port and Process Hygiene
Executed `bash scripts/verify_port_hygiene.sh`:
```
🔍 Auditing port hygiene across project ports: 3005 8005 8080...
✅ Port 3005 is clean and liberated.
✅ Port 8005 is clean and liberated.
✅ Port 8080 is clean and liberated.
✨ All ports verified clean. Zero lingering daemons.
```

---

## 2. Logic Chain

1. **Stop Distance Clamping**:
   - The institutional risk mandate requires stop loss distances to be clamped between 0.4% and 4.0% of the entry price (`0.004 <= stop_dist / entry_price <= 0.040`).
   - For low-priced stocks ($5.00), minimum distance is $0.02 and maximum is $0.20. When raw volatility is ultra-low, the ATR/tick floor ($0.10) sets risk to 2.0%, which is comfortably inside [0.4%, 4.0%]. When raw volatility exceeds $0.20, `min(max_dist, raw_dist)` caps risk at $0.20 (4.0%).
   - For mid-priced stocks ($150.00), minimum distance is $0.60 (0.4%) and maximum is $6.00 (4.0%). Both lower and upper bounds were directly exercised and clamped.
   - For high-priced stocks ($1,000.00), minimum distance is $4.00 (0.4%) and maximum is $40.00 (4.0%). Both bounds were directly exercised and clamped.
   - Property fuzzing across $1.00 to $5,000.00 confirmed that floating-point representations adhere to the boundary constraint without underflow or overflow.

2. **Session Boundary Purge**:
   - `_check_session_boundary` uses `now_dt.astimezone(ET_TZ).date()` to track the session date in US Eastern Time.
   - On the first observation, it records the session date without triggering purges.
   - When the ET date changes (e.g. at 00:00 ET / 04:00 UTC), `engine.cancel_all_orders("SESSION_BOUNDARY_PURGE")` iterates through `engine.working_orders` and transitions all orders from `ACCEPTED` and `PARTIALLY_FILLED` to `CANCELLED`.
   - `engine.working_orders.clear()` guarantees that working order state is completely empty on the new day.
   - Brackets, linkages, realized PnL caches, and strategy daily states are thoroughly reset, preventing cross-day state leakage.
   - Invariant checks verified that intra-day ticks (e.g. 10:00 to 15:30 ET) and UTC midnight crossings that do not change ET calendar date do not erroneously cancel working orders.

3. **Pre-Market Flattening Phase Transitions**:
   - `ZeroOvernightFlatteningEngine.check_time_tick` compares the Eastern Time-of-day against `FlatteningSchedule`.
   - Any time strictly before `market_open_time` (09:30:00 ET) evaluates to `PRE_MARKET`.
   - Any time from 09:30:00 up to `phase1_lockout_time` (15:45:00 ET) evaluates to `NORMAL_TRADING`.
   - Microsecond precision testing verified the exact boundary cutoffs: `09:29:59.999999` is `PRE_MARKET`, and `09:30:00.000000` is `NORMAL_TRADING`.

4. **Port Hygiene**:
   - `scripts/verify_port_hygiene.sh` uses `lsof -tiTCP:<port> -sTCP:LISTEN` to confirm ports 3005, 8005, and 8080 are free.
   - Audits confirmed zero lingering processes.

---

## 3. Caveats

1. **Non-boundary Test Issue Noted in `test_ui_stream_resilience.py`**:
   - During verification, running the full opaque-box runner (`scripts/run_e2e_tests.sh`) surfaced an unrelated failure in `tests/e2e/test_ui_stream_resilience.py::test_high_frequency_broadcast_and_receipt` line 59.
   - Root cause: The test creates a bracket via `bracket_manager.create_bracket(...)` which initializes in `PENDING_ENTRY` state, and immediately sends a `TIGHTEN_STOP` action without activating the bracket via `activate_bracket_on_fill(...)`. In `backend/app/core/bracket.py` line 458, `manual_tighten_stop` correctly guards against modifying pending brackets (`if bracket.status not in (BracketStatus.ACTIVE, BracketStatus.TARGET_1_HIT): return NO_ACTION`).
   - This is an issue in the test harness setup in `test_ui_stream_resilience.py` (missing `activate_bracket_on_fill` call like line 203 of the same file), NOT a bug in the production boundary code under Challenger 2 review. As per Challenger review-only rules, this finding is documented here for the parent agent to coordinate remediation.

2. **Floating-Point Precision Tolerance**:
   - In Python IEEE 754 float arithmetic, `entry_price - stop_loss` on high-priced stocks (e.g. $1161.50 - $1115.04 = 46.460000000000036) can produce an unrounded representation 3.6e-17 above 0.04. Clamping logic in `orb.py` and `news_momentum.py` uses `round(..., 4)` which strictly yields 4 decimal places. Assertions in test suites must compare with `1e-6` precision tolerance.

---

## 4. Conclusion

**Verdict: `APPROVE`**

All four boundary and edge-case requirements assigned to Challenger 2 are empirically verified and meet all institutional specifications:
1. Stop distance clamping strictly conforms to `0.004 <= stop_dist / entry_price <= 0.040` across low ($5.00), mid ($150.00), high ($1,000.00), and extreme ($1.00, $5,000.00) stock prices for both ORB and News Momentum strategies.
2. ET session boundary date transition cleanly purges and clears all working orders, cancels brackets, and resets daily strategy/risk state without false-triggering intra-day.
3. Flattening engine accurately classifies `PRE_MARKET` (< 09:30 ET) and `NORMAL_TRADING` (09:30–15:45 ET) down to microsecond boundaries and with full UTC timezone conversion awareness.
4. Port hygiene is fully verified: ports 3005, 8005, and 8080 are 100% free with zero lingering background daemons.

---

## 5. Verification Method

To independently verify all findings:

1. Run the dedicated Challenger 2 test suite:
   ```bash
   pytest tests/e2e/test_challenger_bracket_2.py -v
   ```
   Expected: 25 passed in < 0.3s.

2. Run backend unit tests:
   ```bash
   pytest backend/tests/unit/ -v
   ```
   Expected: 133 passed.

3. Verify port hygiene:
   ```bash
   bash scripts/verify_port_hygiene.sh
   ```
   Expected:
   ```
   🔍 Auditing port hygiene across project ports: 3005 8005 8080...
   ✅ Port 3005 is clean and liberated.
   ✅ Port 8005 is clean and liberated.
   ✅ Port 8080 is clean and liberated.
   ✨ All ports verified clean. Zero lingering daemons.
   ```
