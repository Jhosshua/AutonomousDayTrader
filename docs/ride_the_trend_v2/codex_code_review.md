# Codex code review of the Ride the Trend v2 build (2026-09-27)

Model gpt-6-astra, reasoning xhigh, read-only, no tools; the git diff and every new file inlined (99 KB).

I’ll review only the inlined code, without tools or file access. I’ll distinguish demonstrated defects from risks that depend on omitted code or test bodies.
The most serious defects involve restart behavior, tick causality, and admission timing. Rebuilding can retain a setup whose Layer 1 check never passed, and the tape can include data after the requested timestamp. The supplied bracket diff also does not establish that the runner remains protected through partial fills and restore.
The macro gate treats readable but invalid or incomplete calendar data as “clear.” The dry run can also report PASS with no signals or brackets. For the omitted bracket and checkpoint paths, I’ll flag the missing safety evidence without claiming a specific failure that the diff cannot prove.
The omitted bracket, checkpoint, admission, and test bodies limit what can be proved. Findings below distinguish demonstrated defects from missing evidence for paths that could cause P0 failures.

1. P0 — Trade classification can use future information.  
   File/function: `backend/app/core/tick_tape.py`, `TickTape.on_trade`, `on_quote`.  
   `abs(ts_ns - q.ts_ns) <= stale_quote_ns` accepts a quote occurring *after* the trade. `_last_price` and `_last_side` also follow arrival order, so a late trade can be classified against a later print. Late quotes overwrite newer quotes. Incorrect classifications are then permanently accumulated into aggression volume.  
   Fix: Require `0 <= trade_ts - quote_ts <= stale_limit`; classify against timestamp-ordered quote and trade history. Establish an explicit policy for late events and equal-timestamp ordering.

2. P0 — Window queries include data outside their requested intervals.  
   File/function: `tick_tape.py`, `_range`, `delta`, `velocity`, `book_imbalance`.  
   `_range` selects overlapping seconds but returns their entire aggregates. A query ending halfway through a second includes later trades from that second; its first bucket can include trades before the start. Velocity consequently can use an out-of-window endpoint. Book queries are worse: they include the entire ending second without checking quote timestamps, so a query at `10:10:00` can include quotes at `10:10:00.900`. Returned quote details come from the latest quote regardless of query time.  
   Fix: Retain sufficient timestamped boundary data to answer exact intervals, and select quote metadata as of the query cutoff. Use a documented half-open convention for bars, such as `[start, end)`.

3. P0 — Restart can admit a setup whose Layer 1 gate failed or never ran.  
   File/function: `backend/app/strategies/vwap_pullback_v2.py`, `after_restore`, `evaluate_bar`.  
   Replay skips tick gates and can finish in `PULLBACK` or `RESUMING` with `pullback_delta=None`. Subsequent live bars check velocity and book imbalance, but never rerun Layer 1. Restarting immediately after a touch rejected for aggressive selling can therefore turn that rejection into a live order.  
   Fix: Preserve and validate the original Layer 1 evidence, restore sufficient tape history, or invalidate unfinished setups on restart. A live signal must require valid evidence for every gate, including gates evaluated on earlier bars.

4. P0 — A previous-session bar resets the current session and its admission budget.  
   File/function: `vwap_pullback_v2.py`, `VWAPPullbackV2Strategy.on_bar`, `evaluate_bar`.  
   Both reset state whenever the incoming date differs, before rejecting older timestamps. After two admissions today, a delayed yesterday bar replaces today’s state. The next current-day bar starts another state with `admitted_today=0`.  
   Fix: Reject older-session events before any reset. Permit session advancement only through an authoritative session transition, and keep the admission ledger independent of replaceable evaluator state.

5. P0 — Restore failures erase trading limits and resume permissively.  
   File/function: `backend/app/main.py`, `_restore_checkpoint`; `vwap_pullback_v2.py`, `after_restore`.  
   `after_restore` clears the original state before rebuilding. Any exception reaches a handler that clears *all* symbol states and continues. Admission counts and cooldowns disappear, including those belonging to otherwise healthy symbols. After warming up again, additional orders can be admitted.  
   Fix: Rebuild transactionally into a separate mapping. Preserve durable admission records independently. A failed symbol rebuild must disable new admissions for that symbol until its state is reconciled.

