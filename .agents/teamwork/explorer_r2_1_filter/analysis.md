# Quantitative & Architectural Forensic Analysis: Inverted Mean Reversion & Causal Staleness

**Specialist**: Explorer R2-1 (Inverted Mean Reversion & Causal Staleness Specialist)  
**Date**: 2026-09-23T04:20:00Z  
**Target Codebase**: `AutonomousDayTrader` (`backend/app/core/market_filter.py`, `backend/app/strategies/mean_reversion.py`, `backend/app/strategies/adaptation.py`)  
**Status**: Complete Investigation & Remediation Architecture

---

## 1. Executive Summary

This investigation diagnoses and resolves two critical flaws in `backend/app/core/market_filter.py` identified by Reviewer 1 and Challenger 1:
1. **Critical Inverted Mean Reversion Logic (`market_filter.py:295-301`)**:
   The market trend filter currently rejects `BUY` orders during `BULLISH` market regimes and rejects `SELL` orders during `BEARISH` regimes. Through unhandled fallthrough, it **approves `SELL` (shorting) orders during strong `BULLISH` market rallies** and **approves `BUY` (catching falling knives) during severe `BEARISH` market cascades**. This inverted policy directly re-enables the primary catastrophic failure mode of 2026-09-22, where TSLA (-$68.30) and AAPL (-$112.04) were shorted directly into an aggressive market-wide morning rally.
2. **Forward Lookahead Data Leakage via `abs()` (`market_filter.py:185, 191`)**:
   Staleness elapsed time is calculated using `abs((now - spy_ts).total_seconds())`. When an index bar arrives with a timestamp in the future of the signal evaluation time `now` (`now < spy_ts`), the negative time interval is reflected to positive by `abs()`. Because `|-60.0| = 60.0 <= 120.0`, the system accepts future index bars as "fresh" past observations, introducing forward data leakage.
3. **Unhandled `None` Timestamp Exception in `IndexState.on_bar` / `MarketTrendFilter.on_bar`**:
   Ingesting a `BarEvent` with `timestamp=None` raises an unhandled `AttributeError: 'NoneType' object has no attribute 'tzinfo'`, crashing the ingestion loop rather than failing safe.

This report establishes the microstructurally and mathematically sound regime policy for Statistical Mean Reversion, formulates a strictly causal non-negative staleness guard, and provides verified code diffs for Worker 2.

---

## 2. Forensic Diagnosis: Inverted Mean Reversion Logic

### 2.1 The Code Under Review
In `backend/app/core/market_filter.py:294-301`:
```python
        # 4. Statistical Mean Reversion (Exhaustion fades)
        elif strat == "mean_reversion":
            if trend == MarketTrend.BULLISH and is_buy:
                return False, f"INDEX_FILTER_DENIED: Cannot catch falling knife LONG during strong BULLISH trend"
            if trend == MarketTrend.BEARISH and not is_buy:
                return False, f"INDEX_FILTER_DENIED: Cannot fade overbought SHORT during strong BEARISH trend"

        return True, f"APPROVED: Signal {side} on {symbol} aligned with MarketTrend.{trend.value}"
```

### 2.2 Trace Analysis & Truth Table
Let us trace all four permutations of `(trend, side)` under lines 295–301:

