# Exhaustive Code Review & Technical Audit: Execution & Strategies Layer

**Target Directory**: `backend/app/strategies/`  
**Modules Reviewed**:
- `base.py` (BaseStrategy, SignalEvent, Built-in Indicators: VWAP, ATR, EMA, SMA, RSI, Z-Score, RVOL)
- `orb.py` (OpeningRangeBreakoutStrategy: opening window, CLV, ATR caps, extension caps, RVOL confirmation)
- `vwap_pullback.py` (VWAPPullbackStrategy: Anchored VWAP, standard deviation bands, EMA trend filters, bounce confirmation)
- `news_momentum.py` (NewsMomentumStrategy: Benzinga NLP sentiment scoring, word-boundary regexes, candle confirmation, volume surge baseline, contradiction circuit breaker)
- `mean_reversion.py` (MeanReversionStrategy: Z-score calculation, RSI-14, volume climax, wick rejection, R/R gate)
- `adaptation.py` (DynamicAdaptationEngine: VIX regime scaling, time-of-day phases, market trend filter alignment, admission gates)

---

## Executive Summary of Findings

| ID | Severity | File & Lines | Category | Summary |
|---|---|---|---|---|
| **SEC-01** | **CRITICAL** | `adaptation.py:215-224` | Invariant Violation | **Stop Distance Floor Violation via VIX Scaling**: Multiplying 0.4% floor stops by Low VIX multiplier (0.85x) produces 0.34% stops, triggering 100% rejection by `InstitutionalRiskEngine` (`STOP_DISTANCE_TOO_TIGHT`). In Crisis VIX (2.0x), stops exceed 4.0% ceiling (`STOP_DISTANCE_TOO_WIDE`). |
| **SEC-02** | **CRITICAL** | `news_momentum.py:214-218` | Lookahead Bias / Data Leakage | **Negative Elapsed Time Allows Historical Bars to Consume Future News**: `(now_ts - c.timestamp <= 180)` evaluates True for any `now_ts < c.timestamp`. A bar timestamped before news occurred can consume future news and trigger entries in backtest/replay. |
| **SEC-03** | **MAJOR** | `vwap_pullback.py:158-164, 200-206` | Profit Target Geometry | **Obsolete 1.5R / 2.5R Fallback Targets**: When band target is invalidated, VWAP pullback falls back to 1.5R / 2.5R, overriding `bracket_manager` calibrated 0.8R targets with mathematically unachievable intraday multiples. |
| **SEC-04** | **MAJOR** | `vwap_pullback.py:158, 200` | Inverted Risk/Reward | **Band Geometry Can Yield Microscopic Profit Targets (<0.2R)**: Anchoring Target 1 to `vwap ± 1.0*std` when price is already extended inside the band creates trades risking 1.35*std to gain 0.15*std (~0.11R). |
| **SEC-05** | **MAJOR** | `news_momentum.py:204-207` | Resource Leak | **Unbounded Buffer Growth / Memory Leak in `recent_bars`**: Unlike all other strategies, `news_momentum.py` never trims `self.recent_bars[sym]`, growing monotonically across all ticks. |
| **SEC-06** | **MAJOR** | `orb.py:229` | State Machine Gate | **Premature ORB Lockout on Downstream Rejection**: `state.breakout_fired = True` is set before returning signal. If rejected by Market Filter, concurrency, or risk engine, the symbol is permanently locked out of ORB for the entire session. |
| **SEC-07** | **MAJOR** | `orb.py:155-164` | Semantic Misalignment | **Spurious Midday Opening Range Initialization**: If a symbol has zero bars during 09:30–09:35, the first bar arriving hours later (e.g. 11:00 AM) seeds the "Opening Range", triggering breakout trades on midday 1-minute bars. |
| **SEC-08** | **MAJOR** | `vwap_pullback.py:150, 192` | Boundary Edge Case | **Zero-Volume False Bounce Confirmation**: `bar.volume >= 1.20 * sma10_vol` evaluates to True when volume and `sma10_vol` are both 0.0 (`0.0 >= 0.0`), allowing trades with zero volume. |
| **SEC-09** | **MAJOR** | `mean_reversion.py:170-176, 203-209` | Strategy Starvation | **SMA Mean Reversion Target vs 0.4% Stop Floor Starvation**: Widening raw stop to 0.40% ($0.60 on $150 stock) causes `(reward / risk) >= 1.00` to fail whenever standard deviation is under $0.30, completely starving Mean Reversion under normal/low VIX. |
| **SEC-10** | **MINOR** | `news_momentum.py:33-44, 51-63` | NLP Algorithm | **Token Substring Double-Counting**: Overlapping tokens (`"beats"` & `"beats estimates"`, `"raises"` & `"raises guidance"`) match concurrently on single phrases, artificially doubling the raw sentiment score. |
| **SEC-11** | **MINOR** | `news_momentum.py:49, 55, 61` | NLP Algorithm | **Missing Multi-Word Negation Window**: Negation regex `r"\bnot\s+"` fails when words intervene (e.g. `"not a secondary offering"`), misclassifying negated headlines as bearish. |
| **SEC-12** | **MINOR** | `orb.py:52-60` | Division by Zero / Edge Case | **Zero-Range Bar Triggers SELL Breakout**: When `high == low`, `candle_range = 0.0001` and `clv = 0.0`. Since `0.0 <= max_clv_sell + 1e-5`, flat candles below range low trigger false breakdown signals. |
| **SEC-13** | **MINOR** | `base.py:153-169`, `mean_reversion.py:28-45` | Precision & Duplication | **Duplicated Z-Score Function with Precision Truncation**: `mean_reversion.py` defines its own `evaluate_mean_reversion_zscore` which truncates `std` to 2 decimal places (`round(std, 2)`). When `std < 0.005`, it returns `std = 0.00`, collapsing Target 2 to Target 1. |
| **SEC-14** | **MINOR** | `base.py:22-37`, `orb.py:243`, `news_momentum.py:278-279` | Type Contract | **Undeclared Dynamic Attributes on SignalEvent**: Attributes `sig.rvol`, `sig.catalyst_sentiment`, and `sig.volume_surge` are assigned dynamically without being defined on the `SignalEvent` dataclass. |
| **SEC-15** | **MINOR** | `adaptation.py:66, 112`, `risk.py:43` | Invariant Alignment | **Single-Position Allocation Cap Inconsistency**: `DynamicAdaptationEngine` defaults `max_alloc_pct = 0.25` ($12,500), whereas `risk.py` specifies `max_position_equity_pct = 0.50` ($25,000). Neither contains an absolute dollar cap of $25,000 for equity > $50,000. |

