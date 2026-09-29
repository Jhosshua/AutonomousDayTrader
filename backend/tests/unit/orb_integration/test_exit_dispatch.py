"""Every ADT path that cancels orders or closes positions hands ORB's symbols to the ORB controller
(plan 8.2 / 9.2): it cancels ORB's parent + bracket legs, confirms them, then closes exactly ORB's
quantity with one market order. ADT never sends its own liquidation for an ORB symbol, never hits
Alpaca's one-sell-order refusal, and its book stays equal to Alpaca's.

Each test opens a real bracket on the fake Alpaca through main's OrbIntegration, disables ORB's own
11:00 time flatten (so only the ADT path under test can cause the exit), drives the path, then lets
ORB's scheduler run its supervisor passes."""
import asyncio
from datetime import timedelta

import pytest
from fastapi import HTTPException

from backend.app.core.flattening import FlatteningDirective, FlatteningPhase
from backend.app.models.events import OrderSide, OrderType
from backend.app.strategies.base import SignalEvent
from backend.tests.unit.orb_integration.harness import MainOrb, at


def opened(r, **kw):
    h = MainOrb(r, **kw)
    h.open_bracket()
    h.ctl.cfg["flatten"] = "23:59"        # only the ADT path under test may close the trade
    assert r.account.positions["APP"].shares == 454
    assert r.account.positions["APP"].strategy_id == "orb"
    assert r._local_signed_positions(r.account) == h.alpaca_positions() == {"APP": 454}
    return h


def exit_requested(h, reason):
    return any(e.get("kind") in ("exit_requested", "exit_all_requested") and e.get("reason") == reason
               for e in h.ctl.state["events"])


def test_bracket_fill_is_booked_into_adts_book_and_ledger(main_runtime):
    r = main_runtime
    h = opened(r)
    pos = r.account.positions["APP"]
    assert pos.avg_entry_price == pytest.approx(100.2)
    locals_ = [o for o in r.engine.orders.values() if o.strategy_id == "orb"]
    assert len(locals_) == 1 and locals_[0].execution_policy == "orb_bracket"
    assert locals_[0].id not in r.engine.working_orders and not locals_[0].broker_order_id
    # ADT's Alpaca position check agrees (no mismatch, entries stay open)
    r._compare_with_broker(h.alpaca_positions(), None)
    assert r.broker_state["mismatch"] is False
    # booking is idempotent: another sync books nothing twice
    r.orb.sync(h.clock.now)
    assert r.account.positions["APP"].shares == 454


def test_orb_fills_are_never_booked_again_by_adts_late_fill_settlement(main_runtime):
    r = main_runtime
    h = opened(r)
    r.engine.broker = h.broker
    try:
        n = len(h.alpaca.requests)
        r._settle_broker_orders()
        assert r.account.positions["APP"].shares == 454
        assert not [q for q in h.alpaca.requests[n:] if q[0] in ("POST", "DELETE")]
    finally:
        r.engine.broker = None


def test_circuit_breaker_routes_orb_to_its_controller_and_blocks_new_orb_entries(main_runtime):
    r = main_runtime
    h = opened(r)
    n = len(h.alpaca.requests)
    r._trip_circuit_breaker(h.clock.now)
    assert exit_requested(h, "CIRCUIT_BREAKER") and h.ctl.entries_blocked() == "CIRCUIT_BREAKER"
    h.run_supervisor()
    h.assert_orb_closed_through_its_controller(n)
    assert h.pos()["status"] == "CLOSED"


def test_adt_daily_loss_breaker_with_an_orb_bracket_open(main_runtime):
    """Blocking test 8.10: another ADT position loses past the daily limit -> ADT's breaker trips ->
    ORB exits its own bracket (legs first) and places nothing more today."""
    r = main_runtime
    h = opened(r)
    now = h.clock.now
    r.account.apply_fill("vw1", "AAPL", "BUY", 1000, 150.0, 0.0, now, strategy_id="vwap_pullback")
    r.account.update_market_price("AAPL", 148.5)          # -$1,500 on ADT's own trade
    n = len(h.alpaca.requests)
    status = r.risk_engine.evaluate_account_state(equity=r.account.equity, cash=r.account.cash,
                                                  realized_pnl=r.account.realized_pnl,
                                                  unrealized_pnl=r.account.unrealized_pnl, timestamp=now)
    assert status == r.BreakerStatus.HALTED_DAILY_LOSS
    r._trip_circuit_breaker(now)
    assert "AAPL" not in r.account.positions               # ADT liquidated its own trade locally
    assert r.orb.account_halt()                            # the controller sees ADT's stop
    h.run_supervisor()
    h.assert_orb_closed_through_its_controller(n)
    out = h.ctl.execute([{"symbol": "PLTR", "direction": "long", "entry": 50.0, "stop": 49.0}], h.clock.now)
    assert not out["ok"] and ("loss stop" in out["reason"] or "flattened" in out["reason"]), out


def test_flatten_symbol_helper_never_liquidates_an_orb_position(main_runtime):
    r = main_runtime
    h = opened(r)
    n = len(h.alpaca.requests)
    assert r._flatten_symbol("APP", 100.0, h.clock.now) == []
    assert exit_requested(h, "FLATTEN")
    h.run_supervisor()
    h.assert_orb_closed_through_its_controller(n)


