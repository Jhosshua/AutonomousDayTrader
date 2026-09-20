# MEMORY.md — AutonomousDayTrader

## Decisions

### 2026-09-20: Full audit & hardening cycle
- **Canonical VIX regime map is 15/25/35 with sizing 1.20/1.00/0.70/0.35.** Why: the adaptation engine and enum docstring used it, and boundary tests expected it. Rejected: vix_client's divergent 15/22/30 (0.60/0.25) map — three sources of truth for the same regime logic caused silent sizing divergence at VIX 22-25.
- **Duplicate entry signals are rejected while a symbol has a working entry order or live bracket (PENDING_ENTRY/ACTIVE/TARGET_1_HIT).** Why: overwriting `symbol_to_bracket` orphaned brackets and left unprotected stop orders after flatten. Rejected: allowing overwrite (old behavior) — it leaked brackets.
- **1x feed replay is true wall-clock; the 3s inter-event cap applies only above 1x.** Why: M5 "live-speed" dry runs were actually running ~20x fast.
- **Session state resets on ET date change** (risk, flattening, account, brackets, all strategy daily state). Why: live mode had no daily reset, so day-2+ trading was silently impossible.
- **`max_position_equity_pct` is now 1.0** ($50k notional cap from config), replacing risk.py's hardcoded 0.5 default. Why: env config was dead code; wiring it through changed the effective default. Intentional, flagged.
- **STOP_LIMIT is rejected (HTTP 400) at the order API.** Why: the execution engine has no stop-limit trigger branch, so such orders hung forever. Rejected: implementing trigger-then-limit matching — not worth the risk surface for a paper bot.
- **TIGHTEN_STOP applies only through bracket modify directives.** Why: the old fallback rewrote every stop order unconditionally and could loosen protection below entry.

## Session log

### 2026-09-20
- **Worked on**: Full independent audit of the entire codebase (3 audit agents), fixing ~45 findings (2 CRITICAL, ~13 MAJOR), independent diff review (2 reviewers), follow-up fixes, full QA, deploy prep.
- **Completed**: All audit findings fixed and re-verified; QA green (140 backend, 293 E2E, Monday dry run re-certified, frontend build clean); notes updated (PROJECT.md audit history, contract doc drift corrected); committed (1071b10, 5deff66), pushed to origin main, and deployed to Railway (deployment 7ae3c12a SUCCESS, production /health verified with new build hash).
- **In progress**: Nothing.
- **Next session priorities**: Observe first live Monday session behavior with the new session-boundary reset.

### 2026-09-20 (update): GitHub auto-deploy restored
- Railway service `AutonomousDayTrader` reconnected to repo `Jhosshua/AutonomousDayTrader` branch `main` via `railway service source connect` — pushes to `main` now auto-deploy. Verified end to end: docs push a41caa6 triggered deployment 973b7d25 automatically, SUCCESS, /health healthy. `railway up` is no longer needed.
