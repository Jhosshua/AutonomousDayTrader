# Quantitative & E2E Regression Analysis: Test Alignment & Production Filter Integrity

**Agent**: Explorer R2-2 (E2E Runner Regressions & Test Alignment Specialist)  
**Date**: 2026-09-23T04:18:00Z  
**Scope**: Full investigation of 7 failing E2E tests in `tests/e2e/runner.py`, CLV IEEE 754 boundary behavior, and exact fix strategy for 100% (320/320) test pass rate.

---

## 1. Executive Summary & Failure Matrix

Execution of `python3 tests/e2e/runner.py` yielded **313 passed, 7 failed** (total 320 tests, 97.8% pass rate).  
All 7 failures stem from a mismatch between recently implemented **production quantitative safety filters** (Target 1 calibrated to 0.8R, Close Location Value filter `CLV >= 0.65`, and Candle Direction Confirmation `close > open`) and **synthetic test fixtures** that either asserted legacy parameters or constructed deformed candles with unscaled fixed-dollar wicks.

| # | Test Identifier | File & Lines | Root Cause | Target Fix |
|---|---|---|---|---|
| 1 | `test_adv_bracket_volatility_flash_double_fill_race` | `tests/e2e/test_tier5_adversarial.py:326` | Test asserted legacy Target 1 of 1.5R ($103.00) and Target 2 of 2.5R ($105.00), whereas production `DynamicBracketManager` calibrated Target 1 to 0.8R ($101.60) and Target 2 to 1.8R ($103.60). | Update test assertions to 0.8R ($101.60) and 1.8R ($103.60), and child fill simulation to $101.60. |
| 2 | `TestNewsMomentumStopDistanceClamping[5.0]` | `tests/e2e/test_challenger_bracket_2.py:270` | Synthetic fixture had `open_p = 5.0, close_p = 5.0` (doji). Production candle direction confirmation (`bar.close <= bar.open`) rejected the bar (`assert 0 == 1`). | Set `open_p = round(price - 0.0005, 4)` so `close > open` confirms a bullish bar while keeping tight stop distance. |
| 3 | `TestNewsMomentumStopDistanceClamping[150.0]` | `tests/e2e/test_challenger_bracket_2.py:270` | Synthetic fixture had `open_p = 150.0, close_p = 150.0` (doji). Rejected by candle direction confirmation (`assert 0 == 1`). | Set `open_p = round(price - 0.0005, 4)` to confirm bullish bar. |
| 4 | `TestNewsMomentumStopDistanceClamping[1000.0]` | `tests/e2e/test_challenger_bracket_2.py:270` | Synthetic fixture had `open_p = 1000.0, close_p = 1000.0` (doji). Rejected by candle direction confirmation (`assert 0 == 1`). | Set `open_p = round(price - 0.0005, 4)` to confirm bullish bar. |
| 5 | `TestOrbStopDistanceClamping::test_orb_bullish_breakout_stop_distance_clamping_tight_range[5.0]` | `tests/e2e/test_challenger_bracket_2.py:122` | Breakout candle had fixed $0.01 wick: `entry = 5.01, high = 5.02, low = 5.00`. $CLV = \frac{5.01-5.00}{5.02-5.00} = 0.5000 < 0.65$. Rejected by production CLV filter (`assert 0 == 1`). | Scale upper wick proportionally: `wick = round((entry - price) * 0.1, 4)` yielding $CLV = 0.9091 \ge 0.65$. |
| 6 | `TestOrbStopDistanceClamping::test_orb_bullish_breakout_stop_distance_clamping_wide_range[5.0]` | `tests/e2e/test_challenger_bracket_2.py:166` | Breakout candle had fixed $0.50 wick: `entry = 5.81, high = 6.31, low = 5.00`. $CLV = \frac{5.81-5.00}{6.31-5.00} = 0.6183 < 0.65$. Rejected by production CLV filter (`assert 0 == 1`). | Scale upper wick proportionally: `wick = round((entry - price) * 0.05, 2)` yielding $CLV = 0.9529 \ge 0.65$. |
| 7 | `TestExtremePricesClamping::test_extreme_price_clamping_orb[1.0]` | `tests/e2e/test_challenger_bracket_2.py:709` | Breakout candle had fixed $0.05 wick: `entry = 1.0151, high = 1.0651, low = 1.00`. $CLV = \frac{1.0151-1.00}{1.0651-1.00} = 0.2320 \ll 0.65$ (extreme shooting star). Rejected by production CLV filter (`assert 0 == 1`). | Scale upper wick proportionally: `wick = round((entry - price) * 0.1, 4)` yielding $CLV = 0.9096 \ge 0.65$. |