---

## Detailed Findings & Technical Analysis

### 1. [CRITICAL] SEC-01: Stop Distance Floor Violation via VIX Adaptation Tightening
- **Target File**: `backend/app/strategies/adaptation.py` (lines 215–224)
- **Downstream Callers**: `backend/app/main.py` (lines 896, 917–934), `backend/app/core/risk.py` (lines 200–240)
- **Code Observation**:
  ```python
  # backend/app/strategies/adaptation.py:215-224
  def calculate_adapted_stop(self, signal: SignalEvent) -> float:
      """Calculate volatility-adapted stop-loss price scaled by current_stop_multiplier."""
      raw_dist = abs(signal.entry_price - signal.stop_loss)
      adapted_dist = raw_dist * self.current_stop_multiplier
      is_buy = signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY"
      if is_buy:
          return round(signal.entry_price - adapted_dist, 4)
      else:
          return round(signal.entry_price + adapted_dist, 4)
  ```
  In `backend/app/models/events.py`:
  ```python
  VIX_REGIME_STOP_MULTIPLIERS: Tuple[float, float, float, float] = (0.85, 1.00, 1.40, 2.00)
  ```
  In `backend/app/core/risk.py`:
  ```python
  # lines 217-228
  stop_dist_pct = stop_dist / entry_price
  EPS = 1e-6
  if stop_dist_pct < self.config.min_stop_distance_pct - EPS:
      return RiskCheckResult(
          approved=False,
          reason=f"STOP_DISTANCE_TOO_TIGHT: Stop distance {stop_dist_pct:.4f} < min {self.config.min_stop_distance_pct:.4f}",
          rejection_code="STOP_DISTANCE_TOO_TIGHT",
      )
  ```
