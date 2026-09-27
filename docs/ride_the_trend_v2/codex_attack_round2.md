# Codex attack round 2 on PLAN_2026_09_27_ride_the_trend_v2.md (draft v2)

Model gpt-6-astra, reasoning xhigh, read-only, no tools, revised plan + round 1 findings + v1 source + restore/adaptation excerpts inlined. 2026-09-27.

I’ll review only the inlined material, with no tools or file access. I’ll separate Monday’s shadow blockers from defects in the later live milestone.
The revision still leaves the experiment nondeterministic and the promotion statistic undefined. The live milestone also introduces new ways to lose track of pending exposure. The P0 findings below concern that later milestone; they are not claims that shadow mode has submitted orders.

Round-1 dispositions follow. “FIXES” means the written specification addresses the finding, not that implementation has been verified.

| # | Verdict | Remaining issue |
|---|---|---|
| 1 | PARTLY | `CANCEL_FAILED` has no recovery path; terminal-state reservation handling and the “AND” expiry rule remain unsafe. |
| 2 | PARTLY | Recomputing protection on partial fills changes the supposedly final risk basis; exits can occur before that basis freezes. |
| 3 | PARTLY | Exit serialization omits cancellation of the still-working entry, and “confirmed T1 fill” does not define partial-fill completion. |
| 4 | FIXES | The signal-time formula preserves structure, rejects excessive width, and explicitly prevents a second VIX multiplication. |
| 5 | PARTLY | Reservations are specified, but their transfer to filled-position ownership is missing; releasing them at entry termination can undercount exposure. |
| 6 | PARTLY | Session bars cannot reconstruct admission decisions, availability timing, admitted counters, or durable event watermarks. |
| 7 | PARTLY | The checkpoint equality requirement conflicts with persistent shadow state, and no independent shadow admission ledger is specified. |
| 8 | PARTLY | Cancel-on-redeploy cannot cover abrupt termination; outage recovery and protection-before-new-entry ordering remain unspecified. |
| 9 | FIXES | The final submission boundary explicitly rejects non-LIMIT v2 entries and invalid limit prices. |
| 10 | FIXES | An explicit runner policy covers construction, persistence, restoration, simulation, and initial protection. |
| 11 | PARTLY | TRAP escape, timeout behavior, low updates, terminal-state handling, and simultaneous transition priority remain ambiguous. |
| 12 | FIXES | Reference, first-touch volume, and resumption-volume intervals are explicitly defined; post-touch volume is excluded from PVR by construction. |
| 13 | PARTLY | RS alignment is partly specified; index-filter alignment, VIX availability, delayed admission, and earliest permissible fill remain unresolved. |
| 14 | PARTLY | The earliest-bar arithmetic is wrong, retained EMA50 readiness is omitted, and nonpositive denominators lack rules. |
| 15 | PARTLY | Sample floors do not establish power; the requested geometry breakdown and detectable effect are absent. |
| 16 | FIXES | The numerator is close-to-close, age excludes zero at evaluation, and the current bar must close in the resumption direction. |
| 17 | PARTLY | Opening exclusions do not remove intraday volume decay; time-of-day, duration, and volatility controls are still absent. |
| 18 | PARTLY | A4 supplies an on/off comparison, but conditional analysis against absolute move and entry distance is missing. |
| 19 | PARTLY | The new impulse/setup restrictions confound A1/A2, and the full stop-plus-runner policy is absent from the manifest. |
| 20 | PARTLY | Eight names are enumerated, but their executable definitions and the identity of the eventual promoted configuration conflict. |
| 21 | FIXES | Historical periods are explicitly development evidence and cannot independently authorize broker orders. |
| 22 | PARTLY | The selection statistic uses per-trade R while the bootstrap resamples centered daily dollar P&L. |
| 23 | PARTLY | Confidence-bound construction, selected-configuration inference, degenerate samples, and prospective repeated testing remain undefined. |
| 24 | FIXES | The drift veto is removed and a fixed 30-minute diagnostic is separated by signal and modeled fill. |
| 25 | PARTLY | Two sensitivities do not specify latency, quantity, gap execution, intrabar ordering, or the interaction of adverse assumptions. |
| 26 | PARTLY | Parity still stops before sizing, modeled fills, exits, and portfolio admission; populated cases may not exist. |
| 27 | PARTLY | A positive event/bar ratio cannot establish completeness, and durable deduplication across checkpoint/queue boundaries is missing. |
| 28 | PARTLY | Admission is named, but shadow cannot perform it as specified; duplicates, missing bars, resets, and cooldown boundaries remain undefined. |
| 29 | PARTLY | Dollars are reported, but sizing reconciliation is not demonstrated and positive dollars are not required for qualification. |
| 30 | PARTLY | Combined replay is required, but arbitration order, loss accounting, and an acceptable marginal effect are not defined. |
| 31 | PARTLY | Modeled outcomes are distinguished, but the configuration mismatch and incomplete execution model prevent the proposed confirmation. |
| 32 | FIXES | VPR is removed from the qualifying family and described only as exploratory logging. |