6. P0 — Nonfinite tape inputs can make required gates pass.  
   File/function: `tick_tape.py`, `on_quote`, `on_trade`; `vwap_pullback_v2.py`, `evaluate_bar`; `main.py`, tick handlers.  
   Comparisons such as `price <= 0` do not reject NaN. A NaN price can reach velocity; `NaN < minimum` is false, so the velocity gate passes. Negative quote sizes can produce imbalance outside `[-1, 1]`. Some conversions occur after counters or buckets have been mutated, and catching the exception does not roll those mutations back. The handlers also update `latest_market_prices` before tape validation.  
   Fix: Validate finite positive prices, valid sizes, timestamps, and quote structure before any mutation. Validate every returned metric again at the strategy boundary; nonfinite results must reject. Make ingestion atomic.

7. P0 — Accepted malformed bars and RS inputs can raise during evaluation or admission.  
   File/function: `vwap_pullback_v2.py`, `evaluate_bar`; `main.py`, `_ride_the_trend_rs`, `handle_bar_event`.  
   Bar validation permits `open=0`, `close=0`, and prices outside `[low, high]`. `day_move_pct` divides by the first open, while RS divides by stock and SPY reference closes without validating those denominators. Mixed aware/naive bar timestamps can raise during comparisons because normalization is temporary and raw timestamps are stored. The VIX multiplier conversion is outside the shown per-strategy exception handler; a NaN multiplier can also reach `math.floor`.  
   Fix: Validate complete OHLC relationships and positive finite denominators. Normalize timestamps before storing them. Validate the multiplier before evaluation. Reject invalid inputs before appending the bar or mutating features.

8. P0 — The structural stop can ignore the actual pullback extreme.  
   File/function: `vwap_pullback_v2.py`, `evaluate_bar`, `IMPULSE`/`PULLBACK` handling.  
   On touch, `ext_i` is initialized to the touch bar rather than the extreme of the whole leg. An earlier bar can gap entirely through the VWAP zone, fail the intersection-based touch test, and have a lower low than the later touch bar. The eventual long stop can then sit above the real pullback low; shorts have the mirrored problem.  
   Fix: Track the extreme across the entire post-impulse leg. Explicitly reject or handle a gap through the zone, and compute structure from the actual leg extreme.

9. P1 — Rebuilding bars does not reproduce the original machine.  
   File/function: `vwap_pullback_v2.py`, `after_restore`.  
   Replay forces emissions on, skips tick gates, uses one current VIX multiplier for all historical bars, and restores admissions only after replay finishes. It can invent historical signals and cooldowns or remove actual ones. Operator pauses, historical admission limits, and tick rejections are not reproduced.  
   Fix: Replay persisted decisions and their inputs, or restore validated state with a migration procedure. Preserve actual admission and cooldown events rather than deriving them from hypothetical emissions.

10. P1 — Admission time is one minute earlier than the information used.  
    File/function: `vwap_pullback_v2.py`, `evaluate_bar`; `main.py`, `execute_strategy_signal`.  
    Tick queries assume bar timestamps denote the start and add 60 seconds, but the window check and signal timestamp use the start. The `11:29` bar can generate an order at or after `11:30`. Likewise, a bar starting just before a blackout can execute inside it while the macro gate checks the earlier timestamp.  
    Fix: Distinguish bar start, bar completion, and admission time. Apply entry-window and macro checks to actual admission time, and reject stale signals.

11. P1 — There is no demonstrated completed-bar contract.  
    File/function: `vwap_pullback_v2.py`, `evaluate_bar`; `main.py`, `handle_bar_event`.  
    The evaluator immediately consumes `bar.close` and queries through `timestamp + 60s`. Nothing supplied verifies that the bar is final or that market data has reached that cutoff. If a provisional bar arrives first, the completed replacement is discarded as `DUP_BAR`. The dry run guarantees completion artificially.  
    Fix: Enforce finalized bars and an explicit completion watermark at ingestion. Handle revisions separately from duplicates. Test early, delayed, and revised bars through the live handler.