- **Empirical Demonstration**:
  Running execution with `VIX = 14.0` (Low VIX regime) and a valid strategy stop placed at the 0.40% floor:
  ```
  Entry Price: $100.00
  Strategy Stop: $99.60 (distance = 0.40%)
  VIX = 14.0 -> stop_multiplier = 0.85
  adapted_stop = $100.00 - (0.40 * 0.85) = $99.66
  stop_dist_pct = 0.34%
  Risk Engine Evaluation: STOP_DISTANCE_TOO_TIGHT: Stop distance 0.0034 < min 0.0040
  Order Approved: False
  ```
- **Impact**: In low volatility regimes (VIX < 15.0), any strategy trade that was safely anchored to the 0.4% minimum stop floor (such as tight ORB midpoint stops, VWAP bounces near VWAP, or news breakouts) is scaled down to 0.34%, triggering immediate rejection by the risk engine. In elevated or crisis regimes (VIX >= 35.0, multiplier 2.0x), valid stops wider than 2.0% are scaled beyond 4.0%, causing `STOP_DISTANCE_TOO_WIDE` rejections.
- **Remediation Recommendation**:
  Clamp `adapted_dist` in `calculate_adapted_stop()` strictly within the institutional stop boundaries `[entry_price * 0.0040, entry_price * 0.0400]`:
  ```python
  def calculate_adapted_stop(self, signal: SignalEvent) -> float:
      raw_dist = abs(signal.entry_price - signal.stop_loss)
      adapted_dist = raw_dist * self.current_stop_multiplier
      min_dist = signal.entry_price * 0.0040
      max_dist = signal.entry_price * 0.0400
      clamped_dist = max(min_dist, min(max_dist, adapted_dist))
      is_buy = signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY"
      if is_buy:
          return math.floor((signal.entry_price - clamped_dist) * 10000) / 10000
      else:
          return math.ceil((signal.entry_price + clamped_dist) * 10000) / 10000
  ```

---

### 2. [CRITICAL] SEC-02: News Momentum Causal Lookahead / Forward Data Leakage in Catalyst TTL
- **Target File**: `backend/app/strategies/news_momentum.py` (lines 213–218)
- **Code Observation**:
  ```python
  # backend/app/strategies/news_momentum.py:213-218
  now_ts = bar.timestamp.timestamp()
  valid_catalysts = [
      c for c in pending_list
      if (now_ts - c.timestamp.timestamp() <= self.catalyst_ttl_seconds) and not c.processed
  ]
  ```
- **Empirical Demonstration**:
  ```python
  # News arrives at 09:32:00
  news = NewsEvent(headline="AAPL beats estimates", created_at=datetime(2026, 9, 21, 9, 32, 0, tzinfo=timezone.utc), ...)
  strat.on_news(news)

  # Bar arrives timestamped 09:30:00 (closed at 09:31:00, 1 minute BEFORE news was created)
  bar = BarEvent(symbol="AAPL", timestamp=datetime(2026, 9, 21, 9, 30, 0, tzinfo=timezone.utc), ...)
  sigs = strat.on_bar(bar)
  # Result: Emits SignalEvent from 09:30:00 bar consuming 09:32:00 news!
  ```
  `now_ts - c.timestamp = 09:30:00 - 09:32:00 = -120` seconds. In Python, `-120 <= 180` evaluates to `True`.
- **Impact**: Forward data leakage. Any bar timestamped before the news was published satisfies `(now_ts - c.timestamp <= 180)`. In deterministic replay, backtesting, or if bars arrive out-of-order/delayed relative to news feeds, historical bars execute trades on future news events before they occur.
- **Remediation Recommendation**:
  Enforce causality by bounding elapsed time on both sides:
  ```python
  # Allow the bar that contains the news (bar timestamp <= news timestamp < bar timestamp + 60s)
  # or subsequent bars within the 180s TTL window:
  elapsed = (bar.timestamp - c.timestamp).total_seconds()
  # Bar represents [bar.timestamp, bar.timestamp + 60s). A bar is only valid if it closed after or during the catalyst:
  if -60.0 <= elapsed <= self.catalyst_ttl_seconds and not c.processed:
      ...
  ```

