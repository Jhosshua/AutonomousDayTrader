"""Fixes from the phase-3 adversarial review (2026-09-28)."""
import threading

import pytest

from backend.app.core.orb_execution import RequestBudget
from backend.tests.unit.orb_integration.harness import MainOrb, pick




def test_api_orders_cannot_use_the_orb_strategy_id(main_runtime):
    import asyncio
    from fastapi import HTTPException
    r = main_runtime
    with pytest.raises(HTTPException) as err:
        asyncio.run(r.submit_order(r.OrderCreateRequest(symbol="APP", side="BUY", order_type="MARKET", qty=1,
                                                        stop_price=90.0, strategy_id="orb")))
    assert err.value.status_code == 400


def test_the_fallback_off_build_never_overwrites_the_unreadable_row(main_runtime, monkeypatch, tmp_path):
    from backend.app.core.persistence import TradingStateStore
    r = main_runtime
    r.state_store = TradingStateStore(str(tmp_path / "s.sqlite3"))
    try:
        evidence = {"version": 99, "positions": {"adt-orb-X-APP": {"symbol": "APP", "status": "OPEN"}}}
        r.state_store.save_orb_state("controller", evidence)
        monkeypatch.setattr(r.settings, "ORB_MODE", "shadow")
        monkeypatch.setattr(r.settings, "ORB_STATE_DIR", str(tmp_path / "orbs"))
        r.orb.start()
        assert r.orb.mode == "off"
        r.orb.controller._persist_quiet()
        r.orb.controller.reconcile_on_startup()
        r.orb.scheduler._persist()
        assert r.state_store.load_orb_state("controller") == evidence
        assert r.state_store.load_orb_state("scheduler") is None
    finally:
        r.orb.shutdown()
        r.state_store.close()


def test_a_stale_snapshot_can_never_overwrite_a_newer_one(main_runtime):
    """P2: persist_cb re-snapshots under a per-section lock, so an old state handed in late is ignored."""
    r = main_runtime
    h = MainOrb(r)
    stale = h.ctl.to_state()
    h.open_bracket()
    h.ctl.persist_cb(stale)                         # a slow thread delivers its old snapshot last
    assert r.orb._load("controller")["positions"]    # the saved row still has the open position


def test_open_risk_counts_the_full_planned_size_while_the_entry_is_still_working(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.alpaca.entry_mode, h.alpaca.partial_qty = "partial", 100
    h.open_bracket()
    assert h.ctl.own_qty("APP") == 100
    assert r.orb.open_risk() == pytest.approx(454 * 2.2, abs=0.01)


def test_broker_check_books_orb_fills_first(main_runtime):
    r = main_runtime
    h = MainOrb(r)
    h.clock.set(h.clock.now.replace(hour=9, minute=39))
    h.alpaca.prices["APP"] = 100.2
    assert h.ctl.execute([pick("APP", "long", 100.0, 98.0)], h.clock.now)["ok"]   # not synced yet
    assert "APP" not in r.account.positions
    r.alpaca_broker, r.engine.broker = h.broker, h.broker
    try:
        import asyncio
        asyncio.run(r._broker_reconcile_once())
        assert r.broker_state["mismatch"] is False and r.account.positions["APP"].shares == 454
    finally:
        r.alpaca_broker, r.engine.broker = None, None
