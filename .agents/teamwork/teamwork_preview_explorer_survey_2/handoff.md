# Handoff Report: Explorer 2 (Market Data, Indicators, Signal Qualification & Calendar)

**Agent**: Explorer 2 (`teamwork_preview_explorer_survey_2`)  
**Mission**: Market Data Ingestion, Rolling Daily Indicator Calculations, Signal Qualification (16:00 ET Close vs 09:30 ET Open), and 48-Hour Earnings Calendar Architecture  
**Authoritative Documents**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md` (2026-09-23T21:24:25Z), `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_7/DISPATCH.md`  

---

## 1. Observation

### 1.1 Ingestion & Bar Processing Architecture
1. **Stock WebSocket Ingestion (`backend/app/ingestion/stock_ws.py`)**:
   - `StockWebSocketClient` connects to `wss://alpacarelay-production.up.railway.app/v2/stocks` (or mock relay at `ws://127.0.0.1:8080`) and subscribes to channels:
     ```python
     # lines 196-202
     if settings.SUBSCRIBE_BARS:
         cmd["bars"] = sym_list
     if settings.SUBSCRIBE_QUOTES:
         cmd["quotes"] = sym_list
     if settings.SUBSCRIBE_TRADES:
         cmd["trades"] = sym_list
     ```
   - Inbound `b` frames represent **1-minute aggregate bars** (parsed into `BarEvent` at lines 254–257).
   - There is currently **zero support** for receiving or storing daily OHLCV bars over the WebSocket.
2. **Historical Bar Endpoints in Mock Relay (`backend/app/replay/mock_relay.py`)**:
   - `MockAlpacaRelayServer` implements a REST endpoint:
     ```python
     # lines 193-205
     # REST GET /data/v2/stocks/{symbol}/bars
     if path.startswith("/data/v2/stocks/"):
         ...
         symbol = parts[4].upper() if len(parts) >= 5 else ""
         bars = self.historical_bars.get(symbol, [])
         payload = {"bars": bars, "symbol": symbol, "next_page_token": None}
         return http.HTTPStatus.OK, [("Content-Type", "application/json")], json.dumps(payload).encode("utf-8")
     ```
   - In production, `RELAY_HTTP_URL` points to `https://alpacarelay-production.up.railway.app`, but currently only `VixClient` makes HTTP requests (`GET /vix`). There is no client module in `backend/app/ingestion/` that fetches historical daily bars.
3. **Symbol Rosters (`backend/app/config.py`)**:
   - `WATCHLIST_SYMBOLS`:
     ```python
     # line 60
     WATCHLIST_SYMBOLS: List[str] = Field(
         default=["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"],
         description="Default symbol roster for stock market data subscriptions"
     )
     ```
   - The 5 certified swing stocks are: `LRCX`, `KLAC`, `MU`, `AMD`, `GS` (plus `QQQ` as relative strength benchmark).
   - Notice: Only `AMD` and `QQQ` are currently in `WATCHLIST_SYMBOLS`. `LRCX`, `KLAC`, `MU`, and `GS` are not subscribed.
4. **Sector Mappings & Risk Engine Concurrency (`backend/app/core/risk.py`)**:
   - `InstitutionalRiskEngine` tracks sector allocations:
     ```python
     # lines 65-78
     self.symbol_sectors: Dict[str, str] = {
         "SPY": "Index", "QQQ": "Index", "AAPL": "Technology",
         "NVDA": "Semiconductors", "AMD": "Semiconductors",
         "MSFT": "Software", "PLTR": "Software",
         "TSLA": "Consumer Discretionary", "AMZN": "Consumer Discretionary",
         "GOOGL": "Communication Services", "META": "Communication Services",
         "COIN": "Fintech/Crypto",
     }
     ```
   - Sector parameters in `RiskEngineConfig`: `max_positions_per_sector = 2`, `max_concurrent_positions = 3`.
   - `LRCX`, `KLAC`, `MU`, and `AMD` are all in the **Semiconductors** sector; `GS` is in **Financials**. If swing trading shared the intraday sector limit without exemption, holding `LRCX` and `KLAC` would lock out `MU` or `AMD`.
