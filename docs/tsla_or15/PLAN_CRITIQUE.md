# Independent implementation-plan critique

Reviewed on 2026-09-25 before implementation. Scope: `PLAN_2026_09_25_tsla_or15.md`, the frozen source at `/Users/mo/megacap_intraday_edge_lab/TSLA_OR15_RETEST_EXECUTION_PLAN.md`, and the existing ingestion, execution, broker, bracket, risk, persistence and clock paths. This review changed only this document.

The proposed integration is appropriate for the existing framework. The explicit override to immediate one-share Alpaca paper execution resolves the user's no-shadow requirement; actual paper prices cannot reproduce the source's raw-open convention. Keep the exact offline model as a test oracle and preserve both the paper execution differences and the source's unvalidated status. The following implementation obligations should be added before building.

## Required corrections

### 1. Specify real protection and its lifecycle

**Critical:** Current `broker.py:159` sends only market or limit day orders. A local STOP in `DynamicBracketManager` is not a stop resting at Alpaca: `engine.py:256` converts a triggered local STOP into a market order only after the application sees a crossing price. Feed loss or a process outage leaves that position without broker protection.

Choose and implement an explicit tested procedure, preferably broker-native stop/target OCO protection attached immediately after the actual one-share fill through the existing broker adapter. Keep its broker ids, actual accepted prices and protection status in the same durable runtime. Do not claim the position is protected merely because a local stop is in `working_orders`. If initial protection cannot be confirmed, reconcile the entry then close the held share and record a protection failure.

Native protection also requires an exception in `engine.settle_broker_orders` (`engine.py:275`): that function currently cancels every linked broker order while settling it. It must poll intended persistent protective orders without cancelling them. Stop/target/manual/time exits must have a single owner: confirm cancellation or discover an intervening fill before submitting a competing exit, then verify both broker position and remaining orders. The existing quantity precheck is insufficient if a resting sibling can fill between the check and the new sell. Test delayed cancellation, an OCO fill during cancellation, restart with a pending exit, and a failed flatten response.

### 2. Persist intent before a network order can exist

**Critical:** `engine._broker_execute` assigns a broker client id immediately before POST, while the normal checkpoint follows event processing. `main._checkpoint_runtime` even returns success without writing when called during an uncommitted inflight event (`main.py:602`). A crash after POST but before the checkpoint can lose the only id needed to recover the one permitted entry; replay creates random local ids and can submit again.

Use a stable strategy/session entry identity, and durably commit consumed-signal state, fixed quantity, pending entry, attempt/client id and timing before sending. Verify that the persistence call actually writes; a deferred write is not proof. An ambiguous POST must only look up/reconcile that same id. If it was rejected, expired, cancelled with zero fill, or missed its entry window, consume the session without a replacement entry. Preserve pending ownership until the broker conclusively resolves it; `_release_dead_entry_brackets` must not discard the link needed to protect a late entry fill. Exits can retry safely under their own stable identities after reconciliation.

### 3. Protect strategy ownership from existing exit signals

**High:** `execute_strategy_signal` (`main.py:1480`) currently closes any intraday position on a news `EXIT`/`CONTRADICTION`, irrespective of the position's strategy id. Excluding OR15 only from entry arbitration and adaptation does not protect its frozen stop/target/time contract. Exclude an OR15-owned position from those strategy-driven exits; continue to allow explicit operator/account emergency exits and log them as deviations. Also retain pending OR15 symbol ownership against entries from every other arm, including manual and swing routes, and never average into an existing TSLA position.

### 4. Define clocks and freshness numerically

**High:** Freeze the bar-completion grace, maximum feed delay, quote/trade freshness requirement and narrow T+2 submission deadline as implementation constants, record them with the code hash, and add boundary tests. Otherwise “fresh” and “bounded” leave different strategies possible. A useful production schedule is decision only after completed T is available at T+1, followed by one submission at T+2 within the declared small scheduling tolerance; never wait for T+2 OHLCV to complete. The 11:30 signal therefore remains eligible for an 11:32 attempt.

Use separate timestamps for source bar start, local receipt, decision, broker submission, broker fill and local booking. `engine._execute_fill` currently passes the triggering event timestamp into the Fill; delayed fills in `settle_broker_orders` use local observation time (`engine.py:304`). Neither is necessarily Alpaca's execution timestamp. Compute 120 minutes from the actual broker fill timestamp and persist it through restart. Record an unavailable broker timestamp explicitly rather than presenting an estimate as measured.

