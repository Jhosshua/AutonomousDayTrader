# Codex code review round 2 of data layers part 2 (2026-09-27)

Model gpt-6-astra, reasoning xhigh, read-only, no tools; full core modules + main diff + round-1 findings inlined.

I’ll review only the inlined code, without tools or file access. I’ll separate confirmed defects from risks that depend on runtime behavior the excerpts don’t show.
The packed-store redesign has a new accounting defect: an old print can be appended to a bucket that `_advance()` already evicted, leaving `raw_count` counting data that no query can retrieve. The book calculation also applies each second’s final quote to the whole second, which can reverse the imbalance seen by the required gate.
The raw `_iter()` boundaries look correct, including fractional edge seconds. Synchronous queries also cannot be interrupted by ordinary asyncio tasks on the same event-loop thread; generator `yield` does not yield to the event loop. The excerpts don’t establish that all access stays on that thread, and the replay’s exchange-time sorting hides the late-arrival failures.
No-go for this revision. Disabling the new gates does not isolate the rewritten tape, the existing required book gate, or shared ingestion.

This is a static review of the inlined code only. No tools or files were used; the replay results are treated as reported evidence.

Round-1 dispositions:

1. FIXED — Velocity now passes through `_ok()`, requiring completeness and a finite `per_second`.
2. PARTLY — A right-boundary check exists, but a quote watermark and five-second allowance do not establish trade-stream completeness or finality.
3. FIXED — Whole-second eviction and the exclusive end boundary prevent the original equal-timestamp cap split; a separate eviction corruption appears below.
4. PARTLY — Coverage, carry-forward, and outage checks exist, but duration weighting is inaccurate and carried state can cross an outage outside the query window.
5. PARTLY — Late prints use an earlier chronological price without overwriting live state, but insertion never repairs affected successors or the resulting live zero-tick direction.
6. PARTLY — Size/side storage ranges are protected; timestamp range validation and transactional mutation are still missing, including in the new re-sort.
7. PARTLY — Tape queries are individually caught and reused, and admission catches regime evaluation; `_ok()` and subsequent result-field extraction remain outside that exception boundary.
8. FIXED — Regime timestamps are stored as UTC-aware values, with query times normalized.
9. PARTLY — Ordinary nonfinite/OHLC errors are rejected, but `float(bar.volume)` can raise before validation returns, and boolean volume escapes rejection through that conversion.
10. FIXED — Historical direction selects the VWAP prefix belonging to the selected bar.
11. FIXED — The shown live startup and constructor reject enforced gates with tick layers disabled; restore discards returned signals.
12. PARTLY — Normal eviction no longer copies the retained tape, but repeated late-print predecessor lookups can repeatedly sort/copy a hot second, and bucket destruction is not constant-cost.
13. PARTLY — IDs expire with buckets on advancing traffic, but the raw cap does not bound ineligible IDs, quote-history count, or total process memory.
14. PARTLY — Eviction has a conservative irreversible boundary; first observed event still substitutes for established feed coverage and is not revised for earlier arrivals.
15. PARTLY — Stock transitions now create intervals, but the latest-200 limit can erase relevant outages, and point marks can interfere with closing an open interval.
16. PARTLY — Ten-second history removes the 500-quote limitation, but supported lateness is undefined, truncation is unreported, and late quotes remain rejected.
17. PARTLY — `quote_ts_max` preserves ordering, but crossed quotes still leave classification history and book state usable.
18. PARTLY — Tick direction is correctly separated from aggressor side, but continuity survives gaps and can become chronologically wrong after a late insertion.
19. PARTLY — Oversized IDs are rejected instead of truncated, but exchange truncation, permissive ID coercion, and absent/zero conflation remain.
20. MISSED — Final-quote-only buckets still cannot answer arbitrary historical nanosecond book boundaries, and partial seconds are not duration-clipped.
21. PARTLY — Tape measures share a validated snapshot, but unavailable full-leg delta falls back to touch evidence in the record, validity metadata is discarded, and age rejection precedes recording.
22. MISSED — “Since extreme” still means post-extreme-bar flow and advance from its close, without consistently naming those boundaries.
23. FIXED — Regime ingestion uses ET regular-session filtering and forward-only session replacement.
24. PARTLY — Both ETF endpoints have freshness checks; stock baselines remain unbounded, and admission still reads the latest mutable stock state.
25. FIXED — Short sector direction requires strict `close < vwap`.
26. MISSED — Replay forces ETF-before-stock ordering without establishing an equivalent live availability/synchronization policy.
27. FIXED — Each emitted `RESUMPTION_MEASURED` now includes contemporaneously queried regime evidence; age-filtered candidates remain the separate finding above.
28. PARTLY — Unknown names and whitespace are handled at startup, but the module set and strategy list remain separate mutable policies.
29. MISSED — Admission notification still lacks signal/session identity, idempotency, and protection from a later emission changing its anchor.
30. PARTLY — The audit horizon and threshold arguments improve matters, but the audit tape retains the default per-symbol million-print cap and all-unknown windows still return `None`.
31. PARTLY — Completeness and historical VWAP improve the observations, but repeated evaluations and hardcoded thresholds still do not constitute an enforcement replay.
32. MISSED — Zero signals leave successful admission/submission unexercised; aggregate bar counts and a lifetime regime counter still cannot prove per-symbol coverage.
33. MISSED — Regime/watchlist overlap still permits quote/trade subscriptions, with no explicit overlap policy in the supplied changes.
34. PARTLY — Quote/ineligible traffic now triggers retention and idle clears impulse evidence; lossy export, lifetime “live” indicators, preemption undercounting, linear lookup, and unused configuration remain.

