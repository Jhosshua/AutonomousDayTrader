# Architecture & Lookahead Bias Audit Report

**Auditor**: Reviewer 1 (Architecture & Lookahead Bias Auditor)  
**Date**: 2026-09-23T04:14:00Z  
**Target Under Review**: Worker 1 Remediation (PR / Changeset for Strategy & Execution Architecture)  
**Gate Verdict**: **REQUEST_CHANGES**

---

## 1. Executive Summary & Verdict

Worker 1 implemented significant structural enhancements aimed at addressing the empirical defects identified in live paper execution (notably context blindness, unrealistic 1.5R target geometry, and crude entry triggers).

However, an exhaustive architectural, mathematical, and adversarial code audit has surfaced **1 CRITICAL finding**, **3 MAJOR findings**, and **1 MINOR finding**. Most notably:
1. **Critical Inverted Logic in Market Trend Filter**: In `backend/app/core/market_filter.py:295-301`, the policy for `mean_reversion` is backwards: during a strong `BULLISH` market rally, it approves `SELL` (shorting) orders claiming they are "aligned with MarketTrend.BULLISH", while rejecting `BUY` orders. Conversely, during a `BEARISH` market decline, it approves `BUY` (catching a falling knife) while rejecting `SELL`. This directly recreates the catastrophic failure mode observed on 2026-09-22 where high-beta assets were shorted into a market-wide rally.
2. **Forward Data Leakage Vulnerability via `abs()` in Staleness Check**: In `backend/app/core/market_filter.py:185, 191`, computing elapsed time as `abs((now - spy_ts).total_seconds())` allows future index bars (`spy_ts > now`) to pass freshness checks. If index bars arrive out of order or ahead of symbol bars, future data leaks into the trend determination for past signals.
3. **E2E Test Suite Regression**: While `pytest backend/tests -v` passes (223/223), the full end-to-end regression suite (`python3 tests/e2e/runner.py`) fails with **7 test failures** across `test_tier5_adversarial.py` and `test_challenger_bracket_2.py`, violating Acceptance Criterion R4 ("100% pass rate with zero regression").
4. **Execution Slippage Hazard on Fixed Absolute Overrides**: Overriding bracket targets with pre-calculated absolute prices (`signal.take_profit_1`, `signal.take_profit_2`) without validating `(target_price - fill_price) * direction > 0` risks placing immediate resting exit orders on the loss side of a filled market order if slippage occurs.

Until these findings are remediated, the changes cannot be certified for live production deployment.

---

## 2. Exhaustive Audit by Dimension

### A. Lookahead Bias & Forward Data Leakage Audit
- **MarketTrendFilter (`backend/app/core/market_filter.py`)**:
  - Anchored VWAP and EMA 9/21 are computed online using closed 1-minute bars (`typical_p = (h + l + c) / 3`, cumulative $PV$ and volume, and recursive exponential smoothing). No future bars or future ticks are referenced within `IndexState.update_bar()`.
  - Pre-market bars prior to 09:30:00 ET are strictly discarded, ensuring the regular session VWAP anchor is clean.
  - **VULNERABILITY DETECTED (`market_filter.py:185, 191`)**:
    ```python
    dt_spy = abs((now - spy_ts).total_seconds())
    dt_qqq = abs((now - qqq_ts).total_seconds())
    ```
    If `spy_ts > now` (e.g. index data arrives with a clock skew or out of order ahead of the stock signal timestamp `now`), `(now - spy_ts)` is negative. Applying `abs()` converts negative elapsed time to positive, treating future index bars as valid "fresh" past observations. This is forward data leakage. It must strictly require `0 <= (now - spy_ts).total_seconds() <= self.stale_threshold_sec`.
- **Opening Range Breakout (`backend/app/strategies/orb.py`)**:
  - `prior_bars = state.all_bars[:-1][-20:]` correctly slices `[:-1]`, excluding the candidate breakout bar from the baseline average volume calculation.
  - `calculate_atr(state.all_bars, period=14)` uses the closed bars up to and including the current bar.
  - CLV and range checks operate strictly on the completed candidate bar. Zero lookahead detected.
- **News Momentum (`backend/app/strategies/news_momentum.py`)**:
  - `recent_volumes = [float(b.volume) for b in self.recent_bars[sym][:-1][-20:]]` correctly excludes the candidate surge bar from the SMA20 volume baseline.
  - Candle direction confirmation `bar.close > bar.open` (for BUY) and `bar.close < bar.open` (for SELL) operates strictly on the completed bar. Zero lookahead detected.
