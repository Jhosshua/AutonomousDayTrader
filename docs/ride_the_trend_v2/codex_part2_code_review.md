# Codex code review round 1 of data layers part 2 (2026-09-27)

Model gpt-6-astra, reasoning xhigh, read-only, no tools; diff + three core modules + replay diagnostics inlined.

I’ll review only the inlined code, without tools or files, and separate confirmed defects from risks the excerpt cannot establish.
The supplied code lets required velocity checks accept incomplete windows, and it labels partial tape coverage as complete in other cases. The new recorded measures also run outside the measurement exception handler, so leaving their gates disabled does not isolate them from order decisions or bar-processing failures.
The replay’s sector-direction results use the final replay VWAP for earlier decisions, so those diagnostics contain look-ahead. The post-replay quote-share query also requests 105 minutes from a store retaining only 60. All three sessions emitted zero signals, leaving the real-data admission path unexercised.
I’m separating confirmed defects from unsupported suspicions. Locked quotes correctly fall back to the tick rule, and the two impulse transitions are internally consistent. The remaining blockers include state corruption after failed array writes, noncausal classification of late prints, incomplete book windows, and synchronous array copying on every eviction once the store fills.
1. P0 — `vwap_pullback_v2.py::evaluate_bar`: velocity accepts incomplete windows.  
   `TickTape.velocity()` can return a plausible velocity with `complete=False`; the evaluator checks only existence and finiteness. A disconnect during the resumption can therefore pass this required gate, even when the separately checked pullback window is complete. Fix: require `complete is True` before using velocity; reject with its coverage reason.

2. P0 — `tick_tape.py::coverage_of`: “complete” does not establish coverage through the window’s end.  
   It checks the left boundary and gap marks, but no ingestion progress, connection interval, or right boundary. Old prints can make a later, partially received window “complete.” This affects impulse, pullback, resumption and rolling delta; checking the returned flag does not solve it. Fix: track independently established feed coverage and processing progress, including an explicit lateness policy. A quote watermark alone does not prove all trades arrived.

3. P0 — `RawStore._drop_front` / `coverage_of`: cap eviction can silently lose prints inside a supposedly complete window.  
   If the cap splits several prints sharing timestamp `T`, retained and discarded prints both have timestamp `T`. A query beginning at `T` reports complete despite missing volume. Fix: retain a monotonic loss boundary recording the last discarded timestamp; conservatively invalidate windows including that timestamp, or evict entire timestamp groups.

4. P0 — `TickTape.book_imbalance`: a required 30-second gate can pass on one second of quotes.  
   It requires neither sufficient window coverage nor absence of feed gaps. One recent bid-heavy bucket can pass a long while the rest of the window is missing. Its freshness check validates only the newest quote. Fix: return quote-coverage metadata and require adequate, uninterrupted coverage in the evaluator.

   The weighting is also wrong for the stated measure: seconds without updates disappear. A book that persists for 27 seconds receives the same contribution as a book represented by one updated second. Fix: carry valid quotes forward across seconds, apply a defined stale limit, and weight by covered duration.

5. P0 — `TickTape.on_trade`: late-print tick classification is noncausal.  
   `_last_price` and `_last_side` follow receive order, while stored prints follow exchange time. A trade at timestamp 102 received before a late trade at 101 classifies the latter using a future price. The late trade then overwrites the reference used for subsequent trades. A query ending before 102 can consequently contain signs derived from information outside its window. Fix: define and enforce an event-time ordering/lateness policy; classify finalized prints against their chronological predecessor. Do not let an old insertion overwrite the current chronological tick state.

6. P0 — `RawStore.insert` / `main.py::handle_trade_event`: failed writes can permanently corrupt column alignment.  
   Appends happen sequentially without range validation or rollback. For example, a size outside signed `array("i")` range raises after timestamp, sequence and price have already been appended. An overflowing packed trade ID fails later still. The handler logs the exception and continues using the damaged store. Subsequent queries can misassociate prices, sizes and sides or raise `IndexError`.  
   Fix: validate every packed value before mutation, make insertion transactional, and quarantine/invalidate the symbol on any partial-write failure. A logged exception is insufficient.

