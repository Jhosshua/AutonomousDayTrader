# Task Dispatch: Frontend De-Themification, UI Polish & Test Updating

## Objective
Implement complete de-themification of music, playlist, and album metaphors across the frontend, e2e test locators, and project documentation, replacing them with professional institutional trading terminology per ORIGINAL_REQUEST.md (§R2) and the Terminology Audit Report (`/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_term_audit_2/terminology_report.md`).

## Exclusive Write Ownership
You exclusively own and may edit only the following files:
- `frontend/` (`frontend/components/`, `frontend/app/`, `frontend/scripts/verify_ui.mjs`, etc.)
- `tests/e2e/` (`test_challenger_mobile.py`, `test_tier1_features.py`, `test_tier2_boundary.py`, `test_contracts.py`)
- Documentation: `PROJECT.md`, `README.md`, `TEST_INFRA.md`, `TEST_READY.md`, `scripts/`

DO NOT modify backend Python files under `backend/app/` (Worker 1 owns those).

## Tasks to Implement
1. [CRITICAL UI STRING] `frontend/components/StrategyCarousel.tsx:23`: Replace `<span>Curated Playlists</span>` with `<span>Trading Strategies</span>`.
2. [CRITICAL UI STRING] `frontend/components/StrategyCard.tsx:126`: Replace `Playlist / Strategy` with `Trading Strategy`. Replace internal `getArtwork` with `getStrategyTheme`, update comments ("Album Artwork" -> "Strategy Visual Banner").
3. [UI METADATA] `frontend/app/layout.tsx:6`: Update meta description to remove "Apple Music design language" and replace with "fluid obsidian execution interface".
4. [COMPONENT REFACTOR] Create `frontend/components/ActivePositionTray.tsx` using `layoutId="active-position-tray"`, replace internal analogies ("Docked Mini-Player Bar", "Ticker Album Avatar", "Lyrics View"), and provide `frontend/components/NowPlayingTray.tsx` as a clean re-export compatibility shim.
5. [DASHBOARD PAGE] `frontend/app/page.tsx`: Import `ActivePositionTray` and update JSX and comments from "Playlists" / "Mini-Player" to "Trading Strategies" / "Active Position Tray".
6. [TEST SUITE SYNCHRONIZATION]:
   - `tests/e2e/test_challenger_mobile.py`: Update line 397 Playwright locator from `"text=Curated Playlists"` to `"text=Trading Strategies"`. Update line 142 to check `ActivePositionTray.tsx` (or fallback). Update function name `test_now_playing_tray...` to `test_active_position_tray...`.
   - `tests/e2e/test_tier1_features.py` & `test_tier2_boundary.py`: Update test headers, function names, and docstrings from "Playlists/Albums" and "Now Playing" to "Trading Strategy Cards" and "Active Position Bottom Tray".
   - `frontend/scripts/verify_ui.mjs`: Update checks to verify `ActivePositionTray.tsx` and updated console logs.
7. [DOCUMENTATION & SCRIPTS]:
   - Update `PROJECT.md`, `README.md`, `TEST_INFRA.md`, `TEST_READY.md`, and helper scripts in `scripts/` to remove music/playlist/album metaphors.
8. [VERIFICATION]:
   - Run grep verification across `frontend/` to confirm ZERO occurrences of "playlist", "curated playlist", "album", or music analogies in user-facing UI labels and frontend components.
   - Run `npm --prefix frontend run build` to verify clean build with 0 TypeScript/lint errors.
   - Run `node frontend/scripts/verify_ui.mjs` to verify frontend architecture checks.

## Deliverable
Write `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_worker_frontend_2/handoff.md` with:
- Exact changes made per file
- Grep verification confirmation (showing zero occurrences of music/playlist terms in UI)
- `npm --prefix frontend run build` verification outcome
- `node frontend/scripts/verify_ui.mjs` verification outcome

## 2026-09-20T13:22:22Z
Frontend De-Themification & UI Polish Worker dispatch confirmed. Starting execution.
