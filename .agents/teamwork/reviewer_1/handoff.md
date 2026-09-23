# Handoff Report — Architecture & Lookahead Bias Audit

**Agent**: Reviewer 1 (Architecture & Lookahead Bias Auditor)  
**Date**: 2026-09-23T04:14:00Z  
**Type**: Hard Handoff (Review Complete)  
**Gate Verdict**: **REQUEST_CHANGES**

---

## 1. Observation

1. **Inverted Admission Logic for Mean Reversion**:
   In `backend/app/core/market_filter.py:295-301`:
   ```python
   # 4. Statistical Mean Reversion (Exhaustion fades)
   elif strat == "mean_reversion":
       if trend == MarketTrend.BULLISH and is_buy:
           return False, f"INDEX_FILTER_DENIED: Cannot catch falling knife LONG during strong BULLISH trend"
       if trend == MarketTrend.BEARISH and not is_buy:
           return False, f"INDEX_FILTER_DENIED: Cannot fade overbought SHORT during strong BEARISH trend"

   return True, f"APPROVED: Signal {side} on {symbol} aligned with MarketTrend.{trend.value}"
   ```
   When `trend == MarketTrend.BULLISH` and `side == OrderSide.SELL`, line 296 (`is_buy`) is False, line 298 is False, and line 301 executes:
   `return True, f"APPROVED: Signal {side} on {symbol} aligned with MarketTrend.{trend.value}"`.
   When `trend == MarketTrend.BEARISH` and `side == OrderSide.BUY`, lines 296 and 298 are False, and line 301 approves the BUY.

2. **Forward Lookahead Vulnerability via `abs()`**:
   In `backend/app/core/market_filter.py:185, 191`:
   ```python
   spy_ts = _to_utc(self.spy_state.last_timestamp)
   dt_spy = abs((now - spy_ts).total_seconds())
   if dt_spy > self.stale_threshold_sec:
       return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: SPY data age ({dt_spy:.1f}s) > {self.stale_threshold_sec}s"
   ```
   If `now` (signal timestamp) is earlier than `spy_ts` (e.g. `now = 09:35:00`, `spy_ts = 09:36:00`), `now - spy_ts` is `-60.0s`. `abs(-60.0) = 60.0`, which passes the staleness threshold, allowing future index bars to validate past signals.

3. **E2E Test Runner Regressions**:
   Command `python3 tests/e2e/runner.py` exited with code 1:
   ```text
   FAILED tests/e2e/test_challenger_bracket_2.py::TestOrbStopDistanceClamping::test_orb_bullish_breakout_stop_distance_clamping_tight_range[5.0]
   FAILED tests/e2e/test_challenger_bracket_2.py::TestOrbStopDistanceClamping::test_orb_bullish_breakout_stop_distance_clamping_wide_range[5.0]
   FAILED tests/e2e/test_challenger_bracket_2.py::TestNewsMomentumStopDistanceClamping::test_news_momentum_bullish_tight_and_wide_stops[5.0]
   FAILED tests/e2e/test_challenger_bracket_2.py::TestNewsMomentumStopDistanceClamping::test_news_momentum_bullish_tight_and_wide_stops[150.0]
   FAILED tests/e2e/test_challenger_bracket_2.py::TestNewsMomentumStopDistanceClamping::test_news_momentum_bullish_tight_and_wide_stops[1000.0]
   FAILED tests/e2e/test_challenger_bracket_2.py::TestExtremePricesClamping::test_extreme_price_clamping_orb[1.0]
   FAILED tests/e2e/test_tier5_adversarial.py::test_adv_bracket_volatility_flash_double_fill_race
   7 failed, 313 passed in 27.12s
   ```
   - `test_adv_bracket_volatility_flash_double_fill_race`: `assert 101.6 == 103.0` (failed due to Target 1 default change from 1.5R to 0.8R).
   - `TestNewsMomentumStopDistanceClamping`: `assert 0 == 1` (failed due to `bar.close <= bar.open` rejection on doji candles).
   - `TestOrbStopDistanceClamping`: `assert 0 == 1` (failed due to `min_clv >= 0.65` rejecting 50% midpoint closes).

4. **Unit Test Pass**:
   Command `pytest backend/tests -v` completed with code 0:
   ```text
   ============================= 223 passed in 0.94s ==============================
   ```

5. **Dry Run Starvation**:
   In `tests/e2e/fixtures/monday_open_session.json`:
   Grep `\"S\": \"SPY\"` returned zero matches.
   Running `python3 scripts/run_integrated_monday_dry_run.py` completed with exit code 0, but:
   ```json
   "strategies": [
     {"id": "orb", "trades_count": 0},
     {"id": "vwap_pullback", "trades_count": 0},
     {"id": "news_momentum", "trades_count": 1},
     {"id": "mean_reversion", "trades_count": 0}
   ]
   ```
   ORB and Mean Reversion were completely starved because `MarketTrendFilter` reported `MarketTrend.UNKNOWN (MISSING_INDEX_BARS)`.

6. **Target Override Slippage Hazard**:
   In `backend/app/core/bracket.py:206-215`:
   `bracket.target_1_price = round(bracket.target_1_override, 2) if bracket.target_1_override is not None else ...`
   In `backend/app/main.py:962-963`:
   `target_1_override=signal.take_profit_1` passed for all strategies. If a market order fills above `take_profit_1` due to slippage on a LONG, `target_1_price` remains below `fill_price`.

---

## 2. Logic Chain

