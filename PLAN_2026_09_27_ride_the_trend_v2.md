# PLAN 2026-09-27: Ride the Trend v2 (new entry rules, measured before they trade)

Status: DRAFT v3, after two Codex attack rounds (round 1: 32 findings; round 2: 22 new findings plus a
disposition of the 32). Both reviews are in `docs/ride_the_trend_v2/`. Triage in section 10. Stopping rule:
no third round; every remaining item is an acceptance criterion on a build step below.
Strategy: `vwap_pullback` ("Ride the Trend" on the dashboard), `backend/app/strategies/vwap_pullback.py`.
Bot: AutonomousDayTrader, Alpaca paper PA3CSVDZMMPY, Railway, push to main = redeploy.
Day one: Monday 2026-09-28.

## 0. The constraint this plan has to live with

On 2026-09-27 the current rules were replayed on 2.7 years of 1-minute SIP bars for the 12 watchlist symbols
(`research/vwap_trend_2026_09_27/REPORT.md`, simulator parity-proven 649/649 signals against the live class):

| Live rules, 12 symbols, $50k, 1% risk | |
|---|---|
| Trades 2024-01-02 to 2026-09-25 | 4,407 |
| Mean R per trade | -0.055 (SE 0.013) |
| Same with zero cost | +0.001 |
| Forward drift after 36,435 raw signals | 0 bps at 5, 15, 30, 60, 120 min and close |
| Rule variants tried, positive in all three periods | 0 of 69 |

The v1 entry carries no information and the market-order round trip is the whole loss. Of the five pasted
principles, three (volatility stops, SPY/QQQ regime, partial-plus-trail) are already in the bot and already
tested with no gain; two (speed of the resumption, volume of the pullback leg) are new and unmeasured. They are
hypotheses. All three historical periods, including "HOLD 2026", have been used to choose rules (69 variants,
then this design), so they are development evidence. Nothing historical can promote v2 to broker orders;
confirmation comes from the shadow run that starts Monday.

## 1. What changes on day one (Monday 2026-09-28), decided now

1. v1 "Ride the Trend" places no more orders. `RIDE_THE_TREND_MODE=v2_shadow` is the deploy setting.
2. One frozen policy, `V2_FULL` (section 4), is evaluated on every bar for the 10 non-plan symbols. TSLA and
   CDE are excluded in every mode (frozen now: Monday is the first live session of the TSLA + CDE Morning Plan).
3. v2 keeps its own state and ledger in the research store, never in the trading checkpoint. It writes an
   event for every evaluated bar, every setup transition and every gate decision, plus a MODELED outcome for
   each would-be trade. Modeled numbers are never mixed with broker fills.
4. The dashboard card reads "Ride the Trend v2, watching only, no orders" and shows the policy id, the last
   bar evaluated, and today's counts (setups, rejected by which gate, modeled trades).
5. Broker-order mode (`v2_live`) is a separate milestone (section 7) behind a fail-closed activation check
   (section 7.8). A config flip alone cannot reach it.

This is the change in execution from day one: the coin-flip market entries stop, and the new rules are
measured on live data from the first bar.

## 2. Principle to rule mapping