- **Mean Reversion (`backend/app/strategies/mean_reversion.py`)**:
  - `volumes[:-1]` correctly excludes the current bar from the volume baseline.
  - Z-score, RSI, and ATR use completed historical bars. Zero lookahead detected.

---

### B. Invariant Violations Audit
- **$1,500 Daily Drawdown Circuit Breaker**:
  - `backend/app/config.py:MAX_DAILY_LOSS_LIMIT = 1500.0` is wired into `risk_engine = InstitutionalRiskEngine(config=RiskEngineConfig(max_daily_loss=settings.MAX_DAILY_LOSS_LIMIT, ...))`.
  - Confirmed via `backend/tests/unit/test_risk.py::test_circuit_breaker_hard_halt_at_1500_loss` (PASSED). Invariant strictly preserved.
- **$25,000 Single Position Cap**:
  - `backend/app/config.py:MAX_POSITION_NOTIONAL = 25000.0` is wired into `main.py` and `risk_engine`.
  - Confirmed via `backend/tests/unit/test_risk.py::test_production_wiring_caps_a_single_position_at_25k` (PASSED). Invariant strictly preserved.
- **0.40% to 4.00% Stop Guardrails**:
  - In `mean_reversion.py:172, 205`, raw stop distances are routed through `resolve_stop(entry_price, raw_dist, is_buy)`. Previously, unscaled ATR stops could violate the 40 bps floor. Now, stops tighter than 0.40% are cleanly expanded to 0.40% with outward rounding.
  - In `bracket.py:get_breakeven_buffer()`, dynamic scaling `max(0.04, round(entry_price * 0.0005, 2))` ensures the breakeven ratchet maintains a safe 5 bps distance without colliding with entry noise.
  - Invariant strictly preserved.

---

### C. Interface Conformance Audit
- **`MarketTrendFilter` ↔ `backend/app/main.py`**:
  - Instantiated as `market_filter = MarketTrendFilter()` in `main.py:63`.
  - Session reset invoked on ET rollover in `_check_session_boundary(now_dt)` (`main.py:756`) and `reset_runtime_state()` (`main.py:1347`).
  - Bar dispatch wired in `handle_bar_event()` (`main.py:984-985`):
    ```python
    if bar.symbol.upper() in ("SPY", "QQQ"):
        market_filter.on_bar(bar)
    ```
- **`MarketTrendFilter` ↔ `backend/app/strategies/adaptation.py`**:
  - `adaptation_engine` receives `market_filter` in constructor (`main.py:73`).
  - In `evaluate_signal_admission()`, signal gating evaluates:
    ```python
    permitted, reason = self.market_filter.is_signal_permitted(
        strategy_id=signal.strategy_id,
        side=signal.side,
        symbol=signal.symbol,
        asof=signal.timestamp,
        catalyst_sentiment=catalyst_sentiment,
        volume_surge=volume_surge,
    )
    ```
  - In `get_market_context()`, `market_trend` is exported to the UI payload.
  - **MINOR INTERFACE DEFECT**: `adaptation.get_market_context()` calls `self.market_filter.get_current_trend()` without passing `asof`. When called during simulation or replay, `get_current_trend()` compares historical bar timestamps against wall-clock `datetime.now(timezone.utc)`, returning `MarketTrend.UNKNOWN`. It should pass `self.last_update` as `asof`.

---

### D. Target Override Pass-Through Audit (`main.py:962-963`)
- **Wiring Verification**:
  In `main.py:962-963`:
  ```python
  target_1_override=signal.take_profit_1,
  target_2_override=signal.take_profit_2,
  ```
  This cleanly passes strategy-derived take-profit targets for ALL strategies, eliminating the previous defect where only `mean_reversion` targets were respected while `orb` and `news_momentum` targets were discarded.
- **Execution Slippage Vulnerability (`bracket.py:207-214`)**:
  When `target_1_override` is passed, `DynamicBracketManager.activate_bracket_on_fill()` uses the literal override price without verifying its geometric relation to the actual `fill_price`. If positive slippage occurs on entry (e.g., BUY order filled at $101.50 while `target_1_override` was set to $101.20), the resulting take-profit limit order will be placed below entry price, triggering immediate execution at an unintended price.

---

## 3. Findings Breakdown

