# ERRORS.md — AutonomousDayTrader

## 2026-09-23: Ingestion Queue Saturation Dropping Critical Candle Bars and Fills (R6-1)

**What did not work**: Under extreme quote floods across 12 tickers (~3.5M quotes/session), incoming wire messages filled the FIFO queue beyond `QUEUE_MAX_SIZE` (10,000 items). An unprioritized queue dropped all subsequent messages via `asyncio.QueueFull`, discarding critical candle bars (`b`) and trade execution prints (`t`).

**What worked instead**: Implemented prioritized frame detection in `stock_ws._read_loop`. If the queue is saturated, high-priority frames (`"T":"b"`, `"T":"t"`, `"T":"relay"`) discard older quote frames (`q`) via `self._queue.get_nowait()` before inserting, guaranteeing zero dropped bars or execution reconciliation prints.

**Note for next time**: Real-time market data queues must implement QoS priority tiers. High-frequency telemetry (quotes) must yield to lower-frequency, state-critical frames (bars, trades) during saturation.

## 2026-09-23: Mid-Minute News Catalyst Dropping on Start-of-Minute Evaluation (R6-1)

**What did not work**: In `news_momentum.py`, news catalysts arriving at e.g. 09:35:45 were compared against bar timestamp 09:35:00. The check `(now_ts - c.timestamp.timestamp()) <= TTL` treated `elapsed` as negative (-45s) and dropped the catalyst when purging expired news, missing the breakout on the 09:36 reaction bar. Additionally, non-watchlist symbols accumulated without bound in `pending_catalysts`.

**What worked instead**: Filtered catalysts to `settings.WATCHLIST_SYMBOLS`, open positions, and active bars; capped queue depth to 10; and preserved mid-minute catalysts (`0 < c.timestamp - now_ts <= 60.0`) across the bar duration so subsequent reaction bars can trigger breakouts.

**Note for next time**: When aligning asynchronous event timestamps (news) with discretely bucketed candle bars, ensure the sliding retention window accounts for the candle's duration ($t_{bar} \le t_{event} < t_{bar} + \Delta t$).

## 2026-09-23: Indicator Baseline Self-Contamination Diluting Breakout RVOL and ATR (R6-2)

**What did not work**: In `vwap_pullback.py` and `orb.py`, rolling volume SMA and ATR calculations included the current candidate breakout bar itself (`state.recent_bars` or `state.all_bars`). On large expansion candles, the candidate bar's own elevated volume and range inflated the baseline denominator, artificially compressing calculated RVOL and ATR multiples and failing entry filters.

**What worked instead**: Sliced baselines strictly to prior closed bars: `state.recent_bars[:-1][-10:]` for VWAP pullback volume and `state.all_bars[:-1]` for ORB ATR, strictly maintaining indicator causality.

**Note for next time**: Never include the current candidate/decision bar in its own baseline reference window. The baseline must represent strictly prior, fully finalized state.

## 2026-09-23: Sub-Second NTP Jitter Triggering False Future Index Rejections (R6-2)

**What did not work**: In `MarketTrendFilter`, a strict zero-tolerance guard `if elapsed < 0: return FUTURE_INDEX_DATA` caused quotes arriving with microsecond clock skew (e.g. -0.002s) to be rejected as lookahead leaks, marking market trend as `UNKNOWN` and intermittently freezing strategy execution.

**What worked instead**: Relaxed the forward tolerance bound to `elapsed < -1.0s`, accommodating realistic sub-second NTP network jitter while strictly rejecting genuine lookahead bias and forward-dated feeds.

**Note for next time**: Causal time checks must distinguish between microsecond clock jitter/network skew and actual forward-data anomalies by including a tight, realistic tolerance epsilon ($\approx 1.0\text{s}$).

## 2026-09-23: Pre-Trade Drawdown Check Bypass and Uncapped Loss Budgeting (R6-3)