The new failure mechanisms are below. Where they overlap a Round-1 category, this identifies the redesign’s specific failure rather than counting the old finding again.

1. P0 — `_bucket()` can return an already-evicted bucket, permanently corrupting accounting.

   `_bucket()` inserts the bucket, calls `_advance()`, and returns its local `b` without checking whether `_advance()` removed it.

   Concrete sequence:

   - With `keep_seconds=10`, the watermark reaches second `100`.
   - An eligible late print arrives for second `89`.
   - `_bucket()` creates bucket `89`; `_advance()` immediately age-evicts that empty bucket.
   - `on_trade()` appends to the detached bucket and increments `t.raw_count`.

   The print is unreachable by queries, but counted as retained. Thus:

   ```text
   raw_count != sum(bucket.n_trades for bucket in buckets.values())
   ```

   Repeated arrivals eventually evict valid buckets to compensate for nonexistent retained prints. Once phantom count exceeds the cap with no buckets left, `_advance()` can evict every newly created empty bucket before its append. The tape can become persistently unusable until reset.

   Fix: determine retention admissibility before creating/mutating a bucket. An append must either become part of retained storage or leave retained accounting unchanged.

2. P0 — New book carry-forward can manufacture coverage across a known outage.

   `_outage_in()` checks the query window, but does not check whether the quote used to seed that window crossed an outage.

   With default book settings:

   ```text
   Valid bid-heavy quote: 97 s
   Disconnect/reconnect: [98 s, 99 s)
   Query window:         [100 s, 130 s)
   Fresh ask-heavy quote:129 s
   ```

   The old quote supplies seconds `100` through `126`: 27 seconds. The fresh quote supplies another second. The function reports `28/30` coverage, sees a fresh final quote, finds no outage overlapping the query, and returns `complete=True`.

   Only the final quote establishes post-outage book state. Nevertheless, the old bid-heavy sizes can dominate and pass the required long gate.

   There is also backward assignment within each second: the final quote is applied to the entire second. If every second is ask-heavy from `.000` until `.999`, then bid-heavy for its final millisecond, this implementation reports a strongly bid-heavy window. True duration weighting gives the opposite result.

   Fix: accumulate actual valid quote-duration contributions. An outage or invalid quote must terminate the carried state, including when that termination precedes `t0`.