The new failures and remaining contradictions are ranked below.

1. P0 — §6.2 allows a completed exit to be followed by a new entry fill.

   Protection starts on the first partial fill, so a stop, T1, or flatten can execute while the entry order still has unfilled quantity. Section 6.3 serializes competing exits, but never requires cancellation and reconciliation of the remaining entry.

   A valid sequence is:

   - Entry for 100 shares fills 20.
   - The controller stops out those 20 and closes the bracket.
   - The remaining entry fills another 30.
   - The supposedly closed position now has new exposure.

   Every reduction or flattening transition needs an explicit policy for the working entry. Reconciliation must continue after the position temporarily reaches zero, until that entry is terminal. “One controller” alone does not establish this invariant.

2. P0 — §§3.1/6.2 make the invalid-fill check potentially self-defeating and the risk denominator mutable.

   For the first long fill at price \(F\), recomputing the stop as

   \[
   S=F-\max(\text{positive candidates})
   \]

   guarantees \(S<F\). Checking whether that same fill landed at or below the recomputed stop cannot detect a first fill that gapped through the signal’s original invalidation level. The short side has the corresponding problem.

   The plan must distinguish:

   - The frozen setup invalidation level checked before accepting the fill.
   - The protective order price calculated for actual exposure.
   - The risk basis used for sizing and reporting.

   It currently conflates them.

   “R freezes at the last entry fill” also fails when T1 or a stop executes before the last entry fill. Subsequent fills can change average entry, stop, T1, runner allocation, and the denominator of already-realized returns. The specification needs either a frozen accounting convention for incremental exposure or a lifecycle that finalizes entry before enabling scale-out. Repeatedly calling the recalculated stop “final” does not solve this.

3. P0 — §6.1’s “3 completed bars AND 4 minutes” defeats its outage guarantee.

   Read literally, cancellation requires both conditions. During a feed outage, the bar condition never becomes true, so four elapsed minutes do nothing.

   The deadline must fire at the earlier of the completed-bar expiry and the absolute wall-clock expiry. Define when each clock starts, whether delayed or backfilled bars count, and whether the expiration-boundary bar can fill before cancellation.

   A local timer also cannot cancel while the process is down. Restart must reconcile and cancel overdue entries before new admission. Graceful shutdown cancellation cannot be presented as a guarantee for every redeploy or crash.

4. P0 — §§6.1/6.4 confuse termination of an entry order with termination of its exposure.

   `CANCEL_FAILED` is listed without an outgoing transition. A failed cancellation can leave a fully working order. Treating it as terminal and releasing reservations permits overbooking; retaining it indefinitely without reconciliation creates a stuck reservation and potentially an indefinitely working entry.

   `FILLED` and `CANCELLED` need different handling too:

   - `FILLED`: transfer pending reservations to position exposure atomically.
   - `CANCELLED` after partial fills: release only unfilled exposure; preserve ownership and protection of filled shares.
   - Unknown cancel result: retain potential exposure and reconcile until authoritative resolution.

   The state graph also needs explicit immediate-fill, rejection, broker-expiration, and uncertain-submission paths. A stable client ID does not itself specify recovery from a lost submission acknowledgement.

