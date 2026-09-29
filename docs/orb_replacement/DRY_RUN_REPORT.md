# ORB inside ADT: full dry run on 5 recorded days (2026-09-28)

## Final code (orb-integration @48038e9, re-run 2026-09-28 night)

Everything below this section is the FIRST run (before the fixes); this section supersedes it.

**What changed in the harness:** branch `orb-dryrun` fast-forwarded to `orb-integration` 48038e9. The
harness-only macro-veto flip is removed (plain `ORB_MODE=live`). ORB's decision code now runs in its real child
process (`backend/app/core/orb_facade_proc.py`, started by `orb.start()` in lifespan). The harness gives that
child the recorded relay through the proxy's own `http_factory` hook (`scripts/orb_dry_run/child_replay.py`):
the child blocks every socket, answers every relay request from the recorded cache, and reads the simulated
time from an 8-byte shared file the parent updates each step. Every run below went through the child
process path (there is no in-process facade in these runs). 0 relay misses, 0 child restarts, 0 run errors.

**Verdict:** ADT's ORB now behaves like ORBStraddle end to end. All 160 boards and all 130 decisions (full
adaptive reply) are identical to ORBStraddle's ORIGINAL code on the same boards at the same times, plain live
mode places, manages and exits trades, shadow makes the same decisions with zero broker writes, and every
restart gives the same ledger.

### Per day (ORB_MODE=live, fake broker from the recorded tape; shadow decisions identical every day)

| Day | 09:38 verdict | Picks | Entries | Exits | ORB P&L | vs original code | vs ORBStraddle live |
|---|---|---|---|---|---|---|---|
| 09-22 | pass | 09:46 SOFI short, 09:47 SKHY long (MU, MRNA, WDC later refused: 2 structure slots used) | SOFI 1,461 @ 17.385 (ref 17.44, stop 18.12, tgt 16.93, risk $993.48); SKHY 28 @ 192.765 (stop 183.96, tgt 199.45, risk $247.77 = rest of 2.5%) | SOFI absorption 10:30:13 @ 17.0301; SKHY 11:00 flatten @ 193.66 | +$543.57 | 32/32 boards, 28/28 decisions | live ran v1.4.2; it also picked SOFI 09:46 and SKHY 09:47 but executed nothing |
| 09-23 | pass | none | none | none | $0 | 32/32, 26/26 | live v1.4.2 traded HOOD, C (other rules) |
| 09-24 | sit out (ONE_SIDED) | none | none | none | $0 | 32/32, 28/28 | same 09:38 sit-out; later trades were v1.4.2 |
| 09-25 | pass | none | none | none | $0 | 32/32, 22/22 | live v1.4.2 traded BMNR, NKE |
| 09-28 | sit out (ONE_SIDED 83.7% short) | 10:05 APP short | APP 62 @ 311.4689 (ref 310.70, stop 326.64, tgt 298.75, rd 15.9397, risk $988.26) | 11:00 flatten, filled 11:00:03 @ 309.645 | +$113.08 | 32/32, 26/26 | **match**: same sit-out (0.837), same APP short from the same 10:05 board, same stop and formulas, same exit path (11:00 flatten). Live decided 3.5 min later (late scans) at 313.03, 106 shares on its $72k account: +$309.52 |

Entry math re-computed independently for all 3 entries (fresh price, stop, rd, target, shares = floor(2% x
$49,702.10 / rd), capped by the 2.5% day budget): all equal, and equal to the bracket payload sent.
These results are identical to the first run's adapter runs (same fills, same P&L), so the P0 fix changes
nothing but the contract.

### Restarts (09-28, live, new process on the same state and fake account)

| Scenario | Ledger vs uninterrupted | Orders | Notes |
|---|---|---|---|
| Hard kill 09:38:45 | identical (+$113.08) | 1 bracket + 1 exit | 9:38 decision not re-run; startup reconcile ok, 0 unknown orders |
| Hard kill 10:30:03 (mid-trade) | identical | 1 + 1 | position rebuilt, 11:00 flatten |
| Graceful stop 10:30:03 | identical | 1 + 1 | drain + final checkpoint, then restore |
| Kill inside the APP bracket POST | +$113.70 (exit 1 s earlier @ 309.635) | 1 + 1, no duplicate | fill found by client id; supervisor 5 s phase shifted 1 s |