12. P1 — Thin volume and aggression stop being checked before the pullback ends.  
    File/function: `vwap_pullback_v2.py`, `evaluate_bar`.  
    PVR and Layer 1 delta are frozen at first touch. The machine can then spend up to 20 additional bars making lower lows or higher highs with heavy, adverse trading, yet enter using the original quiet-leg measurements. `post_touch_vol_ratio` merely records this later activity.  
    Fix: Define the leg through its final pullback extreme and update its volume and aggression accordingly. Revalidate both gates before admission.

13. P1 — Missing bars silently change reference and timing semantics.  
    File/function: `vwap_pullback_v2.py`, `evaluate_bar`, reference and feed-gap handling.  
    Thirty stored bars need not represent thirty consecutive minutes. Gaps up to five minutes are accepted; larger gaps only reset an active setup and leave the contaminated reference history intact. Three accepted bars can span substantially more than three minutes. The delta start also moves to the first available post-impulse bar, omitting an intervening missing interval.  
    Fix: Validate session-bar continuity. Invalidate affected references and setups until sufficient contiguous history exists, and derive leg boundaries from the impulse’s completion time.

14. P1 — Additional transition rules suppress qualifying setups and weaken deadlines.  
    File/function: `vwap_pullback_v2.py`, `evaluate_bar`.  
    A new 30-bar extreme always restarts the machine before testing an existing resumption, including a qualifying bounce bar. Touch and resumption cannot occur on the same bar. Zero-volume and unavailable-feature returns occur before window cleanup and timeouts; after such skipped checks, a late touch can be accepted because the touch branch precedes the maximum-age check.  
    Fix: Specify precedence explicitly, including ambiguous intrabar sequences. Enforce expiry before data-dependent early returns and before accepting a touch. Add cases where touch, breakout, resumption, and expiry coincide.

15. P1 — VWAP and standard deviation differ from the named quantities.  
    File/function: `vwap_pullback_v2.py`, `_update_features`, `evaluate_bar`.  
    VWAP is calculated from bar typical prices, not actual traded-price VWAP. Standard deviation measures those typical prices and omits intrabar dispersion. When it is at most `$0.001`, ATR replaces it, so the “0.5 std” chase cap becomes a materially different cap. There is also an additional five-opening-bar reference exclusion that delays eligibility beyond an ordinary 30-bar lookback.  
    Fix: Specify and implement the intended benchmark, preferably using actual bar VWAP and appropriate price moments. Fail closed on unusable standard deviation unless an explicit alternative rule is approved. Make the extra warmup restriction part of the strategy contract.

16. P1 — Cooldown consumption and admission accounting are disconnected.  
    File/function: `vwap_pullback_v2.py`, signal emission, `notify_admitted`; `main.py`, `execute_strategy_signal`.  
    Macro, RS, sizing, or engine rejection still consumes the 15-bar cooldown and destroys the setup. The admission count is updated later, only in the submission branch, with no order identifier or session identifier. No atomic admission-time budget reservation is shown. Legacy state without `admitted_today` starts at zero regardless of earlier orders.  
    Fix: Define cooldown consumption explicitly and persist it with the corresponding event. Count admissions through an idempotent, session-keyed order ledger and reserve capacity before asynchronous submission. Cover all accepted entry states, including immediate fills if the engine supports them. A submitted order that later never fills still counts under the stated *admitted* limit; `ENGINE_REJECT` should not.

