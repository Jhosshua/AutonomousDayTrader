# @steered SNARE-2 2026-09-30
"""T6: symbol conflicts between the overnight holds and the day strategies (S1, S2, X6, S11, R2-14).

Each test drives the real main (validator, engine, bracket manager, flatten, runtime clock) with
one fake Alpaca behind every arm."""
import asyncio
from datetime import date

import pytest

from backend.app.core import overnight_schedule as osch
from backend.app.core.broker import BrokerReject
from backend.app.main import OrderCreateRequest, submit_order
from backend.tests.unit.overnight_integration.fakes import MainOvernight, at, buy_night, open_sale, queue_sales

THU, FRI = date(2026, 10, 1), date(2026, 10, 2)


def _prices(h):
    for sym, px in (("NVDA", 180.0), ("IREN", 40.0), ("HUT", 50.0)):
        h.price(sym, px)


def test_day_limit_entry_working_at_1546_is_cancelled_before_the_buy(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    _prices(h)
    out = asyncio.run(submit_order(OrderCreateRequest(symbol="NVDA", side="BUY", order_type="LIMIT", qty=10,
                                                      limit_price=170.0, stop_price=168.0, strategy_id="vwap_pullback")))
    assert out["status"] == "ACCEPTED"
    entry = h.r.engine.orders[out["order_id"]]
    h.run(at(THU, 15, 46, 10))
    assert entry.status.value == "CANCELLED"
    assert entry.audit_trail[-1].reason == "OVERNIGHT_RESERVED_ENTRY_CANCEL"
    assert "NVDA" not in h.r.bracket_manager.symbol_to_bracket
    assert [b["time_in_force"] for b in h.alpaca.posts("NVDA")] == ["cls"]


def test_day_stop_firing_at_154530_still_works(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 30))
    _prices(h)
    h.day_trade("NVDA", 20, 180.0, 178.0)
    assert h.r.account.positions["NVDA"].shares == 20
    h.run(at(THU, 15, 45, 30))
    assert h.r.overnight.reserves("NVDA")                  # reserved since 15:45
    h.quote("NVDA", 177.5)                                   # through the day trade's stop
    assert "NVDA" not in h.r.account.positions
    sells = [b for b in h.alpaca.posts("NVDA") if b["side"] == "sell"]
    assert len(sells) == 1 and int(sells[0]["qty"]) == 20
    assert h.alpaca.signed_positions().get("NVDA", 0) == 0


def test_day_trade_closed_first_recorded_then_the_buy_goes_in(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 30))
    _prices(h)
    h.day_trade("NVDA", 20, 180.0, 178.0)
    bracket_id = h.r.bracket_manager.symbol_to_bracket["NVDA"]
    h.price("NVDA", 181.0)
    h.run(at(THU, 15, 46, 30))
    posts = h.alpaca.posts("NVDA")
    kinds = [(b["side"], b["time_in_force"], b["client_order_id"].split("-")[4] if b["client_order_id"].startswith("adt-ovn") else "day")
             for b in posts]
    assert kinds[0] == ("buy", "day", "day")                 # the day trade's entry
    assert kinds[1] == ("sell", "day", "x6")                 # X6: closed through the controller's close
    assert kinds[2] == ("buy", "cls", "buy")                 # then the closing auction buy
    assert int(posts[1]["qty"]) == 20
    assert h.alpaca.wash_refusals == []
    trade = h.r.pending_trade_records.get(bracket_id)
    assert trade is not None and trade["quantity"] == 20 and trade["realized_pnl"] == pytest.approx(20.0)
    assert any(row.get("event") == "X6_DAY_TRADE_CLOSED_EARLY" for row in h.ctl.state["log"])
    assert h.r.bracket_manager.symbol_to_bracket.get("NVDA") is None


def test_no_buy_night_day_trade_is_closed_at_1555_as_today(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 30))
    _prices(h)
    h.day_trade("NVDA", 20, 180.0, 178.0)
    ok, text = h.r.overnight.set_no_buy_tonight(True, at(THU, 15, 40))
    assert ok, text
    h.run(at(THU, 15, 54, 55), every=5)
    assert h.night("NVDA", THU)["state"] == "SKIPPED"
    assert h.night("NVDA", THU)["reason"] == osch.OPERATOR_NO_BUY_TONIGHT
    assert not h.r.overnight.reserves("NVDA")
    assert h.r.account.positions["NVDA"].shares == 20        # not closed early on a no buy night
    h.run(at(THU, 15, 55, 10))
    assert "NVDA" not in h.r.account.positions
    sell = [b for b in h.alpaca.posts("NVDA") if b["side"] == "sell"]
    assert len(sell) == 1 and not sell[0]["client_order_id"].startswith("adt-ovn")
    assert [b for b in h.alpaca.posts("NVDA") if b["time_in_force"] == "cls"] == []