| Case | MarketTrend | OrderSide | Evaluates Line 296 (`BULLISH and is_buy`) | Evaluates Line 298 (`BEARISH and not is_buy`) | Execution Outcome | Intended Institutional Microstructure |
|---|---|---|---|---|---|---|
| **A** | `BULLISH` | `SELL` | `False` (`is_buy=False`) | `False` (`trend!=BEARISH`) | **Line 301 APPROVED**: `APPROVED: Signal OrderSide.SELL on NVDA aligned with MarketTrend.BULLISH` | **CATASTROPHIC HAZARD**: Fading an overbought stock into a market-wide bull rally (short squeeze trap). |
| **B** | `BULLISH` | `BUY` | `True` (`trend==BULLISH and is_buy`) | — | **Line 297 REJECTED**: `INDEX_FILTER_DENIED: Cannot catch falling knife LONG during strong BULLISH trend` | **ERRONEOUS REJECTION**: Buying an oversold pullback with broad market tide at the back is high-probability dip buying. |
| **C** | `BEARISH` | `BUY` | `False` (`trend!=BULLISH`) | `False` (`is_buy=True`) | **Line 301 APPROVED**: `APPROVED: Signal OrderSide.BUY on NVDA aligned with MarketTrend.BEARISH` | **CATASTROPHIC HAZARD**: Catching a falling knife into a market-wide liquidation cascade. |
| **D** | `BEARISH` | `SELL` | `False` (`trend!=BULLISH`) | `True` (`trend==BEARISH and not is_buy`) | **Line 299 REJECTED**: `INDEX_FILTER_DENIED: Cannot fade overbought SHORT during strong BEARISH trend` | **ERRONEOUS REJECTION**: Shorting an overbought relief bounce into resistance in a macro bear market is trend-aligned. |

### 2.3 The Root Cognitive & Semantic Inversion
The defect stems from a complete confusion between **single-stock idiosyncratic price trajectory** and **macro-index systematic drift**:
1. The author observed that Mean Reversion trades when an individual stock has extended far from its mean:
   - For a `BUY` signal: the stock has dropped ($Z \le -2.00$). The author thought: *"The stock is falling; falling stocks are falling knives! Therefore, reject BUY!"*
   - But the author conditioned this check on `trend == MarketTrend.BULLISH`!
   - In a `BULLISH` market, a stock pulling back to an oversold extreme is NOT a systemic falling knife; it is an idiosyncratic dip in a rising market.
   - Conversely, when the market is `BEARISH`, a falling stock is indeed caught in a market-wide liquidation waterfall. Yet during `BEARISH`, the check `is_buy` was ignored, and the falling knife was approved!
2. Symmetrically, for a `SELL` signal: the stock has rallied to an overbought extreme ($Z \ge +2.00$).
   - In a `BEARISH` market, an overbought stock is an exhausted dead-cat bounce ready to roll over with the macro tide. The author rejected it, writing: *"Cannot fade overbought SHORT during strong BEARISH trend"*.
   - In a `BULLISH` market, an overbought stock is surging with systemic momentum. Shorting it is suicidal. Yet the code let it fall through to line 301 and stamped it: `"APPROVED ... aligned with MarketTrend.BULLISH"`.

### 2.4 Empirical Reproduction Against Ledger Failure Modes
On 2026-09-22:
- At 09:31 ET, the system executed a `news_momentum` SHORT on TSLA while SPY and QQQ were surging out of the gate. Result: **-$68.30 loss (full stopout)**.
- At 10:09 ET, the system executed an `orb` SHORT on AAPL while SPY and QQQ were in confirmed bullish expansion. Result: **-$112.04 loss (full stopout)**.

Under Worker 1's code in `market_filter.py:277-293`, ORB and News Momentum correctly reject counter-trend shorts in a `BULLISH` regime (`INDEX_BETA_CONTRADICTION`).
However, for `mean_reversion`, lines 295–301 **specifically permitted SHORT orders in a BULLISH regime**. If TSLA or AAPL had touched $Z \ge 2.0$ after 10:00 ET during the rally, `MarketTrendFilter` would have approved shorting them.

---

## 3. Microstructural & Mathematical Policy Formulation for Mean Reversion

### 3.1 Quantitative Asset Return Decomposition
Asset returns intraday decompose into systematic market beta and idiosyncratic residual components:
$$r_{i,t} = \alpha_i + \beta_i r_{m,t} + \epsilon_{i,t}$$

Where:
- $r_{m,t}$ is the composite market index return (SPY/QQQ).
- $\beta_i \in [1.1, 1.8]$ for mega-cap equities (TSLA, NVDA, AAPL, MSFT, AMZN, META).
- $\epsilon_{i,t}$ is the idiosyncratic residual, governed by an Ornstein-Uhlenbeck (OU) mean-reverting stochastic process:
  $$d\epsilon_t = -\theta \epsilon_t dt + \sigma dW_t, \quad \theta > 0$$

