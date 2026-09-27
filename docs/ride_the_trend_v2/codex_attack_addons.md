# Codex attack on PLAN_2026_09_27_ride_the_trend_addons.md (draft v1)

Model gpt-6-astra, reasoning xhigh, read-only, no tools; plan + live evaluator/tape excerpts + replay funnels inlined. 2026-09-27.

1. P0 — §§1, 4, 6: `session_delta(symbol)` has no decision cutoff.  
   The existing measurements query through `bar_end_ns`; the proposed accumulator returns whatever has arrived by processing time. A delayed bar can therefore use trades from later bars. A replay that preloads the tape can give an early candidate the entire day’s delta.  
   Fix: require `session_delta(symbol, t1_ns)` over `[session_open, t1_ns)`, backed by timestamped aggregates. Define late-arrival handling and distinguish event time from arrival time. Test that appending events after the cutoff cannot change an earlier decision.

2. P0 — §§1, 6: the session accumulator lacks a safe session and reconnect boundary.  
   “First event” can mean premarket, a quote, or an event after a midday deployment. The supplied `_advance` never changes `session_start_ns` on a new trading date. After reconnect, admissible late prints from before the new `since_ns` could contaminate restarted totals unless explicitly excluded. Resetting only on reconnect also misses `note_gap`.  
   Fix: key totals by exchange session and continuity epoch; filter regular-session timestamps explicitly; update totals only after eligibility and deduplication; exclude events outside the accumulator’s epoch. Clear or invalidate classification state across gaps. A missing opening segment makes session delta unavailable until recovered from an authoritative source; thirty minutes of subsequent data does not reconstruct it.

3. P0 — §2 and tape ingestion: crossed quotes do not invalidate the stored history; locked quotes can produce a zero denominator.  
   On a cross, `on_quote` clears `t.quote` but leaves `quote_hist` and the last valid bucket quote intact. `_quote_for` can still classify subsequent trades against the earlier quote, and book-style reconstruction can carry it forward. A spread implementation using those buckets inherits that defect. Locked quotes are accepted and can make `spread_ref == 0`.  
   Fix: record timestamped invalidation events consumed by classification, book and spread queries. Define locked-market treatment explicitly. Require a finite, strictly positive reference before division; return a reasoned unavailable result otherwise. Never silently discard an invalid quote while continuing the previous valid state.

4. P0 — §3: the overhead predicate misses obstacles it claims to exclude.  
   A node beginning 1.2 ATR above entry passes, although the stated first target is at least 1.5 ATR away. Structural stops, the percentage floor and VIX scaling can increase that distance further. A node containing entry also passes because its `low` is not above entry. For example, entry 100.2 inside `[100, 101]` ignores all the node’s volume above entry.  
   Fix: calculate the rounded stop and actual target before this gate. Test node interval intersection with the entry-to-target path, mirrored for shorts. Define whether entering inside the support node is permitted and what breakout evidence exempts that node; otherwise support and obstruction rules can contradict each other.

5. P0 — §§3, 6: profile validity and publication do not establish causality.  
   “Built today” can describe a profile using stale sessions, today’s bars, or an intraday “last close.” Checking only that a profile is not older than the current date also does not reject a future-dated replay profile. Concurrent rebuilds can publish results for the wrong session or expose partially replaced state.  
   Fix: publish immutable profiles atomically, tagged with the exact target session, source-session dates, source cutoff, prior-session reference close, adjustment policy and generation ID. Reject obsolete build results. Require every source bar to precede the target session. Read one profile version per snapshot. Building after the open is not itself look-ahead; including information unavailable at the decision is.

6. P0 — §§4, 6: “background task” does not guarantee isolation from the bar loop.  
   Synchronous HTTP calls, retry sleeps or CPU work inside an async task can still block the event loop. New measurements execute before even `NO_UP_CLOSE`, so an unchecked malformed profile, zero division or query exception can affect candidates that would otherwise reject immediately.  
   Fix: keep network operations outside evaluation, use bounded asynchronous requests or a worker, and bound retries and total build time. Make measurement queries pure and return typed unavailable results for expected data failures. Verify bar processing continues during slow responses, failed authentication, malformed payloads and profile rebuilds.

