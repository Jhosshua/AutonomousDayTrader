# Handoff Report: Execution & Strategies Layer Code Review

**Agent**: Explorer 2 (`explorer_2_strategies_adaptation`)  
**Target Milestone**: Exhaustive Code Review of Execution & Strategies Layer (`backend/app/strategies/`)  
**Date**: 2026-09-23T15:12:00Z  

---

## 1. Observation

Direct observations and evidence collected from static analysis, control-flow tracing, and empirical execution:

### Observation 1.1: VIX Stop Adaptation Directly Violates Risk Floor
- **File**: `backend/app/strategies/adaptation.py:215-224`
  ```python
  def calculate_adapted_stop(self, signal: SignalEvent) -> float:
      raw_dist = abs(signal.entry_price - signal.stop_loss)
      adapted_dist = raw_dist * self.current_stop_multiplier
      is_buy = signal.side == OrderSide.BUY or str(signal.side).upper() == "BUY"
      if is_buy:
          return round(signal.entry_price - adapted_dist, 4)
      else:
          return round(signal.entry_price + adapted_dist, 4)
  ```
- **File**: `backend/app/models/events.py:33`
  ```python
  VIX_REGIME_STOP_MULTIPLIERS: Tuple[float, float, float, float] = (0.85, 1.00, 1.40, 2.00)
  ```
- **File**: `backend/app/core/risk.py:219-228`
  ```python
  stop_dist_pct = stop_dist / entry_price
  EPS = 1e-6
  if stop_dist_pct < self.config.min_stop_distance_pct - EPS:
      return RiskCheckResult(
          approved=False,
          reason=f"STOP_DISTANCE_TOO_TIGHT: Stop distance {stop_dist_pct:.4f} < min {self.config.min_stop_distance_pct:.4f}",
          rejection_code="STOP_DISTANCE_TOO_TIGHT",
      )
  ```
- **Direct Execution Output**:
  ```
  $ python3 -c "from backend.app.strategies.adaptation import DynamicAdaptationEngine; from backend.app.core.risk import InstitutionalRiskEngine, RiskEngineConfig; from backend.app.strategies.base import SignalEvent; from backend.app.models.events import OrderSide, OrderType; adapt = DynamicAdaptationEngine(default_vix=14.0); risk = InstitutionalRiskEngine(RiskEngineConfig()); sig = SignalEvent(symbol='AAPL', side=OrderSide.BUY, order_type=OrderType.MARKET, entry_price=100.0, stop_loss=99.60, take_profit_1=100.80, take_profit_2=101.80, strategy_id='orb', confidence=0.8, reason='test'); adapted_stop = adapt.calculate_adapted_stop(sig); res = risk.evaluate_order_request(symbol='AAPL', side='BUY', requested_qty=10, entry_price=100.0, stop_price=adapted_stop, account_equity=50000.0, buying_power=200000.0, active_positions_count=0, active_symbols=set(), active_sectors=set()); print(res.reason)"
  STOP_DISTANCE_TOO_TIGHT: Stop distance 0.0034 < min 0.0040
  ```

### Observation 1.2: News Momentum Future News Leakage via Negative Elapsed Time
- **File**: `backend/app/strategies/news_momentum.py:214-218`
  ```python
  now_ts = bar.timestamp.timestamp()
  valid_catalysts = [
      c for c in pending_list
      if (now_ts - c.timestamp.timestamp() <= self.catalyst_ttl_seconds) and not c.processed
  ]
  ```
- **Direct Execution Output**:
  ```
  $ python3 -c "from datetime import datetime, timezone; from backend.app.models.events import BarEvent, NewsEvent; from backend.app.strategies.news_momentum import NewsMomentumStrategy; strat = NewsMomentumStrategy(); news = NewsEvent(article_id=1, headline='AAPL beats estimates with record revenue surge', summary='strong', symbols=['AAPL'], source='benzinga', created_at=datetime(2026, 9, 21, 9, 32, 0, tzinfo=timezone.utc), sentiment_score=0.9, sentiment_confidence=0.9); strat.on_news(news); bar = BarEvent(symbol='AAPL', open=100.0, high=102.0, low=99.5, close=101.5, volume=2000000, timestamp=datetime(2026, 9, 21, 9, 30, 0, tzinfo=timezone.utc)); sigs = strat.on_bar(bar); print('Emitted signals count:', len(sigs))"
  Emitted signals count: 1
  ```
  The bar timestamped `09:30:00` (which closed at `09:31:00`) triggered an entry on news published at `09:32:00` because `-120.0 <= 180` evaluates to `True`.

### Observation 1.3: VWAP Pullback Uses Obsolete 1.5R / 2.5R Fallback Multiples
- **File**: `backend/app/strategies/vwap_pullback.py:158-164, 200-206`
  ```python
  tp1 = round(vwap + (1.0 * std), 4)
  if tp1 <= entry_price:
      tp1 = round(entry_price + 1.5 * risk, 4)
  tp2 = round(vwap + (2.0 * std), 4)
  if tp2 <= tp1:
      tp2 = round(entry_price + 2.5 * risk, 4)
  ```