---

### 3. [MAJOR] SEC-03: VWAP Pullback Uses Obsolete, Uncalibrated 1.5R / 2.5R Fallback Targets
- **Target File**: `backend/app/strategies/vwap_pullback.py` (lines 158–164, 200–206)
- **Downstream Callers**: `backend/app/main.py` (lines 962–963), `backend/app/core/bracket.py` (lines 139–140, 220–246)
- **Code Observation**:
  ```python
  # backend/app/strategies/vwap_pullback.py:158-164
  tp1 = round(vwap + (1.0 * std), 4)
  if tp1 <= entry_price:
      tp1 = round(entry_price + 1.5 * risk, 4)
  tp2 = round(vwap + (2.0 * std), 4)
  if tp2 <= tp1:
      tp2 = round(entry_price + 2.5 * risk, 4)

  # backend/app/strategies/vwap_pullback.py:200-206
  tp1 = round(vwap - (1.0 * std), 4)
  if tp1 >= entry_price:
      tp1 = round(entry_price - 1.5 * risk, 4)
  tp2 = round(vwap - (2.0 * std), 4)
  if tp2 >= tp1:
      tp2 = round(entry_price - 2.5 * risk, 4)
  ```
- **Context**: In `ORIGINAL_REQUEST.md`:
  > "Unrealistic Profit Geometry: Target 1 at 1.5R and Target 2 at 2.5R are mathematically unachievable for intraday 1m/5m bars before noise stops out the trade."
  > "R2. Strategy & Execution Architecture Remediation: Enable realistic scaling (e.g., Target 1 at 0.8R–1.0R to de-risk trades quickly)."
- **Impact**: While `orb.py` and `news_momentum.py` were updated to `target_1_r = 0.80`, `vwap_pullback.py` was overlooked and still passes 1.5R and 2.5R overrides to `bracket_manager`. As observed in live production on 2026-09-21/22, trades seeking 1.5R intraday get stopped out by noise or scratched by trailing stops before reaching 1.5R.
- **Remediation Recommendation**:
  Add `target_1_r: float = 0.80` and `target_2_r: float = 1.80` to `VWAPPullbackStrategy.__init__` and use `self.target_1_r` (0.80R) and `self.target_2_r` (1.80R) in fallback target calculations.

---

### 4. [MAJOR] SEC-04: VWAP Pullback Band Geometry Yields Inverted / Microscopic Risk-Reward (<0.2R)
- **Target File**: `backend/app/strategies/vwap_pullback.py` (lines 158–164, 200–206)
- **Code Observation**:
  ```python
  entry_price = bar.close
  stop_loss = round(vwap - (0.50 * std), 4)
  # ...
  tp1 = round(vwap + (1.0 * std), 4)
  ```
- **Mathematical Analysis**:
  Suppose `vwap = 100.00`, `std = 1.00`.
  The bounce bar closes at `entry_price = 100.85` (near the top of the test zone).
  `tp1 = vwap + 1.0 * std = 101.00`.
  Distance to Target 1 = `101.00 - 100.85 = $0.15`.
  Stop Loss = `vwap - 0.50 * std = 99.50`.
  Distance to Stop Loss = `100.85 - 99.50 = $1.35`.
  Reward-to-risk ratio = `$0.15 / $1.35 = 0.111R`!
  Since `tp1 > entry_price` (101.00 > 100.85), the fallback condition `if tp1 <= entry_price:` does NOT trigger!
- **Impact**: The strategy enters trades where the trade risks $1.35 to make $0.15. The target is hit on noise, but any single loss wipes out 9 winning trades.
- **Remediation Recommendation**:
  Ensure Target 1 is at least `entry_price + (self.target_1_r * risk)`:
  ```python
  tp1_band = round(vwap + (1.0 * std), 4)
  tp1_r = round(entry_price + self.target_1_r * risk, 4)
  tp1 = max(tp1_band, tp1_r)
  ```

