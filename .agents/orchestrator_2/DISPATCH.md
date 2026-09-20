## 2026-09-20T13:15:15Z
You are the Project Orchestrator for AutonomousDayTrader.

Your working directory is: /Users/mo/AutonomousDayTrader/.agents/orchestrator_2
Project root / workspace directory: /Users/mo/AutonomousDayTrader

Authoritative user request: See /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md (specifically the latest section "## 2026-09-20T13:14:36Z").
Reference materials:
- /Users/mo/AutonomousDayTrader/PROJECT.md
- /Users/mo/AutonomousDayTrader/MEMORY.md
- /Users/mo/AutonomousDayTrader/TEST_INFRA.md

Requirements:
1. R1. Comprehensive Architectural Audit & Bug Remediation: Conduct a rigorous audit across the entire codebase (backend core, ingestion adapters, order book, risk engine, state machines, the 4 trading strategies, and WebSocket streaming). Identify any missed connections, unhandled states, inverted risk boundaries, orphaned bracket orders, or dead code paths. Fix all identified defects while preserving intended specifications in PROJECT.md.
2. R2. De-themification of Music & Playlist Terminology: Completely remove all music, playlist, and album metaphors across the codebase, frontend components, state models, docs, and test suites. Replace them with professional trading terminology:
   - Replace "Curated Playlists" / "Playlists / Albums" with "Trading Strategies".
   - Replace "Now Playing" bottom tray with "Active Position" (or "Live Execution").
   - Clean up any remaining music-inspired labels (e.g., "album art", "track", "playlist") in comments, tests, and component strings.
   - Grep verification must confirm zero occurrences of "playlist", "curated playlist", "album", or music analogies in user-facing UI labels, frontend components, or active trade drawers.
3. R3. Multi-Agent Adversarial Diff Review & Full QA Cycle: Dispatch subagents to perform an adversarial review of all code diffs to ensure no unintended behavior, contract breakage, or regressions were introduced. Execute the complete backend test suite and full E2E testing suite (pytest tests/ and scripts/run_e2e_tests.sh) to achieve 100% pass rate.
4. R4. Deterministic Market Open Dry-Run Simulation: Execute a complete deterministic Monday market open simulation dry run through the actual production ingestion and execution paths as if trading live. Verify signal ingestion, bracket management, PnL tracking, risk circuit breakers, flattening routines, and UI WebSocket serialization with clean logs and zero unhandled exceptions.
5. R5. Mobile & Desktop Visual UI Audit: Perform a visual UI inspection across both mobile (390x844) and desktop (1440x900) viewports. Verify that all components, strategy cards, active position trays, charts, risk badges, and manual intervention controls render cleanly without truncation, overlapping, or horizontal overflow. Frontend must build cleanly with zero errors (npm --prefix frontend run build).
6. R6. Git Push, Railway CI/CD Deployment Verification & Process Hygiene:
   - Document all audit findings, fixes, and verification outcomes in MEMORY.md and PROJECT.md.
   - Commit and push all changes to GitHub origin main.
   - Verify via Railway CLI or dashboard that Railway automatically detects the push and finishes a successful build with active status SUCCESS.
   - Verify remote live production health endpoint GET https://autonomousdaytrader-production.up.railway.app/health returns {"status":"ok"} (or HTTP 200).
   - Enforce strict process hygiene: immediately terminate all local test servers, mock feeds, and background processes, confirming ports 8005, 3005, and 8080 are released.
