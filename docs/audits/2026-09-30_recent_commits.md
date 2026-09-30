# Recent-commit audit — 2026-09-30

Audited the production changes pulled from `cd67aec` through `4dd902a`, concentrating on overnight order execution, account booking, restart recovery, risk/flattening exemptions, and dashboard delivery. Work was done in an isolated checkout; the original checkout's unrelated research and report edits were preserved.

## Findings and fixes

1. **P1 — restored buy intents ignored disabled settings.** An intent saved before sending could submit a new closing buy after restarting with `OVERNIGHT_MODE=off` or its stock disabled. Pending attempts now check those settings before sending while still looking up existing broker orders. Regression tests cover both settings.
2. **P1 — a slow lookup could outlive permission to buy.** Workers captured permission before a potentially slow broker lookup. A lookup returning after 15:49:30, after the no-buy control, or after mode was disabled could still submit a buy. Workers now recheck the actual clock and current controls immediately before a new POST. Existing accepted orders remain discoverable and managed. Three regression cases failed before the fix and pass afterward.
3. **P1 — split state changed before ledger acceptance.** The controller changed its held shares and cost basis before calling the split booking hook, ignored a refusal, and left the two books inconsistent if the hook raised. It now books the split first. A refusal or exception leaves the original controller quantities intact, cancels the stale queued sale, retains the reservation, and raises `BOOKING_REFUSED`. The split hook also requires the exact long position and strategy. Tests cover refusal, exception, successful replacement, and the real runtime's refusal path.
4. **P2 — trade history overstated or mismatched quantities.** The closed row used original buy shares and prices even after splits, symbol changes, or a sale of fewer shares than ADT's book. Rows now use the shares actually sold and the controller's adjusted entry price. Regression checks cover 550 split-adjusted NVDA shares at $18, 124 converted IRNX shares at $80, and a 40-share NVDA sale with 15 shares still disputed in the book. Raw fill legs and corporate-action metadata remain available.
5. **Dependency audit — vulnerable nested PostCSS.** Updated Next.js within version 15 to 15.5.27 and required PostCSS 8.5.28 throughout the dependency tree. `npm audit` reports zero vulnerabilities. The override prevents Next's older nested PostCSS from remaining installed. Relevant maintainer advisories: [source map disclosure](https://github.com/postcss/postcss/security/advisories/GHSA-fxqj-rqcc-2cmp) and [CSS output escaping](https://github.com/postcss/postcss/security/advisories/GHSA-qx2v-qp2m-jg93).

Two test portability problems were corrected: the subscription test now runs with an event loop, and the tape timestamp test pins its wall clock rather than relying on a fixed date still being in the future. The initial run used the host's Python 3.9; the full final backend run used Python 3.12.13, matching the production Docker major/minor.

## Validation

- Full `backend/tests`: **1,423 passed, 1 skipped** on Python 3.12.
- New execution regressions: all seven reproduced failures before fixing the code; a real-runtime split history regression also reproduced the old wrong quantity.
- Overnight replay using the original checkout's read-only historical SIP bars and a fake Alpaca: **17/17 checks passed**. Real network order submission was blocked by the harness. Report: `docs/overnight_holds/DRY_RUN_REPORT.md`.
- Frontend architecture/resilience tests, TypeScript checking, static production build, and zero-vulnerability dependency audit passed.
- Overnight desktop/phone fixture QA: **584 passed, 0 failed**.
- Authenticated **GET-only** production-credential probes: `/v2/assets/{NVDA,IREN,HUT}` each returned HTTP 200, marginable true, `margin_requirement_long="30"`; `/v2/corporate_actions/announcements` returned HTTP 200 with an empty NVDA result for September 29–30. No secrets were printed or saved, and no real orders were sent.

## Limits

The corporate-action probe verifies authentication and the endpoint/query, not the interpretation of a nonempty live announcement. Closing/opening auction acceptance and actual fills still require a real market session; the replay is wiring evidence. A refused split now requires an operator to reconcile the book, consistent with the existing unclear-symbol-change behavior; there is no automatic repair tool. Strategy profitability was not revalidated by this operational audit.
