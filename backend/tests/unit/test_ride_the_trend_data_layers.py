"""Ride the Trend v2 data layers: tick tape (Layer 1 aggression, Layer 2 top-of-book,
Layer 3 nanosecond velocity), macro calendar (Layer 4) and the gates that read them."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pytest

from backend.app.core.macro_calendar import MacroCalendar
from backend.app.core.tick_tape import NS, TickTape
from backend.app.models.events import BarEvent, OrderSide, OrderType, QuoteEvent, TradeEvent
from backend.app.strategies.base import SignalEvent
from backend.app.strategies import vwap_pullback_v2 as v2
from backend.app.strategies.vwap_pullback_v2 import V2Params, V2SymbolState, VWAPPullbackV2Strategy, evaluate_bar
from backend.tests.unit.test_vwap_pullback_v2 import Scenario, T0, _base

ET = ZoneInfo("America/New_York")
T0_NS = int(T0.timestamp() * NS)


# =============================================================================
# Tick tape
# =============================================================================

def _tape_with_quote(bid=100.0, ask=100.1, bs=500, as_=300, t=T0_NS) -> TickTape:
    tape = TickTape()
    tape.on_quote("AAPL", bid, ask, bs, as_, t)
    return tape


def test_prints_are_classified_at_ask_bid_or_by_tick_rule():
    tape = _tape_with_quote()
    assert tape.on_trade("AAPL", 100.10, 100, T0_NS + 10) == 1      # at the ask: buyer aggressive (quote)
    assert tape.on_trade("AAPL", 100.00, 100, T0_NS + 20) == -1     # at the bid: seller aggressive (quote)
    assert tape.on_trade("AAPL", 100.05, 100, T0_NS + 30) == 2      # inside: uptick vs last print (tick rule)
    assert tape.on_trade("AAPL", 100.05, 100, T0_NS + 40) == 2      # same price: keeps the last side
    assert tape.on_trade("AAPL", 100.04, 100, T0_NS + 50) == -2     # downtick
    # stale quote (older than 2 s) falls back to the tick rule
    assert tape.on_trade("AAPL", 100.20, 100, T0_NS + 5 * NS) == 2   # uptick even though above the old ask
    d = tape.delta("AAPL", T0_NS, T0_NS + 10 * NS, min_classified_share=0.0)
    assert d["n_trades"] == 6 and d["buy_vol"] == 400 and d["sell_vol"] == 200
    assert d["buy_quote"] == 100 and d["sell_quote"] == 100 and d["buy_tick"] == 300 and d["sell_tick"] == 100
    assert d["delta_ratio"] == pytest.approx(200 / 600) and d["quote_share"] == pytest.approx(200 / 600)
    # a third quote-classified clears the 20% data-quality floor; below it the window is unavailable
    assert tape.delta("AAPL", T0_NS, T0_NS + 10 * NS) is not None
    assert tape.delta("AAPL", T0_NS, T0_NS + 10 * NS, min_quote_share=0.5) is None


def test_delta_is_causal_and_windowed():
    tape = _tape_with_quote()
    for k in range(10):
        tape.on_quote("AAPL", 100.0, 100.1, 500, 300, T0_NS + k * NS - 1)
        tape.on_trade("AAPL", 100.10, 10, T0_NS + k * NS)          # 10 s of buying at the ask
    for k in range(10, 20):
        tape.on_quote("AAPL", 100.0, 100.1, 500, 300, T0_NS + k * NS - 1)
        tape.on_trade("AAPL", 100.00, 10, T0_NS + k * NS)          # then 10 s of selling at the bid
    early = tape.delta("AAPL", T0_NS, T0_NS + 9 * NS + 1)
    assert early["sell_vol"] == 0 and early["delta_ratio"] == 1.0
    full = tape.delta("AAPL", T0_NS, T0_NS + 20 * NS)
    assert full["delta_ratio"] == pytest.approx(0.0)
    assert tape.delta("AAPL", T0_NS + 100 * NS, T0_NS + 200 * NS) is None
    assert tape.delta("MSFT", T0_NS, T0_NS + 20 * NS) is None


def test_velocity_uses_print_timestamps_and_needs_enough_prints():
    tape = _tape_with_quote()
    for k in range(6):
        tape.on_trade("AAPL", 100.0 + 0.05 * k, 10, T0_NS + k * 10 * NS)   # +0.25 over 50 s (tick rule)
    v = tape.velocity("AAPL", T0_NS + 60 * NS, 60)
    assert v["n_trades"] == 6 and v["price_change"] == pytest.approx(0.25)
    assert v["elapsed_s"] == pytest.approx(50.0) and v["per_second"] == pytest.approx(0.005)
    assert tape.velocity("AAPL", T0_NS + 60 * NS, 60, min_trades=7) is None
    assert tape.velocity("AAPL", T0_NS + 500 * NS, 60) is None       # nothing in that window


def test_book_imbalance_is_time_weighted_over_the_window():
    tape = TickTape()
    tape.on_quote("AAPL", 100.0, 100.1, 900, 100, T0_NS)             # bid-heavy, in force 10 s
    tape.on_quote("AAPL", 100.0, 100.1, 100, 900, T0_NS + 10 * NS)   # ask-heavy, in force 2 s
    b = tape.book_imbalance("AAPL", T0_NS + 12 * NS, 12)
    # the state in force at the START of each second counts: second 0 has none, 1..10 the first quote,
    # 11 the second quote (stamped at exactly 10.0 s, so it governs from second 11)
    assert b["quotes"] == 2 and b["seconds"] == 11 and b["coverage"] == pytest.approx(11 / 12)
    assert b["imbalance"] == pytest.approx((900 * 10 + 100 - (100 * 10 + 900)) / 11000)
    assert b["bid_size"] == 100 and b["ask_size"] == 900 and b["spread"] == pytest.approx(0.1)
    only_first = tape.book_imbalance("AAPL", T0_NS + 5 * NS, 5)      # [t-5, t): the 10 s quote is later
    assert only_first["imbalance"] == pytest.approx(0.8)
    assert tape.book_imbalance("AAPL", T0_NS + 30 * NS, 30) is None   # newest quote is 20 s old: stale
    assert tape.book_imbalance("AAPL", T0_NS + 500 * NS, 30) is None
    # a window mostly without any quote state is not covered enough
    sparse = TickTape()
    sparse.on_quote("AAPL", 100.0, 100.1, 900, 100, T0_NS + 28 * NS)
    assert sparse.book_imbalance("AAPL", T0_NS + 30 * NS, 30) is None
    # time-weighted: 50 identical bid-heavy updates inside one second do not outweigh one quiet ask-heavy second
    burst = TickTape()
    for k in range(50):
        burst.on_quote("AAPL", 100.0, 100.1, 900, 100, T0_NS + k * 1_000_000)
    burst.on_quote("AAPL", 100.0, 100.1, 100, 900, T0_NS + 1 * NS)
    bb = burst.book_imbalance("AAPL", T0_NS + 3 * NS, 2)             # seconds 1 and 2: bid-heavy then ask-heavy
    assert bb["quotes"] == 1 and bb["imbalance"] == pytest.approx(0.0)
    # a quote from before an outage never seeds the book after it
    cut = TickTape()
    cut.on_quote("AAPL", 100.0, 100.1, 900, 100, T0_NS + 97 * NS)
    cut.note_disconnect(T0_NS + 98 * NS); cut.note_reconnect(T0_NS + 99 * NS)
    cut.on_quote("AAPL", 100.0, 100.1, 100, 900, T0_NS + 129 * NS)
    assert cut.book_imbalance("AAPL", T0_NS + 130 * NS, 30) is None


def test_tape_never_uses_a_later_quote_or_partial_second_data():
    tape = TickTape()
    # a quote that arrives with a LATER exchange stamp than the print must not classify it
    tape.on_quote("AAPL", 100.0, 100.1, 10, 10, T0_NS + 5 * NS)
    assert tape.on_trade("AAPL", 100.10, 5, T0_NS + 1 * NS) == 0   # print before the quote: unknown
    assert tape.on_trade("AAPL", 100.10, 5, T0_NS + 6 * NS) == 1   # after the quote: at the ask
    # a late quote never replaces the newer one
    assert tape.on_quote("AAPL", 99.0, 99.1, 10, 10, T0_NS + 2 * NS) is False
    assert tape.on_trade("AAPL", 100.10, 5, T0_NS + 7 * NS) == 1
    # half-open windows: a query ending at 10 s never sees the print stamped at 10.5 s
    tape.on_trade("AAPL", 100.10, 100, T0_NS + int(10.5 * NS))
    d = tape.delta("AAPL", T0_NS, T0_NS + 10 * NS)
    assert d["n_trades"] == 3 and d["buy_vol"] == 10
    # NaN and negative sizes are refused before anything is mutated
    assert tape.on_trade("AAPL", float("nan"), 5, T0_NS + 8 * NS) == 0
    assert tape.on_quote("AAPL", 100.0, 100.1, -1, 10, T0_NS + 9 * NS) is False
    assert tape.rejected >= 3
    # mostly unclassified volume is unavailable, not a pass
    tape2 = TickTape()
    tape2.on_trade("AAPL", 100.0, 1000, T0_NS)          # no quote, no prior print: unknown
    tape2.on_trade("AAPL", 100.05, 1, T0_NS + NS)       # uptick: one tick-inferred share
    assert tape2.delta("AAPL", T0_NS, T0_NS + 5 * NS) is None            # classified share ~0
    tape3 = TickTape()
    for k in range(10):
        tape3.on_trade("AAPL", 100.0 + 0.01 * k, 10, T0_NS + k * NS)    # all tick-rule, no quotes at all
    assert tape3.delta("AAPL", T0_NS, T0_NS + 10 * NS) is None            # quote share 0 < 20%
    assert tape2.delta("AAPL", T0_NS, T0_NS + 5 * NS, min_classified_share=0.0, min_quote_share=0.0)["quote_share"] == 0.0
    # velocity needs coverage and a fresh last print
    tape3 = TickTape()
    for k in range(6):
        tape3.on_trade("AAPL", 100.0 + 0.05 * k, 10, T0_NS + k * 100_000_000)   # a 0.5 s burst
    assert tape3.velocity("AAPL", T0_NS + 60 * NS, 60) is None


def test_late_second_is_kept_in_order_and_trimmed():
    tape = TickTape(keep_seconds=60)
    tape.on_trade("AAPL", 100.0, 1, T0_NS + 100 * NS)
    tape.on_trade("AAPL", 100.0, 1, T0_NS + 50 * NS)    # a late second, still inside retention
    keys = list(tape._syms["AAPL"].buckets.keys())
    assert keys == sorted(keys)
    tape.on_trade("AAPL", 100.0, 1, T0_NS + 200 * NS)   # watermark 200: cutoff 140 evicts 50 AND 100
    assert list(tape._syms["AAPL"].buckets.keys()) == [T0_NS // NS + 200]
    assert tape.coverage("AAPL")["evicted_by_age"] == 2


def test_tape_trims_old_seconds_and_rejects_bad_input():
    tape = TickTape(keep_seconds=60)
    tape.on_quote("AAPL", 100.0, 100.1, 1, 1, T0_NS)
    tape.on_trade("AAPL", 100.1, 5, T0_NS)
    tape.on_trade("AAPL", 100.1, 5, T0_NS + 200 * NS)
    assert tape.coverage("AAPL")["seconds"] == 1
    assert tape.on_trade("AAPL", 0.0, 5, T0_NS) == 0 and tape.on_trade("AAPL", 100.0, 0, T0_NS) == 0
    assert tape.on_quote("AAPL", 100.2, 100.1, 1, 1, T0_NS + 300 * NS) is False   # crossed quote ignored
    assert tape.quotes_seen == 1
    h = tape.health()
    # the crossed quote at +300 s advanced the watermark: the 200 s print aged out too
    assert h["symbols"] == ["AAPL"] and h["trades_seen"] == 2 and h["raw_prints_total"] == 0
    assert h["per_symbol"]["AAPL"]["evicted_by_age"] == 2


def test_relay_events_carry_nanoseconds_into_the_tape():
    tape = TickTape()
    q = QuoteEvent.from_relay_dict({"S": "AAPL", "ap": 336.07, "as": 40, "bp": 335.97, "bs": 440,
                                    "t": "2026-09-25T14:00:00.009910381Z"})
    t = TradeEvent.from_relay_dict({"S": "AAPL", "i": 1, "p": 336.07, "s": 40, "t": "2026-09-25T14:00:00.017028058Z"})
    assert q.timestamp_ns % NS == 9910381 and t.timestamp_ns % NS == 17028058
    tape.on_quote(q.symbol, q.bid_price, q.ask_price, q.bid_size, q.ask_size, q.timestamp_ns)
    assert tape.on_trade(t.symbol, t.price, t.size, t.timestamp_ns) == 1


# =============================================================================
# Macro calendar
# =============================================================================

def _cal(tmp_path: Path, payload: Dict[str, Any]) -> MacroCalendar:
    p = tmp_path / "macro.json"
    p.write_text(json.dumps(payload))
    return MacroCalendar(p)


def test_macro_blackout_windows_and_first_friday(tmp_path):
    cal = _cal(tmp_path, {
        "events": [{"name": "FOMC", "date": "2026-10-28", "time_et": "14:00", "before_min": 30, "after_min": 30}],
        "recurring": [{"name": "NFP", "rule": "first_friday", "time_et": "08:30", "before_min": 15, "after_min": 45}],
        "valid_through": "2026-12-31",
    })
    assert cal.check(datetime(2026, 10, 28, 13, 29, tzinfo=ET))[0] is True
    ok, why = cal.check(datetime(2026, 10, 28, 13, 31, tzinfo=ET))
    assert ok is False and why.startswith("MACRO_BLACKOUT: FOMC")
    assert cal.check(datetime(2026, 10, 28, 14, 31, tzinfo=ET))[0] is True
    # 2026-10-02 is the first Friday of October
    assert cal.check(datetime(2026, 10, 2, 9, 0, tzinfo=ET))[0] is False
    assert cal.check(datetime(2026, 10, 2, 9, 16, tzinfo=ET))[0] is True
    assert cal.check(datetime(2026, 10, 9, 9, 0, tzinfo=ET))[0] is True
    assert "FOMC" in cal.today_text(datetime(2026, 10, 28).date())


def test_missing_calendar_fails_closed(tmp_path):
    cal = MacroCalendar(tmp_path / "nope.json")
    ok, why = cal.check(datetime(2026, 9, 28, 10, 0, tzinfo=ET))
    assert ok is False and why.startswith("MACRO_UNAVAILABLE")
    assert "unavailable" in cal.today_text(datetime(2026, 9, 28).date())
    # readable but invalid data also fails closed
    for bad in ({}, {"events": [], "valid_through": "2026-12-31", "recurring": [{"rule": "second_tuesday", "time_et": "08:30"}]},
                {"events": [{"name": "x", "date": "2026-13-01", "time_et": "08:30"}], "valid_through": "2026-12-31"},
                {"events": [{"name": "x", "date": "2026-10-01", "time_et": "08:30", "before_min": -5}], "valid_through": "2026-12-31"}):
        assert _cal(tmp_path, bad).check(datetime(2026, 9, 28, 10, 0, tzinfo=ET))[0] is False
    # an authoritative empty schedule is fine until its horizon, refused after it
    cal = _cal(tmp_path, {"events": [], "valid_through": "2026-10-31"})
    assert cal.check(datetime(2026, 10, 30, 10, 0, tzinfo=ET))[0] is True
    assert cal.check(datetime(2026, 11, 2, 10, 0, tzinfo=ET))[0] is False
    # naive timestamps are UTC, like the rest of the bot
    cal = _cal(tmp_path, {"events": [{"name": "FOMC", "date": "2026-10-28", "time_et": "14:00", "before_min": 30, "after_min": 30}],
                          "valid_through": "2026-12-31"})
    assert cal.check(datetime(2026, 10, 28, 18, 0))[0] is False   # 18:00 UTC = 14:00 ET


def test_shipped_calendar_loads_and_is_clear_on_monday_0928():
    from backend.app.core.macro_calendar import macro_calendar
    assert macro_calendar.loaded and macro_calendar.error is None
    assert macro_calendar.check(datetime(2026, 9, 28, 10, 9, tzinfo=ET))[0] is True
    assert macro_calendar.check(datetime(2026, 10, 28, 13, 45, tzinfo=ET))[0] is False


# =============================================================================
# Gates inside the evaluator (FakeTape)
# =============================================================================

class FakeTape:
    def __init__(self, delta_ratio: Optional[float] = 0.1, per_second: Optional[float] = 0.01,
                 imbalance: Optional[float] = 0.3):
        self.delta_ratio = delta_ratio
        self.per_second = per_second
        self.imbalance_v = imbalance
        self.calls: List[tuple] = []

    def delta(self, symbol, t0, t1, min_classified_share=0.5):
        self.calls.append(("delta", symbol, t0, t1))
        if self.delta_ratio is None:
            return None
        return {"buy_vol": 600, "sell_vol": 400, "unknown_vol": 50, "n_trades": 30,
                "buy_quote": 500, "sell_quote": 350, "buy_tick": 100, "sell_tick": 50,
                "delta": int(round(self.delta_ratio * 1000)), "delta_ratio": self.delta_ratio,
                "classified_share": 0.95, "quote_share": 0.8,
                "complete": True, "coverage": {"complete": True}}

    def rolling_delta(self, symbol, t1, window_s=1800):
        self.calls.append(("rolling", symbol, t1, window_s))
        d = self.delta(symbol, t1 - window_s * NS, t1)
        if d is not None:
            d["window_s"] = window_s
        return d

    def velocity(self, symbol, t1, window_s=60, min_trades=5):
        self.calls.append(("velocity", symbol, t1, window_s))
        if self.per_second is None:
            return None
        return {"price_change": self.per_second * 50, "elapsed_s": 50.0, "per_second": self.per_second,
                "n_trades": 40, "first_ns": t1 - 50 * NS, "last_ns": t1, "complete": True}

    def book_imbalance(self, symbol, t1, window_s=30):
        self.calls.append(("book", symbol, t1, window_s))
        if self.imbalance_v is None:
            return None
        return {"imbalance": self.imbalance_v, "quotes": 12, "bid": 100.0, "ask": 100.02, "bid_size": 500,
                "ask_size": 200, "spread": 0.02, "quote_ts_ns": t1, "complete": True}

    def health(self):
        return {"trades_seen": 1, "quotes_seen": 1, "symbols": ["AAPL"], "per_symbol": {}, "raw_prints_total": 1}


def _gated(tape, side="LONG"):
    s = Scenario(tape=tape, tick_gates=True)
    s.quiet(40)
    s.full_setup(side)
    return s


def test_all_layers_pass_and_are_recorded_on_the_signal():
    tape = FakeTape(delta_ratio=0.1, per_second=0.01, imbalance=0.3)
    s = _gated(tape)
    assert s.names(40) == ["IMPULSE", "PULLBACK", "RESUMING", "SIGNAL"]
    sig = s.signals[0][1]
    f = sig.features
    assert f["layer1_pullback_delta_ratio"] == pytest.approx(0.1)
    assert f["layer2_book_imbalance"] == pytest.approx(0.3)
    assert f["layer3_tick_velocity_atr_per_min"] == pytest.approx(0.01 * 60 / f["atr"], rel=1e-3)
    assert f["tick_gates"] is True
    deltas = [c for c in tape.calls if c[0] == "delta"]
    # first call: the impulse bar itself (part-2 impulse aggression, recorded)
    assert deltas[0][2] == int(s.st.bars[40].timestamp.timestamp() * NS) and deltas[0][3] == deltas[0][2] + 60 * NS
    # second call: the pullback leg, first leg bar start .. touch bar end
    impulse_ts = s.st.bars[41].timestamp
    assert deltas[1][2] == int(impulse_ts.timestamp() * NS) and deltas[1][3] == int(s.st.bars[42].timestamp.timestamp() * NS) + 60 * NS
    assert f["layer3_impulse_delta_ratio"] == pytest.approx(0.1) and f["layer3_resumption_delta_ratio"] == pytest.approx(0.1)
    assert f["layer3_rolling_delta_ratio"] == pytest.approx(0.1) and f["enforced_gates"] == []


def test_aggressive_selling_pullback_is_a_trap():
    s = _gated(FakeTape(delta_ratio=-0.45))
    assert s.names(40) == ["IMPULSE", "AGGRESSIVE_PULLBACK"] and s.signals == []
    assert s.events[-1][2]["delta_ratio"] == pytest.approx(-0.45)


def test_short_side_trap_is_aggressive_buying():
    s = _gated(FakeTape(delta_ratio=+0.45), side="SHORT")
    assert s.names(40) == ["IMPULSE", "AGGRESSIVE_PULLBACK"]
    ok = _gated(FakeTape(delta_ratio=-0.2, per_second=-0.01, imbalance=-0.3), side="SHORT")
    assert ok.names(40)[-1] == "SIGNAL"


def test_missing_tick_data_fails_closed_at_each_gate():
    s = _gated(FakeTape(delta_ratio=None))
    assert s.names(40) == ["IMPULSE", "TICK_UNAVAILABLE"] and s.events[-1][2]["where"] == "pullback_delta"
    s = _gated(FakeTape(per_second=None))
    assert s.names(40) == ["IMPULSE", "PULLBACK", "RESUMING", "TICK_UNAVAILABLE"]
    assert s.events[-1][2]["where"] == "velocity"
    s = _gated(FakeTape(imbalance=None))
    assert s.names(40)[-1] == "BOOK_UNAVAILABLE"
    s = Scenario(tape=None, tick_gates=True)  # no tape installed at all
    s.quiet(40)
    s.full_setup("LONG")
    assert s.names(40) == ["IMPULSE", "TICK_UNAVAILABLE"]


def test_slow_tick_velocity_and_book_against_wait_but_do_not_kill_the_setup():
    s = _gated(FakeTape(per_second=0.0001))
    assert s.names(40)[-1] == "TICK_VELOCITY_LOW" and s.st.state == "RESUMING"
    s2 = Scenario(tape=FakeTape(imbalance=-0.2), tick_gates=True)
    s2.quiet(40)
    s2.impulse("LONG")
    s2.leg_bar("LONG")
    s2.touch("LONG", close_off_std=-0.4)   # deeper touch leaves room under the chase cap
    s2.resume("LONG")
    assert s2.names(40)[-1] == "BOOK_AGAINST" and s2.st.state == "RESUMING"
    # a later bar inside the age limit with the book now leaning our way signals
    s2.tape.imbalance_v = 0.3
    s2.resume("LONG", slope_atr=1.05)  # age 2: slope 0.525 ATR/bar
    assert s2.names(40)[-1] == "SIGNAL"


def test_rebuild_after_restart_skips_tick_gates_then_live_bars_enforce_them():
    strat = VWAPPullbackV2Strategy(require_tick_layers=True)
    s = Scenario(tick_gates=False)
    s.quiet(40)
    s.impulse("LONG")
    s.leg_bar("LONG")
    s.touch("LONG")                       # a setup in progress at the restart
    strat.symbol_states["AAPL"] = V2SymbolState()
    from backend.app.strategies.vwap_pullback import SymbolVWAPState
    strat.symbol_states["AAPL"] = SymbolVWAPState(session_bars=list(s.bars))
    report = strat.after_restore()
    # the setup in progress lost its tick evidence with the restart: it is discarded, not resumed
    assert strat.symbol_states["AAPL"].state == "IDLE"
    assert report["invalidated"] == [{"symbol": "AAPL", "state": "PULLBACK"}]
    old = v2.TAPE
    v2.TAPE = FakeTape(per_second=None)
    try:
        last = strat.symbol_states["AAPL"].bars[-1]
        i = len(strat.symbol_states["AAPL"].bars)
        c = last.close + 0.2
        out = strat.on_bar(BarEvent(symbol="AAPL", open=c - 0.1, high=c + 0.02, low=c - 0.15,
                                    close=c, volume=900, timestamp=T0 + timedelta(minutes=i)))
    finally:
        v2.TAPE = old
    assert out == [] and strat.event_counts_today.get("SIGNAL", 0) == 0


def test_restore_failure_closes_the_budget_and_previous_day_bars_are_ignored():
    strat = VWAPPullbackV2Strategy(require_tick_layers=False)

    class Broken:
        bars = property(lambda self: (_ for _ in ()).throw(RuntimeError("corrupt")))
    strat.symbol_states["AAPL"] = Broken()
    report = strat.after_restore()
    assert report["failed"][0]["symbol"] == "AAPL"
    assert strat.symbol_states["AAPL"].admitted_today == strat.max_signals_per_day
    # a bar from yesterday never resets today's state or budget
    s = Scenario(tick_gates=False)
    s.quiet(45)
    for b in s.bars:
        strat.on_bar(b)
    strat.symbol_states["AAPL"].admitted_today = 2
    stale = BarEvent(symbol="AAPL", open=100, high=101, low=99, close=100, volume=1000, timestamp=T0 - timedelta(days=1))
    assert strat.on_bar(stale) == []
    assert strat.symbol_states["AAPL"].admitted_today == 2 and len(strat.symbol_states["AAPL"].bars) == 45


def test_card_reports_the_data_layers():
    strat = VWAPPullbackV2Strategy()
    old = v2.TAPE
    v2.TAPE = FakeTape()
    try:
        d = strat.to_dict()["data_layers"]
    finally:
        v2.TAPE = old
    assert d["required"] is True and d["layer1_ticks"]["live"] and d["layer2_book"]["live"]
    assert d["layer2_book"]["depth"] == "top_of_book_nbbo" and d["layer3_timestamps"]["source"] == "exchange_nanoseconds"
    assert d["layer4_macro"]["calendar_loaded"] is True


# =============================================================================
# main.py: handlers feed the tape, macro gate rejects, RS unaffected
# =============================================================================

def test_main_handlers_fold_prints_and_quotes_into_the_tape_and_macro_gate_rejects():
    import asyncio
    from backend.app import main as r
    r.reset_runtime_state()
    r.set_simulation_mode(True)
    try:
        q = QuoteEvent.from_relay_dict({"S": "AAPL", "ap": 100.1, "as": 40, "bp": 100.0, "bs": 440,
                                        "t": "2026-09-28T14:00:00.000000100Z"})
        t = TradeEvent.from_relay_dict({"S": "AAPL", "i": 1, "p": 100.1, "s": 40, "t": "2026-09-28T14:00:00.000000200Z"})
        asyncio.run(r.handle_quote_event(q))
        asyncio.run(r.handle_trade_event(t))
        h = r.tick_tape.health()
        assert h["trades_seen"] == 1 and h["quotes_seen"] == 1 and h["symbols"] == ["AAPL"]
        assert r.tick_tape.delta("AAPL", t.timestamp_ns - NS, t.timestamp_ns + NS)["buy_vol"] == 40
        # macro gate: a v2 signal inside the FOMC window is refused before anything else
        sig = SignalEvent("AAPL", OrderSide.BUY, OrderType.MARKET, 100.0, 99.0, 101.0, 101.0, "vwap_pullback",
                          0.75, "x", timestamp=datetime(2026, 10, 28, 13, 45, tzinfo=ET), stop_is_final=True)
        asyncio.run(r.execute_strategy_signal(sig))
        rec = r.decision_log.recent(1, "vwap_pullback")[0]
        assert rec["outcome"] == "MACRO_BLACKOUT" and "FOMC" in rec["detail"]
        assert r.engine.working_orders == {}
        health = asyncio.run(r.health()) if hasattr(r, "health") else None
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)


def test_card_blocks_when_tick_data_is_missing_and_during_a_macro_blackout():
    from backend.app import main as r
    r.reset_runtime_state()
    r.set_simulation_mode(False)   # live semantics for the card; no broker calls happen here
    r.engine.broker = None
    r.vwap_strategy.resume()   # an earlier test may have left it paused
    try:
        r.tick_tape.reset()
        in_hours = datetime(2026, 9, 28, 10, 20, tzinfo=ET)
        card = next(c for c in r._strategy_cards(in_hours) if c["id"] == "vwap_pullback")
        assert "Waiting for tick and quote data." in card["window"]["blockers"]
        assert card["window"]["state"] == "BLOCKED"
        r.tick_tape.on_quote("AAPL", 100.0, 100.1, 1, 1, int(in_hours.timestamp() * NS))
        r.tick_tape.on_trade("AAPL", 100.1, 1, int(in_hours.timestamp() * NS))
        card = next(c for c in r._strategy_cards(in_hours) if c["id"] == "vwap_pullback")
        assert "Waiting for tick and quote data." not in card["window"]["blockers"]
        assert card["window"]["notes"][-2] == "No scheduled macro releases today."
        assert card["window"]["notes"][-1] == "Dollar and rates data not flowing yet."
        fomc = datetime(2026, 10, 28, 10, 20, tzinfo=ET)  # in hours, blackout is 13:30-14:30 so clear
        card = next(c for c in r._strategy_cards(fomc) if c["id"] == "vwap_pullback")
        assert "Macro release blackout." not in card["window"]["blockers"]
        assert "FOMC" in card["window"]["notes"][-2]
        nfp = datetime(2026, 10, 2, 9, 40, tzinfo=ET)     # first Friday, 08:15-09:15 blackout; 09:40 is clear
        card = next(c for c in r._strategy_cards(nfp) if c["id"] == "vwap_pullback")
        assert "Nonfarm" in card["window"]["notes"][-2]
        inside = datetime(2026, 10, 2, 9, 31, tzinfo=ET)  # bar judged one minute back: 09:30, still not in blackout
        card = next(c for c in r._strategy_cards(inside) if c["id"] == "vwap_pullback")
        assert card["window"]["in_hours"] is True
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)


def test_raw_store_coverage_cap_gaps_duplicates_conditions_and_late_prints():
    tape = TickTape(keep_seconds=120, raw_cap=5)
    for k in range(5):
        tape.on_quote("AAPL", 100.0, 100.1, 10, 10, T0_NS + k * NS - 1)
        tape.on_trade("AAPL", 100.1, 10, T0_NS + k * NS, trade_id=k + 1, exchange="Q", conditions=["@"])
    d = tape.delta("AAPL", T0_NS, T0_NS + 5 * NS)
    assert d["complete"] is True and d["n_trades"] == 5 and d["buy_quote"] == 50
    # a window starting before the first print is incomplete
    assert tape.delta("AAPL", T0_NS - 10 * NS, T0_NS + 5 * NS)["complete"] is False
    assert tape.rolling_delta("AAPL", T0_NS + 5 * NS, window_s=15) is None
    # the cap evicts the oldest print and the coverage says so
    tape.on_quote("AAPL", 100.0, 100.1, 10, 10, T0_NS + 5 * NS - 1)
    tape.on_trade("AAPL", 100.1, 10, T0_NS + 5 * NS, trade_id=6, exchange="Q")
    cov = tape.coverage_of("AAPL", T0_NS, T0_NS + 6 * NS)
    assert cov["complete"] is False and cov["reason"] == "window_starts_before_held_data" and cov["evicted_by_cap"] == 1
    assert tape.coverage_of("AAPL", T0_NS + 1 * NS, T0_NS + 6 * NS)["complete"] is True
    # the feed must have caught up to the window end (minus 5 s of slack)
    assert tape.coverage_of("AAPL", T0_NS + 1 * NS, T0_NS + 20 * NS)["reason"] == "feed_not_caught_up_to_window_end"
    # duplicate trade id is ignored
    assert tape.on_trade("AAPL", 100.1, 10, T0_NS + 5 * NS + 5, trade_id=6, exchange="Q") == 0
    assert tape.duplicates == 1
    # ineligible condition (sold out of sequence) is stored but never measured
    before = tape.delta("AAPL", T0_NS + 1 * NS, T0_NS + 6 * NS)["buy_vol"]
    tape.on_trade("AAPL", 100.1, 999, T0_NS + 5 * NS + 10, trade_id=7, exchange="Q", conditions=["Z"])
    assert tape.delta("AAPL", T0_NS + 1 * NS, T0_NS + 6 * NS)["buy_vol"] == before
    assert len(tape.prints("AAPL", T0_NS + 5 * NS, T0_NS + 6 * NS)) == 1 and tape.ineligible == 1
    # a feed gap inside the window makes it incomplete
    tape.note_gap(T0_NS + 3 * NS)
    assert tape.coverage_of("AAPL", T0_NS + 1 * NS, T0_NS + 6 * NS)["reason"] == "feed_outage_in_window"
    assert tape.coverage_of("AAPL", T0_NS + 4 * NS, T0_NS + 6 * NS)["complete"] is True
    # an outage interval (disconnect .. reconnect) makes every overlapping window incomplete, including
    # a window entirely inside it
    outage = TickTape()
    outage.on_quote("AAPL", 100.0, 100.1, 10, 10, T0_NS)
    outage.note_disconnect(T0_NS + 10 * NS)
    outage.note_reconnect(T0_NS + 40 * NS)
    outage.on_quote("AAPL", 100.0, 100.1, 10, 10, T0_NS + 60 * NS)
    assert outage.coverage_of("AAPL", T0_NS + 20 * NS, T0_NS + 30 * NS)["reason"] == "feed_outage_in_window"
    assert outage.coverage_of("AAPL", T0_NS + 45 * NS, T0_NS + 60 * NS)["complete"] is True
    assert outage.health()["outages"] == 1 and outage.health()["open_outage"] is False
    # a late print is classified against the quote in force at ITS timestamp, not the newest quote
    late = TickTape()
    late.on_quote("AAPL", 100.0, 100.1, 10, 10, T0_NS)
    late.on_quote("AAPL", 101.0, 101.1, 10, 10, T0_NS + 5 * NS)
    assert late.on_trade("AAPL", 100.1, 10, T0_NS + 1 * NS) == 1      # at the ask of the OLD quote
    assert late.on_trade("AAPL", 100.1, 10, T0_NS + 6 * NS) == -1     # at/below the bid of the NEW quote
    # a print arriving LATE (older than the newest print) is classified against its chronological
    # predecessor and does not disturb the live tick state
    late.on_trade("AAPL", 100.05, 10, T0_NS + 8 * NS)                 # inside the new spread: downtick vs 100.1
    assert late._syms["AAPL"].last_price == 100.05 and late._syms["AAPL"].last_tick_dir == -1
    assert late.on_trade("AAPL", 101.05, 10, T0_NS + 7 * NS) == 2     # late, inside the spread: vs predecessor 100.1 at 6 s = uptick
    assert late._syms["AAPL"].last_price == 100.05                    # live tick state untouched
    b7 = late._syms["AAPL"].buckets[(T0_NS + 7 * NS) // NS]
    assert list(b7.ts) == sorted(b7.ts)
    h = tape.health()
    assert h["duplicates"] == 1 and h["outages"] == 1 and h["per_symbol"]["AAPL"]["evicted_by_cap"] == 1


# =============================================================================
# Regime feed (Layer 4)
# =============================================================================

def _rbar(sym, minute, close, vol=1000):
    ts = T0 + timedelta(minutes=minute)
    return BarEvent(symbol=sym, open=close, high=close + 0.02, low=close - 0.02, close=close, volume=vol, timestamp=ts)


def test_regime_feed_returns_direction_freshness_and_evaluate():
    from backend.app.core.regime_feed import RegimeFeed
    feed = RegimeFeed(["XLK", "UUP", "IEF", "SHY"])
    for m in range(0, 45):
        feed.on_bar(_rbar("XLK", m, 100 + 0.01 * m))            # rising, above VWAP
        feed.on_bar(_rbar("IEF", m, 90 - 0.001 * m))            # drifting down (yields up a little)
        feed.on_bar(_rbar("SHY", m, 81.0))
        if m % 3 == 0:
            feed.on_bar(_rbar("UUP", m, 28.6 + 0.0005 * m))     # sparse dollar bars
    assert feed.on_bar(_rbar("AAPL", 1, 100)) is False          # not a regime symbol
    t_end = T0 + timedelta(minutes=45)                          # decision time: the 44 bar completes at 45
    r = feed.wallclock_return("XLK", t_end, 30)
    assert r is not None and r["return"] == pytest.approx((100 + 0.44) / (100 + 0.14) - 1, rel=1e-6)
    assert feed.direction("XLK", t_end)["above_vwap"] is True
    u = feed.wallclock_return("UUP", t_end, 30, max_age_s=300)
    assert u is not None and u["age_s"] <= 300
    assert feed.wallclock_return("UUP", t_end, 30, max_age_s=60) is None   # too stale at 2 minutes
    ev = feed.evaluate("AAPL", True, t_end, stock_return_30=0.005)
    assert ev["measures"]["sector_etf"] == "XLK" and ev["failed"] == [] and ev["unavailable"] == []
    weak = feed.evaluate("AAPL", True, t_end, stock_return_30=-0.01)
    assert "SECTOR_RS_FILTER" in weak["failed"]
    short = feed.evaluate("AAPL", False, t_end, stock_return_30=-0.01)
    assert "SECTOR_AGAINST" in short["failed"]                    # sector above VWAP blocks shorts
    # no sector mapping -> unavailable, not a pass
    ev2 = feed.evaluate("ZZZZ", True, t_end, stock_return_30=0.0)
    assert "SECTOR_DIRECTION" in ev2["unavailable"] and "SECTOR_RS" in ev2["unavailable"]
    # a dollar surge blocks longs and clears shorts
    surge = RegimeFeed(["XLK", "UUP", "IEF", "SHY"])
    for m in range(0, 45):
        surge.on_bar(_rbar("XLK", m, 100)); surge.on_bar(_rbar("IEF", m, 90)); surge.on_bar(_rbar("SHY", m, 81))
        surge.on_bar(_rbar("UUP", m, 28.0 * (1 + 0.0002 * m)))     # +0.6% over 30 bars
    assert "MACRO_WIND_AGAINST:dollar" in surge.evaluate("AAPL", True, t_end, 0.0)["failed"]
    assert "MACRO_WIND_AGAINST:dollar" not in surge.evaluate("AAPL", False, t_end, 0.0)["failed"]
    assert "surging" in surge.wind_text(t_end).lower()
    assert feed.health()["per_symbol"]["XLK"]["bars"] == 45


def test_part2_gates_enforce_only_when_named():
    from backend.app.strategies.vwap_pullback_v2 import V2Params
    weak = FakeTape(delta_ratio=0.02)     # positive but under every part-2 threshold
    s = Scenario(tape=weak, tick_gates=True)
    s.quiet(40)
    s.full_setup("LONG")
    assert s.names(40)[-1] == "SIGNAL"    # recorded only: nothing enforced
    measured = [e for e in s.events if e[1] == "RESUMPTION_MEASURED"][-1][2]
    assert measured["resumption_delta"] == pytest.approx(0.02) and measured["impulse_delta"] == pytest.approx(0.02)
    for gate, event in (("IMPULSE_DELTA", "IMPULSE_NOT_AGGRESSIVE"), ("RESUMPTION_DELTA", "RESUMPTION_NOT_AGGRESSIVE")):
        s2 = Scenario(tape=weak, tick_gates=True, p=V2Params(enforced_gates=(gate,)))
        s2.quiet(40)
        s2.full_setup("LONG")
        assert event in s2.names(40) and s2.signals == []
    neg = FakeTape(delta_ratio=-0.05)
    s3 = Scenario(tape=neg, tick_gates=True, p=V2Params(enforced_gates=("ROLLING_DELTA",), pullback_delta_min=-0.5))
    s3.quiet(40)
    s3.full_setup("LONG")
    assert "CUM_DELTA_AGAINST" in s3.names(40) and s3.signals == []
    # enforced and unavailable fails closed
    s4 = Scenario(tape=FakeTape(delta_ratio=None), tick_gates=True, p=V2Params(enforced_gates=("IMPULSE_DELTA",)))
    s4.quiet(40)
    s4.impulse("LONG")
    assert s4.names(40) == ["IMPULSE", "TICK_UNAVAILABLE"] and s4.st.state == "IDLE"


def test_regime_gate_in_admission_records_and_enforces():
    import asyncio
    from backend.app import main as r
    from backend.app.core.regime_feed import RegimeFeed
    r.reset_runtime_state()
    r.set_simulation_mode(True)
    old_enf = set(r.RIDE_THE_TREND_ENFORCED)
    try:
        r.vwap_strategy.reset_daily_stats()
        stock = [100 + 0.02 * k for k in range(40)]
        for k, c in enumerate(stock):
            r.vwap_strategy.on_bar(BarEvent(symbol="AAPL", open=c, high=c + 0.1, low=c - 0.1, close=c, volume=1000,
                                            timestamp=T0 + timedelta(minutes=k)))
        for m in range(40):
            r.regime_feed.on_bar(_rbar("XLK", m, 100 - 0.01 * m))   # sector falling, below VWAP
            r.regime_feed.on_bar(_rbar("UUP", m, 28.6)); r.regime_feed.on_bar(_rbar("IEF", m, 90.0)); r.regime_feed.on_bar(_rbar("SHY", m, 81.0))
        sig = SignalEvent("AAPL", OrderSide.BUY, OrderType.MARKET, stock[-1], stock[-1] - 1, stock[-1] + 1, stock[-1] + 1,
                          "vwap_pullback", 0.75, "x", timestamp=T0 + timedelta(minutes=39), stop_is_final=True)
        stages: Dict = {}
        outcome, detail = r._ride_the_trend_regime(sig, stages)
        assert outcome is None and "SECTOR_AGAINST" in stages["regime"]["failed"]     # recorded, not enforced
        assert stages["regime"]["measures"]["sector_etf"] == "XLK"
        r.RIDE_THE_TREND_ENFORCED.clear(); r.RIDE_THE_TREND_ENFORCED.add("SECTOR_DIRECTION")
        outcome, detail = r._ride_the_trend_regime(sig, {})
        assert outcome == "SECTOR_AGAINST"
        r.RIDE_THE_TREND_ENFORCED.clear(); r.RIDE_THE_TREND_ENFORCED.add("DOLLAR_WIND")
        r.regime_feed.reset_session()
        outcome, detail = r._ride_the_trend_regime(sig, {})
        assert outcome == "REGIME_UNAVAILABLE"
    finally:
        r.RIDE_THE_TREND_ENFORCED.clear(); r.RIDE_THE_TREND_ENFORCED.update(old_enf)
        r.reset_runtime_state()
        r.set_simulation_mode(False)


def test_tape_rejects_implausible_and_out_of_retention_events_before_mutating():
    tape = TickTape(keep_seconds=10)
    tape.on_quote("AAPL", 100.0, 100.1, 10, 10, T0_NS + 100 * NS)
    tape.on_trade("AAPL", 100.1, 5, T0_NS + 100 * NS)
    t = tape._syms["AAPL"]
    # a print older than the retention window is refused, nothing counted, nothing evicted
    assert tape.on_trade("AAPL", 100.1, 5, T0_NS + 89 * NS) == 0
    assert t.raw_count == 1 == sum(b.n_trades for b in t.buckets.values()) and tape.rejected == 1
    # an absurd timestamp (overflow, or an hour into the future) is refused before the watermark moves
    assert tape.on_trade("AAPL", 100.1, 5, 2 ** 63) == 0
    assert tape.on_trade("AAPL", 100.1, 5, T0_NS + 100 * NS + 2 * 3600 * NS) == 0
    assert tape.on_quote("AAPL", 100.0, 100.1, 10, 10, T0_NS + 100 * NS + 2 * 3600 * NS) is False
    assert t.watermark_ns == T0_NS + 100 * NS and t.raw_count == 1
    # accounting invariant survives a late print inside retention (no predecessor: unknown side, still stored)
    assert tape.on_trade("AAPL", 100.1, 5, T0_NS + 95 * NS) == 0
    assert t.raw_count == 2 == sum(b.n_trades for b in t.buckets.values())
    # a point mark during an open outage does not prevent the reconnect from closing it
    tape.note_disconnect(T0_NS + 200 * NS)
    tape.note_gap(T0_NS + 210 * NS)
    tape.note_reconnect(T0_NS + 220 * NS)
    assert tape.health()["open_outage"] is False and tape.health()["outages"] == 2
    tape.on_quote("AAPL", 100.0, 100.1, 10, 10, T0_NS + 230 * NS)
    assert tape.coverage_of("AAPL", T0_NS + 225 * NS, T0_NS + 230 * NS)["complete"] is True
    # a print far behind the watermark is stored but not tick-classified (no predecessor churn)
    far = TickTape()
    far.on_trade("AAPL", 100.0, 5, T0_NS + 1000 * NS)
    far.on_trade("AAPL", 100.2, 5, T0_NS + 1001 * NS)
    assert far.on_trade("AAPL", 100.3, 5, T0_NS + 900 * NS) == 0
    assert far._syms["AAPL"].raw_count == 3
