# Handoff Report — Inverted Mean Reversion & Causal Staleness Investigation

**Agent**: Explorer R2-1 (Inverted Mean Reversion & Causal Staleness Specialist)  
**Date**: 2026-09-23T04:22:00Z  
**Type**: Hard Handoff (Investigation & Architecture Complete)  
**Target Recipient**: Worker 2 (Remediation Implementer) & Lead Orchestrator  
**Status**: Ready for Code Application & Verification  

---

## 1. Observation

1. **Inverted Mean Reversion Logic in `backend/app/core/market_filter.py:295-301`**:
   ```python
   # 4. Statistical Mean Reversion (Exhaustion fades)
   elif strat == "mean_reversion":
       if trend == MarketTrend.BULLISH and is_buy:
           return False, f"INDEX_FILTER_DENIED: Cannot catch falling knife LONG during strong BULLISH trend"
       if trend == MarketTrend.BEARISH and not is_buy:
           return False, f"INDEX_FILTER_DENIED: Cannot fade overbought SHORT during strong BEARISH trend"

   return True, f"APPROVED: Signal {side} on {symbol} aligned with MarketTrend.{trend.value}"
   ```
   - When `trend == MarketTrend.BULLISH` and `side == OrderSide.SELL`: Line 296 (`is_buy`) is False, line 298 is False, and line 301 executes:
     `APPROVED: Signal SELL on TSLA aligned with MarketTrend.BULLISH`.
     Empirically verified via `python3 -c`: shorting into a confirmed BULLISH trend is approved.
   - When `trend == MarketTrend.BULLISH` and `side == OrderSide.BUY`: Line 296 executes:
     `INDEX_FILTER_DENIED: Cannot catch falling knife LONG during strong BULLISH trend`.
     Empirically verified: dip-buying an oversold pullback during a BULLISH market is rejected.
   - When `trend == MarketTrend.BEARISH` and `side == OrderSide.BUY`: Evaluates to line 301:
     `APPROVED: Signal BUY on TSLA aligned with MarketTrend.BEARISH`.
     Empirically verified: catching a falling knife in a BEARISH market is approved.
   - When `trend == MarketTrend.BEARISH` and `side == OrderSide.SELL`: Line 298 executes:
     `INDEX_FILTER_DENIED: Cannot fade overbought SHORT during strong BEARISH trend`.
     Empirically verified: shorting an overbought relief bounce during a BEARISH market is rejected.

2. **Forward Lookahead Vulnerability via `abs()` in `market_filter.py:185, 191`**:
   ```python
   spy_ts = _to_utc(self.spy_state.last_timestamp)
   dt_spy = abs((now - spy_ts).total_seconds())
   if dt_spy > self.stale_threshold_sec:
       return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: SPY data age ({dt_spy:.1f}s) > {self.stale_threshold_sec}s"
   ```
   - If `asof` is in the past of the index bar (e.g. `now = 10:04:00`, `spy_ts = 10:05:00`), `now - spy_ts` is `-60.0s`.
   - `abs(-60.0) = 60.0 <= 120.0`, which passes the freshness check.
   - Empirically reproduced via `python3 -c`: querying trend asof 10:04:00 when SPY had bars up to 10:05:00 returned `MarketTrend.BULLISH` using SPY's 10:05:00 price and VWAP. Future data leaked into past trend determination.

3. **Unhandled `None` Timestamp Exception**:
   In `market_filter.py:157-160`:
   ```python
   ts = bar.timestamp
   if ts.tzinfo is None:
       ts = ts.replace(tzinfo=timezone.utc)
   bar_dt = ts.astimezone(ET_TZ)
   ```
   When `bar.timestamp is None`, executing `mf.on_bar(BarEvent("SPY", 500, 505, 499, 504, 10000, None))` raised:
   `AttributeError: 'NoneType' object has no attribute 'tzinfo'`.

4. **Test Fixtures Cementing the Defect**:
   - `backend/tests/unit/test_market_filter.py:192-200` specifically asserted the broken inversion:
     ```python
     # Short fade allowed
     ok, _ = mf.is_signal_permitted("mean_reversion", OrderSide.SELL, "NVDA", asof=asof_dt)
     assert ok is True
     # Long fade (falling knife into bull market) rejected
     ok, reason = mf.is_signal_permitted("mean_reversion", OrderSide.BUY, "NVDA", asof=asof_dt)
     assert ok is False
     assert "falling knife" in reason
     ```
   - `.agents/teamwork/challenger_1/test_adversarial_market_filter.py:381-382, 425-426` also asserted the inverted behavior.

---

## 2. Logic Chain