### ADT side effects (all 10 day runs)

Broker mismatch never latched; zero Alpaca 403s; other arms refused ORB symbols (`ORB_OWNED`); breaker drawdown
equals ORB's unrealized loss (checked while holding); zero broker writes after 11:01 (15:55 flatten untouched);
end-of-day checkpoint restored in a new process with the same account and trades on all 5 days; ORB Alpaca
requests max 2,347 per morning, never throttled.

### Event-loop lag, 09:36-10:15 (ORB decision code in the child process)

- **Clean measurement (one run alone, parent tape not preloaded, 09-28 to 10:08 incl. the APP entry): max 13.7 ms,
  0 samples over 100 ms** of 50,135. Target < 100 ms: met. First run (decision code in-process): 1.6-2.6 s.
- The 14-run matrix shows one sample per run between 145 and 244 ms. Traced: a full (generation 2) garbage
  collection of 115.7 ms at 10:06:30, caused by the harness itself: it preloaded the whole recorded tape (millions
  of rows) into the parent, which production never holds. With the preload off, no collection over 20 ms. The
  matrix numbers also include CPU contention (7 runs + 7 child processes at once). Preload is now opt-in.
- ADT's own loop work: `_runtime_clock_step` max 21-29 ms (one 196 ms outlier in the parallel 09-22 shadow run).

### Bugs (final code)

- **BUG 1 (P0) macro veto read backwards: FIXED** in `orb_execution.py:1752-1761`; my 2 regression tests pass.
- RISK 1 (GIL stalls): **resolved** by the child process (see lag above).
- P3 (card step text while a trade is open): **resolved**; at 10:16 on 09-28 the card reads "Shorted APP (short), 62 shares, now -0.09x its risk; stop 326.64, target 298.75 (held at Alpaca); closes by 11:00 AM."
- No new bugs found.

Branch `orb-dryrun` (from `orb-integration` @1c9a47c). Harness: `scripts/orb_dry_run/`. No real orders, no real
Alpaca, no network during a run (every socket connect raises). Relay answers came from the recorded parity cache
plus a small read-only top-up (see "Inputs").

## Verdict in one paragraph

ADT's ORB decides exactly like ORBStraddle's original code on all 5 days: every board (160 of 160) and every
decision (130 of 130, the full adaptive reply) is identical to the original code on the same board at the same
time, and the entry math is exactly ORBStraddle's formula applied to the fresh price. **But ORB_MODE=live as
built can never place a normal trade**: the controller reads the macro veto's answer backwards (BUG 1, P0).
On 09-22 and 09-28 every pick was dropped right before the order with "macro veto before the order: " (empty
reason), and a pick the macro veto refuses would be sent. Shadow mode skips that check, so the planned shadow
morning would not have caught it. With a harness adapter that flips the answer, the rest of the live path works:
brackets, fills, exits (absorption, 11:00 flatten), ledger, breaker, restarts, end-of-day restore.

## How the dry run works

`run_day.py` boots `backend.app.main` in-process with its real `lifespan` (checkpoint restore, broker startup
sync, `orb.start()`, final drain and checkpoint), persistence in a temp dir, `ORB_MODE` per run, and ADT's real
`AlpacaBroker` pointed (MockTransport) at `tape_broker.TapeAlpaca`, a fake paper account driven by the recorded
SIP tape:

- market orders (bracket parents, exits) fill at the first regular-lot print after acceptance;
- after a parent fills, the take-profit leg is `new` and the stop leg stays `held` (Alpaca), so a PATCH of the
  stop answers 422; take-profit fills at its limit when a print reaches it; a stop triggers on a crossing print
  and fills at the next print; OCO cancels the sibling;
- one sell order per position (403 / 40310000 when a plain order does not fit next to working or held legs);
- last_equity $49,702.10 (ADT 09-28 opening equity), buying power 4x equity, marks = last print.

A simulated clock drives `main._runtime_clock_step` every second 09:30-11:05 (5 s before, 10 s after, to
16:05). ADT's `_broker_reconcile_once` (30 s) and settle (5 s) run on the same clock, ADT's SIP trade handler
gets each held symbol's last print. ORB's worker jobs (scans, decisions, orders, 5 s supervisor, absorption
reads) run on their real threads; the clock waits for them (zero compute latency, wall time recorded).
Clock injection only through `OrbIntegration.build(clock=, monotonic=, sleep=, budget=)` (as the phase-3
tests do), plus the decision shim's unpinned `now_et()` and `orb_integration`'s throttle clock. No product code
changed.

