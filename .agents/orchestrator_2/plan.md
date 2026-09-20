# Execution Plan: AutonomousDayTrader Full Audit, De-Themification, QA & Release

## Objectives
Execute the requirements set forth in ORIGINAL_REQUEST.md (2026-09-20T13:14:36Z):
1. **R1**: Comprehensive Architectural Audit & Bug Remediation (backend core, ingestion, order book, risk engine, state machines, strategies, WebSocket streaming).
2. **R2**: De-themification of Music & Playlist Terminology (replace with Trading Strategies, Active Position, remove all music/playlist/album/track metaphors).
3. **R3**: Multi-Agent Adversarial Diff Review & Full QA Cycle (100% pytest and scripts/run_e2e_tests.sh).
4. **R4**: Deterministic Market Open Dry-Run Simulation (Monday 09:25–10:30 ET simulation through production path).
5. **R5**: Mobile & Desktop Visual UI Audit (390x844 & 1440x900 viewport visual check, clean npm build).
6. **R6**: Git Push, Railway CI/CD Deployment Verification & Process Hygiene (update MEMORY.md and PROJECT.md, git push origin main, verify Railway auto-deploy SUCCESS, verify live remote health endpoint, kill all local background processes and free ports).

## Step-by-Step Plan

### Phase 1: Parallel Exploration & Deep Audit
- **Explorer 1 (Backend & Architecture Audit)**:
  - Scope: `src/` (backend core, ingestion adapters, order book, risk engine, state machines, 4 strategies, WebSocket streaming).
  - Check for missed connections, unhandled states, inverted risk boundaries, orphaned brackets, dead code paths, race conditions.
  - Deliverable: `.agents/explorer_backend/audit_report.md`.
- **Explorer 2 (Terminology & Metaphor Inventory)**:
  - Scope: Full codebase (`src/`, `frontend/`, `tests/`, docs, configs).
  - Search for all occurrences of "playlist", "curated playlist", "album", "track", "now playing", and other music metaphors.
  - Deliverable: `.agents/explorer_terminology/terminology_inventory.md`.

### Phase 2: Implementation & Remediation (Worker)
- **Worker 1 (Backend Remediation & Core De-themification)**:
  - Fix any architectural/logic bugs identified by Explorer 1.
  - Replace backend/model terminology (e.g. any music references in schemas, events, or state).
  - Deliverable: `.agents/worker_backend/handoff.md`.
- **Worker 2 (Frontend De-themification & UI Polish)**:
  - Update frontend components, labels, drawers, and styles (Trading Strategies, Active Position / Live Execution).
  - Ensure zero music analogies remain in UI.
  - Run frontend typecheck/build verification (`npm --prefix frontend run build`).
  - Deliverable: `.agents/worker_frontend/handoff.md`.

### Phase 3: Adversarial Diff Review & Forensic Audit
- **Reviewer 1 & Reviewer 2**:
  - Independent review of all git diffs.
  - Ensure contract preservation, no regressions, clean trading terminology.
- **Forensic Auditor**:
  - Verify authenticity of fixes and changes, ensure no hardcoding or bypasses.

### Phase 4: Full QA Suite & Deterministic Market Open Dry Run
- **QA & Simulation Specialist**:
  - Run full backend test suite (`pytest tests/`).
  - Run full E2E test suite (`scripts/run_e2e_tests.sh`).
  - Execute deterministic Monday market open dry-run simulation and inspect output logs.
  - Deliverable: `.agents/worker_qa/qa_report.md`.

### Phase 5: Mobile & Desktop Visual UI Audit
- **Visual UI Specialist**:
  - Verify frontend renders cleanly on mobile (390x844) and desktop (1440x900).
  - Verify strategy cards, active position tray, charts, badges, and controls.
  - Deliverable: `.agents/worker_visual/visual_audit_report.md`.

### Phase 6: Release, Remote Railway Deployment & Process Hygiene
- **Release Engineer**:
  - Update `MEMORY.md` and `PROJECT.md` with complete session records.
  - Commit all changes and push to `origin main`.
  - Monitor Railway deployment until status SUCCESS.
  - Query remote production health endpoint `GET https://autonomousdaytrader-production.up.railway.app/health`.
  - Enforce process hygiene: verify all local test processes killed and ports 8005, 3005, 8080 free.
  - Deliverable: `.agents/worker_release/release_report.md`.

### Phase 7: Synthesis & Final Orchestrator Handoff
- Synthesize all agent reports, verify all acceptance criteria, generate final handoff.md, and send completion message to Sentinel.