3. P0 — `ensure_sorted()` can irreversibly misalign columns after an allocation failure.

   It replaces each array separately:

   ```text
   replace timestamps
   replace prices
   replace sizes
   ...
   ```

   For example, start with:

   ```text
   timestamps: [102, 101]
   prices:     [10,  20]
   ```

   If timestamp replacement succeeds and price allocation raises, the bucket becomes:

   ```text
   timestamps: [101, 102]
   prices:     [10,  20]   # wrong associations
   sorted:     False
   ```

   A subsequent retry sorts the already-sorted timestamps using the identity permutation. It cannot recover the original associations.

   This is conditional on allocation failure, but the surrounding handlers expressly catch exceptions and continue, making silent continued use possible. The growing ID sets and temporary sorting allocations make that failure relevant.

   Fix: construct all replacement arrays in locals before committing any of them. Failed appends also need rollback or symbol quarantine; validating values does not prevent allocation failures.

4. P0 — An out-of-range timestamp can destroy valid history before its append fails.

   `on_trade()` accepts any positive Python integer timestamp. It does not enforce the signed 64-bit range required by `array("q")`.

   With `ts_ns = 263`, `_bucket()` advances the watermark and age-evicts normal history before `b.ts.append()` raises `OverflowError`. The handler logs and continues with the poisoned watermark. Subsequent normal timestamps then encounter the detached-bucket failure above.

   This overflow occurs on the first column, so it does not itself misalign columns; it corrupts retention and coverage state.

   Positive but absurd future timestamps within the storage range can cause similar damage without an exception. Quotes have the additional consequence of poisoning `quote_ts_max` and rejecting subsequent normal quotes.

   Fix: validate storage range and an explicit feed-time plausibility policy before changing watermark, quote ordering, deduplication, or retention state.

5. P1 — “Lazy sorting” is performed repeatedly inside late-print ingestion.

   For a late print that needs tick-rule classification, `_predecessor_price()` calls `ensure_sorted()`. The subsequent append can immediately make that same bucket unsorted again.

   A burst of late prints therefore does:

   ```text
   sort/copy bucket → append → sort/copy bucket → append → ...
   ```

   Even when sorting an almost-sorted list is linear, copying all five columns on each insertion gives quadratic work over a growing burst. Each predecessor lookup also allocates a list of retained bucket keys.

   The reported replay orders events by exchange timestamp, so it does not exercise this delivery pattern. Its average throughput does not establish worst-case ingestion latency.

   Fix: use a bounded lateness/finalization policy or batch late work, and measure event-loop delay under late bursts after retention fills.

6. P1 — A point gap can prevent an open outage from ever closing.

   This public API sequence is broken:

   ```text
   note_disconnect(100) → [[100, None]]
   note_gap(110)        → [[100, None], [110, 110]]
   note_reconnect(120)  → unchanged
   ```

   Reconnect examines only the final entry. The original outage remains open, while `health()["open_outage"]` reports false because the final entry is closed.

   Future queries fail coverage indefinitely until that interval is discarded or the tape resets. The supplied main changes do not establish whether a `note_gap()` call can currently occur during disconnection; the API failure itself is definite.

   Fix: keep active-disconnection state separately from completed intervals and point marks.

7. P1 — The new recorded regime snapshot is not the snapshot used for regime admission.

   The evaluator records `REGIME.evaluate(...)`, but does not attach that full result to the signal. `_ride_the_trend_regime()` evaluates again and retrieves stock return from mutable strategy state.

   If an awaited admission step permits another bar to arrive, the recorded evidence and enforced decision can differ. The excerpts do not establish whether that interleaving occurs in the current caller, so this is a structural risk rather than a demonstrated live race.

   Fix: bind complete regime evidence and stock endpoint identities to the candidate, or explicitly record admission re-evaluation as a separate decision snapshot.

For the requested per-second-store attack:

- Successful re-sort preserves column alignment, but does not repair classification. Stable sorting is appropriate for rows already carrying their own fields. However, consider arrival order `(t=100, price=100)`, `(102, 101)`, then late `(101, 102)`, with no usable quotes. The stored print at `102` remains tick-buy, although its newly established predecessor makes it a downtick. An unchanged print at `103` also inherits the wrong live upward direction. Chronological reconstruction requires repairing affected successors or delaying classification until a defined lateness boundary.

- Raw `_iter()` edge handling is correct in the ordinary, uncorrupted case. `s0=floor(t0)` and `s1=ceil(t1)` select the right seconds. Both bisects apply when the window fits within one bucket. Exact `t1` timestamps are excluded. This finding does not extend to book queries, whose final-quote-only representation cannot reproduce fractional historical boundaries.