**What did not work**: In `InstitutionalRiskEngine.evaluate_order_request`, the circuit breaker check checked `self.status != BreakerStatus.ARMED`. Because breaker transitions occurred periodically or after fills, intraday equity drawdown could cross the $1,500 threshold between evaluations, allowing new orders to execute during an active hard-loss state. Furthermore, orders were sized without verifying that risk dollars fit within the remaining daily loss budget ($1,500 - \text{drawdown}$).

**What worked instead**: Added an immediate, real-time drawdown check `dd_dollars >= self.config.hard_max_daily_loss_dollars` that halts new orders with `CIRCUIT_BREAKER_HALTED`. Capped order risk via `min(target_risk_dollars, remaining_loss_budget)`.

**Note for next time**: Real-time pre-trade risk checks must independently evaluate raw current equity and hard boundaries on every single order request, rather than relying solely on cached state machine flags.

## 2026-09-23: Multi-Ticker Signal Collisions Racing Past Portfolio Concentration Caps (R6-3)

**What did not work**: With 12 active tickers, multiple breakout strategies fired signals on the same millisecond tick. Because order fills occur asynchronously on subsequent quotes, `len(account.positions)` remained 0 while all 12 orders were evaluated, accepting up to 12 orders and drastically breaching the 3-position total cap and 2-position sector cap once filled.

**What worked instead**: Implemented `_get_effective_committed_portfolio` in `backend/app/main.py`. This routine constructs the union of filled positions, accepted entry orders, and pending brackets, evaluating both total portfolio commitments and per-sector reservations atomically during pre-trade filtering.

**Note for next time**: In asynchronous trading systems, portfolio capacity must track committed orders in-flight, not just completed fills.

## 2026-09-23: Phase 2 EOD Flattening Order Purge Stripping Protective Stops (R6-3)

**What did not work**: At 15:50 ET (Phase 2 `ORDER_PURGE`), the flattening routine cancelled all working orders across all symbols. This stripped protective stop-loss orders from open positions, leaving active holdings completely unhedged and exposed for 5 minutes until market liquidation at 15:55 ET (Phase 3).

**What worked instead**: Refactored Phase 2 purge to cancel ONLY unfilled entry orders, explicitly preserving active protective stop brackets until Phase 3 executes market liquidations.

**Note for next time**: Flattening phases must preserve defensive exit orders while active exposure remains on the book. Never strip protective stops before the underlying position is closed.

## 2026-09-23: Non-Finite Floats (NaN/Infinity) Crashing WebSocket Frontend Deserialization (R6-3)

**What did not work**: Division-by-zero or zero-range ATR/volatility conditions generated `NaN` or `Infinity` float values in backend state dictionaries. Standard Python `json.dumps()` serialized these as bare tokens (`NaN`, `Infinity`), violating RFC 8259 and crashing browser `JSON.parse` with `SyntaxError: Unexpected token N in JSON`.

**What worked instead**: Implemented recursive `_sanitize_for_json` replacing all `NaN` and `Infinity` floats with `0.0`, and passed `allow_nan=False` to `json.dumps` to ensure strict RFC 8259 compliance. Added coordinate guards in `LiveChart.tsx`.

**Note for next time**: Never trust IEEE 754 floating point numbers to be finite when serializing over JSON APIs. Always enforce RFC 8259 compliance at the serialization boundary.

## 2026-09-23: Filter-Stacking Bottleneck Causing Total Strategy Starvation

**What did not work**: The combination of a narrow 3-single-stock watchlist, binary sector lockout, extreme volume surge hurdles ($3.5\times$ on 1-minute bars), and locking out all directional strategies during `NEUTRAL` market regimes without an active mean reversion counterpart caused a filter-stacking bottleneck. Trade frequency collapsed to ~0 trades/day despite normal market liquidity and hours.