- **File**: `backend/app/main.py:962-963`
  ```python
  bracket = bracket_manager.create_bracket(
      ...
      target_1_override=signal.take_profit_1,
      target_2_override=signal.take_profit_2,
  )
  ```
  `signal.take_profit_1` (1.5R) directly overrides `bracket_manager.default_target_1_r` (0.80R).

### Observation 1.4: Zero-Volume Signal Trigger in VWAP Pullback
- **File**: `backend/app/strategies/vwap_pullback.py:150`
  ```python
  volume_confirmed = bar.volume >= 1.20 * sma10_vol
  ```
- **Direct Execution Output**:
  ```
  Signal on bar 40: VWAP_PULLBACK_LONG: Test of VWAP 99.52, bounce to 100.10, VolSurge=0.00x
  ```
  `0.0 >= 1.20 * 0.0` evaluates to `True`, triggering entries when volume is zero.

### Observation 1.5: Uncapped Monotonic Buffer Growth in News Momentum
- **File**: `backend/app/strategies/news_momentum.py:204-207`
  ```python
  sym = bar.symbol.upper()
  if sym not in self.recent_bars:
      self.recent_bars[sym] = []
  self.recent_bars[sym].append(bar)
  ```
  No deletion or slicing is performed on `self.recent_bars[sym]`.

---

## 2. Logic Chain

1. **Premise 1**: Institutional risk invariants require stop loss distances to remain strictly within `[0.0040, 0.0400]` of entry price (`ORIGINAL_REQUEST.md §R2, §Acceptance Criteria; PROJECT.md §F5; risk.py:45-46`).
2. **From Observation 1.1**: `calculate_adapted_stop()` scales raw stop distance by `0.85` under `VIX < 15.0`. A raw stop placed at the 0.40% floor becomes `0.34%`. The risk engine compares `0.0034 < 0.0040 - 1e-6` and rejects the order.
3. **Conclusion 1**: Under normal low-volatility conditions (VIX < 15), 100% of trades with tight stops are rejected by the risk engine. Under crisis volatility (VIX >= 35), stops wider than 2.0% are scaled past 4.0% and rejected.
4. **Premise 2**: A causal day trading system forbids lookahead bias, unclosed bar access, and future data leakage (`ORIGINAL_REQUEST.md §R3; PROJECT.md §Interface Contracts`).
5. **From Observation 1.2**: In `news_momentum.py:216`, the predicate `(now_ts - c.timestamp.timestamp() <= self.catalyst_ttl_seconds)` lacks a lower bound (`>= 0` or `>= -60s`). When `now_ts < c.timestamp`, the result is negative and evaluates to `True`.
6. **Conclusion 2**: Bars preceding the publication of a news catalyst are permitted to consume future news and fire trades prior to news dissemination.
7. **Premise 3**: Production calibration requires realistic intraday scaling (Target 1 at 0.80R) to eliminate the 0% win rate observed with 1.5R targets (`ORIGINAL_REQUEST.md §2026-09-23T03:48:51Z`).
8. **From Observation 1.3**: `vwap_pullback.py` was not updated to 0.80R and still emits 1.5R / 2.5R fallback targets, which override `bracket_manager` and reinstate the failed 1.5R target geometry.

---

## 3. Caveats

1. **Live Feed Discrepancies vs Synthetic Mock**: AlpacaRelay live WebSocket streams can emit bars and news with network jitter. The lookahead vulnerability in `news_momentum.py` is primarily exploitable during replay or when news and bar streams arrive with timestamp interleaving. In strict live streaming, bars arrive chronologically, but a lower bound is still structurally mandatory.
2. **Market Filter Consensus Requirement**: The market filter enforces SPY and QQQ trend consensus. Under NEUTRAL market trend, both ORB and VWAP Pullback are intentionally blocked by design; this is verified expected behavior per `PROJECT.md §F22`.
3. **Investigation Boundary**: Investigation was conducted in read-only mode in accordance with Explorer role instructions. No application source files were modified.

---

## 4. Conclusion

