# Task Dispatch: Terminology & Metaphor Audit (De-Themification)

## Objective
Audit the entire AutonomousDayTrader repository to locate all occurrences of music, playlist, and album metaphors and plan their replacement with professional trading terminology.

## Requirements
From ORIGINAL_REQUEST.md (§R2):
- Replace "Curated Playlists" / "Playlists / Albums" with "Trading Strategies".
- Replace "Now Playing" bottom tray with "Active Position" (or "Live Execution").
- Clean up any remaining music-inspired labels (e.g. "album art", "track", "playlist", "now playing", "playing") in comments, tests, UI components, state models, docs.
- The user requires that grep verification must confirm ZERO occurrences of "playlist", "curated playlist", "album", or music analogies in user-facing UI labels, frontend components, or active trade drawers.

## Instructions
1. Search the full project (`frontend/`, `src/`, `tests/`, `scripts/`, `docs/`):
   - Locate every occurrence of "playlist", "album", "track", "now playing", "now-playing", "now_playing", "curated", "music", etc.
   - Separate findings into:
     a) User-facing UI labels and components (must be 100% purged/replaced)
     b) CSS classes, component filenames, test files, internal variables, comments
     c) API models, state payloads, schemas (assess if any backend keys or types need renaming while maintaining frontend-backend synchronization)
2. Detail the exact replacement plan:
   - "Curated Playlists" -> "Trading Strategies"
   - "Now Playing" -> "Active Position"
   - Any other identified music terms -> corresponding institutional trading terminology
3. Note any backward compatibility considerations between frontend and backend WebSocket message formats.
4. Write your detailed inventory and plan to `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_term_audit_2/terminology_report.md` and write `handoff.md`.
5. Send a completion message back when done.

## 2026-09-20T13:16:14Z
You are the Terminology Auditor for AutonomousDayTrader.

Your working directory is: /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_term_audit_2
Project root: /Users/mo/AutonomousDayTrader

MANDATORY FIRST STEP: Read /Users/mo/AutonomousDayTrader/ORIGINAL_REQUEST.md before starting work.
Also read:
- /Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_term_audit_2/DISPATCH.md
- /Users/mo/AutonomousDayTrader/PROJECT.md
- /Users/mo/AutonomousDayTrader/MEMORY.md

Task:
Perform a comprehensive audit of music, playlist, and album metaphors across the entire codebase (`frontend/`, `src/`, `tests/`, `scripts/`, docs).
From ORIGINAL_REQUEST.md (§R2):
- Replace "Curated Playlists" / "Playlists / Albums" with "Trading Strategies".
- Replace "Now Playing" bottom tray with "Active Position" (or "Live Execution").
- Clean up any remaining music-inspired labels (e.g., "album art", "track", "playlist") in comments, tests, component strings, schemas, and state.
- Ensure grep verification will confirm zero occurrences of "playlist", "curated playlist", "album", or music analogies in user-facing UI labels, frontend components, or active trade drawers.

Produce:
- `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_term_audit_2/terminology_report.md` with:
  1. Full inventory of all files and exact line numbers containing music/playlist/album/track terminology.
  2. Categorization: UI user-facing labels, component files, state/store, backend models/routes, tests, and documentation.
  3. Concrete replacement mappings for every occurrence.
  4. Contract verification: ensuring frontend-backend WebSocket synchronization remains intact.
- `/Users/mo/AutonomousDayTrader/.agents/teamwork_preview_explorer_term_audit_2/handoff.md`

Send a message back when completed.
