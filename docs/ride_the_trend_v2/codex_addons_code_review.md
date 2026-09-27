# Codex code review of the add-ons build (2026-09-27)

Model gpt-6-astra, reasoning xhigh, read-only, no tools; as-built plan section + diff + three full modules + replay results inlined.

I’m reviewing the inlined code and replay evidence for concrete failure paths, separating demonstrated defects from integration risks the supplied code cannot resolve.
1. P0 — Obstacle-query failures pass the required gate.  
   `backend/app/strategies/vwap_pullback_v2.py`, `evaluate_bar`: `_safe(prof.path_obstacle, ...)` returns `None` both when the road is clear and when the query raises. If support was found, an exception in the obstacle calculation allows entry. This directly violates fail-closed enforcement.

   Fix: distinguish successful measurement from its result: `{available: True, obstacle: None}` versus `{available: False, error: ...}`. Any failed profile query must discard the setup with a visible unavailable reason.

2. P0 — Crossed quotes leave usable spread and book evidence behind.  
   `backend/app/core/tick_tape.py`, `on_quote`, `spread_stats`, `book_imbalance`: crossing clears `quote_hist` and `t.quote`, but neither spread nor book reads those structures. Both reconstruct state from `SecondBucket`, where the previous valid quote remains intact. No invalidation marker enters that history.

   A cross occurring two seconds before the decision can leave the previous quote eligible for the book’s five-second freshness check and the spread’s thirty-second carry. The strategy can therefore enter using an inside market explicitly declared unusable.

   Fix: record timestamped quote-validity transitions in the history consumed by all three consumers. A cross must terminate the preceding quote’s validity until a strictly uncrossed quote arrives.

3. P0 — Crossed quotes do not stop trade classification.  
   `tick_tape.py`, `_quote_for`, `on_trade`: a missing or invalid quote falls through to the tick rule. After a cross, rising prints still become `SIDE_TICK_BUY`; falling prints become sells. Earlier quote-classified volume can keep the aggregate above the 20% quote-share floor, allowing this invalid-period flow to determine the gate.

   Fix: distinguish “no quote available for quote classification” from “classification explicitly invalidated by a cross.” Under the stated policy, prints in the latter interval must remain unknown, including the tick-rule path. Retain invalid intervals so late prints receive the same treatment.

4. P0 — Reconnects and gaps contaminate the restarted session total.  
   `tick_tape.py`, `note_gap`, `note_reconnect`, `_touch_session`, `session_delta`: restarting only clears `since_ns`. It preserves aggregates and sets the new start from the next arriving event, regardless of whether that event predates the gap.

   Two independent failures follow:

   - Reconnecting at 10:15:50 includes the entire 10:15 minute, including trades before the reconnect.
   - A delayed pre-gap event arriving first can start the continuity clock before the gap, allowing premature warm-up and inclusion of older flow.

   Fix: create a feed-generation boundary with an explicit restart timestamp. Reject pre-boundary events from that generation’s cumulative total. Either aggregate the new generation separately or retain timestamps sufficient to trim its first minute exactly. Do not infer the restart boundary from the next arbitrary arrival.

5. P0 — Eviction removes deduplication while retaining session totals.  
   `tick_tape.py`, `_evict_oldest`, `_admissible`, `on_trade`: trade IDs live only in second buckets. A bucket evicted by the raw-print cap loses its IDs, but its volume remains in `minutes`. `_admissible` checks age, not the cap-eviction boundary, so a retransmitted trade from that second can be accepted and added to the cumulative total again.

   Repeated retransmissions can change the required session gate without new market activity.

   Fix: retain deduplication/correction state for the lifetime of the cumulative aggregate, or explicitly reject events below a finalized ingestion boundary. If previously unseen late events cannot be incorporated accurately, mark the affected aggregate incomplete.

6. P0 — “Complete” session flow does not establish that trade data reached the cutoff.  
   `tick_tape.py`, `_advance`, `session_delta`: quotes, eligible trades, and even ineligible prints advance the same watermark. A healthy quote stream can make session delta complete while trade delivery is stalled. The five-second slack also explicitly permits a missing tail of trade data.

   Thirty elapsed minutes plus a recent quote is not evidence of thirty minutes of complete trade delivery.

   Fix: use relay sequence/coverage information or a trade-channel completion barrier for the decision cutoff. Define bounded lateness and finalization explicitly. Quote freshness must not certify trade completeness; unavailable trade coverage must block with its actual reason.

