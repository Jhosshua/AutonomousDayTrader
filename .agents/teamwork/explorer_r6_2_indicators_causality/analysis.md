# Adversarial Audit Report: Indicator Causality, Bar Buffering, Lookahead Bias, and Multi-Symbol Session Synchronization

- **Auditor**: Explorer R6-2
- **Target System**: `AutonomousDayTrader`
- **Date**: 2026-09-23T20:14:30Z
- **Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r6_2_indicators_causality`

---

## 1. Executive Summary

An exhaustive, adversarial code review and quantitative forensic investigation of `AutonomousDayTrader` was conducted across all strategy modules (`orb.py`, `vwap_pullback.py`, `news_momentum.py`, `mean_reversion.py`), market regime filters (`market_filter.py`, `adaptation.py`), core execution and risk systems (`risk.py`, `bracket.py`, `engine.py`, `main.py`), and mathematical indicator functions (`base.py`).

### Key Audit Findings Matrix

| Ref | Severity | Module / Location | Failure Mechanism | Operational Impact |
|---|---|---|---|---|
| **V1** | **CRITICAL** | `news_momentum.py`:215–219 | Overwriting `pending_catalysts` with strict `0 <= (now_ts - c.ts) <= TTL` discards any news arriving mid-minute ($c.ts > bar.ts$). | Strategy is 100% blind to headlines arriving between minute marks. Volume reaction on the subsequent bar finds no pending catalyst. |
| **V2** | **CRITICAL** | `main.py`:914–945, `risk.py`:180–215 | `active_positions_count` and `active_sectors` only inspect filled `account.positions`, ignoring `PENDING_ENTRY` working brackets. | Under 12-symbol universe, simultaneous bar signals bypass the 3-position concurrency cap and 2-position sector cap, allowing up to 4+ positions to fill. |
| **V3** | **MAJOR** | `market_filter.py`:189–199 | Strict `elapsed < 0` without microsecond tolerance causes `FUTURE_INDEX_DATA` when SPY/QQQ bars arrive microseconds ahead of single stocks. | Rejects all single-stock signals under asynchronous exchange arrival jitter during the same 1-minute window. |
| **V4** | **MAJOR** | `main.py`:700–708 | `_check_session_boundary` has no monotonicity guard (`session_date < last_session_date`). | Out-of-order historical bar/quote triggers backward session reset, wiping daily metrics, brackets, and tripping liquidation. |
| **V5** | **MAJOR** | `vwap_pullback.py`:89, 132–158 | Baseline volume `sma10_vol` includes the current candidate bar `bar.volume`. | Dilutes the volume surge ratio, requiring $1.227\times$ prior volume rather than $1.20\times$; violates candidate-baseline separation. |
| **V6** | **MAJOR** | `orb.py`:128–132, 151; `news_momentum.py`:204–207 | Pre-market bars (08:00–09:29) are appended to `all_bars` and `recent_bars` before checking `open_bell`. | Deflates volume and ATR baselines with thin pre-market data, inflating ORB RVOL and rejecting valid breakout bars via artificially small ATR ceilings. |
| **V7** | **MEDIUM** | `orb.py`:207–215 | `calculate_atr(state.all_bars)` includes the candidate breakout bar in its own volatility baseline. | Bar range extension filter leaks the candidate bar's volatility into the ATR denominator, self-expanding the allowable range threshold. |
| **V8** | **MEDIUM** | `orb.py`:167–175 | Late-arriving symbol (09:35–09:45) with 0 opening bars seeds the entire 5m opening range from a single 1-minute bar. | Breaks out against a 1-minute candle rather than an established opening range. |
| **V9** | **MINOR** | `mean_reversion.py`:28–46, 138 | `evaluate_mean_reversion_zscore` hardcodes `prices[-20:]` and ignores `self.period`. Duplicate of `calculate_zscore`. | Configured lookback periods $\ne 20$ are silently ignored. |

---

## 2. In-Depth Forensic Analysis by System Area

### 2.1 Area 1: Unclosed Bar Leakage & Lookahead Bias in Rolling Buffers

#### 2.1.1 Candidate Bar Inclusion in `vwap_pullback.py` Volume Baseline (Defect V5)
- **Code Reference**: `backend/app/strategies/vwap_pullback.py`, lines 89–95, 132–133, 158:
  ```python
  state.recent_bars.append(bar)                      # Line 89: appends current bar
  ...
  volumes = [float(b.volume) for b in state.recent_bars] # Line 132: includes bar.volume
  sma10_vol = calculate_sma(volumes, 10)             # Line 133: candidate bar is in SMA10
  ...
  volume_confirmed = bar.volume >= 1.20 * sma10_vol  # Line 158: candidate compared to itself
  ```
- **Forensic Mechanics**:
  Let $V_c$ be the candidate bar volume, and $S_9 = \sum_{i=1}^9 V_{t-i}$ be the sum of the prior 9 bars. The calculated SMA10 is:
  $$\text{SMA}_{10} = \frac{S_9 + V_c}{10}$$
  The condition $V_c \ge 1.20 \cdot \text{SMA}_{10}$ translates to:
  $$V_c \ge 1.20 \cdot \frac{S_9 + V_c}{10} \implies 10 V_c \ge 1.2 S_9 + 1.2 V_c \implies 8.8 V_c \ge 1.2 S_9$$
  $$V_c \ge \frac{1.2}{8.8} S_9 \approx 0.13636 \cdot S_9 = 1.22727 \cdot \overline{V}_{\text{prior}}$$
  where $\overline{V}_{\text{prior}} = \frac{S_9}{9}$ is the true prior baseline. The threshold is unintentionally tightened by $+2.73\%$ through self-dilution, and more importantly, it violates the non-anticipative causality requirement that rolling baseline statistics must be strictly measurable with respect to the filtration $\mathcal{F}_{t-1}$.
- **Contrast**: Both `orb.py` (line 186: `prior_bars = state.all_bars[:-1][-20:]`) and `news_momentum.py` (line 228: `recent_volumes = [float(b.volume) for b in self.recent_bars[sym][:-1][-20:]]`) correctly sliced `[:-1]` to exclude the candidate bar. `vwap_pullback.py` failed to do so.

#### 2.1.2 Self-Referential ATR Filter in `orb.py` (Defect V7)
- **Code Reference**: `backend/app/strategies/orb.py`, lines 129, 207–215:
  ```python
  state.all_bars.append(bar)                           # Line 129
  ...
  atr = calculate_atr(state.all_bars, period=14)       # Line 207: state.all_bars[-1] is bar
  candle_range = bar.high - bar.low
  if candle_range > (self.max_bar_range_atr * atr):   # Line 210: ATR expanded by candle_range
      return []
  ```
- **Forensic Mechanics**:
  `calculate_atr` with Wilder's smoothing incorporates the True Range of the candidate bar into `atr`. If a bar is extended with a massive True Range $\text{TR}_c$, it pulls up the ATR:
  $$\text{ATR}_t = \frac{13 \cdot \text{ATR}_{t-1} + \text{TR}_c}{14}$$
  The range gate condition $\text{TR}_c \le 2.2 \cdot \text{ATR}_t$ becomes:
  $$\text{TR}_c \le 2.2 \left( \frac{13 \cdot \text{ATR}_{t-1} + \text{TR}_c}{14} \right) \implies \text{TR}_c \left(1 - \frac{2.2}{14}\right) \le \frac{28.6}{14} \text{ATR}_{t-1}$$
  $$\text{TR}_c \le \frac{28.6}{11.8} \text{ATR}_{t-1} \approx 2.4237 \cdot \text{ATR}_{t-1}$$
  An extended candle with a range of $2.35\times$ baseline ATR will PASS the filter when it should have been clipped at $2.20\times$, because it leaks its own outlier range into the ATR denominator.

---

### 2.2 Area 2: Pre-Market Data Contamination of Regular-Session Baselines (Defect V6)

#### 2.2.1 ORB Buffer Contamination
- **Code Reference**: `backend/app/strategies/orb.py`, lines 128–154:
  ```python
  state = self._get_state(bar.symbol)
  state.all_bars.append(bar)                           # Appended unconditionally!
  if len(state.all_bars) > 60:
      del state.all_bars[:-60]
  ...
  if t_time < open_bell:                               # Checks open_bell AFTER appending!
      return []
  ```
- **Forensic Impact**:
  If 30 pre-market bars arrive from 09:00 to 09:29 ET:
  1. `state.all_bars` contains 30 pre-market bars.
  2. At 09:35 ET, when the opening range completes, `prior_bars = state.all_bars[:-1][-20:]` extracts bars from 09:15 to 09:34 (15 pre-market bars and 5 opening range bars).
  3. Pre-market volume is typically $\le 5\%$ of regular hours. For TSLA, pre-market volume may be 3,000 shares/min, while regular hours opening volume is 150,000 shares/min.
  4. The 20-bar average is heavily depressed:
     $$\text{AvgVol} = \frac{15 \times 3,000 + 5 \times 150,000}{20} = \frac{45,000 + 750,000}{20} = 39,750$$
  5. A normal, non-breakout bar with 80,000 shares produces $\text{RVOL} = \frac{80,000}{39,750} = 2.01\times \ge 1.80\times$, triggering an erroneous breakout on below-average volume!
  6. Simultaneously, ATR is depressed by low pre-market bar ranges, causing normal opening volatility to be rejected by the bar range cap.

#### 2.2.2 News Momentum Volume Floor Bypass
- **Code Reference**: `backend/app/strategies/news_momentum.py`, lines 204–207, 231–234:
  ```python
  self.recent_bars[sym].append(bar) # Appends pre-market bars unconditionally
  ...
  recent_volumes = [float(b.volume) for b in self.recent_bars[sym][:-1][-20:]]
  sma20_vol = calculate_sma(recent_volumes, 20)
  if len(recent_volumes) < 5:
      sma20_vol = max(500000.0, sma20_vol)
  ```
- **Forensic Impact**:
  At 09:31 ET, if 10 pre-market bars arrived, `len(recent_volumes) == 10 \ge 5`. The 500,000 share safety floor designed for the open flush is BYPASSED. The baseline volume collapses to pre-market averages (~5,000 shares), allowing a 20,000-share tick to register as a $4.0\times$ volume surge.

---

### 2.3 Area 3: Mid-Minute News Catalyst Purging (Defect V1)

- **Code Reference**: `backend/app/strategies/news_momentum.py`, lines 213–220:
  ```python
  now_ts = bar.timestamp.timestamp()
  valid_catalysts = [
      c for c in pending_list
      if (0 <= (now_ts - c.timestamp.timestamp()) <= self.catalyst_ttl_seconds) and not c.processed
  ]
  self.pending_catalysts[sym] = valid_catalysts
  ```
- **Timeline Analysis of the Failure**:
  1. `T = 10:14:20Z`: Benzinga news headline arrives with sentiment $+0.88$. It is recorded in `pending_catalysts[sym]` with `timestamp = 10:14:20Z`.
  2. `T = 10:15:00Z`: Exchange finishes the 1-minute bar for `10:14:00–10:14:59`. The bar event arrives at the bot with `bar.timestamp = 10:14:00Z` (Alpaca bar start convention).
  3. `on_bar` executes for `bar_1014`:
     - `now_ts = 10:14:00.0`
     - `c.timestamp.timestamp() = 10:14:20.0`
     - $\Delta t = \text{now\_ts} - c.\text{ts} = -20.0\,\text{s}$
     - Condition $0 \le -20.0 \le 180$ evaluates to `False`.
     - `valid_catalysts` is empty `[]`.
     - `self.pending_catalysts[sym] = []` **overwrites and obliterates the catalyst!**
  4. `T = 10:16:00Z`: The bar covering `10:15:00–10:15:59` arrives, showing a $3.8\times$ volume surge reacting to the 10:14:20 headline.
  5. `on_bar` inspects `self.pending_catalysts[sym]`, finds it **empty**, and returns `[]`.
  6. **Conclusion**: Strategy 3 is completely incapable of trading any headline that breaks during a minute bar.

---

### 2.4 Area 4: Microsecond Skew False Rejections in MarketTrendFilter (Defect V3)

- **Code Reference**: `backend/app/core/market_filter.py`, lines 189–201:
  ```python
  if self.spy_state.last_timestamp:
      spy_ts = _to_utc(self.spy_state.last_timestamp)
      elapsed = (now - spy_ts).total_seconds()
      if elapsed < 0:
          return MarketTrend.UNKNOWN, f"FUTURE_INDEX_DATA: Index timestamp is in the future ({elapsed:.1f}s)"
  ```
- **Mechanism**:
  - In a 12-symbol universe, AlpacaRelay distributes 1-minute bars with microsecond precision.
  - When SPY bar has timestamp `10:00:00.000250` and arrives first, `spy_state.last_timestamp` is updated.
  - When AAPL bar has timestamp `10:00:00.000100` and generates an ORB signal, `asof` is `10:00:00.000100`.
  - In `get_current_trend(asof=10:00:00.000100)`:
    $$\text{elapsed} = 10:00:00.000100 - 10:00:00.000250 = -0.000150\,\text{s} < 0$$
  - Strict zero tolerance triggers `FUTURE_INDEX_DATA`, returning `MarketTrend.UNKNOWN`.
  - `is_signal_permitted` returns `(False, "INDEX_FILTER_DENIED: Market trend UNKNOWN")`.
  - Every single-stock breakout is rejected whenever index feed microsecond timestamps lead the single stock.

---

### 2.5 Area 5: Risk Engine & Concurrency Blindness to Pending Entry Brackets (Defect V2)

- **Code Reference**: `backend/app/main.py`, lines 914–945:
  ```python
  active_symbols = set(account.positions.keys())
  active_sectors = [
      risk_engine.symbol_sectors.get(s, "Other")
      for s in active_symbols
      if s in risk_engine.symbol_sectors
  ]
  risk_preview = risk_engine.evaluate_order_request(
      ...
      active_positions_count=len(account.positions), # Only filled positions!
      active_symbols=active_symbols,
      active_sectors=active_sectors,
      ...
  )
  ```
- **Mechanism & Race Condition**:
  - `account.positions` records only filled positions.
  - At 09:35:00 ET, ORB signals fire for `NVDA`, `AMD`, `MSFT`, and `PLTR`.
  - `handle_bar_event` processes signals sequentially in the single-threaded event loop:
    1. `NVDA` evaluated: `len(account.positions) == 0`. NVDA approved, working order created, bracket status `PENDING_ENTRY`.
    2. `AMD` evaluated: `len(account.positions) == 0` (NVDA is not yet filled!). AMD approved, working order created, bracket status `PENDING_ENTRY`.
    3. `MSFT` evaluated: `len(account.positions) == 0`. MSFT approved, working order created.
    4. `PLTR` evaluated: `len(account.positions) == 0`. PLTR approved, working order created.
  - When the bar fills execute at line 1044 (`engine.process_bar`):
    - All 4 orders fill.
    - Portfolio now holds 4 active positions, violating `max_concurrent_positions = 3`.
    - Both NVDA and AMD fill, plus if a 3rd semiconductor was submitted, it fills, violating `max_positions_per_sector = 2`.
  - **Invariant Breach**: The institutional risk guardrail ($50,000 equity, max 3 positions, max 2 per sector) is breached because the risk gate does not count in-flight working orders / pending brackets.

---

### 2.6 Area 6: Non-Monotonic Session Boundary Handling (Defect V4)

- **Code Reference**: `backend/app/main.py`, lines 700–707:
  ```python
  def _check_session_boundary(now_dt: datetime) -> None:
      global last_session_date
      session_date = now_dt.astimezone(ET_TZ).date()
      if last_session_date == session_date:
          return
      is_first_observation = last_session_date is None
      previous_session_date = last_session_date
      if is_first_observation:
          last_session_date = session_date
          return
      ...
      # Session boundary wipe and liquidation triggers here
  ```
- **Mechanism**:
  - The system checks `if last_session_date == session_date: return`.
  - If a delayed quote, network retransmission, or replay packet from `2026-09-22 15:59:59` arrives while the system has already advanced to `2026-09-23`:
    $$\text{session\_date} = 2026-09-22 \ne 2026-09-23 = \text{last\_session\_date}$$
  - The function interprets this as a new session boundary, resets the risk engine, purges active brackets, clears strategy states, and attempts liquidation!
  - Temporal monotonicity requirement ($t_{k} \ge t_{k-1}$) is missing.

---

### 2.7 Area 7: Microstructure Boundary Parameters & Threshold Compliance

1. **RVOL Thresholds**:
   - `orb.py` line 45: `rvol < 1.80` returns `None`. Confirmed compliant with $1.80\times$ baseline.
   - `market_filter.py` line 298: In `MarketTrend.NEUTRAL`, requires `rvol >= 2.20`. Confirmed compliant with $2.20\times$ idiosyncratic breakout requirement.
2. **Mean Reversion Parameters**:
   - `mean_reversion.py`: `z_threshold = 1.65`, `volume_climax_multiplier = 1.30`, `min_wick_ratio = 0.30`.
   - Condition at line 175: `(reward / risk) >= self.min_rr_ratio` (1.00).
   - In low-beta mega-caps, `resolve_stop` widens the stop to the 40 bps floor ($0.0040 \cdot \text{entry}$). Since the mean reversion target is the 20-SMA ($Z \cdot \text{std}$), whenever $1.65 \cdot \text{std} < 0.0040 \cdot \text{entry}$ ($\text{std} < 24.2\,\text{bps}$), the signal is silently dropped by the R:R filter. This is an intended risk preservation mechanism, but must be documented.
3. **News Momentum Surge**:
   - `news_momentum.py`: `volume_surge_multiplier = 2.00`. Confirmed calibrated from 3.50x to 2.00x.
   - In `market_filter.py`: `news_momentum` in `NEUTRAL` regime requires `rvol >= 2.20`.

---

## 3. Production-Grade Fix Strategies

### Fix 1: Microsecond Skew Tolerance in `market_filter.py`
```python
# File: backend/app/core/market_filter.py
SKEW_TOLERANCE_SEC: float = 1.0  # Allow up to 1000ms inter-symbol clock jitter

