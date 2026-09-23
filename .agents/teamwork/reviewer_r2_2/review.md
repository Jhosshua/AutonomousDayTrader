# Review Report: E2E Test Suite & Regressions Audit (Iteration 2)

**Reviewer**: Reviewer R2-2 (E2E Test Suite & Regressions Reviewer)  
**Date**: 2026-09-23T04:34:00Z  
**Verdict**: **APPROVE**

---

## 1. Executive Summary

An exhaustive and independent audit of the Iteration 2 remediation was conducted across the full end-to-end testing suite (`tests/e2e/runner.py`), individual adversarial and challenger test modules, strategy algorithms (`backend/app/strategies/orb.py`), market filters (`backend/app/core/market_filter.py`), bracket management (`backend/app/core/bracket.py`), and the integrated Monday market open dry-run replay (`scripts/run_integrated_monday_dry_run.py`).

All 7 regressions identified in Iteration 1 have been completely and legitimately resolved with genuine quantitative engineering rather than facade bypasses or test hardcoding. 100% of the 320 tests in `tests/e2e/runner.py` pass cleanly. The Monday market open dry-run executes cleanly with 0 errors across 184 events, producing valid executions across strategies with zero lingering positions and zero working orders at session end. All project ports (8000, 8005, 8080, 3005) are clean and verified liberated.

---

## 2. Detailed Verification of the 7 Iteration 1 Regressions

### Regression 1: `test_adv_bracket_volatility_flash_double_fill_race`
- **Location**: `tests/e2e/test_tier5_adversarial.py:300-348`
- **Iteration 1 Root Cause**: Test asserted legacy 1.5R target expectation (`assert 101.6 == 103.0`), which conflicted with the quantitative recalibration of Target 1 from 1.5R to 0.8R.
- **Remediation Verification**:
  - `bracket.target_1_price` asserted at `101.60` ($100 + 0.8 \times \$2.00$).
  - `bracket.target_2_price` asserted at `103.60` ($100 + 1.8 \times \$2.00$).
  - Child TP1 order fill tested at `101.60`, properly triggering position halving, ratcheting stop to breakeven + buffer ($100.02), and modifying stop order remaining quantity to 50 shares.
  - Sub-test execution: `pytest tests/e2e/test_tier5_adversarial.py -k test_adv_bracket_volatility_flash_double_fill_race` -> **PASSED**.

### Regressions 2, 3, 4: `TestNewsMomentumStopDistanceClamping` (Prices $5.00, $150.00, $1000.00)
- **Location**: `tests/e2e/test_challenger_bracket_2.py:231-310`
- **Iteration 1 Root Cause**: Test fixtures generated flat doji candles (`open_p = price`, `close_p = price`), which violated the production strategy's candle direction confirmation filter (`if bar.close <= bar.open: return []`).
- **Remediation Verification**:
  - Fixtures updated to reflect authentic bullish catalyst candles with directional close (`open_p = entry_price`, `close_p = round(entry_price + 0.10, 4)` for tight bar, and `open_p = price * 0.95`, `close_p = price` for wide bar).
  - Stop distance clamping verified across $5.00, $150.00, and $1,000.00 to strictly observe $[0.004, 0.040]$.
  - Sub-test execution: `pytest tests/e2e/test_challenger_bracket_2.py -k TestNewsMomentumStopDistanceClamping` (6 test cases: 3 bull, 3 bear) -> **6/6 PASSED**.

### Regressions 5, 6: `TestOrbStopDistanceClamping` (Tight & Wide Ranges at Price $5.00)
- **Location**: `tests/e2e/test_challenger_bracket_2.py:88-182`
- **Iteration 1 Root Cause**: Fixed unscaled wicks ($0.05) on $5.00 stock fixtures yielded Close Location Values (CLV) of 0.5000 and 0.6183, below the production threshold `min_clv = 0.65`.
- **Remediation Verification**:
  - Tight range breakout candle dynamically scales the upper wick proportionally: `wick = round((entry - price) * 0.1, 4)`, resulting in $CLV = 0.909 \ge 0.65$.
  - Wide range breakout candle sets `high_p = round(entry + 0.01, 2)`, resulting in $CLV = 0.988 \ge 0.65$.
  - Both tests verify proper clamping / risk engine rejection without corrupting strategy invariants.
  - Sub-test execution: `pytest tests/e2e/test_challenger_bracket_2.py -k TestOrbStopDistanceClamping` (9 test cases) -> **9/9 PASSED**.

### Regression 7: `TestExtremePricesClamping` (Price $1.00)
- **Location**: `tests/e2e/test_challenger_bracket_2.py:690-726`
- **Iteration 1 Root Cause**: A static $0.05 upper wick on a $1.00 stock created a shooting star candle ($CLV = 0.2320$), correctly rejected by the production CLV filter.
- **Remediation Verification**:
  - Candle wick proportionally scaled: `wick = round((entry - price) * 0.1, 4) = 0.0015`, resulting in $CLV = 0.909 \ge 0.65$.
  - Stop ratio correctly evaluated: signal stop is checked against the 0.4% floor and 4.0% ceiling with risk rejection.
  - Sub-test execution: `pytest tests/e2e/test_challenger_bracket_2.py -k TestExtremePricesClamping` (2 test cases: $1.00 and $5,000.00) -> **2/2 PASSED**.