### [CRITICAL] Finding 1: Inverted Admission Logic in `MarketTrendFilter` for `mean_reversion`
- **Location**: `backend/app/core/market_filter.py:295-301`
- **Code**:
  ```python
  # 4. Statistical Mean Reversion (Exhaustion fades)
  elif strat == "mean_reversion":
      if trend == MarketTrend.BULLISH and is_buy:
          return False, f"INDEX_FILTER_DENIED: Cannot catch falling knife LONG during strong BULLISH trend"
      if trend == MarketTrend.BEARISH and not is_buy:
          return False, f"INDEX_FILTER_DENIED: Cannot fade overbought SHORT during strong BEARISH trend"

  return True, f"APPROVED: Signal {side} on {symbol} aligned with MarketTrend.{trend.value}"
  ```
- **Analysis & Impact**:
  1. If `trend == MarketTrend.BULLISH` and `side == OrderSide.SELL`:
     - Line 296 (`is_buy`) evaluates to `False`.
     - Line 298 (`trend == BEARISH`) evaluates to `False`.
     - Line 301 executes: `return True, "APPROVED: Signal OrderSide.SELL on NVDA aligned with MarketTrend.BULLISH"`.
     - **Result**: The engine APPROVES shorting stocks during a strong BULLISH market rally, claiming a SELL order is "aligned with MarketTrend.BULLISH". This completely defeats the primary objective of eliminating counter-trend shorting into market rallies (which caused the AAPL and TSLA losses on 2026-09-22).
  2. If `trend == MarketTrend.BULLISH` and `side == OrderSide.BUY`:
     - Line 296 triggers: `return False, "Cannot catch falling knife LONG during strong BULLISH trend"`.
     - **Result**: Dip-buying an oversold pullback during a strong bull trend is rejected.
  3. If `trend == MarketTrend.BEARISH` and `side == OrderSide.BUY`:
     - Evaluates to line 301: `APPROVED: Signal OrderSide.BUY on NVDA aligned with MarketTrend.BEARISH`.
     - **Result**: Catching a falling knife in a crashing bear market is APPROVED!
  4. If `trend == MarketTrend.BEARISH` and `side == OrderSide.SELL`:
     - Line 298 triggers: `INDEX_FILTER_DENIED: Cannot fade overbought SHORT during strong BEARISH trend`.
     - **Result**: Shorting an overbought relief rally in a bear market is REJECTED.
- **Required Remediation**:
  Invert the checks or define the correct policy for mean reversion:
  - In a `BULLISH` trend: permit BUY (buying oversold dips aligned with macro bull trend); reject SELL (shorting overbought bars into a runaway bull rally) unless extreme statistical exhaustion (e.g. $Z \ge 3.5$) is present.
  - In a `BEARISH` trend: permit SELL (shorting overbought bounces aligned with macro bear trend); reject BUY (catching falling knives into a runaway bear decline) unless extreme statistical exhaustion (e.g. $Z \le -3.5$) is present.

---

### [MAJOR] Finding 2: Forward Data Leakage via `abs()` in Staleness Check
- **Location**: `backend/app/core/market_filter.py:185, 191`
- **Code**:
  ```python
  spy_ts = _to_utc(self.spy_state.last_timestamp)
  dt_spy = abs((now - spy_ts).total_seconds())
  if dt_spy > self.stale_threshold_sec:
      return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: SPY data age ({dt_spy:.1f}s) > {self.stale_threshold_sec}s"
  ```
- **Analysis & Impact**:
  If a trading signal from a symbol bar at `now = 09:35:00` is evaluated when `self.spy_state.last_timestamp = 09:36:00` (e.g. due to feed latency differences, bar arrival ordering, or historical replay), `now - spy_ts` is `-60.0`. Taking `abs()` makes it `+60.0`, passing the staleness threshold. Consequently, the trend decision uses future market index information to validate a past trade signal.
- **Required Remediation**:
  Enforce strict non-negative chronological causality:
  ```python
  elapsed_spy = (now - spy_ts).total_seconds()
  if elapsed_spy < 0:
      return MarketTrend.UNKNOWN, f"FUTURE_INDEX_DATA: SPY timestamp ({spy_ts.isoformat()}) is in the future of asof ({now.isoformat()})"
  if elapsed_spy > self.stale_threshold_sec:
      return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: SPY data age ({elapsed_spy:.1f}s) > {self.stale_threshold_sec}s"
  ```

---