7. P0 — `evaluate_bar` / `main.py::_ride_the_trend_regime`: recorded-only work can interrupt trading.  
   The measurement handler catches only its first batch of tape calls. Impulse queries are unprotected; resumption and rolling queries run again, unprotected, even with their gates disabled. Extracting measurement dictionary fields also happens outside the handler. Regime evaluation is unconditional once admission reaches it. These failures propagate after strategy bars/features/state may already have changed; retrying that bar then encounters duplicate-bar handling.  
   Fix: compute validated results once. Optional measurement failures should produce explicit unavailable records; required-data failures should produce controlled rejection. Neither should escape with a partially transitioned strategy.

8. P0 — `regime_feed.py::_Series.add`, `close_at_or_before`, `health`: timestamp normalization is discarded.  
   `add()` creates an aware local `ts` but stores the original `bar`. A naive first bar subsequently causes aware/naive comparisons or subtraction to raise. This can break regime ingestion, admission and health endpoints even in recorded-only mode.  
   Fix: store canonical UTC-aware timestamps and normalize query times at the API boundary.

9. P0 — `RegimeFeed.on_bar` / `evaluate`: malformed bars can poison enforced gates or crash ingestion.  
   There is no finite/OHLC/volume validation. With NaN prices, return comparisons are false, so sector-RS and macro gates can report neither failure nor unavailability. NaN VWAP produces `above_vwap=False`, which passes shorts. Invalid volume conversion can raise before strategy evaluation.  
   Fix: validate bars before changing a series; require finite, valid inputs and outputs for every regime measure. Invalid enforced data must produce `REGIME_UNAVAILABLE`.

10. P0 — `RegimeFeed.direction`: historical sector direction uses future VWAP.  
    The close is selected as of `t_end`, but `s.vwap` includes every accepted bar. Post-replay diagnostics therefore compare morning prices with final-replay VWAP. The live path has the same problem if an ETF has advanced beyond a delayed stock signal’s timestamp. With sector direction enforced, this can change admission.  
    Fix: store cumulative price-volume and volume at each bar and retrieve both at the selected historical index.

11. P0 — `evaluate_bar` / configuration: explicitly enforced tape gates can be disabled by `require_tick_layers=False`.  
    All three part-2 tape gates are conditional on `tick_gates`. Thus `ENFORCE_ALL=True` does not actually enforce all gates when the legacy tick switch is false.  
    Fix: separate measurement collection, required gates and restore mode. Reject contradictory live configuration, and prohibit replay mode from emitting orders.

12. P0 — `RawStore.insert`, `_drop_front`, `trim`: the bounded store has an unbounded-cost ingestion path at production scale.  
    Late insertion shifts seven arrays. More importantly, once retention or the cap begins evicting individual prints, front deletion shifts the surviving arrays on ordinary trades too. At one million prints, this is roughly 38 MB of packed columns moved per one-record eviction. This synchronous work runs in the ingestion path and can starve bar processing and order management.  
    Fix: use chunked storage or a ring/head-offset design with amortized compaction and a bounded late-arrival structure. Validate sustained throughput after the store fills.

13. P0 — `RawStore.seen_ids`: the stated storage estimate omits the dominant memory risk.  
    The columns total approximately 38 bytes, not 30: about 418 MB across 11 capped symbols, before allocation overhead. Python dedup sets and integer objects can push total memory into gigabytes. Moreover, ineligible trades add IDs and return before `trim()`, so an ineligible-only stream can grow the set without triggering its limit. Rebuilding a large set adds transient allocation and latency.  
    Fix: give deduplication its own bounded expiry mechanism that runs on every ingestion path; establish a process-wide memory budget and measure peak usage.

14. P1 — `RawStore.insert` / `coverage_of`: the start boundary is both incorrectly maintained and incorrectly defined.  
    The earlier-timestamp branch leaves `start_ns` unchanged. Separately, the first print’s timestamp is not the beginning of feed coverage: a legitimately quiet prefix gets classified as missing data. Empty retained storage can also fall back to the old start and claim coverage.  
    Fix: distinguish subscription coverage, earliest observed print and irreversible eviction boundaries. Correcting the assignment to `min()` alone is insufficient.