| # | Pasted principle | Bot today | v2 rule | What the 09-27 data already says |
|---|---|---|---|---|
| 1 | No lagging indicators; measure the speed of the resumption | EMA20 > EMA50 on 1-min closes; entry on one bounce bar | Close-to-close resumption slope off the pullback low over completed bars, with a no-chase cap. No EMAs | EMA cross anti-predictive (stocks up 0.5%+ = worst bucket, -0.109). Chasing -0.118. Slope: untested |
| 2 | Thin-volume pullback is real, heavy-volume pullback is a trap | Only the bounce bar's volume (>= 1.2x SMA10) | Volume of the leg from the impulse extreme to the first zone touch, relative to a frozen reference leg. High-volume legs rejected. Stated as an aggregate-volume hypothesis about the leg BEFORE first touch; no claim about who is trading | Bounce-bar surge anti-predictive (-0.063 vs -0.048). Leg volume: untested. The new lever |
| 3 | Volatility-adjusted stops | VWAP - 0.5 std, 0.4% floor, 4% ceiling, VIX multiplier | Stop computed once at signal time: max(1.5 ATR x VIX mult, structure beyond the pullback extreme, floor); rejected if over 4%, never rescaled downstream | 1x/2x ATR, 1 std, no VIX: no help. Not a lever alone; it changes hold time and slot use, so it is an ablation |
| 4 | Relative strength and market regime | SPY/QQQ VWAP + EMA9/21 filter | Keep. Stock-minus-SPY return joined on the same completed-bar timestamp, unavailable = rejected. An ablation, not in V2_FULL | Index filter on/off: drift 0 either way. RS may just re-select strong absolute movers (worst bucket); tested conditionally |
| 5 | Sizing and the tail, not the perfect entry | Market at close; 50% at band T1 (median 0.76R); trail; T2 | Resting limit at the signal close with a real lifecycle (live milestone only); 50% at 1.0R; runner trail with a declared policy; max 2 admitted per symbol-day; morning only | Limit at close -0.024 vs -0.055. Morning + max 2 + limit -0.006 +/- 0.019. Targets/trail: no help |

## 3. The v2 signal machine (deterministic, one machine per symbol)

### 3.1 Inputs and bar policy
- Bars: completed regular-session 1-minute SIP bars from AlpacaRelay. Key = (symbol, bar start timestamp).
  A bar is final when received. Duplicate key: ignored before any state update (event `DUP_BAR`). Timestamp
  earlier than the last accepted bar: ignored (`LATE_BAR`). Non-finite or negative values: ignored (`BAD_BAR`).
- `i` = ordinal of accepted session bars (0 = first accepted bar of the session). All lookbacks, ages,
  cooldowns and expiries count accepted bars. A gap of more than 5 minutes between accepted bars resets the
  machine to IDLE (`FEED_GAP`); the bars themselves are still appended to the session history.
- Zero-volume bar: accepted into history; it can never be a signal bar (`ZERO_VOLUME_BAR`, evaluation skipped
  for that bar, state unchanged except timers).
- `H,L,C,V`, `VWAP[i]`, `std[i]` as today (`std <= 0.001` falls back to ATR as v1 does). `ATR[i]` = Wilder
  ATR14 seeded at i=13 with the mean of the first 14 true ranges (first true range = H-L), Wilder-updated from
  i=14. `ref_mean[i]` = mean of `V[i-30..i-1]`, defined only when `i-30 >= 5` and the mean is finite and > 0.
- Feature unavailable (ATR undefined or 0, std and ATR both 0, ref undefined): no evaluation on that bar
  (`FEATURE_UNAVAILABLE`), state unchanged except timers.
- EMA20/EMA50 are not used. They are logged from i >= 49 (nullable before) for comparison with v1 only.

### 3.2 States and transitions (long side written out; short side in 3.3)
`IDLE -> IMPULSE -> PULLBACK -> RESUMING -> SIGNAL`, with terminal rejections `HIGH_VOLUME_PULLBACK`,
`PVR_NOT_THIN`, `NO_TOUCH`, `TOUCH_TOO_EARLY`, `PULLBACK_TIMEOUT`, `RESUMPTION_TOO_OLD`, `CHASED`,
`SLOPE_TOO_SLOW`, `WINDOW_CLOSED`, all of which go to IDLE on the same bar. Direction is chosen at IMPULSE.