5. **Flattening Engine & Session Boundaries (`backend/app/core/flattening.py` & `backend/app/main.py`)**:
   - `ZeroOvernightFlatteningEngine` enforces a 4-phase schedule:
     - 15:45: Phase 1 Entry Lockout
     - 15:50: Phase 2 Working Order Purge (`cancel_all_orders` except protective stops)
     - 15:55: Phase 3 Mandatory Market Liquidation (`liquidate_all_positions`)
     - 15:58: Phase 4 Zero-Overnight Audit (`run_audit` checking open positions == 0)
     - 16:00: Phase `MARKET_CLOSED`:
       ```python
       # flattening.py lines 152-162
       elif t >= self.schedule.market_close_time:
           if self.current_phase != FlatteningPhase.MARKET_CLOSED:
               self.current_phase = FlatteningPhase.MARKET_CLOSED
               return FlatteningDirective(
                   phase=FlatteningPhase.MARKET_CLOSED,
                   timestamp=now_dt,
                   action_required="SESSION_CLOSED",
                   lock_new_entries=True,
                   cancel_all_orders=True,
               )
       ```
   - In `backend/app/main.py`:
     ```python
     # lines 769-792 (_check_session_boundary)
     if engine.working_orders:
         log.warning("Session boundary detected with %d open working orders; cancelling all", len(engine.working_orders))
         engine.cancel_all_orders("SESSION_BOUNDARY_PURGE")
         _release_dead_entry_brackets()
     if account.positions:
         log.error("Session boundary with %d open position(s); prior-day flatten failed. Liquidating...", len(account.positions))
         for sym, pos in list(account.positions.items()):
             ...
             engine.submit_order(liq_order.id)
             _reconcile_fills(_flatten_symbol(sym, pos.market_price, now_dt))
     ```
   - Direct implication: Any swing position held overnight will be liquidated at the next session boundary unless explicitly tagged and excluded from `_check_session_boundary`, `handle_flattening_directive`, and `execute_phase_4_audit`!
6. **Existing Technical Indicators (`backend/app/strategies/base.py`)**:
   - `calculate_sma(prices: List[float], period: int) -> float`: Simple moving average.
   - `calculate_rsi(prices: List[float], period: int = 14) -> float`: Wilder's smoothed RSI.
   - `calculate_atr(bars: List[Union[BarEvent, Dict]], period: int = 14) -> float`: Wilder's 14-period ATR.
   - `resolve_stop(entry_price, raw_dist, is_long)`: Imposes `MIN_STOP_DISTANCE_PCT = 0.0040` (0.4%) and is paired with `InstitutionalRiskEngine.max_stop_distance_pct = 0.0400` (4.0%). A 2.5x Daily ATR stop (~6% to 9% for high-beta stocks) would trigger a knife-edge rejection in `InstitutionalRiskEngine` if routed through the intraday validator.
7. **Persistence Schema (`backend/app/core/persistence.py`)**:
   - SQLite tables: `schema_info`, `runtime_checkpoint`, `completed_trades`, `session_summaries`, `processed_events`.
   - No table currently exists for `daily_bars` or `earnings_calendar`.

---

## 2. Logic Chain

