# Phase 1: ORBStraddle's decision code inside ADT, and the proof it is the same code

Plan: `PLAN_2026_09_28_orb_rules_match_orbstraddle.md` (sections 2.1, 5.1, 8.5, 8.7, 9.5, 10.1).

## What was built

- `backend/app/strategies/orbs/`: ORBStraddle @71b001f `scanner`, `orbproc`, `signals`, `flow`, `adaptive`,
  `market`, `ticks`, generated from the original files plus only the edits in `SYNC_EDITS.json`
  (package imports, `core` -> `shim`, HTTP through `shim.urlopen`, ORBS_* tunables from the manifest, and the
  ADT-only `exclude_symbols`). `scripts/orbs_parity/sync_copy.py` regenerates them; `--check` verifies.
- `config.py` is built only from `PARITY_MANIFEST.json` (ORBStraddle's live Railway values, flow rules ON).
  Relay URL/token come from ADT settings via the facade.
- `shim.py`: clock, occupancy, telemetry, transport, and `validate`/`_valid_card` copied verbatim from core.py.
- `facade.py`: `OrbsFacade` (prep, scan, decide, recheck, macro_veto, latest_trade, absorption_poll), each method
  documented with the ORBStraddle file:line it wraps.
- `session_calendar.py` was NOT copied: the decision modules do not import it (only ORBStraddle's scheduler
  does). Phase 2's scheduler needs a calendar check.

## Review round 1 (Codex) changes

- `decide()` validates the board first (scan ok, same day and wave, non-empty, coverage, board_id equal to the
  facade's last successful scan of that wave, now before the 10:15 cutoff); otherwise verdict `refused`.
- `scan(..., executed_today=...)` (mandatory for secondary) and `decide(..., executed_today=...)`: the caller
  must pass the durable union of symbols executed today; they never reappear on a secondary board or in picks.
- A new facade builds a fresh session-lockout latch.
- A manifest whose `scanner_env` differs from the import-time values is rejected (ValueError).
- `compare.py` also checks the copy's final facade verdict and picks; `--split-pages 09:35:00,09:35:05`
  forces tape page boundaries at those instants (the synthetic session has rows exactly there).
- The original runner now mirrors the auditor's cutoff and empty-board gates, so the 10:15 scan is not decided.

## How parity is proven

`scripts/orbs_parity/record.py <dates>` records a past session once (read-only relay): prep data, the
250-symbol SIP tape 09:30-10:15, and every other relay answer the ORIGINAL code asks for on the full timeline.
`compare.py <dates>` then replays the ORIGINAL (from /Users/mo/ORBStraddle, hash-checked, run in a temp dir,
no keys) and the COPY on those frozen inputs with a pinned clock: preview 09:36, primary 09:38 + decision at
09:39:00, secondary scans every minute 09:45-10:15 + decision 30 s after each. Boards, scan health,
adaptive replies (picks, audit, regime), validator output and the candle/flow/macro re-checks are diffed
exactly. Any request the cache cannot answer fails the run.

Cache: `research/orbs_parity_cache/<date>/` (gitignored, about 1.5-1.8 GB per day).

Tests (`backend/tests/unit/orbs/`): manifest values, sync check, facade behaviour (including exclusion and
occupancy), an always-on synthetic session whose golden output came from the original, and a recorded-day
primary replay when the cache exists.

## Results (2026-09-28, replay with ORBStraddle's 09-28 live config on every day)

| Session | Steps (33 each) | Misses | Result |
|---|---|---|---|
| 2026-09-22 | 33/33 equal | 0 | EXACT MATCH |
| 2026-09-23 | 33/33 equal | 0 | EXACT MATCH |
| 2026-09-24 | 33/33 equal | 0 | EXACT MATCH |
| 2026-09-25 | 33/33 equal | 0 | EXACT MATCH |
| 2026-09-28 | 33/33 equal | 0 | EXACT MATCH |

Cross-check against what ORBStraddle did live (informational). Only 09-28 ran the pinned rules live
(09-22..09-25 ran adaptive-v1.5.0 without flow rules), so only 09-28 is comparable:
every live 09-28 board (09:38 primary and all 18 secondary boards) has the same cards with the same
direction, entry, stop, trigger time and blocked flags as the replayed original; the only differences are
1-2 extra cards on the replayed boards after 10:08, because live excluded APP once it held it. The live
decisions match the replay through the 10:05 board, including the ONE_SIDED 84% short sit-out at 09:38 and
the APP short pick; after 10:08 the card counts differ (APP excluded live), so the matching is not meaningful.
Live used the 10:05 board at 10:08:27 (scans ran late); the replay decides each board 30 s after its scan.

Details per day: `research/orbs_parity_cache/<date>/parity_report.json` (local, gitignored).
