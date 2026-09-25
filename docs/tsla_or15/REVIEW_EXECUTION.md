# Independent execution and recovery diff review

Reviewed 2026-09-25 against the current uncommitted implementation, `PLAN_2026_09_25_tsla_or15.md`, and `PLAN_CRITIQUE.md`. Scope: broker order lifecycle, fixed entry timing, recovery, durable intent, account emergency exits and manual flattening. No implementation files were changed by this review. Line numbers refer to the reviewed snapshot and may move when fixes land.

**Current review status:** the corrections and final verification below resolve the execution blockers identified in this review. The earlier findings and intermediate open items are retained as the review history; the final correction-pass section records their current disposition.

The fixed strategy is excluded from generic quote/bar matching, uses the existing account and brackets, and restricts the real adapter to Alpaca paper. Native protection is passively polled, and entry intent is saved before the entry POST. The following issues still prevent approval of the execution paths.

## Findings

### 1. Critical: missing OCO after a saved intent strands the holding and blocks every exit

`backend/app/core/or15_execution.py:195`–`200`, `283`–`286`.

The controller saves `protection_client_id` before the OCO POST. A crash between that save and the POST restores a valid one-share holding and an id for an order that never existed. A failed POST never accepted by Alpaca has the same outcome. On recovery, every successful lookup returning 404 sets `PROTECTION_OUTCOME_UNKNOWN` and returns. A requested manual, time or emergency exit calls the same recovery path and refuses to proceed until protection is confirmed. Consequently the share has neither native protection nor a usable flatten path, indefinitely.

Reproduced using the integrated runtime's completed entry and a deterministic broker whose saved-id lookup returns `None`: after three exit attempts, three lookups occurred, phase remained `EXITING`, one share remained held, and `exit_order_id` was `None`.

Required correction: distinguish persisted intent, attempted POST and observed broker state. Provide a tested recovery path for the conclusive no-order case while preserving the same client identity and avoiding a competing sell if an OCO may exist. Recovery must resolve either protection or liquidation; repeatedly returning on a successful 404 is insufficient.

### 2. Critical: a failed exit checkpoint occurs after native protection has already been canceled

`backend/app/core/or15_execution.py:287`, `294`–`306`; related entry-protection failure path at `203`–`207`.

`_run_exit` cancels and confirms both native legs before creating and checkpointing its market exit. If that checkpoint fails, it returns without sending the sell. Both protective orders are now gone while the share remains held. Subsequent attempts continue to depend on persistence recovery. The related failure to persist initial protection also requests an exit that depends on another successful write, so initial protection failure under a storage outage lacks a successful emergency path.

Reproduced with both local protective children bound to deterministic native orders and `checkpoint()` returning false: both native legs were canceled, no protective child remained working, one share remained held, and the accepted local exit had no broker id.

Required correction: persist the exit intent before removing existing native protection, and retain protection if that write fails. Define and test recovery for entry fill followed by a persistence outage; do not report a held share as protected or an emergency close as executed unless broker evidence proves it.

### 3. High: flatten-all omits an OR15 entry staged for the next minute

`backend/app/main.py:3012`–`3016`; targeted cancellation exists at `2924`–`2926`.

A `WAITING_ENTRY` signal has no position, working order or bracket yet. The target list for `/api/flatten` without a symbol therefore omits TSLA. The endpoint reports an empty successful result with zero remaining positions while the strategy remains eligible to buy at T+2. Calling the same route with an explicit TSLA symbol would consume the entry, so the omission specifically affects the all-symbol action.

Reproduced through `manual_flatten(None)` after the pure state machine's qualifying signal: before and after phase were `WAITING_ENTRY`; response was `flattened=[]`, `skipped=[]`, `rejected=[]`, `remaining_positions=0`.

Required correction: include OR15's reserved symbol in the flatten-all target set and verify the scheduled entry cannot subsequently be submitted.

### 4. High: pending entry is omitted from emergency-abort ownership

`backend/app/main.py:1109`–`1117`, `2014`–`2019`, `2041`–`2046`; `backend/app/core/engine.py:444`–`449`.