Per-bar processing order, fixed:
1. Bar policy (3.1). If skipped, only timers advance.
2. Restart check: if `H[i] > max(H[i-30..i-1])` (strict; ties do not count) and `C[i] > VWAP[i]` and features
   are available, the symbol (re)enters IMPULSE-long with `impulse_i = i`, `ref = ref_mean[i]` frozen. If the
   bar ALSO makes a strict 30-bar low with `C[i] < VWAP[i]` (impossible; a close cannot be on both sides) or
   is a new high and a new low at once (outside bar), go IDLE (`AMBIGUOUS_BAR`). Restart takes priority over
   any state-specific step on the same bar, including a touch, and it clears HIGH_VOLUME_PULLBACK.
3. State-specific step (one of the following), then at most one further transition on the same bar
   (PULLBACK -> RESUMING -> SIGNAL is the only allowed chain).

- IMPULSE at bar t > impulse_i: touch = the candle intersects the long zone,
  `L[t] <= VWAP[t] + 0.3 std[t]` AND `H[t] >= VWAP[t] - 0.2 std[t]` (bands known at bar t).
  If touch and `t - impulse_i == 1`: `TOUCH_TOO_EARLY`. If no touch and `t - impulse_i >= 20`: `NO_TOUCH`.
  If touch and `2 <= t - impulse_i <= 20`: `touch_i = t`, `leg = impulse_i+1 .. touch_i` inclusive,
  `pvr = mean(V[leg]) / ref`. `pvr >= 1.20` -> `HIGH_VOLUME_PULLBACK` (IDLE until the next restart);
  `0.80 < pvr < 1.20` -> `PVR_NOT_THIN`; `pvr <= 0.80` -> PULLBACK with `low_i = touch_i`.
  The hypothesis measured is "volume of the leg before first touch". `post_touch_vol_ratio` =
  mean(V[touch_i+1..k]) / ref is logged on every later bar and on the signal, and is NOT a gate.
- PULLBACK at bar k > touch_i: if `L[k] < L[low_i]` then `low_i = k` (strictly lower; ties keep the earlier).
  If `k - touch_i > 20`: `PULLBACK_TIMEOUT`. Else if `C[k] > C[k-1]` and `k > low_i`: enter RESUMING and
  evaluate the signal test on this same bar with `age = k - low_i`.
- RESUMING at bar k: if `L[k] < L[low_i]`: back to PULLBACK with `low_i = k` (age resets; no evaluation).
  Else `age = k - low_i`; if `age > 3`: `RESUMPTION_TOO_OLD`. Else evaluate, in this order, logging every
  failed check (multi-reason logging; the funnel counts the FIRST failure):
  1. `C[k] > C[k-1]` else stay RESUMING (`NO_UP_CLOSE`, not terminal).
  2. `slope = (C[k] - C[low_i]) / age / ATR[k] >= 0.50` else stay RESUMING (`SLOPE_TOO_SLOW` logged; terminal
     only when age reaches 3).
  3. `C[k] <= VWAP[k] + 0.5 std[k]` else `CHASED` (terminal).
  4. Bar time in [09:45, 11:30) ET else `WINDOW_CLOSED` (terminal; at 11:30 every active setup is discarded).
  5. Emission enabled (3.4) else `EMISSION_DISABLED` (logged; setup discarded, since the entry bar has passed).
  Pass -> SIGNAL: `entry_ref = C[k]`, stop and targets per 3.5, features frozen at this bar. Then IDLE.
- v1's bounce-bar wick ratio and volume/SMA10 ratio are logged on the signal, never gated.

Earliest possible long signal: `ref` needs volumes 5..34, so `impulse_i >= 35`, `touch_i >= 37`, first
up-close and evaluation at `k >= 38`, the 10:08 bar, which completes at 10:09 ET. The window open of 09:45
is nominal; the card shows "first possible signal 10:09".