if self.spy_state.last_timestamp:
    spy_ts = _to_utc(self.spy_state.last_timestamp)
    elapsed = (now - spy_ts).total_seconds()
    if elapsed < -SKEW_TOLERANCE_SEC:
        return MarketTrend.UNKNOWN, f"FUTURE_INDEX_DATA: Index timestamp is in the future ({elapsed:.1f}s)"
```

### Fix 2: Causal News Catalyst Retention in `news_momentum.py`
```python
# File: backend/app/strategies/news_momentum.py
now_ts = bar.timestamp.timestamp()
# Bar duration is 60 seconds (covers bar.timestamp to bar.timestamp + 60s)
bar_close_ts = now_ts + 60.0

# 1. Retain catalysts that have not expired relative to bar close
self.pending_catalysts[sym] = [
    c for c in pending_list
    if ((bar_close_ts - c.timestamp.timestamp()) <= self.catalyst_ttl_seconds) and not c.processed
]

# 2. Select eligible catalysts that arrived prior to or during the bar, within TTL
valid_catalysts = [
    c for c in self.pending_catalysts[sym]
    if (0 <= (bar_close_ts - c.timestamp.timestamp()) <= self.catalyst_ttl_seconds)
]
```

### Fix 3: Regular Session Pre-Market Exclusion in `orb.py`
```python
# File: backend/app/strategies/orb.py
# Ingest bars during opening range (09:30 to 09:30 + range_minutes)
if t_time < open_bell:
    # Pre-market bar: do NOT pollute regular session baseline or ATR buffer
    return []

