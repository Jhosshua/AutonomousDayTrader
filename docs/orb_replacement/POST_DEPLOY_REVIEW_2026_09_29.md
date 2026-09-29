# ORB post-deployment review — 2026-09-29 UTC

Reviewed the ORB rewrite merged in `edd5fbb` and the dashboard label change in
`3ed4ea0`. The deployed paper account was flat, matched Alpaca, and had ORB in
`shadow` (watch-only) mode when the review began. Unrelated research changes in
the working tree were excluded from this review and commit.

## Confirmed defects and fixes

Each defect was reproduced with a regression test before its fix.

| Defect | Consequence | Fix |
| --- | --- | --- |
| Entry checks used the initial clock value | A slow macro read, durable save, or request-budget wait could send a bracket after the entry cutoff | Check the current session and cutoff again immediately before the broker call; retain ORBStraddle's inclusive 10:15:00 boundary |
| ADT's entry gate ran before slow entry work | A new broker mismatch or persistence halt could be ignored by the pending entry | Repeat the gate after the macro read and after persistence/budget waits |
| The replacement controller ignored the ORB strategy's saved pause/cooldown | ORB could submit while the dashboard strategy was inactive | Require the ORB strategy to be ACTIVE for new broker entries; keep existing-position exits available |
| Quarantined malformed state was ignored on the next restart | A second restart could clear the entry block without resolving corrupt rows | Keep ORB unready while any quarantined rows remain |
| A timed-out decision-process call left the process marked healthy | ADT would never restart a hung decision worker | Mark the matching process connection down on timeout, fail pending calls, and allow the existing restart path to replace it; synchronize reply/timeout handling |
| Flatten-all waited on an admission lock held during an entry POST | A slow broker request could freeze ADT's event loop, delaying every strategy | Publish exit requests and the entry block immediately; leave cancellation/close serialization to the supervisor's symbol lock |
| Exit requests synchronously saved SQLite state on ADT's event loop | Slow storage could freeze the runtime during flatten/breaker handling | Queue only exit-command saves on a dedicated writer; flush at shutdown, block entries on save failure, and retain synchronous durable persistence before every broker POST |
| Fixed-plan session summaries included raw datetime objects in closed trade tranches | Session rollover failed every checkpoint retry and locked out new entries across ADT; production reported `recovery_halt` during this review | Normalize the immutable session summary to JSON-safe values before it is queued, preserving timestamps as ISO strings |

The final defect predates the ORB rewrite. Railway's live traceback identified
session-summary serialization as the failing path; the new test reproduces the
same failure with a closed TRI trade at session rollover.

The decision modules, 2% first-trade risk, 2.5% daily risk budget, TSLA/CDE
exclusions, and watch-only configuration are unchanged. This does not change the
previously accepted retry behavior when Alpaca refuses a close.

## Verification

Regression coverage includes cutoff/gate changes during macro, persistence, and
budget waits; pause/cooldown; repeated corrupt-state restart; a real hung child
process and restart; entry/flatten concurrency; slow storage; persistence failure
and recovery; and writer shutdown. All broker and historical execution tests use
fake broker transports. Historical replays use recorded data and prohibit real
network requests.

- Backend: 1,209 tests passed, one skipped. Six port-hygiene tests initially
  encountered another local server on port 3005; all six passed on rerun after
  that process exited. No unrelated process was stopped by this review.
- End-to-end: 309 tests passed after the final checkpoint fix.
- Frontend: `npm test` and `npx tsc --noEmit` passed.
- After checking the pinned original `core.py @71b001f`, preserved its exact
  inclusive cutoff (`now.time() > 10:15:00` refuses an entry). The final ORB suite
  plus the rollover regression passed: 360 tests, one skipped.
- Historical replay: all 23 segments completed, covering five recorded days in
  live simulation and shadow, five end-of-day restores, and four interruption/
  restart scenarios. Each mode matched 160/160 boards and 130/130 decision calls
  against the pinned original code. No duplicate broker orders, broker mismatch,
  application errors, missing recorded inputs, or shadow broker writes were found.
  Every completed day/restart ended flat. Interrupted orchestration attempts were
  restarted with fresh test state and excluded from these counts.
- Sept 28: the normal run and all four restart cases retained one APP parent
  bracket, 62 shares, short at 10:05 with stop 326.64. The submission interruption
  occurred at 10:05:02; startup recovered the existing order and fill.
- Maximum measured scan-window event-loop lag was 53.9 ms during the parallel
  replay; no scan-window sample exceeded 100 ms. This run does not reproduce the
  earlier report's specific 13.7 ms maximum.

Raw results, comparison summaries, and `verification.json` are in
`/tmp/adt-orb-post-review-20260929`.

One informational risk-math diagnostic was false in the accelerated graceful
restart replay: marked drawdown was $31.36 while the cached breaker value was
$22.08. Account equity and the 2.5% limit were correct. This is consistent with
the harness advancing its market clock faster than the existing five-second
wall-clock breaker throttle and sampling a newer price before the deferred
evaluation runs. The existing tests
for evaluating the newest mark and executing an owed breaker check after the
throttle expires both passed again. No risk-throttle change was made.

## Deployment

Fix commit `92e28608b8bb30a70799db51e8f2b2c06b169275` was pushed to `origin/main`.
Railway deployment `aabe0a4a-9a23-4908-93ce-a0a87af5780d` reached `SUCCESS`.
The production API at
`https://autonomousdaytrader-production.up.railway.app` reported healthy at
04:06 UTC, durable schema-3 persistence with no error, zero broker mismatch,
no positions or new orders, and ORB ready in configured/effective `shadow` mode.
The decision process was running and TSLA/CDE remained excluded.

The cutoff compatibility correction accompanies this report in the final
follow-up commit.

This is an after-hours deployment/health check, not evidence of a market-session
scan or a real fill. Historical executions use a fake broker.

Local Chrome visual QA was attempted using the installed `browser-use` CLI after
checking both CLI doctors. The connection timed out waiting for Chrome debugging
consent; `browser-harness mac-approve` found no pending Allow prompt. No screenshot
was obtained. Existing Chrome tabs, permissions, and plugin files were preserved.
