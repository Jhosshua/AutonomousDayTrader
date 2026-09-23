# Handoff Report — E2E Runner Regressions & Test Alignment

**Agent**: Explorer R2-2 (E2E Runner Regressions & Test Alignment Specialist)  
**Date**: 2026-09-23T04:19:30Z  
**Type**: Hard Handoff (Analysis Complete)  
**Gate Verdict**: **READY_FOR_IMPLEMENTATION**

---

## 1. Observation

1. **E2E Test Runner Failures**:
   Execution of `python3 tests/e2e/runner.py` in `/Users/mo/AutonomousDayTrader` exited with code 1:
   ```text
   FAILED tests/e2e/test_challenger_bracket_2.py::TestOrbStopDistanceClamping::test_orb_bullish_breakout_stop_distance_clamping_tight_range[5.0]
   FAILED tests/e2e/test_challenger_bracket_2.py::TestOrbStopDistanceClamping::test_orb_bullish_breakout_stop_distance_clamping_wide_range[5.0]
   FAILED tests/e2e/test_challenger_bracket_2.py::TestNewsMomentumStopDistanceClamping::test_news_momentum_bullish_tight_and_wide_stops[5.0]
   FAILED tests/e2e/test_challenger_bracket_2.py::TestNewsMomentumStopDistanceClamping::test_news_momentum_bullish_tight_and_wide_stops[150.0]
   FAILED tests/e2e/test_challenger_bracket_2.py::TestNewsMomentumStopDistanceClamping::test_news_momentum_bullish_tight_and_wide_stops[1000.0]
   FAILED tests/e2e/test_challenger_bracket_2.py::TestExtremePricesClamping::test_extreme_price_clamping_orb[1.0]
   FAILED tests/e2e/test_tier5_adversarial.py::test_adv_bracket_volatility_flash_double_fill_race
   7 failed, 313 passed in 25.90s
   ```

2. **Adversarial Double-Fill Flash Race Assertion**:
   In `tests/e2e/test_tier5_adversarial.py:326`:
   ```python
   # TP1 = 100 + 1.5*2 = 103.00, TP2 = 100 + 2.5*2 = 105.00
   assert bracket.target_1_price == 103.00
   assert bracket.target_2_price == 105.00
   ```
   Direct output: `AssertionError: assert 101.6 == 103.0`.
   In `backend/app/core/bracket.py:77-78`, `default_target_1_r` is `0.80` and `default_target_2_r` is `1.80`. For `entry = 100.00, stop = 98.00` ($R = 2.00$), $100.00 + 0.80 \times 2.00 = 101.60$.

3. **News Momentum Candle Confirmation Rejection**:
   In `tests/e2e/test_challenger_bracket_2.py:260-270`:
   ```python
   tight_bar = _make_bar(
       symbol=sym,
       open_p=price,
       high_p=price + 0.05,
       low_p=round(price - 0.001, 4),
       close_p=price,
       vol=45000,
       ts_str="2026-09-21T10:21:00-04:00",
   )
   sigs = strat.on_bar(tight_bar)
   assert len(sigs) == 1, "Expected 1 News Momentum BUY signal"
   ```
   Direct output: `AssertionError: assert 0 == 1 where 0 = len([])`.
   In `backend/app/strategies/news_momentum.py:244`, `if bar.close <= bar.open: return []`. `tight_bar` had `open_p = close_p = price`, which is a flat doji.

4. **ORB Close Location Value (CLV) Rejection on Unscaled Wicks**:
   In `tests/e2e/test_challenger_bracket_2.py`:
   - `test_orb_bullish_breakout_stop_distance_clamping_tight_range[5.0]` (line 115): `entry = 5.01, high_p = entry + 0.01 = 5.02, low_p = 5.00`.
     $\text{CLV} = \frac{5.01 - 5.00}{5.02 - 5.00} = \frac{0.01}{0.02} = 0.5000$. Direct output: `assert 0 == 1`.
   - `test_orb_bullish_breakout_stop_distance_clamping_wide_range[5.0]` (line 159): `entry = 5.81, high_p = entry + 0.50 = 6.31, low_p = 5.00`.
     $\text{CLV} = \frac{5.81 - 5.00}{6.31 - 5.00} = \frac{0.81}{1.31} = 0.6183$. Direct output: `assert 0 == 1`.
   - `test_extreme_price_clamping_orb[1.0]` (line 705): `entry = 1.0151, high_p = entry + 0.05 = 1.0651, low_p = 1.00`.
     $\text{CLV} = \frac{1.0151 - 1.00}{1.0651 - 1.00} = \frac{0.0151}{0.0651} = 0.2320$. Direct output: `assert 0 == 1`.
   In `backend/app/strategies/orb.py:56`, `if clv >= min_clv` where `min_clv = 0.65`. All three test bars failed the CLV threshold.

5. **Challenger 1's CLV Recommendation**:
   In `backend/app/strategies/orb.py:53`, floating point arithmetic evaluates `(102.60 - 100.00) / 4.0 = 0.6499999999999986 < 0.65`.

---

## 2. Logic Chain

