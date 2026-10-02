"""ORBStraddle's decision code, copied into ADT (phase 1 of PLAN_2026_09_28_orb_rules_match_orbstraddle.md).

Copied near-verbatim from ORBStraddle @06ca29f (first pin 71b001f, 2026-09-28): scanner, orbproc, signals,
flow, adaptive, market, ticks, session_calendar (see SYNC_EDITS.json for the only edits). config.py is built from PARITY_MANIFEST.json (ORBStraddle's
live values); shim.py stands in for the few ORBStraddle core.py calls; facade.py is the API ADT calls.
Parity with the original is proven by scripts/orbs_parity (record/replay) and tests/unit/orbs.

Import `backend.app.strategies.orbs.facade` explicitly; this package init imports nothing on purpose.
"""
