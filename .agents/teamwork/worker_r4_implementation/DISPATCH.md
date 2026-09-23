# Dispatch for Worker (worker_r4_implementation)
Role: Core Implementation & Calibration Worker
Working directory: /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_r4_implementation
Authoritative request: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md (under ## 2026-09-23T19:09:59Z)

## 2026-09-23T19:20:16Z
You are the Core Implementation Worker (worker_r4_implementation) for AutonomousDayTrader.
Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork/worker_r4_implementation
Your parent is: orchestrator_5 (Conversation ID: 5cdb7319-1240-43a6-9073-f74cd8e19cf8)

Authoritative request: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md (under ## 2026-09-23T19:09:59Z)

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Reference Explorer Reports:
Read these reports carefully before writing code:
- Explorer 1 (Universe & Risk): /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_1_universe_risk/handoff.md and analysis.md
- Explorer 2 (Strategies & Regimes): /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_2_strategies_regime/handoff.md and analysis.md
- Explorer 3 (Verification & Deploy): /Users/mo/AutonomousDayTrader/.agents/teamwork/explorer_r4_3_verification_deploy/handoff.md and analysis.md

Your Implementation Tasks:

1. R1: Universe Expansion & Sector Risk:
- In `backend/app/config.py`: Expand `WATCHLIST_SYMBOLS` to `["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]`.
- In `backend/app/core/risk.py`:
  - Update `self.symbol_sectors`:
    "SPY": "Index", "QQQ": "Index",
    "AAPL": "Technology",
    "NVDA": "Semiconductors", "AMD": "Semiconductors",
    "MSFT": "Software", "PLTR": "Software",
    "TSLA": "Consumer Discretionary", "AMZN": "Consumer Discretionary",
    "GOOGL": "Communication Services", "META": "Communication Services",
    "COIN": "Fintech/Crypto"
  - In `RiskEngineConfig`: Add `max_positions_per_sector: int = 2`.
  - In `evaluate_order_request()`: Count existing active positions in the requested symbol's sector. Allow up to 2 positions in that sector. Reject a 3rd position in that sector with `rejection_code="CORRELATED_SECTOR_EXPOSURE"`.
  - Maintain strict portfolio max of 3 concurrent positions total (`MAX_CONCURRENT_POSITIONS_REACHED`).
  - Preserve all risk invariants: $1,500 daily circuit breaker, $25,000 position notional cap (50% equity), stop distances strictly in [0.0040, 0.0400], and 4-phase EOD zero-overnight auto-flattening.
- In `backend/app/core/runtime_state.py`: Ensure `symbol_sectors` is merged on restore rather than overwritten.

2. R2: Regime-Separated Strategy Execution:
- In `backend/app/core/market_filter.py`:
  - Extend `is_signal_permitted(self, strategy_name: str, side: OrderSide, symbol: str, catalyst_sentiment: float = 0.0, volume_surge: float = 0.0, rvol: Optional[float] = None) -> Tuple[bool, str]`.
  - In `MarketTrend.NEUTRAL`:
    - Permit `mean_reversion` (both BUY and SELL).
    - Permit `orb` and `news_momentum` if `rvol is not None and rvol >= 2.20` with reason containing `APPROVED_IDIOSYNCRATIC_BREAKOUT`. If rvol < 2.20 or None, deny with `INDEX_FILTER_DENIED`.
    - Deny `vwap_pullback` with `INDEX_FILTER_DENIED`.
  - In `MarketTrend.BULLISH` / `BEARISH`:
    - Permit `orb` and `vwap_pullback` along index beta (BUY in BULLISH, SELL in BEARISH).
    - Deny counter-trend `mean_reversion` with `INDEX_FILTER_DENIED`.
- In `backend/app/strategies/base.py` & `backend/app/strategies/adaptation.py`:
  - Add typed `rvol: Optional[float] = None` to `SignalEvent`.
  - Pass `rvol=signal.rvol` in `adaptation_engine.evaluate_signal_admission()` into `market_filter.is_signal_permitted()`.

3. R3: Microstructure & Indicator Calibration:
- In `backend/app/strategies/news_momentum.py`:
  - Change default `volume_surge_multiplier` from `3.50` to `2.00`.
  - Attach `sig.rvol = vol_ratio` to emitted signals.
- In `backend/app/ingestion/sentiment.py`:
  - Fix naive substring matching: enforce regex word boundary `r"\b" + re.escape(...) + r"\b"` on multi-word phrases and keyword matching in `_classify_category()` so that words like "sector" do not falsely trigger "sec" -> `LEGAL_INVESTIGATION`.
- In `backend/app/strategies/mean_reversion.py`:
  - Calibrate defaults: `z_threshold = 1.65`, `volume_climax_multiplier = 1.30`, `min_wick_ratio = 0.30`.

4. Test Updates & Mutation Tests:
- In `tests/e2e/runner.py`: Add port 8000 to `ports_to_check = [8080, 8005, 8000, 3005]`.
- In `backend/tests/unit/test_strategies.py`: Update assertions on default parameters to 1.65, 1.30, 0.30.
- In `backend/tests/unit/test_market_filter.py`: Add tests for NEUTRAL idiosyncratic breakouts (RVOL >= 2.20), Mean Reversion in NEUTRAL, lockout in BULLISH/BEARISH.
- In `backend/tests/unit/test_risk.py`: Add tests for 2 positions per sector allowed, 3rd rejected, max 3 total.
- In `backend/tests/unit/test_sentiment.py`: Add test verifying "Apple Leads Tech Sector Rally After Strong Demand" does NOT categorize as LEGAL_INVESTIGATION.
- In `tests/e2e/test_tier5_adversarial.py`: Update `test_adv_concurrent_sector_concentration_barrier` to verify the 2-position sector barrier (e.g. 2 semiconductors allowed, 3rd blocked).
- Create `backend/tests/stress/test_challenger_r4_remediation.py` with mutation tests certifying:
  1. Mutating sector cap to 3 is caught.
  2. Mutating RVOL threshold below 2.20 in NEUTRAL is caught.
  3. Mutating Z-score above 1.65 or below is caught.
  4. Sentiment regex word boundary mutation is caught.
  5. Causal indicator lookback mutation is caught.

5. Verification Execution:
- Run `pytest backend/tests -v` and ensure 100% pass rate.
- Run `python3 tests/e2e/runner.py` and ensure 100% pass rate.
- Verify port hygiene using `bash scripts/verify_port_hygiene.sh`.

Deliverables:
- Write detailed completion report to `/Users/mo/AutonomousDayTrader/.agents/teamwork/worker_r4_implementation/handoff.md`.
- Include exact files modified, test commands executed, and verified results.
- Send a completion message via send_message to orchestrator_5.
