"""Codex review round 2 of phase 1: final-snapshot requirements for the primary board and a guard that checks
the ACTUAL scanner/market import-time constants."""
import copy

import pytest

from backend.app.strategies.orbs import config, facade as facade_mod, scanner
from backend.app.strategies.orbs.facade import OrbsFacade
from backend.tests.unit.orbs.test_facade import D, at, fac, primary, replay  # noqa: F401  (fixtures)


def _refused(f, board, now=at(9, 39)):
    out = f.decide(D, board, "primary", now, set())
    assert out["verdict"] == "refused" and out["picks"] == []
    return out["reason"]


def test_primary_must_be_the_final_freeze_board(fac):  # noqa: F811
    early = fac.scan(D, at(9, 37), "primary", set())
    assert early["ok"] and early["cards"]                                   # a perfectly good scan...
    assert "not the 09:38 final" in _refused(fac, early)                    # ...but not the final board
    board = primary(fac)
    assert fac.decide(D, board, "primary", at(9, 39), set())["verdict"] == "trade"
    assert "before the 09:38 freeze" in _refused(fac, board, now=at(9, 37, 59))
    assert "card source" in _refused(fac, {**board, "health": {**board["health"], "source": "demo"}})


def test_primary_rejects_a_board_with_any_invalid_card(fac):  # noqa: F811
    board = primary(fac)
    bad = dict(board["cards"][0], symbol="ZZZ", stop=board["cards"][0]["entry"])   # stop == entry: invalid
    mixed = {**board, "cards": board["cards"] + [bad],
             "health": {**board["health"], "cards": len(board["cards"]) + 1}}
    assert _refused(fac, mixed) == "card count differs from scan metadata"
    only_bad = {**board, "cards": [bad], "health": {**board["health"], "cards": 1}}
    assert _refused(fac, only_bad) == "no valid cards in the final scan"
    assert _refused(fac, {**board, "health": {**board["health"], "cards": None}}) == \
        "card count differs from scan metadata"


def test_guard_checks_the_actual_module_constants(tmp_path):
    """Codex repro: the config says ORBS_PAGE_LIMIT=100 but scanner.PAGE_LIMIT was fixed at import (10000)."""
    manifest = copy.deepcopy(config.load_manifest())
    manifest["scanner_env"]["ORBS_PAGE_LIMIT"] = "100"
    try:
        config.apply_manifest(manifest)                   # config agrees with the manifest ...
        assert scanner.PAGE_LIMIT == 10000                # ... the loaded module does not
        with pytest.raises(ValueError, match="scanner.PAGE_LIMIT"):
            OrbsFacade(str(tmp_path), "https://relay.invalid", "t", manifest=manifest)
        big = copy.deepcopy(config.load_manifest())
        big["scanner_env"]["ORBS_RELAY_MAX_BODY_BYTES"] = str(32 * 1024 * 1024)
        assert facade_mod.import_time_mismatches(big) == [
            ("market.MAX_RESPONSE_BYTES", 16 * 1024 * 1024, 32 * 1024 * 1024)]
    finally:
        config.apply_manifest(config.load_manifest())
    assert facade_mod.import_time_mismatches(config.load_manifest()) == []
    OrbsFacade(str(tmp_path), "https://relay.invalid", "t")          # the pinned manifest is accepted