def test_0931_day_short_refused_while_the_sale_is_unbooked(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.alpaca.halted.add("NVDA")                              # the opening sale does not fill
    open_sale(h, FRI, until=(9, 31, 5))
    assert h.r.account.positions["NVDA"].shares == 55
    before = len(h.alpaca.posts("NVDA"))
    # a day strategy's short entry, the way execute_strategy_signal submits it: without S1 the
    # validator would classify a SELL against a LONG as an exit and sell the hold
    short = h.r.engine.create_order(symbol="NVDA", side=h.r.OrderSide.SELL, order_type=h.r.OrderType.MARKET,
                                    qty=30, stop_price=185.0, estimated_price=180.0, strategy_id="vwap_pullback")
    submitted = h.r.engine.submit_order(short.id)
    assert submitted.status.value == "REJECTED" and submitted.reject_reason.startswith("OVERNIGHT_HOLD")
    assert "9:30 AM open on Fri Oct 2" in submitted.reject_reason
    h.quote("NVDA", 180.0)
    assert h.r.account.positions["NVDA"].shares == 55
    # S2: the same rule at the broker choke point
    order = h.r.engine.create_order(symbol="NVDA", side=h.r.OrderSide.SELL, order_type=h.r.OrderType.MARKET,
                                    qty=30, strategy_id="vwap_pullback")
    with pytest.raises(BrokerReject, match="OVERNIGHT_HOLD"):
        h.r.engine._broker_execute(order, 30)
    day_sells = [b for b in h.alpaca.posts("NVDA")[before:] if not b["client_order_id"].startswith("adt-ovn")]
    assert day_sells == []


def test_entry_in_a_reserved_stock_is_refused_but_its_exit_is_not(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 30))
    _prices(h)
    h.day_trade("NVDA", 20, 180.0, 178.0)
    h.run(at(THU, 15, 45, 10))
    assert h.r.overnight.reserves("NVDA")
    add = h.r.engine.create_order(symbol="NVDA", side=h.r.OrderSide.BUY, order_type=h.r.OrderType.MARKET, qty=5,
                                  stop_price=178.0, estimated_price=180.0, strategy_id="MANUAL")
    ok, why = h.r.pre_trade_risk_validator(add, h.r.account)
    assert not ok and why.startswith("OVERNIGHT_RESERVED")
    too_big = h.r.engine.create_order(symbol="NVDA", side=h.r.OrderSide.SELL, order_type=h.r.OrderType.MARKET,
                                      qty=25, strategy_id="MANUAL_FLATTEN")
    ok, why = h.r.pre_trade_risk_validator(too_big, h.r.account)
    assert not ok and why.startswith("OVERNIGHT_RESERVED")
    exit_ = h.r.engine.create_order(symbol="NVDA", side=h.r.OrderSide.SELL, order_type=h.r.OrderType.MARKET,
                                    qty=20, strategy_id="MANUAL_FLATTEN")
    assert h.r.pre_trade_risk_validator(exit_, h.r.account)[0]


def test_orb_cannot_claim_a_reserved_or_held_stock(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 44))
    _prices(h)
    assert h.r.orb.adt_occupied("NVDA") is None
    h.run(at(THU, 15, 45, 5))
    assert "overnight" in (h.r.orb.adt_occupied("NVDA") or "")
    buy_night(h, THU, start=(15, 45, 6))
    assert h.r.orb.adt_occupied("NVDA")                      # held (ADT holds it) and reserved
    open_sale(h, FRI, until=(9, 31))
    assert "NVDA" not in h.r.account.positions
    assert not h.r.overnight.reserves("NVDA") and h.r.orb.adt_occupied("NVDA") is None   # released


def test_stock_held_by_orb_is_skipped(main_runtime, monkeypatch):
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    _prices(h)
    monkeypatch.setattr(h.r.orb, "owns", lambda sym: sym.upper() == "HUT")
    h.run(at(THU, 15, 49, 35), every=5)
    assert h.night("HUT", THU)["state"] == "SKIPPED"
    assert h.night("HUT", THU)["reason"] == osch.HELD_BY_OTHER_STRATEGY
    assert [b for b in h.alpaca.posts("HUT")] == []
    assert h.night("NVDA", THU)["state"] == "BUY_ACCEPTED"


def test_an_unsold_hold_does_not_use_a_day_slot_or_the_sector_limit(main_runtime):
    """S12: NVDA held (its opening sale halted) must not count toward the 3 day trade slots or the
    2 per sector limit (NVDA, AMD and MU are all Semiconductors)."""
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.alpaca.halted.update({"NVDA", "IREN", "HUT"})
    open_sale(h, FRI, until=(9, 40))
    assert set(h.r.account.positions) == {"NVDA", "IREN", "HUT"}
    h.day_trade("AMD", 50, 100.0, 98.0)
    h.day_trade("MU", 50, 101.0, 99.0)
    assert h.r.account.positions["AMD"].shares == 50
    assert h.r.account.positions["MU"].shares == 50