The expected return over horizon $\Delta t$ conditional on current residual $\epsilon_0$ and market drift $\mu_m$:
$$\mathbb{E}[r_{i, \Delta t} \mid \epsilon_0, \mu_m] = \beta_i \mu_m \Delta t + \epsilon_0 (e^{-\theta \Delta t} - 1)$$

### 3.2 Directional Analysis by Market Regime

#### Case 1: Strong Directional Bull Market ($\mu_m \gg 0$, `MarketTrend.BULLISH`)
1. **Shorting Overbought Exhaustion ($Z \ge 2.0$, $\epsilon_0 > 0$, `side = SELL`)**:
   - The trade seeks negative return: $\mathbb{E}[\text{PnL}] \propto -\mathbb{E}[r_{i, \Delta t}]$.
   - Expected drift: $-\beta_i \mu_m \Delta t + \epsilon_0 (1 - e^{-\theta \Delta t})$.
   - Here, $-\beta_i \mu_m < 0$ works directly **against** the trade. In strong trend days, the systematic drift $\beta_i \mu_m$ overwhelms the mean-reversion rate $\theta$.
   - Microstructural hazard: Strong upward market drift causes continuous buying flow (passive index funds, CTA trend followers, dealer gamma-hedging above call strikes). Overbought conditions regularly stretch from $Z = 2.0$ to $Z = 4.0+$. Short positions experience immediate adverse excursion and stop out at maximum risk.
   - **Policy Directive**: `SELL` during `BULLISH` must be **STRICTLY BLOCKED** (`INDEX_BETA_CONTRADICTION`). Zero exceptions.
2. **Buying Oversold Pullback ($Z \le -2.0$, $\epsilon_0 < 0$, `side = BUY`)**:
   - The trade seeks positive return: $\mathbb{E}[\text{PnL}] \propto \mathbb{E}[r_{i, \Delta t}]$.
   - Expected drift: $\beta_i \mu_m \Delta t + |\epsilon_0| (1 - e^{-\theta \Delta t})$.
   - Here, both the systematic drift $\beta_i \mu_m > 0$ and the idiosyncratic reversion force $|\epsilon_0|(1 - e^{-\theta \Delta t}) > 0$ **point in the exact same positive direction**.
   - Microstructural confirmation: `mean_reversion.py` requires volume climax ($\ge 1.75\times$) and lower wick rejection ($\ge 35\%$). This confirms local selling exhaustion. Once selling abates, the macro market bid immediately lifts the stock back toward its 20-SMA.
   - **Policy Directive**: `BUY` during `BULLISH` is **PERMITTED** (trend-aligned dip buying).

#### Case 2: Strong Directional Bear Market ($\mu_m \ll 0$, `MarketTrend.BEARISH`)
1. **Buying Oversold Extreme ($Z \le -2.0$, $\epsilon_0 < 0$, `side = BUY`)**:
   - Systematic drift $-\beta_i |\mu_m| < 0$ crushes the trade.
   - Institutional liquidation, margin calls, and negative gamma sweeps cascade through bids. Bounces are non-existent or truncated.
   - **Policy Directive**: `BUY` during `BEARISH` must be **STRICTLY BLOCKED** (`INDEX_BETA_CONTRADICTION`).
2. **Shorting Overbought Relief Bounce ($Z \ge 2.0$, $\epsilon_0 > 0$, `side = SELL`)**:
   - Both systematic drift $-\beta_i |\mu_m| < 0$ and mean-reversion pull the price downward.
   - Fading an overbought pop in a bear market is trend-aligned shorting.
   - **Policy Directive**: `SELL` during `BEARISH` is **PERMITTED** (trend-aligned relief fade).

#### Case 3: Neutral / Rangebound Market ($\mu_m \approx 0$, `MarketTrend.NEUTRAL`)
- When SPY and QQQ are divergent or hovering within the VWAP deadband ($\pm 0.03\%$), systematic drift is zero ($\mu_m = 0$).
- Asset dynamics reduce purely to the Ornstein-Uhlenbeck process $d\epsilon_t = -\theta \epsilon_t dt + \sigma dW_t$.
- This is the **optimal operational environment** for statistical mean reversion.
- Directional breakout strategies (ORB, VWAP Continuation) fail in chop, but Mean Reversion excels.
- **Policy Directive**: Both `BUY` and `SELL` are **PERMITTED**.