1. **Inverted Mean Reversion Risk**:
   - *Premise*: The primary root cause of the $201.68 production drawdown was shorting into a market-wide rally without broader index confirmation (e.g. TSLA short @ 09:31 ET, AAPL short @ 10:09 ET).
   - *Deduction*: By approving `SELL` orders during `MarketTrend.BULLISH` and rejecting `BUY` orders (Observation 1), `MarketTrendFilter` explicitly authorizes shorting into market-wide morning rallies and permits catching falling knives in market-wide crashes. The engine therefore still contains the exact failure mode it was intended to eliminate.

2. **Causal Data Integrity**:
   - *Premise*: Quantitative trading systems must prevent forward data leakage where future market index bars inform past order execution decisions.
   - *Deduction*: By taking the absolute value `abs(now - spy_ts)` (Observation 2), future index bars are accepted as fresh observations rather than being rejected as out-of-order forward leakage.

3. **Test Suite Integrity & Regression**:
   - *Premise*: Acceptance Criterion R4 requires a 100% pass rate across the full test suite with zero regression.
   - *Deduction*: While the unit suite passes (Observation 4), the E2E suite fails with 7 regressions (Observation 3). Worker 1 verified only the unit suite and omitted `tests/e2e/runner.py`.

4. **Execution Safety**:
   - *Premise*: Multi-tier brackets must maintain invariant take-profit geometry relative to realized fill price.
   - *Deduction*: Applying raw unverified pre-fill price targets under slippage (Observation 6) creates an execution hazard where limit exit orders can execute immediately below entry.

---

## 3. Caveats

- **Scope of Review**: This audit covered backend architectural integrity, data causality, and mathematical invariant preservation. Frontend UI rendering and visual layouts were not evaluated.
- **Production Index Availability**: In live trading, SPY and QQQ bars are subscribed to in `WATCHLIST_SYMBOLS`. However, the lack of index bars in test fixtures (Observation 5) prevents full integration validation in CI.

---

## 4. Conclusion

**Verdict: REQUEST_CHANGES**

Worker 1's implementation provides strong improvements in ORB bar quality (CLV and ATR range caps), News Momentum substring protection, and bracket price-scaled breakeven buffers. However, the changes CANNOT be approved due to:
1. **Critical Inverted Logic**: `market_filter.py:295-301` approves shorting during bull trends and buying during bear trends for `mean_reversion`.
2. **Forward Lookahead Vulnerability**: `market_filter.py:185, 191` uses `abs()` which permits future index bars to validate past signals.
3. **E2E Regressions**: 7 tests failing in `tests/e2e/runner.py`.
4. **Slippage Boundary Hazard**: In `bracket.py:206-215`, literal target overrides lack sanity checks against `fill_price`.

---

## 5. Verification Method

To independently verify all findings and validate fixes:

```bash
# 1. Run full backend unit tests (all 223 must pass)
pytest backend/tests -v

# 2. Run full E2E test suite (verify 7 failing tests reproduce)
python3 tests/e2e/runner.py

# 3. Specifically verify the 7 failing tests:
pytest "tests/e2e/test_challenger_bracket_2.py::TestOrbStopDistanceClamping::test_orb_bullish_breakout_stop_distance_clamping_tight_range[5.0]" -v
pytest "tests/e2e/test_challenger_bracket_2.py::TestOrbStopDistanceClamping::test_orb_bullish_breakout_stop_distance_clamping_wide_range[5.0]"
pytest "tests/e2e/test_challenger_bracket_2.py::TestNewsMomentumStopDistanceClamping" -v
pytest "tests/e2e/test_challenger_bracket_2.py::TestExtremePricesClamping::test_extreme_price_clamping_orb[1.0]" -v
pytest "tests/e2e/test_tier5_adversarial.py::test_adv_bracket_volatility_flash_double_fill_race" -v

# 4. Verify inverted logic in market_filter.py:295-301:
python3 -c "
from backend.app.core.market_filter import MarketTrendFilter, MarketTrend
from backend.app.models.events import OrderSide, BarEvent
from datetime import datetime, timezone
mf = MarketTrendFilter()
dt = datetime.now(timezone.utc)
# Simulate bull bars
for _ in range(5):
    mf.on_bar(BarEvent('SPY', 500, 505, 499, 504, 10000, dt))
    mf.on_bar(BarEvent('QQQ', 450, 455, 449, 454, 10000, dt))
# Check mean reversion SELL in BULL trend:
ok, reason = mf.is_signal_permitted('mean_reversion', OrderSide.SELL, 'NVDA', asof=dt)
print('SELL in BULLISH allowed:', ok, reason)
# Check mean reversion BUY in BULL trend:
ok, reason = mf.is_signal_permitted('mean_reversion', OrderSide.BUY, 'NVDA', asof=dt)
print('BUY in BULLISH allowed:', ok, reason)
"

# 5. Run integrated dry run:
python3 scripts/run_integrated_monday_dry_run.py

# 6. Verify port hygiene:
lsof -i :8000 -i :8005 -i :8080 -i :3005
```

### Invalidation Conditions:
- The inverted logic finding is invalidated if quantitative specification demonstrates that mean reversion was deliberately intended to short during market-wide bull rallies and buy during market-wide crashes.
- The forward data leakage finding is invalidated if `spy_ts` can mathematically never exceed `now` under any network or replay conditions.
- The E2E regression finding is invalidated only when `python3 tests/e2e/runner.py` achieves 320/320 passed (100%).