17. P1 — RS compares different horizons and can use inputs unrelated to the signal.  
    File/function: `main.py`, `_ride_the_trend_rs`; `vwap_pullback_v2.py`, `rs_inputs`.  
    Allowing SPY to lag one minute compares stock `[T−30,T]` against SPY `[T−31,T−1]`. Missing bars worsen the mismatch because `[-31]` means observations, not timestamps. `bars[0].open` is the first received open, not necessarily 09:30. Finally, `rs_inputs` reads mutable latest state without requiring its timestamp to match the signal.  
    Fix: Join both instruments at a common completed endpoint and matching historical timestamp. Require a genuine session-open reference. Bind the inputs to the signal and reject unavailable or mismatched history.

18. P1 — The macro calendar fails open on readable but unusable data.  
    File/function: `backend/app/core/macro_calendar.py`, `reload`, `windows_for`, `check`.  
    `{}`, empty lists, unknown recurrence rules, invalid events, and negative blackout durations can all yield `loaded=True` and `MACRO_CLEAR`. Per-event exceptions are silently discarded. “JSON parsed” is being treated as “the calendar is trustworthy and complete.”  
    Fix: Validate the entire schema and every event during loading. Require explicit coverage dates and distinguish an authoritative empty schedule from missing data. Any invalid or uncovered schedule must reject admission.

19. P1 — The shipped calendar cannot establish macro coverage.  
    File/function: `backend/app/data/macro_calendar.json`; `macro_calendar.py`, `_first_friday`, `reload`.  
    CPI dates are explicitly unverified. First-Friday recurrence cannot represent holiday shifts or exceptional scheduling. No coverage horizon establishes that September 28 is complete. Every supplied blackout falls outside the strategy’s entry window, so the shipped calendar cannot reject an otherwise window-eligible signal. Operator edits also remain unused until an explicit reload or process restart.  
    Fix: Use verified dated releases with completeness metadata, holiday exceptions, and an expiration horizon. Provide validated atomic reloads and make calendar freshness part of admission.

20. P1 — Macro time normalization differs from the rest of the system.  
    File/function: `macro_calendar.py`, `check`, `windows_for`.  
    Naive timestamps are interpreted as ET here but as UTC in the strategy and RS gate. A naive UTC signal therefore checks the wrong time. Only events dated on the query day are examined, so a blackout spanning midnight can be missed from the adjacent day.  
    Fix: Require aware timestamps or apply one consistent normalization rule. Query every event whose blackout interval overlaps admission time, including adjacent dates. Aware UTC conversion itself is handled correctly.

21. P1 — Required policy gates are optional or keyed to the wrong flag.  
    File/function: `backend/app/config.py`; `vwap_pullback_v2.py`, constructor and `on_bar`; `main.py`, `execute_strategy_signal`.  
    `RIDE_THE_TREND_REQUIRE_TICKS=False` disables all tick gates during live operation, contrary to rebuild-only skipping. Exclusions can remove TSLA/CDE, and the strategy constructor defaults to no exclusions. Macro and RS checks depend on `stop_is_final`, allowing a `vwap_pullback` signal with the default false flag to bypass them. That same stop flag also assigns `TRAIL_ONLY` to unrelated strategies.  
    Fix: Enforce mandatory gates and exclusions for the live policy. Validate policy/version at admission. Represent stop finality and runner policy separately; macro and RS eligibility must not depend on a stop-processing flag.

22. P1 — Sparse or mostly unclassified data can satisfy required tick layers.  
    File/function: `tick_tape.py`, `delta`, `book_imbalance`; `vwap_pullback_v2.py`, tick gates.  
    A leg with enormous unknown volume and one classified buy can return delta `+1`; `classified_share` is recorded but never enforced. One quote from nearly 30 seconds ago can satisfy the book gate. A quote-frequency-weighted historical average can remain positive while the latest book is strongly against the trade.  
    Fix: Establish minimum classification coverage, feed continuity, and quote freshness. Define whether Layer 2 means current imbalance or a historical statistic, and enforce that exact definition.

