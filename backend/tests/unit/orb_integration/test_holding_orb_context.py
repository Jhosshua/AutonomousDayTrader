"""ORB holding context for the "Holding now" card (PLAN_2026_09_29): the 9:38 market check a trade passed is
saved in ORB's own ledger section, position_details always carries every orb_context key, and the flatten time
is read from the controller's config. Display only: nothing here can change an order."""
import copy
import json

from backend.tests.unit.orb_integration.harness import at, pick
from backend.tests.unit.orb_integration.test_fake_session import board, calls, decision, run, session

ORB_KEYS = {"classification", "short_frac", "wave", "decided_at", "short_bounds", "flow_rules_on",
            "breakeven_r", "risk_usd", "flatten_at"}


def _trade_day(r):
    h = session(r)
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    d = decision(pick("APP", "long", 100.0, 98.0))
    d["regime"] = {"action": "TRADE_NORMAL", "classification": "CALM_TREND", "short_frac": 0.4}
    h.facade.decide_results = [d]
    run(h, at(9, 10), at(9, 39))
    assert r.account.positions["APP"].strategy_id == "orb"
    return h


def test_a_decision_with_picks_saves_the_regime_subset_and_the_holding_carries_it(main_runtime):
    r = main_runtime
    h = _trade_day(r)
    saved = r.orb.ledger["regime"]
    assert saved["day"] == h.clock.now.date().isoformat()
    note = saved["symbols"]["APP"]
    assert note["classification"] == "CALM_TREND" and note["short_frac"] == 0.4 and note["wave"] == "primary" and note["at"]
    ctx = r._serialize_position("APP", include_chart=False)["orb_context"]
    assert set(ctx) == ORB_KEYS
    assert (ctx["classification"], ctx["short_frac"], ctx["wave"]) == ("CALM_TREND", 0.4, "primary")
    assert ctx["short_bounds"] == {"min": 0.25, "max": 0.75}
    assert ctx["flow_rules_on"] == ["candle", "delta", "velocity", "macro", "absorption"]
    assert ctx["breakeven_r"] == 0.75 and ctx["flatten_at"] == "11:00"
    assert ctx["risk_usd"] == r.orb.controller.holdings()[0]["risk_usd"] and ctx["risk_usd"] > 0
    # the section rides the checkpoint and an older reader that only knows the four keys ignores it
    wire = json.loads(json.dumps(r.orb.ledger_state()))
    assert wire["regime"]["symbols"]["APP"]["classification"] == "CALM_TREND"
    r.orb.load_ledger_state(copy.deepcopy(wire))
    assert r.orb.ledger_error is None and r.orb.ledger["regime"] == wire["regime"]
    legacy = {k: v for k, v in wire.items() if k != "regime"}
    r.orb.load_ledger_state(legacy)
    assert r.orb.ledger_error is None and "regime" not in r.orb.ledger


def test_orb_context_has_every_key_even_with_nothing_saved_and_yesterdays_note_is_ignored(main_runtime):
    r = main_runtime
    h = _trade_day(r)
    r.orb.ledger["regime"]["day"] = "2026-01-01"                     # a stale day
    ctx = r._serialize_position("APP", include_chart=False)["orb_context"]
    assert set(ctx) == ORB_KEYS
    assert ctx["classification"] is None and ctx["short_frac"] is None and ctx["wave"] is None
    assert ctx["breakeven_r"] == 0.75                                # config values still there
    r.orb.ledger.pop("regime")
    assert set(r._serialize_position("APP", include_chart=False)["orb_context"]) == ORB_KEYS
    # a non-holding symbol asked directly still answers every key
    assert set(r.orb.position_details("ZZZ")["orb_context"]) == ORB_KEYS
    assert r.orb.position_details("ZZZ")["exit_due"] is None


def test_flatten_time_comes_from_the_controller_config(main_runtime):
    r = main_runtime
    h = _trade_day(r)
    due = r._serialize_position("APP", include_chart=False)["exit_due"]
    assert due.endswith("-04:00") and "T11:00:00" in due
    r.orb.controller.cfg["flatten"] = "10:45"
    out = r._serialize_position("APP", include_chart=False)
    assert "T10:45:00" in out["exit_due"] and out["orb_context"]["flatten_at"] == "10:45"
    assert len(calls(h, "decide")) == 1


def test_a_failure_building_the_context_leaves_the_position_serializable(main_runtime, monkeypatch):
    r = main_runtime
    _trade_day(r)
    import backend.app.strategies.orbs.adaptive as adaptive
    monkeypatch.setattr(adaptive, "ADAPTIVE_CONFIG", {})            # KeyError inside the context builder
    out = r._serialize_position("APP", include_chart=False)
    assert set(out["orb_context"]) == ORB_KEYS and out["orb_context"]["short_bounds"] is None
    assert out["strategy_id"] == "orb" and out["fixed_protection"] is True
