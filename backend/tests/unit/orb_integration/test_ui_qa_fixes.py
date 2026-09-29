"""Dashboard QA fixes (2026-09-28): no-trade-today state, orphan text, scoped errors, plain step text,
card P&L including open trades."""
from datetime import timedelta

import pytest

from backend.tests.unit.orb_integration.harness import MainOrb, at, pick
from backend.tests.unit.orb_integration.test_codex_p3_fixes import _orphan
from backend.tests.unit.orb_integration.test_fake_session import board, run, session


def card(r, now):
    return next(c for c in r._strategy_cards(now) if c["id"] == "orb")


def test_a_failed_9_38_scan_shows_no_trade_today_never_watching(main_runtime):
    r = main_runtime
    h = session(r)
    h.facade.scan_results = [board("APP"), {"ok": False, "error": "relay failed", "coverage": None, "cards": [],
                                            "board_id": None},
                             {"ok": False, "error": "relay failed", "coverage": None, "cards": [], "board_id": None}]
    run(h, at(9, 10), at(9, 40), step=10)
    c = card(r, at(9, 45))
    assert c["window"]["state"] == "NO_TRADE_TODAY" and c["window"]["state"] not in ("CAN_TRADE", "LIMITED")
    assert c["window"]["market_text"] == "No trade today: the 9:38 scan failed (relay error)."
    assert c["orb"]["no_trade_reason"] == "the 9:38 scan failed (relay error)"


def test_a_loss_halt_also_means_no_trade_today(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.ctl._latch_halt("ORB's own P&L is -3.1%", -3.1)
    assert card(r, at(9, 50))["window"]["state"] == "NO_TRADE_TODAY"
    h2 = MainOrb(r)
    assert card(r, at(9, 50))["window"]["state"] != "NO_TRADE_TODAY"     # a healthy ORB can still trade


def test_orphan_alarm_text_matches_reality(main_runtime):
    r = main_runtime
    _orphan(r)
    assert r.orb.owns("APP")
    assert "press" in r.orb.alerts["APP"] and "Nothing will sell them automatically" in r.orb.alerts["APP"]
    notes = [e.get("note") or "" for e in r.orb.errors if e.get("kind") == "orphan_orb_position"]
    assert notes and "Nothing sells it automatically" in notes[0] and "press the button" in notes[0]
    assert not any("will cancel" in n for n in notes)


def test_errors_are_scoped_to_today_and_cleared_when_resolved(main_runtime):
    r = main_runtime
    h = _orphan(r)
    assert any(e.get("symbol") == "APP" for e in r.orb.current_errors())
    # the orphan goes away (e.g. resolved): its errors go with it
    r.account.positions.pop("APP")
    r.orb.check_orphans()
    assert not any(e.get("symbol") == "APP" for e in r.orb.current_errors())
    r.orb._err({"kind": "ledger_sync", "err": "x"})
    assert r.orb.current_errors()
    h.clock.set(h.clock.now + timedelta(days=1))                 # next session: yesterday's are not shown
    assert r.orb.current_errors() == []


def test_step_text_is_plain_and_card_pnl_includes_open_trades(main_runtime):
    r = main_runtime
    h = session(r)
    h.facade.scan_results = [board("APP"), board("APP", board_id="final")]
    h.facade.decide_results = [{"verdict": "trade", "reason": None, "picks": [pick("APP", "long", 100.0, 98.0)],
                                "audit": []}]
    run(h, at(9, 10), at(9, 39))
    r.orb.note_price("APP", 101.1, h.clock.now + timedelta(seconds=30))
    r.orb.mark_positions(h.clock.now + timedelta(seconds=30), force_eval=True)
    c = card(r, h.clock.now)
    assert "Bought APP (long)" in c["orb"]["step"] and "picked" not in c["orb"]["step"]
    assert c["orb"]["unrealized_pnl"] == pytest.approx(454 * 0.9, abs=0.01)
    assert c["orb"]["total_pnl"] == pytest.approx(454 * 0.9, abs=0.01)