The shared bar handler advances clocks to start-labelled bar timestamps even in normal operation (`main.py:1646` onward). The OR15 controller must not rewind its wall clock or consume incomplete bar values through `on_time_tick(bar.timestamp)`. Use an injected explicit replay clock and a production wall clock for this arm.

### 5. Make data rejection occur before generic deduplication hides it

**High:** Identical production BAR events are discarded by `_begin_durable_event` before strategies see them (`main.py:1639`), whereas no-store/simulation tests bypass that deduplication. A duplicate test directly calling the strategy can pass while the real ingress path silently accepts a session the source says to reject.

Define a timestamp-level TSLA/QQQ validity check at ingress that observes relevant duplicates before generic deduplication, with an explicit distinction for internal durable-inbox replay. Validate OHLC relationships, finite positive prices, nonnegative volume, timezone/minute alignment and session boundaries. Buffer independent TSLA/QQQ arrival order only within the declared grace; process completed matching minutes in order. Require cumulative QQQ data from 09:30 for its VWAP and reject zero cumulative volume. Do not turn transport reversal across symbols into false within-symbol out-of-order errors. Test this with the real durable inbox enabled as well as the pure strategy.

### 6. Make account limits versus forbidden strategy filters explicit

**High:** Generic `pre_trade_risk_validator` still calls the risk engine with adaptation's VIX sizing multiplier (`main.py:485`), and `risk.py:342` rejects stops below 0.4% or above 4%. The plan says no extra discretionary filters and says never widen ORL, but does not settle whether these stop-width restrictions remain applicable. For source fidelity, bypass strategy stop-width/volatility sizing restrictions only for the frozen OR15 arm while enforcing one share, positive R, actual available buying power, shared position/sector/exposure caps and account loss/persistence/broker gates. Do not route the new arm through generic adaptation admission after calculating one share.

At actual fill, handle `fill <= ORL` explicitly. The present bracket recomputes risk with `abs(fill-stop)` (`bracket.py:237`), which converts invalid long risk into a positive number. A pre-order price check cannot rule out this fill. Record it as an execution deviation and flatten the actual held share; do not calculate a profitable-looking target from the absolute distance. Freeze a broker tick rounding policy without changing the mathematical source levels in the offline oracle.

### 7. Preserve exact offline precedence in the integrated dry run

**High:** Calling generic `process_bar` is not an exact test of the source: market fills use bar close plus spread/slippage (`engine.py:575`), and the handler evaluates signals before running the matcher on that same bar. Mark all dedicated entry and exit orders so the generic bar/quote matcher cannot execute them early or examine a pre-fill minute. Offline entry must use T+2 open, check that fill bar for stop/target after entry, and apply time/forced-flat open before inspecting the terminal bar high/low. Preserve stop-first when both levels touch, adverse opening gap stop price, exact touched-target fill, and the unusual ATR formula with the first session TR equal to high-low.

Use the source's actual `research/tsla_deep_audit.py:64` formula as an independent expected calculation; it is the rolling mean of up to 14 TR observations with `min_periods=3`, not conventional/Wilder ATR. A same-bar breakout+retest may only arm. Include exact threshold equalities and first qualified signal rejected at each gate. Keep paper-client transport tests distinct from deterministic raw-open tests.

### 8. Extend recovery invariants, not just the strategy-set migration

The exact legacy-four-to-known-five allowance is appropriate; do not broadly tolerate arbitrary missing strategies. Add validation for the new lifecycle: one consumed signal/session; one-share owner agreement; pending entry ids/deadlines; no terminal session with unexplained working entry; active bracket target equal to actual fill plus 2R; stop equal to ORL; matching broker protection; timeout and official close. Restore and reconcile broker orders before admitting any new signal. A restored expired entry must be cancelled/reconciled, never retriggered by an old durable input.

Only checkpoint fields serializable by the existing encoder. Injected callbacks, clocks and broker objects should live outside strategy `__dict__`, which is currently captured wholesale. Keep constructor-derived fixed rules/code hashes fresh on restore and compare source/version identity explicitly rather than silently adopting an older protocol. Archive session results and detailed retest evidence durably before daily reset; a capped UI history alone does not fulfill the source's all-session record.

## Additional acceptance evidence