15. P1 — `_handle_relay_status`, `note_gap`, `_gap_in`: point marks do not represent outages accurately.  
    A query wholly inside a disconnected interval need not contain either endpoint. Marks are added for every stock status event, including repeated unchanged statuses, creating false gaps. Dropping everything beyond the latest 1,000 marks can erase still-relevant loss evidence. Status receipt times can also lag the actual loss interval.  
    Fix: track connectivity transitions and open/closed outage intervals, use conservative time boundaries, and retain them by query horizon.

16. P1 — `TickTape._quote_for` / `on_quote`: 500 quotes do not guarantee the required history.  
    Above 250 quotes/second, 500 entries cover less than two seconds; late-print support needs additional history for the permitted delivery delay. Discarded history silently converts otherwise quote-classifiable volume into tick-inferred volume. Rejecting every late quote also prevents useful historical insertion without advancing the current quote.  
    Fix: retain history by time plus permitted lateness, with explicit truncation reporting; maintain current-quote state separately from historical insertion.

17. P1 — `TickTape.on_quote`: crossed quotes do not reliably invalidate classification or preserve ordering.  
    A crossed quote removes `_quotes[sym]` but leaves the old quote history and book buckets usable. Removing the current quote also removes the timestamp check’s anchor, allowing an older quote to be accepted next and appended out of order. `_quote_for` can then select the wrong historical quote.  
    Fix: retain a monotonic quote timestamp independently of validity and represent invalid intervals in both classification and book coverage.

18. P1 — `TickTape.on_trade`: zero-tick direction is not the tick rule after quote classification.  
    `_last_side` stores the last aggressor classification, not the last nonzero price-change direction. A price increase classified at the bid sets a sell side; the next unchanged inside-spread print becomes tick-sell even though the last price change was up.  
    Fix: maintain a separate tick-direction state, updated from price changes even when the current print is quote-classified. Reset its continuity across relevant feed/session gaps.

19. P1 — `TickTape.on_trade`: the dedup key silently changes trade identity.  
    Only the first exchange character and the low 56 trade-ID bits survive. Distinct inputs can collide and valid prints be dropped. Missing IDs and an actual zero ID are conflated. Rebuilding `seen_ids` from retained eligible rows also discards the history of ineligible IDs.  
    Fix: preserve the complete supported identity, validate its documented bounds, distinguish absent IDs, and define the deduplication horizon explicitly.

20. P1 — `book_imbalance` / `_seconds`: arbitrary nanosecond windows are not stable historical queries.  
    Buckets store only their final quote. A later quote after `t1` in the same second makes the whole bucket disappear, including earlier valid quotes. Appending future data can therefore change an earlier result. Partial boundary seconds are not duration-clipped.  
    Fix: retain sufficient quote transitions to answer historical boundaries, or restrict and document this API as finalized whole-second queries. Current minute-aligned strategy calls avoid only the partial-second case.

21. P1 — `evaluate_bar::RESUMPTION_MEASURED`: recorded evidence can disagree with the actual gate.  
    Resumption delta and velocity are recorded without checking completeness. Pullback delta is the stored value from the touch or a previous evaluation; the full-leg value is recomputed later. One measurement exception wipes all four results, including successful earlier calls. Coverage, classification shares, window bounds and failure reasons are omitted.  
    Fix: collect one structured snapshot containing the current full-leg measure and individual validity/error metadata, then use that same snapshot for recording and enforcement.

    The literal “before any gate” claim also excludes the resumption-age rejection, which runs before this event.

22. P1 — `evaluate_bar`: two “since extreme” measurements use different, unstated boundaries.  
    Resumption delta starts at the end of the extreme bar, excluding any recovery within it. `advance_atr` starts at the extreme bar’s close, not its low/high. These are not measurements from the actual price extreme.  
    Fix: name them explicitly as post-extreme-bar flow and close-to-close advance, or preserve the actual extreme timestamp/price and calculate the advertised measurements.