- Ordinary asyncio tasks cannot evict halfway through these synchronous queries. There is no `await` in ingestion, query execution, or the evaluator’s measurement sequence. Generator `yield` returns control to the synchronous caller; it is not an asyncio scheduling point. The supplied handler declarations do not prove that every call originates on the same thread, however. Threaded ingestion or executor access would require synchronization; whole-second eviction alone provides none.

- Eviction is not literally O(1) end to end. Removing an `OrderedDict` entry is constant-time, but destroying its ID set visits its entries. One cap enforcement can also traverse multiple leading quote-only buckets. This is substantially better than shifting the entire retained tape, but needs bounded latency measurements.

- The ID sets remain a major NVDA-rate memory concern. Packed columns use 26 bytes per eligible print, excluding allocation overhead. On common 64-bit CPython builds, a packed ID integer alone is roughly 36 bytes, before its set table. Ineligible IDs occupy those sets without consuming `raw_count`; for scale, 1,000 unique ineligible IDs/second retained for an hour means 3.6 million IDs despite zero eligible raw prints. This is a stress example, not a claim about measured NVDA traffic. A time horizon is not a hard memory budget.

- The million-print cap is per symbol. Approximately 1.1 million retained prints across symbols is not a cap violation. The separate audit tape also inherits that per-symbol cap: `keep_seconds=8*3600` does not guarantee a full morning. Reporting `complete=False` is useful, but the resulting suffix share must not be presented as the full 09:45–11:30 share.

- Quote-only watermark advancement cannot certify trade completeness. Quotes can continue while trades are delayed, dropped, or unsubscribed. Existing earlier prints can still satisfy delta’s classification thresholds and velocity’s span/last-age requirements. Neither connection status nor quote progress proves that the missing trade tail was processed. Crossed quotes and ineligible trades also advance this watermark.

- `right_slack_s=5` is an allowed unconfirmed tail, not a five-second wait. At a bar’s completion, a watermark at `end−4 s` permits immediate `complete=True`. A genuinely quiet symbol whose last event was `end−6 s` instead fails coverage, even if transport is healthy. The code cannot distinguish quietness from missing delivery. A lateness/finalization policy must make that distinction explicit.

- The impulse window itself is correct. Querying `[bar_start, bar_start+60s)` at completion introduces no inherent look-ahead. The defect is certifying it before its trade tail is established. Impulse evidence is then frozen; later prints do not revisit that original gate or measurement.

The unchanged crossed-quote issue also remains material: setting `t.quote=None` does not affect `_quote_for()` or book reconstruction, because neither uses that field to invalidate historical state.

Go-live verdict: No. Single condition: deploy only a corrected revision that passes a real-`TickTape` release acceptance suite covering these P0 counterexamples and a successful controlled admission/submission path. Three zero-signal replays and ORB coexistence do not satisfy that condition.

---

## Triage (Claude, applied before commit)

Fixed: new P0-1 (a print older than retention is refused before any mutation; `raw_count` always equals the retained buckets' counts), P0-2 (book carry-forward never seeds from a quote that precedes an outage; each second contributes the quote state in force at its START, never a quote that arrived inside it), P0-3 (`ensure_sorted` builds every column first and commits atomically), P0-4 (timestamps validated against the signed 64-bit range, one hour past the watermark and the retention window before the watermark, quote ordering or retention state change), P1-5 (prints more than 30 s behind the watermark are stored but never tick-classified, so late bursts do not re-sort hot seconds), P1-6 (an open outage is tracked separately from closed intervals and point marks; a point mark can no longer strand it).

Accepted as documented limits (this build is recorded-only for every part-2 measure): the quote watermark cannot prove the trade tail is complete (5 s slack is an allowance, not a wait); late prints do not repair the classification of already-stored successors; crossed quotes do not invalidate quote history; per-second book state cannot answer sub-second boundaries; the recorded regime snapshot and the admission-time regime evaluation are two evaluations (documented); admission notification carries no signal identity; three zero-signal replays do not exercise submission on real ticks (the fake full session does). Stopping rule: two review rounds; the remaining items are listed in the plan as acceptance criteria for promoting any gate.
