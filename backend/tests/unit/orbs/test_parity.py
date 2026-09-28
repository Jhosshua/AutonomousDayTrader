"""Parity: ADT's copy produces EXACTLY what ORBStraddle's original produces on the same frozen inputs.

1. Always: the checked-in synthetic session. golden_original.json was produced by ORBStraddle's ORIGINAL
   modules (scripts/orbs_parity/make_synthetic_fixture.py); the copy replays the same inputs in-process and
   every board, decision, audit row, validator result and re-check must match exactly.
2. When the ORBStraddle checkout exists: the original is re-run on the fixture (subprocess, replay) and
   must still reproduce the golden output.
3. When recorded real sessions exist (research/orbs_parity_cache/<date>/, gitignored, see record.py): the
   copy replays each day's prep + preview + primary scan + primary decision and must match the original's
   replay output for the same steps. The full-day comparison (all 33 scans) is scripts/orbs_parity/compare.py.
"""
import json
import os
import subprocess
import sys

import pytest

from backend.tests.unit.orbs._helpers import (ORBSTRADDLE_DIR, PARITY_DIR, SYNTH_DAY, load_golden, parity_modules,
                                               unpack_fixture)


@pytest.fixture(scope="module")
def synthetic_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("orbs_parity_synth")
    unpack_fixture(str(root))
    return str(root)


def _with_root(common, root):
    old = common.CACHE_ROOT
    common.CACHE_ROOT = root
    return old


def test_copy_matches_original_golden_on_synthetic_session(synthetic_root):
    common, copy_runner, compare = parity_modules()
    old = _with_root(common, synthetic_root)
    try:
        golden = load_golden(os.path.join(synthetic_root, SYNTH_DAY))
        result = copy_runner.run_copy(SYNTH_DAY, log=lambda m: None)
    finally:
        common.CACHE_ROOT = old
    report = compare.parity(golden, result)
    bad = [s for s in report["steps"] if not s["equal"]]
    assert report["equal"], json.dumps({"sections": report["sections"], "steps": bad[:3],
                                        "misses": report["misses"]}, default=str)[:3000]
    # the fixture really exercises the decision branches (guards against a vacuous golden file)
    decisions = [s["decision"] for s in report["steps"] if s["decision"] is not None]
    assert ["XOM", "AMD"] in decisions and ["PLTR", "BA"] in decisions and "ONE_SIDED" in decisions
    assert len(report["steps"]) == 33


@pytest.mark.skipif(not os.path.exists(os.path.join(ORBSTRADDLE_DIR, "scanner.py")),
                    reason="ORBStraddle checkout not available")
def test_original_still_reproduces_the_golden_output(synthetic_root, tmp_path):
    _, _, compare = parity_modules()
    out = tmp_path / "orig.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ORBS_", "ALPACA_"))}
    env["ADT_PARITY_CACHE_ROOT"] = synthetic_root
    subprocess.run([sys.executable, os.path.join(PARITY_DIR, "orig_runner.py"), "--date", SYNTH_DAY, "--mode",
                    "replay", "--out", str(out)], env=env, check=True, capture_output=True)
    with open(out) as f:
        rerun = json.load(f)
    golden = load_golden(os.path.join(synthetic_root, SYNTH_DAY))
    report = compare.parity(golden, rerun)
    assert report["equal"], json.dumps(report["steps"][:3], default=str)[:2000]


def _recorded_days():
    common, _, _ = parity_modules()
    if not os.path.isdir(common.CACHE_ROOT):
        return []
    days = []
    for day in sorted(os.listdir(common.CACHE_ROOT)):
        ddir = os.path.join(common.CACHE_ROOT, day)
        if os.path.exists(os.path.join(ddir, "replay_original.json")):
            days.append(day)
    return days if os.environ.get("ADT_PARITY_ALL_DAYS") == "1" else days[:1]


@pytest.mark.parametrize("day", _recorded_days() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no replayed session under research/orbs_parity_cache (run record.py then compare.py)"))])
def test_copy_matches_original_on_recorded_session_primary(day):
    common, copy_runner, compare = parity_modules()
    ddir = common.day_dir(day)
    with open(os.path.join(ddir, "replay_original.json")) as f:
        orig = json.load(f)
    result = copy_runner.run_copy(day, log=lambda m: None, max_steps=2)       # preview + primary + decision
    orig["steps"] = orig["steps"][:2]
    report = compare.parity(orig, result)
    assert report["equal"], json.dumps({"sections": report["sections"], "steps": report["steps"]},
                                       default=str)[:3000]