7. P0 — A feed interruption longer than one hour can permanently freeze ingestion.  
   `tick_tape.py`, `_admissible`, `note_reconnect`: every new event more than one hour beyond the old watermark is rejected. Reconnect resets `since_ns` but leaves that watermark unchanged. After a seventy-minute interruption, current events are rejected forever because nothing can advance the watermark.

   The same problem exists across overnight inactivity unless an external reset occurs; that reset is not established by the supplied boundary diff.

   Fix: establish a new ingestion epoch on reconnect/session rollover and validate its initial timestamp against an appropriate current-time bound. Do not use an indefinitely old event watermark as the sole future-time plausibility reference.

8. P0 — Profile work can block the live event loop or run without a total bound.  
   `volume_profile.py`, `build_profile`; `backend/app/main.py`, `_fetch_relay_bars`, `rebuild_volume_profiles`:

   - Bucket work is proportional to `(high - low) / width`, with no limit.
   - `_valid` permits a tiny closing price outside the bar’s range, producing an arbitrarily small width and an enormous bucket loop.
   - The synchronous profile build runs directly inside the asyncio task. Creating a task does not move CPU work off the event loop.
   - Pagination has no page limit, repeated-token detection, response-size limit, or whole-build deadline. A thirty-second request timeout does not bound the entire operation.

   Fix: validate OHLC geometry and bounded bucket counts before allocation; cap history size, pages, and total duration; detect repeated cursors. Build outside the trading event loop and publish a completed result atomically. Bound the computation even when it runs elsewhere.

9. P0 — Profile rebuild requests can be lost, and obsolete tasks can repopulate reset state.  
   `main.py`, `_schedule_profile_rebuild`, `_check_session_boundary`, `reset_runtime_state`, `rebuild_volume_profiles`: any running task causes a new request to be discarded—even when the requested session differs. Once the old task finishes, nothing schedules the discarded session. A failed symbol also gets no retry until another scheduling event.

   Reset clears the store without cancelling or invalidating the worker. An outstanding HTTP response can subsequently repopulate it, or remove a newer profile on failure. There is no generation check before publication.

   Fix: track the desired session and a generation token. Coalesce requests toward the latest session, retry transient failures within a bounded policy, and reject publications from obsolete generations. Cancel and await workers during reset/shutdown.

   Calling the scheduler inside async lifespan is valid. Calling it from a synchronous function is also valid when that function runs on the event-loop thread. The defect is silently swallowing the genuinely absent-loop case and retaining no pending request or error.

10. P0 — Session rollover can move backward and erase current aggregates.  
    `tick_tape.py`, `_touch_session`: `t.session != day` resets the session in either direction. An admissible late trade from the preceding ET date clears the newer date’s totals; the next current-date event clears them again. The strategy’s bar state explicitly rejects backward sessions, but the tape does not.

    Fix: make session progression monotonic. Route previous-session late events to separate historical state or reject them; never let them reset the active session. UTC minute keys themselves are not the problem here.

11. P1 — The required five-session profile accepts three sessions.  
    `volume_profile.py`, `build_profile`, `ProfileStore`, `VolumeProfile.is_valid_for`: the default minimum is three throughout. `VOLUME_PROFILE_SESSIONS=5` limits how many dates are selected; it does not require five. `V2Params.hvn_min_sessions` does not control the store.

    Fix: require exactly the intended five prior sessions, or explicitly change the operator-approved requirement. Validate configuration at startup: zero and negative session counts currently have surprising slicing behavior, and counts below the minimum create permanently unavailable profiles.