#### Case 4: Unknown / Stale Market State (`MarketTrend.UNKNOWN`)
- Insufficient data, disconnected feeds, or temporal lookahead anomalies.
- **Policy Directive**: All trades are **FAIL-CLOSED REJECTED** (`INDEX_FILTER_DENIED`).

### 3.3 Comparative Policy Evaluation: Asymmetric Macro-Aligned vs Strict Neutral-Only

| Feature | Option 1: Asymmetric Macro-Aligned (Recommended) | Option 2: Strict Neutral-Only |
|---|---|---|
| **BULLISH Regime** | `BUY`: Approved (dip buy)<br>`SELL`: Blocked (short squeeze shield) | `BUY`: Blocked<br>`SELL`: Blocked |
| **BEARISH Regime** | `BUY`: Blocked (knife shield)<br>`SELL`: Approved (relief fade) | `BUY`: Blocked<br>`SELL`: Blocked |
| **NEUTRAL Regime** | `BUY`: Approved<br>`SELL`: Approved | `BUY`: Approved<br>`SELL`: Approved |
| **2026-09-22 Morning Short Protection** | **100% Protected**: Shorting in Bullish is blocked | **100% Protected**: Shorting in Bullish is blocked |
| **Strategy Starvation Risk** | **Low**: Allows high-expectancy pullback trades on trend days | **High**: Zero trades on any day where market establishes a trend |
| **Expected Sharpe Ratio** | **Higher**: Trend-aligned pullbacks have $\mathbb{E}[r] = \beta \mu + \Delta \text{rev}$ | **Lower**: Starves high-win-rate dip buys |

**Recommendation**: **Option 1 (Asymmetric Macro-Aligned)** is the mathematically superior and microstructurally sound policy. It completely eliminates counter-trend shorting into market rallies while preserving high-probability trend-aligned pullbacks.

---

## 4. Forensic Diagnosis: Forward Lookahead via `abs()` & Causal Staleness

### 4.1 The Lookahead Defect
In `backend/app/core/market_filter.py:180-194`:
```python
        # 2. Check freshness against asof timestamp
        now = _to_utc(asof) if asof is not None else datetime.now(timezone.utc)

        if self.spy_state.last_timestamp:
            spy_ts = _to_utc(self.spy_state.last_timestamp)
            dt_spy = abs((now - spy_ts).total_seconds())
            if dt_spy > self.stale_threshold_sec:
                return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: SPY data age ({dt_spy:.1f}s) > {self.stale_threshold_sec}s"

        if self.qqq_state.last_timestamp:
            qqq_ts = _to_utc(self.qqq_state.last_timestamp)
            dt_qqq = abs((now - qqq_ts).total_seconds())
            if dt_qqq > self.stale_threshold_sec:
                return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: QQQ data age ({dt_qqq:.1f}s) > {self.stale_threshold_sec}s"
```

### 4.2 Causality Proof & Vulnerability Mechanics
In continuous-time filtration $\mathcal{F}_t$, a decision at time $t$ must be measurable with respect to $\mathcal{F}_t$:
$$\mathbb{I}_{\text{permitted}} \in \sigma(S_u, M_u : u \le t)$$

1. Let $t = \text{now}$ be the timestamp of the candidate trade signal.
2. Let $t_{\text{index}} = \text{spy\_ts}$ be the timestamp of the latest ingested index bar.
3. The true physical elapsed time is:
   $$\Delta t = t - t_{\text{index}} = \text{now} - \text{spy\_ts}$$
4. For causality to hold:
   $$t_{\text{index}} \le t \iff \Delta t \ge 0$$
