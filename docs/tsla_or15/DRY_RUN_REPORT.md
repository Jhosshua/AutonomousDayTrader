# TSLA OR15 verification report — 2026-09-25

## Scope and execution contract

Implemented the frozen source as the fifth strategy in the existing bot, with immediate one-share Alpaca paper routing and no shadow activation stage. The user explicitly overrode the source's shadow-only direction. Actual paper fills use Alpaca prices/times and native OCO protection. The source's exact T+2 raw-open, stop-first ambiguous OHLC and terminal-open conventions are tested separately in offline replay. Neither synthetic tests nor after-hours health establish an actual OR15 market-session fill or profitability.

Source SHA-256: `1ed5091248fcaf1b66004eda2a8c21ed5114c23dbe9a590370c7a30e596ee5cd`.

Implementation SHA-256: `b926f187e2313b51e2ddff4dd38105cae096bb96b709aa9ec6425a25dddd6aa1`.

## Reproducible evidence

- `python3 scripts/run_tsla_or15_dry_run.py`: six scenarios pass through the real bar handler, strategy, account risk, order/bracket engine, ledger and WebSocket serializer. Every scenario ends with zero positions and zero working orders. Detailed event/trade evidence is in `DRY_RUN_EVIDENCE.json`.
- `test_or15_broker_lifecycle.py` uses the real AlpacaBroker HTTP adapter with deterministic HTTP responses and actual temporary SQLite storage. Every POST checks its saved predecessor identity. The full production-ingress case processes 36 completed symbol bars, then a fresh quote, entry, native OCO, target fill and a persisted completed trade. Calendar/wall time is explicitly injected; it does not contact Alpaca.
- `test_tsla_or15.py` verifies source timing, reverse symbol arrival, frozen ATR, duplicate/missing/stale/unfinished/invalid input, no feed, consumed-signal expiry, no premature entry, exact exit conventions, zero-volume early QQQ, final 11:30 signal, early close and cancellation of a staged signal.

| Replay | Entry | Exit | Reason |
| --- | ---: | ---: | --- |
| Target touch | 102 | 108 | TARGET |
| Stop touch | 102 | 99 | STOP |
| Both stop and target in one bar | 102 | 99 | STOP first |
| Gap below stop | 102 | 97 | STOP at worse open |
| 120-minute terminal bar also touches both | 102 | 104 | TIME_LIMIT at open |
| Nov 27 early close; last 11:30 signal | 102 | 104 | FORCED_FLAT at 12:55 open |

Broker/recovery cases include actual fill differing from the model, passive native protection, cancel/fill races, unresolved cancellation, entry/OCO rejection, nonpositive actual risk, ownership/manual-stop controls, held restart, OCO crash before POST, failed storage immediately after BUY, failed storage before emergency exit, canceled/rejected emergency retries and restart without duplicate sells. Independent reviewer probes also cover a timed-out POST that creates no order. Native accepted response prices, requested cent levels and theoretical prices are distinct. Five-decimal entry/stop precision survives SQLite restore.

## Reviews and fixes

Plan critique was completed before implementation. Independent execution and source/UI diff reviewers reproduced defects and re-reviewed the corrections. Their current verdicts report no remaining blocking findings in their scopes. Reports: `PLAN_CRITIQUE.md`, `REVIEW_EXECUTION.md`, `REVIEW_FIDELITY_UI.md`.

Resolved defects include generic OCO cancellation, protection POST crash recovery, storage-down exit identity recovery, missed staged Close All, zero-fill abort cleanup, stale time before POST, malformed replay fill bars, zero-volume initial QQQ handling, contradictory checkpoint states and fixed-price rounding. Final labeling uses OFFLINE_TEST for synthetic sessions and null broker fees; modeled fees have their own field.

## Visual audit

Route actually tested: installed local `browser-use` CLI against the user's existing Chrome. Doctor confirmed one active browser connection after the user enabled remote debugging. Desktop 1440×1000 and phone 390×844 used the current built frontend with captured real runtime WebSocket payloads. Idle and one-share holding screenshots were visually inspected. All five cards fit; document width equals viewport width; fixed stop/target/deadline are readable; breakeven control is disabled; sell control remains available. Shortened the managing badge, clarified offline replay text, and removed a repeated waiting phrase found during inspection.

Screenshots are retained locally under `.local_qa_screenshots/or15-{desktop,mobile}-{idle,holding}.png` plus `or15-mobile-detail.png`. These controlled snapshots are synthetic UI evidence. Production screenshots and remote release evidence are recorded in `DEPLOYMENT.md` after release.

## Regression and cleanup

Final full-suite result and release observations are appended below. The earlier separate backend run passed 613 tests; existing E2E runner passed 321; a further production-ingress test was added afterward. Frontend architecture checks, five stream-resilience checks, type checking and production build pass. One baseline news test used wall-clock session time and failed after hours; its clock is now a fixed regular-session instant.

An initial combined suite overlapped the visual server on port 8005 and correctly failed port-isolation/loading-skeleton checks. The server was stopped and the full suite rerun with clear ports. This was test orchestration interference, not a passing result. Both task-created visual server processes were terminated; ports 8000/8005/8017/8080/3005 were confirmed free before the final isolated run.

## Limits and first eligible session

Paper routing is active as soon as the deployed service is ready, subject to verified SIP, healthy durable state and existing account controls. Deployment is after Friday's market close; the next eligible session is Monday 2026-09-28. A missing opening minute causes a skip, not a late reconstructed entry. Commissioning precedes the formal 2026-10-01 observation window. The source's four quarters/minimum 100 trades and statistical gates cannot be met today, remain NOT_EVALUATED, and a broker-paper trial does not validate the source's shadow trial. Broker fees are not supplied by order responses and remain unknown. Existing calendar coverage ends in 2027.

## Final isolated result

`python3 -m pytest -q --junitxml=/tmp/or15-final-results.xml`: **935 passed in 31.81 seconds**, zero failures/errors. This includes 614 backend tests and 321 existing E2E tests. The 36 dedicated OR15 cases are included in that total. Frontend checks and final production build passed. The final six-case replay has matching implementation hash, all session events labeled OFFLINE_TEST and broker_fees null.
