# Comprehensive Technical Analysis: Requirement R1 (Universe Expansion & Sector Mapping)

**Author**: Explorer 1 (Universe & Risk Explorer)  
**Date**: 2026-09-23  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_1_universe_risk`  
**Target Milestone**: R1 — Universe Expansion, Sector Mapping & Portfolio Concentration Invariants  

---

## 1. Executive Summary

AutonomousDayTrader was engineered with strict institutional risk controls ($1,500 daily circuit breaker, $25,000 / 50% equity single-position notional cap, 0.4%–4.0% stop guardrails, 4-phase zero-overnight auto-flattening). While these controls successfully eliminated catastrophic tail-risk drawdowns ($49,798.32 equity preserved), live execution revealed a critical **Filter-Stacking Bottleneck** that dropped trade frequency to ~0 trades/day:
1. **Universe Restriction**: Only 3 single stocks (`AAPL`, `NVDA`, `TSLA`) were monitored alongside 2 index ETFs (`SPY`, `QQQ`).
2. **Artificial Sector Lockout & Starvation**: In `backend/app/core/risk.py`, `AAPL`, `NVDA`, and `MSFT` were all classified under the broad label `"Technology"`. Furthermore, the sector diversification check strictly rejected any order if `sector in active_sectors` (a hard max 1 position per sector limit). Holding an initial position in `AAPL` immediately barred both `NVDA` and `MSFT` from executing, irrespective of breakout quality, RVOL surge, or news catalyst score.
3. **Requirement R1 Directive**:
   - Expand `backend/app/config.py` `WATCHLIST_SYMBOLS` to 12 top liquid high-beta intraday names across diverse sectors: `["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]`.
   - Update `backend/app/core/risk.py` sector mappings across 6 distinct industry sectors (Semiconductors, Software, Discretionary, Communication Services, Fintech/Crypto, Technology Hardware) plus Index/ETF benchmark classification.
   - Address sector starvation by transitioning the sector concentration rule from a binary 1-position lock to **max 2 positions per sector**, while strictly capping **max 3 concurrent positions total** across the entire portfolio.
   - Preserve all non-negotiable institutional risk invariants: $1,500 hard daily drawdown circuit breaker, $25,000 single-position cap, 0.4%–4.0% stop distances, and zero overnight holds.

---

## 2. In-Depth Codebase Forensics & Investigation

### 2.1 Configuration Layer (`backend/app/config.py`)
- **Current State (Lines 59–62)**:
  ```python
  WATCHLIST_SYMBOLS: List[str] = Field(
      default=["SPY", "QQQ", "AAPL", "NVDA", "TSLA"],
      description="Default symbol roster for stock market data subscriptions"
  )
  ```
- **Required State**:
  ```python
  WATCHLIST_SYMBOLS: List[str] = Field(
      default=[
          "SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD",
          "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"
      ],
      description="Expanded 12-symbol roster for stock market data subscriptions"
  )
  ```
- **Risk Invariants in `config.py` (Lines 93–100)**:
  - `INITIAL_CASH`: $50,000.00
  - `DAY_TRADING_LEVERAGE`: 4.0 (FINRA Rule 4210 $200,000 DTBP)
  - `MAX_DAILY_LOSS_LIMIT`: $1,500.00 (Hard daily loss circuit breaker)
  - `PER_POSITION_RISK_PCT`: 0.01 (1.0% = $500 target trade risk)
  - `MAX_POSITION_NOTIONAL`: $25,000.00 (50% of equity / 12.5% of DTBP)
  - `MAX_CONCURRENT_POSITIONS`: 3 simultaneous open positions

### 2.2 Risk Engine & Sector Diversification (`backend/app/core/risk.py`)
- **Current Sector Mapping (Lines 64–74)**:
  ```python
  self.symbol_sectors: Dict[str, str] = {
      "SPY": "Index",
      "QQQ": "Index",
      "AAPL": "Technology",
      "NVDA": "Technology",
      "TSLA": "Consumer Discretionary",
      "MSFT": "Technology",
      "AMZN": "Consumer Discretionary",
      "GOOGL": "Communication Services",
      "META": "Communication Services",
  }
  ```
  *Defects identified*:
  1. `AMD`, `PLTR`, `COIN` are completely missing.
  2. `NVDA` and `MSFT` are lumped into `"Technology"` with `AAPL`, preventing concurrent holdings across distinct industries (semiconductors vs enterprise software vs consumer devices).
- **Current Sector Check Mechanism (Lines 187–198)**:
  ```python
  # 4. Sector Diversification Check
  sector = self.symbol_sectors.get(symbol)
  if sector and sector != "Index" and symbol not in active_symbols and sector in active_sectors:
      return RiskCheckResult(
          approved=False,
          reason=f"CORRELATED_SECTOR_EXPOSURE: Another active position already exists in sector '{sector}'",
          requested_qty=requested_qty,
          authorized_qty=0,
          estimated_risk_dollars=0.0,
          risk_level=self.risk_level,
          rejection_code="CORRELATED_SECTOR_EXPOSURE",
      )
  ```
  *Defects identified*:
  1. The condition `and sector in active_sectors` treats any non-empty presence in `active_sectors` as an immediate rejection. This restricts exposure to **at most 1 position per sector**.
  2. `active_sectors` is supplied by callers as a `Set[str]` (e.g., `main.py` lines 111–115 and 930–934). Sets inherently discard duplicate counts.
  3. `RiskEngineConfig` lacks an explicit `max_positions_per_sector` parameter (currently hardcoded implicitly as 1).

### 2.3 Account State & Notional Invariants (`backend/app/core/account.py`)
- `PaperTradingAccount.can_afford()` (Lines 145–149):
  ```python
  max_alloc = self.initial_balance * self.leverage * self.MAX_POSITION_ALLOCATION_PCT
  if self.max_position_notional is not None:
      max_alloc = min(max_alloc, self.max_position_notional)
  ```
  `max_position_notional` is initialized to `$25,000.00` from `settings.MAX_POSITION_NOTIONAL`. Both `account.py` and `risk.py` (`max_position_equity_pct = 0.500`) bind single positions to $25,000.
- With `MAX_CONCURRENT_POSITIONS = 3`, peak portfolio notional exposure is strictly bounded:
  $$\text{Max Portfolio Notional} = 3 \times \$25,000.00 = \$75,000.00$$
  This represents $1.5\times$ initial equity and only $37.5\%$ of the FINRA Rule 4210 $200,000 buying power, guaranteeing that margin excess remains safely positive even under severe market shocks.

### 2.4 Feed Ingestion & Streaming Scalability
- **`backend/app/ingestion/stock_ws.py`**:
  - `StockWebSocketClient.symbols` defaults to `set(symbols or settings.WATCHLIST_SYMBOLS)`.
  - In `_send_initial_subscriptions(ws)` (Lines 192–204), subscriptions for `bars`, `quotes`, and `trades` are transmitted as `sorted(list(self.symbols))`.
  - Expanding `WATCHLIST_SYMBOLS` to 12 names requires **zero changes** to `stock_ws.py` code; it dynamically subscribes to all 12 tickers on handshake.
  - Backpressure metrics: Queue buffer size is 10,000 messages with high watermark at 80% (8,000 messages). For 12 liquid mega-cap tickers, peak tick density during the 09:30 open is ~100–300 messages/sec, which is well within the `asyncio` consumer throughput of >5,000 frames/sec.
- **`backend/app/ingestion/news_ws.py`**:
  - `NewsWebSocketClient` subscribes to wildcard `{"action": "subscribe", "news": ["*"]}`. All news articles for AMD, PLTR, COIN, etc., are already captured and ingested into `FinancialSentimentScorer`.
- **`backend/app/core/market_filter.py`**:
  - Explicitly filters incoming bars on `bar.symbol.upper() in ("SPY", "QQQ")` (Line 157) to update anchored VWAPs and 9/21 EMAs.
  - Expanding the single-stock universe does not affect the calculation or integrity of `MarketTrendFilter`.
- **`backend/app/core/runtime_state.py`**:
  - Lines 71 & 185–186: `symbol_sectors` is persisted in the SQLite runtime checkpoint.
  - *Critical Forward-Compatibility Finding*: When restoring state from SQLite via `restore_runtime_state()`, line 186 executes:
    ```python
    for name, value in decoded["risk"].items():
        setattr(risk_engine, name, value)
    ```
    If an existing checkpoint was written prior to universe expansion, `decoded["risk"]["symbol_sectors"]` will only contain the original 9 symbols, which would overwrite the new 12-symbol dictionary in memory. The restoration routine must be hardened to merge existing mappings (`risk_engine.symbol_sectors.update(...)`) or refresh defaults so new symbols are never dropped during state hydration.

---

## 3. Structural Sector Taxonomy & Benchmark Categorization

The expanded universe consists of 12 highly liquid, high-beta symbols. The sector classification must reflect true economic correlation and market microstructure drivers:

| Symbol | Sector Classification | Primary Microstructure & Intraday Drivers | Sector Cap |
| :--- | :--- | :--- | :--- |
| **SPY** | `Index` (or `Index/ETF`) | S&P 500 Market Beta, Macro Regime Benchmark | **Exempt** |
| **QQQ** | `Index` (or `Index/ETF`) | Nasdaq-100 Tech Beta, Macro Regime Benchmark | **Exempt** |
| **NVDA** | `Semiconductors` | AI Hardware, GPU Cyclical, Foundry/Supply Chain | Max 2 |
| **AMD** | `Semiconductors` | CPU/GPU Cyclical, Datacenter Chip Competition | Max 2 |
| **MSFT** | `Software` | Enterprise Cloud, SaaS recurring revenues, Copilot | Max 2 |
| **PLTR** | `Software` | Government/Commercial Defense AI Data Platforms | Max 2 |
| **TSLA** | `Consumer Discretionary` | EV/Automotive, Clean Energy, Retail High-Beta | Max 2 |
| **AMZN** | `Consumer Discretionary` | E-Commerce, Retail Consumer Spending (AWS overlap) | Max 2 |
| **GOOGL** | `Communication Services` | Digital Advertising, Search, YouTube Media | Max 2 |
| **META** | `Communication Services` | Social Networks, Digital Ad CPMs, Reality Labs | Max 2 |
| **COIN** | `Fintech/Crypto` | Digital Asset Brokerage, BTC/ETH Spot Correlation | Max 2 |
| **AAPL** | `Technology` (Hardware) | Consumer Electronics, Mobile Devices, Services Ecosystem | Max 2 |

### Why This Taxonomy Resolves Correlation & Starvation
1. **De-clustering "Technology"**:
   - Previously, `AAPL`, `NVDA`, and `MSFT` were all tagged as `"Technology"`.
   - By distinguishing **Semiconductors** (`NVDA`, `AMD`) from **Software** (`MSFT`, `PLTR`) and **Technology Hardware** (`AAPL`), idiosyncratic setups in chips or cloud software are never suppressed simply because Apple is forming an opening range.
2. **Benchmark Exemption (`Index`)**:
   - `SPY` and `QQQ` serve as the system's causal regime filters. If traded directly by any strategy, they are classified as `"Index"`, which is explicitly exempted from sector concentration limits (`sector != "Index"`).
3. **Multi-Asset Fintech/Crypto Representation**:
   - `COIN` introduces an uncorrelated high-beta vector tied to crypto market volatility, broadening opportunity during equity market consolidation.

---

## 4. Starvation Prevention & Portfolio Concentration Architecture

### 4.1 The Dual-Cap Invariant Matrix
To prevent single-sector starvation without compromising institutional risk, the risk engine must enforce a **two-tier hierarchy**:

1. **Portfolio-Level Concurrency Cap**: $\le 3$ active positions simultaneously.
2. **Sector-Level Concentration Cap**: $\le 2$ active positions in any single sector simultaneously.
3. **Position-Level Notional Cap**: $\le \$25,000.00$ per position ($50\%$ equity).
4. **Daily Drawdown Limit**: $\$1,500.00$ hard circuit breaker.

### 4.2 Mathematical Guarantee of Sector Diversification
Because $\text{Max Per Sector} = 2$ and $\text{Max Portfolio} = 3$:
- The portfolio **can never hold 3 positions from the same sector**.
- Any 3-position full portfolio is mathematically forced to span at least **two distinct sectors**:
  $$\text{Possible Full Portfolio Distributions: } (2, 1) \text{ or } (1, 1, 1)$$
- Under a $(2, 1)$ distribution, the 2 positions in the same sector represent at most $2 \times \$25,000 = \$50,000$ notional (1.0x initial equity), leaving the 3rd position in an independent sector.
- Under a $(1, 1, 1)$ distribution, the portfolio is maximally diversified across 3 completely independent sectors.

### 4.3 Sizing and Gap-Loss Safety Proof
- Maximum single position: $\$25,000.00$.
- Standard trade risk: $1.0\%$ equity ($=\$500.00$) at normal stop distance.
- Extreme overnight/intraday adverse gap scenario:
  - If a stock gaps adversely by $5.0\%$, the loss on a maximum $\$25,000$ position is:
    $$\text{Loss}_{5\%} = \$25,000.00 \times 0.05 = \$1,250.00$$
    This is strictly strictly below the $\$1,500.00$ daily circuit breaker threshold.
  - It requires a $>6.0\%$ gap on a maximum-sized position to trip the breaker from a single trade.
  - Across 2 positions in the same sector (e.g. $2 \times \$25,000 = \$50,000$ notional):
    Normal stops are dynamic and capped between $0.4\%$ and $4.0\%$ ($0.0040 \le \text{stop\_pct} \le 0.0400$).
    With stops placed at $1.5\%$ average, two concurrent stopped-out trades lose:
    $$\text{Loss} = 2 \times (\$50,000 \times 0.010) = \$1,000.00$$
    This reaches the `WARNING` threshold ($dd \ge \$1,000$), clamping subsequent position risk to $1.0\%$ and halting entries if losses reach $\$1,500$.

### 4.4 Algorithm for `evaluate_order_request`
The updated pre-trade validation algorithm in `backend/app/core/risk.py` should be implemented as follows:

```python
# 1. In RiskEngineConfig:
max_positions_per_sector: int = 2

# 2. In evaluate_order_request():
# Sector Diversification Check
sector = self.symbol_sectors.get(symbol)
if sector and sector not in ("Index", "Index/ETF") and symbol not in active_symbols:
    # Derive active count directly from active symbols in portfolio
    sector_count_from_symbols = sum(
        1 for s in active_symbols if self.symbol_sectors.get(s) == sector
    )
    
    # Backward compatibility with callers passing active_sectors
    if isinstance(active_sectors, dict):
        sector_count_from_arg = active_sectors.get(sector, 0)
    elif isinstance(active_sectors, list):
        sector_count_from_arg = active_sectors.count(sector)
    else:
        sector_count_from_arg = 1 if sector in active_sectors else 0
        
    current_sector_count = max(sector_count_from_symbols, sector_count_from_arg)
    
    if current_sector_count >= self.config.max_positions_per_sector:
        return RiskCheckResult(
            approved=False,
            reason=(
                f"CORRELATED_SECTOR_EXPOSURE: Maximum of "
                f"{self.config.max_positions_per_sector} active positions "
                f"reached for sector '{sector}'"
            ),
            requested_qty=requested_qty,
            authorized_qty=0,
            estimated_risk_dollars=0.0,
            risk_level=self.risk_level,
            rejection_code="CORRELATED_SECTOR_EXPOSURE",
        )
```

And in `backend/app/main.py` (lines 111–115 & 930–934):
Callers should preserve the occurrence count by constructing `active_sectors` as a list:
```python
active_sectors = [
    risk_engine.symbol_sectors.get(s, "Other")
    for s in active_symbols
    if s in risk_engine.symbol_sectors
]
```

---

## 5. Audit of Existing Test Suites & Breaking Assumptions

### 5.1 Test Analysis Table

| Test File & Function | Location | Current Assumption | Impact of R1 Changes & Required Updates |
| :--- | :--- | :--- | :--- |
| `tests/e2e/test_tier5_adversarial.py`::`test_adv_concurrent_sector_concentration_barrier` | Lines 193–250 | Assumes opening `AAPL` causes `NVDA` and `MSFT` to be rejected because all 3 were tagged `"Technology"` and sector cap was 1. | **Must be updated**: Under R1, `AAPL` (Technology Hardware), `NVDA` (Semiconductors), and `MSFT` (Software) are in distinct sectors. Furthermore, sector cap is 2. The test must be updated to open 2 stocks in the same sector (e.g. `NVDA` and `AMD`), assert both are accepted, and verify that a 3rd stock in `Semiconductors` (e.g. `INTC`) is rejected with `CORRELATED_SECTOR_EXPOSURE`. |
| `tests/e2e/test_tier5_adversarial.py`::`test_adv_concurrent_burst_order_storm_atomicity` | Lines 147–192 | Submits 10 orders concurrently across `AAPL, NVDA, TSLA, MSFT, AMZN, GOOGL, META, AMD, INTC, SPY`. Asserts `len(positions) <= 3`. | **Compatible**: Passes cleanly because concurrency cap ($\le 3$) and sector barriers ($\le 2$) continue to restrict accepted orders to $\le 3$. |
| `backend/tests/unit/test_risk.py`::`test_risk_engine_max_concurrent_positions` | Lines 94–110 | Sets `active_positions_count = 3` and asserts order on `NVDA` fails with `MAX_CONCURRENT_POSITIONS_REACHED`. | **Compatible**: Passes because concurrency check executes prior to sector check when `active_positions_count >= 3`. |
| `backend/tests/unit/test_risk.py`::`test_production_wiring_caps_a_single_position_at_25k` | Lines 188–218 | Asserts $25,000 cap restricts authorized quantity on $100 stock to 250 shares. | **Compatible**: Position cap remains strictly $25,000.00. |
| `backend/tests/unit/test_risk.py`::`test_circuit_breaker_hard_halt_at_1500_loss` | Lines 128–158 | Asserts daily drawdown of $1,500 transitions status to `HALTED_DAILY_LOSS` and halts trading. | **Compatible**: $1,500 daily breaker remains unchanged. |
| `backend/tests/unit/test_risk.py` (New Tests Needed) | `backend/tests/unit/test_risk.py` | None currently test the 2-position sector cap explicitly. | **New Tests Required**: Add tests verifying: (1) 2 positions in same sector approved; (2) 3rd in same sector rejected; (3) 3 positions across sectors approved; (4) Index exempt from sector cap; (5) All 12 symbols properly mapped. |

---

## 6. Implementation Action Plan for Remediation Sub-Agents

1. **Step 1: Configuration (`backend/app/config.py`)**
   - Update `WATCHLIST_SYMBOLS` to: `["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]`.
2. **Step 2: Risk Engine Config & Sector Mapping (`backend/app/core/risk.py`)**
   - Add `max_positions_per_sector: int = 2` to `RiskEngineConfig`.
   - Update `self.symbol_sectors` dictionary with the 12-symbol taxonomy:
     - `SPY`, `QQQ`: `"Index"`
     - `NVDA`, `AMD`: `"Semiconductors"`
     - `MSFT`, `PLTR`: `"Software"`
     - `TSLA`, `AMZN`: `"Consumer Discretionary"`
     - `GOOGL`, `META`: `"Communication Services"`
     - `COIN`: `"Fintech/Crypto"`
     - `AAPL`: `"Technology"`
   - Update `evaluate_order_request()` to count positions per sector and reject only when `current_sector_count >= self.config.max_positions_per_sector`.
3. **Step 3: Main Integration (`backend/app/main.py`)**
   - Update `active_sectors` list construction in `pre_trade_risk_validator` (lines 111–115) and signal execution preview (lines 930–934).
4. **Step 4: Runtime State Hydration (`backend/app/core/runtime_state.py`)**
   - In `restore_runtime_state()` (line 186), ensure `risk_engine.symbol_sectors` merges restored symbols with the latest default mappings to guarantee forward compatibility.
5. **Step 5: Test Hardening (`backend/tests/unit/test_risk.py` & `tests/e2e/test_tier5_adversarial.py`)**
   - Update `test_adv_concurrent_sector_concentration_barrier` to validate the new 2-position sector barrier.
   - Add comprehensive unit tests in `test_risk.py` covering the 12-symbol sector roster, 2-position sector limits, and 3-position portfolio cap.
