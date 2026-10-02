"""The parity manifest pins ORBStraddle's LIVE values, and the copy runs on exactly those values."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from backend.app.strategies.orbs import adaptive, config, scanner
from backend.tests.unit.orbs._helpers import ORBSTRADDLE_DIR

MANIFEST = config.load_manifest()


def test_manifest_pins_orbstraddle_live_values():
    eff = MANIFEST["effective"]
    assert MANIFEST["source"]["commit"] == "06ca29f"
    assert MANIFEST["source"]["rules_version"] == "adaptive-v1.7.1-deal-rule"
    # live Railway: all four flow rules ON (the code default is OFF), candle rule ON
    for key in ("DELTA_RULE", "VELOCITY_RULE", "MACRO_RULE", "ABSORPTION_EXIT", "CANDLE_RULE"):
        assert eff[key] is True, key
    # Deal rule (ORBStraddle v1.7.1): operator ruling pending, so ADT pins the CODE DEFAULT (off), not
    # ORBStraddle's live 'on'. The env key must be absent (unset -> _on_off default), never "on".
    assert eff["DEAL_RULE"] is False
    assert "ORBS_DEAL_RULE" not in MANIFEST["orbstraddle_railway_env"]
    assert eff["RISK_PCT"] == 2.0
    assert eff["MAX_DAY_RISK_PCT"] == 2.5
    assert eff["MAX_OPEN_SLOTS"] == 4 and eff["MAX_PICKS"] == 4
    assert eff["MAX_STRUCTURE_PICKS"] == 2 and eff["MAX_STRUCTURE"] == 2
    assert eff["AUTOPILOT_CUTOFF"] == "10:15"
    assert eff["FLATTEN_ET"] == "11:00"
    assert eff["FREEZE_ET"] == "09:38"
    assert (eff["SECONDARY_WAVE_START"], eff["SECONDARY_WAVE_END"]) == ("09:45:00", "10:15:00")
    assert eff["SECONDARY_SCAN_INTERVAL_S"] == 60
    assert eff["MAX_SLIP_STOP_FRAC"] == 0.33
    assert eff["MAX_GROSS_PCT"] == 300.0 and eff["MAX_BUYING_POWER_PCT"] == 60.0
    assert eff["MAX_NAME_NOTIONAL_PCT"] == 150.0
    assert eff["DAILY_LOSS_HALT_PCT"] == 3.0
    assert eff["MIN_SCAN_COVERAGE"] == 0.9
    assert (eff["TARGET_R"], eff["FAST_FAIL_R"], eff["BE_LOCK_R"], eff["TRAIL_PEAK_R"], eff["TRAIL_GIVEBACK_R"]) == (
        0.75, -0.4, 0.75, 0.6, 0.25)
    assert eff["DELTA_MIN"] == 0.05 and eff["VELOCITY_MIN_RATIO"] == 2.0 and eff["VELOCITY_MIN_TRADES"] == 5
    assert eff["LONG_ONLY_MODE"] == ""          # shorts allowed live
    assert MANIFEST["adt"]["exclude_symbols"] == []    # parity default; ADT sets TSLA,CDE when wiring


def test_copy_effective_config_equals_manifest():
    config.apply_manifest(MANIFEST)
    assert config.effective() == MANIFEST["effective"]
    assert config.EXCLUDE_SYMBOLS == frozenset()
    assert config.DEAL_RULE is False


def test_deal_rule_is_off_at_runtime_in_a_fresh_process():
    """What the copied adaptive.select_adaptive reads as config.DEAL_RULE when ADT imports the package
    cold (no test-time apply_manifest, no ORBS_* environment): the manifest's pinned value, False."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORBS_")}
    env["ORBS_DEAL_RULE"] = "on"                # the process environment must not be able to switch it on
    code = ("from backend.app.strategies.orbs import config, adaptive; "
            "print(repr(config.DEAL_RULE), repr(adaptive.config.DEAL_RULE), config.RULES_VERSION)")
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    out = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env, capture_output=True, text=True,
                         check=True).stdout.split()
    assert out == ["False", "False", "adaptive-v1.7.1-deal-rule"]


def test_scanner_tunables_come_from_manifest():
    env = MANIFEST["scanner_env"]
    assert scanner.PAGE_LIMIT == int(env["ORBS_PAGE_LIMIT"]) == 10000
    assert scanner.PAGE_ATTEMPTS == int(env["ORBS_PAGE_ATTEMPTS"]) == 3
    assert scanner.SCAN_DEADLINE_S == float(env["ORBS_SCAN_DEADLINE_S"]) == 240.0
    assert scanner.ORBS_INCREMENTAL is True and env["ORBS_INCREMENTAL"] == "on"
    assert scanner.CORRECTION_HORIZON_S == 900.0
    assert scanner.REPAIR_WINDOW_S == 120.0
    assert scanner.RECONCILE_INTERVAL_S == 300.0


def test_getenv_answers_only_from_the_manifest(monkeypatch):
    config.apply_manifest(MANIFEST)
    monkeypatch.setenv("ORBS_PAGE_LIMIT", "5")          # the process environment is ignored
    assert config.getenv("ORBS_PAGE_LIMIT", "10000") == "10000"
    assert config.getenv("ORBS_RELAY_BASE") is None     # pinned null -> the caller's default
    assert config.getenv("ORBS_RELAY_TOKEN", "") == ""
    with pytest.raises(KeyError):
        config.getenv("ORBS_NOT_PINNED", "x")


def test_code_constants_match_manifest():
    consts = MANIFEST["code_constants"]
    assert adaptive.ADAPTIVE_CONFIG == consts["adaptive.ADAPTIVE_CONFIG"]
    assert adaptive.ADAPTIVE_VERSION == consts["adaptive.ADAPTIVE_VERSION"] == MANIFEST["effective"]["RULES_VERSION"]
    for key, value in consts["scanner"].items():
        assert getattr(scanner, key) == value, key
    assert consts["scanner"]["SCAN_CONCURRENCY"] == 8          # 2026-10-02: nine robots x 8 threads fit the relay
    assert adaptive.NEWS_PAGE_ATTEMPTS == consts["adaptive.NEWS_PAGE_ATTEMPTS"] == 3
    assert list(adaptive.NEWS_RETRIABLE_HTTP) == consts["adaptive.NEWS_RETRIABLE_HTTP"]


def test_unsupported_long_only_mode_is_refused():
    bad = json.loads(json.dumps(MANIFEST))
    bad["effective"]["LONG_ONLY_MODE"] = "auto"
    with pytest.raises(ValueError):
        config.apply_manifest(bad)
    config.apply_manifest(MANIFEST)


@pytest.mark.skipif(not os.path.exists(os.path.join(ORBSTRADDLE_DIR, "config.py")),
                    reason="ORBStraddle checkout not available")
def test_orbstraddle_config_under_live_env_reproduces_manifest(tmp_path):
    """Run ORBStraddle's own config.py (a copy, without its .env) with the live Railway variables."""
    shutil.copy(os.path.join(ORBSTRADDLE_DIR, "config.py"), tmp_path / "config.py")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORBS_")}
    env.update(MANIFEST["orbstraddle_railway_env"])
    env["ORBS_RUNTIME_ENV"] = str(tmp_path / "absent.env")
    keys = list(MANIFEST["effective"])
    code = ("import json, config; print(json.dumps({k: (list(getattr(config, k)) if isinstance(getattr(config, k), "
            "tuple) else getattr(config, k)) for k in %r}))" % keys)
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True,
                         check=True).stdout
    assert json.loads(out) == MANIFEST["effective"]
