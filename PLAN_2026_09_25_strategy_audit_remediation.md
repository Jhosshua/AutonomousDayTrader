# Strategy audit remediation plan — 2026-09-25

## Scope and evidence

Repair the defects found in the 2026-09-25 five-strategy audit while preserving the existing Alpaca paper account, checkpoint compatibility, live positions, and the documented entry/exit rules in `PROJECT.md` and `ORIGINAL_REQUEST.md`. A live signal, a simulated fill, and a production broker fill are distinct forms of evidence. The 11:01 ET read-only snapshot showed AMZN and AMD ORB shorts plus a SPY VWAP long; `/health` reported no broker position mismatch.

## Changes

1. **Intraday target and risk geometry.** The adapted stop already exists before admission. Size admission from that stop, with the existing risk check still governing actual quantity. For ORB and News, omit absolute target overrides: the bracket manager already recomputes its 0.8R/1.8R targets from the actual fill and adapted stop. For VWAP, reapply its documented 0.50R minimum and 0.8R/1.8R fallback to adapted risk; preserve standard-deviation targets when they qualify. For Mean Reversion, reject before submission if its structural 20-SMA target falls below 1.0R using adapted risk. A broker fill cannot be rejected after the fact: keep its stop, re-anchor R-based targets to the fill, and log any structural-target reward/risk erosion. Add a pre-order slippage buffer for structural targets without changing market orders to limit orders. Existing open brackets retain stored levels.
2. **VWAP trend warmup.** Require genuine 20/50 EMA coverage from regular-session bars before a VWAP entry; remove the EMA10/20 and price/VWAP fallbacks. The first possible entry becomes about 10:20 ET after a normal 09:30 start, and operator documentation must say so. Do not seed the trend from premarket or a prior session. Retain anchored VWAP from 09:30. Test both sides and the 49/50-bar boundary.
3. **News catalyst window.** Do not use premarket bars for the volume baseline and do not consume a catalyst or emit an entry before 09:30. Allow an eligible premarket headline to trigger on a qualifying regular-session bar within its 180-second TTL; otherwise expire normally. Match the strict `>2.0x` condition and correct the stale 3.5x source comment. Preserve contradiction exits for intraday positions of any strategy, as current tests explicitly require, while excluding Swing positions per arm isolation. Test 09:29/09:30, exact threshold, stale/future headlines, and contradiction behavior.
4. **Mean Reversion minimum reward/risk.** Enforce its 1.0 minimum with the adapted stop before sending an order. Preserve the 20-SMA structural target; do not move the stop inward to force approval. Verify regime gating still blocks countertrend signals.
5. **Swing open execution.** Save an optional linked local order id on the staged entry and reuse it for transient retries; never send a second Alpaca order until the linked one is definitively settled. Count working Swing entries against slots and avoid generic intraday bar/quote matching of Swing orders. Remove staging only after confirmed completion or an unambiguous permanent rejection. Route every direct, late, and partial Swing fill through one idempotent post-fill hook that applies ATR, entry date, and a stop anchored to the actual average fill. Preserve failed exits for retry. Retain broker-linked orders in pruning. Test with a mocked broker, including accepted-unfilled, timeout, late fill, partial fill plus late remainder, retry, and restart.
6. **Swing scan visibility.** Persist a bounded last-scan summary including session date, wall-clock scan time and last-close-data note in a new optional checkpoint key so `last_scan_time` survives restart without confusing catch-up scans with close scans. Keep older checkpoints restorable. Verify the candidate list and scan record are distinct and an actual close scan updates the record.
7. **Documentation cleanup.** Update `PROJECT.md`, `MEMORY.md`, `ERRORS.md`, and `README.md` with observed defects, fixes, QA results, and deployment identity. Identify the stale Markdown file from content/references or the user's answer; remove it only after migrating any unique needed facts and fixing links. Preserve pre-existing unrelated worktree edits unless they are explicitly incorporated and reviewed.

## Review and QA gates

- Claude Code CLI completed a read-only critique on 2026-09-25 (saved at `/Users/mo/.claude/plans/critique-plan-2026-09-25-strategy-audit-immutable-hare.md`). This revision incorporates the 4 blocking, 5 assumption, and 5 failure-mode findings. No capped-limit entry change is planned because it changes the order specification.
- Add focused regression tests for each defect and broker failure mode, including buy/sell, low/normal/high VIX, premarket-to-open transition, and old checkpoint restore. Run the relevant focused suites, then the full backend and E2E suites and frontend build if touched. Keep simulations on the local broker stub; do not submit QA trades to the live paper account.
- Inspect git diff, make sure no secrets enter git, and distinguish current live telemetry from synthetic tests. Deploy only after the 15:55 ET intraday flatten is complete and the book is verified flat, or before a future open. Local stops cannot protect positions during a Railway restart. Capture predeploy positions, stop prices, staged/working orders, and broker health; compare after deploy. Do not alter an existing bracket. Halt rollout or roll back if state or protection changes unexpectedly.
- Commit reviewed changes on `main`, push `origin main`, wait for Railway to deploy the exact commit, inspect build/runtime logs, and verify the remote `/health`, strategy, decision, position, and swing endpoints. Check local process and port hygiene before reporting completion.

## Claude Code critique and responses

Claude Code ran in read-only plan mode with first-party account authentication. Its first review identified four blocking safety issues: restart exposure while stops are local, duplicate Swing buys on retry, missing ATR metadata after late fills, and the impossibility of rejecting an already-filled broker trade. It also identified the VWAP fallback rule, premarket volume contamination, cross-arm News exits, checkpoint compatibility, scan dates, and broker-linked order pruning. The revised plan above addresses these findings. No marketable-limit order change was made; entries remain the specified market orders.

A second read-only implementation review found no P0 issues and two P1 issues. The VWAP strategy now marks R-based fallback targets so the bracket can rebuild them from the adapted stop and actual fill. At the Swing open-window cutoff, AMD and other reserved symbols remain locked until all possibly live Alpaca entry orders settle. A later local review also found that the 16:00 scan must count those unresolved orders against both Swing slots; that is now enforced. Its remaining QA requests were added to the tests: both VWAP directions at the 49/50-bar boundary, low/normal/elevated/crisis stop geometry, legacy checkpoint fields, and an unresolved Swing order crossing 09:45.

A final local review caught an order-pruning edge case: with no retention slots left after preserving broker-linked orders, slicing at `-0` kept every completed order. Completed orders are now dropped in that case, with a regression assertion in the broker test.

## Predeployment verification (2026-09-25)

- `pytest -q`: 899 passed.
- Opaque-box E2E runner: 321 passed; audited ports 8080, 8005, 8000, and 3005 free.
- `frontend/npm run build`: passed, including type checking and static export.
- Integrated production-path Monday replay: PASS, 184 events, zero event-bus errors. At its 10:30 ET endpoint a Mean Reversion runner remains open with an active stop and target. The replay now verifies exact open-share protection and the absence of orphan working orders instead of falsely claiming the position was flat.
- Six-day concurrent Swing replay: PASS, $53,056.11 final equity, zero open positions, clean ports.
- The replays used the built-in simulator and a local mock relay, not the Alpaca paper account. Mock server shut down.

## Remaining limits

No code-only QA can prove the five strategies have positive trading expectancy or that a rare News or Swing setup will occur today. Production behavior after deployment can verify wiring and state preservation; a later qualifying market session is required to observe a real Swing entry and a new News entry.
