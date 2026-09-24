# Orchestrator 8 Dispatch Briefing

## 2026-09-23T23:59:13Z
You are the Project Orchestrator for AutonomousDayTrader.

## Mission
Perform a deep forensic audit of the entire "2-Day Panic Dip" swing trading engine and intraday day-trading integration within `AutonomousDayTrader` to uncover and fix all LLM shortcuts, edge cases, timing vulnerabilities, and mock dependencies. Deploy an engineering and verification team to remediate all findings, run an exhaustive multi-day end-to-end dry run testing both arms concurrently, verify the UI, synchronize all markdown documentation, and deploy to Railway.

## Context & File Locations
- Project Root: /Users/mo/AutonomousDayTrader
- Your Agent Directory: /Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_8
- Authoritative User Request: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md (and /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md)
- Subagent Directory Base: /Users/mo/AutonomousDayTrader/.agents/teamwork/

## Requirements & Scope

### R1. Deep Forensic Codebase & Architecture Audit
Conduct a deep, unsparing forensic audit across the codebase:
1. **Timing & Market-Open Vulnerabilities**: Inspect 09:30 ET open execution in `backend/app/main.py` and `swing_panic_dip.py` (e.g. what happens if the 09:30:00 bar is delayed, illiquid, or arrives at 09:31; ensure staged orders do not hang indefinitely).
2. **Staged Order Idempotency**: Verify `evaluate_market_close` cannot double-stage entries or exceed the 2-position cap on repeated scans or server restarts between 16:00 and 09:30 ET.
3. **Session Rollover & State Integrity**: Verify `reset_for_new_session()` and `_handle_session_rollover()` cannot wipe staged swing orders, prematurely increment `holding_days`, or release mutual exclusion reservations (e.g. for `AMD`) while active swing holdings or staged orders exist.
4. **Blocking I/O in Async Coroutines**: Audit `backend/app/strategies/earnings_calendar.py` and other services to eliminate synchronous blocking calls (such as `urllib.request.urlopen`) inside async event loops.
5. **Persistence Round-Trip Fidelity**: Verify all swing position attributes (`entry_date`, `entry_atr`, `stop_loss_price`, `holding_days`, `arm`, `strategy_id`) survive SQLite checkpoint encoding, restarts, and restoration without loss or schema degradation.

### R2. Production Remediation & Hardening
Remediate every flaw identified during the audit:
- Guarantee robust open-window order execution (e.g. execution window tolerance or first-available morning print) so staged orders never get marooned.
- Ensure strict idempotency on order staging and state checkpoints.
- Ensure non-blocking asynchronous HTTP fetching for external calendar/market services with durable local cached fallback.
- Extend unit and integration regression test suites to prove that every fixed vulnerability is covered.

### R3. Exhaustive Multi-Day End-to-End Dry Run
Deploy a dedicated simulation team to execute a full-scale end-to-end dry run:
- Simulate multiple consecutive trading days with both the Intraday Day Trading arm (ORB, VWAP, News, Mean Reversion) and the Swing Trading arm (`LRCX`, `KLAC`, `MU`, `AMD`, `GS`) executing simultaneously from the shared $50,000 account pool.
- Verify real-world order lifecycles: 16:00 close signal qualification, 09:30 open fills with realistic slippage, ATR stop protection, 15:45–15:58 ET intraday auto-flattening exemption for swing positions, and all 3 swing exit triggers (5 SMA, RSI > 70, 5-day time stop).
- Generate a comprehensive dry-run report (`SWING_FULL_E2E_DRY_RUN_REPORT.md`) logging all transactions, equity curves, drawdown, and arm isolation verification.

### R4. Operator UI Visual QA & WebSocket Resilience
- Verify the Next.js Obsidian dark UI across desktop and mobile viewports.
- Validate that the Segmented Mode Toggle, Swing Candidate Watchlist, Active Swing Positions Table, and manual controls update seamlessly over real-time WebSockets without UI exceptions or layout overflows.

### R5. Cloud Deployment & Process Hygiene
- Update project documentation (`PROJECT.md`, `MEMORY.md`, `README.md`).
- Terminate and verify liberation of all local test processes, ports, and background servers.
- Commit all changes and push to `origin main`.
- Verify live remote Railway build and cloud deployment health endpoints (`/health` and `/api/swing/state`).

## Acceptance Criteria
- [ ] Zero unhandled blocking I/O calls inside asyncio loops.
- [ ] Staged swing orders execute reliably even if initial 09:30 open prints arrive with jitter.
- [ ] Staging logic is strictly idempotent across multiple scans, clock ticks, and server restarts.
- [ ] Mutual exclusion for shared symbols (e.g. `AMD`) remains locked across overnight session boundaries until swing positions are fully exited.
- [ ] Complete round-trip SQLite persistence verified for all swing position fields (`entry_atr`, `entry_date`, `holding_days`, `stop_loss_price`).
- [ ] Multi-day concurrent simulation passes with 100% test success across both trading arms.
- [ ] 0 intraday positions held overnight; 0 swing positions liquidated by 15:58 ET intraday flattening.
- [ ] All 430+ backend tests and E2E suites pass with zero regressions.
- [ ] Mobile (390px) and Desktop (1440px) visual QA certified clean with 0px horizontal overflow.
- [ ] Remote Railway deployment live, healthy (`200 OK`), and returning valid telemetry.
- [ ] All local ports (`3005`, `8000`, `8005`, `8080`) verified free.
- [ ] Documentation updated with complete audit and dry-run evidence.

## Operational Protocol
- Initialize your `BRIEFING.md` and `progress.md` in `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_8/`.
- Deploy specialized subagents (explorers/auditors, workers, reviewers/challengers) with unique working directories under `/Users/mo/AutonomousDayTrader/.agents/teamwork/`.
- Maintain active `progress.md` updates as milestones advance.
- When finished, ensure all tests pass, visual QA is verified, code is committed and pushed to `origin main`, Railway is deployed and healthy, local processes/ports are terminated and freed, and provide a final handoff report (`handoff.md`).
- Then notify the Sentinel via `send_message` claiming victory so independent Victory Audit can commence.
