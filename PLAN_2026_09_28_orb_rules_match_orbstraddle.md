# Plan: ADT's own ORB strategy trades by ORBStraddle's rules (2026-09-28, draft v3 after Codex rounds 1 and 2)

**Status (2026-09-28 evening):** phase 1 (decision code copy, exact parity), phase 2 (execution layer) and
phase 3 (integration into `main.py`: settings, old ORB removed, checkpoint migration, exit dispatch,
shared reservations, risk hooks, card, fake sessions) are built on branch `orb-integration`. NOT
deployed. Next: deploy after 16:00 in `ORB_MODE=shadow` (section 10 step 4). Deviations from this plan
are listed in MEMORY.md (2026-09-28 evening entry). 9.6 "and vice versa" stays UNBUILT on purpose
(operator/coordinator ruling 2026-09-28): ORB's sizing stays exactly ORBStraddle's and never shrinks
for other arms' losses; ADT's daily loss stop still halts ORB, and ORB's open risk is reserved out of
ADT's budget. Known risks ACCEPTED (ORBStraddle parity, alarms escalate at 30 s / 2 min / 10 min):
after a conclusively rejected close ORB restores no protection (the legs are already cancelled; the exit
retries every pass), and after an ambiguous close answer (429 / 5xx / lost reply) the shares are unprotected
for up to the 60 s grace before a new exit may go out. The shadow input fingerprints (9.8) are not built; the Alpaca limiter is ORB's own budget (100/min +
50 exit reserve), not one limiter shared with ADT (8.9).

Operator request (2026-09-28): ADT's general ORB must execute exactly like ORBStraddle. Clarified after a
wrong first plan (sidecar robot, `PLAN_2026_09_28_orb_becomes_orbstraddle.md`, SUPERSEDED): **ORB stays
inside ADT as its own `orb` strategy; its rules are rewritten to match ORBStraddle's** (range, triggers,
filters, stops, targets, exits, sizing). No second service. Operator: make the best decisions, call them out.

Source of truth for ORBStraddle's live behaviour: `docs/orb_replacement/orbstraddle_spec.md` (ORBStraddle
HEAD 71b001f, rules `adaptive-v1.6.0-flow-rules`, live Railway vars). ADT map: `docs/orb_replacement/adt_orb_map.md`.

## 1. Target behaviour (what ORB will do, same as ORBStraddle today)

- **Universe:** top 250 non-fund US stocks by prior-day close x volume, rebuilt each morning.
- **Range:** SIP trade prints 09:30:00-09:35:00 (odd lots excluded from price).
- **Break:** first price-eligible print beyond the range at/after 09:35:05 with a fresh deep quote
  (<= 5 s, >= $25k each side) and SOFI |z| >= 2. Candle rule, delta >= 0.05 its way, velocity burst.
- **Decision:** one at ~09:38:30 (primary), then each minute 09:45-10:15 on new symbols (secondary).
  Wave sits out if the board is one-sided (<25% or >75% shorts) or SPY/QQQ bars or news are incomplete.
  Per card: spread <= 8 bps, RVOL >= 2.2, ATR >= 2%, stop 0.5-7.5%, macro veto (SPY + sector ETF),
  no-news picks must be a failed gap >= 0.8% (not a mega cap). Utility model, at most 2 picks, pick 2 in a
  different sector.
- **Entry:** Alpaca market BRACKET (day) resting at the broker: stop = range extreme -/+ 0.25 ATR (at least
  0.5%), target = fresh price +/- 0.75R. No-chase: skip if price moved > 0.33 of the stop distance.
- **Exits:** broker bracket (target / stop), plus software exits every 5 s: fast-fail at -0.40R, breakeven
  after +0.75R, clawback (peak >= 0.60R then -0.25R), absorption exit (flow rule 4); flatten 11:00.
- **Sizing:** 2.0% of day-start equity on the first trade, 2.5% day risk budget (2nd trade <= 0.5%, then
  none), own daily halt at -3%, 4 slots / max 2 structure picks, gross cap min(300% equity, 60% buying
  power), per-name 150%.

## 2. Decisions (called out)