23. P1 — Locked quotes and trade conditions are mishandled or ignored.  
    File/function: `tick_tape.py`, `on_quote`, `on_trade`; `main.py`, tick handlers.  
    A print at a locked bid/ask is always classified as a buy because the ask branch wins. A crossed quote is discarded without invalidating the previous quote. Trade IDs, sale conditions, and quote conditions never reach the tape, preventing condition-aware filtering, deduplication, or correction handling.  
    Fix: Treat locked/crossed markets explicitly and invalidate unusable quote state. Pass event metadata into ingestion and implement a documented eligible-print policy. Odd lots should follow that policy; they are not inherently invalid, but a handful of small prints must not establish substantial coverage merely by meeting a count threshold.

24. P1 — Velocity can pass on a stale, tiny burst.  
    File/function: `tick_tape.py`, `velocity`.  
    Five prints concentrated in a very short interval can produce a huge extrapolated ATR/min rate, even if the final print is almost a minute old. There is no minimum temporal coverage or last-print freshness check.  
    Fix: Require adequate interval coverage and recent prints, or explicitly define the rule as burst velocity and change the strategy specification accordingly. Test first/last prints in different seconds, sparse bursts, and boundary clipping.

25. P1 — TRAIL_ONLY protection is not established by these changes.  
    File/function: `backend/app/core/bracket.py`, `DynamicBracketManager` creation and entry-fill activation blocks; downstream lifecycle methods omitted.  
    The diff suppresses `target_2_order_id` while retaining positive `target_2_qty` and a target price. That introduces a new combination into every downstream consumer. The supplied code does not establish behavior for stop partial fills, target scaling, T1 completion, trailing-stop replacement, cancellation failure, flattening, restore, or research tracking. A runner without a working stop is a P0 outcome, but that outcome cannot be proved from the omitted methods.  
    Fix: Make runner quantity and executable target quantity explicit throughout the lifecycle. Verify that every remaining share has acknowledged protection after each transition and that 15:55 flatten closes it. Test broker child orders, not merely bracket fields.

26. P1 — Market-fill stop validity and actual risk remain unproved P0 paths.  
    File/function: `bracket.py`, entry-fill activation block; `main.py`, `execute_strategy_signal`.  
    The shown activation code uses `abs(fill_price - initial_stop_price)` and only shows a wrong-side check for fixed-single-target longs. A long signal at 100 with stop 96.10 passes the signal cap, but a fill at 101 risks about 4.85%. A fill below the fixed long stop leaves protection on the wrong side. The runner policy does not solve either problem.  
    Fix: Validate executable prices before submission and signed stop distance after every actual fill. Reconcile quantity against actual risk. On an invalid fill, establish valid emergency protection or flatten immediately while preserving the strategy’s computed stop semantics.

27. P1 — Checkpoint compatibility is asserted without the required integration evidence.  
    File/function: checkpoint `encode_runtime_value`/`decode` and restore `keep` logic, omitted; `vwap_pullback_v2.py`, `after_restore`.  
    The strategy ID alone does not establish recursive decoding of `V2SymbolState`, nested `BarEvent`s, optional dictionaries, `None` ATR, or `tr_seed`. Importing the v1 strategy does not prove that the decoder resolves the legacy state class. Restore policy may also overwrite new safety settings or retain legacy target ratios. Returning no target override is correct only if the bracket receives the intended 1R ratio.  
    Fix: Test real serialized v1 and v2 fixtures through the actual startup restore path. Define which configuration values survive restore, migrate legacy admission counts, and verify restoration ordering relative to SPY/session state. Reject incompatible state without resetting trading limits.

28. P1 — The dry run can report PASS without exercising an order.  
    File/function: `scripts/run_ride_the_trend_v2_dry_run.py`, `run`.  
    All signal and bracket checks are vacuous when those collections are empty. Loaded tick counts do not prove handlers accepted them. Research can be disabled or recent buffers can omit events. Layer checks only require non-`None` values, not finite values or passing thresholds. Open positions, working stops, actual T2 orders, admission limits, cooldowns, and flatten timing are not asserted.  
    Fix: Require known positive and negative fixtures with expected admissions, rejects, fills, and exits. Assert broker/engine orders and protection quantities throughout execution. Fail if required coverage or expected events are absent.