**What worked instead**: Decoupled the filter stack:
1. Expanded universe to 12 liquid, high-beta symbols across multiple sectors (`SPY`, `QQQ`, `AAPL`, `NVDA`, `TSLA`, `AMD`, `MSFT`, `AMZN`, `META`, `GOOGL`, `PLTR`, `COIN`).
2. Permitted high-RVOL idiosyncratic breakouts ($\text{RVOL} \ge 2.20\times$) in `NEUTRAL` market regimes.
3. Activated Statistical Mean Reversion during `NEUTRAL` regimes to monetize range-bound oscillations.
4. Calibrated volume thresholds to realistic institutional participation levels.

**Note for next time**: Never stack multiple restrictive filters across universe, regime, sector, and microstructure simultaneously without empirical frequency backtesting. Multiplied filter probabilities ($P_1 \times P_2 \times P_3 \times P_4$) quickly approach zero.

## 2026-09-23: Single-Sector Starvation via Binary Sector Concentration Cap

**What did not work**: In `backend/app/core/risk.py`, the sector concentration check enforced `sector in active_sectors`, permitting only a single active position per sector. Because both `AAPL` and `NVDA` were categorized under "Technology", holding AAPL completely locked out NVDA from any trade setup, regardless of signal quality or overall portfolio risk budget.

**What worked instead**:
1. Granularized sector mappings (e.g. `NVDA` and `AMD` as "Semiconductors", `MSFT` and `PLTR` as "Software", `GOOGL` and `META` as "Communication Services").
2. Replaced the binary check with a multi-position limit: `max_positions_per_sector = 2`, while maintaining `max_concurrent_positions = 3` total.
3. Exempted Index ETFs (`SPY`, `QQQ`) from sector concentration limits.

**Note for next time**: Sector limits should accommodate multiple non-identical names up to a defined concentration ratio ($2 / 3$ max exposure) rather than a crude 1-name lockout that starves liquid setups.

## 2026-09-23: Sentiment Substring NLP False Positive Leakage

**What did not work**: In `backend/app/ingestion/sentiment.py`, `_classify_category` evaluated keywords using Python substring searches (`any(k in text for k in ...)`). Common financial words accidentally triggered unintended catalyst buckets: "sector" matched "sec" $\to$ `LEGAL_INVESTIGATION`; "approbed" matched "probe" $\to$ `LEGAL_INVESTIGATION`; "window" matched "win" $\to$ `PARTNERSHIP_CONTRACT`. This corrupted news sentiment classification and emitted erroneous signals or blocked trades.

**What worked instead**: Refactored keyword matching to use strict word-boundary regular expressions:
```python
def _has_kw(keywords: Tuple[str, ...]) -> bool:
    return any(re.search(r"\b" + re.escape(k) + r"\b", text) for k in keywords)
```
This ensures keywords only match complete words, completely eliminating substring leakage.

**Note for next time**: Never use substring matching (`k in text`) for token-based NLP categorization. Always use regex word boundaries (`\b`) or tokenized vocabularies.

## 2026-09-23: Mean Reversion Parameter Starvation Under Moderate VIX

**What did not work**: `MeanReversionStrategy` used extreme entry hurdles: $Z$-score $\ge 2.00$, volume climax multiplier $\ge 1.75\times$, and minimum wick rejection ratio $\ge 0.35$. Under moderate VIX regimes (14–16), 1-minute bars rarely reach $2.0\sigma$ with $1.75\times$ volume and $35\%$ wick rejection simultaneously, resulting in 0 valid mean reversion triggers during normal chop sessions.

**What worked instead**: Calibrated parameters to achievable intraday turning points:
- $Z$-score threshold: lowered from 2.00 to 1.65.
- Volume climax multiplier: lowered from $1.75\times$ to $1.30\times$.
- Minimum wick rejection ratio: lowered from 0.35 to 0.30.
These levels reliably capture range-bound exhaustion fades back to the 20-SMA while preserving stop-loss protection and minimum reward ratios.

**Note for next time**: Reversion hurdles must match the empirical distribution of the underlying asset's volatility regime. $2.0\sigma$ on 1-minute bars is an extreme outlier threshold that starves intraday reversion strategies.