- No-signal and no-feed sessions must be recorded by clock/session finalization even if no relevant bar reaches the strategy.
- A supported official calendar date must be established before arming; close-minus-five must work on a half-day despite the shared generic 15:55 engine. An unknown calendar must prevent entry without disabling management of an existing position.
- The fifth card needs its own exact signal hours/status; generic `strategy_window` otherwise reports shared trend blockers and phase ranges that do not apply to this arm. Display source watch hours 09:45–11:30 and distinguish completed-signal decision/entry timing in its explanation. Disable frozen stop editing on both server and UI.
- Exercise actual filled/protected/exited payloads in desktop/mobile QA and retain screenshots; an idle fifth card alone is weak evidence for management controls and durable history.
- Report source hash, implementation revision, one-share enabled paper state, retained account state after migration, dry-run outcomes and remote deployed commit separately. Unknown actual broker fees must remain unknown, not measured zero; fixed 3/6 bps costs and observed slippage require separate fields.

## Plan amendment checked

The subsequent pre-implementation “Critique resolutions” section now commits to native OCO protection, passive polling, confirmed sibling cancellation, durable client identity, news ownership isolation, SIP verification, explicit 10-second completed-pair grace, quote age at most two seconds, and an entry window confined to the first five seconds of T+2. These address the main design uncertainties. The actual-write-before-POST requirement, deduplication placement, risk exemptions, true fill timestamps and invalid-fill handling above remain concrete implementation obligations; they are not reasons to delay implementation while seeking further user permission.

The design can proceed with these obligations. This critique is a design review, not evidence that the implementation or deployed strategy has passed its requested tests.

## Independent final implementation acceptance audit

Revisited the final strategy/controller, runtime integration, fixed bracket and recovery validation, source copy, both diff-review reports, tests and `DRY_RUN_EVIDENCE.json` after the reported correction passes. This addendum records the current disposition; the earlier design findings are preserved as history. No implementation files were changed and no server or external broker request was started by this audit.

The frozen source is byte-identical to the supplied lab document; independently calculated SHA-256 is `1ed5091248fcaf1b66004eda2a8c21ed5114c23dbe9a590370c7a30e596ee5cd`. The dry-run evidence's implementation hash matched a fresh hash of the current implementation during this audit. The six-case artifact contains target, stop, both-hit stop precedence, adverse opening gap, 120-minute terminal open, and half-day close-minus-five terminal open; each records one share, a closed trade and zero remaining positions/orders. Inspection of the generator confirms that it drives the real bar handler and UI serializer. These are explicitly synthetic offline observations.

| Requirement | Current evidence |
| --- | --- |
| Exact fixed signal | Strategy uses same-session minute bars, exactly 15 opening minutes, the source's nonstandard rolling ATR, a separate strict breakout bar, all specified retest/QQQ comparisons and inclusive 11:30 cutoff. |
| Day-one paper routing | Dedicated controller requires the existing paper broker and verified SIP feed, uses one share and the consumed-signal latch, and schedules one production entry within the frozen T+2 tolerance. Production never selects the offline raw-open model. |
| Fixed management | Bracket preserves exact theoretical ORL/2R and one full target; native broker prices are separately recorded. Shared matchers, adaptation, trailing and news exits exclude the fixed owner. Actual fills drive timeout. |
| Recovery/protection | Current code contains durable entry/exit identities, passive native protection polling, native cancellation/settlement before a competing close, emergency retry identities, fill reconciliation and explicit lifecycle validation. Both independent diff-review reports record their final corrective verification. |
| No-signal sessions | Independent pure probe fed all 121 required pairs through the last eligible signal minute, then advanced beyond completion grace. Result: `NO_SIGNAL`, no skip reason, no trade. |
| No-feed sessions | Independent runtime probe advanced with no bars, then rolled the date. The archived session remained `SKIPPED / MISSING_REQUIRED_BAR`, preserved `2026-09-28` as its date and reported zero trades. |
| Prospective status | Runtime/API/session records keep `NOT_EVALUATED`; commissioning precedes 2026-10-01. The frozen document retains the four-quarter/minimum-trade/statistical criteria. Current implementation tests cannot satisfy those future criteria and no validated edge is claimed. |

Two record-label cleanups were sent to the main agent: a synthetic offline session dated after October 1 currently emits `evaluation=PAPER_OBSERVATION`, even though its enclosing mode is correctly `offline_raw_open`; and offline completed rows currently copy simulated engine fees into the `broker_fees` field. Prefer `OFFLINE_TEST` for the former and null/explicit simulated fees for the latter so nested records cannot be mistaken for measured broker observations. These do not change the source's signal or exit behavior.

No new blocking execution/source-rule defect was found in this final scope. Completion of the user's overall request still requires the main agent's actual desktop/mobile screenshots and assessment, notes, commit/push, Railway deployment identity and live endpoint verification. The task must not be called fully complete based only on this implementation audit. Native broker acceptance during a future market session and formal statistical validation are separate from the deterministic implementation evidence above.