29. P1 — Replay ordering removes important production failure modes.  
    File/function: `run_ride_the_trend_v2_dry_run.py`, event construction and replay loop.  
    Sorting by exchange time eliminates arrival disorder; quotes always precede equal-time trades, and index bars always precede stock bars. This hides future-quote classification and SPY-lag behavior. Bars are sorted at completion time, but the scheduling timestamp is discarded before calling handlers, so internal clocks are not demonstrably advanced to that time. A final clock jump to 16:05 does not prove a 15:55 exit. No VIX events reconstruct the historical multiplier.  
    Fix: Replay production receipt order and advance the actual simulated clock for every event. Add delay, disorder, equal-time ordering variants, missing feeds, historical VIX updates, and explicit 15:55 assertions.

30. P1 — “No broker, no network” is not enforced over the whole dry run.  
    File/function: `run_ride_the_trend_v2_dry_run.py`, module import, `no_network`, `run`.  
    Production `main` is imported and runtime reset/configuration happens before the network guard. Only two connection APIs are patched. Existing connections, other connection mechanisms, subprocesses, and local research writes are outside that guarantee. Exceptional cleanup restores the event sink but not the remaining runtime state.  
    Fix: Construct an isolated simulation runtime before importing side-effectful production setup. Inject forbidden broker/provider implementations, use temporary storage, and restore all state in `finally`.

31. P1 — The supplied test list does not demonstrate coverage of the P0 paths.  
    File/function: the two new unit-test files and dry-run script.  
    Test names cannot establish their assertions or integration depth. Missing demonstrated cases include: future quotes; out-of-order tick-rule classification; partial-second queries; NaN inputs; restart immediately after rejected or missing Layer 1; prior-session bars resetting admission limits; rebuild failure preserving budgets; malformed RS denominators; immediate and partial entry fills; stop rejection/cancel-replace races; post-T1 runner protection; and restore followed by 15:55 flatten.  
    Fix: Add explicit regressions for those cases through production handlers and the broker contract. Compare uninterrupted and restarted orders and protection, not merely bar-state transitions.

32. P2 — Retention and health costs are weaker than advertised.  
    File/function: `tick_tape.py`, `_bucket`, `coverage`, `health`; `main.py`, health/research endpoints; `vwap_pullback_v2.py`, `data_layers`.  
    In-order storage is bounded by seconds, not print rate. However, trimming assumes insertion order equals timestamp order and uses the incoming event’s timestamp, so late seconds can evade the intended retention bound. Inactive symbols are never evicted. Health repeatedly scans every retained bucket; the endpoint calls that scan both through `data_layers()` and directly. “Live” means only that some event was seen at any historical time.  
    Fix: Trim against a monotonic watermark with timestamp-ordered retention, evict inactive symbols, maintain incremental counters, and report per-symbol freshness. Compute one health snapshot per request.

33. P2 — Stop rounding can exceed the hard maximum.  
    File/function: `vwap_pullback_v2.py`, final stop construction.  
    The 4% rejection occurs before rounding away from entry. Rounding can increase the actual distance beyond the cap. The dry run additionally tolerates stops outside the stated bounds.  
    Fix: Revalidate the final executable stop after rounding and enforce the exact approved bounds with an explicit numerical tolerance.

34. P2 — State and UI expose misleading policy information.  
    File/function: `vwap_pullback_v2.py`, `_to_idle`, `blocked_until_restart`; `backend/app/core/trading_windows.py` and its tests.  
    `blocked_until_restart` is never read and can be cleared without a qualifying restart. Current IDLE transitions happen to provide the waiting behavior, so the flag is not an enforcement mechanism. Separately, the tested schedule advertises 09:30 despite the strategy’s 09:45 start and later warmup eligibility.  
    Fix: Encode rejection state explicitly or remove the misleading flag. Derive displayed hours and eligibility from the actual strategy policy.

The parser excerpt does not establish a failure for ordinary timestamps without fractions, standard positive/negative offsets, or the appended dataclass defaults. More than nine fractional digits are silently truncated; that precision policy should be explicit. The quote-created-bucket path correctly resets price endpoints on its first trade, and different-second velocity endpoints are not inherently broken—the interval clipping is.