1. **Copy ORBStraddle's decision code, rebuild only the execution.** The five decision modules (`scanner`,
   `orbproc`, `signals`, `flow`, `adaptive`, ~4.3k lines) are copied nearly verbatim into
   `backend/app/strategies/orbs/` (imports rewritten; a small shim replaces the 11 calls they make into
   ORBStraddle's `core`/`config`: `today`, `now_et`, `validate`, `_valid_card`, `long_only`, ledger
   reads, heartbeat). A header records the source commit 71b001f. Why: "exactly" is only provable if the
   decision code is the same code; a rewrite from the spec would drift in dozens of details (tie order of
   trades vs quotes, excursion latches, float rounding). Rejected: re-deriving the rules in ADT's
   bar-based framework (ADT strategies see 1-min bars of ~17 symbols; ORBStraddle needs raw trades +
   quotes for 250 names).
   Execution (orders, brackets, supervisor exits, sizing, ownership) is NOT copied: ORBStraddle's `core.py`
   is 4.4k lines built around its own files and ledger. It is re-implemented in ADT as
   `backend/app/core/orb_execution.py`, modelled on ADT's tri-engine (`tri_execution.py`), which already
   does durable intents, broker-side OCO, cancel-then-close exits and restart reconciliation.
2. **Keep the strategy id `orb`.** Checkpoint restore raises on id-set changes. The old per-symbol ORB
   state (`SymbolORBState`) is intraday-only; a restore rule in the same commit drops it for `orb`
   (tested), and the new controller persists its own state in the checkpoint.
3. **ORB leaves ADT's shared admission path, like the tri-engine.** No ADT market filter, phase gate,
   VIX sizing, $12.5k allocation cap, 0.4-4% stop band or 3-position cap for ORB: ORBStraddle's own gates
   and sizing replace them (keeping them would not be "exactly"). ORB positions get their own 4 slots.
4. **ADT's account-level safety still covers ORB.** ADT's daily loss stop (min $1,500, 2.5% equity, about
   $1,240) counts ORB's P&L and, when hit, the ORB controller stops entries and exits its own positions.
   Called out: one full ORB stop (~$990 at 2% of ~$49.7k) uses most of that stop, so a bad ORB morning can
   end ADT's other strategies' day. ORB's own -3% halt is kept too (it will rarely be the one that binds).
5. **Sizing exactly ORBStraddle's: 2% of day-start equity.** Called out: about 2x ADT's usual ~1%, and
   the old $12.5k cap no longer applies to ORB (a tight 0.5% stop can mean ~$74k notional; the gross cap
   60% of buying power and ADT's buying power still bound it).
6. **TSLA and CDE are removed from ORB's eligible picks** (they stay on the board so the one-sided-board
   test is unchanged; the next candidate is promoted). Why: ADT's Tesla/Coeur morning plans enter
   09:45-12:00 and skip their day if the symbol is already held. The only deviation; called out.
7. **Same-symbol rule inside ADT:** ORB will not pick a symbol ADT already holds or has a working order on
   (ORBStraddle's "occupied" rule, applied to ADT's own book), and ADT's other arms refuse a symbol ORB
   holds or has reserved. One process, one lock, so no race (unlike the sidecar).
8. **Data: same relay endpoints ORBStraddle uses** (daily bars, SIP trades/quotes REST for the board,
   SPY/QQQ + sector ETF 1-min bars, news, latest trade), with ADT's existing `RELAY_TOKEN`. Runs in worker
   threads with deadlines so ADT's event loop never waits (same pattern as the volume-profile builder).
9. **One shadow morning first, then live (Codex #11).** `ORB_MODE=shadow` on the first session after the
   build: it scans, decides and records, sends no orders, and its 09:38 board and verdict are compared
   with ORBStraddle's live board that morning. Orders start the next session only if they match (same
   cards, same verdict; picks may differ only by TSLA/CDE and occupied symbols). Called out: ORB places no
   orders for one morning.