## 2026-09-23: E2E Runner Port 8000 Audit Omission

**What did not work**: In `tests/e2e/runner.py`, `run_tests` monitored ports `[8080, 8005, 3005]`, omitting port `8000` (the standard FastAPI default port and host collision vector). If any rogue process or unconfigured backend instance spawned on port 8000 during test execution, the runner would report clean port hygiene despite port 8000 remaining bound.

**What worked instead**: Updated `ports_to_check` in `tests/e2e/runner.py` to `[8080, 8005, 8000, 3005]`, ensuring all four key ports are audited, confirmed clean, and liberated after test runs.

**Note for next time**: All test runners and hygiene scripts must audit the complete set of system and default ports, including alternative or standard ports (8000 as well as 8005).

## 2026-09-23: VIX Stop Distance Clamping Violation

**What did not work**: In `backend/app/strategies/adaptation.py`, `calculate_adapted_stop` multiplied the strategy base stop distance by VIX regime multipliers (e.g. 0.85 in Low VIX to 2.00 in Crisis VIX) without clamping the result to institutional risk bounds. When VIX reached Elevated (25–35) or Crisis (35+) levels, or on symbols with wider base stop spreads, the calculated stop distance expanded beyond 4.00% of entry price (e.g., 4.2%–5.0%). When downstream signals reached `InstitutionalRiskEngine.evaluate_order`, the risk engine strictly rejected the order with `STOP_TOO_WIDE`. In quiet regimes, it could also contract below 0.40% (`STOP_TOO_TIGHT`). This resulted in erratic strategy signal rejections during volatile market conditions when risk management is most critical.

**What worked instead**: Enforced strict institutional clamping inside `calculate_adapted_stop`:
`min_bound = 0.0040 * entry_price`
`max_bound = 0.0400 * entry_price`
`adapted_dist = max(min_bound, min(max_bound, adapted_dist))`
This guarantees that regardless of VIX regime multiplier or raw stop width, the output stop distance strictly satisfies the `[0.0040, 0.0400]` risk engine invariant before order submission.

**Note for next time**: Strategy adaptation multipliers must always be bounded by the outer risk engine's non-negotiable envelope. Never permit an internal multiplier to scale a parameter past the external validator's rejection threshold.

## 2026-09-23: News Momentum Lookahead Bias via Unbounded Lower Timestamp

**What did not work**: In `backend/app/strategies/news_momentum.py`, the `on_bar` method filtered news catalysts using `(now_ts - c.timestamp.timestamp()) <= self.catalyst_ttl_seconds`. Lacking a lower bound of zero (`0 <=`), if a news article arrived with a timestamp in the future of `now_ts` (from clock skew, asynchronous queue jitter, or forward-dated feed replay), `elapsed` was negative, trivially satisfying the inequality. This allowed future news catalysts to trigger entry signals before their chronological publication time, introducing forward lookahead bias and violating causality.

**What worked instead**: Added an explicit non-negative lower bound:
`if not (0 <= (now_ts - c.timestamp.timestamp()) <= self.catalyst_ttl_seconds): continue`
Any news item with a future timestamp ($t_{\text{news}} > t_{\text{bar}}$) or expired timestamp ($t_{\text{bar}} - t_{\text{news}} > \text{TTL}$) is strictly rejected from triggering momentum entries.

**Note for next time**: Any freshness window or TTL check of the form `delta <= TTL` must explicitly enforce `0 <= delta <= TTL`. A missing lower bound is a classic vector for lookahead bias in event-driven systems.

## 2026-09-23: Quote Stop-Fill Double Execution via Missing Loop Break

