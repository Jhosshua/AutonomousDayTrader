# Handoff Report: Milestone 2 Strategy Adversarial Verification

**Agent**: `challenger_m2_1` (Strategy Adversarial Verifier)  
**Target Milestone**: Milestone 2 (`strategies_adaptation`)  
**Parent Orchestrator**: `f9df3e28-501d-4830-bf1f-140b6216f49e`  
**Date**: 2026-09-20  
**Handoff Type**: Hard Handoff (Adversarial Audit Complete)  
**Structured Verdict**: **`REQUEST_CHANGES`**

---

## 1. Observation

Direct empirical observations across the codebase and verification test suite:

### 1.1 Mandated Objective Tests
1. **False breakouts in ORB (`backend/app/strategies/orb.py`)**:
   - `evaluate_orb_signal` requires `rvol >= 1.80` and `close_p > range_high` (Long) or `close_p < range_low` (Short).
   - Intra-bar piercing of range high (`high > range_high`) with sub-threshold volume (`rvol < 1.80`) correctly yields `None` (verified via `test_orb_false_breakout_piercing_high_low_rvol`).
   - Intra-bar piercing of range high with high volume (`rvol = 4.0x`) closing inside range (`close <= range_high`) correctly yields `None` without consuming `breakout_fired` (verified via `test_orb_false_breakout_piercing_high_closing_back_inside_range`).
   - A subsequent genuine breakout following a prior false breakout successfully fires (verified via `test_orb_subsequent_genuine_breakout_after_false_breakout`).
   - 15-minute ORB bars prior to 09:45 ET do not fire (verified via `test_orb_15_minute_range_timing_boundaries`).

2. **News Contradiction Breaker in News Momentum (`backend/app/strategies/news_momentum.py`)**:
   - In `on_news`, when holding a `LONG` position and sentiment is `< -0.35`, the strategy emits an emergency market `SELL` order with `confidence = 1.0` and clears the symbol from `monitored_positions` (verified via `test_news_contradiction_breaker_long_position_emergency_liquidation`).
   - When holding a `SHORT` position and sentiment is `> 0.35`, it emits an emergency market `BUY` order with `confidence = 1.0` (verified via `test_news_contradiction_breaker_short_position_emergency_liquidation`).
   - Mild sentiment (`-0.35 <= S <= 0.35`) does not trigger liquidation (verified via `test_news_mild_headline_does_not_trigger_emergency_liquidation`).
   - Rapid back-to-back adverse headlines are idempotent and do not flip the position into an accidental short (verified via `test_news_contradiction_idempotency_prevents_unintended_short`).

3. **Mean Reversion Edge Cases (`backend/app/strategies/mean_reversion.py`)**:
   - Extreme parabolic runaway trends with $Z \ge 3.0$ and $RSI \ge 85$ without an exhaustion rejection wick (wick ratio $< 50\%$) strictly emit NO signals (verified via `test_mean_reversion_parabolic_runaway_bull_trend_without_wick`).
   - Waterfall crashes ($Z \le -3.0$, $RSI \le 15$) without lower rejection wicks strictly emit NO signals (verified via `test_mean_reversion_waterfall_crash_without_wick`).
   - Trades with unfavorable reward-to-risk ($< 1.20$) are suppressed (verified via `test_mean_reversion_unfavorable_reward_to_risk_suppression`).
   - Trades during `OPEN_VOLATILITY_FLUSH` (09:30–10:00 ET) are strictly locked out (verified via `test_mean_reversion_strictly_disabled_during_morning_flush`).

---

### 1.2 Defects and Vulnerabilities Discovered

#### Defect 1: CRITICAL — ALL Strategy Market Entry Orders Rejected by Risk Engine in `main.py`
- **Location**: `backend/app/main.py:87-88`
- **Verbatim Code**:
  ```python
  # Estimate stop price
  est_price = order.limit_price or order.stop_price or 100.0
  s_price = order.stop_price or (est_price * 0.98 if order.side == OrderSide.BUY else est_price * 1.02)
  ```
- **Observed Behavior**:
  When a strategy generates a market entry order with `stop_price` set:
  * `order.limit_price` is `None`.
  * `est_price` falls back to `order.stop_price`.
  * `s_price` evaluates to `order.stop_price`.
  * In `backend/app/core/risk.py:201`:
    `stop_dist = abs(entry_price - stop_price) = abs(order.stop_price - order.stop_price) = 0.0`.
  * The risk engine evaluates `stop_dist <= 0` and returns `rejection_code="INVALID_PRICE_GEOMETRY"`.
  * Result: `engine.submit_order()` transitions every market entry order to `OrderState.REJECTED`. **Zero strategy entry trades can ever be accepted into the engine.**