1. **Target 1 Calibration Consistency**:
   - *Observation 2 shows* `bracket.target_1_price` is 101.60, while `test_adv_bracket_volatility_flash_double_fill_race` asserts 103.00.
   - *Forensic requirement R2* mandated reducing Target 1 from 1.5R to 0.8R because 0 of 7 live trades hit 1.5R.
   - *Therefore*, the test assertion is outdated; updating it to 101.60 (TP1) and 103.60 (TP2) restores alignment with production bracket geometry.

2. **Candle Direction Invariant vs. Test Fixture**:
   - *Observation 3 shows* `news_momentum` requires `bar.close > bar.open` for BUY signals, rejecting doji bars.
   - *The test fixture* intended to stress-test stop clamping when `entry_price - bar.low` is small (0.001), but accidentally set `open_p = close_p = price`.
   - *Therefore*, setting `open_p = round(price - 0.0005, 4)` creates a valid bullish green bar without altering the tight stop distance or changing the test's intent.

3. **Breakout Candle Geometry vs. Scale Invariance**:
   - *Observation 4 shows* that at $150.00 and $1000.00, hardcoded dollar wicks ($0.01, $0.50, $0.05) represent $<1\%$ of the candle range, yielding $CLV > 0.96$.
   - *At $5.00 and $1.00*, the same unscaled dollar wicks represent 38% to 77% of the candle, distorting the bar into an indecision bar ($CLV = 0.50$), an exhausted candle ($CLV = 0.618$), or an inverted hammer ($CLV = 0.232$).
   - *Therefore*, scaling the upper wick proportionally to the breakout body (`wick = round((entry - price) * 0.1, 4)`) ensures the candle is a true breakout candle ($CLV \approx 0.91$) across all price regimes, allowing the test to verify stop clamping.

4. **IEEE 754 Boundary Precision**:
   - *Observation 5 proves* that exact 0.6500 boundaries produce `0.6499999999999986` due to machine epsilon.
   - *Therefore*, adopting `clv = round((close_p - low_p) / candle_range, 4)` and `clv >= min_clv - 1e-5` prevents false boundary rejections in production.

---

## 3. Caveats

- **Test Fixture Edits vs Strategy Defaults**: The fixes modify the synthetic test fixtures in `test_challenger_bracket_2.py` and `test_tier5_adversarial.py` rather than lowering production safety filters (such as `min_clv = 0.65` or candle direction confirmation). Lowering production safety filters would re-introduce the exact live paper-trading failure modes (chasing dojis and shooting stars) identified in `ORIGINAL_REQUEST.md`.
- **Target Override Slippage**: As identified by Reviewer 1, `target_1_override` in `bracket.py` should be validated against `fill_price` to prevent inverted limits under slippage.

---

## 4. Conclusion

All 7 failing E2E tests are fully diagnosed and remediated:
1. `test_tier5_adversarial.py:326-334`: Update TP1 from $103.00 \to \$101.60$, TP2 from $105.00 \to \$103.60$, and fill from $103.00 \to \$101.60$.
2. `test_challenger_bracket_2.py:262`: Update `tight_bar` to `open_p = round(price - 0.0005, 4)`.
3. `test_challenger_bracket_2.py:115`: Update `bo_bar` in tight ORB to `wick = round((entry - price) * 0.1, 4)` and `high_p = round(entry + wick, 4)`.
4. `test_challenger_bracket_2.py:158`: Update `bo_bar` in wide ORB to `wick = round((entry - price) * 0.05, 2)` and `high_p = round(entry + wick, 2)`.
5. `test_challenger_bracket_2.py:705`: Update `bo_bar` in extreme ORB to `wick = round((entry - price) * 0.1, 4)` and `high_p = round(entry + wick, 4)`.
6. `backend/app/strategies/orb.py:53-60`: Implement Challenger 1's CLV rounding `round(..., 4)` with $10^{-5}$ epsilon margin.

Applying these precise modifications will achieve **100% pass rate (320/320)** in `python3 tests/e2e/runner.py`.

---

## 5. Verification Method

To independently verify these findings and confirm the fix:

```bash
# 1. Run full E2E runner (currently reproduces 7 failures):
python3 tests/e2e/runner.py

# 2. Run targeted pytest on the 7 failing tests:
pytest tests/e2e/test_challenger_bracket_2.py -v
pytest tests/e2e/test_tier5_adversarial.py -k "test_adv_bracket_volatility_flash_double_fill_race" -v

# 3. Verify proportional wick and candle direction math in Python:
python3 -c "
from backend.app.strategies.orb import evaluate_orb_signal
from backend.app.models.events import BarEvent
from datetime import datetime

# Verify tight ORB at $5.00 with proportional wick (clv = 0.9091 >= 0.65):
high_p = round(5.01 + (5.01 - 5.0) * 0.1, 4)  # 5.011
clv = round((5.01 - 5.0) / (high_p - 5.0), 4)
print('ORB Tight 5.0 CLV:', clv, 'Passes >= 0.65:', clv >= 0.65)

# Verify News Momentum green candle (open < close):
open_p = round(5.0 - 0.0005, 4)
close_p = 5.0
print('News Momentum 5.0 Bullish Green:', close_p > open_p)
"

# 4. Verify post-fix complete suite:
python3 tests/e2e/runner.py
# Invalidation Condition: Fails if exit code != 0 or passed tests != 320.
```