1. **Origin of Drawdown vs Inverted Policy**:
   - *Observation 1* shows that `market_filter.py:295-301` approves `OrderSide.SELL` during `MarketTrend.BULLISH` and rejects `OrderSide.BUY`.
   - *Premise*: The primary failure mode on 2026-09-22 was shorting TSLA (-$68.30) and AAPL (-$112.04) directly into a broad market morning bid.
   - *Deduction*: By approving shorting orders during a bull market and claiming they are "aligned with MarketTrend.BULLISH", the filter directly authorizes the exact counter-trend shorting failure mode it was created to prevent.
   - *Microstructural Principle*: In a confirmed `BULLISH` market ($\mu_m > 0$), systemic delta drift and dealer gamma-hedging dominate. Overbought conditions expand from $Z = 2.0$ to $Z = 4.0+$, resulting in short squeezes. Conversely, an oversold pullback ($Z \le -2.0$) in a bull market is a high-probability dip buy where macro drift $\beta \mu_m > 0$ and mean-reversion drift $-\theta \epsilon > 0$ align in the same positive direction.
   - *Correction*: In `BULLISH`, `SELL` must be strictly blocked (`INDEX_BETA_CONTRADICTION`) and `BUY` permitted. In `BEARISH`, `BUY` must be strictly blocked (falling knife) and `SELL` permitted (relief fade).

2. **Causal Data Integrity vs `abs()`**:
   - *Observation 2* demonstrates that `abs()` transforms negative time differences (where `now < spy_ts`) into positive values, treating future bars as valid past data.
   - *Premise*: Trading systems must enforce strict causality: no information from $t' > t$ may inform decisions at $t$.
   - *Deduction*: The elapsed time calculation $\Delta t = \text{now} - \text{spy\_ts}$ must strictly require $\Delta t \ge 0$. If $\Delta t < 0$, it is a temporal anomaly (`FUTURE_INDEX_DATA`) and must fail-closed to `MarketTrend.UNKNOWN`.

3. **Exception Resilience**:
   - *Observation 3* shows unhandled `AttributeError` on `bar.timestamp is None`.
   - *Premise*: Core market filters must never throw unhandled runtime exceptions on malformed bar events.
   - *Deduction*: Adding `if bar.timestamp is None: return` at the top of `IndexState.update_bar` and `MarketTrendFilter.on_bar` makes ingestion 100% resilient.

---

## 3. Caveats

- **Scope of Responsibility**: Explorer R2-1 is a read-only analysis role. Production file modifications must be executed by Worker 2.
- **E2E Regressions**: The 7 failing tests in `tests/e2e/runner.py` identified by Reviewer 1 (due to Target 1 0.8R default, doji candle `open==close`, and CLV thresholds) are separate from `market_filter.py` and must be addressed by Worker 2 across `bracket.py`, `news_momentum.py`, and `orb.py`.
- **Replay Fixture Index Bars**: `tests/e2e/fixtures/monday_open_session.json` lacks SPY/QQQ bars; Worker 2 must populate index bars so the integrated dry run does not starve ORB and Mean Reversion.

---

## 4. Conclusion

The defects are fully understood, mathematically diagnosed, and proven via isolated reproducible scripts:
1. `backend/app/core/market_filter.py:295-301` must be replaced with the Asymmetric Macro-Aligned Policy:
   ```python
   elif strat == "mean_reversion":
       if trend == MarketTrend.BULLISH and not is_buy:
           return False, f"INDEX_BETA_CONTRADICTION: Shorting overbought {symbol} denied during strong BULLISH market rally"
       if trend == MarketTrend.BEARISH and is_buy:
           return False, f"INDEX_BETA_CONTRADICTION: Buying oversold {symbol} (catching falling knife) denied during strong BEARISH market decline"
   ```
2. `market_filter.py:180-194` must replace `abs()` with strict causal non-negative verification ($0 \le \Delta t \le \text{stale\_threshold\_sec}$).
3. `IndexState.update_bar` and `MarketTrendFilter.on_bar` must guard against `bar.timestamp is None`.
4. Unit tests in `backend/tests/unit/test_market_filter.py` and adversarial tests in `challenger_1` must be aligned with the corrected behavior.

---

## 5. Verification Method

Worker 2 and QA auditors should run the following commands to verify all remediations:

```bash
# 1. Verify Mean Reversion Macro-Regime Matrix
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
print('CHECK 1 PASSED: Bullish Mean Reversion gating verified')
"

# 2. Verify Causal Non-Negative Freshness Guard (Future Bar Rejection)
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
print('CHECK 2 PASSED: Lookahead future bar rejected')
"

# 3. Verify None Timestamp Crash Immunity
python3 -c "
from backend.app.core.market_filter import MarketTrendFilter
from backend.app.models.events import BarEvent

mf = MarketTrendFilter()
mf.on_bar(BarEvent('SPY', 500, 505, 499, 504, 10000, None))
print('CHECK 3 PASSED: None timestamp handled gracefully')
"

# 4. Run Unit Test Suite
pytest backend/tests/unit/test_market_filter.py -v
```

### Invalidation Conditions:
- If `mf.is_signal_permitted("mean_reversion", OrderSide.SELL, sym)` returns `True` when `trend == MarketTrend.BULLISH`.
- If `mf.is_signal_permitted("mean_reversion", OrderSide.BUY, sym)` returns `True` when `trend == MarketTrend.BEARISH`.
- If `mf.get_current_trend(asof)` returns `BULLISH` or `BEARISH` when `asof < last_index_timestamp`.
- If `mf.on_bar(BarEvent(..., timestamp=None))` raises `AttributeError`.