---

## 2. Detailed Root Cause Investigations

### 2.1 Failure 1: Target 1 Geometry Alignment (`test_tier5_adversarial.py`)
- **Code Reference**: `tests/e2e/test_tier5_adversarial.py:309-335`
- **Execution Log**:
  ```text
  assert bracket.target_1_price == 103.00
  E AssertionError: assert 101.6 == 103.0
  E  + where 101.6 = BracketOrder(...).target_1_price
  ```
- **Analysis**:
  Milestone 3 Requirement R2 mandates:
  > "Restructure profit target and bracket management in `backend/app/core/bracket.py`: Enable realistic scaling (e.g., Target 1 at 0.8R–1.0R to de-risk trades quickly)."
  
  In `backend/app/core/bracket.py:77-78`:
  ```python
  def __init__(
      self,
      breakeven_buffer: Optional[float] = None,
      default_target_1_r: float = 0.80,
      default_target_2_r: float = 1.80,
  ) -> None:
  ```
  For an entry at $100.00$ with stop at $98.00$, the risk unit is $R = \$2.00$.
  - Target 1: $100.00 + 0.80 \times \$2.00 = \$101.60$.
  - Target 2: $100.00 + 1.80 \times \$2.00 = \$103.60$.
  The test was written prior to this calibration when defaults were $1.5R$ ($103.00$) and $2.5R$ ($105.00$). The bracket manager is operating exactly according to the quantitative specification. The test assertion must be updated to $101.60$ and $103.60$.

---

### 2.2 Failures 2, 3, 4: News Momentum Candle Direction Confirmation
- **Code Reference**: `tests/e2e/test_challenger_bracket_2.py:259-271`
- **Execution Log**:
  ```text
  sigs = strat.on_bar(tight_bar)
  > assert len(sigs) == 1, "Expected 1 News Momentum BUY signal"
  E AssertionError: Expected 1 News Momentum BUY signal
  E assert 0 == 1
  E  + where 0 = len([])
  ```