**What did not work**: In `backend/app/core/engine.py`, `process_quote` iterated over `self.working_orders.values()` to evaluate stop loss triggers. When a `STOP` or `STOP_LIMIT` order crossed the quote bid/ask and was filled via `_execute_fill(order, fill_price, bar_time, ...)` (which cancels sibling bracket exit orders asynchronously via `on_child_order_fill`), the loop did not execute a `break` statement (unlike `process_bar`). On wide or crossed quotes, or when order dictionary iteration order allowed, a sibling take-profit limit order or competing exit order could trigger and fill on the exact same price quote tick before cancellation took effect. This led to duplicate fills, over-execution, and flipped short/long exposure.

**What worked instead**: Added an immediate `break` statement after filling a `STOP` or `STOP_LIMIT` order in `process_quote`:
```python
if is_stop:
    self._execute_fill(order, fill_price, bar_time, liquidity="TAKER")
    break
```
This guarantees that once a protective stop fills, no further orders for that tick are executed, mirroring the deterministic semantics of `process_bar`.

**Note for next time**: Quote-level and bar-level order matching loops must maintain identical loop-control invariants. Stop executions are terminal state transitions for the position on that market tick.

## 2026-09-23: Manual Flatten Omission of Working Orders on Flat Positions

**What did not work**: In `backend/app/main.py`, `_execute_manual_flatten` and `manual_flatten` iterated solely over `account.positions.keys()`. If a trader or strategy had pending limit entry orders or unfulfilled bracket orders for a symbol where `account.positions[symbol]` was zero or not yet established, those working orders were completely bypassed by the flatten routine. If the market subsequently moved to touch those pending limit orders, they filled unprotected on the broker/paper book, creating new unauthorized positions after a manual flatten had been certified.

**What worked instead**: Aggregated target symbols across all three core registries:
```python
target_symbols = set(account.positions.keys())
target_symbols.update(engine.working_orders.keys())
for order in list(engine.working_orders.values()):
    if hasattr(order, "symbol"):
        target_symbols.add(order.symbol)
if bracket_manager:
    target_symbols.update(bracket_manager.symbol_to_bracket.keys())
```
The routine then cancels all working orders in `engine.working_orders` for all target symbols, purges bracket state, and liquidates any existing inventory, guaranteeing complete flat-book enforcement.

**Note for next time**: "Flatten" means zero exposure AND zero intent. Always purge the union of active inventory and working orders across the engine, bracket manager, and account.

## 2026-09-23: UI Broadcast Slow-Consumer Event Loop Starvation

**What did not work**: `broadcast_ui_state` in `backend/app/main.py` was invoked synchronously on every market quote arrival tick without rate limiting. In high-frequency quote streams (hundreds of quote updates per second across the 5-symbol watchlist), serializing and sending UI payloads to connected WebSockets consumed significant CPU and blocked the `asyncio` event loop. Furthermore, if a single browser client lagged or suffered network backpressure, `ws.send_text(raw)` stalled or blocked indefinitely, starving AlpacaRelay ingestion workers and delaying order execution fills.

**What worked instead**: Implemented dual protection in `main.py`:
1. **Rate Limiting**: Added a 4 Hz throttle (`_UI_BROADCAST_THROTTLE_SEC = 0.25`) so full UI payloads are pushed at most once every 250ms during high-frequency quote bursts.
2. **Client Timeout & Eviction**: Wrapped client transmissions in `asyncio.wait_for(ws.send_text(raw), timeout=0.35)`. If a client fails to accept the payload within 350ms, it is immediately evicted from `ui_clients` and disconnected, completely isolating the core trading engine from slow frontends.

**Note for next time**: Never link high-throughput market data processing directly to downstream consumer WebSockets. Always throttle UI broadcasts and aggressively evict slow consumers with strict timeouts.

## 2026-09-23: Inverted Mean Reversion Policy

**What did not work**: In `backend/app/core/market_filter.py`, the initial mean reversion policy blocked buying oversold dips during `BULLISH` regimes and blocked fading overbought spikes during `BEARISH` regimes, while falling through to approve selling during `BULLISH` and buying during `BEARISH`. This allowed shorting directly into morning market-wide bull rallies (catching the full brunt of systematic trend drift) and catching falling knives during market liquidations, directly causing the 2026-09-22 TSLA and AAPL stop-outs.

