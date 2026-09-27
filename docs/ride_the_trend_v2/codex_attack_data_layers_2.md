# Codex attack on PLAN_2026_09_27_ride_the_trend_data_layers_2.md (draft v1)

Model gpt-6-astra, reasoning xhigh, read-only, no tools; plan + live evaluator + live tape + three dry-run funnels inlined. 2026-09-27.

I’ll review only the inlined plan, source and funnels, with no tools or file access. I’ll distinguish proven contradictions from risks that need replay measurements.
The absorption veto is mathematically unreachable after the existing slope gate. The unchanged classifier also counts tick-rule guesses as classified volume, so its coverage check cannot validate aggression. The three zero-signal replays leave no executed candidates for assessing the added filters.
1. P0 — §2: The absorption veto is mathematically unreachable after the slope gate.  
   Let \(a\) be bars since the extreme. The existing slope gate requires directional movement from the extreme bar’s close of at least \(0.5a\,ATR\), with \(a \ge 1\). Movement from that bar’s low for a long—or high for a short—is at least as large. Absorption requires movement below \(0.25\,ATR\). Those conditions cannot coexist. Moving the absorption check earlier only rejects candidates the slope check already rejects.

   Fix: Remove this veto. Research absorption on a separately defined interval, using signed flow versus price response. Do not promote it until it rejects otherwise eligible candidates and those rejections have measurable value.

2. P0 — §§1–2 / `TickTape.on_trade`: The classifier can use future information relative to a print’s timestamp.  
   Only the newest quote survives. If a late print arrives after a newer quote, its valid historical quote is unavailable. The fallback then compares it with `_last_price`, which follows arrival order and can belong to a later event timestamp. The late print subsequently overwrites `_last_price`, contaminating following classifications. Restricting quote timestamps does not make the complete classifier causal.

   Fix: Retain quote history, define bounded event reordering, and classify against eligible preceding quotes and trades. Preserve event time and receive time separately. Specify what happens to events arriving after the decision cutoff.

3. P0 — §§2, 5: `classified_share >= 0.5` does not establish trustworthy aggression.  
   Tick-rule guesses count as classified, including unchanged prices inheriting the previous side. A quote outage can therefore produce nearly 100% “classified” volume. Even with perfect classification of the known half, a resumption ratio of \(+0.10\) at 50% coverage permits true full-volume signed imbalance anywhere from −45% to +55%.

   Fix: Report quote-classified, tick-inferred and unknown volume separately. Measure quote age, spread state and classification stability. Apply uncertainty bounds or empirically validated quality requirements; do not treat this percentage as confidence in aggressor identity.

4. P0 — §§0–1: The cap can silently turn a requested window into a different window.  
   A 500,000-print cap supports 60 minutes only below 139 prints/second per symbol. At 500 prints/second it retains 16 minutes 40 seconds; at 1,000 it retains 8 minutes 20 seconds. A requested 30-minute delta could become an eight-minute delta and still pass classified-share checks.

   The supplied daily totals do not establish NVDA or TSLA’s peak per-symbol rates.

   Fix: Every query must return coverage and completeness for the requested interval, including cap eviction, connection gaps and recovery status. Reject incomplete windows. Size retention from measured per-symbol rolling counts and bursts, or retain exact aggregates for longer-window calculations.

5. P0 — §§1–3, 6: Event-time boundaries alone do not prevent look-ahead.  
   An ETF bar or late print can have an eligible timestamp but arrive after the stock’s decision. Fetching completed historical ETF bars and sorting everything by event timestamp can make unavailable information appear available. Conversely, processing the stock bar before contemporaneous ETF bars arrive can reject a valid candidate solely because of callback order.

   Fix: Define three times: interval end, information arrival and decision deadline. Replay arrival order where recorded. Use a bounded synchronization policy and immutable decision snapshots. Future arrivals may revise research truth, but must not rewrite what the bot knew when it decided.