- **Analysis**:
  In `backend/app/strategies/news_momentum.py:242-245`:
  ```python
  # Enforce candle direction confirmation (close > open for BUY, close < open for SELL)
  if cat.sentiment >= self.sentiment_threshold:
      if bar.close <= bar.open:
          return []
  ```
  The production safety filter requires that when trading a bullish news catalyst, the confirmation bar must have closed green (`close > open`). In the paper-trading forensics, shorting or buying into opposite-colored candles or flat dojis was identified as a primary cause of immediate adverse excursion.
  
  In `test_news_momentum_bullish_tight_and_wide_stops`, the fixture constructed `tight_bar`:
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
  ```
  Because `open_p = price` and `close_p = price`, this candle is a **flat doji** (`close <= open` is `True`). The strategy properly rejected the trade.
  
  The intent of the test is to verify that when `entry_price - bar.low` is very small (0.001), the risk engine / strategy clamps the stop distance to the 0.4% floor ($0.004 \times \text{entry}$). Setting `open_p = round(price - 0.0005, 4)` provides a valid green candle (`close > open`), keeps `low_p` at `price - 0.001`, and exercises the exact stop clamping assertion without bypassing the production direction guardrail.

---

### 2.3 Failures 5, 6, 7: ORB Close Location Value (CLV) Rejection on Unscaled Wicks
- **Code Reference**: `tests/e2e/test_challenger_bracket_2.py:111-122`, `154-167`, `703-710`
- **Execution Log**:
  ```text
  sigs = strat.on_bar(bo_bar)
  > assert len(sigs) == 1
  E AssertionError: Expected 1 ORB breakout BUY signal
  E assert 0 == 1
  ```
- **Analysis**:
  In `backend/app/strategies/orb.py:52-57`:
  ```python
  candle_range = max(0.0001, high_p - low_p)
  clv = (close_p - low_p) / candle_range
  if close_p > range_high:
      if clv >= min_clv:  # default min_clv = 0.65
          return "BUY"
  ```
  The CLV metric measures where the bar closes relative to its entire high-low range:
  $$\text{CLV} = \frac{\text{close} - \text{low}}{\text{high} - \text{low}}$$
  A valid breakout candle must close in the upper 35% of its range ($\text{CLV} \ge 0.65$). A candle closing near its midpoint ($\text{CLV} \approx 0.50$) or near its low ($\text{CLV} < 0.50$) represents intraday exhaustion, an inverted hammer, or rejection at the highs.
  
  In the three failing tests:
  1. **Tight Range ORB at $5.00** (`test_orb_bullish_breakout_stop_distance_clamping_tight_range[5.0]`):
     - Fixture: `entry = 5.01, high_p = entry + 0.01 = 5.02, low_p = 5.00, close_p = 5.01`.
     - $\text{Candle Range} = 5.02 - 5.00 = 0.02$.
     - $\text{CLV} = \frac{5.01 - 5.00}{0.02} = \frac{0.01}{0.02} = 0.5000$.
     - $0.5000 < 0.65 \implies$ **REJECTED**.
     - Note: At $150.00$ (`entry = 150.30, high = 150.31`), the $0.01 wick is negligible, yielding $\text{CLV} = 0.9677$. The test passed at $150 and $1000 only because a hardcoded 1-cent wick is tiny on large numbers.
  2. **Wide Range ORB at $5.00** (`test_orb_bullish_breakout_stop_distance_clamping_wide_range[5.0]`):
     - Fixture: `entry = 5.81, high_p = entry + 0.50 = 6.31, low_p = 5.00, close_p = 5.81`.
     - $\text{Candle Range} = 6.31 - 5.00 = 1.31$.
     - $\text{CLV} = \frac{5.81 - 5.00}{1.31} = \frac{0.81}{1.31} = 0.6183$.
     - $0.6183 < 0.65 \implies$ **REJECTED**.
     - At $150.00$, the $0.50 wick gave $\text{CLV} = 0.9798$.
  3. **Extreme Low Price ORB at $1.00** (`test_extreme_price_clamping_orb[1.0]`):
     - Fixture: `entry = 1.0151, high_p = entry + 0.05 = 1.0651, low_p = 1.00, close_p = 1.0151`.
     - $\text{Candle Range} = 1.0651 - 1.00 = 0.0651$.
     - $\text{CLV} = \frac{1.0151 - 1.00}{0.0651} = \frac{0.0151}{0.0651} = 0.2320$.
     - $0.2320 \ll 0.65 \implies$ **REJECTED**.
     - A $0.05 wick on a $1.00 stock was 3.3x larger than the breakout body, creating a severe shooting star candle.

---

## 3. Evaluation of Challenger 1's CLV Recommendation

Challenger 1 recommended:
```python
clv = round((close_p - low_p) / candle_range, 4)
# with comparison:
if clv >= (min_clv - 1e-5):
```

### 3.1 Mathematical & Binary Representation Proof
Under IEEE 754 double-precision arithmetic:
Consider a bar with `low = 100.00`, `high = 104.00`, `close = 102.60`:
- Exact algebra: $\frac{102.60 - 100.00}{104.00 - 100.00} = \frac{2.60}{4.00} = 0.6500$.
- In binary floating-point:
  `102.60` is represented as `102.5999999999999943156581139...`
  `102.60 - 100.00 = 2.599999999999994315658...`
  `2.5999999999999943 / 4.0 = 0.6499999999999985789...`
  Evaluating `0.6499999999999986 >= 0.65` yields **`False`**.

A valid boundary setup closing at exactly the 65.0% mark is silently discarded due to binary roundoff error.

### 3.2 Symmetric Short Breakdown Protection
For SELL signals (`max_clv_sell = 0.35`):
Consider `low = 100.00`, `high = 104.00`, `close = 101.40`:
- Exact algebra: $\frac{101.40 - 100.00}{4.00} = \frac{1.40}{4.00} = 0.3500$.
- In floating-point:
  `101.40 - 100.00` can evaluate to `1.4000000000000057... / 4.0 = 0.3500000000000014`.
  Evaluating `0.3500000000000014 <= 0.35` yields **`False`**.

### 3.3 Assessment & Recommendation
- **Verdict**: Strongly **ENDORSED**.
- **Implementation**: In `backend/app/strategies/orb.py:53-60`:
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
- **Distinction from Test Failures**: Note that applying Challenger 1's epsilon does NOT fix the 3 failing ORB test fixtures, because those fixtures had CLVs of 0.5000, 0.6183, and 0.2320 (far below $0.65 - 10^{-5}$). The test fixtures must be updated independently to have proportional wicks.

---

## 4. Exact Fix Strategy

### 4.1 Update Test Fixtures in `tests/e2e/test_tier5_adversarial.py`
Replace legacy target assertions and fill simulation in `test_adv_bracket_volatility_flash_double_fill_race`:

```python
<<<<
    # TP1 = 100 + 1.5*2 = 103.00, TP2 = 100 + 2.5*2 = 105.00
    assert bracket.target_1_price == 103.00
    assert bracket.target_2_price == 105.00

    # Entry fills
    bm.activate_bracket_on_fill(bracket.bracket_id, total_qty, entry_price, now_dt)
    assert bracket.status == BracketStatus.ACTIVE

    # Child TP1 fills first
    dir_tp1 = bm.on_child_order_fill(bracket.target_1_order_id, 103.00, 50, now_dt)