state.all_bars.append(bar)
if len(state.all_bars) > 60:
    del state.all_bars[:-60]
```

### Fix 4: Candidate Bar Exclusion in `vwap_pullback.py` Volume Baseline
```python
# File: backend/app/strategies/vwap_pullback.py
# Exclude candidate bar from volume baseline
prior_volumes = [float(b.volume) for b in state.recent_bars[:-1][-10:]]
sma10_vol = calculate_sma(prior_volumes, 10) if prior_volumes else float(bar.volume)
```

### Fix 5: Prior-Bar ATR Slicing in `orb.py`
```python
# File: backend/app/strategies/orb.py
# ATR calculated on prior closed bars to prevent candidate range leakage
atr = calculate_atr(state.all_bars[:-1], period=14) if len(state.all_bars) > 1 else calculate_atr(state.all_bars, period=14)
```

### Fix 6: In-Flight Pending Bracket Tracking in `main.py`
```python
# File: backend/app/main.py
# Track both filled positions and pending entry brackets
active_symbols = set(account.positions.keys())
for b in bracket_manager.brackets.values():
    if b.status == BracketStatus.PENDING_ENTRY:
        active_symbols.add(b.symbol)

active_positions_count = len(active_symbols)
active_sectors = [
    risk_engine.symbol_sectors.get(s, "Other")
    for s in active_symbols
    if s in risk_engine.symbol_sectors
]
```

### Fix 7: Monotonicity Enforcement in `main.py` Session Boundary
```python
# File: backend/app/main.py
session_date = now_dt.astimezone(ET_TZ).date()
if last_session_date is not None:
    if session_date == last_session_date:
        return
    if session_date < last_session_date:
        log.warning("Ignoring out-of-order historical event from %s (current session: %s)", session_date, last_session_date)
        return
