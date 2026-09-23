# Handoff Report: Indicator Causality, Bar Buffering, Lookahead Bias, and Multi-Symbol Session Synchronization

- **Agent**: Explorer R6-2 (`explorer_r6_2_indicators_causality`)
- **Recipient**: Parent Orchestrator (`919291d6-b0dc-48c9-ab39-d3b8659498d2`)
- **Type**: Hard Handoff (Investigation & Forensic Audit Complete)
- **Target System**: `AutonomousDayTrader`
- **Date**: 2026-09-23T20:15:00Z

---

## 1. Observation

Direct code observations from static analysis and execution tracing:

1. **`news_momentum.py` (lines 215–219)**:
   ```python
   now_ts = bar.timestamp.timestamp()
   valid_catalysts = [
       c for c in pending_list
       if (0 <= (now_ts - c.timestamp.timestamp()) <= self.catalyst_ttl_seconds) and not c.processed
   ]
   self.pending_catalysts[sym] = valid_catalysts
   ```
   When a news headline arrives at `10:14:30Z` and a 1-minute bar covering `10:14:00–10:14:59` is ingested with `bar.timestamp = 10:14:00Z`, `now_ts - c.timestamp.timestamp() = -30.0s`. The condition `0 <= -30.0` evaluates to `False`, so `valid_catalysts` is empty. Line 219 overwrites `self.pending_catalysts[sym]` with `[]`, purging the catalyst before the subsequent reacting volume bar arrives.

2. **`main.py` (lines 916–918, 929–945) & `risk.py` (lines 180, 204–205)**:
   In `main.py`:
   ```python
   active_symbols = set(account.positions.keys())
   active_sectors = [
       risk_engine.symbol_sectors.get(s, "Other")
       for s in active_symbols
       if s in risk_engine.symbol_sectors
   ]
   risk_preview = risk_engine.evaluate_order_request(
       ...
       active_positions_count=len(account.positions),
       active_symbols=active_symbols,
       active_sectors=active_sectors,
       ...
   )
   ```
   `account.positions` contains only filled positions. Working orders and brackets in `BracketStatus.PENDING_ENTRY` are omitted from `active_positions_count`, `active_symbols`, and `active_sectors`.

3. **`market_filter.py` (lines 189–191, 197–199)**:
   ```python
   if self.spy_state.last_timestamp:
       spy_ts = _to_utc(self.spy_state.last_timestamp)
       elapsed = (now - spy_ts).total_seconds()
       if elapsed < 0:
           return MarketTrend.UNKNOWN, f"FUTURE_INDEX_DATA: Index timestamp is in the future ({elapsed:.1f}s)"
   ```
   If SPY bar has timestamp `10:00:00.000150` and AAPL signal has `asof = 10:00:00.000000`, `elapsed = -0.000150s < 0`. This returns `MarketTrend.UNKNOWN`, causing `is_signal_permitted` to fail-closed and deny the AAPL signal.

