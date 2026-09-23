## 2026-09-23T04:20:44Z
You are Worker 2: Strategy & Execution Architecture Remediator (Iteration 2).

Your working directory is:
/Users/mo/AutonomousDayTrader/.agents/teamwork/worker_remediation_r2
All your logs, state, and handoff report must be written to your working directory.

Authoritative source of truth:
You MUST read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also study:
- /Users/mo/AutonomousDayTrader/.agents/teamwork/reviewer_1/handoff.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/reviewer_1/review.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r2_1_filter/handoff.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r2_1_filter/analysis.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r2_2_e2e/handoff.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r2_2_e2e/analysis.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r2_3_bracket/handoff.md
- /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r2_3_bracket/analysis.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Exclusive Write Ownership:
- backend/app/core/market_filter.py
- backend/app/core/bracket.py
- backend/app/strategies/orb.py
- backend/tests/unit/test_market_filter.py
- tests/e2e/test_challenger_bracket_2.py
- tests/e2e/test_tier5_adversarial.py
- tests/e2e/fixtures/monday_open_session.json

Tasks to Execute:
1. In `backend/app/core/market_filter.py`:
   - Implement Macro-Aligned Mean Reversion policy (from Explorer R2-1):
     `BULLISH`: Allow BUY (dip buying oversold), strictly BLOCK SELL (`INDEX_BETA_CONTRADICTION`) to prevent shorting bull rallies (fixing the 2026-09-22 TSLA/AAPL loss).
     `BEARISH`: Allow SELL (relief fade), strictly BLOCK BUY (`INDEX_BETA_CONTRADICTION`) to prevent catching falling knives.
     `NEUTRAL`: Allow both BUY and SELL.
     `UNKNOWN`: Block all.
   - Replace `abs()` staleness calculation with strictly causal checks:
     `elapsed = (now - spy_ts).total_seconds()`
     `if elapsed < 0: return MarketTrend.UNKNOWN, f"FUTURE_INDEX_DATA: Index timestamp is in the future ({elapsed:.1f}s)"`
     `if elapsed > self.stale_threshold_sec: return MarketTrend.UNKNOWN, f"STALE_INDEX_DATA: SPY data age ({elapsed:.1f}s) > {self.stale_threshold_sec}s"`
     (Apply identical causal check for QQQ).
   - Add `if bar.timestamp is None: return` in `IndexState.update_bar` and `MarketTrendFilter.on_bar`.
   - Update `backend/tests/unit/test_market_filter.py` to verify the macro-aligned mean reversion policy and causal staleness guard.
2. In `backend/app/core/bracket.py`:
   - Slippage Boundary Sanity Guard in `activate_bracket_on_fill` (from Explorer R2-3):
     Ensure `target_1_override > fill_price` for LONG (and `< fill_price` for SHORT). If slippage causes violation, dynamically re-anchor target 1 relative to `fill_price` using `default_target_1_r`.
     Ensure `target_2_override > target_1_price` for LONG (and `< target_1_price` for SHORT).
   - Target 1 Partial Fill Orphan Fix (from Explorer R2-3 & Challenger 2):
     In `on_child_order_fill`:
     `bracket.target_1_qty = max(0, bracket.target_1_qty - filled_qty)`
     `bracket.target_1_filled = (bracket.target_1_qty == 0)`
     In `on_child_order_fill` for `STOP_LOSS`:
     Cancel targets if `(not bracket.target_X_filled or bracket.target_X_qty > 0)`.
     Add `@property def target_1_remaining_qty(self) -> int: return self.target_1_qty`.
3. In `backend/app/strategies/orb.py`:
   - Use `clv = round((close_p - low_p) / candle_range, 4)` and check `clv >= (min_clv - 1e-5)` for BUY and `clv <= (max_clv_sell + 1e-5)` for SELL to avoid IEEE 754 precision dropouts at 0.6500.
4. In `tests/e2e/`:
   - In `tests/e2e/test_tier5_adversarial.py:155`: update expected target price to 101.60 (matching 0.8R calibrated target).
   - In `tests/e2e/test_challenger_bracket_2.py`:
     In `TestNewsMomentumStopDistanceClamping`: set test fixture candle to have directional close (`close_p = round(entry_price + 0.10, 4)` for buy) so candle direction check passes.
     In `TestOrbStopDistanceClamping` and `TestExtremePricesClamping`: set test fixture candle close near high for buy (`high_p = close_p + 0.01`) so CLV check passes.
5. In `tests/e2e/fixtures/monday_open_session.json`:
   - Ensure SPY and QQQ bars are included during the 09:30-10:30 market session so the integrated Monday dry run has valid market trend consensus.
6. Run Verification Commands:
   - `pytest backend/tests -v` (100% pass)
   - `python3 tests/e2e/runner.py` (320/320 pass, 100%)
   - `python3 scripts/run_integrated_monday_dry_run.py` (runs cleanly with 0 errors)
   - `lsof -i :8000 -i :8005 -i :8080 -i :3005` (zero listening processes)
7. Write hard handoff report to:
   /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_remediation_r2/handoff.md
   Include full test outputs and commands run. Send completion message to parent when done.