5. If $t_{\text{index}} > t$ (i.e. $\Delta t < 0$), the index state contains price and volume information from the future relative to the decision time $t$.
6. Under the vulnerable code:
   $$\text{dt\_spy} = |\Delta t|$$
   If an index bar arrives at $t_{\text{index}} = \text{09:36:00}$ while evaluating a symbol signal at $t = \text{09:35:00}$:
   $$\Delta t = -60.0\text{s} \implies \text{dt\_spy} = |-60.0| = 60.0\text{s}$$
   Because $60.0 \le 120.0$ (`stale_threshold_sec`), this **passes** as "fresh"!
   The strategy evaluates the 09:35:00 trade using SPY's 09:36:00 anchored VWAP and EMA values.
   In live trading with asynchronous WebSocket streams, out-of-order queue processing, or event-driven backtesting/replay, this leaks future market movements into historical decisions.

### 4.3 Causal Non-Negative Time-Arrow Guard
To eliminate lookahead bias, `abs()` must be eliminated and replaced with strict causal validation:
```python
        now = _to_utc(asof) if asof is not None else datetime.now(timezone.utc)

        if self.spy_state.last_timestamp is None or self.qqq_state.last_timestamp is None:
            return MarketTrend.UNKNOWN, "MISSING_INDEX_TIMESTAMP: SPY or QQQ timestamp missing"

        spy_ts = _to_utc(self.spy_state.last_timestamp)
        elapsed_spy = (now - spy_ts).total_seconds()
        if elapsed_spy < 0:
            return (
                MarketTrend.UNKNOWN,
                f"FUTURE_INDEX_DATA: SPY timestamp ({spy_ts.isoformat()}) is in the future of asof ({now.isoformat()})",
            )
        if elapsed_spy > self.stale_threshold_sec:
            return (
                MarketTrend.UNKNOWN,
                f"STALE_INDEX_DATA: SPY data age ({elapsed_spy:.1f}s) > {self.stale_threshold_sec}s",
            )

        qqq_ts = _to_utc(self.qqq_state.last_timestamp)
        elapsed_qqq = (now - qqq_ts).total_seconds()
        if elapsed_qqq < 0:
            return (
                MarketTrend.UNKNOWN,
                f"FUTURE_INDEX_DATA: QQQ timestamp ({qqq_ts.isoformat()}) is in the future of asof ({now.isoformat()})",
            )
        if elapsed_qqq > self.stale_threshold_sec:
            return (
                MarketTrend.UNKNOWN,
                f"STALE_INDEX_DATA: QQQ data age ({elapsed_qqq:.1f}s) > {self.stale_threshold_sec}s",
            )
```

With this guard:
- If `elapsed < 0`: Fail-closed with `FUTURE_INDEX_DATA`.
- If `elapsed > stale_threshold_sec`: Fail-closed with `STALE_INDEX_DATA`.
- Valid domain is strictly: $0 \le \text{elapsed} \le \text{stale\_threshold\_sec}$.

---

## 5. Unhandled `None` Timestamp in `IndexState.on_bar` / `MarketTrendFilter.on_bar`

### 5.1 Observation & Error Trace
In `backend/app/core/market_filter.py:150-160`:
```python
    def on_bar(self, bar: BarEvent) -> None:
        """Ingest bar update for SPY or QQQ."""
        sym = bar.symbol.upper()
        if sym not in ("SPY", "QQQ"):
            return

        # Check session boundary in ET
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        bar_dt = ts.astimezone(ET_TZ)
```
If a bar has `bar.timestamp is None` (e.g. malformed test fixture, corrupted tick, or raw mock), line 158 raises:
`AttributeError: 'NoneType' object has no attribute 'tzinfo'`.

Similarly, in `IndexState.update_bar`:
```python
    def update_bar(self, bar: BarEvent) -> None:
        typical_p = (bar.high + bar.low + bar.close) / 3.0
        ...
        self.last_timestamp = bar.timestamp
```
If `bar.timestamp is None`, `self.last_timestamp` is set to `None`, polluting the state and causing downstream null pointer exceptions.