- **Empirical Test**: `test_reproduce_defect_market_order_rejected_invalid_price_geometry` in `backend/tests/unit/test_empirical_stress_m2.py` passes and proves the defect.

#### Defect 2: CRITICAL — `TypeError` Argument Mismatch on `create_bracket` in `main.py`
- **Location**: `backend/app/main.py:224-235`
- **Verbatim Code**:
  ```python
  bracket_manager.create_bracket(
      symbol=sym,
      entry_order_id=submitted.id,
      side=side.value,
      total_qty=qty,
      entry_price=signal.entry_price,
      stop_loss_price=signal.stop_loss,
      take_profit_1_price=signal.take_profit_1,
      take_profit_2_price=signal.take_profit_2,
      strategy_id=signal.strategy_id,
      timestamp=signal.timestamp,
  )
  ```
- **Observed Behavior**:
  * `DynamicBracketManager.create_bracket` signature in `backend/app/core/bracket.py:78-90` is:
    `create_bracket(self, bracket_id: str, symbol: str, side: str, total_qty: int, entry_price: float, stop_price: float, strategy_id: str = "MANUAL", ...)`
  * `main.py` omits `bracket_id` and passes unexpected kwargs `entry_order_id`, `stop_loss_price`, `take_profit_1_price`, `take_profit_2_price`.
  * Result: If an entry order is accepted, `execute_strategy_signal` crashes with `TypeError: create_bracket() got an unexpected keyword argument 'entry_order_id'`.
- **Empirical Test**: `test_reproduce_defect_create_bracket_argument_mismatch` passes and reproduces the `TypeError`.

#### Defect 3: HIGH — Discarded Bracket Cancellation Directive on News Contradiction in `main.py`
- **Location**: `backend/app/main.py:188`
- **Verbatim Code**:
  ```python
  bracket_manager.cancel_bracket_for_flattening(sym, reason=signal.reason)
  ```
- **Observed Behavior**:
  * `cancel_bracket_for_flattening` returns a `BracketUpdateDirective` with `orders_to_cancel=[stop_order_id, target_1_order_id, target_2_order_id]`.
  * `main.py` ignores the return value and never calls `engine.cancel_order()` for the working child orders.
  * Result: If bracket child orders were active in `engine.working_orders`, they remain orphaned after position liquidation. If a subsequent bar touches their price levels, orphaned orders fill and open an unintended opposing position.
- **Empirical Test**: `test_reproduce_defect_news_contradiction_bracket_cancellation_directive_discarded` passes and proves the gap.

#### Defect 4: MEDIUM — Unused RSI Filter in Mean Reversion Strategy
- **Location**: `backend/app/strategies/mean_reversion.py:143-145, 175-177`
- **Verbatim Code**:
  ```python
  is_rsi_overbought = rsi >= self.rsi_overbought or rsi >= 70.0
  if has_wick_rejection and has_climax:
  ...
  is_rsi_oversold = rsi <= self.rsi_oversold or rsi <= 30.0
  if has_wick_rejection and has_climax:
  ```
- **Observed Behavior**:
  * `is_rsi_overbought` and `is_rsi_oversold` are computed but omitted from the subsequent `if` condition.
  * Result: A stock with $Z \ge 2.50$, volume climax, and wick rejection triggers a trade even if RSI is neutral (e.g., $RSI = 50.0$), violating the requirement of RSI-14 extreme confirmation.
- **Empirical Test**: `test_reproduce_defect_mean_reversion_rsi_condition_omitted` passes and confirms the omission.

#### Defect 5: LOW — RVOL Calculation Self-Inclusion Attenuation in ORB
- **Location**: `backend/app/strategies/orb.py:104, 147-149`
- **Verbatim Code**:
  ```python
  state.all_bars.append(bar)
  ...
  recent_vols = [b.volume for b in state.all_bars[-20:]]
  avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else state.baseline_volume / 20.0
  rvol = round(bar.volume / max(1.0, avg_vol), 2)
  ```
- **Observed Behavior**:
  * `bar` is appended to `all_bars` before slicing `[-20:]`, meaning `recent_vols` includes the breakout bar itself.
  * Mathematical consequence: For a 5-bar opening range (6 bars total), $RVOL = 6V / (S + V)$. An actual 1.80x surge over previous bars produces an $RVOL$ of only 1.59x. To achieve $RVOL \ge 1.80$, volume must actually be $\ge 2.14\times$ the prior baseline.
- **Empirical Test**: `test_orb_rvol_self_inclusion_mathematical_attenuation` passes and proves the attenuation.

---