23. P1 — `RegimeFeed.on_bar`: session handling accepts non-session data and can roll backward.  
    No regular-session filter exists despite the series contract. Premarket bars can enter session VWAP. Any different date—including an older delayed date—replaces the current series before ordering is checked. Dates use the timestamp’s existing timezone rather than an explicit ET trading session.  
    Fix: establish an ET session identifier, reject earlier sessions, filter the intended hours and reset only on forward session transitions.

24. P1 — `wallclock_return` / `rs_inputs` / `_ride_the_trend_regime`: return endpoints are insufficiently constrained.  
    Freshness is checked only at the newest ETF endpoint. A fresh UUP bar can be compared with a baseline far older than 30 minutes. Stock baselines have no freshness bound either. Additionally, admission obtains stock return from the latest mutable strategy state rather than querying it as of the signal timestamp.  
    Fix: validate both endpoints, record their ages and actual interval, and bind stock and ETF calculations to the same signal-time policy.

25. P1 — `RegimeFeed.evaluate`: short sector direction is not an exact mirror.  
    `not above_vwap` accepts equality for shorts, while longs require strict inequality. Fix: compare `close < vwap` explicitly for shorts and specify the neutral/equality policy. The default UUP and IEF directional multipliers themselves do mirror the stated long rules; they are not sign-inversion bugs.

26. P1 — `handle_bar_event` / dry-run `run`: replay assumes a cross-symbol ordering live ingestion does not guarantee.  
    Updating regime state before strategies only orders processing of the current event. It does not ensure the same-minute ETF event arrived before a stock event. Replay explicitly gives ETF bars priority, while live evaluation may use older bars within freshness tolerance.  
    Fix: choose a documented synchronization/as-of policy and reproduce it in replay; record endpoint timestamps and availability times. Previous-minute data is permissible only as an explicit policy, not proof of same-minute equivalence.

27. P1 — `_ride_the_trend_regime`: regime measures are not recorded for every resumption candidate.  
    They run only after a signal reaches this admission stage and passes preceding filters. The 22 `RESUMPTION_MEASURED` events do not carry contemporaneous regime snapshots; the diagnostic reconstructs them afterward.  
    Fix: record regime evidence at the intended candidate event, including candidates rejected by earlier filters, using the same historical query implementation as admission.

28. P1 — configuration in `main.py` and `VWAPPullbackV2Strategy.__init__`: misspelled gates silently disappear.  
    Both comprehensions discard unknown names; whitespace is not stripped. The mutable module set and the strategy’s copied list can also diverge, making health/admission disagree with tape enforcement. No mutation is shown, but the inconsistency is structurally possible.  
    Fix: validate and reject unknown names at startup; use one immutable, versioned effective policy shared by enforcement and reporting.

29. P1 — `notify_admitted`: cooldown is anchored to emission, not necessarily admission.  
    It uses mutable `last_emitted_i`, which may precede admission or belong to a later signal. It also has no signal/session identity or idempotency protection. The supplied main diff does not show the notification call site, so correct timing, duplicate handling and reservation of the daily budget are unverified.  
    Fix: pass an immutable admission identity and session, anchor the cooldown to the chosen admission bar, and make accounting idempotent. Test delayed and duplicate admission notifications.

30. P1 — dry-run `run`: the quote-share diagnostic cannot measure its advertised interval.  
    It queries 09:45–11:30, a 105-minute interval, after replay from a 60-minute store. Depending on replay end, this returns a retained suffix or nothing. It ignores completeness. Setting `min_classified_share=0` still leaves `min_quote_share=0.20`, censoring precisely the low-quote-share cases needed to assess that threshold.  
    Fix: accumulate numerator and denominator during the interval, or use retained historical input independently of the live rolling store. Report shares without qualification thresholds and include coverage.