12. P1 — Profile validity checks a label and count, not the required history.  
    `volume_profile.py`, `_session_of`, `build_profile`, `is_valid_for`: one bar on each of three dates is enough. Missing recent sessions, incomplete history, and older replacement dates are accepted. Fixed 09:30–16:00 filtering also does not establish the actual regular-session schedule, including shortened sessions.

    A profile tagged for today can therefore be “valid” without representing the prior five regular sessions.

    Fix: derive expected sessions and their boundaries from a trading calendar. Validate history coverage and freshness against those sessions, with an explicit policy for legitimate missing minute bars. Store the resulting quality assessment, not merely the date count.

13. P1 — Profile inputs can silently mix or misweight data.  
    `volume_profile.py`, `_valid`, `build_profile`; `main.py`, `_fetch_relay_bars`: the builder does not verify the bar’s symbol, deduplicate timestamps, resolve revisions, or require open/close to lie inside low/high. Volume is converted to float before its type is checked, accepting boolean volume. Fetching also does not establish the price-adjustment basis used relative to current trading prices.

    Duplicate pages can inflate nodes; mixed-symbol input can create unrelated support; inconsistent adjustment across a split can place levels on the wrong price scale.

    Fix: canonicalize and verify symbol, validate complete OHLCV geometry, deduplicate by symbol/timestamp with a defined revision policy, and specify/record the adjustment basis. Reject inconsistent history with a specific error.

14. P1 — Bucket allocation is not uniform volume across the price range.  
    `volume_profile.py`, `build_profile`: every touched bucket receives an equal share, regardless of how much of the bar overlaps it. An exact upper boundary also adds another bucket through `floor(high / width)`.

    With width 1 and range `[100.99, 102.00]`, the implementation allocates one third to each of buckets 100, 101, and 102. Almost all actual range length lies in bucket 101; bucket 102 has zero positive-length overlap.

    Fix: allocate by overlap length, handling zero-range bars separately. If equal allocation among touched buckets is intentionally the approved proxy, state that explicitly; it is a different distribution from uniform-by-price allocation. The discontinuous qualifying-run logic does flush and restart correctly.

15. P1 — Support selection can exempt the obstacle the road gate should examine.  
    `volume_profile.py`, `support_node`, `path_obstacle`: the highest-volume qualifying node wins even when the extreme is actually inside another node.

    For a long with ATR 1 and extreme 100, node A `[99.8, 100.0]` contains the extreme. A larger node B `[100.2, 100.8]` also qualifies through the tolerance. B wins and is excluded. From entry 100.3 to target 101.8, B covers one third of the road, but the gate ignores it.

    Fix: define support selection explicitly—actual containment before tolerance, then proximity and an appropriate directional tie-break—or reject ambiguity. Exempt only the node selected under that rule. Identity comparison works in the supplied call chain because the selected object comes directly from the same profile; the unsafe ambiguity is which object gets selected.

16. P1 — The road is calculated against a different target from the signal.  
    `vwap_pullback_v2.py`, `evaluate_bar`: `_target` uses the unrounded distance before both stop-cap checks. The actual signal rounds the stop away from entry, recomputes distance, and rounds the target. Near the 10% overlap threshold, these differences can change the obstacle result.

    An already-invalid stop can also produce a retaining `HVN_OVERHEAD` event instead of the stop rejection that would discard the setup.

    Fix: calculate and validate executable entry/stop/target once in a pure helper. Use those exact values for profile evaluation, events, and `SignalEvent`.

17. P1 — Locked quotes are not consistently ignored.  
    `tick_tape.py`, `on_quote`, `_quote_for`, `spread_stats`, `book_imbalance`: locked quotes enter quote history and book state, increment quote counts, and overwrite the second’s last real spread with `-1`. A lock after a valid quote in the same second erases that spread observation. A later valid quote can make intervening locked quotes count toward `min_now_quotes`.

    Fix: separate feed progress from valid quote observations. Ignored locks should not replace usable measurement state or inflate valid-quote counts. Define their classification behavior explicitly and preserve crossed-market invalidation until a strictly valid quote arrives.

18. P1 — Spread seeding stops at a trade-only bucket.  
    `tick_tape.py`, `spread_stats`: the backward search breaks on the first bucket before the reference window, even if that bucket contains no quote. A valid quote a second earlier is missed despite being within the thirty-second carry limit.

    This can remove enough covered seconds to turn an otherwise valid reference into unavailable data.

    Fix: search backward until a usable quote or invalidation is found, stopping when the maximum carry age is exceeded. Do not stop merely because a second bucket exists.