@pytest.mark.parametrize("phase,flags,reason", [
    (FlatteningPhase.ORDER_PURGE, {"cancel_all_orders": False}, "EOD_ORDER_PURGE"),
    (FlatteningPhase.MANDATORY_LIQUIDATION, {"liquidate_all_positions": True}, "FORCED_FLAT"),
    (FlatteningPhase.ZERO_AUDIT, {"run_audit": True}, "EMERGENCY_SWEEP"),
])
def test_end_of_day_phases_route_orb_to_its_controller(main_runtime, phase, flags, reason):
    r = main_runtime
    h = opened(r)
    n = len(h.alpaca.requests)
    h.clock.set(at(15, 50))
    directive = FlatteningDirective(phase=phase, timestamp=h.clock.now, action_required="test", **flags)
    asyncio.run(r.handle_flattening_directive(directive))
    assert exit_requested(h, reason)
    h.run_supervisor()
    h.assert_orb_closed_through_its_controller(n)


def test_session_boundary_liquidation_leaves_orb_shares_to_the_controller(main_runtime):
    r = main_runtime
    h = opened(r)
    r._check_session_boundary(h.clock.now)                # first observation
    n = len(h.alpaca.requests)
    nxt = h.clock.now + timedelta(days=1)
    r._check_session_boundary(nxt)
    assert exit_requested(h, "SESSION_BOUNDARY_LIQUIDATION")
    assert not [o for o in r.engine.orders.values() if o.strategy_id == "SESSION_BOUNDARY_LIQUIDATION"]
    h.run_supervisor()
    h.assert_orb_closed_through_its_controller(n)


@pytest.mark.parametrize("reason", ["NEWS_CONTRADICTION_EXIT", "CONTRADICTION: negative news", "EXIT_SIGNAL"])
def test_generic_exit_and_contradiction_signals_route_orb(main_runtime, reason):
    r = main_runtime
    h = opened(r)
    n = len(h.alpaca.requests)
    sig = SignalEvent(symbol="APP", side=OrderSide.SELL, order_type=OrderType.MARKET, entry_price=100.0,
                      stop_loss=0.0, take_profit_1=0.0, take_profit_2=0.0, strategy_id="news_momentum",
                      confidence=1.0, reason=reason, timestamp=h.clock.now)
    asyncio.run(r.execute_strategy_signal(sig))
    assert any(e.get("kind") == "exit_requested" for e in h.ctl.state["events"])
    h.run_supervisor()
    h.assert_orb_closed_through_its_controller(n)


def test_manual_flatten_one_symbol_routes_orb(main_runtime):
    r = main_runtime
    h = opened(r)
    n = len(h.alpaca.requests)
    out = asyncio.run(r._execute_manual_flatten(["APP"], h.clock.now, None))
    assert out["rejected"] == [{"symbol": "APP", "reason": "Close requested; waiting for broker confirmation"}]
    assert exit_requested(h, "MANUAL_FLATTEN")
    assert h.ctl.entries_blocked() is None                  # one symbol does not stop ORB for the day
    h.run_supervisor()
    h.assert_orb_closed_through_its_controller(n)


def test_manual_flatten_all_routes_orb_and_blocks_its_entries_for_the_day(main_runtime):
    r = main_runtime
    h = opened(r)
    n = len(h.alpaca.requests)
    out = asyncio.run(r.manual_flatten(None))
    assert {"symbol": "APP", "reason": "Close requested; waiting for broker confirmation"} in out["rejected"]
    assert h.ctl.entries_blocked() == "MANUAL_FLATTEN_ALL"
    h.run_supervisor()
    h.assert_orb_closed_through_its_controller(n)


def test_api_cancel_order_refuses_orb_orders(main_runtime):
    r = main_runtime
    h = opened(r)
    local = next(o for o in r.engine.orders.values() if o.strategy_id == "orb")
    n = len(h.alpaca.requests)
    with pytest.raises(HTTPException) as err:
        asyncio.run(r.cancel_order(local.id))
    assert "ORB" in err.value.detail and "Close trade" in err.value.detail
    assert h.writes_after(n) == [] and r.account.positions["APP"].shares == 454


def test_manual_stop_tighten_is_refused_for_orb_with_a_plain_message(main_runtime):
    r = main_runtime
    h = opened(r)
    msg = r._orb_tighten_refusal("APP")
    assert msg and "held at Alpaca" in msg and "Close trade" in msg
    assert r._orb_tighten_refusal("NVDA") is None


def test_other_arms_cannot_send_any_order_on_an_orb_symbol(main_runtime):
    """Defense in depth: the pre-trade validator refuses ANY non-ORB order on an ORB symbol, entries
    and liquidations alike (a generic market sell would collide with the resting bracket legs)."""
    r = main_runtime
    h = opened(r)
    for strat, side in (("vwap_pullback", OrderSide.BUY), ("AUTO_FLATTEN", OrderSide.SELL),
                        ("MANUAL", OrderSide.SELL), ("swing_panic_dip", OrderSide.BUY)):
        o = r.engine.create_order("APP", side, OrderType.MARKET, 10, estimated_price=100.0, stop_price=98.0,
                                  strategy_id=strat,
                                  arm=r.TradingArm.SWING if strat == "swing_panic_dip" else r.TradingArm.INTRADAY)
        ok, why = r.pre_trade_risk_validator(o, r.account)
        assert not ok and why.startswith("ORB_OWNED"), (strat, why)
    assert h.alpaca.refused_403 == []