10. **Day risk budget stays 2.5% (Codex #7 checked):** ORBStraddle's config comment mentions a historic 8.0,
   but the live Railway var is unset and live `/api/state` reports `day_risk_budget.max_risk_pct 2.5`, so
   2.5% is what ORBStraddle actually runs. Pinned in the parity manifest (section 8).

## 3. Execution design (`orb_execution.py`)

- **Entry:** per pick, re-check occupied + candle/flow + fresh macro veto, fresh price (relay latest SIP
  trade, age <= 60 s), no-chase 0.33, stop side and >= 0.5%, sizing (section 1), live buying power check
  (refuse, never shrink). Intent persisted (checkpoint) BEFORE the POST. Order: Alpaca `order_class=bracket`,
  parent `market`, `time_in_force=day`, `take_profit.limit_price`, `stop_loss.stop_price` (2 dp).
  Client id `adt-orb-{SYM}-{date}-w{n}-a{k}`. New broker method `submit_bracket` (ADT's broker has
  market/limit/OCO only). Transport error: look up by client id 3x, 2 s apart; explicit 4xx = rejected;
  otherwise UNKNOWN, kept as pending and resolved by client id (ORBStraddle's rule, no resubmit).
- **Ownership:** own quantity from fills of ORB's parent AND its bracket legs (legs are found through the
  nested parent, their client ids are broker UUIDs). Partial parent fill: legs resized to the filled qty.
- **Supervisor (every 5 s while ORB holds a position):** reads Alpaca position price; R = (px - avg fill)/rd
  signed. Exit priority: ADT daily loss stop / ORB halt, 11:00 flatten, breakeven (try PATCH stop leg to
  entry at +0.75R; software exit at R <= 0 after that either way), clawback, absorption, fast-fail -0.40R.
- **Every exit:** cancel ORB's own bracket legs, confirm they are final, then one market day order for
  exactly ORB's own qty (never `DELETE /positions`), confirm by fills. This avoids Alpaca's "one sell order
  per position" refusal.
- **ADT's generic exit paths skip ORB** exactly as they skip tri today (15:50 purge, 15:55 flatten, 15:58
  sweep, circuit breaker, news-contradiction exit, manual flatten, session-boundary liquidation): they call
  the ORB controller's own exit instead, which cancels legs first. ORB is flat by 11:00 anyway.
- **Restart:** on startup, rebuild ORB's positions from the checkpoint + Alpaca orders by client id
  (parents with nested legs); anything ORB cannot prove is its own is reported, never adopted.
- **Mismatch check:** ORB's positions are in ADT's local book (booked from real fills), so ADT's existing
  position comparison keeps working unchanged.

## 4. Dashboard and records

ORB card: "Opening Range Breakout (ORBStraddle rules)"; hours "Decides 9:38 AM, may add trades until
10:15 AM, closes by 11:00 AM"; shows the verdict in plain words (picked X / sat out: board one-sided /
no card passed, top reason), open trade with stop, target and R, P&L. Old ORB text (5-minute bar range,
11:30, RVOL vs 20 bars) removed everywhere. Decisions log and `/api/research` record every card, verdict
and pick with the same fields ORBStraddle's ledger has; trades land in ADT's normal ledger.

## 5. Verification before live orders

1. **Parity of the decision code:** run the copied modules on ORBStraddle's recorded inputs for
   09-22..09-28 (its live `/api/research/day` cards and ledger) and require identical cards, board
   verdicts and picks (TSLA/CDE exclusion off for this test). Any difference = not "exactly", fix first.
2. **Execution unit tests** with a fake Alpaca: bracket submit, partial fill + leg resize, target fill,
   stop fill (UUID legs), each software exit (cancel legs then close), 11:00 flatten, UNKNOWN submit
   resolved by client id, restart mid-trade, ADT flatten/breaker/manual paths routing to the ORB
   controller, ADT daily stop stopping ORB, day risk budget and slots, occupied rules both ways, TSLA/CDE
   exclusion promoting the next pick.
3. **Full fake session** through `main` (as done for Ride the Trend v2): 09:30 range -> 09:38 pick ->
   bracket -> +0.75R breakeven -> exit; and a sit-out day. Full ADT suite green.
