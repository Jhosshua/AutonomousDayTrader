# PLAN 2026-09-27: Ride the Trend add-ons, live from day one (session delta, spread proxy, volume profile)

Current audit corrections and release evidence: [AUDIT_2026_09_27](docs/ride_the_trend_v2/AUDIT_2026_09_27.md). The historical build notes below describe the earlier revision.

Status: v2, BUILT 2026-09-27 night after the Codex attack (21 findings, 6 P0; `docs/ride_the_trend_v2/codex_attack_addons.md`).
Operator decision: all three live as ENFORCED, fail-closed gates, no recorded-only period. Builds on the live v2 + part-2 build (commits ef789a3, 87d6022, 7142f41).
Strategy: `vwap_pullback` (Ride the Trend v2), evaluator `backend/app/strategies/vwap_pullback_v2.py`,
tape `backend/app/core/tick_tape.py`. Bot: Alpaca paper, Railway, push to main = redeploy.

## 1. Add-on A: session cumulative delta ("pseudo-L2")
- Data: the tape already tags every eligible print buyer-aggressive (at the ask), seller-aggressive (at the
  bid), tick-rule inferred, or unknown. New: per-symbol running sums since the session's first event
  (buy, sell, unknown, quote-classified volume, print count), independent of the 60-minute raw retention.
- Validity: the running total is trustworthy only while the feed has been continuous. On a stock-feed
  outage the sums restart from the reconnect (`since_ns` moves), and the gate is unavailable until 30
  minutes of continuous feed exist again. Quote-classified share since `since_ns` must be >= 20% and
  classified share >= 50% (the same floors as the window delta).
- Gate `SESSION_DELTA` at the resumption test: long requires cumulative signed volume since `since_ns` > 0
  (net buying on the day so far); short requires < 0. Reject `SESSION_DELTA_AGAINST`; unavailable ->
  `TICK_UNAVAILABLE where=session_delta`.
- Recorded on `RESUMPTION_MEASURED` and the signal: `session_delta`, `session_delta_ratio`, `since`,
  `minutes_continuous`, `quote_share`. Card: no new line (the wind and macro lines stay).

## 2. Add-on B: bid-ask spread proxy
- Data: every NBBO quote; per second the tape keeps the last bid and ask. New: per-second spread in basis
  points of the mid, and two rolling statistics per symbol: `spread_ref` = median of the per-second spread
  over the trailing 30 minutes (needs >= 300 covered seconds, else unavailable) and `spread_now` = median
  over the last 10 seconds (needs >= 3 quotes in the window).
- Measured 2026-09-23..25 at the 22 recorded setups: median ratio spread_now / spread_ref = 1.00, worst
  1.68 (PLTR 09-23, three of its evaluations at 1.57 to 1.68). Morning medians: AMZN 1.6-2.0 bps,
  GOOGL 1.8, META 3.7, PLTR 4.7, AMD 6.7, COIN 11.4.