**What worked instead**: Inverting the policy to be macro-trend aligned (`INDEX_BETA_CONTRADICTION`). In a `BULLISH` trend, buying oversold dips ($Z \le -2.0$) is permitted because systematic upward drift works with mean reversion, while shorting overbought rallies is strictly forbidden. In a `BEARISH` trend, fading relief rallies is permitted, while buying falling knives is strictly forbidden. In `NEUTRAL`, two-way reversion is permitted.

**Note for next time**: In equity intraday microstructure, systematic market beta ($\beta \cdot \mu_{\text{mkt}}$) dominates idiosyncratic mean reversion on trend days. Never fade an idiosyncratic extreme against a confirmed market-wide trend regime.

## 2026-09-23: Temporal Lookahead via abs() on Timestamps

**What did not work**: In `MarketTrendFilter.get_current_trend`, index data freshness was evaluated with `abs((now - spy_ts).total_seconds()) > self.stale_threshold_sec`. Using `abs()` masked the sign of the time interval. When an index bar timestamp arrived from the future of `now` (`now < spy_ts`), `elapsed` was negative, but `abs(elapsed)` evaluated to a small positive number within the threshold. This allowed future index bars up to 120s ahead to be ingested as valid historical context, introducing forward data leakage and lookahead bias.

**What worked instead**: Enforcing a strict signed causal time arrow: `elapsed = (now - spy_ts).total_seconds()`. If `elapsed < 0`, the filter immediately rejects the evaluation with `MarketTrend.UNKNOWN` and reason `"FUTURE_INDEX_DATA: Index timestamp is in the future"`. Only non-negative elapsed times $\le \text{stale\_threshold\_sec}$ are accepted. Defensive `None` guards were also added to prevent `AttributeError`.

**Note for next time**: Never use `abs()` for time delta checks. Time has a physical causal direction; $t_{\text{event}} \le t_{\text{eval}}$ must always hold. Signed comparison is a fundamental causality invariant.

## 2026-09-23: Target Override Slippage Hazard

**What did not work**: In `backend/app/core/bracket.py`, when a trade signal specified explicit profit target overrides (`target_1_override`, `target_2_override`), `activate_bracket_on_fill` blindly assigned `bracket.target_1_price = round(bracket.target_1_override, 2)` without validating against the realized fill price. If adverse slippage filled a BUY order above `target_1_override`, the bracket placed a limit sell order below the entry price, becoming immediately marketable and locking in an instantaneous loss or scratching the trade.

**What worked instead**: Adding directional slippage sanity validation: for BUY orders, `target_1_override > entry_price` must hold; for SELL orders, `target_1_override < entry_price` must hold. If adverse slippage violates this boundary condition, the invalid override is discarded and the target is dynamically re-anchored to `entry_price + direction * default_target_r * r_distance`.

**Note for next time**: Pre-calculated prices from signal generation time are proposals. Once an order fills, every exit boundary must be validated against the realized execution price, never assumed to be geometrically sound.

## 2026-09-23: Target 1 Partial Fill Orphan Vulnerability

**What did not work**: In `BracketOrderManager.on_child_order_fill`, receiving ANY fill event for Target 1 unconditionally set `bracket.target_1_filled = True` without decrementing the open quantity. When a partial fill occurred (e.g., 20 of 50 shares filled), `target_1_filled` became `True`. If the price subsequently reversed and triggered the stop loss, the stop handler checked `if bracket.target_1_order_id and not bracket.target_1_filled:`, which evaluated to `False`. The remaining 30-share limit order was omitted from cancellation, remaining orphaned on the book. On market recovery, it filled unprotected, creating an unintended short position.

**What worked instead**: Implementing explicit decremental remaining quantity tracking: `bracket.target_1_qty = max(0, bracket.target_1_qty - filled_qty)`, setting `target_1_filled = True` strictly when `target_1_qty == 0`, and updating stop execution cancellation logic to cancel open targets whenever `(not bracket.target_X_filled or bracket.target_X_qty > 0)`. Added `@property target_1_remaining_qty` and `target_2_remaining_qty`.