4. **`main.py` (lines 700–708)**:
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
   ```
   There is no monotonicity check. If an out-of-order event arrives where `session_date < last_session_date`, it executes a backward session reset, wiping strategy states and attempting liquidation.

5. **`vwap_pullback.py` (lines 89, 132–133, 158)**:
   ```python
   state.recent_bars.append(bar)
   ...
   volumes = [float(b.volume) for b in state.recent_bars]
   sma10_vol = calculate_sma(volumes, 10)
   ...
   volume_confirmed = bar.volume > 0 and sma10_vol > 0 and bar.volume >= 1.20 * sma10_vol
   ```
   `volumes` includes `bar.volume`, diluting the volume ratio by including the candidate bar in its own baseline.

6. **`orb.py` (lines 129, 151) & `news_momentum.py` (lines 204–207)**:
   In `orb.py`, `state.all_bars.append(bar)` occurs at line 129 before the check `if t_time < open_bell: return []` at line 151. Pre-market bars are appended to `all_bars`, deflating volume and ATR baselines.

7. **`orb.py` (lines 207–215)**:
   `atr = calculate_atr(state.all_bars, period=14)` evaluates ATR over `state.all_bars` which includes the candidate bar `bar`.

8. **`mean_reversion.py` (lines 28–46, 138)**:
   `evaluate_mean_reversion_zscore` hardcodes `prices[-20:]` and `20.0`, ignoring `self.period`.

---

## 2. Logic Chain

1. **Defect V1 (News Catalyst Purging)**:
   - Observation 1 demonstrates that any pending catalyst with $c.\text{timestamp} > \text{bar}.\text{timestamp}$ yields $0 \le (\text{now\_ts} - c.\text{ts}) \le \text{TTL} \equiv \text{False}$.
   - Because `self.pending_catalysts[sym] = valid_catalysts` replaces the buffer with the filtered list, future catalysts are permanently deleted rather than deferred.
   - When the subsequent minute bar arrives representing the market reaction, the catalyst has already been erased, causing zero signals to be emitted.

2. **Defect V2 (Concurrency & Sector Leakage)**:
   - Observation 2 demonstrates that `active_positions_count` and `active_sectors` count only `account.positions`.
   - In a 12-symbol watchlist, multiple breakout signals can trigger on the same 1-minute bar.
   - Because order fills happen asynchronously after signal arbitration, all signals in the same bar batch observe an empty or sub-cap position count.
   - This allows 4 or more positions to be created and filled, breaking the hard institutional invariant of max 3 concurrent positions and max 2 positions per sector.

3. **Defect V3 (Microsecond Clock Skew Rejection)**:
   - Observation 3 shows `elapsed = (now - spy_ts).total_seconds() < 0` triggers `FUTURE_INDEX_DATA`.
   - In WebSocket streaming, microsecond timestamps from exchange matching engines naturally exhibit jitter across symbols in the same 1-minute batch.
   - Any sub-millisecond lead in index timestamps causes legitimate single-stock setups to be rejected.

4. **Defect V4 (Backward Session Reset)**:
   - Observation 4 shows `session_date != last_session_date` triggers session reset without checking `session_date > last_session_date`.
   - Any out-of-order replay or delayed packet causes backward session mutation, clearing brackets and resetting account metrics.

5. **Defect V5 & V7 (Baseline Dilution & Self-Referential ATR)**:
   - Observations 5 and 7 show that candidate bars are included in their own rolling baselines.
   - In `vwap_pullback.py`, candidate volume dilutes SMA10, distorting the $1.20\times$ threshold to $1.227\times$.
   - In `orb.py`, candidate range expands ATR, leaking volatility into the denominator and bypassing the $2.20\times$ range ceiling.

---

## 3. Caveats

1. The audit focused on causal indicator mechanics, bar buffering, lookahead bias, and multi-symbol synchronization. UI component rendering and WebSocket serialization throughput were evaluated for interface contracts but not load-tested under live network congestion.
2. In `mean_reversion.py`, the strict reward-to-risk requirement (`reward / risk >= 1.00`) combined with the 40 bps stop floor mathematically filters out low-volatility mega-caps where 1-minute standard deviation is below 24.2 bps. This is structurally sound risk preservation, but restricts Mean Reversion execution to higher-beta names (`TSLA`, `NVDA`, `COIN`).

---

## 4. Conclusion

The system possesses institutional architecture and strong foundational invariants, but suffers from two **CRITICAL** operational bugs (mid-minute news catalyst purging and pending bracket concurrency leakage) and three **MAJOR** causality/skew vulnerabilities (microsecond index clock rejection, pre-market buffer contamination, and unclosed bar volume dilution).

All defects have concrete, deterministic remediations detailed in `analysis.md`. Applying these fixes will resolve signal starvation in `news_momentum`, prevent portfolio concurrency breaches across the 12-symbol universe, and immunize the market trend filter against microsecond arrival jitter.

---

## 5. Verification Method

### Concrete Test Commands
1. Run backend unit and stress test suite:
   ```bash
   pytest backend/tests -v
   ```
2. Run empirical causality and challenger test suite:
   ```bash
   pytest backend/tests/stress/test_challenger_causality_empirical.py -v
   ```
3. Run end-to-end test runner:
   ```bash
   python3 tests/e2e/runner.py
   ```
4. Run integrated dry run:
   ```bash
   python scripts/run_integrated_monday_dry_run.py
   ```

### Specific Files to Inspect
- `backend/app/strategies/news_momentum.py` (lines 215–225)
- `backend/app/main.py` (lines 700–715, 914–945)
- `backend/app/core/market_filter.py` (lines 189–202)
- `backend/app/strategies/vwap_pullback.py` (lines 132–158)
- `backend/app/strategies/orb.py` (lines 128–154, 207–215)
- `backend/app/strategies/mean_reversion.py` (lines 28–46)

### Invalidation Conditions
- If `news_momentum` is tested with a news event timestamped at minute $T + 30\text{s}$ and fails to emit a signal on the minute $T+1$ bar despite meeting volume and sentiment thresholds, Defect V1 remains unresolved.
- If 4 simultaneous breakout signals on the same bar all transition to `ACCEPTED` and fill when `max_concurrent_positions == 3`, Defect V2 remains unresolved.
- If SPY bar timestamp leads AAPL bar timestamp by $100\mu\text{s}$ and returns `FUTURE_INDEX_DATA`, Defect V3 remains unresolved.