4. **Rate budget:** ORB's supervisor (12/min per position) + board scans (relay, not Alpaca) + ADT's
   existing use stays under Alpaca's 200/min; measured in the fake session and on the first morning.
5. **After deploy (after 16:00):** `/api/strategies` shows the new ORB card and hours; checkpoint restore
   clean; `/health` no errors. First morning: compare ADT ORB's 09:38 board and verdict to ORBStraddle's
   (same cards; picks may differ only by TSLA/CDE, occupied symbols, equity).

## 6. Rollback

`ORB_MODE=off` (no new ORB entries; open ORB positions keep being managed to their exits) or
`ORB_MODE=shadow`. The old ORB code is removed from the strategy (git history keeps it).

## 7. Known limits carried over from ORBStraddle (not fixed, on purpose: "exactly")

Breakeven PATCH of a held leg probably returns 422 (software breakeven exit is the real one); secondary
decisions reuse the 09:38 SPY/QQQ window and news cutoff; supervisor exits lag up to 5 s plus slippage;
no proven edge for these rules (project notes). Future ORBStraddle changes must be re-synced into the
copied modules (a sync check compares the copied files with ORBStraddle `main` and fails the test if the
logic differs from the recorded source commit).

## 8. Additions from Codex round 1 (`docs/orb_replacement/codex_rules_plan_round1.md`, 2 P0 + 9 P1, all accepted)

1. **Bracket lifecycle adapter (P0 #1).** `broker.py` gains `submit_bracket`, `get_order_nested(id)`,
   `patch_order(id, qty/stop)`, `cancel_and_confirm(ids)`. `orb_execution.py` owns the full lifecycle:
   parent id + nested leg ids (UUID client ids found through the parent), cumulative parent and leg fills
   booked into ADT's local position and ledger (so ADT's mismatch check stays true), partial-fill leg
   resize, UNKNOWN POST outcome resolved by client id, cancel/replace, and restart reconciliation from
   Alpaca BEFORE ORB may enter. ORB fills never go through ADT's generic late-fill path.