19. P1 — Start-of-second sampling and staleness boundaries are inconsistent.  
    `tick_tape.py`, `spread_stats`: a quote timestamped exactly at a second’s start should apply to that second, but the implementation samples the old state before processing it. Conversely, freshness is tested at the second’s end, rejecting some states that are fresh at its start.

    The bucket retains only the last quote, so it cannot always recover an exact-boundary quote when additional quotes followed within that second.

    Fix: define quote validity intervals and sample the state at the actual second-start timestamp. Apply the thirty-second age limit at that same timestamp, with explicit boundary tests.

20. P1 — A partial “now” window is reported as complete.  
    `tick_tape.py`, `spread_stats`: `not now` is the only coverage test for the ten-second window. One represented second can pass if three quotes were counted elsewhere in the window. `now_seconds` and quote freshness are not reported.

    Three quotes concentrated at the beginning are permitted by the stated thirty-second carry policy; their concentration alone is not a defect. The defect is treating quote count as adequate evidence when most of the requested window has no represented state. The additional three-quote requirement can also reject a fully covered window maintained by fewer quotes, although that requirement is absent from the stated rule.

    Fix: specify and enforce current-window coverage independently from quote count, and report covered seconds, freshness, and unavailable reasons.

21. P1 — Historical cutoff queries are mutable, and arbitrary cutoffs are not exact.  
    `tick_tape.py`, `session_delta`: a late-arriving print changes a previously queried minute, so querying the same cutoff again can produce a different answer. An already-returned decision dictionary is not retroactively mutated, but the claimed immutable historical-query behavior is absent.

    Also, `m1 = t_ns // minute` discards the entire final partial minute for a non-minute-aligned cutoff. The evaluator does not validate minute alignment of incoming bars.

    Fix: distinguish event time from availability time. Finalize/version decision snapshots under a defined lateness policy. Either enforce minute-aligned bar/query boundaries or retain enough detail to trim both edge minutes exactly.

22. P1 — `partial` is calculated incorrectly and omitted from decision evidence.  
    `tick_tape.py`, `session_delta`; `vwap_pullback_v2.py`, `evaluate_bar`: comparing the restart minute with the earliest minute containing a trade misses same-minute restarts and cases where the first eligible trade occurs after the restart. A process restart also loses the original session provenance.

    Neither `RESUMPTION_MEASURED` nor signal features preserves `partial`, `since_ns`, and cutoff together. The operator-facing “on the day” text can describe only post-reconnect flow.

    Fix: track session origin and feed-generation provenance directly. Persist or explicitly mark uncertain restart provenance. Include start, cutoff, partial status, and availability reason in events and signal evidence, and make the displayed description match the measured interval.

23. P1 — Tape “session” means ET calendar date, with no regular-session boundary.  
    `tick_tape.py`, `_touch_session`, `on_quote`, `on_trade`: valid premarket events can start warm-up and contribute flow. There is no 09:30 reset here. Thus the implementation does not itself establish a regular-session cumulative delta or a warm-up beginning at the regular open.

    Fix: specify whether session delta includes extended hours. If it means the strategy’s regular session, initialize/filter using that session’s boundaries. Cache those boundaries rather than deriving policy from whichever event arrives first.

24. P1 — Required failures do not reach the ordinary decision explanation path.  
    `main.py`, `_strategy_cards`, `_record_setup_event`; `backend/app/core/decisions.py`, `OUTCOME_TEXT`; `vwap_pullback_v2.py`, `on_bar`: adding outcome text does not route evaluator rejections into admission decisions. Those branches return no signal. The supplied event sink is research-only and exits when research is disabled.

    The card reports missing profiles only when every configured symbol lacks one. One successful profile hides partial readiness. Per-symbol fetch errors do exist in `/health`, but that does not explain ordinary setup rejection, swallowed measurement exceptions, or why a particular symbol never trades.

    Fix: publish per-symbol gate readiness and the latest blocking reason independently of research recording. Route evaluator blockers into an operator-visible decision stream. Show partial profile availability, current-session validity, worker status, retry state, and effective enforcement settings.