Generic bulk cancellation deliberately skips every OR15 order. Circuit-breaker, forced-flat and emergency-sweep paths then request OR15 exit only by iterating existing positions and checking `owns()`. An unresolved `ENTERING` order has no position yet, so those actions do not latch an abort for it. A later fill becomes `HOLDING` with no emergency exit reason; the clock controller does not independently check the halted account. A later market event might trigger another breaker pass, but correct recovery must not depend on one arriving.

Reproduced the controller-state portion: invoking `_trip_circuit_breaker` with phase `ENTERING` and no current position left phase `ENTERING` and `exit_reason=None`.

Required correction: emergency actions must cover reserved/pending OR15 ownership, consume waiting entries and persist an exit request for uncertain entries. A fill discovered after the abort should proceed directly through cancellation reconciliation and liquidation without waiting for another market event.

### 5. High: entry timing and quote freshness are checked against an old timestamp

`backend/app/main.py:1692`, `1828`; `backend/app/core/or15_execution.py:77`–`81`, `150`–`157`; `backend/app/core/engine.py:263`–`268`.

The bar handler captures `now` at ingress and passes it to the controller after generic strategy and broker execution work. Those synchronous broker calls can take several seconds. The controller checks its five-second deadline and two-second quote age against the old snapshot, then performs two durable checkpoints before the actual POST. The final broker gate checks market hours and mismatch only. A delayed handler or checkpoint can therefore submit outside the frozen T+2 window using a quote older than the accepted limit while the recorded intent appears timely.

Required correction: immediately before the actual fixed-entry POST, re-read production wall time, enforce the deadline and quote-age/positive-risk conditions, and record actual submission time. Inject the clock in deterministic tests and advance it during preceding work/checkpointing to prove late POSTs are refused.

## Validation performed

- `python -m pytest backend/tests/unit/test_tsla_or15.py backend/tests/unit/test_alpaca_broker.py -q`: **42 passed** in the reviewed snapshot.
- Separate isolated Python processes reproduced findings 1, 2, 3 and the missing emergency-abort latch in 4 using runtime state plus deterministic in-memory broker responses. These made no broker network requests, created no server, and left no background process.
- Finding 5 is established by control-flow inspection; it needs a clock-advance regression test at the final POST boundary.

The passing existing tests do not cover the reproduced recovery and operator cases. This review does not establish real market-session execution, native Alpaca acceptance of an OCO, deployment health, or future strategy performance. Re-review the corrected diffs and their focused tests before deployment.

## Resolution review after the first correction pass

Re-inspected the changes that predeclare OCO and emergency-exit identities before the entry POST. The created emergency order remains outside working orders until a close is requested; the broker settlement path can recover its saved identity after a crash. A successful close while storage is unavailable was independently exercised, followed by restoring the last durable holding snapshot: reconciliation removed the stale local holding, set phase `CLOSED`, and made **zero additional POSTs**. This validates the intended successful-first-close recovery path.

Current focused command covering `test_tsla_or15.py`, `test_or15_broker_lifecycle.py`, and `test_alpaca_broker.py`: **60 passed**. New tests cover the original missing-OCO recovery, storage failure immediately after the buy, a successful emergency close with storage unavailable, and stale submission time.

- **Finding 1 corrected for the original scenario:** a 404 now submits the same saved OCO client id, so a crash before POST can recover protection instead of waiting indefinitely.
- **Finding 3 corrected:** flatten-all includes TSLA when the controller reserves it.
- **Finding 5 corrected:** the controller rechecks production time and quote age after durable I/O at the final entry submit boundary. The bar handler also refreshes its timestamp after generic work.
- **Finding 4 partly corrected:** circuit-breaker and the normal forced-flat directive now latch an abort for `ENTERING` and consume `WAITING_ENTRY`. The audit emergency-sweep branch (`main.py:2050`–`2057`) still only covers positions and needs the same pending-ownership handling.
- **Finding 2 partly corrected:** initial protection and the first emergency close can use identities saved with the entry when later storage writes fail. The following retry case remains unsafe.

### Open: a canceled first emergency close exhausts the only durable fallback identity

`backend/app/core/engine.py:262`–`265`; `backend/app/core/or15_execution.py:46`–`52`, `313`–`325`.