Relay (`store.py`): tape 09:30-10:15 from the parity cache (read-only), exact answers from the parity cache,
and three synthesizing stores, each proven against the recorded answers: 1-minute ETF bars (reproduces all 178
recorded bar answers byte-for-byte after parsing), news (same article set for all 278 recorded news queries;
order differs only among ties, which adaptive.py ignores except for exact tier+timestamp ties), latest trade =
last print at or before now. Top-up (read-only relay, `topup.py`): 1-minute bars of SPY, QQQ and 11 sector
ETFs 09:30-11:05 each day, and trades+quotes 10:15-11:05 for SOFI, SKHY, MU, MRNA, WDC, BABA (09-22) and APP
(09-28). Zero cache misses in every run.

Reference for "what ORBStraddle does": `orig_decide.py` runs ORBStraddle's ORIGINAL modules
(`/Users/mo/ORBStraddle` @71b001f, hash-checked, no keys) on ADT's exact board at ADT's decision time. Boards are
compared with the phase-1 replay of the original (`replay_original.json`), minus the symbols ADT had to leave out
(executed today / supervised). ORBStraddle's live behaviour comes from its `/api/research/day?day=` (fetched
read-only). Only 09-28 ran the pinned rules live (adaptive-v1.6.0-flow-rules); 09-22..09-25 ran
adaptive-v1.4.2-scan-recovery live, so for those days the reference is the original code, not live.

Runs (all in `research/orbs_parity_cache/dry_run_out/matrix/`, gitignored): per day `live` (as built),
`adapt` (live + the BUG 1 adapter), `shadow`, `adapt_eod` (a new process restores the 16:05 checkpoint); on 09-28
also 4 restart scenarios and a run with 80 ms relay latency. 26 processes, 0 run errors, 0 relay misses.

## Per-day results

Sizing base $49,702.10: first trade risk 2% = $994.04, day budget 2.5% = $1,242.55.

| Day | 09:38 verdict | Picks (ADT, time) | Entries (adapter run) | Exits | ORB P&L | Match vs ORBStraddle |
|---|---|---|---|---|---|---|
| 09-22 | pass (CALM_TREND) | 09:46 SOFI short, 09:47 SKHY long; MU, MRNA, WDC later picked but refused "max structure tier slots (2) occupied" | SOFI short 1,461 @ 17.385 (ref 17.44, stop 18.12, target 16.93, risk $993.48); SKHY long 28 @ 192.765 (stop 183.96, target 199.45, risk $247.77 = rest of the 2.5% budget) | SOFI absorption exit 10:30:13 @ 17.0301 (+0.51R); SKHY 11:00 flatten @ 193.66 | +$518.51, +$25.06 = **+$543.57** | **Yes vs original code**: 32/32 boards, 28/28 decisions identical. Live ran v1.4.2 but also picked SOFI 09:46 and SKHY 09:47 (it executed nothing that day). |
| 09-23 | pass | none all day | none | none | $0 | **Yes vs original**: 32/32, 26/26. Live (v1.4.2) traded HOOD and C; different rules, not comparable. |
| 09-24 | sit out, ONE_SIDED | none | none | none | $0 | **Yes vs original**: 32/32, 28/28. Live also sat out at 09:38 (ONE_SIDED); its later AMD/INTC trades were v1.4.2. |
| 09-25 | pass | none | none | none | $0 | **Yes vs original**: 32/32, 22/22. Live (v1.4.2) traded BMNR and NKE. |
| 09-28 | sit out, ONE_SIDED 83.7% short | 10:05 APP short | APP short 62 @ 311.4689 (ref 310.70, stop 326.64, target 298.75, rd 15.9397, risk $988.26) | 11:00 flatten, filled 11:00:03 @ 309.645 (+0.11R) | **+$113.08** | **Yes**: 32/32, 26/26 vs original; same 09:38 sit-out (0.837) and same APP short on the 10:05 board as ORBStraddle live. Differences are timing and size only, see below. |

