# Plan 2026-09-24: operator trading windows, decision reporting, open issues

Context: live paper bot on Railway (push main = redeploy). Market open now. Bot may hold positions;
restarts must keep them (checkpoint restore, already verified twice today).

## 1. Strategy cards show when each strategy can and cannot trade (backend + UI)
- Single source of truth: `DynamicAdaptationEngine.is_strategy_permitted(strategy_id, phase)` plus
  `get_time_of_day_phase`. Add `adaptation.strategy_window(strategy_id, now_et)` that returns:
  `can_open_now: bool`, `reason` (plain sentence), `window_label` (e.g. "9:30 AM - 11:30 AM"),
  `next_change_at_et` (HH:MM when it flips, or null), `state` in
  {`TRADING`, `WAITING` (outside hours today, opens later), `DONE_FOR_DAY`, `MARKET_CLOSED`, `PAUSED`}.
  Computed by walking the phase boundaries (09:30, 10:00, 11:30, 14:00, 15:00, 15:45, 16:00) with the
  SAME `is_strategy_permitted`, so the card can never disagree with the gate.
- Weekend/holiday: `adaptation.market_status()` already exists; if market status is CLOSED -> MARKET_CLOSED.
- Also expose the live market-direction filter summary per strategy (from `market_filter`):
  e.g. "Market trend BULLISH: longs only", "NEUTRAL: needs RVOL >= 2.2x", "NEUTRAL: allowed" (mean reversion).
  Text only, derived from the same MarketTrend value the gate reads.
- Operator-paused (`status == PAUSED`) overrides everything -> PAUSED.
- `to_dict()` payload adds a `window` object; `status` keeps its meaning (operator pause) for backwards compat.
- UI `StrategyCard.tsx`: badge becomes TRADING NOW (green) / OPENS 2:00 PM (neutral, not red) /
  DONE FOR TODAY (neutral) / MARKET CLOSED (neutral) / PAUSED. Add one plain line under the name:
  "Can open trades 9:30 AM - 11:30 AM. Closed now, opens again tomorrow 9:30 AM."
  plus the market-direction line. Idle is never red (operator dashboard rule).
- Swing card: show "Checks at the 4:00 PM close, buys at next open" (already has STANDBY; add same line).

## 2. Reporting: every blocked signal is recorded and visible
- New bounded ring buffer `signal_decisions` (last 200, in-memory + included in checkpoint? NO: in-memory
  only, plus a per-session per-strategy counter that IS checkpointed so restarts keep today's counts).
- Record at every exit of `_process_signal`'s entry path: duplicate, adaptation denial (reason), risk
  denial (reason), qty 0, engine reject, APPROVED/SUBMITTED. Fields: time ET, strategy, symbol, side,
  price, outcome, reason (short code + plain text).
- `log.info` one line per decision (currently zero logs, so prod behavior is invisible).
- API: `GET /api/decisions?limit=50&strategy=` and per-strategy counts in `/api/strategies` window block
  (`signals_today`, `blocked_today`, `top_block_reason`).
- UI card: "Today: 12 signals, 9 blocked (mostly market trend)". Execution log keeps order events.
- Session history report (`/api/history/sessions`) adds per-strategy signals/blocked counts for the day.

## 3. Swing daily bar survives intraday restarts
- Checkpoint the aggregator's in-flight bars (`_in_flight`) and restore them.
- On startup during/after market hours, if today's in-flight bar for a swing symbol is missing or starts
  after 09:31 ET, backfill today's 1-min bars from the relay `/data` REST (one call, 5s timeout,
  fail-open to current behavior with a WARNING) and rebuild the in-flight bar.
- Aggregator ignores minute bars with timestamp <= last minute already folded in (no double counting
  between backfill and live stream).
- This repairs today's 09-24 bar (damaged by 3 restarts) at this deploy.

## 4. News Momentum never fires (0.60 sentiment bar)
- Do NOT change the threshold blind. Replay 5 recent real sessions (09-17..09-23) with 0.60 vs 0.50,
  count signals and outcomes. Change only if it produces signals AND they are not net losers; otherwise
  keep 0.60 and say so on the card ("Needs very strong news, rare").

## 5. Earnings calendar unverified
- Verify the 15 entries in `earnings_calendar.json` (LRCX, KLAC, MU, AMD, GS) against public sources;
  correct wrong dates. Flag anything unverifiable.

