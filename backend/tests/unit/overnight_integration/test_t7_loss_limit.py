# @steered SNARE-2 2026-09-30
"""T7: the daily loss stop and the overnight result (D6, S14, R2-2).

An overnight result is booked on the day it sells and shows in that day's results, but it never
counts against that day's loss stop. Restarts across the sale (23:00 to 09:40, Friday to Monday
09:35) book it into the right day with the right stop, because the session boundary for "now"
runs before the overnight reconcile. Intraday losses still trip the stop."""
from datetime import date

import pytest

from backend.app.core.persistence import TradingStateStore
from backend.app.core.risk import BreakerStatus
from backend.tests.unit.overnight_integration.fakes import PRICES, MainOvernight, at, buy_night, open_sale, queue_sales

THU, FRI, MON = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)
SHARES = {"NVDA": 55, "IREN": 248, "HUT": 198}
BAD = {"NVDA": 150.0, "IREN": 34.0, "HUT": 43.0}       # 55*-30 + 248*-6 + 198*-7 = -4,524
GOOD = {"NVDA": 185.0, "IREN": 41.0, "HUT": 52.0}      # 55*5 + 248*1 + 198*2 = +919


def _result(prices):
    return round(sum(SHARES[s] * (prices[s] - PRICES[s]) for s in SHARES), 2)


def _daily_pnl(r):
    return round(r.account.equity - r.account.daily_starting_equity, 2)


def test_bad_night_does_not_trip_the_stop_at_0931_and_shows_in_results(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    open_sale(h, FRI, BAD, until=(9, 31, 5))
    r = h.r
    loss = _result(BAD)
    assert loss < -r.risk_engine.config.hard_max_daily_loss_dollars       # bigger than the whole limit
    assert _daily_pnl(r) == pytest.approx(loss)                            # results show it (account baseline)
    assert r.risk_engine.overnight_realized_today == pytest.approx(loss)
    h.quote("SPY", 500.0)                                                  # a quote evaluates the stop
    assert r.risk_engine.status == BreakerStatus.ARMED
    assert r.risk_engine.current_drawdown_dollars == 0.0
    assert r.account.status.value == "ACTIVE"
    trades = [t for t in r.pending_trade_records.values() if t["strategy_id"].startswith("overnight_")]
    assert {t["symbol"] for t in trades} == set(SHARES)
    assert all(t["session_date"] == FRI.isoformat() for t in trades)
    assert sum(t["realized_pnl"] for t in trades) == pytest.approx(loss)


def test_intraday_losses_still_trip_the_stop_after_a_bad_night(main_runtime):
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    open_sale(h, FRI, BAD, until=(9, 31, 5))
    r = h.r
    h.set(at(FRI, 10, 0))
    h.day_trade("AMD", 124, 100.0, 98.0)
    assert r.account.positions["AMD"].shares == 124
    h.quote("AMD", 88.0)                                                   # 124 x -12 = -1,488 intraday
    assert r.risk_engine.status == BreakerStatus.HALTED_DAILY_LOSS
    assert r.risk_engine.current_drawdown_dollars >= r.risk_engine.config.hard_max_daily_loss_dollars


def test_a_good_night_does_not_loosen_the_stop(main_runtime):
    """The offset works both ways: a $919 overnight win does not give the day $919 more room."""
    h = MainOvernight(main_runtime, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    open_sale(h, FRI, GOOD, until=(9, 31, 5))
    r = h.r
    assert _daily_pnl(r) == pytest.approx(_result(GOOD))
    h.set(at(FRI, 10, 0))
    h.day_trade("AMD", 124, 100.0, 98.0)
    h.quote("AMD", 88.0)
    assert r.risk_engine.status == BreakerStatus.HALTED_DAILY_LOSS


def _restart(h, store, now, monkeypatch):
    """A new robot process: memory gone, the durable store and Alpaca (the fake) kept. Then the
    real lifespan steps: restore, broker attached, session boundary + reconcile, startup compare."""
    r = h.r
    r.reset_runtime_state()
    r.state_store = store
    assert r._restore_checkpoint()
    r.alpaca_broker = h.broker
    r.set_simulation_mode(False)
    r.engine.broker_gate = h._gate
    h.set(now)
    monkeypatch.setattr(r.overnight, "start", lambda broker: h.build())
    r._startup_overnight(now)
    h.ctl = r.overnight.controller
    h.compare()


@pytest.mark.parametrize("prices", [GOOD, BAD], ids=["win", "loss"])
def test_down_2300_to_0940_books_the_sale_into_friday(main_runtime, tmp_path, monkeypatch, prices):
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40))
    store = TradingStateStore(str(tmp_path / "state.sqlite3"))
    r.state_store = store
    buy_night(h, THU)
    queue_sales(h, THU)
    h.run(at(THU, 23, 0), every=60)
    assert r._checkpoint_runtime("TEST_2300")
    # the robot is down: the opening auction fills the queued sales at 09:30
    h.set(at(FRI, 9, 30))
    h.alpaca.open_auction(prices)
    assert h.alpaca.signed_positions() == {}
    _restart(h, store, at(FRI, 9, 40), monkeypatch)
    result = _result(prices)
    assert r.last_session_date == FRI
    assert r.account.positions == {}
    assert r.broker_state["mismatch"] is False
    assert r.account.daily_starting_equity == pytest.approx(49_700.0)     # the day started with holds at buy price
    assert _daily_pnl(r) == pytest.approx(result)                          # booked into Friday's results
    assert r.risk_engine.overnight_realized_today == pytest.approx(result)
    assert r.risk_engine.status == BreakerStatus.ARMED and r.risk_engine.current_drawdown_dollars == 0.0
    assert r._checkpoint_runtime("TEST_AFTER")
    fri_trades = [t for t in store.list_trades_for_session(FRI.isoformat()) if t["strategy_id"].startswith("overnight_")]
    assert len(fri_trades) == 3 and sum(t["realized_pnl"] for t in fri_trades) == pytest.approx(result)
    assert store.list_trades_for_session(THU.isoformat()) == []
    summary = r._session_summary(FRI)
    assert summary["realized_pnl"] == pytest.approx(result)