As built (`live`, no adapter): same verdicts and picks, **zero orders on every day** (09-22: SOFI, SKHY, MU,
MRNA, WDC all dropped by BUG 1; 09-28: APP dropped). Because each dropped pick also counts as executed today
(ORBStraddle does the same, core.py:2400), the 09-22 as-built run then passes on boards where the adapter run
kept picking MU.

**09-28 vs ORBStraddle live, APP short.** Same board (10:05), same direction, same stop 326.64 (card stop), same
formulas. ORBStraddle's scans ran late, so it decided at 10:08:27 and priced at 313.03 (rd 13.612, target
302.82, 106 shares = 2% of ~$72.1k), filled 312.69, flattened 11:00:03.96 @ 309.77: +$309.52 (+0.21R). ADT
decided at 10:05:01, priced 310.70 (the 10:05:01 print; slip 0.1% of stop distance), filled 311.47 (next print),
flattened 11:00:03 @ 309.645: +$113.08 (+0.11R). Same exit path (no target, no stop, no software exit; 11:00
flatten). ADT's size is 62 shares because ADT's equity is smaller and its fresh price was 2.33 lower (wider rd).

**Entry math check** (every execution): entry_ref = the fresh latest-trade price rounded to 2 dp, stop =
round(card stop, 2), rd = |price - stop|, target = price -/+ 0.75 rd (2 dp), shares = floor(2% x 49,702.10 / rd),
then capped by the remaining day budget: all 3 entries match the independent recomputation, and the bracket
payload at the fake broker carries the same qty, limit and stop.

**Shadow**: every day, the same decisions and picks as the live runs (identical records), zero broker writes,
and "would have placed" rows with the same sizes (09-22 SOFI 1,461 / SKHY 28, 09-28 APP 62).

## Restarts (09-28, adapter)

Each restart is a new process on the same SQLite state and the same fake account.

| Scenario | Result |
|---|---|
| Hard kill at 09:38:45 (after the 9:38 decision) | 9:38 decision not re-run; board, pick, order, exit identical; ledger identical (+$113.08); one bracket POST |
| Hard kill at 10:30:03 (mid-trade) | ORB restored its APP position from its state + Alpaca, startup reconcile ok (0 unknown orders), 11:00 flatten; ledger identical |
| Graceful stop at 10:30:03 (lifespan drain + final checkpoint) | ledger identical |
| Kill inside the APP bracket POST (order reached Alpaca, reply lost, process gone) | restarted at the same second; reconcile ok; the fill was found by client id and booked at 10:05:07; no second bracket; the 10:05 decision was not re-run; exit at 11:00:02 @ 309.635 = +$113.70 (the restart moved the 5 s supervisor phase by 1 s; same exit rule) |

The restart times 10:30:03 were chosen on the supervisor's 5 s phase so an identical ledger is possible; any
other restart second shifts the 11:00 exit by up to 5 s (a different print).

## ADT side effects

- **Broker mismatch never latched** in any run (reconcile every 30 s through `_broker_reconcile_once`; ORB's fill
  lag is healed by the ORB refresh before the compare).
- **Other arms refuse ORB symbols**: while APP (09-28) / SOFI (09-22) were held, `pre_trade_risk_validator`
  refused vwap_pullback BUY, mean_reversion SELL and news_momentum BUY with `ORB_OWNED`.
- **Breaker math includes ORB P&L**: at 10:16 on 09-28 the account's unrealized -$93.07 (ORB's APP) is exactly the
  breaker's drawdown ($93.07 vs starting equity $49,702.10); ORB's open risk ($847.55) is reserved out of ADT's
  budget; equity = cash + marks at every check. Realized ORB P&L lands in the account and `/api/trades`
  (strategy_id orb, exit_reason, R multiple, fill legs).
- **15:55 flatten does not touch ORB**: zero broker writes after 11:01 on every day (ORB is flat by 11:00:03).
- **One sell order per position**: zero 403s; every exit cancelled both legs, then one market order for ORB's qty.
- **End-of-day checkpoint**: a new process at 16:06 restored the same account (equity, realized, flat), the same
  trades and a ready ORB on all 5 days.
- **Alpaca request budget**: at most 2,345 ORB requests in a morning (09-22, two positions; ~30/min while
  holding), never throttled (budget 90/min + 40 exit reserve).
