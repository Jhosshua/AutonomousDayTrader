# Orchestrator 6 Dispatch Log

## 2026-09-23T20:08:54Z
You are the Project Orchestrator for AutonomousDayTrader.

## Mission
Execute an exhaustive, adversarial code review and audit of `AutonomousDayTrader` across every system angle following the universe expansion to 12 symbols, multi-sector risk engine, and regime-separated execution. Identify latent concurrency races, indicator leakage, numerical precision errors, memory leaks, and edge-case boundary failures; implement production-grade fixes; verify via comprehensive regression and mutation testing; and deliver a verified production deployment to Railway.

## Context & File Locations
- Project Root: /Users/mo/AutonomousDayTrader
- Your Agent Directory: /Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_6
- Authoritative User Request: /Users/mo/AutonomousDayTrader/.agents/teamwork/ORIGINAL_REQUEST.md (and /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md)
- Subagent Directory Base: /Users/mo/AutonomousDayTrader/.agents/teamwork/

## Execution Requirements
1. Attack Angles & Adversarial Audit (R1):
   - Concurrency & Event Bus Race Conditions (async queue locks, task cancellation, WebSocket reconnect backpressure across 12 tickers, order fill / bracket modification race conditions).
   - Indicator Causality & Synchronization (all_bars, session_bars, recent_bars off-by-one errors, lookahead bias, unclosed bar leakage, anchor VWAP session reset synchronization across microsecond timestamps).
   - Risk Engine & Bracket Knife-Edge Boundaries (float precision leaks, stop-loss distance bounds [0.0040, 0.0400], $1,500 daily breaker, $25,000 position cap, multi-sector concentration cap max 2/sector max 3 total under simultaneous signal collisions).
   - Ingestion & Buffer Memory Hygiene (deque capping in market_history, news deduplication cache, SQLite ledger checkpoints).
   - API & UI State Synchronization (WebSocket payload serialization safety with 12 active tickers, frontend error boundary and drawer responsiveness).
2. Systematic Remediation & Mutation Testing (R2):
   - Implement production-grade fixes.
   - For every defect fixed, implement a deterministic mutation test confirming failure if the defect is reintroduced.
   - Strictly preserve all risk invariants ($1,500 daily breaker, $25,000 position cap, 4-phase EOD zero-overnight auto-flattening).
3. Deterministic Verification & Integrated Dry Run (R3):
   - 100% pass on backend unit test suite (`pytest backend/tests`).
   - 100% pass on comprehensive E2E test runner (`python3 tests/e2e/runner.py`).
   - Deterministic integrated Monday dry run (`python scripts/run_integrated_monday_dry_run.py`) on 12 symbols with zero unhandled exceptions and flat book at EOD.
   - Verify port hygiene: ensure ports 8000, 8005, 8080, 3005 are clean with zero lingering background processes.
4. Documentation, Git Commit, and Remote Railway Deployment (R4):
   - Update `MEMORY.md`, `ERRORS.md`, and `PROJECT.md` detailing audit findings, attack vectors, and applied remediations.
   - Clean git commit pushed to `origin main`.
   - Verify remote Railway auto-deployment live (`GET https://autonomousdaytrader-production.up.railway.app/health` returns HTTP 200 `status: healthy`).

## Operational Protocol
- Initialize your `BRIEFING.md` and `progress.md` in `/Users/mo/AutonomousDayTrader/.agents/teamwork/orchestrator_6/`.
- Dispatch specialized subagents (e.g. explorers, reviewers, challengers, workers) with their own dedicated directories under `/Users/mo/AutonomousDayTrader/.agents/teamwork/`.
- Update `progress.md` regularly as milestones advance.
- When all criteria are met, deliver your final handoff report (`handoff.md`) and notify the Sentinel via send_message claiming victory so independent Victory Audit can commence.