6. P0 — §§1–2, 6: Restart and feed-gap handling does not preserve session cumulative delta.  
   Discarding unfinished setups does not restore signed volume since 09:30. Bars cannot reconstruct it. Restoring a cumulative total without its exact ingestion position can double-count replayed prints or omit reconnect gaps. Late prints also invalidate previously stored minute prefixes.

   There is a useful distinction: a constant missing opening offset cancels in a continuous, complete 30-minute difference. A missing interval inside that window does not.

   Fix: Persist cumulative state with an idempotent ingestion cursor and reconcile reconnect history, or explicitly mark session cumulative delta unavailable. Independently allow the rolling gate only after its entire interval is complete. Define late-event updates to snapshots and the supported range of arbitrary-time cumulative queries.

7. P0 — §1: The proposed “raw” record omits information needed to prevent materially wrong delta.  
   Timestamp, price, size, side and an undefined `seq` do not identify duplicate deliveries, cancellations, corrections, venue or trade conditions. An auction, late report or other condition may contribute to the tape differently from the bars. A large duplicated or ineligible print can dominate every flow gate.

   Fix: Preserve provider identifiers, conditions, venue, timestamp provenance, receive order and correction relationships. Define eligibility consistently with the strategy’s price and volume measurements. Make replay idempotent and corrections explicit. A locally assigned sequence number is not a provider trade identifier.

8. P0 — §0 / existing `book_imbalance`: The retained book gate can be dominated by quote-update frequency.  
   It sums displayed sizes once per update. Repeated bid-heavy updates can outweigh a longer ask-heavy state even when very little time was spent bid-heavy. NBBO size also provides neither full depth nor proof of spoofing.

   Fix: Measure time-weighted inside imbalance with quote coverage and state duration. Test invariance to repeated identical quotes. Keep any spoof-detection claim out of this layer; genuine order-book OFI would require explicit treatment of price and size changes.

9. P1 — §§0, 1, 6: The RAM estimate depends on an unspecified representation.  
   Packed arrays using 8-byte timestamp, price, size and sequence plus a 1-byte side need 33 bytes/print: 16.5 MB per capped symbol, or 181.5 MB for 11 symbols, before other storage. Python lists holding Python numeric objects can instead consume roughly 140–180 bytes/print depending on representation—approximately 770–990 MB across 11 capped symbols.

   The retained second buckets, quote history, temporary slices, serialization and allocator overhead are additional.

   Fix: Specify dtypes and allocation strategy. Benchmark process RSS under realistic bursts with every symbol populated. Bound temporary allocations, not just the number of retained prints.

10. P1 — §§1, 6 / `_bucket`: Late-event trimming is already defective, and the new design leaves ordering unspecified.  
    Immediately after `series[sec] = b`, the newly inserted key is necessarily the last insertion. Therefore `next(reversed(series)) != sec` is false, and the intended late-second sorting branch never runs. An old second can sit behind newer ones and escape head-only trimming.

    Parallel raw arrays have the same design problem unless event ordering, late insertion and eviction are specified.

    Fix: Use a monotonic retention watermark and an explicitly ordered structure. Reject events outside retention without disturbing current classification state. Test late events older than the retained head, equal timestamps and large forward timestamp anomalies.

11. P1 — §1: Nanosecond storage does not establish microsecond “who hit first.”  
    Timestamp resolution does not establish clock accuracy, cross-venue synchronization, aggressor identity or a total causal order. SIP arrival order and reported event order are different observations. The current `_ns()` also passes through floating-point seconds; it is unsuitable as a general arbitrary-nanosecond conversion, even though ordinary whole-minute boundaries avoid that particular precision problem.

    Fix: Preserve provider integer timestamps without float conversion, document their actual meaning, and store receive order separately. Define equal-timestamp tie behavior. Describe the result as inferred signed flow ordered by reported timestamps, with no cross-venue microsecond causality claim.

