# PLAN 2026-09-27: Ride the Trend data layers, part 2 (raw feed, real-time delta, macro/correlation)

Status: v3, BUILT 2026-09-27 night (recorded-only rollout; every gate promotable by config). Plan attack:
`docs/ride_the_trend_v2/codex_attack_data_layers_2.md`; code review: `docs/ride_the_trend_v2/codex_part2_code_review.md`;
step-0 diagnostics: `docs/ride_the_trend_v2/step0_diagnostics_2026-09-27.md`. Scope = items 1, 3 and 4 of the
operator's four-item data list. Item 2 (full order-book depth) is deferred pending the vendor decision.
Builds on the live v2 build: `backend/app/strategies/vwap_pullback_v2.py`, `backend/app/core/tick_tape.py`,
`backend/app/core/macro_calendar.py`.

## 0. What changed after the attack (accepted findings)
- Absorption as a veto is dropped: with the slope gate in place (price must have moved at least 0.5 ATR
  per bar off the low) a "price did not move" test can never fire. Absorption becomes a research feature
  on its own interval (P0-1).
- Two live bugs found in the tape are fixed and deployed with this plan: a late second was never re-sorted
  (P1-10), and the book gate was weighted by quote count, so a burst of repeated updates outweighed a
  longer quiet state; it is now time-weighted per second (P0-8).
- Classified volume is reported in three parts: quote-classified, tick-rule inferred, unknown. Gates
  require quote-classified share >= 0.5, not "anything but unknown" (P0-3).
- Every raw-store query returns coverage (window requested vs window actually held, eviction, gaps) and a
  gate refuses an incomplete window; the cap is sized from measured per-symbol peak rates before deploy
  (P0-4, P1-9, P1-25).
- Cumulative delta "since 09:30" is marked unavailable after a restart or feed gap; the gate uses a rolling
  30-minute difference over a complete window only (P0-6).
- The raw record keeps the provider trade id, conditions and exchange so duplicates, corrections and
  ineligible prints are handled (P0-7). Late prints are classified against the retained quote history
  (P0-2), and receive order is stored next to event time (P0-5, P1-11).
- Regime dependency is per candidate: the stock's own sector ETF plus UUP and IEF. SHY is logged only.
  An unrelated ETF outage no longer vetoes every trade (P1-21). UUP prints sparsely (19 bars in the first
  35 minutes on 09-25), so its rule uses the last bar within 5 minutes and a 30-minute return over wall-clock
  endpoints, not bar counts (P1-20).
- Naming is honest: "ETF price filters" (UUP, IEF, SHY), "sector direction and relative-return filter";
  no correlation, intent or spoofing claims (P1-15, P1-18, P1-19).
- Rollout: data-integrity failures stay fail-closed. The NEW alpha gates (impulse aggression, resumption
  aggression, cumulative delta, sector relative strength, dollar and rates wind) are computed and recorded
  on every candidate first, then promoted to gates one at a time on measured conditional rejection rates.
  Reason: the live strategy produced zero signals on three replayed sessions with the gates it already has;
  stacking more mandatory vetoes cannot raise that and would hide which rule is doing the work (P1-12,
  P1-22, P1-24). The operator can override and switch every gate to mandatory on day one via config.

## 1. Decisions taken (operator may overrule)
1. Dollar and yields via ETF price proxies on the existing feed (UUP, IEF; SHY logged). Real DXY and
   cash yields need a vendor; out of scope.
2. Sector ETFs: XLK (AAPL, NVDA, AMD, MSFT, PLTR), XLC (META, GOOGL), XLY (AMZN), XLF (COIN); bars only.
   A beta-adjusted alternative (stock return minus beta x ETF return, 20-day beta) is logged beside the raw
   difference for the ablation.
3. Raw prints kept in packed numpy arrays (int64 ns, float64 price, int32 size, int8 side, int64 trade id,
   int8 flags: about 30 bytes a print) for a rolling 60 minutes; cap set from the measured peak (NVDA on
   09-23 is the benchmark day) with headroom; coverage reported on every query.
4. Gate promotion order after the measurement period: resumption aggression, impulse aggression, sector
   direction, dollar/rates wind, cumulative delta, sector relative strength.

## 2. Item 1: the raw feed
`TickTape` adds a per-symbol raw print store beside the second buckets: arrays `ts_ns`, `recv_seq`, `price`,
`size`, `side` (+1 quote-ask, -1 quote-bid, +2/-2 tick-rule up/down, 0 unknown), `trade_id`, `flags`
(conditions: regular, odd lot, late, auction, corrected). A small quote history (last 2 seconds) allows a
late print to be classified against the quote that was in force at its timestamp. Queries are half-open
on nanoseconds and return `{value, coverage: {requested, held, gaps, evicted}}`:
`prints(symbol, t0, t1)`, `delta(symbol, t0, t1)`, `velocity(symbol, t1, window_s)`,
`rolling_delta(symbol, t1, window_s)`. Duplicated trade ids are ignored; a correction replaces its print.
Health: per-symbol prints retained, usable interval, oldest age, evictions, quote-classified share today.