7. P1 — §1: reconnect delta is falsely labeled “net buying on the day.”  
   Restarting at 10:15 and passing at 10:45 produces a different indicator from uninterrupted operation. The same market history can yield opposite decisions solely because one instance disconnected.  
   Fix: choose an explicit contract: recover full-session data and retain the session label, or rename it “delta since reconnect” and treat it as a separate indicator. Do not silently substitute one for the other.

8. P1 — §§1, 7: cumulative sign has neither a recency requirement nor a meaningful strength threshold.  
   Opening buying of +1,000,000 shares followed by −200,000 shares still passes a long. A net +1 share also passes despite potentially large inferred and unknown volumes. At approximately 10:00, a complete session delta and the 30-minute delta cover the same interval; later they can oppose each other. The corresponding short can be blocked by old buying despite current selling.  
   Fix: specify which horizon controls entry and what disagreement means. Measure directional agreement, sign stability and sensitivity to inferred trades. Any enforced directional threshold needs evidence beyond a zero crossing; duplicating horizon gates does not establish independent confirmation.

9. P1 — §1: whole-session classification floors hide current degradation.  
   A well-classified opening burst can keep both floors satisfied after quote classification deteriorates. Elapsed time since an event or reconnect does not establish continuous trade and quote delivery. A quote-only stream can advance the supplied watermark while trades are missing.  
   Fix: require recent classification quality and quote freshness alongside session aggregates. Track trade and quote subscription health separately, with explicit outage evidence. Report quote-derived and tick-inferred signed flow separately; a 20% quote share does not establish that the aggregate sign is reliable or represent level-two depth.

10. P1 — §2: last quote per second is an endpoint sample, not time-weighted spread.  
    A wide spread lasting 990 milliseconds followed by a tight quote for 10 milliseconds makes the entire second appear tight. Conversely, excluding seconds without updates underweights stable quote states. Three quotes can all occur within one second near the beginning of the ten-second window. A median can also remain tight while the latest executable spread has widened sharply.  
    Fix: retain quote transitions and calculate a duration-weighted distribution with bounded carry-forward and invalidation. Require covered duration and maximum quote age. Add a fresh executable-quote check at order admission if this is intended to constrain transaction cost.

11. P1 — §§1–2: the warm-up rules imply inconsistent earliest entry times.  
    Three hundred covered seconds means five minutes of observations, not thirty minutes of history. At 09:45, fifteen session minutes exist and can satisfy that count. If the implementation instead requires complete coverage of `[t−30m, t)`, it cannot pass before approximately 10:00. By 10:09, a full thirty-minute session window exists. Independently, the session-delta requirement blocks 09:45–09:59 with a 09:30 start.  
    Fix: specify elapsed-history, coverage-fraction and observation-count requirements separately. Either accept an explicitly labeled partial-session reference or declare the later effective entry window. Test exact opening boundaries and late starts.

12. P1 — §§2, 7: the spread calibration does not measure the proposed gate.  
    Signal-minute ratios cannot validate a trailing-ten-second predicate. Three PLTR evaluations may be repeated observations of one setup, not three independent setups. The supplied median and maximum establish neither rejection probability nor benefit. With an 11.4 bps reference, the ratio rule caps spread at 17.1 bps; the 30 bps cap is inactive. It becomes binding only when the reference reaches 20 bps.  
    Fix: recompute both windows exactly at each decision cutoff, group repeated evaluations by setup, and report results by symbol and time. Evaluate spread relative to target and stop distance, including execution costs. A cap “above normal COIN spread” is not a cost justification.

13. P1 — §3: minute OHLCV cannot identify the asserted price-level volume.  
    The same minute bar can represent volume concentrated at its low, its high or its middle. Uniform allocation produces identical nodes for all three. Giving equal weight to every touched bucket also overallocates volume to barely touched boundary buckets.  
    Fix: use historical trades for a volume-at-price gate. If retaining bars, label this an estimated occupancy profile and establish stability under alternative plausible allocations before enforcing it. Weighting by range overlap and conserving volume fixes allocation bookkeeping, not the missing information.

