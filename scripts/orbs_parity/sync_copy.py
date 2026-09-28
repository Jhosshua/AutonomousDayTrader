"""Regenerate (default) or verify (--check) ADT's copies of ORBStraddle's decision modules.

    python scripts/orbs_parity/sync_copy.py [--source /Users/mo/ORBStraddle] [--check]

The source files must be byte-identical to the pinned commit (sha256 in orbs/SYNC_EDITS.json);
otherwise ORBStraddle has moved on and the edit list must be reviewed before re-syncing.
"""
import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from backend.app.strategies.orbs import _sync  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=_sync.DEFAULT_SOURCE_DIR)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    problems = _sync.check_sources(args.source)
    if problems:
        for name, why in sorted(problems.items()):
            print(f"DRIFT {name}: {why}")
        if args.check:
            sys.exit(2)
        sys.exit("refusing to regenerate from a source that is not the pinned commit")
    if args.check:
        diffs = _sync.check_copies(args.source)
        for name in diffs:
            print(f"COPY DIFFERS {name}")
        print("sync check:", "FAIL" if diffs else "OK (copies == ORBStraddle@pinned + documented edits)")
        sys.exit(1 if diffs else 0)
    for name in _sync.write_copies(args.source):
        print("wrote", name)


if __name__ == "__main__":
    main()