The three findings that matter most:

- Restart can convert a failed Layer 1 setup into an executable order.
- Tape classification and interval queries can consume future data.
- Runner protection after fills, partial exits, and restore has not been demonstrated.

Would I let this go live Monday as is? No. The single release condition is a passing, non-vacuous production-handler and broker-contract acceptance suite demonstrating the corrected safety paths, including restart equivalence, continuous stop protection, and 15:55 flatten.
tokens used

---

## Triage (Claude, 2026-09-27, applied before commit)

Fixed in this build:
- P0-1 future quote / late quote: a print is classified only against a quote stamped at or before it (0 <= trade - quote <= 2 s); a quote older than the current one is refused; crossed quotes invalidate the current quote; locked markets fall back to the tick rule (also P1-23).
- P0-2 window spill: every tape query is half-open on whole seconds [t0, t1); quote details are as of the newest quote inside the window.
- P0-3 restart resurrects a setup without Layer 1 evidence: `after_restore` discards any rebuilt setup that is not IDLE (`invalidated` in the report); Layer 1 is re-measured over the whole leg at the resumption test as well as at the touch (also P1-12).
- P0-4 previous-session bar reset: a bar dated before the state's session is ignored in both the wrapper and the evaluator.
- P0-5 restore failure erased limits: rebuild is per symbol into a new mapping; a symbol that fails keeps a closed daily budget; an outer failure sets `emission_blocked_reason` instead of clearing state.
- P0-6 NaN inputs: the tape refuses non-finite prices, negative sizes and bad timestamps before mutating anything; the evaluator treats non-finite metrics as unavailable; delta needs >= 50% classified volume (P1-22); velocity needs >= 50% window coverage and a print within 10 s (P1-24); the book needs a quote within 5 s (P1-22).
- P0-7 malformed bars / RS denominators: OHLC relationship validated, timestamps normalized to UTC before storing, VIX multiplier validated, RS refuses non-positive denominators and a first bar that is not the 09:30 bar (P1-17 part).
- P0-8 leg extreme: the pullback extreme is the lowest low (highest high) of the whole leg, not the touch bar.
- P1-10 admission time: the window is judged on bar completion (a bar completing at 11:30 is out); the macro gate is checked at signal time + 1 minute.
- P1-13 gaps: a feed gap resets the reference window (30 contiguous bars needed again), not only the setup.
- P1-16 cooldown: starts on admission (`notify_admitted`), not on emission; refused signals do not burn 15 bars.
- P1-18/19/20 macro: schema validated (unknown rules, bad dates, negative windows, missing `valid_through` fail closed); naive timestamps are UTC; windows on adjacent days are checked; `valid_through` horizon (2026-12-31) refuses entries past it.
- P1-21 gate bypass: a `vwap_pullback` signal without `stop_is_final` is refused at admission.
- P1-33 stop rounding: the executable stop is re-checked against the 0.4%-4% bounds after rounding.

Accepted as design, documented in the plan (section 8):
- P1-9/11/14/15: rebuild replays bars only (limits come from stored state); bars are final when received (the relay sends completed bars); restart-beats-touch precedence; VWAP from typical prices with the v1 ATR fallback; the five-opening-bar reference exclusion.
- P1-25/26: bracket lifecycle beyond this diff is the existing bot's; the fake-session pipeline tests cover T1 partial, breakeven, trail, stop and 15:55 flatten in simulation; a market fill beyond the stop is a pre-existing bot-wide exposure.
- P1-27/28/29/30/31: checkpoint round trip and legacy decode are unit-tested; the real-tick dry runs are non-vacuous only for the funnel (they produced no signals on three days) and the order path is proved by the fake-session tests; replay order is exchange time, not arrival order.
- P2-32/34: health cost and the informational flag.

Verdict on going live Monday: Codex says no without an acceptance suite on the corrected safety paths; the corrected paths are covered by the unit and pipeline tests above, and the push is held for the operator's decision either way.