14. P1 — §3: grid resolution and the median threshold can manufacture or erase support.  
    One shared width across five sessions is internally consistent; it does not inherently drift within that build. The problems are an unspecified grid origin, shifting boundaries on rebuild, and resolution unrelated to ATR. A bucket can be wider than the entire support tolerance. Sparse low-volume buckets change the non-empty median and thus change nodes elsewhere.  
    A flat positive profile produces zero nodes at a 1.5× median threshold. Literally making every occupied bucket one qualifying node is impossible under that rule, but one broad node can still cover the entire relevant trading range and make support almost automatic.  
    Fix: specify a tick-aligned grid and origin, test shifted-grid and width sensitivity, and constrain node width relative to the trading geometry. Treat a valid zero-node profile distinctly from missing data. Do not tune the threshold merely until nodes appear.

15. P1 — §3: proximity to the pullback extreme does not prove that the pullback found support.  
    The long tolerance accepts an extreme below the node, inside it, or above it without touching it. These describe different price behavior. The supplied snapshot exposes `ext_close`, while this rule requires `ext.low` for longs and `ext.high` for shorts. Moreover, `support_node(price, atr)` cannot apply the advertised asymmetric short mirror without receiving direction elsewhere.  
    Fix: define whether the rule requires touching, rejecting or reclaiming a node, and use the actual extreme. Pass direction explicitly. Long proximity is `[low−0.25 ATR, high+0.50 ATR]`; the mirrored short interval is `[low−0.50 ATR, high+0.25 ATR]`. Test asymmetric examples.

16. P1 — §§3, 6: counting session dates does not validate historical data.  
    Three dates with one bar each could satisfy the stated minimum. Truncated pagination, duplicate bars, missing intervals or extended-hours data can change the profile without making it “unavailable.” Splits can place old prices on a different basis. Earnings or unusually active sessions can dominate raw volume, while gaps and halts change the occupied-price distribution.  
    Fix: validate pagination, unique timestamps, exchange-calendar session bounds, bar integrity and per-session completeness, distinguishing scheduled closures and halts from missing data. Align historical prices and volumes with the current execution basis. Report each session’s contribution and test sensitivity to dominant event sessions using rules fixed before evaluation.

17. P1 — §§3, 6: the relay dependency and startup behavior remain untested assumptions.  
    Existing relay use does not prove this endpoint accepts the same token, supplies the required feed and adjustments, or returns the complete history. A rebuild launched at the open can leave profiles unavailable during valid setups. Depending on failure transitions, those setups can be discarded permanently before the fetch succeeds.  
    Fix: verify the actual paginated historical request from the deployment environment, with bounded deadlines and explicit error statuses. Prebuild before the open and load a validated artifact on startup where possible. Define behavior when readiness misses the trading window; health must show the reason and affected symbols.

18. P1 — §§4, 6–7: the funnels provide no evidence of useful incremental rejection or joint reachability.  
    There are 22 evaluations and eight entries labeled feasible, but “feasible” is undefined. Neither those labels nor the recorded-only failure counts establish that a candidate passes the enforced strategy. With zero baseline signals, rejection share among baseline signals is undefined. Adding conjunctions cannot rescue those same candidates. New reset behavior can change future candidates, so even monotonicity requires care in this state machine.  
    Fix: produce a per-evaluation table containing all old and new predicates, availability reasons, trading-window eligibility and setup ID. For each new gate report evaluated, available, independently failing, reached, and incremental rejection counts. Show the joint intersection. Use broader untouched sessions to obtain actual reachable candidates; the supplied aggregates cannot answer whether the new gates ever pass together.

19. P1 — §§4–6: enforcement and failure transitions are unspecified.  
    Existing failures sometimes preserve `RESUMING` and sometimes reset to `IDLE`; the new gates specify event names without equivalent state behavior. A temporary data failure and permanent lack of support need not have the same lifecycle. Copying the existing `... and tick_gates` pattern can also silently bypass supposedly mandatory add-ons when tick gating is disabled.  
    Fix: define a transition table for every rejection and unavailable result, including retry and expiry. Validate mandatory data dependencies at startup and explicitly reject unavailable inputs regardless of unrelated flags. Record the effective four-gate enforcement set and thresholds; the current `enforced_gates` feature would otherwise describe only part of the policy.