## 2. Logic Chain

1. **Strategy Core Functionality**:
   - ORB, News Momentum, Mean Reversion, and VWAP Pullback have mathematically correct standalone algorithms (as verified by tests 1–19 in `test_strategies.py` and tests 1–18 in `test_empirical_stress_m2.py`).
2. **Integration Failure in `main.py`**:
   - `worker_m2` verified strategy unit tests using synthetic `SignalEvent` assertions, but did not execute end-to-end integration tests routing strategy signals through `main.py:execute_strategy_signal`.
   - In `pre_trade_risk_validator` (`main.py:87`), for Market orders without a limit price, setting `est_price = order.limit_price or order.stop_price` causes `est_price == s_price`, yielding `stop_dist = 0` and triggering `INVALID_PRICE_GEOMETRY` rejection.
   - Because of this, every strategy market entry order is rejected at submission time.
   - Furthermore, even if accepted, calling `bracket_manager.create_bracket` with kwargs `entry_order_id`, `stop_loss_price`, etc. immediately throws a `TypeError`.
3. **Execution Safety**:
   - On news contradiction exit, working bracket orders are not cancelled in `engine.working_orders`, posing an orphaned fill hazard.
4. **Specification Conformance**:
   - Mean reversion does not verify `is_rsi_overbought` / `is_rsi_oversold` in its execution branch.
5. **Deduction**:
   - The system cannot enter intraday positions in live integration without runtime errors or rejections. Therefore, Milestone 2 must be **`REQUEST_CHANGES`** until these defects are repaired.

---

## 3. Caveats

- Process hygiene and host port liberation were fully verified: ports 8005, 8080, and 3005 are clean with zero orphaned processes.
- All 248 existing Tier 1–4 E2E tests pass because they test backend components via direct API mocks rather than the `main.py` strategy-to-bracket event bus flow.

---

## 4. Conclusion

**Verdict: `REQUEST_CHANGES`**

The worker must implement the following specific remediations:
1. **Fix `pre_trade_risk_validator` in `backend/app/main.py:87-88`**:
   For MARKET orders, obtain `est_price` from the bar close or quote price (e.g. `order.limit_price or (acct.positions[order.symbol].market_price if order.symbol in acct.positions else 100.0)`), and DO NOT fallback `est_price` to `order.stop_price`.
2. **Fix `create_bracket` call in `backend/app/main.py:224-235`**:
   Match `DynamicBracketManager.create_bracket` signature:
   ```python
   bracket_manager.create_bracket(
       bracket_id=f"brk_{submitted.id}",
       symbol=sym,
       side="LONG" if side == OrderSide.BUY else "SHORT",
       total_qty=qty,
       entry_price=signal.entry_price,
       stop_price=signal.stop_loss,
       strategy_id=signal.strategy_id,
       timestamp=signal.timestamp,
   )
   ```
3. **Handle bracket cancellation directive in `backend/app/main.py:188`**:
   ```python
   dir_cancel = bracket_manager.cancel_bracket_for_flattening(sym, reason=signal.reason)
   if dir_cancel and dir_cancel.orders_to_cancel:
       for oid in dir_cancel.orders_to_cancel:
           if oid in engine.working_orders:
               engine.cancel_order(oid, reason="NEWS_CONTRADICTION")
   ```
4. **Include RSI condition in `backend/app/strategies/mean_reversion.py:145,177`**:
   Update `if has_wick_rejection and has_climax:` to:
   `if has_wick_rejection and has_climax and is_rsi_overbought:` (Short) and
   `if has_wick_rejection and has_climax and is_rsi_oversold:` (Long).
5. **Refine ORB RVOL baseline calculation in `backend/app/strategies/orb.py:147`**:
   Exclude the current breakout bar from the baseline volume average (`state.all_bars[:-1]`), or utilize `calculate_rvol(bar.volume, avg_vol)`.

---

## 5. Verification Method

To reproduce the findings and verify the test harness independently:

1. **Run Milestone 2 Adversarial Stress Suite**:
   ```bash
   PYTHONPATH=. pytest backend/tests/unit/test_empirical_stress_m2.py -v
   ```
   *Result*: 24 passed in < 0.25s (including 4 empirical defect proofs).

2. **Run Full Backend Unit & Stress Suite**:
   ```bash
   PYTHONPATH=. pytest backend/tests/ -v
   ```
   *Result*: 136 passed, 2 xfailed in < 0.8s.

3. **Verify Host Port Hygiene**:
   ```bash
   lsof -i :8005 -i :8080 -i :3005 || echo "CLEAN: All ports free"
   ```
   *Result*: `CLEAN: All ports free`.