---

### 5. [MAJOR] SEC-05: Unbounded Buffer Growth / Memory Leak in `NewsMomentumStrategy.recent_bars`
- **Target File**: `backend/app/strategies/news_momentum.py` (lines 204–207)
- **Code Observation**:
  ```python
  sym = bar.symbol.upper()
  if sym not in self.recent_bars:
      self.recent_bars[sym] = []
  self.recent_bars[sym].append(bar)
  ```
  Compare with:
  - `orb.py:125-126`: `if len(state.all_bars) > 60: del state.all_bars[:-60]`
  - `vwap_pullback.py:88-89`: `if len(state.recent_bars) > max_recent: del state.recent_bars[:-max_recent]`
  - `mean_reversion.py:114-115`: `if len(state.bars) > max_bars: del state.bars[:-max_bars]`
- **Impact**: `self.recent_bars[sym]` is never capped or trimmed. In an always-on day trading engine running 390 minutes/day across dozens of symbols, this list grows indefinitely, creating a continuous memory leak.
- **Remediation Recommendation**:
  Cap `self.recent_bars[sym]` to 60 bars (sufficient for the 20-period baseline plus headroom):
  ```python
  if len(self.recent_bars[sym]) > 60:
      del self.recent_bars[sym][:-60]
  ```

---

### 6. [MAJOR] SEC-06: Premature ORB Lockout on Downstream Rejection
- **Target File**: `backend/app/strategies/orb.py` (lines 171, 229)
- **Code Observation**:
  ```python
  # orb.py:229
  state.breakout_fired = True
  sig = SignalEvent(...)
  return [sig]
  ```
  And line 171:
  ```python
  if t_time >= cutoff_time or state.breakout_fired:
      return []
  ```
- **Impact**: `state.breakout_fired = True` is committed immediately when `on_bar()` creates a signal. If the signal is subsequently rejected downstream by `MarketTrendFilter` (e.g. market trend is NEUTRAL at 09:35), `DynamicAdaptationEngine` (max concurrent positions full), or `InstitutionalRiskEngine` (circuit breaker / sector limit), NO order is submitted. However, because `breakout_fired` is already `True`, the symbol is permanently prevented from evaluating any future breakout for the remainder of the session, even if market trend clarifies to BULLISH at 09:38.
- **Remediation Recommendation**:
  Replace boolean lockout with a 15-minute cooldown (identical to `vwap_pullback` and `mean_reversion`):
  ```python
  state.last_signal_time = bar.timestamp
  # At line 171:
  if state.last_signal_time is not None:
      elapsed = (bar.timestamp - state.last_signal_time).total_seconds()
      if elapsed < 15 * 60:
          return []
  ```

---

### 7. [MAJOR] SEC-07: Spurious Midday Opening Range Initialization
- **Target File**: `backend/app/strategies/orb.py` (lines 154–164)
- **Code Observation**:
  ```python
  if not state.range_established:
      if not state.opening_bars:
          # Missed the open: seed the range from this bar but skip signal
          # evaluation, since a range containing the current bar can never
          # be broken out of on that same bar.
          state.opening_bars.append(bar)
          state.range_high = bar.high
          state.range_low = bar.low
          state.range_midpoint = round((bar.high + bar.low) / 2.0, 4)
          state.range_established = True
          return []
  ```
- **Impact**: If a symbol has zero trades during 09:30–09:35 (e.g. trading halt, delayed subscription, illiquid ticker), and its first bar arrives at 11:00 AM, `not state.opening_bars` triggers. That single 11:00 AM 1-minute bar is established as the "Opening Range". On the very next bar at 11:01 AM, any move beyond the 11:00 AM high/low triggers an "Opening Range Breakout" signal. This corrupts the strategy definition and triggers false breakouts on midday noise.
- **Remediation Recommendation**:
  If the session open was missed, ORB should be marked inactive for that symbol for the day, not seeded with a midday bar:
  ```python
  if not state.range_established:
      if not state.opening_bars:
          # Open missed: disable ORB for this symbol today
          state.range_established = True
          state.breakout_fired = True
          return []
  ```