12. P1 — §§2–3: This adds vetoes to a strategy that already produced zero signals.  
    At one explicit gate-family granularity, the evaluator already has:

    - 13 setup/market checks: eligible impulse, valid touch, touch PVR, touch delta, pullback timeout, extreme age, directional close, slope, chase, final PVR, final delta, velocity and book.
    - 6 readiness/execution constraints: valid history/features, session/window, emission permission, cooldown, daily cap and stop validity.
    - The plan adds 4 delta checks and 6 regime checks, counting IEF and SHY separately.

    That is 29 grouped checks before the existing admission filters. The exact total cannot be determined without `main.py`.

    The supplied runs show 347 impulse events, 10 pullback events, 13 resuming events and zero signals. These are transition counts, not independent trials. Adding admission vetoes cannot improve that observed frequency. State-machine changes can change paths, which requires a separate comparison.

    Fix: Establish a baseline candidate funnel and measure conditional pass rates. As a sensitivity illustration only, ten independent 80%-pass checks retain 10.7% of candidates; at 90%, they retain 34.9%. Independence is implausible here, so an actual frequency estimate requires replay. Zero observed trades is currently the only supported result.

13. P1 — §§2, 5: Existing slope, chase and restart rules may be the main frequency bottleneck.  
    A long must satisfy both:

    \[
    close \ge extremeClose + 0.5a\,ATR
    \]
    \[
    close \le VWAP + 0.5\,std
    \]

    Thus a necessary condition is:

    \[
    extremeClose + 0.5a\,ATR \le VWAP + 0.5\,std
    \]

    Many setups may have no feasible entry price. Separately, a resumption making a fresh 30-bar high triggers the earlier impulse restart and returns before entry evaluation.

    Fix: Measure feasible price intervals and count candidates preempted by restart. Inspect those candidates before adding confirmation requirements. Decide explicitly whether a new high during resumption invalidates the setup or should permit entry evaluation.

14. P1 — §2: Five delta names do not establish five independent measurements.  
    Impulse and resumption aggression use different periods but the same inferred sign mechanism. Absorption reuses resumption delta. Thirty-minute delta overlaps impulse, pullback and resumption. Pullback delta is tested twice on overlapping legs. Tick-rule classification also mechanically links flow signs to price movement already required by slope and velocity.

    Fix: Record pairwise relationships and, more importantly, each gate’s conditional rejection rate and outcome contribution after existing gates. Use ablations to remove redundant conditions. Do not multiply marginal pass rates to forecast frequency.

15. P1 — §2: Signed trade volume is being called order-flow imbalance, intent and absorption without establishing any of those equivalences.  
    Trade delta omits additions and cancellations of displayed liquidity. Net delta also collapses very different situations: little activity and intense two-way trading can have the same ratio. Whole-bar impulse delta cannot prove that aggressive buying caused the bar’s high.

    Fix: Name the feature precisely. Record gross buy/sell volume, signed volume per unit time, turnover and price response. If quote-based OFI is wanted, define it separately from trade delta. Treat absorption as a hypothesis about observed flow and response, not identification of someone’s intent.

16. P1 — §2: “Since the extreme” and rejection transitions are unspecified.  
    Including the entire extreme bar mixes the decline into the low with the subsequent rebound and overlaps the pullback measurement. Starting after its close omits any rebound within that bar. Neither is equivalent to starting at the intrabar extreme.

    The plan also does not say whether a failed new gate waits, kills the setup or preserves an older setup when a replacement impulse fails.

    Fix: Specify exact window endpoints and transition behavior for every rejection. Invalidate superseded setups consistently with the current restart rule. If intrabar extreme timing is required, derive it from eligible prints and retain that timestamp; otherwise label the feature as beginning after the extreme bar.

17. P1 — §§2–3: “Shorts mirror” is too loose for implementation and verification.  
    For shorts, the corresponding raw delta thresholds are impulse ≤ −0.15, pullback ≤ +0.30, resumption ≤ −0.10, and rolling cumulative change < 0. Absorption would use delta ≤ −0.30 and a positive directional decline. Dollar returns must be ≥ −0.25%; IEF and SHY returns must be ≤ +0.25% and ≤ +0.10% respectively.

    Fix: Express all directional rules using one direction multiplier and publish their expanded long/short inequalities. Test exact boundaries and asymmetric examples; symmetric fixtures can conceal sign errors.