5. P1 — §4 bootstraps a different statistic from the one it selects.

   The observed statistic is

   \[
   T=\max_{c=1,\ldots,7}\left(\bar R_c-\bar R_0\right).
   \]

   Its null samples are described as centered daily dollar P&L vectors. Those vectors cannot reconstruct per-trade R, particularly with variable stops, VIX sizing, and the notional cap.

   There are two coherent choices:

   - Select using daily dollar P&L differences and bootstrap those differences.
   - Keep the mean-R selection statistic and retain daily sums of trade R and trade counts for every configuration.

   For the second choice, define \(S_{cd}\) as the sum of trade R and \(N_{cd}\) as trade count on day \(d\). Then

   \[
   \hat\mu_c=\frac{\sum_d S_{cd}}{\sum_d N_{cd}}.
   \]

   Jointly resample calendar blocks, and construct centered bootstrap differences such as

   \[
   \Delta_c^{*,0}
   =
   \left(\frac{\sum S_{cd}^*}{\sum N_{cd}^*}-\hat\mu_c\right)
   -
   \left(\frac{\sum S_{0d}^*}{\sum N_{0d}^*}-\hat\mu_0\right).
   \]

   The null maximum is then \(\max_c\Delta_c^{*,0}\), using the same resampled days for every configuration.

   Recentring daily dollar series separately is not inherently wrong for a daily-dollar difference test: subtracting each mean centers their differences. It is wrong here because the statistic being tested is a different, trade-count-weighted ratio.

6. P1 — A0 with 2,366 trades and A3 with 40 exposes both the inference problem and the wrong economic objective.

   Forty A3 trades fail the declared minimum evidence rule. A3 is inconclusive regardless of its mean, confidence interval, or the family’s p-value.

   Under equal independent trade variance, its mean would have approximately

   \[
   \sqrt{2366/40}\approx7.7
   \]

   times A0’s standard error. Actual dependence can make the comparison worse. The large control sample does not supply missing information about the sparse treatment.

   An unstudentized maximum of mean differences is not automatically invalid if that is genuinely the selected objective and the bootstrap correctly reproduces its distribution. But a noisy, sparse configuration can dominate both the observed maximum and its null tail. The plan needs a fixed eligibility rule and explicit treatment of bootstrap draws with zero trades; silently dropping those draws or assigning mean R zero is invalid.

   More fundamentally, per-trade R can reward a policy that contributes fewer dollars or loses money at deployed sizing. For example, twenty \(+1R\) trades risking $100 and twenty \(-0.5R\) trades risking $500 produce mean \(+0.25R\) and net −$3,000, before any additional costs.

   Reporting dollars does not fix a qualification gate that ignores them.

7. P1 — The maximum-statistic p-value cannot be assigned to an arbitrary qualifying configuration.

   Suppose A1 produces the family’s strongest improvement, while A3 produces a small improvement. A significant p-value for the maximum establishes evidence somewhere in the family; it does not establish that A3’s improvement is significant.

   Yet A1/A2 participate in the maximum while historical PASS is reserved for A3–A7. This allows a nonqualifying policy to provide the significant family result unless candidate-specific inference is defined.

   The plan needs either:

   - A fixed primary tested directly; or
   - Multiplicity-adjusted inference for each configuration that may qualify.

   Separately, ordinary 95% bounds for whichever configuration is selected are not simultaneous bounds. The interaction between selection, IS/VAL positivity, minimum counts, and the sensitivity runs must be specified as one qualification procedure.

8. P1 — §§3–5 do not identify one policy that is built, measured, and promoted.

   The contradictions are operational:

   | Specification | Conflict |
   |---|---|
   | A3 is the primary | Section 3 presents the new final stop and runner as v2 rules, but A3 retains v1 stops and targets. |
   | A6 changes the stop | No configuration combines A6’s stop with A7’s runner. |
   | A4–A7 may become primary | Section 5 still targets 150 A3 trades and outcomes under A3’s execution. |
   | A4 adds RS | Section 3.3 first says unavailable RS rejects “the signal,” then says RS is only a gate in A4. |
   | A1 adds PVR only | Reusing the whole section-3 machine would also impose resumption and slope behavior. |
   | A2 adds slope only | Reusing that machine would still enter TRAP or reject non-thin PVR unless those branches are explicitly disabled. |
   | A0 retains v1 rules | Section 3 omits the supplied v1 EMA trend qualification and 50-bar warm-up. |
   | Trend-by-structure is a separate ablation | No such ablation exists in A0–A7. |

   A1 and A2 also introduce the new 30-bar-high discovery rule and setup lifecycle unless those are already present in their control. Their comparisons therefore measure a bundle of candidate-selection changes, not isolated volume or slope effects.

   Each configuration needs an executable definition covering discovery, trend qualification, transitions, gates, admission, sizing, fills, and exits. The selected policy’s exact identity must remain fixed through prospective evaluation and any eventual activation.