25. P1 — The profile universe can differ from the evaluated universe.  
    `main.py`, `PROFILE_SYMBOLS`, `_fetch_relay_bars`, `_strategy_cards`; `vwap_pullback_v2.py`, `on_bar`: profile symbols are frozen at import, while `on_bar` accepts any non-excluded symbol. Any later-delivered symbol outside that list can be evaluated indefinitely without ever receiving a profile or appearing in the card’s missing-profile calculation.

    Fetch lookup also uses the unnormalized input symbol as the response dictionary key. If upstream settings permit lowercase symbols and the endpoint returns uppercase keys, valid returned bars are silently treated as absent.

    Fix: use one canonical, deduplicated universe for subscription, evaluation, readiness, and rebuilding. Trigger builds when that universe changes, and normalize response keys.

26. P1 — Checkpoint migration and effective enforcement are unverified.  
    `vwap_pullback_v2.py`, `params`, `after_restore`; `main.py`, strategy initialization and health: `after_restore` immediately reads newly added attributes through `params()`, before its per-symbol exception handling. A restored pre-change object missing those attributes would fail there. A restoration scheme that overwrites current configuration could also restore disabled enforcement while health reports the current settings value.

    The supplied `_restore_checkpoint` implementation is absent, so neither failure can be declared proven. Neither is covered by the supplied evidence.

    Fix: explicitly migrate old strategy state, reapply current operator policy, validate the live enforcement invariant, and report the strategy’s effective configuration. Test an actual pre-change checkpoint. The intentional `tick_gates=False` rebuild does not itself send orders; its returned signals are discarded.

27. P1 — The replays do not demonstrate enforcement of these gates.  
    `scripts/run_ride_the_trend_v2_dry_run.py`, `run`, diagnostics: `would_fail` evaluates measurements recorded before earlier gates return. It therefore counts candidates that never reach an add-on gate, and repeated bars from one setup are counted separately.

    The supplied funnels contain no named add-on rejection events and no signals. They do not demonstrate either successful passage through all gates or the intended rejection transitions. `TICK_UNAVAILABLE` without its `where` detail does not establish which gate was exercised. The repeated all-arms result adds no demonstrated coverage of those paths.

    Fix: report candidate counts reaching each enforced gate, actual rejection events with details, unique setups, and signals passing all gates. Exercise each add-on with all preceding gates satisfied.

28. P1 — Replay/live equivalence and timing are not established.  
    `run_ride_the_trend_v2_dry_run.py`, `load_prior_bars`, `run`; `main.py`, `rebuild_volume_profiles`: the replay scans all cached history and hardcodes the loader’s default five sessions. Live fetching uses a fourteen-calendar-day horizon and configured session count. Missing recent data or changed settings can produce different profiles.

    Filtering dates strictly before the replay day excludes direct target-session bar leakage. It does not establish point-in-time bar revisions, adjustment consistency, or arrival-order equivalence. Those remain unverified, rather than proven look-ahead.

    Forty-two to fifty-three seconds per replay session says nothing about worst-case live callback latency, queue lag, or profile-building interference.

    Fix: share profile-input selection and record data provenance. Test delayed arrivals/revisions under a defined availability model. Measure live-path latency and backlog while profile fetching/building runs.

29. P2 — Several exposed parameters and messages misrepresent behavior.  
    `vwap_pullback_v2.py`, `V2Params`, `params`, feature construction: `hvn_overhead_atr` is exposed and recorded but no longer affects the road test. `hvn_min_sessions` is unused. `volume_profile.py` still describes the old ATR-distance overhead query.

    `ProfileStore.build` reports every `None` result as insufficient sessions, including zero usable volume or an invalid reference price.

    Fix: remove obsolete knobs or wire them into the approved rule; update documentation; return specific build failure reasons. Report the effective 10:09 strategy eligibility separately from the approximate thirty-minute feed warm-up.