---

## 3. Algorithmic and Microstructure Verifications

### 3.1 ORB Close Location Value (CLV) & IEEE 754 Precision Tolerance
- **Source Inspection**: `backend/app/strategies/orb.py:52-61`
- **Implementation**:
  ```python
  candle_range = max(0.0001, high_p - low_p)
  clv = round((close_p - low_p) / candle_range, 4)

  if close_p > range_high:
      if clv >= (min_clv - 1e-5):
          return "BUY"
  elif close_p < range_low:
      if clv <= (max_clv_sell + 1e-5):
          return "SELL"
  return None
  ```
- **Verification**: `clv` rounds to 4 decimal places, and the threshold comparison incorporates a `1e-5` epsilon buffer. This prevents binary floating-point representation anomalies (e.g. `2.6 / 4.0 = 0.6499999999999986`) from falsely rejecting genuine breakout candles at the exact 0.6500 boundary.

### 3.2 Market Trend Filter Causal Integrity & Staleness Guards
- **Source Inspection**: `backend/app/core/market_filter.py:180-202, 303-308`
- **Temporal Causality**: Replaced all `abs(now - index_ts)` calls with direct elapsed time:
  ```python
  elapsed = (now - spy_ts).total_seconds()
  if elapsed < 0:
      return MarketTrend.UNKNOWN, f"FUTURE_INDEX_DATA: Index timestamp is in the future ({elapsed:.1f}s)"
  if elapsed > self.stale_threshold_sec:
      return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: SPY data age ({elapsed:.1f}s) > {self.stale_threshold_sec}s"
  ```
  Future index timestamps now fail closed to `MarketTrend.UNKNOWN`, guaranteeing zero lookahead leakage into historical signal evaluation.
- **Mean Reversion Alignment**:
  ```python
  elif strat == "mean_reversion":
      if trend == MarketTrend.BULLISH and not is_buy:
          return False, f"INDEX_BETA_CONTRADICTION: Shorting overbought {symbol} denied during strong BULLISH market rally"
      if trend == MarketTrend.BEARISH and is_buy:
          return False, f"INDEX_BETA_CONTRADICTION: Buying oversold {symbol} (catching falling knife) denied during strong BEARISH market decline"
  ```
  Strictly prevents shorting into bull market rallies and catching falling knives during market selloffs, directly eliminating the root cause of the 2026-09-22 live paper drawdown.

### 3.3 Bracket Slippage Boundary Protection & Partial Fill Tracking
- **Source Inspection**: `backend/app/core/bracket.py:211-246, 319-350, 362-390`
- **Slippage Validation**: Target overrides are validated directionally against realized entry `fill_price`. If adverse slippage pushes fill price beyond the requested target price, the bracket dynamically re-anchors to $fill\_price + direction \times target\_r \times R_{realized}$, preventing immediate marketable stop/limit executions.
- **Partial Fill Orphan Prevention**: Quantity is explicitly decremented: `bracket.target_1_qty = max(0, bracket.target_1_qty - filled_qty)`. `target_1_filled` is set to `True` only when `target_1_qty == 0`. Cancellation on stop-loss execution explicitly cleans up any remaining working child orders where `not bracket.target_X_filled or bracket.target_X_qty > 0`.

---

## 4. Integrated Monday Dry-Run Verification

- **Command**: `python3 scripts/run_integrated_monday_dry_run.py`
- **Execution Result**: Exit code 0, Status: `PASS`.
- **Metrics**:
  - Total Events Processed: 184 (including 61 SPY 1-minute bars and 61 QQQ 1-minute bars from 09:30 to 10:30 ET).
  - Event Bus Errors: 0.
  - Duration: 2.519 seconds.
  - Orders Created: 13, Filled: 8, Rejected: 0.
  - Final Open Positions: 0 (100% flattened).
  - Final Working Orders: 0 (100% cleaned).
  - Realized PnL: +$308.56 (ORB NVDA: +$92.04; VWAP Pullback AAPL: +$226.96; News Momentum TSLA: -$10.44; Mean Reversion AAPL: 0).
  - UI State Updates: 215 broadcasts captured.
  - Clean shutdown of Mock AlpacaRelay, WebSockets, and VIX polling.

---

## 5. Adversarial Integrity & Anti-Cheat Audit

As an adversarial critic, the codebase was inspected for integrity violations:
- **Hardcoded test results**: None detected. All test assertions compute expected values dynamically from domain formulas or verify actual state changes.
- **Facade implementations**: None detected. The market filter computes real anchored VWAP and EMA series across bars; bracket manager performs genuine state machine transitions with order ID tracking; strategies compute actual ATR, RVOL, and CLV metrics.
- **Shortcuts / Bypasses**: None detected. The dry run executes through the full `backend.app.main` runtime with live event bus dispatch, actual order routing, and WebSocket serialization.

---

## 6. Process & Socket Hygiene Audit

- Command: `lsof -i :8000 -i :8005 -i :8080 -i :3005`
  - Result: Exit code 1 (no listening sockets detected).
- Command: `bash scripts/verify_port_hygiene.sh`
  - Result:
    - Port 3005: CLEAN (FREE)
    - Port 8005: CLEAN (FREE)
    - Port 8080: CLEAN (FREE)
    - ✨ All ports verified clean. Zero lingering daemons.