9. P1 — Section 3 is not yet a deterministic state machine.

   A line-by-line audit follows.

   | Rule | Failure or missing decision |
   |---|---|
   | `i` counts regular-session bars | It is unclear whether `i` is the minute’s position in the session or the ordinal of observed bars. Missing bars make those different. |
   | Completed bars only | Timestamp convention and finality are undefined. Revised or late bars require an explicit acceptance policy. |
   | ATR14 seed | Fourteen bars occupy indices 0–13. If ATR is deliberately unavailable until 14, specify whether `ATR[14]` is the original seed or the Wilder update using the fifteenth range. |
   | `ref_mean` window | Readiness requires a complete, valid window and a strictly positive denominator, not merely indices in range. |
   | IDLE → IMPULSE | No arbitration is specified against an active opposite-direction setup. “Per symbol” does not define whether there is one directional state or two. |
   | New high while IMPULSE | A bar can make a new high and touch the zone. Restart-first and touch-first produce different legs, PVR, and rejection events. |
   | First touch | If the first touch is at `impulse_i + 1`, it violates the two-bar minimum, but there is no prescribed transition. Ignoring it would violate “first touch.” |
   | Twenty-bar limit | Expiry is described when a late touch arrives. A setup with no touch has no explicit timeout. |
   | Zone test | `L <= upper_band` accepts a candle entirely below the zone. It is not an intersection test and does not retain v1’s bounded-zone condition. |
   | PVR classification | Zero reference volume, NaN, and other nonfinite values have no branch. |
   | TRAP | “Stay until the next IDLE→IMPULSE” is circular because TRAP is not IDLE. The later “new high at any point” rule supplies a different possible escape. |
   | PULLBACK low tracking | There is no unconditional pullback timeout. Continuing lower lows can keep moving the anchor indefinitely. |
   | PULLBACK → RESUMING | The plan must say whether the resumption-start bar is evaluated immediately. Delaying evaluation loses age-one opportunities and changes earliest readiness. |
   | New low during RESUMING | If the low updates, age becomes zero and the stated rule expires the setup. If it does not update, structure and slope use an obsolete low. No return-to-PULLBACK rule is specified. |
   | Age outside 1–3 | An age-four first uptick should apparently expire immediately, but that depends on same-bar transition processing. |
   | Slope, chase, and age-three rejection | Several failures can occur on one bar. The chosen reason affects the funnel unless precedence or multi-reason logging is fixed. |
   | New high “at any point” | Does restart require the full original predicate, including close above VWAP and feature availability, or only a new high? Can it override SIGNAL or TRAP on that bar? |
   | SIGNAL → IDLE | Is this immediate, next-bar, or after admission feedback? What happens after an admission rejection? |
   | Daily budget | “Done for the day” conflicts with continuing state maintenance unless it means emission-disabled only. |
   | Cooldown | “15 completed bars after” needs an exact inclusive/exclusive boundary and a definition of which bars count. |
   | Session/window boundary | The handling of active setups, delayed admissions, and pending modeled entries at 11:30 and at the next session is unspecified. |

   Two transitions can legitimately occur on one bar—for example, entering RESUMING and immediately emitting SIGNAL. The requirement should therefore be deterministic transition priority and a bounded sequence of transitions per bar, not an indiscriminate “one transition per bar” rule.

   Conversely, priority must not imply knowledge of intrabar order. An outside candle does not tell you whether the new high preceded the pullback low.