The Execution & Strategies layer contains **2 CRITICAL**, **7 MAJOR**, and **6 MINOR** defects:
1. **Critical Defect SEC-01**: `DynamicAdaptationEngine.calculate_adapted_stop()` systematically violates the `[0.0040, 0.0400]` stop loss invariant under Low VIX (< 15.0) and Crisis VIX (>= 35.0), leading to 100% order rejection.
2. **Critical Defect SEC-02**: `NewsMomentumStrategy.on_bar()` contains forward data leakage where historical bars consume future news due to negative elapsed time comparison.
3. **Major Architecture Defect SEC-03**: `VWAPPullbackStrategy` fails to adhere to the 0.80R Target 1 recalibration and emits unachievable 1.5R / 2.5R fallback targets.
4. **Major Risk Defect SEC-04**: VWAP pullback band geometry permits micro-reward trades (<0.20R) with inverted risk/reward.
5. **Major Resource Defect SEC-05**: `news_momentum.py` contains a memory leak via unbounded `recent_bars` accumulation.
6. **Major State Defect SEC-06 & SEC-07**: ORB permanently locks out symbols on downstream signal rejection, and spuriously seeds opening ranges on midday bars if the market open was missed.
7. **Major Edge Case Defect SEC-08 & SEC-09**: VWAP pullback confirms bounces on zero volume (`0 >= 0`), and Mean Reversion is starved by conflict between the 0.40% stop floor and the 20-SMA mean reversion target.

All 15 findings are fully documented with line-level code citations and concrete remediations in `analysis.md`.

---

## 5. Verification Method

To independently verify the findings:

1. **Verify VIX Stop Floor Rejection (SEC-01)**:
   ```bash
   python3 -c "
   from backend.app.strategies.adaptation import DynamicAdaptationEngine
   from backend.app.core.risk import InstitutionalRiskEngine, RiskEngineConfig
   from backend.app.strategies.base import SignalEvent
   from backend.app.models.events import OrderSide, OrderType

   adapt = DynamicAdaptationEngine(default_vix=14.0)
   risk = InstitutionalRiskEngine(RiskEngineConfig())
   sig = SignalEvent(symbol='AAPL', side=OrderSide.BUY, order_type=OrderType.MARKET, entry_price=100.0, stop_loss=99.60, take_profit_1=100.80, take_profit_2=101.80, strategy_id='orb', confidence=0.8, reason='test')
   adapted_stop = adapt.calculate_adapted_stop(sig)
   res = risk.evaluate_order_request(symbol='AAPL', side='BUY', requested_qty=10, entry_price=100.0, stop_price=adapted_stop, account_equity=50000.0, buying_power=200000.0, active_positions_count=0, active_symbols=set(), active_sectors=set())
   assert not res.approved, 'Expected rejection'
   assert res.rejection_code == 'STOP_DISTANCE_TOO_TIGHT'
   print('Verified SEC-01: Stop distance', (100.0 - adapted_stop)/100.0, '< 0.0040 rejected with', res.rejection_code)
   "
   ```

2. **Verify News Momentum Lookahead Bias (SEC-02)**:
   ```bash
   python3 -c "
   from datetime import datetime, timezone
   from backend.app.models.events import BarEvent, NewsEvent
   from backend.app.strategies.news_momentum import NewsMomentumStrategy

   strat = NewsMomentumStrategy()
   news = NewsEvent(article_id=1, headline='AAPL beats estimates with record revenue surge', summary='strong', symbols=['AAPL'], source='benzinga', created_at=datetime(2026, 9, 21, 9, 32, 0, tzinfo=timezone.utc), sentiment_score=0.9, sentiment_confidence=0.9)
   strat.on_news(news)
   # Bar 09:30:00 (closed at 09:31:00) before news at 09:32:00
   bar = BarEvent(symbol='AAPL', open=100.0, high=102.0, low=99.5, close=101.5, volume=2000000, timestamp=datetime(2026, 9, 21, 9, 30, 0, tzinfo=timezone.utc))
   sigs = strat.on_bar(bar)
   assert len(sigs) == 1, 'Expected signal on past bar'
   print('Verified SEC-02: Past bar consumed future news and emitted signal:', sigs[0].reason)
   "
   ```

3. **Verify VWAP Pullback Zero-Volume Bounce (SEC-08)**:
   ```bash
   python3 -c "
   from datetime import datetime
   import zoneinfo
   from backend.app.models.events import BarEvent
   from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy

   ET_TZ = zoneinfo.ZoneInfo('America/New_York')
   strat = VWAPPullbackStrategy()
   for m in range(30, 40):
       b = BarEvent(symbol='AAPL', open=99.0 + (m-30)*0.1, high=99.5 + (m-30)*0.1, low=98.5 + (m-30)*0.1, close=99.2 + (m-30)*0.1, volume=10000, timestamp=datetime(2026, 9, 21, 9, m, 0, tzinfo=ET_TZ))
       strat.on_bar(b)

   # Bar with 0 volume
   b_zero = BarEvent(symbol='AAPL', open=100.0, high=100.5, low=99.5, close=100.1, volume=0, timestamp=datetime(2026, 9, 21, 9, 40, 0, tzinfo=ET_TZ))
   sigs = strat.on_bar(b_zero)
   assert len(sigs) == 1, 'Expected signal on zero volume'
   print('Verified SEC-08: Signal on zero volume bar:', sigs[0].reason)
   "
   ```

4. **Verify Existing Test Suite Pass Rate**:
   ```bash
   pytest backend/tests/unit/test_strategies.py
   ```