@pytest.mark.parametrize("prices", [GOOD, BAD], ids=["win", "loss"])
def test_down_friday_to_monday_0935_books_the_sale_into_monday(main_runtime, tmp_path, monkeypatch, prices):
    r = main_runtime
    h = MainOvernight(r, at(FRI, 15, 40))
    store = TradingStateStore(str(tmp_path / "state.sqlite3"))
    r.state_store = store
    buy_night(h, FRI)
    queue_sales(h, FRI)
    assert {o["symbol"] for o in h.alpaca.live(side="sell")} == set(SHARES)
    assert r._checkpoint_runtime("TEST_FRIDAY_EVENING")
    h.set(at(MON, 9, 30))
    h.alpaca.open_auction(prices)
    _restart(h, store, at(MON, 9, 35), monkeypatch)
    result = _result(prices)
    assert r.last_session_date == MON
    assert r.account.positions == {} and r.broker_state["mismatch"] is False
    assert _daily_pnl(r) == pytest.approx(result)
    assert r.risk_engine.status == BreakerStatus.ARMED and r.risk_engine.current_drawdown_dollars == 0.0
    # an intraday loss on Monday still trips Monday's stop
    h.day_trade("AMD", 124, 100.0, 98.0)
    h.quote("AMD", 88.0)
    assert r.risk_engine.status == BreakerStatus.HALTED_DAILY_LOSS


def test_booking_waits_for_the_session_boundary(main_runtime, tmp_path):
    """R2-2 directly: a reconcile that ran before the boundary books nothing (it would land in
    the wrong day); the fill is booked right after the boundary runs."""
    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    h.set(at(FRI, 9, 30))
    h.alpaca.open_auction(BAD)
    h.set(at(FRI, 9, 40))
    assert r.last_session_date == THU
    r.overnight.reconcile(h.clock.now)
    assert set(r.account.positions) == set(SHARES)                         # nothing booked yet
    h.step()                                                               # the clock: boundary, then the tick
    assert r.last_session_date == FRI and r.account.positions == {}
    assert _daily_pnl(r) == pytest.approx(_result(BAD))
    assert r.risk_engine.status == BreakerStatus.ARMED


def test_safety_meter_reads_the_offset_adjusted_drawdown(main_runtime):
    import asyncio
    import json

    class Capture:
        def __init__(self):
            self.frames = []

        async def send_text(self, raw):
            self.frames.append(json.loads(raw))

    r = main_runtime
    h = MainOvernight(r, at(THU, 15, 40))
    buy_night(h, THU)
    queue_sales(h, THU)
    open_sale(h, FRI, BAD, until=(9, 31, 5))
    cap = Capture()
    r.ui_clients.add(cap)
    try:
        asyncio.run(r.broadcast_ui_state(force=True))
    finally:
        r.ui_clients.discard(cap)
    acct = cap.frames[-1]["account"]
    assert acct["daily_drawdown"] == pytest.approx(-_result(BAD))           # the account (results) drawdown
    assert acct["risk_drawdown"] == 0.0                                     # what the loss stop measures
    assert acct["overnight_realized_today"] == pytest.approx(_result(BAD))  # its own line
    assert cap.frames[-1]["overnight"]["state"]["realized_today"] == pytest.approx(_result(BAD))
