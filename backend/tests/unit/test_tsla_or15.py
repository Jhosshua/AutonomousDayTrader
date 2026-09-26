from datetime import datetime, timedelta
from dataclasses import replace

import pytest

from backend.app.core.trading_windows import ET
from backend.app.models.events import BarEvent
from backend.app.strategies.tsla_or15_retest import TSLAOR15RetestStrategy, frozen_atr


START = datetime(2026, 9, 28, 9, 30, tzinfo=ET)


def bars(i, *, start=START, o=100, h=101, l=99, c=100):
    ts = start + timedelta(minutes=i)
    return (BarEvent("TSLA", o, h, l, c, 10000, ts),
            BarEvent("QQQ", 200, 201, 199, 200.5, 10000, ts))


def feed(s, pair, reverse=False):
    result = []
    for bar in pair[::-1] if reverse else pair:
        result.extend(s.on_completed_bar(bar, bar.timestamp + timedelta(minutes=1)))
    return result


def setup_signal(s, *, reverse=False, start=START):
    for i in range(15):
        assert not feed(s, bars(i, start=start), reverse)
    assert not feed(s, bars(15, start=start, o=100, h=102.5, l=100, c=102), reverse)
    return feed(s, bars(16, start=start, o=101.1, h=102, l=101, c=101.5), reverse)


@pytest.mark.parametrize("reverse", [False, True])
def test_signal_requires_later_bar_and_exact_pair(reverse):
    s = TSLAOR15RetestStrategy()
    result = setup_signal(s, reverse=reverse)
    assert len(result) == 1
    assert s.entry_due == START + timedelta(minutes=18)
    assert s.or_low == 99 and s.or_high == 101 and s.or_mid == 100
    assert result[0].target_qty == 1
    assert s.signal_consumed
    assert not feed(s, bars(17, o=101.1, h=102, l=101, c=101.5))


def test_atr_matches_frozen_unconventional_formula():
    b = [bars(0, o=100,h=101,l=99,c=100)[0], bars(1,o=90,h=91,l=89,c=90)[0], bars(2,o=91,h=92,l=90,c=91)[0]]
    assert frozen_atr(b) == pytest.approx((2 + 9 + 2) / 3)
    assert frozen_atr(b[:2]) == .45


@pytest.mark.parametrize("kind", ["duplicate", "missing", "stale", "unfinished", "invalid"])
def test_data_fault_ends_session(kind):
    s = TSLAOR15RetestStrategy()
    feed(s, bars(0))
    bar = bars(1)[0]
    now = bar.timestamp + timedelta(minutes=1)
    if kind == "duplicate":
        bar = bars(0)[0]
        now = bar.timestamp + timedelta(minutes=1)
    if kind == "missing":
        bar = bars(2)[0]
        now = bar.timestamp + timedelta(minutes=1)
    if kind == "stale":
        now += timedelta(seconds=11)
    if kind == "unfinished":
        now -= timedelta(seconds=1)
    if kind == "invalid":
        bar = replace(bar, high=98)
    s.on_completed_bar(bar, now)
    assert s.phase == "SKIPPED"


def test_no_feed_and_entry_deadline_fail_closed():
    s = TSLAOR15RetestStrategy()
    s.on_time_tick(START + timedelta(minutes=1, seconds=11))
    assert s.reason == "MISSING_REQUIRED_BAR"
    s = TSLAOR15RetestStrategy()
    setup_signal(s)
    s.on_time_tick(s.entry_due + timedelta(seconds=6))
    assert s.reason == "MISSED_ENTRY_WINDOW" and s.signal_consumed


@pytest.fixture
def runtime(monkeypatch):
    from backend.app import main as r
    monkeypatch.setattr(r, "OR15_NEW_ENTRIES", True)  # retired protocol, still tested
    r.reset_runtime_state()
    r.set_simulation_mode(True)
    for s in r.strategies:
        if s is not r.tsla_or15_strategy:
            s.pause()
    yield r
    r.reset_runtime_state()
    r.set_simulation_mode(False)
    for s in r.strategies:
        s.resume()


async def runtime_pair(r, pair, reverse=False):
    for bar in pair[::-1] if reverse else pair:
        await r.handle_bar_event(bar)


