# ERRORS.md — AutonomousDayTrader

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