### 5.2 Remediation
Add defensive null-check guards at the entry of both `MarketTrendFilter.on_bar` and `IndexState.update_bar`:
```python
    def on_bar(self, bar: BarEvent) -> None:
        if bar.timestamp is None:
            return
        sym = bar.symbol.upper()
        ...
```
and
```python
    def update_bar(self, bar: BarEvent) -> None:
        if bar.timestamp is None:
            return
        typical_p = (bar.high + bar.low + bar.close) / 3.0
        ...
```

---

## 6. Concrete Code Diffs for Worker 2

### 6.1 Patch for `backend/app/core/market_filter.py`

```diff
--- a/backend/app/core/market_filter.py
+++ b/backend/app/core/market_filter.py
@@ -73,6 +73,8 @@ class IndexState:
     closes: List[float] = field(default_factory=list)
 
     def update_bar(self, bar: BarEvent) -> None:
+        if bar.timestamp is None:
+            return
         typical_p = (bar.high + bar.low + bar.close) / 3.0
         vol = float(bar.volume)
         self.cum_pv += typical_p * vol
@@ -152,6 +154,8 @@ class MarketTrendFilter:
     def on_bar(self, bar: BarEvent) -> None:
         """Ingest bar update for SPY or QQQ."""
+        if bar.timestamp is None:
+            return
         sym = bar.symbol.upper()
         if sym not in ("SPY", "QQQ"):
             return
@@ -180,18 +184,29 @@ class MarketTrendFilter:
         # 2. Check freshness against asof timestamp
         now = _to_utc(asof) if asof is not None else datetime.now(timezone.utc)
 
-        if self.spy_state.last_timestamp:
-            spy_ts = _to_utc(self.spy_state.last_timestamp)
-            dt_spy = abs((now - spy_ts).total_seconds())
-            if dt_spy > self.stale_threshold_sec:
-                return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: SPY data age ({dt_spy:.1f}s) > {self.stale_threshold_sec}s"
+        if self.spy_state.last_timestamp is None or self.qqq_state.last_timestamp is None:
+            return MarketTrend.UNKNOWN, "MISSING_INDEX_TIMESTAMP: SPY or QQQ timestamp missing"
+
+        spy_ts = _to_utc(self.spy_state.last_timestamp)
+        elapsed_spy = (now - spy_ts).total_seconds()
+        if elapsed_spy < 0:
+            return (
+                MarketTrend.UNKNOWN,
+                f"FUTURE_INDEX_DATA: SPY timestamp ({spy_ts.isoformat()}) is in the future of asof ({now.isoformat()})",
+            )
+        if elapsed_spy > self.stale_threshold_sec:
+            return (
+                MarketTrend.UNKNOWN,
+                f"STALE_INDEX_DATA: SPY data age ({elapsed_spy:.1f}s) > {self.stale_threshold_sec}s",
+            )
 
-        if self.qqq_state.last_timestamp:
-            qqq_ts = _to_utc(self.qqq_state.last_timestamp)
-            dt_qqq = abs((now - qqq_ts).total_seconds())
-            if dt_qqq > self.stale_threshold_sec:
-                return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: QQQ data age ({dt_qqq:.1f}s) > {self.stale_threshold_sec}s"
+        qqq_ts = _to_utc(self.qqq_state.last_timestamp)
+        elapsed_qqq = (now - qqq_ts).total_seconds()
+        if elapsed_qqq < 0:
+            return (
+                MarketTrend.UNKNOWN,
+                f"FUTURE_INDEX_DATA: QQQ timestamp ({qqq_ts.isoformat()}) is in the future of asof ({now.isoformat()})",
+            )
+        if elapsed_qqq > self.stale_threshold_sec:
+            return (
+                MarketTrend.UNKNOWN,
+                f"STALE_INDEX_DATA: QQQ data age ({elapsed_qqq:.1f}s) > {self.stale_threshold_sec}s",
+            )
 
         # 3. Check early open convergence (first 3 minutes)
@@ -295,8 +310,8 @@ class MarketTrendFilter:
         # 4. Statistical Mean Reversion (Exhaustion fades)
         elif strat == "mean_reversion":
-            if trend == MarketTrend.BULLISH and is_buy:
-                return False, f"INDEX_FILTER_DENIED: Cannot catch falling knife LONG during strong BULLISH trend"
-            if trend == MarketTrend.BEARISH and not is_buy:
-                return False, f"INDEX_FILTER_DENIED: Cannot fade overbought SHORT during strong BEARISH trend"
+            if trend == MarketTrend.BULLISH and not is_buy:
+                return False, f"INDEX_BETA_CONTRADICTION: Shorting overbought {symbol} denied during strong BULLISH market rally"
+            if trend == MarketTrend.BEARISH and is_buy:
+                return False, f"INDEX_BETA_CONTRADICTION: Buying oversold {symbol} (catching falling knife) denied during strong BEARISH market decline"
 
         return True, f"APPROVED: Signal {side} on {symbol} aligned with MarketTrend.{trend.value}"
```