async def runtime_entry(r, start=START):
    for i in range(15):
        await runtime_pair(r, bars(i, start=start))
    await runtime_pair(r, bars(15, start=start, o=100, h=102.5, l=100, c=102))
    await runtime_pair(r, bars(16, start=start, o=101.1, h=102, l=101, c=101.5))
    assert not r.account.positions
    await runtime_pair(r, bars(17, start=start, o=102,h=103,l=101,c=102))
    assert not r.account.positions
    await runtime_pair(r, bars(18, start=start, o=102,h=103,l=101,c=102))
    return r.tsla_or15_strategy


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [("target",108), ("stop",99), ("both",99), ("gap",97)])
async def test_integrated_fixed_exit_prices(runtime, case, expected):
    r = runtime
    s = await runtime_entry(r)
    assert r.account.positions["TSLA"].shares == 1
    assert s.entry_price == 102 and s.target_price == 108
    bracket = r.or15_controller._bracket()
    assert bracket.current_stop_price == 99 and bracket.target_1_price == 108
    assert bracket.target_2_qty == 0 and not bracket.use_trailing_target_2
    params = {"target":dict(o=103,h=108,l=102,c=107), "stop":dict(o=102,h=103,l=99,c=100),
              "both":dict(o=102,h=109,l=98,c=104), "gap":dict(o=97,h=101,l=96,c=100)}[case]
    await runtime_pair(r, bars(19, **params))
    assert "TSLA" not in r.account.positions
    assert not [o for o in r.engine.working_orders.values() if o.symbol == "TSLA"]
    trade = next(t for t in r.pending_trade_records.values() if t["strategy_id"] == "tsla_or15_retest")
    assert trade["avg_entry_price"] == 102 and trade["avg_exit_price"] == expected
    assert trade["execution_mode"] == "offline_raw_open"
    assert s.phase == "CLOSED"


@pytest.mark.asyncio
async def test_integrated_terminal_open_precedes_high_low(runtime):
    r = runtime
    s = await runtime_entry(r)
    for i in range(19, 138):
        await runtime_pair(r, bars(i,o=102,h=103,l=101,c=102))
    assert r.account.positions
    await runtime_pair(r,bars(138,o=104,h=110,l=90,c=102))
    trade = next(iter(r.pending_trade_records.values()))
    assert trade["avg_exit_price"] == 104
    assert trade["exit_reason"] == "TIME_LIMIT"
    assert s.phase == "CLOSED"


@pytest.mark.asyncio
async def test_malformed_fill_bar_never_creates_trade(runtime):
    r = runtime
    for i in range(15):
        await runtime_pair(r,bars(i))
    await runtime_pair(r,bars(15,o=100,h=102.5,l=100,c=102))
    await runtime_pair(r,bars(16,o=101.1,h=102,l=101,c=101.5))
    await runtime_pair(r,bars(17,o=102,h=103,l=101,c=102))
    await runtime_pair(r,bars(18,o=102,h=103,l=-1,c=102))
    assert not r.account.positions and not r.pending_trade_records
    assert r.tsla_or15_strategy.reason == "INVALID_OHLCV"


def test_zero_volume_early_qqq_bar_is_valid_if_signal_vwap_exists():
    s = TSLAOR15RetestStrategy()
    t,q = bars(0)
    feed(s,(t,replace(q,volume=0)))
    for i in range(1,15):
        feed(s,bars(i))
    feed(s,bars(15,o=100,h=102.5,l=100,c=102))
    assert len(feed(s,bars(16,o=101.1,h=102,l=101,c=101.5))) == 1


@pytest.mark.asyncio
async def test_last_signal_minute_and_half_day_flatten(runtime):
    r = runtime
    start = datetime(2026,11,27,9,30,tzinfo=ET)
    for i in range(120):
        await runtime_pair(r,bars(i,start=start,o=100,h=101,l=99,c=100) if i<119 else bars(i,start=start,o=100,h=102.5,l=100,c=102))
    await runtime_pair(r,bars(120,start=start,o=101.1,h=102,l=101,c=101.5))
    assert r.tsla_or15_strategy.signal.timestamp.time().isoformat() == "11:30:00"
    await runtime_pair(r,bars(121,start=start,o=102,h=103,l=101,c=102))
    await runtime_pair(r,bars(122,start=start,o=102,h=103,l=101,c=102))
    assert r.tsla_or15_strategy.exit_due == start.replace(hour=12,minute=55)
    for i in range(123,205):
        await runtime_pair(r,bars(i,start=start,o=102,h=103,l=101,c=102))
    await runtime_pair(r,bars(205,start=start,o=104,h=110,l=90,c=102))
    trade = next(iter(r.pending_trade_records.values()))
    assert trade["exit_reason"] == "FORCED_FLAT" and trade["avg_exit_price"] == 104


@pytest.mark.asyncio
async def test_bulk_flatten_cancels_staged_signal(runtime):
    r = runtime
    setup_signal(r.tsla_or15_strategy)
    await r.manual_flatten()
    assert r.tsla_or15_strategy.phase == "SKIPPED"
    assert r.tsla_or15_strategy.reason == "MANUAL_CANCEL"


def test_small_clock_skew_does_not_end_session():
    # Railway bars arrive ~0.05 s after the minute; a host clock a little behind the
    # exchange makes a complete bar look early. That must not skip the whole day.
    s = TSLAOR15RetestStrategy()
    bar = bars(0)[0]
    s.on_completed_bar(bar, bar.timestamp + timedelta(minutes=1) - timedelta(seconds=0.2))
    assert s.phase == "BUILDING_RANGE" and len(s.bars["TSLA"]) == 1