- **Event loop**: ADT's own loop-side work is short: `_runtime_clock_step` max 27 ms, ORB fill sync max 13 ms,
  trade handler < 1 ms. But see RISK 1 (GIL).

Card and `/health.orb` at 09:37 / 09:39 / 10:00 / 10:16 / 11:01 / 16:00 are in each run's `result.json`. Card text
follows the day (running the 9:38 scan, sat out with the reason, managing the open trade with stop/target/R,
done). The card's hour-window block is evaluated on the wall clock in this harness (the endpoint takes no time
argument), so its `window.state` is not meaningful here.

## Bugs and risks found

**BUG 1 (P0): the macro veto answer is read backwards before every live order.**
- `backend/app/strategies/orbs/facade.py:350-354` `macro_veto()` returns `(vetoed, reason)`; its docstring says
  "vetoed=True means the trade must NOT be placed".
- `backend/app/core/orb_execution.py:1558-1564` `_late_macro()` unpacks it as `ok, why` and skips the order when
  `not ok`; `orb_execution.py:1465-1470` then drops the pick ("macro veto before the order: " + empty reason).
- `backend/tests/unit/orb_execution/fakes.py:327, 366-368` (FakeFacade) returns `(True, "")` for "no veto", the
  controller's reading, so all unit and fake-session tests pass.
- Effect: in ORB_MODE=live no pick that passes the macro veto is ever sent, and a pick the macro veto refuses IS
  sent. Shadow never calls `_late_macro`, so the shadow morning compare (plan decision 9) cannot catch it. Only
  trace: decisions log `ORB_REFUSED "macro veto: "`; card and `/health.orb` stay clean.
- Failing tests: `backend/tests/unit/orb_execution/test_dry_run_findings.py` (2 tests, call the real
  `OrbsFacade.macro_veto`; both fail today). Not fixed here.

**RISK 1 (P2, performance): ORB scans stall ADT's event loop through the GIL.** The scanner runs 24 pure-Python
worker threads (`scanner.py:868, 1138`). While a scan runs, the asyncio loop waits for the GIL: max 1.6-2.6 s
per day, ~1,100-1,400 loop wake-ups over 200 ms per morning, all during `preview`, `final` or `secondary_scan`
jobs (sampled stacks: page parsing in `scanner._iter_pages`, timestamp parsing in `ticks._ts_parts`). Caveats:
replay serves pages from memory, so scans are more CPU-dense than live; parallel runs on one machine inflate the
numbers (a single-process probe measured 0.44 s max); 80 ms relay latency per request only lowered the count
(17% vs ~30% of samples over 200 ms), not the max (2.1 s). Live impact to check: quote/bar handlers and the
tri-engine / OR15 timing can be delayed by seconds each minute 09:36-10:15. Options (not done): run the scanner
in a subprocess, or fewer scan threads.

**Note (P3): the card's step text shows the last secondary verdict** ("Last decision: sat out ...") while an ORB
trade is open (09-28 10:16, 09-22 10:00); the open trade is listed under it, but the operator does not see
which decision opened it.

Scan wall times in replay: first secondary scan (09:45, full rebuild) up to 156 s with 7 runs in parallel; later
scans a few seconds. The production deadline for a secondary scan is 240 s. The dry run does not apply deadlines
(zero-latency clock), so late-scan behaviour is not tested here.

## Limits of this dry run

- Fills are modelled from SIP prints (next regular-lot print; limit at the limit; stop at the next print after
  the trigger); Alpaca's real paper fills can differ by cents and timing. Partial fills not simulated.
- Zero compute latency: decisions happen 0-1 s after the scan minute; live ORBStraddle decided up to 3.5 min late
  on 09-28.
- Other ADT strategies stayed idle (no bars recorded for them); only their admission check was exercised.
- Replay bars/news for times the parity run never asked are synthesized from recorded data (validated above).

## Reproduce

```
python scripts/orb_dry_run/topup.py 2026-09-28 --bars --symbols APP          # once, read-only relay
python scripts/orb_dry_run/run_all.py research/orbs_parity_cache/dry_run_out/matrix --jobs 7
python scripts/orb_dry_run/analyze.py research/orbs_parity_cache/dry_run_out/matrix
```