```

---

## 4. Deterministic Mutation Test Designs

### Mutation Test 1: Microsecond Clock Jitter in Market Filter
- **Hypothesis**: A sub-millisecond arrival skew between index and single-stock must not cause `FUTURE_INDEX_DATA`.
- **Test Setup**:
  1. Feed SPY bar at `2026-09-24T09:35:00.000200Z`.
  2. Evaluate AAPL signal at `2026-09-24T09:35:00.000050Z` ($\Delta t = -150\,\mu\text{s}$).
- **Mutant Behavior** (current code): Returns `MarketTrend.UNKNOWN`, reason `"FUTURE_INDEX_DATA: Index timestamp is in the future (-0.0s)"`. Signal denied.
- **Fixed Behavior**: Tolerates skew $< 1.0\,\text{s}$, returns `MarketTrend.BULLISH`. Signal approved.

### Mutation Test 2: Mid-Minute News Catalyst Survival
- **Hypothesis**: News published at `10:15:30Z` must trigger on the `10:15:00Z` bar when processed at `10:16:00Z`.
- **Test Setup**:
  1. Publish headline at `10:15:30Z` with sentiment $+0.85$.
  2. Feed bar covering `10:15:00–10:15:59` with timestamp `10:15:00Z` and $3.0\times$ volume.
- **Mutant Behavior** (current code): Catalyst dropped on bar ingestion because `10:15:00 - 10:15:30 < 0`. Zero signals.
- **Fixed Behavior**: Catalyst is retained, evaluated against the completing bar, and emits `SignalEvent(OrderSide.BUY)`.

### Mutation Test 3: Pre-Market Contamination Elimination in ORB RVOL
- **Hypothesis**: 30 pre-market bars must not deflate the opening range volume baseline.
- **Test Setup**:
  1. Feed 30 pre-market bars with volume 2,000.
  2. Feed 5 opening range bars (09:30–09:34) with volume 100,000.
  3. Feed 09:35 candidate breakout bar with volume 150,000.
- **Mutant Behavior** (current code): Pre-market bars in `state.all_bars` deflate average to $\sim 26,000$, yielding an absurd $\text{RVOL} = 5.77\times$.
- **Fixed Behavior**: Baseline correctly uses only regular session bars ($\text{AvgVol} = 100,000$), yielding clean $\text{RVOL} = 1.50\times$ (correctly rejected as $< 1.80\times$).

### Mutation Test 4: VWAP Pullback Candidate Bar Volume Exclusion
- **Hypothesis**: Baseline volume must not include the candidate bar's own volume.
- **Test Setup**:
  1. Feed 9 bars with volume 10,000.
  2. Candidate 10th bar arrives with volume 12,100 ($1.21\times$ prior baseline).
- **Mutant Behavior** (current code): Candidate bar dilutes SMA10 to 10,210. Ratio becomes $12,100 / 10,210 = 1.185\times < 1.20\times$. Signal rejected.
- **Fixed Behavior**: Baseline uses prior 9 bars ($10,000$). Ratio is $12,100 / 10,000 = 1.21\times \ge 1.20\times$. Signal fires.

### Mutation Test 5: In-Flight Concurrent Bracket Overflow Prevention
- **Hypothesis**: In-flight `PENDING_ENTRY` brackets must be counted towards the 3-position cap and 2-per-sector cap.
- **Test Setup**:
  1. Open 2 filled positions (e.g. `AAPL`, `MSFT`).
  2. Simultaneously emit breakout signals for `NVDA` (Semis) and `AMD` (Semis).
- **Mutant Behavior** (current code): Both signals pass because `len(account.positions) == 2`. 4 positions fill, breaching concurrency and sector limits.
- **Fixed Behavior**: `NVDA` fills the 3rd slot. `AMD` is rejected with `MAX_CONCURRENT_POSITIONS_REACHED` and `CORRELATED_SECTOR_EXPOSURE`.