====
    # TP1 = 100 + 0.8*2 = 101.60, TP2 = 100 + 1.8*2 = 103.60
    assert bracket.target_1_price == 101.60
    assert bracket.target_2_price == 103.60

    # Entry fills
    bm.activate_bracket_on_fill(bracket.bracket_id, total_qty, entry_price, now_dt)
    assert bracket.status == BracketStatus.ACTIVE

    # Child TP1 fills first
    dir_tp1 = bm.on_child_order_fill(bracket.target_1_order_id, 101.60, 50, now_dt)
>>>>
```

### 4.2 Update Test Fixtures in `tests/e2e/test_challenger_bracket_2.py`

#### A. Tight Range ORB Test (`lines 111-120`)
Scale upper wick proportionally to breakout extension:
```python
<<<<
        # Breakout bar closing slightly above range high with RVOL 3.0x
        entry = round(price * 1.002, 4)
        bo_bar = _make_bar(
            symbol=sym,
            open_p=price + delta,
            high_p=entry + 0.01,
            low_p=price,
            close_p=entry,
            vol=50000,
            ts_str="2026-09-21T09:35:00-04:00",
        )
====
        # Breakout bar closing slightly above range high with RVOL 3.0x (proportional upper wick)
        entry = round(price * 1.002, 4)
        wick = round((entry - price) * 0.1, 4)
        bo_bar = _make_bar(
            symbol=sym,
            open_p=price + delta,
            high_p=round(entry + wick, 4),
            low_p=price,
            close_p=entry,
            vol=50000,
            ts_str="2026-09-21T09:35:00-04:00",
        )
