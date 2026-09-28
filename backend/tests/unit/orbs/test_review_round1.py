"""Codex review round 1 of phase 1: board gates in decide(), the executed-today exclusion, the lockout reset,
import-time limits, the final-verdict comparison and page splits at the 09:35 boundaries."""
import copy
import json
import os

import pytest

from backend.app.strategies.orbs import config, signals
from backend.app.strategies.orbs.facade import OrbsFacade
from backend.tests.unit.orbs._helpers import SYNTH_DAY, load_golden, parity_modules, unpack_fixture
from backend.tests.unit.orbs.test_facade import D, at, fac, primary, replay  # noqa: F401  (fixtures)


def test_decide_refuses_boards_orbstraddle_would_not_decide(fac):  # noqa: F811
    board = primary(fac)
    assert fac.decide(D, board, "primary", at(9, 39), set())["verdict"] == "trade"

    def refused(b, wave="primary", now=at(9, 39)):
        out = fac.decide(D, b, wave, now, set())
        assert out["verdict"] == "refused" and out["picks"] == []
        return out["reason"]

    assert "cutoff" in refused(board, now=at(10, 15))                 # auditor._past_cutoff: >= 10:15
    assert "wave" in refused(board, wave="secondary", now=at(9, 50))
    assert "did not succeed" in refused({**board, "ok": False, "error": "coverage"})
    assert "is for" in refused({**board, "health": {**board["health"], "day": "2031-03-03"}})
    assert "covered only" in refused({**board, "health": {**board["health"], "ok": 5, "failed": 2}})
    assert "do not match" in refused({**board, "cards": board["cards"][1:]})
    assert "not the last successful" in refused({**board, "board_id": "0" * 16})
    assert refused({**board, "cards": []}) == "the board is empty"
    again = fac.scan(D, at(9, 38), "primary", set())                  # same inputs -> same board, still valid
    assert again["board_id"] == board["board_id"]
    assert fac.decide(D, board, "primary", at(9, 39), set())["verdict"] == "trade"
    fac.scan(D, at(9, 37), "primary", set())                           # a later scan supersedes the old board
    assert "not the last successful" in refused(board)


def test_executed_today_symbol_never_reappears_on_secondary_board_or_picks(fac):  # noqa: F811
    primary(fac)
    both = fac.scan(D, at(9, 51), "secondary", set(), executed_today=set())
    assert {c["symbol"] for c in both["cards"]} == {"PLTR", "BA"}
    # PLTR was traded earlier today and is closed now: not open (skip_symbols empty) but executed today
    board = fac.scan(D, at(9, 52), "secondary", set(), executed_today={"PLTR"})
    assert {c["symbol"] for c in board["cards"]} == {"BA"}
    assert board["health"]["attempted"] == 6
    out = fac.decide(D, board, "secondary", at(9, 52, 30), set(), executed_today={"PLTR"})
    assert "PLTR" not in [p["symbol"] for p in out["picks"]]
    with pytest.raises(ValueError):
        fac.scan(D, at(9, 53), "secondary", set())                    # executed_today is mandatory there
    fresh = primary(fac)                                               # a board that still shows it
    out = fac.decide(D, fresh, "primary", at(9, 39), set(), executed_today={"XOM"})
    assert [p["symbol"] for p in out["picks"]] == ["AMD"]
    assert {"symbol": "XOM", "reason": "already executed today"} in out["refused"]


def test_new_facade_resets_the_session_lockout(replay, tmp_path):  # noqa: F811
    OrbsFacade(str(tmp_path / "a"), replay.RELAY_ROOT, "t", http=replay.Transport(SYNTH_DAY, "replay"))
    signals.SESSION_LOCKOUT.trigger_lockout("SPY", 4.0, "test")
    assert signals.SESSION_LOCKOUT.is_locked()
    OrbsFacade(str(tmp_path / "b"), replay.RELAY_ROOT, "t", http=replay.Transport(SYNTH_DAY, "replay"))
    assert not signals.SESSION_LOCKOUT.is_locked()
    assert signals.SESSION_LOCKOUT.get_info()["symbol"] is None
    assert signals.SESSION_LOCKOUT.filepath == str(tmp_path / "b" / "session_lockout.json")


def test_manifest_cannot_silently_change_import_time_limits(tmp_path):
    manifest = copy.deepcopy(config.load_manifest())
    manifest["scanner_env"]["ORBS_SCAN_DEADLINE_S"] = "999"
    with pytest.raises(ValueError, match="ORBS_SCAN_DEADLINE_S"):
        OrbsFacade(str(tmp_path), "https://relay.invalid", "t", manifest=manifest)
    manifest = copy.deepcopy(config.load_manifest())
    manifest["effective"]["MIN_SCAN_COVERAGE"] = 0.95                  # runtime values do apply per facade
    try:
        OrbsFacade(str(tmp_path), "https://relay.invalid", "t", manifest=manifest)
        assert config.MIN_SCAN_COVERAGE == 0.95
    finally:
        config.apply_manifest(config.load_manifest())


@pytest.fixture(scope="module")
def synthetic_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("orbs_review_synth")
    unpack_fixture(str(root))
    return str(root)


def test_copy_matches_golden_when_pages_split_at_0935_boundaries(synthetic_root, monkeypatch):
    """Tape pages end exactly at 09:35:00.000000000 and 09:35:05.000000000 (rows stamped there open the
    next page); the copy must still produce the golden (unsplit, original) output."""
    common, copy_runner, compare = parity_modules()
    monkeypatch.setenv("ADT_PARITY_SPLIT_AT", "09:35:00,09:35:05")
    old, common.CACHE_ROOT = common.CACHE_ROOT, synthetic_root
    try:
        golden = load_golden(os.path.join(synthetic_root, SYNTH_DAY))
        result = copy_runner.run_copy(SYNTH_DAY, log=lambda m: None)
        tape = common.TapeStore(SYNTH_DAY)
        lines, stamps = tape._load("AMD", "trades")
        assert common.ts_ns(f"{SYNTH_DAY}T09:35:00-05:00") in stamps     # rows really sit on the boundaries
        assert common.ts_ns(f"{SYNTH_DAY}T09:35:05-05:00") in stamps
    finally:
        common.CACHE_ROOT = old
    assert result["tape_splits"] > 0 and result["counts"]["tape"] > golden["counts"]["tape"]
    report = compare.parity(golden, result)
    assert report["equal"], json.dumps([s for s in report["steps"] if not s["equal"]][:2], default=str)[:2000]


def test_final_verdict_mismatch_is_reported(synthetic_root):
    _, _, compare = parity_modules()
    golden = load_golden(os.path.join(synthetic_root, SYNTH_DAY))
    fake = json.loads(json.dumps(golden))
    for step in fake["steps"]:
        if step.get("decision"):
            step["decision"]["verdict"] = "trade"
            step["decision"]["picks"] = ["XOM"]
    report = compare.parity(golden, fake)
    assert not report["equal"]
    assert any(d["path"] == "decision.final" for s in report["steps"] for d in s["diffs"])