## 6. Test hygiene
- `pytest` from repo root fails to collect `tests/e2e` (ModuleNotFoundError tests.e2e). Fix the package
  path so a bare `pytest` works.

## QA gates
- Unit tests for strategy_window at every boundary (09:29, 09:30, 09:59, 10:00, 11:29, 11:30, 13:59, 14:00,
  15:44, 15:45, 16:00, Saturday) and a test that card state == gate decision for all 4 strategies at every
  minute of a session (property test, so card and gate cannot drift).
- Decision log tests: each rejection path records exactly one decision with the right code.
- In-flight restore + backfill dedupe tests.
- Full backend suite, E2E runner, frontend `npm run build`, headless screenshot of the cards at a
  TRADING time and a WAITING time (phone width 390 and desktop).
- Deploy: push main, wait for new restored_at, verify /health, /api/strategies window block matches the
  clock, /api/decisions populating, positions still intact, no ERROR lines in logs.

---
## REVISION after Codex attack (27 findings, 2026-09-24 11:05 ET)

Accepted and changed in the plan:
- C1/C8/C9: cards separate "schedule window" from "can open right now". The card lists the live blockers
  read from the same objects the entry path reads: operator pause/cooldown, circuit breaker, EOD entry
  lockout, persistence halt, positions full, VIX stale (sizing note), market-direction policy text per
  strategy (incl. NEUTRAL+RVOL exception, news extreme override). Wording is "allowed to open trades" /
  "waiting for a setup", never "trading". Unknown/stale telemetry shows as such.
- C2: weekend + NYSE 2026/2027 holiday calendar for the card's "next open". Half days listed; the card says
  "early close 1:00 PM". (Flattening on half days is a real gap, see Deferred.)
- C3: card uses the aware ET wall clock; tests pin DST and naive-time inputs.
- C6: operator PAUSED survives the daily reset.
- C8: clock loop broadcasts state every 10 s so cards flip at 11:30 etc. without a bar; REST fallback
  refreshes strategies.
- C10/C11: all new checkpoint data is a new OPTIONAL top-level key of primitives (no strategy __dict__, no
  deque); restore ignores its absence; old code ignores its presence. RUNTIME_STATE_VERSION unchanged.
  Test: checkpoint without the key restores; checkpoint with the key round-trips.
- C12: on startup, rebuild the SPY/QQQ market-direction filter from today's REST 1-min bars in an
  ISOLATED filter object, then swap it in atomically (never through the live bar handler, no fills).
- C13/C14/C15/C16: aggregator stores bars per minute (merge by minute, live wins, no watermark);
  backfill merges REST minutes (paginated, deadline, RTH-bounded) for swing symbols + QQQ at startup and
  again at the close. No replay through the trading handler.
- C17/C18: close processing runs as a background task: backfill, require coverage (>= 385/390 minutes per
  swing symbol and QQQ), then finalize + evaluate. If coverage is short, exits are still evaluated but NEW
  swing entries are withheld and the UI/log says why.
- C19/C20/C21/C22: decision log hooks at arbitration drop AND each admission outcome; fixed reason codes;
  bounded (last 300) and checkpointed with per-strategy per-reason counters in the optional key, so
  restarts keep today's record; counters snapshot into the session summary at rollover; one INFO log line
  per decision (signals are dozens/day, not per bar). Strategy-internal "no setup" funnel is Deferred.
- C25: earnings exit fix: a BMO report on the trading day after next must exit at the next open (current
  code exits after the report). `confirmed` stays informational.
- C26: deploy check compares positions, shares, stop prices and working protective orders before vs after.
- C27: `backend/__init__.py` committed; bare `pytest` runs all 813.

Rejected / already handled:
- C23/C24 (item 4 method): agreed; the replay was only diagnostic. No threshold change is made. Card will
  say News Momentum is rare by design.
- C5 (warm-up states): covered by "waiting for a setup" wording; per-strategy warm-up detail Deferred.

Deferred (logged, not done today):
- Half-day early close: flattening still targets 15:55; next half day 2026-11-27. Must fix before then.
- C7: entry orders placed just before a window closes are not cancelled at the boundary (15:50 purge
  still applies).
- Strategy-internal funnel reporting (news TTL/volume/candle failures).
- Finalization after a restart that lands after 16:00 (no retrigger).