10. P1 — The revised volume gate can certify a thin pullback before the actual pullback finishes.

    PVR freezes at first touch, but the low keeps updating afterward. A thin first-touch leg can be followed by several high-volume selloff bars, a much deeper low, and a qualifying bounce.

    That setup passes A3’s PVR gate because the heavy-volume portion is outside `leg`. The resumption-volume diagnostic also excludes bars before the final low.

    This is causal, but it does not test the stated hypothesis about the volume of the completed pullback. It tests volume before first touch. Either name that hypothesis accurately or define a different, separately registered accumulation rule. Quietly extending the leg during implementation would change the experiment again.

11. P1 — “Shorts mirror” is insufficient for the directional and nondirectional rules.

    A coherent short specification requires at least:

    \[
    \begin{aligned}
    &L[i] < \min L[i-30..i-1],\quad C[i]<VWAP[i],\\
    &H[t]\ge VWAP[t]-0.3\,std[t],\\
    &high_i=\arg\max H[touch_i..k],\\
    &C[k]<C[k-1],\\
    &\frac{C[high_i]-C[k]}{(k-high_i)ATR[k]}\ge0.50,\\
    &C[k]\ge VWAP[k]-0.5\,std[k],\\
    &dist=\max(1.5ATR\,m,\ pullback\_high-entry+0.5ATR,\ 0.004entry),\\
    &stop=entry+dist,\qquad T1=entry-dist.
    \end{aligned}
    \]

    A4’s short RS inequalities must be nonpositive if the intended hypothesis is relative weakness. Its index qualification must also be directionally defined.

    PVR thresholds, resumption-volume inequalities, distances, ages, volume validity, and daily budgets do not reverse. The direction check must validate short stops above entry, not apply the written long-only validation.

    A short trail must ratchet in the correct direction, and the two directions must share the symbol’s two-admission budget and ownership. None of this should depend on an implementer deciding which occurrences of “sign” to swap.

12. P1 — Exceptional bars can change the policy or halt it.

    | Condition | Consequence under the current specification |
    |---|---|
    | First bar after restart | A previously processed bar can be appended twice or a restored SIGNAL re-emitted without a persisted processing watermark. |
    | Missing minute | Compressing the index changes lookbacks, age, cooldown, and TTL; preserving the minute index leaves undefined observations in those calculations. |
    | Duplicate bar | Volume is counted twice and observed-bar timers advance unless deduplication occurs before all state updates. |
    | Late/out-of-order bar | Rebuilding historical indicators may alter past decisions; appending it as current corrupts chronology. |
    | Corrected bar | Prefix invariance and retrospective correction conflict unless a versioned correction policy is explicit. |
    | Zero-volume signal bar | The supplied v1 explicitly rejects it; v2 has no equivalent rule. A zero-volume pullback also looks maximally thin. |
    | Zero reference volume | Positive/zero can become infinity; zero/zero becomes undefined. Neither has a specified unavailable-feature outcome. |
    | ATR = 0 | Slope can become infinity or NaN. A stop floor does not repair a signal that passed an invalid slope calculation. |
    | std = 0 | The no-chase cap collapses to VWAP, unless the v1 fallback is retained. “As today” does not resolve that choice. |
    | Very small std | The supplied v1 substitutes ATR when `std <= 0.001`; using raw deviation in v2 changes both setup geometry and exits. |

    Required input policy includes a unique bar key, duplicate handling, missing-data handling, finite-value checks, and session identity. Missing data must not silently become zero-volume evidence.

13. P1 — The bar-39 test encodes the wrong arithmetic and may contradict retained v1 readiness.

    With the definitions written:

    - First eligible reference/impulse index: \(35\), using volumes 5–34.
    - Earliest valid touch: \(37\).
    - Earliest subsequent resumption: \(38\).

    The extra `+1 (impulse)` double-counts an index step. If bars are labeled by their start minute, index 38 is the 10:08 bar, available upon completion at approximately 10:09. That does not make it index 39.

    Separately, the supplied v1 refuses qualification until 50 session bars exist, at index 49. If A3 retains v1’s EMA rules, index 38 cannot be its first signal. If the new class removes that requirement, the ablation changed another component.

    A test demanding “first signal at bar 39” can therefore force an artificial implementation delay while missing the actual warm-up inconsistency.

