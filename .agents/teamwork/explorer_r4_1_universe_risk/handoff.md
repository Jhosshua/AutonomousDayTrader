# Handoff Report: R1 Universe Expansion & Sector Risk Invariants

**Author**: Explorer 1 (Universe & Risk Explorer)  
**Date**: 2026-09-23  
**Target Recipient**: orchestrator_5 (Conversation ID: `5cdb7319-1240-43a6-9073-f74cd8e19cf8`)  
**Working Directory**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_1_universe_risk`  
**Reference Analysis**: `/Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_1_universe_risk/analysis.md`  

---

## 1. Observation

1. **Watchlist Configuration (`backend/app/config.py:59–62`)**:
   ```python
   WATCHLIST_SYMBOLS: List[str] = Field(
       default=["SPY", "QQQ", "AAPL", "NVDA", "TSLA"],
       description="Default symbol roster for stock market data subscriptions"
   )
   ```
   Currently only 5 symbols are configured by default. The required expanded universe is 12 symbols:
   `["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]`.

2. **Sector Mapping & Hardcoded 1-Position Lockout (`backend/app/core/risk.py:64–74, 187–198`)**:
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
   And line 189:
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
   - Missing symbols: `AMD`, `PLTR`, `COIN` do not exist in `self.symbol_sectors`.
   - `AAPL`, `NVDA`, and `MSFT` are all labeled `"Technology"`.
   - Because `sector in active_sectors` is a boolean set membership check, having ANY open position in a sector rejects any second position in the same sector.

3. **Active Sector Set Construction in Execution Paths (`backend/app/main.py:111–115, 930–934`)**:
   ```python
   active_symbols = set(acct.positions.keys())
   active_sectors = {
       risk_engine.symbol_sectors.get(s, "Other")
       for s in active_symbols
       if s in risk_engine.symbol_sectors
   }
   ```
   A Python set comprehension `{ ... }` collapses duplicate sector occurrences, discarding the count of positions in a given sector.

4. **Dynamic Feed Ingestion Mechanics (`backend/app/ingestion/stock_ws.py:41, 192–204`)**:
   `StockWebSocketClient.symbols` defaults to `set(symbols or settings.WATCHLIST_SYMBOLS)`. On connection handshake, it transmits subscriptions across `bars`, `quotes`, and `trades` dynamically for all symbols in `self.symbols`. Ingestion queue capacity is 10,000 items with an 80% watermark (8,000 items). `NewsWebSocketClient` subscribes to wildcard `news: ["*"]`.

5. **Runtime Persistence Hydration Risk (`backend/app/core/runtime_state.py:71, 185–186`)**:
   ```python
   for name, value in decoded["risk"].items():
       setattr(risk_engine, name, value)
   ```
   Restoring from a pre-expansion checkpoint overwrites `risk_engine.symbol_sectors` with the older 9-symbol dictionary if not merged.

6. **Adversarial Test Suite Expectations (`tests/e2e/test_tier5_adversarial.py:193–250`)**:
   `test_adv_concurrent_sector_concentration_barrier` explicitly asserts that opening `AAPL` causes `NVDA` and `MSFT` to be rejected with `CORRELATED_SECTOR_EXPOSURE` because all 3 were tagged `"Technology"` under the old 1-position limit.

7. **Test Suite Baseline**:
   - `pytest backend/tests -q`: 272 passed in 4.21s.
   - `python3 tests/e2e/runner.py`: 320 passed in 27.18s, all ports clean.
   - `python3 scripts/run_integrated_monday_dry_run.py`: 184 events processed, 0 errors, flat EOD book.

---

## 2. Logic Chain

1. **Root Cause of Sector Starvation**:
   - From Observation 2, `AAPL`, `NVDA`, and `MSFT` were all grouped under `"Technology"`.
   - When `AAPL` broke out first at 09:35 ET, `AAPL` entered the portfolio.
   - When `NVDA` (Semiconductor) or `MSFT` (Software) subsequently signaled valid setups, Observation 2 line 189 triggered because `sector = "Technology"` was already in `active_sectors`. Both trades were rejected with `CORRELATED_SECTOR_EXPOSURE`.
   - This single-sector lockout caused artificial trade starvation even when broader market liquidity and volume surged.

2. **Resolution via Granular Taxonomy**:
   - Breaking the broad "Technology" label into economically distinct sub-industries:
     - `Semiconductors`: `NVDA`, `AMD`
     - `Software`: `MSFT`, `PLTR`
     - `Technology` (Hardware): `AAPL`
     - `Consumer Discretionary`: `TSLA`, `AMZN`
     - `Communication Services`: `GOOGL`, `META`
     - `Fintech/Crypto`: `COIN`
     - `Index`: `SPY`, `QQQ` (benchmark filter, exempt from sector cap)
   - Holding `AAPL` will no longer block `NVDA` or `MSFT`, because they belong to completely distinct sectors.

3. **Safe Transition to 2 Positions Per Sector**:
   - Under the revised policy: $\text{Max Per Sector} = 2$, $\text{Max Portfolio Total} = 3$.
   - Sizing invariant: $\text{Single Position Notional Cap} = \$25,000.00$ ($50\%$ equity).
   - Even if two positions are opened in the same sector (e.g. `NVDA` and `AMD`), total sector notional is capped at $2 \times \$25,000 = \$50,000$ (1.0x initial equity), which utilizes only $25\%$ of the FINRA Rule 4210 $200,000 DTBP.
   - Standard 1% risk per trade ($500) means two stopped trades in the same sector lose $\$1,000.00$, hitting `WARNING` mode ($dd \ge \$1,000$) rather than breaching the $\$1,500.00$ hard breaker.
   - A single position gapping $5.0\%$ loses $\$1,250.00$, which strictly stays inside the $\$1,500$ daily breaker.
   - Mathematical guarantee: Because portfolio max is 3 and sector max is 2, any full 3-position portfolio must span at least **two distinct sectors** (either a 2+1 distribution or a 1+1+1 distribution). A single sector can never monopolize the portfolio.

4. **Preserving Backward-Compatibility in `evaluate_order_request`**:
   - `evaluate_order_request` already receives `active_symbols`.
   - Counting `sector_count = sum(1 for s in active_symbols if self.symbol_sectors.get(s) == sector)` accurately calculates the open positions in that sector.
   - Supporting `active_sectors` as either `list`, `dict`, or `set` guarantees that both production code and existing tests run without breaking.

---

## 3. Caveats

1. **E2E Adversarial Test Mutation**:
   `tests/e2e/test_tier5_adversarial.py::test_adv_concurrent_sector_concentration_barrier` was written specifically to assert that `AAPL` blocks `NVDA` under the old 1-position Technology regime. When R1 is implemented, this test must be updated to test the new 2-position barrier (e.g., `NVDA` + `AMD` accepted; 3rd semiconductor rejected).
2. **Persistence Deserialization**:
   If an existing SQLite state file (`.data/trading_state.sqlite3`) exists on a production volume with serialized 9-symbol state, `runtime_state.py` must merge rather than blindly overwrite `symbol_sectors` on startup.
3. **No Code Modification Undertaken**:
   In strict accordance with the explorer archetype rules, this investigation was strictly read-only. No source files outside the agent directory were modified.

---

## 4. Conclusion

1. **Expansion is Fully Supported by Ingestion**:
   `WATCHLIST_SYMBOLS` in `backend/app/config.py` can be safely expanded to all 12 symbols (`["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]`). `stock_ws.py` and `news_ws.py` dynamically handle all 12 symbols with zero infrastructure changes required.
2. **Sector Architecture is Defined and Sound**:
   - `Semiconductors`: `NVDA`, `AMD`
   - `Software`: `MSFT`, `PLTR`
   - `Consumer Discretionary`: `TSLA`, `AMZN`
   - `Communication Services`: `GOOGL`, `META`
   - `Fintech/Crypto`: `COIN`
   - `Technology`: `AAPL`
   - `Index`: `SPY`, `QQQ` (exempt from sector cap)
3. **Sector Concentration Limit**:
   - Add `max_positions_per_sector: int = 2` to `RiskEngineConfig`.
   - Update `evaluate_order_request()` to check `sector_count >= self.config.max_positions_per_sector`.
   - Strictly preserves max 3 positions total, $25,000 position notional cap, $1,500 daily breaker, and [0.4%, 4.0%] stop guardrails.

---

## 5. Verification Method

To independently verify the implementation after remediation:

1. **Unit Test Verification**:
   ```bash
   pytest backend/tests -v
   ```
   Must pass with 100% success rate.
2. **Sector Concentration Verification Tests**:
   Inspect and run newly added unit tests in `backend/tests/unit/test_risk.py`:
   - Verify opening 2 positions in `Semiconductors` (`NVDA`, `AMD`) is APPROVED.
   - Verify opening a 3rd position in `Semiconductors` is REJECTED with `CORRELATED_SECTOR_EXPOSURE`.
   - Verify opening 2 in `Semiconductors` + 1 in `Software` (`MSFT`) is APPROVED (3 total).
   - Verify opening a 4th position total is REJECTED with `MAX_CONCURRENT_POSITIONS_REACHED`.
   - Verify `SPY` and `QQQ` are categorized as `Index` and exempt from sector lockout.
3. **Full E2E Suite**:
   ```bash
   python3 tests/e2e/runner.py
   ```
   Must pass all 320+ tests with clean port release.
4. **Integrated Monday Dry Run**:
   ```bash
   python3 scripts/run_integrated_monday_dry_run.py
   ```
   Must complete cleanly with zero unhandled exceptions and verified flat EOD book.