## 3. Item 3: real-time delta
Stream: per-symbol signed volume by second (quote-classified and tick-inferred separately) and a rolling
30-minute difference computed only when the window is complete.

| Measure (long side; shorts use the direction multiplier, expanded inequalities in the code and tests) | Window | Rule | Event |
|---|---|---|---|
| Impulse aggression | the impulse bar | delta ratio >= +0.15 | `IMPULSE_NOT_AGGRESSIVE` |
| Pullback aggression (live) | first leg bar start .. extreme bar end | delta ratio >= -0.30 | `AGGRESSIVE_PULLBACK` |
| Resumption aggression | extreme bar end .. evaluated bar end | delta ratio >= +0.10 | `RESUMPTION_NOT_AGGRESSIVE` |
| Rolling delta trend | last 30 wall-clock minutes, complete | signed volume > 0 | `CUM_DELTA_AGAINST` |
| Absorption (research only) | resumption window | delta ratio and price advance recorded, no veto | none |

Unavailable (incomplete window, quote-classified share < 0.5, no prints) => `TICK_UNAVAILABLE`, always
fail-closed. Impulse/resumption/rolling start as recorded measures (section 0) unless the operator sets
`RIDE_THE_TREND_ENFORCE_ALL=true`.

## 4. Item 4: macro and correlation feed
Config `REGIME_SYMBOLS = ["XLK", "XLC", "XLY", "XLF", "UUP", "SHY", "IEF"]` subscribed for bars only
(verified 2026-09-27: the relay serves 1-minute bars for all seven). `RegimeFeed` keeps session bars,
anchored VWAP and wall-clock returns per symbol with explicit bar-start, bar-complete and receipt times.

| Filter (long side) | Rule | Event |
|---|---|---|
| Sector direction | the candidate's sector ETF last completed bar (within 2 minutes of the stock bar) closes above its VWAP | `SECTOR_AGAINST` |
| Sector relative return | stock 30-minute return minus sector ETF 30-minute return over identical wall-clock endpoints >= 0 | `SECTOR_RS_FILTER` |
| Dollar wind | UUP 30-minute return <= +0.25% (last bar within 5 minutes) | `MACRO_WIND_AGAINST: dollar` |
| Rates wind | IEF 30-minute return >= -0.25% | `MACRO_WIND_AGAINST: yields` |
| Dependency | only the candidate's sector ETF, UUP and IEF must be fresh; otherwise | `REGIME_UNAVAILABLE` |

Thresholds are provisional. Before promotion, the 30-minute return distributions of UUP and IEF by time
of day are measured on the cached sessions and the thresholds set at a stated percentile.
The SPY/QQQ index filter, VIX regime, SPY relative strength and the macro calendar stay as they are.
Card: a "Wind" sentence in plain words; research rows carry every value; `/health.ride_the_trend.regime`
shows each symbol's usable interval and last bar age.

## 5. Frequency check before building gates
The three replayed sessions show 347 impulses, 10 pullbacks, 13 resumptions, 0 signals with today's gates.
Codex's point stands: the slope gate (>= 0.5 ATR per bar off the low) and the no-chase cap (<= 0.5 std above
VWAP) can leave no feasible entry price, and a resumption that makes a new 30-bar high restarts the machine
before the entry test. Step 0 of the build measures, per resumption: the feasible price interval, which gate
rejected, and how many resumptions were pre-empted by a restart. That report goes to the operator before
any new gate is added; loosening or keeping those two rules is the operator's decision.

## 6. Build steps (each with tests)
0. Funnel diagnostics on the three cached sessions (section 5) and per-symbol print-rate measurement for the
   cap. Report to the operator.
1. Raw print store with coverage, quote history for late prints, trade ids and conditions, packed arrays,
   benchmark of RSS and ingest throughput at the 09-23 NVDA rate.
2. Delta measures recorded on every candidate (features + setup events); gates behind config, each
   individually promotable.
3. `RegimeFeed`, split subscriptions (tradable symbols: bars+quotes+trades; regime symbols: bars), per
   candidate dependency, wall-clock returns, UUP sparse-bar rule; measures recorded in admission stages.
4. Card wind line, health, research rows; policy id bumped so old and new rows are distinguishable.
5. Real-data replay of the three sessions with ETF bars fetched from the relay; one-gate-at-a-time ablation
   table (candidates, conditional rejection rate); fake full session through main with all measures present.