**Note for next time**: Order state boolean flags (`is_filled`) must reflect complete order lifecycle termination. Any partial fill leaves working inventory that will orphan if cancellation logic checks only binary completion flags.

## 2026-09-20: A "float precision fix" that was really a behaviour change

**What did not work**: Clamping strategy stop distances to `[0.0042, 0.0380]` in `orb.py`,
`vwap_pullback.py` and `news_momentum.py` to stop IEEE 754 knife-edge rejections at the risk
engine's `[0.0040, 0.0400]` boundary. The clamp was 5% inside each limit; float error is ~1e-6.
The extra margin was not precision, it was a silent trading-behaviour change: a signal whose
structural stop was wider than 4.0% used to be REJECTED (no trade) and instead became a live
trade with its stop pulled inside the structure that justified it.

**What worked instead**: Two separate, correctly-sized fixes.
1. `EPS = 1e-6` tolerance in `risk.py` — this alone fixes the actual float problem.
2. `resolve_stop()` in `strategies/base.py` — widens a too-tight stop to the 0.4% floor, leaves a
   wide stop alone, and rounds the stop *away* from entry so the realised distance can never land
   a fraction under the floor.

**Note for next time**: When a fix's magnitude is far larger than the problem it names, it is a
behaviour change in disguise. Size the fix to the defect. And if a "precision fix" moves a stop,
it is an exit change and needs a backtest before it ships.

## 2026-09-20: Clearing state to make a failure go away

**What did not work**: `account.positions.clear()` in `_check_session_boundary` so that day-2
trading starts flat. It does start flat, but only in this process's memory. A position on the book
at ET rollover means the prior day's 15:55 flatten failed; there is no broker reconciliation in
this codebase, so clearing it left the broker holding shares nothing would ever close.

**What worked instead**: Liquidate at the boundary with a `SESSION_BOUNDARY_LIQUIDATION` market
order per symbol, log at ERROR, and if liquidation does not complete, leave the position on the
book so the next flatten sweep retries.

**Note for next time**: "Reset to a clean state" is only safe when the state is purely local.
When it mirrors something external (a broker position), resetting is forgetting. Fail closed.

## 2026-09-20: Tests that locked in the bug

**What did not work**: 10 E2E tests in `test_challenger_bracket_2.py` asserted the clamp contract
(`assert stop_dist == round(entry * 0.0380, 4)`). They passed, so the release read as green while
pinning the wrong behaviour. A separate new unit test for the session boundary would also have
passed against the buggy code, because asserting "the book is empty" is satisfied by both
liquidating *and* clearing.

**What worked instead**: Mutation-checking every new test — reinstate the old code and confirm the
test fails — before trusting it. Assert the mechanism (a liquidating SELL order exists), not the
end state.

**Note for next time**: A green suite proves the tests agree with the code, not that the code is
right. Run the mutation check.

## 2026-09-21: the "certified" Monday dry run does not use production wiring
- **What did not work**: Treating `scripts/run_monday_dry_run.py` as proof that a production risk-config change is safe. It builds its own `InstitutionalRiskEngine()` and `PaperTradingAccount(initial_cash=50000.00)` with library defaults, so anything set in `config.py` or wired in `main.py` is invisible to it.
- **What worked instead**: `scripts/run_integrated_monday_dry_run.py`, which imports `backend.app.main` and exercises the real wiring, plus a unit test that asserts `main.risk_engine.config` and `main.account` directly rather than `RiskEngineConfig()`.
- **Note for next time**: When a config value changes, assert the object production actually builds. A passing dry run that constructs its own engine proves nothing about the deployed configuration.