18. P1 — §§0, 3, 5: ETF returns do not measure the specified yield changes, and “surge” is uncalibrated.  
    SHY is a short-duration Treasury portfolio, not the cash 2-year yield. IEF is a maturity-range portfolio, not the cash 10-year yield. UUP supplies a traded dollar-futures proxy. Their intraday prices incorporate instrument-specific effects and trading noise.

    Using illustrative durations of two years and seven to eight years, \(r \approx -D\Delta y\) makes the SHY threshold roughly a 5 bp yield move and the IEF threshold roughly 3–3.6 bp. These are different shocks. No supplied evidence establishes whether any proposed threshold is ordinary, rare or dominated by measurement noise over 30 minutes.

    Fix: Label these as ETF price filters. Measure time-of-day distributions, missing-bar rates and price-noise sensitivity. Validate against the target dollar/yield series before claiming fidelity. If that validation is unavailable, actual DXY/yield delivery remains outstanding.

19. P1 — §3: Sector labels are being substituted for measured exposure and correlation.  
    The inlined material does not establish that PLTR→XLK, COIN→XLF or AMZN→XLY are classification errors. The actual defect is treating a categorical assignment as proof of intraday explanatory power: COIN’s crypto exposure and AMZN’s mixed business exposures are obvious reasons to test that assumption.

    Raw stock-minus-ETF return is also sensitive to beta: a high-beta stock can appear strong simply because its sector rises. Overlapping constituents and the existing SPY/QQQ filters create further dependence.

    Fix: Version mappings as of the session, measure exposure stability, and compare raw excess return with a simple beta-adjusted alternative out of sample. Call this a sector-direction/relative-return filter; it contains no actual correlation measurement.

20. P1 — §3: “30 bars,” freshness and startup behavior are not a complete time policy.  
    A 30-bar return normally needs 31 closes. With contiguous opening data, that is available before the source’s earliest signal at 10:09. Therefore 09:45–10:09 is already unavailable under the baseline; it is not evidence of new starvation caused by these filters.

    Missing bars are the real problem. Thirty observations need not span 30 minutes. The current stock code only declares gaps exceeding 300 seconds, so shorter missing intervals silently stretch its bar-count windows. A fresh final ETF bar does not prove a complete return history or correctly anchored session VWAP.

    Fix: Define returns by explicit wall-clock endpoints and validate continuity. Distinguish bar start, bar completion and receipt timestamps. Pair stock and sector returns over identical endpoints. Require verified opening history for anchored measures, and expose startup readiness separately.

21. P1 — §§0, 3: Requiring all seven regime symbols makes unrelated data outages a universal veto.  
    An AAPL candidate can be rejected because XLF has no recent bar, even though its selected sector is XLK. Bars-only delivery also cannot, by itself, distinguish no eligible trades from a broken connection. A previous-minute sector value may satisfy one rule while failing another timestamp interpretation.

    Fix: Define a dependency set per candidate: its sector plus whichever macro proxies are actually required. Separate connection health, eligible-bar availability and stale price observations. Specify previous-minute fallback consistently, including matched return endpoints.

22. P1 — §§0, 5–6: Fail-closed data handling is being conflated with unvalidated trading policy.  
    Missing evidence and an unfavorable experimental feature both reject the trade, but they represent different claims. Making every experimental interpretation mandatory can eliminate trading without demonstrating that any rejected trade was undesirable.

    Fix: Keep required data-integrity failures closed. Introduce unvalidated alpha filters in shadow mode with complete measurements. Set explicit promotion criteria for data availability, conditional selectivity and outcome improvement. Zero trading can be a valid safety state; it is not proof that the requested trading behavior has been delivered.