6. Codex review of the diff, fixes, full suite, deploy; verify health, card, research rows.

Effort: about three days for steps 0 to 4, plus the measurement period before promotions. Railway: measured
in step 1, not assumed.

## 7. Not in this plan
Order-book depth beyond the NBBO (vendor decision), real DXY and cash yields, any change to entries, exits,
sizing, windows or exclusions.

## 8. As built (2026-09-27 night)
- `backend/app/core/tick_tape.py` (redesigned after Codex round 1): one `SecondBucket` per symbol per
  exchange second holding that second's prints in packed `array.array` columns plus its quote aggregates;
  retention (60 minutes) and the 1,000,000-print cap evict whole seconds from the front, so nothing shifts
  and no window is split by an eviction. Coverage on every query: complete only when the window starts
  after the held data, the feed watermark has reached the window end (5 s slack) and no outage interval
  overlaps it. Time-bounded quote history (10 s) classifies late prints against the quote in force at
  their own timestamp; late prints never touch the live tick state; tick direction is tracked separately
  from the aggressor side. Trade-id de-duplication per second; ineligible sale conditions counted, never
  stored. `delta()` reports quote-classified and tick-inferred volume separately, `rolling_delta()` needs a
  complete window, `velocity()` runs on the raw prints, `book_imbalance()` is time-weighted per second with
  carry-forward (30 s) and needs 80% coverage.
- Data-quality floor set FROM THE DATA, not the plan: 25% to 51% of volume prints at the bid or ask on the
  live feed (the rest prints inside the spread), so `delta()` requires classified >= 50% and quote-classified
  >= 20%. The plan's 50% quote floor would have failed every pullback check closed.
- `backend/app/core/regime_feed.py`: seven ETFs, bars only, wall-clock 30-minute returns, UUP 5-minute
  freshness, per-candidate dependency (own sector + UUP + IEF), `evaluate()` and the card's wind sentence.
- Evaluator: impulse delta at IMPULSE, one measurement snapshot per resumption evaluation (queried once,
  complete-and-finite only, exceptions become unavailable) recorded as `RESUMPTION_MEASURED` before any gate
  decides, then the same snapshot feeds the gates; Layer 4 evidence recorded in the same event at decision
  time; gates `IMPULSE_DELTA`,
  `RESUMPTION_DELTA`, `ROLLING_DELTA` enforced only when named in `RIDE_THE_TREND_ENFORCED_GATES`
  (or `RIDE_THE_TREND_ENFORCE_ALL=true`); the live pullback delta gate now requires a complete window.
- `main.py`: regime feed on every regime bar, `_ride_the_trend_regime` in admission (recorded in the research
  stages; `SECTOR_DIRECTION`, `SECTOR_RS`, `DOLLAR_WIND`, `RATES_WIND` enforced only when named), gap marks
  from stock-feed status changes, trade ids and conditions passed to the tape, `/health.ride_the_trend.regime`
  and `enforced_gates`, card wind line. Subscriptions: regime ETFs get bars only.
- Policy id `V2_FULL_L2_2026_09_28`.
- Step-0 diagnostics (three real sessions): 22 resumption evaluations, 14 with no feasible entry price under
  the existing slope-versus-chase rules, 1 restart pre-emption; would-fail counts per measure are in the
  diagnostics file. Nothing is enforced until the operator picks thresholds from more sessions.
- Codex code review round 1 (34 findings, 13 P0) drove the per-second redesign, completeness on every
  gate, the snapshot, regime hardening (UTC storage, validation, session filter, as-of VWAP, strict shorts,
  fresh endpoints), outage intervals, startup validation of gate names. Round 2 review:
  `docs/ride_the_trend_v2/codex_part2_code_review_round2.md`.
- Replay cost with the raw store: 18 to 33 s per real morning (about 4.7M events), 1.1M prints retained.

## 9. Known limits after two Codex rounds (acceptance criteria before any gate is promoted)
- The feed watermark cannot prove the trade tail of a window is complete; the 5-second slack is an
  allowance, not a wait. Before promoting a delta gate, measure the trade-arrival lag on the deployed host.
- A late print does not repair the tick classification of prints already stored after it; prints more
  than 30 s behind the watermark are stored unclassified.
- Book state is per second (the quote in force at the start of each second); sub-second boundaries are not
  answerable. Crossed quotes do not invalidate the quote history.
- The regime evidence recorded on `RESUMPTION_MEASURED` and the admission-time evaluation are two calls;
  they can differ if a regime bar lands between them (both are recorded in the research rows).
- `notify_admitted` carries no signal identity; a duplicate notification would double-count the budget.
- Three real-session replays produced no signal, so the real-tick path to an order is proved only by the
  fake full session; the first live order will be the first end-to-end proof.
