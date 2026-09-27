"""One full fake session for Ride the Trend v2 through the REAL main.py pipeline
(handle_bar_event -> strategy -> macro/RS/adaptation/risk -> market order -> bracket
TRAIL_ONLY -> T1 half -> breakeven + ATR trail -> stop or 15:55 flatten), with a
fake tick tape standing in for Layers 1-3 so every gate passes on purpose."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import List
from zoneinfo import ZoneInfo

import pytest

from backend.app.core.bracket import BracketStatus
from backend.app.models.events import BarEvent, OrderType
from backend.app.strategies import vwap_pullback_v2 as v2
from backend.tests.unit.test_ride_the_trend_data_layers import FakeProfileStore, FakeTape
from backend.tests.unit.test_vwap_pullback_v2 import Scenario, T0, _base as _base_bar
# main must be imported BEFORE the fixture swaps v2.TAPE: importing main installs the real tape.
from backend.app import main as r  # noqa: E402

ET = ZoneInfo("America/New_York")


class TrendScenario(Scenario):
    """Background bars drift up 4 cents a bar so the stock leads a flat SPY (RS gate)."""

    def quiet(self, n: int, v: int = 1000):
        import math
        for _ in range(n):
            i = self.i
            tp = 100 + 0.05 * i + 0.3 * math.sin(2 * math.pi * i / 40)
            self.feed(BarEvent(symbol=self.sym, open=tp - 0.05, high=tp + 0.1, low=tp - 0.1, close=tp + 0.05,
                               volume=v, timestamp=T0 + timedelta(minutes=i)))


def _index_bar(sym: str, i: int, base: float) -> BarEvent:
    # gently rising index: price above VWAP, EMA9 >= EMA21 -> BULLISH; SPY nearly flat so AAPL leads it
    c = base + 0.01 * i
    return BarEvent(symbol=sym, open=c - 0.01, high=c + 0.05, low=c - 0.05, close=c, volume=100000,
                    timestamp=T0 + timedelta(minutes=i))


async def _feed(r, bars: List[BarEvent]):
    for b in bars:
        await r.handle_bar_event(b)


def _session(r):
    r.reset_runtime_state()
    r.set_simulation_mode(True)
    r.relay_statuses["stock"] = "connected"
    for s in r.strategies:
        if s.strategy_id == "vwap_pullback":
            s.resume()
        else:
            s.pause()
    r.vwap_strategy.reset_daily_stats()
    r.market_filter.reset_session()


@pytest.fixture
def fake_tape():
    old, old_prof = v2.TAPE, v2.PROFILE
    tape = FakeTape(delta_ratio=0.15, per_second=0.02, imbalance=0.35)
    v2.TAPE = tape
    v2.PROFILE = FakeProfileStore()
    yield tape
    v2.TAPE, v2.PROFILE = old, old_prof


def test_full_fake_session_market_entry_trail_only_runner_and_flatten(fake_tape):
    _session(r)
    try:
        s = TrendScenario(tick_gates=False)
        s.quiet(40)
        s.full_setup("LONG")
        stock = list(s.bars)                      # signal on bar 43
        n = len(stock)
        index = [b for i in range(n) for b in (_index_bar("SPY", i, 500.0), _index_bar("QQQ", i, 400.0))]
        events = sorted(index + stock, key=lambda b: (b.timestamp, b.symbol != "AAPL"))
        # index bars must reach the filter before the stock bar of the same minute
        events = sorted(index + stock, key=lambda b: (b.timestamp, 0 if b.symbol in ("SPY", "QQQ") else 1))
        asyncio.run(_feed(r, events))

        decisions = r.decision_log.recent(10, "vwap_pullback")
        assert decisions and decisions[0]["outcome"] == "SUBMITTED", (decisions, r.vwap_strategy.event_counts_today, r.vwap_strategy.status, r.vwap_strategy.to_dict()["data_layers"], v2.TAPE)
        entry_orders = [o for o in r.engine.orders.values() if o.strategy_id == "vwap_pullback" and getattr(o, "bracket_role", None) is None]
        assert len(entry_orders) == 1 and entry_orders[0].order_type == OrderType.MARKET
        assert "AAPL" in r.account.positions
        b = next(x for x in r.bracket_manager.brackets.values() if x.strategy_id == "vwap_pullback")
        assert b.runner_policy == "TRAIL_ONLY" and b.target_2_order_id is None
        assert b.status == BracketStatus.ACTIVE
        fill = b.entry_price
        assert b.target_1_price == pytest.approx(round(fill + 1.0 * b.r_distance, 2))
        # working orders: one STOP for the whole qty, one LIMIT (T1) for half, no T2
        working = [o for o in r.engine.working_orders.values() if o.symbol == "AAPL"]
        assert sorted(o.order_type.value for o in working) == ["LIMIT", "STOP"]
        stop_o = next(o for o in working if o.order_type == OrderType.STOP)
        assert stop_o.qty == b.total_qty
        assert r.vwap_strategy.symbol_states["AAPL"].admitted_today == 1
        sig_row = [x for x in r.research_recorder.recent["signals"] if x["strategy_id"] == "vwap_pullback"][-1]
        f = sig_row["signal"]["features"]
        assert f["layer1_pullback_delta_ratio"] == pytest.approx(0.15) and f["layer2_book_imbalance"] == pytest.approx(0.35)
        assert sig_row["stages"]["macro"] == "MACRO_CLEAR" and sig_row["stages"]["rs_day"] > 0

        # T1 fill: a bar through the target -> half off, stop to breakeven, still no T2
        i = n
        t1 = b.target_1_price
        bar = BarEvent(symbol="AAPL", open=t1 - 0.05, high=t1 + 0.10, low=t1 - 0.08, close=t1 + 0.05, volume=2000,
                       timestamp=T0 + timedelta(minutes=i))
        asyncio.run(_feed(r, [_index_bar("SPY", i, 500.0), _index_bar("QQQ", i, 400.0), bar]))
        assert b.status == BracketStatus.TARGET_1_HIT and b.remaining_qty == b.total_qty - b.total_qty // 2
        assert b.current_stop_price >= fill and b.target_2_order_id is None
        # the runner trails: a strong bar lifts the stop
        i += 1
        hi = t1 + 2.0
        bar = BarEvent(symbol="AAPL", open=t1 + 0.1, high=hi, low=t1 + 0.05, close=hi - 0.1, volume=2000,
                       timestamp=T0 + timedelta(minutes=i))
        stop_before = b.current_stop_price
        asyncio.run(_feed(r, [_index_bar("SPY", i, 500.0), _index_bar("QQQ", i, 400.0), bar]))
        assert b.current_stop_price > stop_before
        working = [o for o in r.engine.working_orders.values() if o.symbol == "AAPL"]
        assert [o.order_type for o in working] == [OrderType.STOP] and working[0].remaining_qty == b.remaining_qty
        # runner stops out on the trailed stop
        i += 1
        st = b.current_stop_price
        bar = BarEvent(symbol="AAPL", open=st + 0.2, high=st + 0.25, low=st - 0.3, close=st - 0.2, volume=2000,
                       timestamp=T0 + timedelta(minutes=i))
        asyncio.run(_feed(r, [_index_bar("SPY", i, 500.0), _index_bar("QQQ", i, 400.0), bar]))
        assert b.status == BracketStatus.COMPLETED_STOP and "AAPL" not in r.account.positions
        assert r.account.realized_pnl > 0
        assert not [o for o in r.engine.working_orders.values() if o.symbol == "AAPL"]
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)


def test_full_fake_session_runner_is_flattened_at_1555(fake_tape):
    _session(r)
    try:
        s = TrendScenario(tick_gates=False)
        s.quiet(40)
        s.full_setup("LONG")
        stock = list(s.bars)
        n = len(stock)
        index = [b for i in range(n) for b in (_index_bar("SPY", i, 500.0), _index_bar("QQQ", i, 400.0))]
        events = sorted(index + stock, key=lambda b: (b.timestamp, 0 if b.symbol in ("SPY", "QQQ") else 1))
        asyncio.run(_feed(r, events))
        b = next(x for x in r.bracket_manager.brackets.values() if x.strategy_id == "vwap_pullback")
        assert b.status == BracketStatus.ACTIVE
        # drift sideways above the stop until the close: no stop, no T1
        last = stock[-1].close
        i = n
        bars = []
        t = T0 + timedelta(minutes=i)
        while t.astimezone(ET).time() < datetime.min.time().replace(hour=15, minute=57):
            bars.append(BarEvent(symbol="AAPL", open=last, high=last + 0.02, low=last - 0.02, close=last, volume=1500, timestamp=t))
            bars.append(_index_bar("SPY", i, 500.0))
            bars.append(_index_bar("QQQ", i, 400.0))
            i += 1
            t = T0 + timedelta(minutes=i)
        asyncio.run(_feed(r, bars))
        assert b.status == BracketStatus.COMPLETED_FLATTEN, b.status
        assert "AAPL" not in r.account.positions
        assert not [o for o in r.engine.working_orders.values() if o.symbol == "AAPL"]
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)


def test_mode_off_places_nothing_but_still_manages_an_open_position(fake_tape):
    _session(r)
    try:
        s = TrendScenario(tick_gates=False)
        s.quiet(40)
        s.full_setup("LONG")
        stock = list(s.bars)
        n = len(stock)
        index = [b for i in range(n) for b in (_index_bar("SPY", i, 500.0), _index_bar("QQQ", i, 400.0))]
        events = sorted(index + stock, key=lambda b: (b.timestamp, 0 if b.symbol in ("SPY", "QQQ") else 1))
        asyncio.run(_feed(r, events))
        b = next(x for x in r.bracket_manager.brackets.values() if x.strategy_id == "vwap_pullback")
        assert b.status == BracketStatus.ACTIVE
        r.vwap_strategy.mode = "off"
        # a fresh setup after the cooldown emits nothing
        s2 = TrendScenario(tick_gates=False)   # private state: builds the continuation bars only
        for prior in stock:
            s2.feed(prior)
        before = len(r.engine.orders)
        s2.quiet(16)
        s2.impulse("LONG"); s2.leg_bar("LONG"); s2.touch("LONG"); s2.resume("LONG")
        new_bars = s2.bars[n:]
        idx = [b for k in range(n, n + len(new_bars)) for b in (_index_bar("SPY", k, 500.0), _index_bar("QQQ", k, 400.0))]
        # replay only through the wrapper + main for the new bars
        ev = sorted(idx + new_bars, key=lambda b: (b.timestamp, 0 if b.symbol in ("SPY", "QQQ") else 1))
        asyncio.run(_feed(r, ev))
        entry_orders = [o for o in r.engine.orders.values() if o.strategy_id == "vwap_pullback" and getattr(o, "bracket_role", None) is None]
        assert len(entry_orders) == 1
        assert r.vwap_strategy.event_counts_today.get("EMISSION_DISABLED", 0) >= 1
        # the open position's stop still works in off mode
        stop = b.current_stop_price
        i = n + len(new_bars)
        bar = BarEvent(symbol="AAPL", open=stop + 0.1, high=stop + 0.15, low=stop - 0.3, close=stop - 0.2, volume=2000,
                       timestamp=T0 + timedelta(minutes=i))
        asyncio.run(_feed(r, [_index_bar("SPY", i, 500.0), _index_bar("QQQ", i, 400.0), bar]))
        assert b.status == BracketStatus.COMPLETED_STOP and "AAPL" not in r.account.positions
    finally:
        r.vwap_strategy.mode = "v2_live"
        r.reset_runtime_state()
        r.set_simulation_mode(False)