2. **Explicit ORB dispatch in every exit path (P0 #2).** Every place that today special-cases tri/OR15
   gets an ORB branch that calls `orb_controller.request_exit(reason)` (cancel + confirm legs, then close
   exactly ORB's quantity): circuit breaker, `_flatten_symbol`, 15:50 purge, 15:55 flatten, 15:58 sweep,
   session-boundary liquidation, news-contradiction exit, manual flatten, manual stop-tighten (refused
   for ORB with a plain message: its stop lives at Alpaca). A test opens a bracket and drives each path.
3. **Own controller registry (P1 #3).** A new ORB controller with its own startup, per-loop tick and exit
   dispatch, registered next to (not inside) tri; the generic strategy loop skips `orb` and the old
   strategy class is deleted, so the old admission rules cannot run. Symbol reservations are held under
   one lock shared by ORB and ADT's other arms (pending intents and working orders count as taken).
4. **Checkpoint migration (P1 #4).** A checkpoint schema bump with a migration at decode time: the old
   `SymbolORBState` type stays importable (a thin compatibility class) until old checkpoints are gone;
   old `orb` fields are dropped, never merged into the new state. Tested on a real pre-change checkpoint
   copied from production `/data`.
5. **Full dependency surface (P1 #5).** The copy includes what the scanner really imports (`market.RelayClient`,
   `ticks`, needed parts of `config`), adapted to ADT's relay settings. A **parity manifest** pins every
   live value from ORBStraddle's Railway vars and `/api/state` (flow rules ON, candle ON, risk 2.0, day
   budget 2.5, slots 4, cutoff 10:15, flatten 11:00, thresholds); a test fails if the copy's effective
   config differs from the manifest. Never inferred from code defaults.
6. **Scheduler state machine (P1 #6).** ORBStraddle's `app.py` timeline ported as an idempotent,
   session-aware state machine: calendar check, 09:15 sizing freeze + prep, 09:36:10 preview, 09:38:30
   final scan, coverage >= 90% or one REST retry else no decision, secondary scans every 60 s 09:45-10:15
   only after a good final scan and with free slots, incremental scan state and secondary dedupe, 10:15
   cutoff, 11:00 flatten. Restart at any point resumes the right step (tested at each step).
7. **Frozen-input parity test (P1 #8).** A recorder saves the exact inputs of a session (universe, prior
   daily bars, raw SIP trades/quotes 09:30-10:15 for the board, SPY/QQQ + sector bars, news with
   timestamps, config) from the relay's historical endpoints. The ORIGINAL ORBStraddle modules and ADT's
   copies both run on the same frozen inputs for 09-22..09-28; boards, verdicts and picks must be
   identical. Plus the live shadow morning compare (decision 9).
8. **Full exit state machine (P1 #9).** Ported: refresh own orders first, partial fills, fresh-only
   absorption results from a background reader, exit priority, failed-close escalation and retries.
   Tests: stale/missing price, PATCH 422, partial fills, cancel not terminal, rejected close, escalation,
   restart mid-exit.
9. **Traffic bounded and measured (P1 #10).** Relay (scans) and Alpaca (orders/supervisor) budgets are
   separate; ORB gets an Alpaca budget with an exit reserve, shared limiter inside ADT; scans have hard
   deadlines and fail closed (no decision) if late or incomplete; measured in the shadow morning.
10. **Blocking tests (P1 #11):** real old-checkpoint migration, holiday + restart recovery, low-coverage
   retry, late secondary scan + dedupe, cross-arm symbol race, ADT breaker with an ORB bracket open,
   unknown parent / UUID leg fills.

## 9. Acceptance criteria from Codex round 2 (`docs/orb_replacement/codex_rules_plan_round2.md`)

Two rounds is the stopping rule; these are build requirements, each with a test.
1. **Exit cancels the parent too:** cancel and confirm the parent remainder and both legs, reconcile final
   fills, then close the remaining own quantity (ORBStraddle orders live entries first, core.py:3992).
   Test: exit while the parent is still partially filling.
2. **Every generic exit reason routes through ORB ownership:** the `EXIT`/`CONTRADICTION` handler
   (main.py:1735), the API cancel route (main.py:3433), and all paths in 8.2.
3. **Reservations include staged swing entries** (swing_panic_dip.py:307), pending intents and working orders.
4. **Checkpoint migration on both layers:** persistence schema version (persistence.py:276) and runtime
   restore (runtime_state.py:178, 229); the ORB controller's state is its own checkpoint section, and
   `orb` stays in the strategy id set.
5. **Parity manifest is a checked-in file** (`backend/app/strategies/orbs/PARITY_MANIFEST.json`): effective
   switches, thresholds, times and the source commit; test compares the copy's effective config to it.
6. **ORB risk reserved account-wide:** ORB's open + pending risk is counted in ADT's remaining daily loss
   budget before other arms size, and vice versa.
7. **Relay ceiling:** ORB scans use a bounded worker pool with a total request ceiling and back-off, scans
   fail closed when late; ORBStraddle's 24-worker setting is kept unless the relay throttles (measured).
8. **Shadow compare uses input fingerprints:** the shadow morning records the tape window, news cutoff and
   config hash for both bots; order mode stays off if they differ.
9. **Account isolation confirmed:** ORBStraddle trades PA3RPSMUR65S, ADT trades PA3CSVDZMMPY (checked in
   both Railway configs 2026-09-28), so no double entries; ORBStraddle keeps running unchanged.
10. **Relay history coverage checked before building the parity test:** the relay served raw trades and
   quotes for 09-23..09-25 in the Ride the Trend replays; 09-22..09-28 trades, quotes and news are fetched
   and saved first, and any gap shortens the parity window rather than being guessed.

## 10. Build and deploy order

1. Recorder + parity harness (original vs copy on frozen inputs) FIRST; copy the modules until parity is exact.
2. Broker bracket adapter, ORB controller, scheduler, exits, dispatch, reservations, migration, card.
3. Full ADT suite + blocking tests + fake full session.
4. Deploy after 16:00 in `ORB_MODE=shadow`; shadow morning compare; switch to live the evening after a
   clean compare.