20. P1 — §6: the proposed validation can pass while proving almost nothing about production behavior.  
    An injected profile bypasses fetching, readiness, session selection and publication. A fabricated all-pass session establishes wiring only. A zero-signal replay cannot show preserved opportunities or improved selection. Constructing bars to produce a known node validates the builder against its own assumptions, not the node’s market meaning.  
    Fix: add:
    
    - Prefix-invariance tests for future trades, quotes and profile bars; replay using the same arrival and cutoff semantics as live.
    - Session rollover, premarket, late-start, disconnect, point-gap, duplicate and late-print tests.
    - Locked/crossed quote invalidation, stale three-quote bursts, zero reference and exact warm-up boundary tests.
    - Target-path intersection and asymmetric short-support cases.
    - Real-fetch integration covering pagination, partial data, authentication failure, timeout and an obsolete rebuild completing last.
    - Separate evidence for synthetic order-path execution, replay reachability and observed paper-session behavior.

    Cached prior-session bars are not automatically look-ahead. Audit their session membership, reference close, adjustments and revision availability; disclose where historical point-in-time fidelity cannot be established.

21. P2 — §§5–6: the deployment checks cannot distinguish working enforcement from universal rejection.  
    Profile build time, session count and a spread reference can all look healthy while every candidate is unavailable or rejected.  
    Fix: expose effective policy version, data cutoffs, coverage, readiness reasons, and per-gate reached/pass/reject/unavailable counters. Verify those counters during actual paper-session operation; a healthy process and unchanged card do not verify the strategy.

(a) The three findings that matter most

- The session accumulator lacks a decision-time cutoff and a defensible session boundary.
- The overhead gate ignores nodes inside the actual target path, including nodes containing entry.
- Zero baseline signals and mismatched spread measurements provide no evidence that the enforced combination is reachable or beneficial.

(b) What to cut: the reconnect-as-session substitution, day-one enforcement of unvalidated bar-derived HVNs, and the fixed 1 ATR overhead shortcut. Cut the claim that the supplied spread statistics justify these thresholds.

(c) What to add: causal timestamped measurements, explicit continuity and quote-validity contracts, target-aware node geometry, immutable validated profiles, a complete failure-transition table, and candidate-level reachability evidence from untouched sessions.

---

## Triage (Claude, applied in the build)

Fixed: P0-1 (session delta is per-minute aggregates queried with the bar-end cutoff; minutes starting at or after the cutoff never count; the feed must have caught up to the cutoff); P0-2 (aggregates keyed by ET session, regular session only, restart on reconnect AND on point gaps, `partial` reported when the total does not start at the session's first event; the gate uses it and says so); P0-3 (a crossed quote clears the quote history and stamps an invalidation so no print is classified against a quote from before it; a locked market records no spread and is skipped by the spread statistic; a non-positive reference is unavailable); P0-4 (the overhead test intersects the node with the actual entry-to-first-target path, computed with the same stop rule, and excepts the support node; `min_overlap_frac` 10%); P0-5 (profiles carry the exact target session, source sessions and reference close; `get()` refuses any profile not built for the current session; bars dated on or after the target session never enter a profile); P0-6 (the fetch runs as an asyncio task with per-request timeouts and bounded pagination; every profile query in the evaluator runs through `_safe`); P1-10 (spread carried forward across quiet seconds, state at the start of each second); P1-15 (support uses the pullback extreme low/high with the direction passed explicitly); P1-19 (transitions: SESSION_DELTA_AGAINST, SPREAD_WIDE and HVN_OVERHEAD keep the setup for the next bar; HVN_NO_SUPPORT and PROFILE_UNAVAILABLE discard it; TICK_UNAVAILABLE discards it; add-on gates require the tape layers, validated at startup).

Accepted as stated limits (operator chose day-one enforcement): P1-7/8 (a restart makes it "delta since reconnect", labeled partial; zero-crossing sign test with no strength threshold); P1-9 (whole-session classification floors); P1-11 (effective earliest signal about 10:00 because the session total needs 30 minutes of feed); P1-12 (the 1.5x / 30 bps lines come from 22 signal-minute observations, not a rejection study); P1-13/14/16 (minute bars give an occupancy estimate, uniform allocation, grid anchored at zero, no split adjustment, no event-session weighting); P1-17 (relay history fetch verified from this machine, not yet from Railway at startup: health shows per-symbol errors and the card blocks when no profile exists); P1-18/20/21 (replays produced no signal, so joint reachability of the enforced set is unproven; per-gate event counters are on the card and in health).
