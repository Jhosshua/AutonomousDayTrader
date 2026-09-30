# @steered SNARE-2 2026-09-30
"""T10: the broker compare never reports a mismatch that is only the overnight booking lagging (S13).

In the broker loop the controller books its own fills first, positions are re-read when it did,
then ADT's book is compared with Alpaca. Fills that land before the snapshot, fills that land
between the snapshot and the booking, and a Slow trade buy staged for the same 09:30 open."""
from datetime import date

import pytest

from backend.tests.unit.overnight_integration.fakes import MainOvernight, at, buy_night, queue_sales

THU, FRI = date(2026, 10, 1), date(2026, 10, 2)
OPEN = {"NVDA": 182.0, "IREN": 41.0, "HUT": 49.0, "MU": 101.0}


def _held_until_the_open(r):
    h = MainOvernight(r, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.run(at(FRI, 9, 29, 50), every=120)
    return h


def test_sale_filled_before_the_snapshot_is_booked_before_the_compare(main_runtime):
    h = _held_until_the_open(main_runtime)
    h.set(at(FRI, 9, 30, 1))
    h.alpaca.open_auction(OPEN)
    h.compare()                                 # no clock tick in between: the broker loop books first
    assert h.r.broker_state["mismatch"] is False, h.r.broker_state["mismatch_detail"]
    assert h.local() == h.alpaca.signed_positions() == {}


def test_sale_filled_after_the_snapshot_is_booked_and_positions_re_read(main_runtime, monkeypatch):
    h = _held_until_the_open(main_runtime)
    h.set(at(FRI, 9, 30, 1))
    real_sync = h.broker.sync

    def sync_then_fill():
        status = real_sync()                    # the snapshot still shows the holds
        h.alpaca.open_auction(OPEN)             # the auction fills right after it
        return status

    monkeypatch.setattr(h.broker, "sync", sync_then_fill)
    h.compare()
    assert h.r.broker_state["mismatch"] is False, h.r.broker_state["mismatch_detail"]
    assert h.local() == h.alpaca.signed_positions() == {}


def test_without_the_booking_step_the_same_race_would_be_a_mismatch(main_runtime, monkeypatch):
    """Negative control: proves the test above can fail (the S13 step is what prevents it)."""
    h = _held_until_the_open(main_runtime)
    h.set(at(FRI, 9, 30, 1))
    h.alpaca.open_auction(OPEN)

    async def no_booking(positions):
        return positions

    monkeypatch.setattr(h.r.overnight, "before_compare", no_booking)
    h.compare()
    assert h.r.broker_state["mismatch"] is True


def test_staged_slow_trade_buy_at_0930_with_the_sale(main_runtime):
    h = _held_until_the_open(main_runtime)
    r = h.r
    r.swing_staged_order_manager.stage_buy("MU", 10_000.0, 3.0, THU, "test staged Slow trade buy")
    h.set(at(FRI, 9, 30, 1))
    h.alpaca.open_auction(OPEN)
    h.price("MU", OPEN["MU"])
    r.swing_strategy_engine.execute_market_open({"MU": OPEN["MU"]}, h.clock.now)
    assert r.account.positions["MU"].shares > 0
    h.compare()
    assert r.broker_state["mismatch"] is False, r.broker_state["mismatch_detail"]
    assert h.local() == h.alpaca.signed_positions()
    assert set(h.local()) == {"MU"}


def test_split_before_0900_is_shown_as_a_split_not_a_mismatch(main_runtime):
    """O12 / S13: Alpaca applies a 2 for 1 NVDA split overnight. Until the 09:00 check adjusts
    the hold, the compare says split, entries are not paused; after it, the counts agree."""
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.run(at(FRI, 4, 0), every=300)
    h.alpaca.positions["NVDA"]["qty"] = 110
    h.alpaca.corporate_actions.append({"ca_type": "split", "ca_sub_type": "forward_split",
                                       "initiating_symbol": "NVDA", "target_symbol": "NVDA",
                                       "old_rate": "1", "new_rate": "2", "ex_date": FRI.isoformat()})
    h.compare()
    assert h.r.broker_state["mismatch"] is False
    assert "NVDA" in (h.r.broker_state.get("splits") or {})
    h.run(at(FRI, 9, 0, 30), every=30)
    assert h.r.account.positions["NVDA"].shares == 110
    assert h.r.account.positions["NVDA"].avg_entry_price == pytest.approx(90.0)
    h.compare()
    assert h.r.broker_state["mismatch"] is False and not h.r.broker_state.get("splits")


def test_an_unexplained_difference_on_a_hold_is_still_a_mismatch(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    h.alpaca.positions["NVDA"]["qty"] = 50
    h.compare()
    assert h.r.broker_state["mismatch"] is True
    assert h.r.broker_state["mismatch_detail"] == {"NVDA": {"bot": 55, "alpaca": 50}}