---

### 8. [MAJOR] SEC-08: Zero-Volume False Bounce Confirmation in `VWAPPullbackStrategy`
- **Target File**: `backend/app/strategies/vwap_pullback.py` (lines 150, 192)
- **Code Observation**:
  ```python
  # line 150 (Long)
  volume_confirmed = bar.volume >= 1.20 * sma10_vol
  # line 192 (Short)
  volume_confirmed = bar.volume >= 1.20 * sma10_vol
  ```
- **Empirical Demonstration**:
  When volume dries up across a series of bars, `sma10_vol` becomes `0.0`.
  For a candidate bar with `bar.volume = 0`:
  `0.0 >= 1.20 * 0.0` evaluates to `True`!
  `has_hammer_wick = lower_wick >= 0.30 * candle_range`.
  If price bounces off the low with `close > open`, `(has_hammer_wick or volume_confirmed)` is `True`!
  Empirically verified in test: `Signal on bar 40: VWAP_PULLBACK_LONG: Test of VWAP 99.52, bounce to 100.10, VolSurge=0.00x`.
- **Impact**: In illiquid stocks, halts, or simulation gaps where volume is zero, `volume_confirmed` is satisfied and generates live trading signals with `VolSurge=0.00x`.
- **Remediation Recommendation**:
  Require non-zero volume:
  ```python
  volume_confirmed = bar.volume > 0 and sma10_vol > 0 and bar.volume >= 1.20 * sma10_vol
  ```

---

### 9. [MAJOR] SEC-09: Mean Reversion Starvation via Stop Widening vs SMA Target Conflict
- **Target File**: `backend/app/strategies/mean_reversion.py` (lines 170–176, 203–209)
- **Code Observation**:
  ```python
  entry_price = bar.close
  target_price = round(mean, 4)
  raw_stop = round(bar.high + self.atr_stop_multiplier * atr, 4)
  raw_dist = max(0.01, raw_stop - entry_price)
  stop_loss, risk = resolve_stop(entry_price, raw_dist, False)

  reward = entry_price - target_price
  if reward > 0 and risk > 0 and (reward / risk) >= self.min_rr_ratio:
  ```
- **Mathematical Analysis**:
  For an exhaustion fade back to the 20-period SMA `mean`:
  `reward = entry_price - mean = z * std`.
  At `z = 2.0`, `reward = 2.0 * std`.
  `resolve_stop()` enforces `risk >= entry_price * 0.0040` (0.40%).
  For a $150 stock, `risk` is at least $0.60.
  For `reward / risk >= 1.00`, we must have:
  `2.0 * std >= $0.60` $\implies$ `std >= $0.30` (or `std / entry_price >= 0.20%`).
  In moderate VIX (14–18), 1-minute standard deviation on megacaps is typically $0.15–$0.25 (0.10%–0.16%).
  Therefore, `reward < risk` and `reward / risk` is 0.60–0.80, causing `(reward / risk) >= 1.00` to reject 100% of valid setups.
- **Impact**: Mean reversion setups are systematically starved under moderate VIX (14–16), explaining why Strategy 4 failed to execute in production.
- **Remediation Recommendation**:
  When `risk` is clamped to the 0.4% floor, allow Target 1 scaling or adjust `min_rr_ratio` threshold to 0.70 when raw stop distance was smaller than the risk engine's floor.

---

### 10. [MINOR] SEC-10: Token Substring Double-Counting in Benzinga NLP Sentiment
- **Target File**: `backend/app/strategies/news_momentum.py` (lines 33–44, 51–63)
- **Code Observation**:
  `bullish_tokens` contains both `"beats"` and `"beats estimates"`, `"raises"` and `"raises guidance"`.
  When a headline contains `"beats estimates"`, both tokens match, adding +2.0 to the raw score instead of +1.0.