>>>>
```

#### B. Wide Range ORB Test (`lines 154-165`)
Scale upper wick proportionally:
```python
<<<<
        # Breakout bar closing above range high with RVOL 3.0x
        entry = round((price + delta) * 1.01, 2)
        bo_bar = _make_bar(
            symbol=sym,
            open_p=price + delta,
            high_p=entry + 0.50,
            low_p=price,
            close_p=entry,
            vol=50000,
            ts_str="2026-09-21T09:35:00-04:00",
        )
====
        # Breakout bar closing above range high with RVOL 3.0x (proportional upper wick)
        entry = round((price + delta) * 1.01, 2)
        wick = round((entry - price) * 0.05, 2)
        bo_bar = _make_bar(
            symbol=sym,
            open_p=price + delta,
            high_p=round(entry + wick, 2),
            low_p=price,
            close_p=entry,
            vol=50000,
            ts_str="2026-09-21T09:35:00-04:00",
        )
>>>>
```

#### C. News Momentum Tight & Wide Stops (`lines 259-269`)
Confirm candle direction with `open_p < close_p`:
```python
<<<<
        # Breakout bar with tight low: low is just 0.001 below close
        tight_bar = _make_bar(
            symbol=sym,
            open_p=price,
            high_p=price + 0.05,
            low_p=round(price - 0.001, 4),
            close_p=price,
            vol=45000,  # 4.5x surge
            ts_str="2026-09-21T10:21:00-04:00",
        )
====
        # Breakout bar with tight low: low is just 0.001 below close, close > open confirms bullish candle
        tight_bar = _make_bar(
            symbol=sym,
            open_p=round(price - 0.0005, 4),
            high_p=price + 0.05,
            low_p=round(price - 0.001, 4),
            close_p=price,
            vol=45000,  # 4.5x surge
            ts_str="2026-09-21T10:21:00-04:00",
        )
>>>>
```

#### D. Extreme Low Price ORB Clamping (`lines 703-708`)
Scale upper wick proportionally:
```python
<<<<
        entry = round((price + delta) * 1.005, 4)
        bo_bar = _make_bar(
            symbol=sym, open_p=price+delta, high_p=entry+0.05, low_p=price, close_p=entry,
            vol=50000, ts_str="2026-09-21T09:35:00-04:00"
        )
====
        entry = round((price + delta) * 1.005, 4)
        wick = round((entry - price) * 0.1, 4)
        bo_bar = _make_bar(
            symbol=sym, open_p=price+delta, high_p=round(entry+wick, 4), low_p=price, close_p=entry,
            vol=50000, ts_str="2026-09-21T09:35:00-04:00"
        )
>>>>
```

---

## 5. Architectural Recommendations for Implementation Specialist

1. **Apply Challenger 1's CLV Boundary Protection**:
   In `backend/app/strategies/orb.py:53-60`, round CLV to 4 decimals and apply $10^{-5}$ epsilon margin.
2. **Defend Bracket Target Overrides Against Slippage**:
   In `backend/app/core/bracket.py:206-215`, verify that `target_1_override` sits on the profitable side of the actual `fill_price`:
   ```python
   if bracket.target_1_override is not None:
       if bracket.side == "LONG" and bracket.target_1_override > bracket.entry_price:
           bracket.target_1_price = round(bracket.target_1_override, 2)
       elif bracket.side == "SHORT" and bracket.target_1_override < bracket.entry_price:
           bracket.target_1_price = round(bracket.target_1_override, 2)
       else:
           bracket.target_1_price = round(bracket.entry_price + direction * self.default_target_1_r * bracket.r_distance, 2)
   else:
       bracket.target_1_price = round(bracket.entry_price + direction * self.default_target_1_r * bracket.r_distance, 2)
   ```
3. **Run Runner and Dry Run**:
   Verify that `python3 tests/e2e/runner.py` achieves 320/320 tests passed and `scripts/run_integrated_monday_dry_run.py` completes with zero errors and clean port liberation.