31. P1 — dry-run diagnostics: “would fail if enforced” is not an enforcement replay.  
    The listed delta counts match the supplied values under default thresholds, but count repeated evaluations of the same setup. Enforcing impulse delta would remove downstream evaluations entirely. Incomplete measurement values are treated as valid, and the sector-direction values have the look-ahead defect in finding 10.  
    Fix: label these as per-evaluation threshold observations. To estimate enforcement effects, replay each policy through the state machine using decision-time evidence.

    Feasibility is likewise only the intersection of two inequalities with frozen features, not overall trade feasibility. Both feasibility and failure thresholds are hardcoded rather than taken from the evaluated policy.

32. P1 — replay validation: three successful exits with zero signals do not exercise real-data admission.  
    None of these sessions validates the complete real-tape path through signal creation, regime admission, submission and cooldown. `n_bars` includes index/regime/other-strategy bars, so it can conceal missing stock bars. Checking only `regime_feed.bars_seen > 0` does not establish coverage of all seven ETFs; that counter also survives `reset_session()`, including runtime resets.  
    Fix: validate per-symbol session coverage and counter deltas. Add a real-`TickTape` integration scenario that reaches submission, plus rejection scenarios for missing and corrupt evidence.

33. P2 — `stock_ws.py::_send_initial_subscriptions`: “regime ETFs are bars only” depends on disjoint configuration.  
    A regime symbol already in `self.symbols` still receives quotes and trades. If also watchlisted, the stated watchlist guard does not exclude it from strategies.  
    Fix: validate the intended disjointness or document and enforce explicit overlap behavior.

34. P2 — retention, diagnostics and cleanup contain smaller inconsistencies.
    - `TickTape.on_quote` advances the watermark but does not trim raw storage; ineligible-only traffic also skips trimming. Enforce retention independently of eligible-trade arrival.
    - `prints()` omits receive sequence and trade identity; stored flags preserve only odd-lot status for eligible prints. Provide a lossless audit export if raw replay is intended.
    - `data_layers()` calls lifetime activity “live,” even after staleness or disconnect, and its layer-4 description omits the new feed. Report freshness and effective coverage.
    - `_to_idle()` leaves impulse delta/detail behind. Clear setup-specific evidence together.
    - `pre_empted` excludes replacement of an existing `IMPULSE`, undercounting “live setup” replacements unless that narrower definition is intended.
    - `_Series.close_at_or_before` is linear in the distance scanned, not necessarily the whole list on every call. Indexed timestamps and prefix aggregates address this, but it is minor beside raw-store copying.
    - Duplicate `self.ineligible = 0`, `if True:`, unused `rates2_min_30m`, and the contradictory “Stored, never measured” condition comment should be removed or corrected.

Only test names were supplied, so absence of assertions cannot be established. The following coverage is not demonstrated: incomplete velocity passing the evaluator; cap splits at identical timestamps; outage-interior windows; column rollback on overflow; event-time tick classification; sparse-second book weighting; quote-history exhaustion and crossed-quote recovery; historical regime prefix invariance; naive/nonfinite/out-of-session regime bars; stale baseline endpoints; contradictory enforcement settings; delayed/idempotent admission; and sustained CPU/peak-memory behavior after retention fills. The test named `test_book_imbalance_is_quote_weighted_over_the_window` particularly needs inspection against the new duration-weighting contract. Fake-tape success does not establish these properties.

Do not count these as defects: locked quotes fall back to the tick rule, including prints exactly at the locked price; ineligible prints do not update tick state; the two impulse events describe consistent successive transitions; `sgn_side(st.side)` equals `sgn` for valid active states; the impulse bar’s completed `[start,start+60s)` window is appropriate subject to ingestion completeness; the real `rolling_delta()` rejects incomplete results; and the macro outcome split matches `OUTCOME_TEXT`. Wall-clock health age is appropriate for live health, although historical replay reports need an explicit replay clock.

(a) Three findings that matter most

- Required velocity/book gates can accept incomplete evidence, and raw coverage can falsely certify completeness.
- Failed ingestion can corrupt packed columns; optional measurement failures can abort processing even with new gates disabled.
- Per-print array compaction and deduplication memory can stall or exhaust the live process after the store fills.

(b) Go-live tonight: No. Single condition: deploy only a revision whose P0 fixes pass real-`TickTape` admission, fault-injection and sustained-load acceptance tests.