- Gate `SPREAD` at the resumption test: `spread_now / spread_ref <= 1.5` AND `spread_now <= 30 bps`
  (absolute cap, above COIN's normal 11 bps with room). Reject `SPREAD_WIDE`; unavailable -> `TICK_UNAVAILABLE
  where=spread`.
- Recorded: `spread_now_bps`, `spread_ref_bps`, `spread_ratio`. Health: per-symbol spread_ref.

## 3. Add-on C: volume profile from prior sessions (high-volume nodes)
- Data: 1-minute bars of the previous 5 regular sessions per tradable symbol. Live source: the relay's
  historical bars endpoint (`RELAY_HTTP_URL/data/v2/stocks/bars`, the same relay and token the bot already
  uses), fetched at startup and at each session boundary for the 9 tradable symbols (about 5 x 390 bars
  each). Replays use the cached bars in `research/vwap_trend_2026_09_27/data/bars/`.
- Build (`backend/app/core/volume_profile.py`): bucket width = 0.10% of the symbol's last close (so about
  $0.34 on a $340 stock); each bar's volume is spread evenly over the buckets its high-low range touches;
  buckets summed across the 5 sessions. A high-volume node (HVN) = a maximal run of adjacent buckets each
  holding >= 1.5 x the median non-empty bucket volume; each node has `low`, `high`, `volume`,
  `peak_price`. Profiles are rebuilt only at the session boundary (prior sessions do not change intraday).
- Validity: a profile built from fewer than 3 sessions, or older than the current session date, is
  unavailable -> `PROFILE_UNAVAILABLE` (fail closed).
- Gates at the resumption test (long; shorts mirror with nodes above / below swapped):
  - `HVN_SUPPORT`: the pullback extreme (lowest low of the leg) lies inside or within 0.25 ATR below /
    0.50 ATR above some HVN, i.e. the dip landed on prior liquidity. Reject `HVN_NO_SUPPORT`.
  - `HVN_OVERHEAD`: no HVN whose `low` is above the entry and within 1.0 ATR of it (the first target is 1R
    away; a node inside that distance is where the runner would stall). Reject `HVN_OVERHEAD`.
- Recorded: `hvn_support` (node low/high/volume or null), `hvn_overhead` (nearest node above), `profile`
  (sessions used, bucket width, node count). Health: per-symbol profile sessions and build time.

## 4. Order of gates at the resumption test (unchanged ones first)
up close -> slope -> no-chase -> tick velocity -> book -> (part-2 measures, enforced only if named) ->
SESSION_DELTA -> SPREAD -> HVN_SUPPORT -> HVN_OVERHEAD -> window -> emission -> stop.
Every value is computed once into the `RESUMPTION_MEASURED` snapshot before any gate decides.

## 5. Configuration
`RIDE_THE_TREND_ADDONS_ENFORCED: bool = True` (operator switch for all three), thresholds in `V2Params`:
`spread_ratio_max=1.5`, `spread_abs_max_bps=30`, `session_delta_min_minutes=30`, `hvn_bucket_pct=0.001`,
`hvn_sessions=5`, `hvn_min_sessions=3`, `hvn_threshold_x_median=1.5`, `hvn_support_below_atr=0.25`,
`hvn_support_above_atr=0.50`, `hvn_overhead_atr=1.0`. Events added to the funnel and the decision outcomes.

## 6. Build steps (each with tests)
1. Tape: session sums with outage reset and `session_delta(symbol)`; per-second spread bps;
   `spread_stats(symbol, t1_ns)` with reference and now medians and coverage. Tests: sums across quotes and
   prints, reset on outage, quote-share floor, spread medians on sparse and dense quotes, unavailable cases.
2. `volume_profile.py`: builder from bars, node detection, `support_node(price, atr)` and
   `overhead_node(price, atr, is_long)` queries; fetcher via the relay with retries; staleness. Tests:
   constructed bars with a known node, bucket spreading across a range, fewer than 3 sessions, stale profile,
   short mirror.
3. Evaluator: snapshot fields and the four gates; events; features. Tests: pass/fail/unavailable per gate,
   shorts, ordering (a spread failure is reached only when the earlier gates passed).
4. `main.py`: profile store built at startup and at the session boundary from the relay (background task,
   never blocks the bar loop), module hook `PROFILE` for the evaluator, health, decision outcome texts,
   `RIDE_THE_TREND_ADDONS_ENFORCED`. Tests: gate wiring through a fake full session (profile injected).
5. Replay of the three cached sessions with profiles built from the five sessions before each; funnel with
   the new events; the fake full session through main proving an order still goes through with all gates
   passing.
6. Codex review of the diff, fixes, full suite, deploy, verify health (`ride_the_trend.profiles`,
   `spread_ref` per symbol) and the card.

## 7. Assumptions to attack
- Net session buying since 09:30 is the right sign test for a long (versus the last-30-minute rolling delta
  already recorded); an outage resets it rather than invalidating the day.
- A 30-minute trailing median is a fair "normal" spread; 1.5x and 30 bps are the right lines given the
  measured 1.00 median and 1.68 worst ratio.
- Spreading a bar's volume evenly across its range approximates the intrabar profile well enough for 0.1%
  buckets over 5 sessions; 1.5x the median bucket is a node.
- The relay's historical bars endpoint is reachable from Railway with the bot's token at startup.
- Adding four more fail-closed gates to a strategy that produced zero signals on three replayed mornings is
  the operator's explicit choice.

## 8. As built (changes from the draft, all from the Codex attack)
- Session delta: per-minute aggregates keyed by ET session, queried over [since, bar end) so no later print
  can reach an earlier decision; `since` restarts on reconnect and on point gaps; `partial` flagged when the
  total does not start at the session's first event; needs 30 minutes of continuous feed and the feed caught
  up to the cutoff. Effective earliest signal is therefore about 10:00 ET.
- Spread: the spread in force at the start of each second, carried forward up to 30 s; locked markets
  ignored; crossed quotes invalidate classification and the quote history until a valid quote arrives.
- Volume profile: the overhead test intersects nodes with the real entry-to-first-target road (computed with
  the stop rule) and excepts the node the dip landed on; a node counts when it covers at least 10% of the
  road. Profiles are valid only for the exact session they were built for; bars from the target session
  never enter them.
- Transitions: SESSION_DELTA_AGAINST, SPREAD_WIDE, HVN_OVERHEAD keep the setup for the next bar (age limit
  still applies); HVN_NO_SUPPORT, PROFILE_UNAVAILABLE and TICK_UNAVAILABLE discard it.
- Live profiles: fetched from the relay's history endpoint as an asyncio task at startup and at each session
  boundary (bounded requests, per-symbol errors in `/health.ride_the_trend.profiles`); the card blocks new
  trades while no profile exists. Replays build the same profiles from the cached bars of the five sessions
  before the replay day.
- Operator switch: `RIDE_THE_TREND_ADDONS_ENFORCED` (default true).

## 9. Codex code review and known limits
Review with triage: `docs/ride_the_trend_v2/codex_addons_code_review.md` (30 findings, 10 P0, all P0s and most P1s
fixed before commit). Remaining limits: one-second resolution for the spread and book states; a late print can
change an earlier minute for later queries; the replays produced no signal on the three cached mornings, so the
joint pass of every enforced gate is demonstrated by the fake full session only; with every gate enforced,
Monday's first live signal is the first end-to-end proof.