### [MAJOR] Finding 3: E2E Test Suite Regression (7 Test Failures)
- **Location**: `tests/e2e/test_tier5_adversarial.py`, `tests/e2e/test_challenger_bracket_2.py`
- **Failures**:
  1. `test_adv_bracket_volatility_flash_double_fill_race` (`test_tier5_adversarial.py:326`):
     Fails because default Target 1 was changed from 1.5R to 0.8R (`assert 101.6 == 103.0`).
  2. `TestNewsMomentumStopDistanceClamping` (3 parametrizations: $5, $150, $1000):
     Fails because `bar.close <= bar.open` in `news_momentum.py:244` rejects doji bars (`open == close`), returning 0 signals instead of 1.
  3. `TestOrbStopDistanceClamping` (2 parametrizations) and `TestExtremePricesClamping` (1 parametrization):
     Fails because `min_clv >= 0.65` in `orb.py` rejects synthetic test candles with 50% midpoint closes (`clv = 0.50`), returning 0 signals instead of 1.
- **Analysis & Impact**:
  Worker 1 only tested `pytest backend/tests` and failed to run `python3 tests/e2e/runner.py`. Breaking 7 tests in the pre-existing test suite violates Acceptance Criteria R3 and R4 ("100% pass rate with zero regression").
- **Required Remediation**:
  Update and align the test fixtures / assertions in `tests/e2e/` so the full E2E suite passes 100% (320/320).

---

### [MAJOR] Finding 4: Bracket Target Invariant Hazard on Entry Slippage
- **Location**: `backend/app/core/bracket.py:206-215`
- **Code**:
  ```python
  bracket.target_1_price = (
      round(bracket.target_1_override, 2)
      if bracket.target_1_override is not None
      else round(bracket.entry_price + direction * self.default_target_1_r * bracket.r_distance, 2)
  )
  ```
- **Analysis & Impact**:
  If a strategy calculates `take_profit_1 = round(bar.close + 0.8 * risk, 2)` and the market entry order experiences positive slippage such that `fill_price >= take_profit_1`, the bracket sets `target_1_price` at or below the entry fill price. For a LONG position, this results in submitting a limit sell order below the current market price, resulting in an immediate fill below entry.
- **Required Remediation**:
  In `activate_bracket_on_fill()`, sanitize `target_1_override` against `fill_price`:
  ```python
  if bracket.target_1_override is not None:
      # Ensure target is strictly in the direction of profit by at least 0.5R
      min_target_dist = 0.50 * bracket.r_distance
      if direction * (bracket.target_1_override - fill_price) >= min_target_dist:
          bracket.target_1_price = round(bracket.target_1_override, 2)
      else:
          bracket.target_1_price = round(fill_price + direction * self.default_target_1_r * bracket.r_distance, 2)
  ```

---

### [MINOR] Finding 5: Replay Fixture Index Starvation
- **Location**: `tests/e2e/fixtures/monday_open_session.json`
- **Analysis & Impact**:
  The fixture used in `scripts/run_integrated_monday_dry_run.py` does not contain SPY or QQQ bars. As a consequence, `MarketTrendFilter` remains in `MarketTrend.UNKNOWN` for the entire replay. All ORB and Mean Reversion trades are silently starved (0 trades executed), and only TSLA News Momentum trades due to its extreme catalyst bypass. While this proves the fail-closed property, it means the integrated dry run cannot verify ORB or Mean Reversion in execution.
- **Required Remediation**:
  Include SPY and QQQ 1-minute bars in `monday_open_session.json`.

---

## 4. Verification Results

| Suite / Command | Total Tests | Passed | Failed | Status | Notes |
|---|---|---|---|---|---|
| `pytest backend/tests -v` | 223 | 223 | 0 | **PASS** | Completed in 0.94s |
| `python3 tests/e2e/runner.py` | 320 | 313 | 7 | **FAIL** | 7 failures in adversarial & bracket tests |
| `python3 scripts/run_integrated_monday_dry_run.py` | 62 events | 62 | 0 | **PASS** | Exit code 0, but ORB & Mean Reversion starved |
| Port hygiene check (`lsof -i :8000,8005,8080,3005`) | 4 ports | 4 free | 0 | **PASS** | Zero dangling listeners |

---

## 5. Summary Recommendation

Issue verdict: **REQUEST_CHANGES**.  
Worker 1 must:
1. Fix the inverted `mean_reversion` logic in `market_filter.py:295-301`.
2. Remove `abs()` from the staleness calculation in `market_filter.py:185, 191` to enforce strict chronological causality.
3. Fix the 7 failing tests in `tests/e2e/` to restore 100% test pass rate across the full suite.
4. Add slippage protection to `activate_bracket_on_fill()` in `bracket.py`.
5. Add SPY and QQQ bars to `monday_open_session.json`.