14. P1 — §§7.2/7.4 make checkpoint isolation and research recovery incompatible unless their persistence boundaries change.

    Full byte equality is achievable only when the compared checkpoint excludes all intentionally different research state and its serialization is deterministic.

    If the checkpoint contains the section-3 machine, a real shadow day should differ from an off day in:

    - Session history and setup state.
    - Frozen reference and low/high anchors.
    - Shadow admission counts and cooldowns.
    - Modeled positions and pending entries.
    - Research event watermarks and last evaluated bar.
    - Potentially mode/configuration metadata.

    Checkpoint timestamps, generated identifiers, and nondeterministic serialization can also differ without any trading contamination.

    Excluding all these fields after a failing test is not an adequate fix. Specify the boundaries beforehand:

    - Persist research state in a separate namespace/store.
    - Compare a canonical projection of live trading state against off mode.
    - Separately test research-state recovery and event equivalence.
    - Control clocks and event order for fields where exact equality is required.

    Live cooldowns must remain identical; shadow cooldowns should evolve. A test that confuses those two requirements either cannot pass or prevents the shadow policy from functioning.

15. P1 — “Before admission, touching only queue and counters” cannot implement the declared shadow policy.

    The policy counts admitted signals and depends on cooldowns, slots, sector capacity, risk, ownership, fills, and exits. Pure signal evaluation alone cannot decide any of that.

    Calling the real admission path risks contaminating live state. Avoiding admission means the shadow sample does not obey its own frequency or portfolio rules.

    Shadow needs its own deterministic policy state, including virtual pending orders and positions, or a clearly defined read-only counterfactual based on recorded admission inputs. Those alternatives answer different questions; choose one.

    A mock assertion that `engine.submit_order` was never called with one strategy ID is also narrower than the isolation claim. It misses mislabeled submissions and alternate broker paths. The final entry boundary needs a runtime mode/provenance guard, while existing-position exits remain permitted.

    Shared queue backpressure is another distinction: state isolation does not prove shadow processing cannot delay live processing.

16. P1 — The supplied restore code does not support the claimed migration merely because two attributes are added.

    The excerpt updates `strategy.__dict__` from saved state. Only the named OR15 and tri-engine strategies receive explicit identity checks. Adding `version="v2"` and `state_schema=2` does not automatically:

    - Intercept legacy state before incompatible fields are restored.
    - Validate the saved v2 configuration.
    - Migrate nested per-symbol objects.
    - Restore event deduplication.
    - Prevent A3 state from being reused under A4/A7.

    Replaying session bars can reconstruct deterministic price-based setup state. It cannot reconstruct which signals passed admission when slots were occupied, when SPY arrived, what VIX value was available, or which research events committed.

    The restart-at-every-bar test needs the same recorded external inputs and admission feedback as the uninterrupted run. Otherwise it tests a simplified evaluator and leaves the deployed recovery problem untouched.

17. P1 — §5 calls a sample threshold “powered” without defining power, and permits an undefined stopping procedure.

    There is no minimum detectable effect, variance assumption, dependence model, target power, or specified prospective confidence-interval method. With five-day blocks, thirty days cover only about six block lengths; 150 trades do not create 150 independent observations.

    “Whichever is later” defines a minimum stopping boundary, but does not say whether there is:

    - One analysis at that boundary.
    - Daily testing afterward until a positive bound appears.
    - Repeated testing across configurations.
    - A maximum duration or an inconclusive outcome.

    Repeatedly checking an ordinary 95% interval until it clears zero is not a fixed-sample 5% test.

    The cohort must also include fully resolved outcomes for a fixed set of admitted opportunities. Counting the first 150 completed trades can preferentially admit fast winners while slower positions remain unresolved.

    If history changes the primary to A4–A7, prospective confirmation must use that exact frozen configuration from a declared start time. A3 outcomes cannot confirm A6 stops or A7 exits.