## 2026-09-21: skipping an update on stale data is fail-open, not fail-safe
- **What did not work**: Guarding against a stale VIX print by skipping the regime update. Skipping changes nothing, so whatever regime was accepted last stays in force. A LOW print read before the feed went dark would have held sizing at 1.20 for the whole session, and the longer the data was stale the longer the stale decision applied.
- **What worked instead**: On stale data, actively move to the neutral setting (`apply_stale_vix_guard()` clamps sizing to 1.00), and only ever in the tightening direction so an already-defensive regime is not loosened.
- **Note for next time**: "We ignore bad data" is not a safety property. Ask what the system keeps doing while it ignores it. Also: check the actual weekday with `date` before reasoning about which session a timestamp belongs to. I misread 2026-09-21 as a Sunday and nearly logged a frozen-feed incident that did not exist.

## 2026-09-21: both Monday dry-run scripts write the same report file
- **What did not work**: Reading `MONDAY_SIMULATION_REPORT.md` as "the" certification. `scripts/run_monday_dry_run.py` and `scripts/run_integrated_monday_dry_run.py` both publish to that one path, so whichever ran last defines the file. The two runs legitimately differ ($50,398.30 standalone vs $49,961.26 integrated) because they exercise different wiring, so the file silently changes meaning depending on run order.
- **What worked instead**: Running both and reading each script's own stdout, and treating the integrated run (production wiring) as the one that speaks for the deployed configuration.
- **Note for next time**: Check which script last wrote a shared report before quoting a number from it. Left as-is deliberately; renaming the output path risks breaking whatever else reads that filename.

## 2026-09-21: a liveness metric that counts failed attempts is not a liveness metric
- **What did not work**: Reading `feeds.vix.last_age_sec` from `/health` to judge whether VIX data was fresh. It showed 4 seconds while the actual VIX value was 380 seconds stale, because `_mark_feed_event("vix")` runs in `handle_vix_print` for every print including stale and fallback ones. It measured "the poller is breathing", not "the data is fresh".
- **What worked instead**: Reading the relay's own `/vix` `age_s` and `upstream` fields, and cross-checking `api/market-context` for the regime the bot actually derived.
- **Note for next time**: When adding a freshness metric, ask what it reads when the upstream is dead. If the answer is "the same as when it is healthy", it is a heartbeat for the wrong component. Fixed: `/health` now reports `last_poll_age_sec` (the poller) and `value_age_sec` + `stale` (the data) as separate numbers.
- **Second note**: I told the user "I have the fix" when I had only the diagnosis. Check the file before reporting a fix as written.

## 2026-09-21: fixing the obvious half of a defect proved nothing
- **What did not work**: Correcting the trailing stop's "ATR" (a single bar's high-low) and assuming that fixed the tight-stop problem. With a correct 14-bar ATR the stop still landed 0.137% from entry on the live geometry, because the real fault was that the trail ran from entry at all instead of from Target 1.
- **What worked instead**: Writing the failing test from the observed live numbers FIRST (entry $223.9502, peak $224.13, stop ending at 0.127%), then letting it stay red until the actual root cause was fixed. The test refused the partial fix.
- **Note for next time**: Anchor the test in the observed production numbers before touching code. A test written after a plausible fix tends to agree with it.


## 2026-09-24: Checkpoint restore wiped new strategy attributes
- What did not work: `__dict__.clear()` + `update(saved)` on restore. Every code change that added a strategy setting crashed that strategy in prod after the next restart, and tests never saw it because they always restore a checkpoint written by the same code.
- What worked: keep constructor params from code, restore only the rest. Test with a checkpoint that is missing a key.
- Note for next time: after any deploy, grep live Railway logs for `error on bar`. Health was green the whole time the strategies were crashing.

## 2026-09-24: Fabricated swing seed data
- What did not work: an agent-generated `daily_bars_seed.json`. Prices were invented (MU $39 vs real $1096).
- What worked: rebuild from Alpaca via relay `/data/v2/stocks/bars` (`scripts/build_daily_bars_seed.py`).
- Note for next time: spot-check any price fixture against one real quote before trusting it.