30. P2 — Tiny references and hot-path work need explicit treatment.  
    `tick_tape.py`, `spread_stats`, `_touch_session`: any positive reference spread is accepted, making ratios arbitrarily sensitive near zero; overflowed ratios become generic unavailable measurements. Every accepted quote/trade also performs imports and ET date conversion, even while remaining within the same session.

    Fix: define/report an economically meaningful quote-resolution policy without silently weakening the ratio rule. Cache imports/timezone/session boundaries, use integer timestamp boundaries, and measure ingestion latency under representative load.

The test-name section is empty. That does not prove tests do not exist, but none were supplied for these P0 paths. At minimum, the missing regression evidence is:

| Path | Required assertion |
|---|---|
| Obstacle query raises after support succeeds | No signal; explicit unavailable reason |
| Cross immediately before decision, then valid recovery | No invalid-period spread/book/classification reuse |
| Mid-minute reconnect followed by a delayed pre-gap event | Exact restart interval; no premature warm-up |
| Cap eviction followed by retransmission | Session volume counted once |
| Quotes continue while trade delivery stalls | Session flow remains unavailable |
| Reconnect after more than one hour | Current events recover without accepting arbitrary future timestamps |
| Session changes/reset occurs during history fetch | Obsolete worker cannot publish or delete current state |
| Repeated pagination token or extreme bar range | Bounded failure without blocking the trading loop |
| Every add-on independently fails, then all pass | Exact transitions and a successful signal through the production enforcement path |

The three findings that matter most are the obstacle query failing open, crossed quotes remaining usable across spread/book/classification, and session totals being contaminated or falsely certified after gaps and delayed delivery.

Go-live tonight: No. Single condition: all P0 findings must be fixed and demonstrated by passing regressions through the production enforcement path.

---

## Triage (Claude, applied before commit)

Fixed (all 10 P0): 1 profile query errors fail closed (`PROFILE_UNAVAILABLE` with the error); 2 crossed quotes stamp an invalidation that the spread and book carry-forward respect (no state from before the cross survives it); 3 nothing is classified, not even by the tick rule, between a cross and the next valid quote; 4 a reconnect or point gap starts the new feed generation AT the mark and only full minutes after it count, `partial` derived from the session's first regular-session event; 5 a print stamped inside an already-evicted second is refused (retransmissions cannot be de-duplicated, so they cannot be counted twice); 6 a separate trade watermark must have reached the cutoff minus 60 s for any delta window or the session total to be complete; 7 future-time plausibility uses the wall clock (a day), so a long outage never freezes ingestion; 8 profile bars validated (open/close inside the range, symbol, boolean volume), bars spanning more than 2,000 buckets skipped, pagination capped at 50 pages with repeated-token detection, whole build bounded by a 300 s deadline, CPU build in a worker thread; 9 rebuilds carry a generation token, a running build is cancelled and superseded, reset bumps the generation, superseded results are never published; 10 the session aggregates never roll backward (earlier ET dates are ignored) and only regular-session events touch them.

Fixed P1s: 11 five sessions required (store and builder), configuration validated; 12/13 sessions need >= 300 bars each, bars de-duplicated by timestamp; 14 allocation by overlap length; 15 a node containing the extreme wins, else the nearest within tolerance; 16 one shared executable stop/target helper (rounded, floor and cap) used by the road test, the events and the signal, invalid stops reject before the profile gates; 17 locked quotes record no spread and enter no classification history; 18 the spread seed search skips trade-only seconds within the carry limit; 20 the now window needs at least half its seconds covered and reports them; 22 `partial`, `since` and `cutoff` recorded in events and features; 23 regular-session filter; 24 per-symbol last block reason on the card (`last_block_by_symbol`) and a profiles-ready count with the missing symbols; 25 canonical uppercase universe and response keys; 29 obsolete knobs removed.

Accepted as documented limits: 19 start-of-second sampling at one-second resolution; 21 a late print can change an earlier minute for later queries (decisions already taken are not rewritten); 26 checkpoint restore keeps code-owned configuration (tested round trip); 27/28 the replays record per-evaluation observations, not an enforcement replay, and no replayed session produced a signal, so the joint pass of every enforced gate is proven only by the fake full session; 30 tiny reference spreads are accepted as measured.