### 6.2 Patch for `backend/tests/unit/test_market_filter.py`

```diff
--- a/backend/tests/unit/test_market_filter.py
+++ b/backend/tests/unit/test_market_filter.py
@@ -192,10 +192,26 @@ def test_signal_admission_policy_matrix():
     # Mean Reversion in BULLISH:
-    # Short fade allowed
-    ok, _ = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof_dt)
-    assert ok is True
-    # Long fade (falling knife into bull market) rejected
-    ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof_dt)
-    assert ok is False
-    assert "falling knife" in reason
+    # Long fade (buying oversold dip in bull market) allowed
+    ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof_dt)
+    assert ok is True
+    assert "aligned with MarketTrend.BULLISH" in reason
+    # Short fade (shorting overbought in bull rally) rejected
+    ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof_dt)
+    assert ok is False
+    assert "INDEX_BETA_CONTRADICTION" in reason
+
+    # Mean Reversion in BEARISH:
+    mf.reset_session()
+    for m in range(30, 36):
+        spy_p = 500.0 - (m - 30) * 0.8
+        qqq_p = 450.0 - (m - 30) * 1.0
+        mf.on_bar(_bar("SPY", spy_p + 0.2, spy_p + 0.3, spy_p - 0.9, spy_p - 0.7, vol=50000, minute=m))
+        mf.on_bar(_bar("QQQ", qqq_p + 0.2, qqq_p + 0.3, qqq_p - 1.1, qqq_p - 0.9, vol=40000, minute=m))
+    # Short fade (fading relief bounce in bear market) allowed
+    ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof_dt)
+    assert ok is True
+    assert "aligned with MarketTrend.BEARISH" in reason
+    # Long fade (catching falling knife in bear decline) rejected
+    ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof_dt)
+    assert ok is False
+    assert "INDEX_BETA_CONTRADICTION" in reason
```

### 6.3 Additional Unit Tests to Add in `test_market_filter.py`
Worker 2 should also append unit tests for future lookahead rejection and None timestamp:
```python
def test_market_filter_future_index_lookahead_rejection():
    mf = MarketTrendFilter(stale_threshold_sec=120.0)
    for m in range(30, 36):
        mf.on_bar(_bar("SPY", 500.0, 501.0, 499.0, 500.5, minute=m))
        mf.on_bar(_bar("QQQ", 450.0, 451.0, 449.0, 450.5, minute=m))

    # Index latest bar is at 09:35:00 ET
    # Query with asof in the past: 09:34:30 ET (-30s elapsed -> future data)
    t_past = datetime(2026, 9, 22, 9, 34, 30, tzinfo=ET_TZ)
    trend, reason = mf.get_current_trend(asof=t_past)
    assert trend == MarketTrend.UNKNOWN
    assert "FUTURE_INDEX_DATA" in reason


def test_market_filter_gracefully_handles_none_timestamp():
    mf = MarketTrendFilter()
    # Must not raise AttributeError
    mf.on_bar(BarEvent("SPY", 500.0, 502.0, 499.0, 501.0, 10000, None))
    assert mf.spy_state.bars_count == 0
```

