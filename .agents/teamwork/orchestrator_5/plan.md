# Orchestration Plan — AutonomousDayTrader Universe & Strategy Scaling

## Objectives
Fulfill requirements R1 through R6 from `ORIGINAL_REQUEST.md` (2026-09-23T19:09:59Z):
- R1: Universe Expansion (12 symbols) & Sector Mapping (5 sectors, max 2/sector, max 3 total).
- R2: Regime-Separated Strategy Execution (Trend: ORB & VWAP; Neutral: Mean Reversion & RVOL >= 2.20x idiosyncratic breakouts).
- R3: Microstructure & Indicator Calibration (news_momentum volume surge 2.0x, mean_reversion z=1.65, vol climax 1.30x, wick 0.30; preserve all risk invariants).
- R4: Independent Adversarial Anti-Hallucination & Anti-Bias Audit (Zero synthetic fixture delusions, zero lookahead bias, zero float escapes, mutation testing).
- R5: Deterministic E2E Dry Run & Process Hygiene (100% pytest pass, E2E simulation runner across 12 symbols & 4 strategies, verify ports 8000, 8005, 8080, 3005 liberated).
- R6: Mobile UI Visual Audit, Documentation, Git Push & Remote Railway Deployment Verification.

## Phase Breakdown

### Phase 1: Survey & Technical Assessment (3 Explorers)
- Explorer 1 (`explorer_r4_1_universe_risk`): Survey `backend/app/config.py`, `backend/app/core/risk.py`, portfolio concentration, sector definitions, and related tests.
- Explorer 2 (`explorer_r4_2_strategies_regime`): Survey `backend/app/core/market_filter.py`, `backend/app/strategies/` (`orb.py`, `vwap_pullback.py`, `mean_reversion.py`, `news_momentum.py`, `adaptation.py`), regime gating, indicator math, volume surge thresholds.
- Explorer 3 (`explorer_r4_3_verification_deploy`): Survey backend pytest suite, `tests/e2e/runner.py`, `scripts/run_integrated_monday_dry_run.py`, frontend components for expanded symbols, port hygiene, and Railway deploy setup.

### Phase 2: Implementation (Worker)
- Worker (`worker_r4_implementation`):
  - Expand `WATCHLIST_SYMBOLS` to `["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]`.
  - Update sector mapping in `risk.py`: Semiconductors (NVDA, AMD), Software (MSFT, PLTR), Discretionary (TSLA, AMZN), Communication Services (GOOGL, META), Fintech/Crypto (COIN), Index/ETFs (SPY, QQQ).
  - Enforce sector concentration limits (max 2 positions/sector, max 3 positions total) while avoiding starvation.
  - Implement regime-separated strategy execution in `market_filter.py` and strategies: Trending (BULLISH/BEARISH) -> ORB & VWAP, lockout Mean Reversion; Neutral -> Statistical Mean Reversion (+-1.6 sigma to 20-SMA) + high-RVOL idiosyncratic breakout (RVOL >= 2.20x).
  - Calibrate `news_momentum.py`: volume surge from 3.5x to 2.0x, strict boundary sentiment NLP.
  - Calibrate `mean_reversion.py`: Z-score 1.65, volume climax 1.30x, wick rejection 0.30.
  - Preserve all invariants: $1,500 daily breaker, $25,000 position cap, stop distances in [0.0040, 0.0400], 4-phase EOD auto-flattening.
  - Update unit/integration tests to match new universe and calibrated parameters.

### Phase 3: Multi-Agent Adversarial Verification & Audit
- Reviewer 1 (`reviewer_r4_1`): Code quality, interface adherence, correctness, regression check.
- Reviewer 2 (`reviewer_r4_2`): Edge-case stress testing, floating point precision, boundary conditions.
- Challenger 1 (`challenger_r4_1`): Empirical verification: zero lookahead bias, unclosed bar inspection, mutation testing on risk limits and indicators.
- Challenger 2 (`challenger_r4_2`): Anti-hallucination verification: test data validation (no fake fixture delusions), RVOL decoupling logic verification, sector starvation prevention tests.
- Forensic Auditor (`auditor_r4_1`): Binary veto forensic audit for integrity, genuine logic, zero hardcoded values, zero bypasses.

### Phase 4: Deterministic End-to-End Dry Run & Port Hygiene
- Run 100% of backend pytest suite (`pytest backend/tests`).
- Execute end-to-end simulation runner covering all 4 strategies, 12 symbols, and regime transitions.
- Verify ports 8000, 8005, 8080, 3005 are clean and liberated.

### Phase 5: Mobile UI Visual Audit, Documentation & Remote Railway Deployment
- Visual audit of Next.js dashboard, bottom drawer state transitions, and WebSocket latency indicators across expanded symbols.
- Update `MEMORY.md`, `ERRORS.md`, and `PROJECT.md` with full mathematical rationale and audit trail.
- Commit to git and push to `origin main`.
- Verify remote Railway deployment live at `https://autonomousdaytrader-production.up.railway.app/health`.