### 3.3 Short side, written out
Restart: `L[i] < min(L[i-30..i-1])` strict and `C[i] < VWAP[i]`. Zone touch: `H[t] >= VWAP[t] - 0.3 std[t]`
AND `L[t] <= VWAP[t] + 0.2 std[t]`. PVR identical (volume does not reverse). Pullback extreme:
`high_i = argmax H[touch_i..k]`, strictly higher replaces, ties keep the earlier. Resumption: `C[k] < C[k-1]`;
`slope = (C[high_i] - C[k]) / age / ATR[k] >= 0.50`; no-chase `C[k] >= VWAP[k] - 0.5 std[k]`. Stop above entry
(3.5), T1 below. Ages, budgets, cooldowns, thresholds and validity rules are direction-free. Long and short
share the symbol's daily budget and cooldown.

### 3.4 Emission, budget, cooldown, window
- Budget: `admitted_today` counts signals that passed admission (index filter, and in shadow the virtual
  admission of 6.2). At 2, emission is disabled for the symbol for the day; the machine keeps running.
- Cooldown: after an admitted signal at bar k, emission is disabled for bars k+1..k+15 inclusive; the machine
  keeps running (v1's early return skipped state maintenance; v2 must not).
- Session reset at the first accepted bar of a new ET session date.

### 3.5 Stop and targets (computed once at signal time; the setup invalidation level is separate)
- `invalidation = L[low_i]` (long) is frozen with the signal and is what a live fill is checked against (7.2).
- `dist = max(1.5 x ATR[k] x vix_stop_mult, (entry_ref - L[low_i]) + 0.5 x ATR[k], 0.004 x entry_ref)`;
  `vix_stop_mult` read from the adaptation engine at bar k and logged. Direction validated (long stop below
  entry, short stop above) before any absolute value. `dist > 0.04 x entry_ref` -> `STOP_TOO_WIDE`.
- `stop = entry_ref - dist` (long) / `entry_ref + dist` (short). Signal carries `stop_is_final=True`;
  `calculate_adapted_stop` returns it unchanged (today it multiplies every signal's distance by the VIX
  multiplier; that path is skipped for final stops or the multiplier applies twice).
- T1 = entry_ref +/- 1.0 x dist for `q1 = max(1, qty // 2)`. Runner policy `TRAIL_ONLY`: no T2 order or
  price is created; breakeven after a CONFIRMED T1 fill (complete fill of q1 reconciled from broker events);
  1.5 ATR trail as today; 15:55 flatten as today; the initial stop protects the whole quantity before T1.
  `create_bracket` today always builds a T2 price and id when q2 > 0; the policy field is carried through the
  bracket model, serialization, restore and the simulator.

### 3.6 Relative strength (admission-side feature; a gate only in ablation A4)
- `rs_day = (C_stock[k] / O_stock[0] - 1) - (C_spy[k] / O_spy[0] - 1)`; `rs_30` = 30-bar return difference,
  SPY bar joined on the same completed-bar timestamp. Not available within 90 s, or index feed stale: value
  `null`, logged; in A4 that rejects the signal (`RS_UNAVAILABLE`), never assumed. Same as-of rule for the
  index filter and for the VIX print (value at or before bar k's completion).
- Short-side hypothesis is relative weakness: `rs_day <= 0 and rs_30 <= 0`.

## 4. Pre-registered configurations (the whole search, frozen)

Execution is held fixed across every row: limit at the signal close with 3-bar life, 09:45-11:30, max 2
admitted per symbol-day, the fill model of 5.3, deployed sizing (1% risk x VIX sizing, $25k cap frozen).

| Id | Executable definition |
|---|---|
| A0 | Control: v1 signal rules exactly (EMA20/50, 50-bar warm-up, zone, bounce bar, wick-or-volume), v1 stop and targets. Known: -0.006 +/- 0.019 R (09-27 mitigation) |
| B | Section 3 machine with the PVR gate and the slope gate DISABLED (touch -> PULLBACK regardless of pvr; RESUMING passes on the first up-close within age 1..3 under the no-chase cap). v1 stop and targets. Isolates the new candidate-selection machinery |
| A1 | B + PVR gate |
| A2 | B + slope gate |
| A3 | B + PVR + slope (the v2 signal) |
| A4 | A3 + RS gate (3.6) |
| A5 | A3 + resumption-volume gate `mean(V[low_i+1..k]) >= mean(V[leg])` (an entry-gate ablation) |
| A6 | A3 + final ATR/structure stop (3.5) instead of the v1 stop |
| A7 | A3 + 1.0R half and TRAIL_ONLY runner (3.5) instead of v1 targets |
| V2_FULL | A3 + 3.5 stop + 3.5 targets and runner = the policy that ships in shadow Monday and the only policy that can ever go live |

Ten rows. Thresholds (0.80/1.20, 0.50, age 3, 0.5 std, 20 bars) are fixed from the operator's description,
not from data. "No second run" has one exception: a defect in the implementation of a frozen rule may be
fixed and the whole table rerun once, with the defect and the before/after recorded in `REPORT_v2.md`.

The primary test is one fixed comparison, V2_FULL versus A0, in dollars at deployed sizing. The other rows
are explanation (which piece did what) and cannot qualify anything.

### 4.1 Reporting, per period IS 2024-01..2025-03 / VAL 2025-04..12 / DEV-2026
Trades, trading days with a trade, mean R and SE, net dollars, mean daily dollars and its block-bootstrap
95% CI, max drawdown, turnover, the rejection funnel (every event name in section 3), limit fill rate,
30-minute mark-outs after the signal bar and after the modeled fill (diagnostics with CIs, not a veto), and
a profit-concentration figure that stays defined: gross profit share of the top 5% winning trades.

### 4.2 Statistics, fixed now
- Calendar grid: every trading day in the period where all 12 symbols have data; days with missing data are
  dropped from the grid for all configurations; a day on the grid with no trade contributes $0.
- Statistic: mean daily dollar difference `D = mean_d(P_V2FULL,d - P_A0,d)`.
- Bootstrap: non-overlapping 5-day blocks in period order, 2,000 resamples, seed 20260927, resample length
  = number of blocks, done separately per period. CI = percentile 2.5/97.5 of the recentered D*.
- Historical PASS = lower 95% bound of D > 0 in IS and in VAL separately, AND V2_FULL net dollars > 0 in IS
  and VAL, AND V2_FULL has >= 150 trades and >= 60 trading days with a trade in each of IS and VAL. Fewer
  trades = INCONCLUSIVE (not a pass, not a fail). One run, one seed; the decision is the CI, no p-value near a
  threshold is argued about.
- Ablations: same block bootstrap for each row minus its parent (B-A0, A1-B, A2-B, A3-B, A4-A3, A5-A3, A6-A3,
  A7-A3, V2_FULL-A3), 95% CIs, reported, no qualification role, no multiplicity claim needed since nothing is
  selected from them.
- Execution sensitivity: V2_FULL is rerun under (a) fill only on trade-through by one tick, (b) 1.5 bps cost
  on every limit fill, (c) both together. PASS must hold under (c).
- Minimum detectable effect: computed from A0's per-day dollar standard deviation before the run and written
  into `REPORT_v2.md` next to the result.
- Historical PASS changes nothing about Monday. It records what shadow is expected to show.

## 5. Shadow evidence rule (prospective confirmation)

- Cohort: the first 150 ADMITTED modeled V2_FULL entries after the declared start (Monday's first bar), or
  30 sessions, whichever comes later; analysis once, when every cohort trade is resolved. Counting admitted
  entries (not completed trades) prevents fast winners from filling the cohort first.
- Test: mean daily modeled dollars over the cohort sessions, non-overlapping 5-day blocks, 2,000 resamples,
  lower 95% bound > 0, AND net modeled dollars > 0. Inconclusive at 150: extend once to 300 admitted entries
  and re-test once; then stop (maximum 16 weeks). No daily peeking; the card shows progress, not the test.
- The confirmed policy is V2_FULL exactly as frozen. If the historical study argues for a different primary,
  that is a new plan with a new shadow start, not a relabel.
- Shadow modeled outcomes come from the same module the simulator uses (5.3), so what is measured live is the
  fill model, and its optimism is bounded by 5.3 and calibrated in 7.7 before any broker orders.

### 5.3 Modeled fill and exit rules (shared module `backend/app/research/v2_model.py`, used by sim and shadow)
- Entry: limit at `entry_ref`, may fill only on bars after the signal bar, fills at the limit price only if
  the bar's low is strictly below it (long); quantity assumed full (stated assumption); life = 3 accepted bars.
- Conservative intrabar ordering: on any bar whose range covers the entry limit and the stop, entry then stop
  (a loss); on any bar covering T1 and the stop, stop first; trail updates after the bar's stop check.
- Exits: stop at the stop price minus 1.5 bps (long), T1 at the limit price, trail as the bracket, flatten at
  15:55 at the close minus 1.5 bps. Costs on every exit including flatten. Gaps through a level fill at the
  bar's open.

## 6. Shadow implementation

### 6.1 State ownership
- The machine state (3.2), the shadow ledger (6.2), event watermarks and modeled positions live in
  `research.sqlite3` tables (`v2_state`, `v2_ledger`, `v2_events`, `v2_modeled_trades`), never in the trading
  checkpoint. The strategy object registered under `strategy_id="vwap_pullback"` (the id must not change:
  `restore_runtime_state` raises "Persisted strategy set does not match this deployment" when the id set
  differs, runtime_state.py:239) holds only `version="v2"`, `policy_id`, and a reference to the store.
- Legacy checkpoint state for `vwap_pullback` (v1 `symbol_states`) is dropped on restore with a logged
  migration event; nothing from it is reused.
- Event ids are deterministic `(symbol, bar_ts, event_name, seq)`; writes are idempotent (INSERT OR IGNORE).

### 6.2 Virtual admission (shadow decides its own budget without touching live state)
- Shadow keeps a virtual ledger: 3 virtual slots, 2 per sector, own cooldowns and daily budgets, modeled
  pending entries and modeled positions. It READS live inputs (index filter verdict, SPY bars, VIX print,
  session equity for sizing) and logs the values it read with their timestamps.
- It never calls the live admission path, `engine.create_order`, `engine.submit_order`, the risk engine's
  reservation methods, the bracket manager, or the ledger writers.
- Runtime provenance guard: the final entry boundary in `main.py` refuses any signal with
  `strategy_id="vwap_pullback"` unless `RIDE_THE_TREND_MODE == "v2_live"` AND the activation check (7.8)
  passes. This holds regardless of the path a signal took.
- Backpressure: shadow evaluation runs on the existing research queue thread; live bar handling never waits
  on it. `/health.research` reports queue depth and the age of the last processed bar.

### 6.3 Isolation test (achievable by construction)
- Run the same recorded session twice in simulation, `RIDE_THE_TREND_MODE=off` and `v2_shadow`, with recorded
  SPY bars, VIX prints and equity. Compare a canonical projection of live trading state: positions, orders,
  brackets, live cooldowns, slot and sector counts, risk reservations, daily-loss baseline, ledger rows,
  strategy trade totals, `/api/trades` and `/api/account`. Must be identical. The research store is expected
  to differ and is compared separately against the expected event list for that session.
- Assert the provenance guard rejects a forged `vwap_pullback` signal injected at the entry boundary in shadow.

### 6.4 Restart test
- Replay a recorded session (bars, SPY bars, VIX prints, equity) uninterrupted; then replay with a process
  restart after every bar, restoring from the research store. The `v2_events` and `v2_modeled_trades` tables
  must be identical (deterministic ids make this a set comparison). Historical signals must not re-emit.

### 6.5 Recorder completeness
- One `BAR_EVALUATED` (or `BAR_SKIPPED` with reason) event per (symbol, accepted bar), plus transition events.
  Completeness = evaluated+skipped events / bars received per symbol per session; must equal 1.0. The bars
  received counter is persisted with the events in the same transaction. `/health.research.v2_completeness`.

## 7. Live milestone (broker orders), a separate build; not scheduled

Acceptance criteria that must all be green before `v2_live` can activate:

1. Entry lifecycle: persisted per-entry machine `SUBMITTED -> {PARTIAL, FILLED, CANCEL_REQUESTED, REJECTED,
   BROKER_EXPIRED, UNKNOWN}`; `CANCEL_REQUESTED -> {CANCELLED, PARTIAL_CANCELLED, FILLED, CANCEL_FAILED}`;
   `CANCEL_FAILED -> CANCEL_REQUESTED` (retry with backoff, exposure retained, new entries paused via the
   existing mismatch path) or `-> UNKNOWN`; `UNKNOWN` is resolved only by broker reconciliation. Expiry fires at
   the EARLIER of 3 accepted bars after submission and 4 minutes wall clock from the broker acknowledgement.
   A lost acknowledgement is reconciled by client order id before any resubmission. Restart: reconcile every
   open order and position at Alpaca and cancel overdue entries BEFORE any admission.
2. Exposure accounting: three separate numbers per trade: the frozen `invalidation` level (3.5) that a fill is
   checked against (a first fill at or beyond it exits immediately, `INVALID_FILL_EXIT`), the protective stop
   price (recomputed from the actual average fill by the 3.5 formula), and the risk basis R (frozen when the
   entry reaches a terminal state). Scale-out (T1) and breakeven are enabled only after the entry is terminal
   (FILLED, CANCELLED or PARTIAL_CANCELLED); the initial stop protects every filled share before that.
3. Any exit transition (stop, trail, flatten, halt, mode change) first cancels and reconciles the working
   entry; exposure is closed only when the entry is terminal and the position is zero. Tests: entry partial
   then stop then late entry fill; T1 partial; one-share position; simultaneous stop/T1/flatten.
4. Reservations: slot, sector, symbol ownership, buying power and risk are reserved at submission, transferred
   to the position atomically on each fill, and released only for the unfilled part on cancel. Working entries
   count as slots. `symbol_to_bracket` rejects a second bracket per symbol.
5. Order-type guard: v2 entries must be LIMIT with a valid price at the final boundary; anything else is
   rejected (today's expression defaults unknown types to MARKET).
6. Mode changes, the daily loss stop and a graceful shutdown cancel-and-reconcile working entries and keep
   exits and flattening running. A crash cannot cancel anything; the restart reconciliation in 7.1 covers it,
   and the protection gap while the process is down (stops are local, none rests at Alpaca) stays on the card.
7. Execution calibration: at least 20 shadow signals replayed against recorded paper NBBO show a median
   modeled-vs-executable fill difference under 2 bps, or the fill model is tightened before activation.
8. Activation check: `v2_live` activates only if `RIDE_THE_TREND_LIVE_APPROVAL` equals the SHA-256 of
   (policy manifest for V2_FULL + evaluator source + `v2_model.py` source + symbol universe). Any change to
   those files changes the hash and drops the bot back to shadow with a logged reason.
9. Combined-portfolio replay with the other arms (deterministic event order: bars processed by timestamp then
   symbol; arm priority tri-engine, then others by signal time), working-order reservations, the TSLA/CDE
   exclusion and the daily loss stop (realized P&L only, as today). Acceptance: the portfolio's mean daily
   dollars with v2 minus without v2 has a lower 95% block-bootstrap bound above -$50 (it must not hurt the
   others) in addition to the section 5 result.

## 8. Build steps for Monday (shadow), each with its test

1. `VWAPPullbackV2` evaluator as a pure function of (accepted bars, features, config) in
   `backend/app/strategies/vwap_pullback_v2.py`; the strategy wrapper registered under `vwap_pullback`.
   Tests: constructed bar sequences for every transition and every event name in section 3 (long and short),
   duplicate/late/zero-volume/gap bars, ATR=0 and std=0, outside bar, restart on the touch bar, first signal
   at index 38, prefix invariance (events for the first N bars unchanged after appending 50 more).
2. Research store tables and idempotent writer; migration drops legacy v1 state on restore (test on the
   09-24 checkpoint format and on the current one).
3. Shadow virtual ledger (6.2) and provenance guard (6.2); isolation test (6.3); restart test (6.4);
   completeness (6.5).
4. `v2_model.py` shared fill/exit model with the ordering rules of 5.3; unit tests for each ordering case.
5. Card and API: `/api/strategies` entry carries `version`, `mode`, `policy_id`, `last_evaluated_bar`,
   `first_possible_signal="10:09"`, `today_counts`, `cohort_progress` ("N of 150"). Grep the UI for old labels
   and the old 09:30-15:45 window text.
6. Simulator: `sim_v2.py` imports the same evaluator and `v2_model.py` (parity by construction for fills and
   exits); `configs_v2.py` holds the ten rows verbatim; `parity_v2.py` compares the evaluator's event stream
   against the bot's shadow path on constructed cases for every branch plus any observed populated
   symbol-days (no minimum count required). Run the table; write `REPORT_v2.md` with funnel, CIs and MDE.
7. Deploy Sunday evening. Verify `/health`, `/api/strategies` shows v2 shadow with policy id, Railway logs on
   Monday for `error on bar`, `v2 BAR_EVALUATED`, completeness 1.0, `/health.research.errors == 0`.

## 9. Rollback
`RIDE_THE_TREND_MODE=off` on Railway. Shadow holds no positions or orders, so rollback is a config change.

## 10. Codex triage

Round 1 (32 findings, 9 P0): all accepted; the v2 draft rewrote the plan around them (lifecycle to a separate
milestone, stop computed once, versioned state, shadow isolation, LIMIT guard, causal machine, ablations,
block bootstrap, periods relabeled development, mark-outs demoted, VPR dropped).

Round 2 (22 new, 4 P0, all on the live milestone): accepted and folded in as follows. P0 1-4 -> 7.1-7.4
(entry cancelled on every exit transition, three separate exposure numbers, expiry at the earlier clock,
CANCEL_FAILED/UNKNOWN transitions, reservation transfer). P1 5-7 -> 4.2 (dollar statistic, one fixed primary
V2_FULL vs A0, ablations cannot qualify). P1 8 -> section 4 manifest with B and V2_FULL, executable rows.
P1 9, 11, 12, 13 -> 3.1-3.3 (bar policy, processing order, priorities, timeouts, intersection zone test,
shorts written out, index 38 / 10:09). P1 10 -> hypothesis renamed to "leg before first touch",
`post_touch_vol_ratio` logged. P1 14-16 -> section 6 (own store, virtual ledger, projection comparison,
restart test with recorded inputs, legacy state dropped). P1 17 -> section 5 (admitted cohort, one analysis,
one extension, maximum duration, MDE). P1 18 -> 5.3 and 7.7. P1 19 -> 4.2 (grid, blocks, seed, per-period).
P1 20 -> 6.5 and 8.6 (constructed cases, no minimum count). P1 21 -> 7.8 and 7.9. P2 22 -> labels.

Rejected: none. Round 2's verdict that the P0s "are not claims that shadow mode has submitted orders" is
why Monday ships shadow and the live milestone waits.

Open for the operator: (1) confirm TSLA and CDE stay excluded from v2 in every mode; (2) whether to build the
live milestone (section 7) before the shadow evidence exists. Recommendation: no; let shadow run.