### 6.4 Notice on `test_adversarial_market_filter.py`
In `.agents/teamwork/challenger_1/test_adversarial_market_filter.py:381-382, 425-426`:
Challenger 1 encoded the previous behavior:
```python
# Lines 381-382 (BULLISH):
("mean_reversion", OrderSide.SELL, None, None, True, "APPROVED"),
("mean_reversion", OrderSide.BUY, None, None, False, "falling knife"),

# Lines 425-426 (BEARISH):
("mean_reversion", OrderSide.BUY, None, None, True, "APPROVED"),
("mean_reversion", OrderSide.SELL, None, None, False, "Cannot fade overbought"),
```
When Worker 2 updates `market_filter.py`, these test parameters should be inverted:
```python
# In BULLISH:
("mean_reversion", OrderSide.BUY, None, None, True, "APPROVED"),
("mean_reversion", OrderSide.SELL, None, None, False, "INDEX_BETA_CONTRADICTION"),

# In BEARISH:
("mean_reversion", OrderSide.SELL, None, None, True, "APPROVED"),
("mean_reversion", OrderSide.BUY, None, None, False, "INDEX_BETA_CONTRADICTION"),
```

---

## 7. Verification Method

Worker 2 and independent auditors can verify these changes via:

1. **Verify Correct Mean Reversion Gating**:
   ```bash
   python3 -c "
   from datetime import datetime
   from zoneinfo import ZoneInfo
   from backend.app.core.market_filter import MarketTrendFilter, MarketTrend
   from backend.app.models.events import BarEvent, OrderSide

   ET = ZoneInfo('America/New_York')
   mf = MarketTrendFilter()
   for m in range(5):
       t = datetime(2026, 9, 22, 10, m, 0, tzinfo=ET)
       mf.on_bar(BarEvent('SPY', 500+m, 505+m, 499+m, 504+m, 10000, t))
       mf.on_bar(BarEvent('QQQ', 450+m, 455+m, 449+m, 454+m, 10000, t))

   asof = datetime(2026, 9, 22, 10, 4, 30, tzinfo=ET)
   ok_buy, _ = mf.is_signal_permitted('mean_reversion', OrderSide.BUY, 'NVDA', asof=asof)
   ok_sell, reason = mf.is_signal_permitted('mean_reversion', OrderSide.SELL, 'NVDA', asof=asof)
   assert ok_buy is True, 'Dip buy in bull market must be approved'
   assert ok_sell is False, 'Shorting in bull market must be blocked'
   assert 'INDEX_BETA_CONTRADICTION' in reason
   print('VERIFICATION 1 (MR Bullish Policy): PASSED')
   "
   ```

2. **Verify Future Lookahead Rejection**:
   ```bash
   python3 -c "
   from datetime import datetime
   from zoneinfo import ZoneInfo
   from backend.app.core.market_filter import MarketTrendFilter, MarketTrend
   from backend.app.models.events import BarEvent

   ET = ZoneInfo('America/New_York')
   mf = MarketTrendFilter()
   for m in range(5):
       t = datetime(2026, 9, 22, 10, m, 0, tzinfo=ET)
       mf.on_bar(BarEvent('SPY', 500+m, 505+m, 499+m, 504+m, 10000, t))
       mf.on_bar(BarEvent('QQQ', 450+m, 455+m, 449+m, 454+m, 10000, t))

   asof_past = datetime(2026, 9, 22, 10, 3, 30, tzinfo=ET) # 30s before latest bar
   trend, reason = mf.get_current_trend(asof=asof_past)
   assert trend == MarketTrend.UNKNOWN
   assert 'FUTURE_INDEX_DATA' in reason
   print('VERIFICATION 2 (Future Lookahead Rejection): PASSED')
   "
   ```

3. **Verify Robustness Against None Timestamp**:
   ```bash
   python3 -c "
   from backend.app.core.market_filter import MarketTrendFilter
   from backend.app.models.events import BarEvent

   mf = MarketTrendFilter()
   mf.on_bar(BarEvent('SPY', 500, 505, 499, 504, 10000, None))
   print('VERIFICATION 3 (None Timestamp Crash Immunity): PASSED')
   "
   ```

4. **Run Unit Suite**:
   ```bash
   pytest backend/tests/unit/test_market_filter.py -v
   ```