With storage unavailable, the controller removes native protection and successfully sends the first predeclared close. If that close conclusively ends canceled with zero fill, subsequent attempts generate new generic client ids. The durable gate rejects each because the new id does not match the single predeclared identity. The share remains held with both protective legs canceled until storage recovers.

Independently reproduced using the real `AlpacaBroker` with `httpx.MockTransport`: after four close runs, broker quantity remained one; stop, target and first close were all canceled; only one market-sell POST occurred; phase remained `EXITING`; the fourth attempted client id did not equal the saved fallback id. This extends finding 2 and remains **critical**. The emergency retry policy must keep future identities recoverable or retain/restore actual broker protection when a retry cannot be durably authorized.

### Open: a predeclared OCO makes an aborted zero-fill entry wait forever

`backend/app/core/or15_execution.py:321`–`328`, `214`.

When an `ENTERING` order is aborted and conclusively cancels with zero fill, the entry ids resolve and there is no holding. The newly predeclared OCO id is nevertheless present. `_run_exit` calls `_ensure_protection`, which returns immediately because no share is held; `_run_exit` then returns because protection is unconfirmed, before its no-position completion branch. This leaves `EXITING` and TSLA ownership reserved indefinitely. Resolve the confirmed zero-fill entry before attempting OCO recovery, and verify that cleanup closes the lifecycle without posting protection or an exit.

### Open: normal protection no longer records accepted broker levels

`backend/app/core/or15_execution.py:222`–`232`, `244`–`245`.

Normal entries now always have a predeclared OCO id and therefore take the first branch. That branch posts protection without the `PROTECTION_POST` audit event; the event carrying theoretical and rounded broker levels remains only in the branch for a missing id. Emit the protection audit for initial and recovery submission, and preserve actual accepted broker levels. This is an audit requirement, separate from the critical retry issue.

Execution approval remains pending the open corrections and their focused regression tests.

## Final correction-pass verification

Re-reviewed the latest implementation and independently repeated the formerly failing storage/retry paths with the real `AlpacaBroker` adapter and an `httpx.MockTransport` that retains each broker order separately. No external orders were sent.

The emergency order now predeclares a deterministic retry namespace. The first broker-created attempt must finish before another identity is used. A lookup showing that an attempted POST created no order causes reuse of that same identity; it does not advance the sequence. This last detail was necessary: during review, a sequence containing an HTTP rejection without a broker order followed by a later successful identity was reproduced as unrecoverable at restart. The final correction to `_broker_execute` (`engine.py:227`–`233`) closes that gap. Recovery can consequently search the durable order's consecutive identities and stop at the first unused one.

Independent results with storage unavailable after entry:

| First close result | Second submission | Restart after successful close |
| --- | --- | --- |
| Broker-created order canceled with zero fill | Uses the next deterministic identity; fills one share | Broker and local quantity zero; `CLOSED`; zero additional POSTs |
| HTTP 403 without a created order | Reuses the original identity; fills one share | Broker and local quantity zero; `CLOSED`; zero additional POSTs |
| Read timeout without a created order | Reuses the original identity; fills one share | Broker and local quantity zero; `CLOSED`; zero additional POSTs |

The independently reproduced pending-entry abort also passes: after a confirmed canceled entry with zero fill, the controller finishes `CLOSED`, holds zero shares, has zero working orders, releases TSLA reservation and sends no protective or exit POST.

The audit phase now consumes `WAITING_ENTRY` and latches an emergency exit for `ENTERING`, independently of whether a position already exists (`main.py:2045`–`2049`). Both normal and recovered OCO confirmation now emit `PROTECTION_CONFIRMED`, including the rounded submitted levels, theoretical levels and native leg ids (`or15_execution.py:260`–`266`).

Final focused suite for this review:

`python -m pytest backend/tests/unit/test_tsla_or15.py backend/tests/unit/test_or15_broker_lifecycle.py backend/tests/unit/test_alpaca_broker.py -q`

**63 passed in 1.89 seconds.** All five original findings and the intermediate retry, zero-fill abort, audit-abort and protection-log findings are resolved in the reviewed code. No remaining critical or high execution/recovery defect was identified within this review's scope. This conclusion covers the inspected code and deterministic adapter/runtime checks; live broker acceptance, deployment and visual audit remain separate deliverables.