### 2.1 Lookahead Bias Elimination in Indicator Math
Every quantitative indicator for the swing strategy must satisfy strict causality:
- **Causality Criterion**: An indicator computed at timestamp $T$ may only use market data from bars that closed at or before $T$.
- **Session Boundary Invariant**:
  - The regular trading session runs from 09:30:00 to 16:00:00 ET.
  - The final 1-minute bar of the regular session is the 15:59:00 bar, which concludes and prints at 16:00:00 ET.
  - Before 16:00:00 ET, today's daily bar is **unclosed and in-flight**. Computing 200 SMA, 60d RS, or RSI(2) on unclosed daily data during market hours causes **repainting and false signal leakage**.
  - Therefore, signal qualification for swing trades must execute strictly **at 16:00:05 ET** after today's final daily bar $[O, H, L, C, V]$ is committed.
  - Execution occurs at **next Market Open (09:30:00 ET)** on day $t+1$. Tomorrow's open price $P_{\text{open}, t+1}$ is completely unknown at 16:00 ET on day $t$. Staged orders must stage sizing in notional dollars ($25,000) and convert to exact share quantity only when $P_{\text{open}, t+1}$ is established.

### 2.2 Mathematical Specifications of the 5 Rolling Daily Indicators

#### A. 200-day Simple Moving Average (Rule 1: Macro Floor)
- **Mathematical Formula**:
  $$\text{SMA}_{200}(t) = \frac{1}{200} \sum_{i=0}^{199} \text{Close}_{t-i}$$
- **Rule 1 Condition**:
  $$\text{Close}_{t} > \text{SMA}_{200}(t)$$