18. P1 — Shadow profitability can validate the fill simulator’s optimism rather than executable profitability.

    A tick-through requirement does not specify quantity available, queue position, submission latency, or the ordering of entry and exit prices within a minute.

    A bar can trade below a buy limit, below the stop, and above T1. Without a conservative ordering rule, the simulator can select the favorable path even while passing both stated sensitivity tests.

    The model must specify:

    - The first eligible fill after signal completion and any admission delay.
    - Gap prices and price improvement.
    - Partial versus full fills and quantity assumptions.
    - Entry/stop/T1/trail ordering within one bar.
    - Expiration-boundary behavior.
    - Costs on every exit type, including flattening.

    “Survive both” is also ambiguous: passing tick-through alone and extra cost alone does not imply passing their combination.

    None of the seven live-milestone items explicitly requires measured execution-model calibration or defines acceptable discrepancy. Functional lifecycle tests can pass while actual fills make the strategy unprofitable.

19. P1 — The block-bootstrap details still permit materially different answers.

    Missing specifications include overlapping versus nonoverlapping blocks, boundary treatment, resample length, random seed, period-specific inference, and handling missing market data.

    All policies must share the same calendar grid. A genuine zero-trade day belongs on that grid with zero daily P&L; a day missing required data is not automatically a zero.

    Pooling blocks across IS, VAL, and DEV also imposes assumptions about temporal stability. Period-specific confidence bounds require a corresponding period-specific resampling procedure.

    At 1,000 replicates, a p-value near 0.05 has Monte Carlo SE around 0.0069. Reporting that SE does not resolve a binary pass decision that can flip with the seed. The resolution rule must be fixed beforehand.

    Profit concentration has another denominator problem: “share of net P&L from the top 5%” can become enormous, negative, or undefined when net profit is near zero or negative. Define its handling and report a measure that remains interpretable.

20. P1 — The parity and completeness tests can fail for reasons unrelated to correctness, or pass while losing most evidence.

    The requirement for 40 symbol-days containing a signal may be impossible when the new rules are sparse. It is especially problematic if the selector itself uses the simulator whose missed signals the parity test is supposed to detect.

    Use deterministic constructed cases for mandatory branches and configurations, plus observed populated cases when available. Do not make correctness depend on discovering a minimum number of historical trades.

    The specified parity trace omits modeled order submission, fill, expiry, exits, sizing, and portfolio arbitration. Agreement through signal generation cannot validate the shadow P&L used for promotion.

    For the recorder, “events written per evaluated bar > 0” is not completeness:

    - One recorded transition can conceal hundreds of missing evaluations.
    - A transitions-only recorder can legitimately record nothing during a quiet session.
    - Multiple events per bar make a ratio above one compatible with missing bars.
    - A crash between checkpoint persistence and queue commit can lose or duplicate events without a database write error.

    Reconcile durable evaluated-bar markers and expected event identities. Queue acknowledgement, checkpoint advancement, and replay need idempotent or transactional coordination.

21. P1 — Historical PASS, live acceptance, and deployment identity are not enforced as one authorization boundary.

    “Cannot be reached by a config flip until signed off” is a requirement without a mechanism. The plan needs a fail-closed activation check tied to the approved configuration, universe, source identity, and execution assumptions.

    Otherwise a signoff for A3 can coexist with an environment setting that activates A6+A7 behavior or a later changed evaluator.

    Combined-portfolio replay is similarly only a reporting requirement. The plan does not say what marginal result fails acceptance, how loss accounting treats unrealized P&L and pending exposure, or how simultaneous candidates are ordered. A replay can show that v2 damages the portfolio and still satisfy the literal checklist.

22. P2 — Several labels conceal specification changes.

    A5 changes an entry gate, although the manifest describes A5–A7 as execution changes. “Independent trading days” overstates what a day count establishes. The institutional-execution title and TRAP terminology remain stronger than the aggregate-volume evidence. “No second run” also needs an explicit exception for correcting implementation defects without changing frozen hypotheses.

- P0: A partially filled entry can refill after its position is closed, while recomputed stops and late-frozen R make exposure accounting unstable.
- P1: Undefined transition priority, recovery inputs, and shadow admission state mean the plan does not specify one reproducible policy.
- P1: The bootstrap tests the wrong quantity, and the configuration selected historically is not necessarily the one confirmed prospectively or activated.
