# TSLA OR15 production release verification

## Verified implementation release

- Repository: `origin/main`, commit `8a3013086b87b03cf81101f77adf57a383734c4c`.
- Railway project/service: AutonomousDayTrader, production.
- Deployment: `11c03878-5edc-4aa3-979b-4051ea341b58`, **SUCCESS** for that exact commit.
- Verified at 2026-09-25 20:47:57 UTC; raw selected observations: `DEPLOYMENT_EVIDENCE.json`.
- Dashboard: https://autonomousdaytrader-production.up.railway.app/

## Live acceptance checks

All returned HTTP 200: `/health`, `/api/tsla-or15`, `/api/strategies`, `/api/positions`, and `/api/trades?range=today&limit=100`.

- Health healthy; stock/news/VIX connected; persistence durable and required; restored schema 2 checkpoint successfully.
- Alpaca paper account PA3CSVDZMMPY matches the bot: equity/cash $49,702.10, no positions, zero equity drift, mismatch false.
- Startup log restored checkpoint revision 42296, zero positions and 26 historical trades; today's history retained 13 completed trades. No error/critical/traceback/recovery-halt line was found in the inspected 120 startup log lines.
- Five strategy ids present, including `tsla_or15_retest`. OR15 status ACTIVE, enabled true, shadow false, quantity 1, mode alpaca_paper, SIP verified.
- Live implementation hash `b926f187e2313b51e2ddff4dd38105cae096bb96b709aa9ec6425a25dddd6aa1` exactly matches the tested replay evidence. Source hash matches the frozen document.
- Deployment was after Friday's close. Today's new strategy session correctly records SKIPPED / MISSING_REQUIRED_BAR because it did not observe today's opening range. This is a session skip, not an activation delay. The next eligible full session is Monday 2026-09-28. No new OR15 scan or fill is claimed from this after-hours check.

## Production visual audit and cleanup

Refreshed the production page in the user's local Chrome using the installed browser-use CLI. The initial navigation reused the old page assets; a reload confirmed the new five-card frontend. Inspected screenshots at desktop 1440×1000 and mobile 390×844, including the Tesla card and retained trade history. Width equals viewport width in both layouts, labels fit, the one-share paper mode is visible, and no position controls appear while flat.

Screenshots retained in `.local_qa_screenshots/`: `or15-production-desktop.png`, `or15-production-mobile.png`, `or15-production-mobile-detail.png`. Existing pro-words preference was preserved. The task tab remains on the production dashboard. Local synthetic visual servers were terminated, and their ports were released. No user Chrome process or unrelated server was stopped.

This report and selected endpoint evidence are committed in a documentation follow-up. It changes no implementation files; the implementation hash and the 935-test verification remain applicable. The final main revision is checked again against Railway and live health after that push.

## 2026-09-25 evening: implementation hash changed by research recording

Commits 0e6fe3e..6a5ec34 added observation-only research hooks to shared files the OR15 fingerprint covers (`engine.py` fill listeners, `main.py`, `runtime_state.py`). OR15 rules, prices and order flow are unchanged. New implementation hash `e45284623485473b5b4cd233a18253194914c999d6a17d378536b1bf53220e8c`; the OR15 dry run was re-run on this code (6 cases PASS, all flat) and `DRY_RUN_EVIDENCE.json` regenerated. The hash is provenance only; nothing gates trading on it.

## 2026-09-25 evening: audit fix, clock-skew tolerance

Audit of 8a30130. Live measurement from inside the Railway container: relay bars arrive 0.05 to 0.14 s after their minute ends, TSLA quotes 0.05 s or more after their exchange stamp. The code rejected any bar that looked even 1 ms early (`INCOMPLETE_OR_STALE_BAR`, which skips the whole session) and any quote stamped ahead of the host clock (lost entry). A host clock drift above about 50 ms would have silently skipped every day. Now a 0.5 s skew is tolerated (`CLOCK_SKEW_SECONDS`); a bar 1 s early is still rejected. Trading rules unchanged. New implementation hash `999dbc0faa12fa1d1dfba1cc547c942df9c0461a1e35f40ee9b854cc221c6cf3`; OR15 dry run PASS (6 cases, all flat), 963 tests pass.