- **Zero-Lookahead Guarantee**:
  - Calculated on daily closing prices up to day $t$ (today's finalized close).
  - Minimum warm-up history: 200 closed daily sessions. If history has $< 200$ bars, signal is rejected.
  - Strict inequality: must be strictly greater than (`>`), not `>=`.

#### B. 60-day Relative Strength vs QQQ (Rule 2: Market Leadership)
- **Mathematical Formula**:
  $$\Delta_{\text{stock}, 60d} = \frac{\text{Close}_{\text{stock}, t} - \text{Close}_{\text{stock}, t-60}}{\text{Close}_{\text{stock}, t-60}}$$
  $$\Delta_{\text{QQQ}, 60d} = \frac{\text{Close}_{\text{QQQ}, t} - \text{Close}_{\text{QQQ}, t-60}}{\text{Close}_{\text{QQQ}, t-60}}$$
- **Rule 2 Condition**:
  $$\Delta_{\text{stock}, 60d} \ge \Delta_{\text{QQQ}, 60d}$$
- **Zero-Lookahead Guarantee**:
  - Compares the stock's closing price change over the exact same 60 completed trading sessions as `QQQ`.
  - Date alignment: Must align the session dates of the stock and `QQQ` so that day $t-60$ corresponds to the same calendar trading day (preventing offset desynchronization if a ticker experienced a trading halt).
  - Minimum warm-up history: 61 closed daily sessions ($t-60$ through $t$).

#### C. 2-day Connors RSI (Rule 3: Panic Trigger & Rule 7b Exit)
- **Mathematical Formula (Wilder's 2-period RSI on Daily Closes)**:
  Let $C_0, C_1, \dots, C_M$ be the historical sequence of daily closes ($M \ge 20$).
  For each session $k \ge 1$:
  $$\Delta_k = C_k - C_{k-1}, \quad U_k = \max(0, \Delta_k), \quad D_k = \max(0, -\Delta_k)$$
  For period $N = 2$:
  $$\text{AvgGain}_2 = \frac{U_1 + U_2}{2}, \quad \text{AvgLoss}_2 = \frac{D_1 + D_2}{2}$$
  For each subsequent session $k > 2$:
  $$\text{AvgGain}_k = \frac{\text{AvgGain}_{k-1} \times (2 - 1) + U_k}{2} = \frac{\text{AvgGain}_{k-1} + U_k}{2}$$
  $$\text{AvgLoss}_k = \frac{\text{AvgLoss}_{k-1} \times (2 - 1) + D_k}{2} = \frac{\text{AvgLoss}_{k-1} + D_k}{2}$$
  $$\text{RS}_k = \begin{cases} \frac{\text{AvgGain}_k}{\text{AvgLoss}_k} & \text{if } \text{AvgLoss}_k > 0 \\ \infty & \text{if } \text{AvgLoss}_k = 0 \text{ and } \text{AvgGain}_k > 0 \\ 1.0 & \text{if } \text{AvgLoss}_k = 0 \text{ and } \text{AvgGain}_k = 0 \end{cases}$$
  $$\text{RSI}(2)_k = 100 - \frac{100}{1 + \text{RS}_k}$$
- **Panic Dip Condition (Rule 3)**:
  $$\text{RSI}(2)_t < 10.0$$
- **Overbought Exit Condition (Rule 7b)**:
  $$\text{RSI}(2)_t > 70.0$$
- **Zero-Lookahead Guarantee**:
  - Computed strictly on daily closes up to session $t$.
  - Note on Connors RSI terminology: In quantitative literature, Larry Connors introduced the "RSI(2) 2-Day Panic Dip" (utilizing 2-period Wilder's RSI on daily closes). Later in 2012, Connors Research formulated the 3-component composite `ConnorsRSI(3, 2, 100) = [RSI(Close, 3) + RSI(Streak, 2) + PercentRank(100)] / 3`. The prompt explicitly specifies:
    - `"2-day Connors RSI (RSI(2)) must close below 10.0"`
    - `"Prior daily RSI(2) crosses above 70.0"`
    The standard Wilder's $\text{RSI}(C, 2)$ precisely matches the $10.0$ panic threshold and $70.0$ exit threshold specified in the strategy rules.

#### D. 14-day Daily ATR for Emergency Stops (Rule 6: 2.5x ATR)
- **Mathematical Formula**:
  For daily bar $k$ with High $H_k$, Low $L_k$, Close $C_k$, and prior close $C_{k-1}$:
  $$\text{TR}_k = \max(H_k - L_k, |H_k - C_{k-1}|, |L_k - C_{k-1}|)$$
  Wilder's 14-period smoothing:
  $$\text{ATR}_{14}(t) = \frac{\text{ATR}_{14}(t-1) \times 13 + \text{TR}_t}{14}$$
- **Rule 6 Stop Price**:
  When filled at 09:30 ET on day $t+1$ at fill price $P_{\text{fill}}$:
  $$\text{StopPrice} = P_{\text{fill}} - 2.5 \times \text{Daily ATR}_{14}(t)$$
  $$\text{StopDistance} = 2.5 \times \text{Daily ATR}_{14}(t)$$
- **Zero-Lookahead Guarantee**:
  - Uses $\text{Daily ATR}_{14}(t)$ from session $t$ (the close at which the signal qualified).
  - On the morning of day $t+1$, day $t+1$'s daily ATR does not exist. Using session $t$'s ATR provides a fixed, pre-computed stop distance that attaches immediately at fill.

#### E. 5-day SMA Exit (Rule 7a: Take-Profit)
- **Mathematical Formula**:
  $$\text{SMA}_5(t) = \frac{1}{5} \sum_{i=0}^4 \text{Close}_{t-i}$$
- **Rule 7a Condition**:
  $$\text{Close}_t > \text{SMA}_5(t)$$
- **Execution**:
  - When today's close crosses above the 5-day SMA, a `SELL` order is staged for execution at **next Market Open (09:30:00 ET)**.

---

### 2.3 Historical Daily Bar Data Pipeline, Caching & Updates

#### A. Architecture Overview
```
┌────────────────────────────────────────────────────────────────────────┐
│                   Historical Daily Bar Pipeline                        │
├───────────────────────────────────┬────────────────────────────────────┤
│  Layer 1: Static Seed Fixture     │  Layer 2: Durable SQLite Table     │
│  `data/daily_bars_seed.json`      │  `trading_state.sqlite3`           │
│  (250+ bars: LRCX,KLAC,MU,AMD,GS, │  Table: `daily_bars`               │
│   QQQ)                            │  (upserted on load & session close)│
├───────────────────────────────────┴────────────────────────────────────┤
│  Layer 3: Intraday 1-Minute Bar Aggregator (`DailyBarAggregator`)      │
│  - Ingests 1m bars from StockWebSocketClient (09:30-16:00 ET)          │
│  - Tracks: O=first_open, H=max(high), L=min(low), C=last_close, V=sum  │
│  - At 16:00:00 ET: Commits finalized daily bar to Layer 2 & memory     │
├────────────────────────────────────────────────────────────────────────┤
│  Layer 4: REST Reconnection / Backfill (Graceful Fallback)             │
│  - Query: GET /data/v2/stocks/{symbol}/bars?timeframe=1Day&limit=250   │
│  - If network or API unavailable -> use local SQLite/Seed seamlessly   │
└────────────────────────────────────────────────────────────────────────┘
```

#### B. Data Schema for `daily_bars` in SQLite (`backend/app/core/persistence.py`)
```sql
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,           -- Format: YYYY-MM-DD
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    finalized INTEGER NOT NULL DEFAULT 1, -- 1 = finalized regular session close
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_bars_sym_date ON daily_bars(symbol, date);
```

#### C. Interaction with `WATCHLIST_SYMBOLS`
- Current `config.py` `WATCHLIST_SYMBOLS`: `["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]`.
- Swing stocks: `["LRCX", "KLAC", "MU", "AMD", "GS"]` + `QQQ`.
- **Architectural Solution**:
  1. Add `SWING_SYMBOLS: List[str] = ["LRCX", "KLAC", "MU", "AMD", "GS"]` and `SWING_BENCHMARK: str = "QQQ"` in `backend/app/config.py`.
  2. In `StockWebSocketClient`, subscribe to the union:
     $$\text{SubscribedSymbols} = \text{set}(\text{WATCHLIST\_SYMBOLS}) \cup \text{set}(\text{SWING\_SYMBOLS}) \cup \{\text{SWING\_BENCHMARK}\}$$
     Total: 16 symbols (`SPY`, `QQQ`, `AAPL`, `NVDA`, `TSLA`, `AMD`, `MSFT`, `AMZN`, `META`, `GOOGL`, `PLTR`, `COIN`, `LRCX`, `KLAC`, `MU`, `GS`).
  3. **Intraday Strategy Guard**:
     In `handle_bar_event()` in `backend/app/main.py`:
     - If `bar.symbol in settings.WATCHLIST_SYMBOLS`: route to intraday strategies (`orb`, `vwap_pullback`, `news_momentum`, `mean_reversion`).
     - Always route bars of `SWING_SYMBOLS + [SWING_BENCHMARK]` to `DailyBarAggregator` to maintain the running daily candle and live swing position valuation.
     This strictly prevents intraday day-trading strategies from generating accidental intraday trades on `LRCX`, `KLAC`, `MU`, or `GS`.

---

### 2.4 Scheduling & Signal Lifecycle (16:00 ET Qualification vs 09:30 ET Execution)

```
        Day t (16:00 ET Close)                     Day t+1 (09:30 ET Open)
 ────────────────────────────────────       ─────────────────────────────────────
 1. 15:58 ET: Intraday Flatten ends.        1. 09:30:00 ET: Market Open arrives.
    Intraday book is 100% FLAT.             2. Execute Staged Exits FIRST:
 2. 16:00:00 ET: Session Closes.               - Sell shares at P_open.
    - Finalize daily bar for Day t.            - Cash released to buying power.
    - Commit bar to SQLite cache.           3. Execute Staged Entries:
 3. 16:00:05 ET: Swing Signal Evaluation:      - Calculate Qty = floor($25,000 / P_open).
    a) Check Active Swing Positions:           - Submit Market Buy (strategy: "swing_panic_dip").
       - Close > 5 SMA?                        - Fill at P_open.
       - RSI(2) > 70.0?                        - Attach hard Stop: P_open - 2.5 * Daily_ATR.
       - Holding Day >= 5?                  4. Intraday Protection:
       - Earnings tomorrow?                    - Stop monitored continuously intraday.
       -> Stage SELL for 09:30 ET.             - Swing positions EXEMPT from 15:58 flatten.
    b) Check Candidate Entries:
       - Close > 200 SMA?
       - 60d RS >= QQQ?
       - RSI(2) < 10.0?
       - No earnings in next 48 hours?
       - Active swing positions < 2?
       -> Stage BUY for 09:30 ET ($25k).
 4. Store staged orders in durable
    `swing_staged_orders` queue.
```

#### Why Staged Orders Must NOT Enter `engine.working_orders` at 16:00 ET
As observed in `backend/app/core/flattening.py` (line 161) and `backend/app/main.py` (line 771):
- At 16:00:00 ET, `FlatteningDirective(phase=MARKET_CLOSED, cancel_all_orders=True)` purges working orders.
- At midnight / session boundary, `_check_session_boundary()` executes `engine.cancel_all_orders("SESSION_BOUNDARY_PURGE")`.
- If staged swing orders were submitted to `ExecutionEngine.working_orders` at 16:00 ET, they would be immediately cancelled!
- **Solution**: Manage staged swing orders in a dedicated, durable `SwingStagedOrderManager`. The staged orders reside safely outside `working_orders` overnight and are dispatched to `ExecutionEngine` at 09:30:00 ET market open.

---

### 2.5 48-Hour Earnings Calendar Lookup With Graceful Fallback

#### A. Quantitative Rule 4 Requirements
- **Rule 4a (Entry Veto)**: If the company reports earnings within 48 hours of the scheduled entry, do not enter.
  - Since entry occurs at 09:30 ET on Day $t+1$, any earnings announcement between Day $t+1$ and Day $t+3$ falls within the blackout window.
- **Rule 4b (Holding Exit)**: If holding an active position and the company reports earnings tomorrow (Day $t+1$), stage a `SELL` order to exit at Market Open (09:30 ET) on Day $t+1$.

#### B. Calendar Architecture & Tiered Fallback
1. **Tier 1: Bundled Curated Calendar (`backend/app/data/earnings_calendar.json`)**:
   - Seeded with verified upcoming earnings announcement dates and report times (`bmo` = before market open, `amc` = after market close) for `LRCX`, `KLAC`, `MU`, `AMD`, and `GS`.
   - Never fails; requires zero network connectivity; guarantees 100% deterministic testing and dry-run execution.
2. **Tier 2: SQLite Cached Calendar (`earnings_calendar` table)**:
   ```sql
   CREATE TABLE IF NOT EXISTS earnings_calendar (
       symbol TEXT NOT NULL,
       report_date TEXT NOT NULL,      -- YYYY-MM-DD
       report_time TEXT NOT NULL,      -- 'bmo', 'amc', or 'unknown'
       confirmed INTEGER NOT NULL DEFAULT 1,
       updated_at TEXT NOT NULL,
       PRIMARY KEY (symbol, report_date)
   );
   ```
3. **Tier 3: Remote Provider Sync (Async Background Refresh)**:
   - On startup and once daily during pre-market, `EarningsCalendarService` attempts to refresh earnings dates from Alpaca / Finnhub / FMP if API credentials or endpoints are available.
   - **Timeout and Exception Isolation**: Any HTTP timeout (e.g. 3.0s) or error is swallowed, logged as `WARNING`, and the service immediately falls back to Tier 2/Tier 1 cache.
   - **Safe-Side Invariant**: If an earnings date cannot be verified but is present in the cache within 48 hours, the trade is vetoed.

---

### 2.6 Flattening Exemption & Account Capital Allocation (R2)

#### A. Flattening Exemption Mechanics
1. **Order & Position Tagging**:
   - Every swing order is created with `strategy_id="swing_panic_dip"` and `bracket_role=None` (or `BracketRole.SWING_ENTRY` / `BracketRole.SWING_STOP`).
   - Every position held by the swing engine is tracked with `position_type="SWING"` (or verified via `pos.symbol in swing_engine.active_positions`).
2. **Exemption in `backend/app/core/flattening.py` & `main.py`**:
   - **Phase 2 (15:50 ET Order Purge)**: Filter out swing protective stops:
     ```python
     if order.strategy_id == "swing_panic_dip":
         continue  # Do not cancel swing emergency stops
     ```
   - **Phase 3 (15:55 ET Mandatory Liquidation)**: Filter out swing positions:
     ```python
     if pos.symbol in swing_engine.active_positions:
         continue  # Do not liquidate swing positions
     ```
   - **Phase 4 (15:58 ET Zero Audit)**: Audit passes if all **intraday** positions are zero:
     ```python
     intraday_positions = {sym: p for sym, p in positions.items() if sym not in swing_engine.active_positions}
     assert len(intraday_positions) == 0
     ```
   - **Session Boundary (`_check_session_boundary`)**:
     Do not cancel swing stops or liquidate swing positions across the midnight boundary.

#### B. Capital & Buying Power Allocation
- Account Cash Pool: $50,000.
- Swing Allocation: $25,000 notional per position; max 2 concurrent positions = $50,000 maximum commitment.
- Available intraday buying power is dynamically computed:
  $$\text{SwingCommittedCapital} = \sum_{\text{pos} \in \text{SwingPositions}} \text{pos.cost\_basis} + \sum_{\text{ord} \in \text{StagedEntries}} \text{ord.target\_notional}$$
  $$\text{EffectiveIntradayCash} = \max(0, \text{AccountCash} - \text{SwingCommittedCapital})$$
  $$\text{IntradayDTBP} = 4.0 \times \text{EffectiveIntradayCash}$$
  This strictly prevents double-spending or margin collisions between intraday and swing operations.

---

## 3. Caveats

1. **Market Holidays & Partial Sessions**:
   - On market half-days (e.g. 13:00 ET close on Black Friday / Christmas Eve), the close qualification must trigger at the adjusted close time, not 16:00 ET.
2. **Weekend & Holiday Holding Days**:
   - Rule 7c specifies: "held for 5 trading days (time stop)".
   - Holding days must increment only on **trading days** (Monday–Friday excluding NYSE holidays), never on calendar days.
3. **High-Priced Equities & Fractional Shares**:
   - A stock such as `LRCX` (trading around $800–$900) will yield $\lfloor 25000 / 850 \rfloor = 29$ shares ($24,650 notional). Sizing uses integer shares (`math.floor(25000.0 / open_price)`); the unallocated cash ($350) remains in cash reserves.
4. **Earnings Announcement Shifts**:
   - Companies occasionally reschedule earnings releases. The 48-hour buffer provides an extra session of protection against unexpected date adjustments.

---

## 4. Conclusion & Concrete Architectural Blueprints

### 4.1 Recommended New Module Layout

| File Path | Component | Responsibility |
|---|---|---|
| `backend/app/core/daily_bars.py` | `DailyBarManager`, `DailyBarAggregator` | Loads historical daily bars from SQLite/Seed, aggregates live 1m bars into today's daily bar, calculates rolling indicators (200 SMA, 60d RS, RSI(2), 14d ATR, 5d SMA) with zero lookahead bias. |
| `backend/app/core/earnings_calendar.py` | `EarningsCalendarService` | Manages 48-hour earnings blackout window and next-day earnings exit checks with SQLite and JSON seed fallback. |
| `backend/app/strategies/swing_panic_dip.py` | `SwingPanicDipEngine` | Executes the 7 quantitative rules across `LRCX`, `KLAC`, `MU`, `AMD`, `GS`: qualifies signals at 16:00 ET close, stages entries/exits, manages holding day counters and 2.5x ATR stops. |
| `backend/app/data/daily_bars_seed.json` | Seed Fixture | Bundled 250-day historical daily bars for `LRCX`, `KLAC`, `MU`, `AMD`, `GS`, `QQQ`. |
| `backend/app/data/earnings_calendar.json` | Calendar Seed | Bundled confirmed earnings schedules for the 5 certified swing stocks. |

### 4.2 Class Signatures & Data Models

#### `backend/app/core/daily_bars.py`
```python
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

@dataclass(frozen=True)
class DailyBar:
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    finalized: bool = True

class DailyBarManager:
    """Manages daily bar history and lookahead-free rolling indicators."""
    def __init__(self, db_path: Optional[str] = None): ...
    def load_seed_data(self, seed_path: str) -> None: ...
    def append_bar(self, bar: DailyBar) -> None: ...
    def get_bars(self, symbol: str, lookback: int = 250) -> List[DailyBar]: ...
    def calculate_sma(self, symbol: str, period: int = 200) -> float: ...
    def calculate_relative_strength_vs_qqq(self, symbol: str, benchmark: str = "QQQ", period: int = 60) -> Tuple[float, float, bool]: ...
    def calculate_rsi2(self, symbol: str) -> float: ...
    def calculate_atr14(self, symbol: str) -> float: ...

class DailyBarAggregator:
    """Aggregates intraday 1-minute bars into a finalized daily bar at 16:00 ET."""
    def on_minute_bar(self, bar: BarEvent) -> None: ...
    def finalize_daily_bar(self, symbol: str, session_date: date) -> DailyBar: ...
```

#### `backend/app/core/earnings_calendar.py`
```python
class EarningsCalendarService:
    def is_blackout_active(self, symbol: str, current_date: date, horizon_hours: int = 48) -> bool: ...
    def has_earnings_tomorrow(self, symbol: str, current_date: date) -> bool: ...
    def refresh_from_provider(self) -> None: ...
```

#### `backend/app/strategies/swing_panic_dip.py`
```python
@dataclass
class StagedSwingOrder:
    symbol: str
    action: str                       # "BUY_ENTRY" or "SELL_EXIT"
    target_notional: float            # $25,000 for entries
    shares: Optional[int]             # Specified for exits, computed at open for entries
    daily_atr_14: float               # Stored for stop calculation
    signal_date: date
    reason: str

@dataclass
class ActiveSwingPosition:
    symbol: str
    shares: int
    entry_price: float
    entry_date: date
    daily_atr_at_entry: float
    emergency_stop_price: float       # entry_price - 2.5 * daily_atr
    holding_days: int                 # 1 to 5
    highest_price_seen: float

class SwingPanicDipEngine:
    def on_market_close(self, session_date: date) -> List[StagedSwingOrder]: ...
    def on_market_open(self, open_prices: Dict[str, float]) -> List[OrderEvent]: ...
    def on_intraday_tick(self, symbol: str, current_price: float) -> Optional[OrderEvent]: ... # Stop check
```

---

## 5. Verification Method

1. **Backend Unit & Indicator Verification**:
   - Run backend pytest suite:
     ```bash
     pytest backend/tests -q
     ```
     *(Audited: currently passes 355/355 tests in 4.21s).*
2. **Dedicated Indicator & Causal Mathematics Test**:
   - Create `backend/tests/unit/test_swing_indicators.py` verifying:
     - `calculate_sma(period=200)` returns exact average across 200 bars.
     - `calculate_rsi(period=2)` matches Wilder's smoothed formula and triggers below 10.0 on 2-day panic drops.
     - `calculate_relative_strength(period=60)` correctly flags stocks outperforming `QQQ`.
     - `calculate_atr(period=14)` computes true range correctly and establishes $2.5 \times \text{ATR}$ emergency stop.
3. **Causality & Lookahead Bias Mutation Test**:
   - Assert that evaluating indicators with Day $t+1$ bars removed produces identical values to evaluation at Day $t$ close.
   - Assert that modifying tomorrow's open price has zero effect on today's 16:00 ET qualification signal.
4. **Port Hygiene Audit**:
   - Run `./scripts/verify_port_hygiene.sh` confirming ports 3005, 8000, 8005, 8080 remain completely free.
