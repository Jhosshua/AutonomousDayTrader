# Independent execution review — 2026-09-25

Scope: `core/tri_execution.py`, its integration into `main.py`, and changes to `core/engine.py` / `core/broker.py`, against `PLAN_2026_09_25_tri_engine.md`, the frozen source execution plan, and Claude's critique. This review uses the real application runtime, durable SQLite checkpoints and `AlpacaBroker` over `httpx.MockTransport`. No external order, deployment, or market-session fill was performed.

## Review result before remediation

**Changes required.** Command: `python3 -m pytest backend/tests/unit/test_tri_broker_lifecycle.py -q`. Initial expanded run: **10 passed, 6 failed**. Each failure below has a deterministic reproducer in that file. These findings describe the inspected implementation before parent-agent remediation; rerun the named tests against the final source before treating any item as resolved.

### E1 — Critical: storage failure immediately after entry leaves the position unprotected

`_prepare_entry` persists only the entry identity. The stop/target and emergency close identities become available after fill booking. `_manage_tranche` exits when `checkpoint()` fails, and `_close_tranche` has the same prerequisite. If durable storage fails while the entry POST fills, both native protection and emergency liquidation are disabled indefinitely. The test observes 187 CDE shares remaining with no OCO.

Reproducer: `test_storage_loss_after_entry_fill_cannot_leave_bare_position`.

Required correction: persist recoverable protection/emergency identities and their allocation contract before entry submission. A storage outage must prevent new exposure while still permitting reconciliation and risk reduction through already committed identities. Test storage loss both immediately after entry and after native protection is already held, then restore from the last successful checkpoint and verify each fill is booked once.

### E2 — High: partial target fill prematurely cancels the remaining fixed exit

`on_fill` assigns `t['exit_reason'] = 'TARGET'` for any quantity. `_manage_tranche` interprets any `exit_reason` as an instruction to cancel the OCO and market-close the remainder. A single share reaching the target therefore liquidates the rest at an unrelated market price, without a time limit, manual exit, safety event, or protection failure.

Reproducer: `test_partial_native_target_keeps_remaining_fixed_protection`.

Required correction: distinguish an observed protective fill reason from a requested liquidation. Preserve valid native protection for a partially filled target; book cumulative fills exactly once and market-close only when the native pair becomes unusable or another exit rule applies.

### E3 — High: a halt during broker metadata lookup still submits the entry

`_entry_poll` captures `cancel` before queuing work. The worker then performs client-id and asset lookups, but its final checks consider clock/quote freshness and `_broker_gate` only. A circuit/manual/feed exit requested during those reads sets `s.exit_reason`, yet the stale captured flag still permits the POST. The reproducer requests the circuit exit inside the asset response and observes both an unnecessary entry and an emergency exit.

Reproducer: `test_halt_during_asset_lookup_cannot_send_new_entry`.

Required correction: revalidate the active entry intent, strategy/account status, exit request and current feed immediately before POST. Workers should use immutable request data and a narrow synchronized authorization handoff, with ledger mutations remaining on the event loop.

### E4 — High: emergency/time exit skips the market-hours broker gate

Entry submission calls `_broker_gate`; `_close_tranche` does not. Restoring a held position at 16:05 can therefore submit a DAY market order that queues for the next open. The old execution engine explicitly rejects this behavior. The reproducer makes the market gate refuse an exit and still observes a new POST.

Reproducer: `test_closed_market_never_queues_next_open_exit`.

Required correction: invoke the exit broker gate immediately before each new close POST while continuing read-only reconciliation outside hours. Also review OCO creation after session close and retain clear unresolved-position state when liquidation is unavailable.

### E5 — High: adverse short fill can exceed the reduced shared risk allocation

Preparation sizes against remaining portfolio risk. Fill handling checks only the instrument's full 0.75% cap. With $650 existing risk in a $50k account, the $100 remaining allowance sizes 66 shares using $1.50/share estimated risk. A $2/share actual short risk creates $132 exposure: combined risk becomes $782 against the $750 cap, while the code treats the trade as valid because $132 is below the instrument's $375 ceiling.

Reproducer: `test_short_actual_fill_cannot_overrun_reduced_portfolio_budget`.

Required correction: compare actual cumulative filled risk plus all other positions and pending commitments with the shared 1.50% limit, and trigger the declared execution-exception reduction path on breach. Include concurrent TSLA/CDE entry and partial-entry reservations in the final check.

### E6 — High: native stop filled before OCO binding prevents completion/recovery

When an OCO response already contains a filled stop and canceled target (possible after HTTP delay or restart), `_manage_tranche` creates and books the stop first. That removes the local position. Creating the already canceled target next goes through new-order risk validation, which treats it as opening exposure and rejects it. The pair never reaches `protection_confirmed`; subsequent polls repeat binding attempts and stay in `EXITING` although both broker and account are flat.

Reproducer: `test_native_stop_fill_before_oco_binding_is_booked_once`.

Required correction: bind all returned native identities as reconciliation records before applying their cumulative fills. Adoption of existing broker orders must not run pretrade gates intended to authorize new exposure. Binding must be idempotent after partial failure and restart, with no duplicate local fill orders or orphan accepted children.

## Behaviors exercised successfully in the initial run

- TSLA and CDE long and short routes through the actual adapter.
- TSLA separate 1.5R/180-minute and 2R/240-minute native tranches.
- First TSLA time limit closes only its own quantity; aggregate completion waits for both.
- Generic broker settlement leaves controller-owned OCO orders untouched.
- OCO cancel/fill race accounts for target fill without a second market sell.
- Pending cancellation blocks a replacement exit until terminal confirmation.
- Held-position restart preserves native identities without duplicate POSTs.
- Passive pending-entry restart preserves its identity and accepts a later actual fill.
- Partial entry cancels the remaining entry and closes only actual filled shares.

## Verification limits and follow-up

The tests use synthetic broker responses and, initially, `inline_io=True`; they do not establish latency or thread safety under a real concurrent executor, nor Alpaca's real partial-OCO behavior. The dedicated pool architecture removes direct synchronous entry waits from the controller event-loop call, but executor saturation, halt/POST races, simultaneous deadlines, transient 429/503 responses and shutdown/restart during in-flight work still require explicit deterministic coverage. Storage recovery also needs tests against the last durable state, not a checkpoint written after the tested failure.

Full production-handler ingress, session circuit behavior, runtime migration/validation and frontend audit remain the parent integration's completion gates. This report does not sign off deployment by itself.