- **Impact**: Phrases containing compound tokens are artificially overweight.
- **Remediation**: Remove redundant single-word sub-tokens or match compound phrases with precedence.

---

### 11. [MINOR] SEC-11: Missing Multi-Word Negation Window in Regex
- **Target File**: `backend/app/strategies/news_momentum.py` (lines 49, 55, 61)
- **Code Observation**:
  `negation_patterns = [r"\bnot\s+", r"\bnever\s+", r"\bfails\s+to\s+", r"\bunable\s+to\s+"]`
  `re.search(neg + re.escape(w) + r"\b", text)`
  `\bnot\s+` only matches immediately adjacent words (`"not approved"`). Headlines such as `"not a secondary offering"` or `"fails completely to beat"` have intermediate words and are not detected as negated.
- **Impact**: Negated bearish headlines can be classified as bearish.
- **Remediation**: Allow up to 3 intermediate words: `rf"{neg}(?:\w+\s+){{0,3}}{re.escape(w)}\b"`.

---

### 12. [MINOR] SEC-12: Zero-Range Candle Triggers SELL Breakout in ORB
- **Target File**: `backend/app/strategies/orb.py` (lines 52–60)
- **Code Observation**:
  ```python
  candle_range = max(0.0001, high_p - low_p)
  clv = round((close_p - low_p) / candle_range, 4)
  # ...
  elif close_p < range_low:
      if clv <= (max_clv_sell + 1e-5):
          return "SELL"
  ```
  If `high_p == low_p`, `candle_range = 0.0001` and `clv = 0.0`. If `close_p < range_low`, it returns `"SELL"`.
- **Impact**: Flat candles with zero range can trigger a SELL breakout.
- **Remediation**: Check `if high_p - low_p <= 0.001: return None` before computing CLV.

---

### 13. [MINOR] SEC-13: Duplicated Z-Score Function with Precision Truncation
- **Target Files**: `backend/app/strategies/base.py` (lines 153–169), `backend/app/strategies/mean_reversion.py` (lines 28–45)
- **Code Observation**:
  `evaluate_mean_reversion_zscore` in `mean_reversion.py` duplicates `calculate_zscore` from `base.py`. Both return `round(std, 2)`. If `std = 0.004`, it returns `std = 0.00`, causing `take_profit_2 = round(mean - 0.5 * std, 4)` to collapse to `mean`.
- **Remediation**: Remove the duplicate function in `mean_reversion.py`, import `calculate_zscore` from `base.py`, and return unrounded or 4-decimal `std`.

---

### 14. [MINOR] SEC-14: Undeclared Dynamic Attributes on `SignalEvent`
- **Target Files**: `backend/app/strategies/base.py` (lines 22–37), `orb.py` (line 243), `news_momentum.py` (lines 278–279)
- **Code Observation**:
  `sig.rvol = rvol`, `sig.catalyst_sentiment = cat.sentiment`, `sig.volume_surge = vol_ratio` are assigned dynamically. `SignalEvent` does not declare these fields in its `@dataclass`.
- **Impact**: Runtime risk if `SignalEvent` is made immutable (`slots=True` or `frozen=True`).
- **Remediation**: Add explicit optional fields to `SignalEvent`: `rvol: Optional[float] = None`, `catalyst_sentiment: Optional[float] = None`, `volume_surge: Optional[float] = None`.

---

### 15. [MINOR] SEC-15: Single-Position Allocation Cap Inconsistency
- **Target Files**: `backend/app/strategies/adaptation.py` (lines 66, 112), `backend/app/core/risk.py` (line 43)
- **Code Observation**:
  `DynamicAdaptationEngine` uses `max_alloc_pct: float = 0.25` ($12,500 on $50,000 equity). `risk.py` uses `max_position_equity_pct: float = 0.50` ($25,000). Neither contains an absolute dollar cap of $25,000 if equity grows beyond $50,000.
- **Impact**: Inconsistency between sizing pre-gate and risk engine limits.
- **Remediation**: Harmonize `max_alloc_pct` to 0.50 (or explicitly configure from settings) and enforce `min(25000.0, ...)` as an absolute dollar ceiling.