23. P1 — §6: The proposed tests can pass while the implementation is ineffective or biased.  
    A fake tape can independently satisfy values that cannot coexist in a real price path—especially absorption versus slope. Equality with existing buckets can reproduce classification and ingestion defects. Replaying three sessions that already produced zero signals cannot establish whether new admission filters improve selection. A forced all-pass session verifies wiring only.

    Fix: Add physically consistent end-to-end fixtures, deliberate late/duplicate/corrected events, quote outages, cap eviction, restart and arrival-order permutations. Test each new gate with all preceding gates genuinely satisfied. Verify that future events cannot alter an already recorded decision. Treat unreachable-branch detection as a test outcome, not something to bypass with mocks.

24. P1 — §§5–6: Three days support diagnostics, not threshold validation.  
    They can establish actual retention requirements, classification behavior, latency sensitivity, feasible setup geometry, gate redundancy and whether new checks are ever reached. They cannot establish robust performance across dollar, rates and equity regimes—especially with zero baseline signals.

    Fix: Compute new features in shadow at a broad, predefined candidate set before early returns. Record every predicate, availability reason and setup identifier, then run one-gate-at-a-time and combined ablations. Measure forward outcomes using decision-time information and realistic execution assumptions. Freeze any chosen thresholds and evaluate on additional untouched sessions; do not tune until these three days happen to trade.

25. P1 — §§1, 6: Low additional subscription traffic does not imply low cost or latency.  
    Seven regular-session minute streams are about 2,730 bars/day, but storing and repeatedly querying millions of prints is the material change. Front-deleting arrays, sorting late insertions, copying slices and putting raw prints in research rows can dominate CPU, allocation, storage and serialization. The supplied material does not substantiate the Railway dollar estimate or the three-day implementation estimate.

    Fix: Benchmark ingestion throughput, peak RSS, query latency, backlog and decision delay under burst load. Use bounded storage and incremental exact aggregates where appropriate. Store research references to archived print ranges instead of copying raw windows into each row. Calculate deployment cost from measured resources and the applicable billing terms.

26. P1 — §§3–4, 6: “Market at the signal close” becomes less credible as synchronization grows.  
    The evaluator calculates entry and stops from `bar.close`. Waiting for multiple feeds, processing delayed prints and applying admission checks moves the executable order later. A historical close is not an available market fill. The supplied source does not show whether execution reconciles the resulting price and risk differences.

    Fix: Measure bar-close-to-decision, decision-to-submission and submission-to-fill delay. Verify execution-time price/risk checks and stop sizing against actual fills. Include those delays in replay. An after-hours health response cannot validate market-session timing.

27. P2 — §§1, 3, 6: Health and research outputs are insufficient to distinguish readiness from activity.  
    “Trades seen,” “quotes seen,” oldest-print age and a recent ETF bar can all look healthy while required windows are incomplete. Signal-attached features are absent when no signals occur. A rolling hour also cannot support later inspection of discarded prints unless the existing archive retains them.

    Fix: Expose per-symbol usable intervals, quality breakdowns, unresolved gaps, eviction boundaries, decision lag and individual gate readiness. Persist candidate-level measurements and immutable archive references. Assign a new policy identifier so the old and expanded rule sets are distinguishable.

(a) The three findings that matter most

- The absorption veto cannot fire after the existing slope requirement.
- The aggression gates can consume temporally contaminated, weakly classified or incomplete flow while treating it as valid evidence.
- The baseline already produces zero signals; additional mandatory filters have no demonstrated selection benefit.

(b) What I would cut

Cut the absorption veto, universal seven-symbol freshness dependency, immediate activation of new alpha gates, microsecond causality claims, and unsupported cost and proxy-fidelity claims.

(c) What I would add

Add a point-in-time data contract, completeness-aware queries, durable replay and correction handling, shadow measurements with gate ablations, an explicit feasibility/frequency analysis, and measured execution latency before deployment.
tokens used
