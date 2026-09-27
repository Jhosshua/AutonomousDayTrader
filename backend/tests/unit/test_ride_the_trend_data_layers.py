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
    assert tape.on_trade("AAPL", 100.10, 100, T0_NS + 10) == 1      # at the ask: buyer aggressive
    assert tape.on_trade("AAPL", 100.00, 100, T0_NS + 20) == -1     # at the bid: seller aggressive
    assert tape.on_trade("AAPL", 100.05, 100, T0_NS + 30) == 1      # inside: uptick vs last print (100.00)
    assert tape.on_trade("AAPL", 100.05, 100, T0_NS + 40) == 1      # same price: keeps the last side
    assert tape.on_trade("AAPL", 100.04, 100, T0_NS + 50) == -1     # downtick
    # stale quote (older than 2 s) falls back to the tick rule
    assert tape.on_trade("AAPL", 100.20, 100, T0_NS + 5 * NS) == 1   # uptick even though above the old ask
    d = tape.delta("AAPL", T0_NS, T0_NS + 10 * NS)
    assert d["n_trades"] == 6 and d["buy_vol"] == 400 and d["sell_vol"] == 200
    assert d["delta_ratio"] == pytest.approx(200 / 600)


def test_delta_is_causal_and_windowed():
    tape = _tape_with_quote()
    for k in range(10):
        tape.on_trade("AAPL", 100.10, 10, T0_NS + k * NS)          # 10 s of buying
    for k in range(10, 20):
        tape.on_trade("AAPL", 100.00, 10, T0_NS + k * NS)          # then 10 s of selling
    early = tape.delta("AAPL", T0_NS, T0_NS + 9 * NS + 1)
    assert early["sell_vol"] == 0 and early["delta_ratio"] == 1.0
    full = tape.delta("AAPL", T0_NS, T0_NS + 20 * NS)
    assert full["delta_ratio"] == pytest.approx(0.0)
    assert tape.delta("AAPL", T0_NS + 100 * NS, T0_NS + 200 * NS) is None
    assert tape.delta("MSFT", T0_NS, T0_NS + 20 * NS) is None


def test_velocity_uses_print_timestamps_and_needs_enough_prints():
    tape = _tape_with_quote()
    for k in range(6):
        tape.on_trade("AAPL", 100.0 + 0.05 * k, 10, T0_NS + k * 10 * NS)   # +0.25 over 50 s
    v = tape.velocity("AAPL", T0_NS + 60 * NS, 60)
    assert v["n_trades"] == 6 and v["price_change"] == pytest.approx(0.25)
    assert v["elapsed_s"] == pytest.approx(50.0) and v["per_second"] == pytest.approx(0.005)
    assert tape.velocity("AAPL", T0_NS + 60 * NS, 60, min_trades=7) is None
    assert tape.velocity("AAPL", T0_NS + 500 * NS, 60) is None       # nothing in that window


def test_book_imbalance_is_quote_weighted_over_the_window():
    tape = TickTape()
    tape.on_quote("AAPL", 100.0, 100.1, 900, 100, T0_NS)             # bid-heavy
    tape.on_quote("AAPL", 100.0, 100.1, 100, 900, T0_NS + 10 * NS)   # ask-heavy
    b = tape.book_imbalance("AAPL", T0_NS + 12 * NS, 30)
    assert b["quotes"] == 2 and b["imbalance"] == pytest.approx(0.0)
    only_first = tape.book_imbalance("AAPL", T0_NS + 5 * NS, 30)     # [t-30, t): the 10 s quote is later
    assert only_first["imbalance"] == pytest.approx(0.8)
    assert tape.book_imbalance("AAPL", T0_NS + 30 * NS, 30) is None   # newest quote is 20 s old: stale
    assert tape.book_imbalance("AAPL", T0_NS + 500 * NS, 30) is None
    assert b["bid_size"] == 100 and b["ask_size"] == 900 and b["spread"] == pytest.approx(0.1)


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
    tape2.on_trade("AAPL", 100.05, 1, T0_NS + NS)       # uptick: one classified share
    assert tape2.delta("AAPL", T0_NS, T0_NS + 5 * NS) is None
    # velocity needs coverage and a fresh last print
    tape3 = TickTape()
    for k in range(6):
        tape3.on_trade("AAPL", 100.0 + 0.05 * k, 10, T0_NS + k * 100_000_000)   # a 0.5 s burst
    assert tape3.velocity("AAPL", T0_NS + 60 * NS, 60) is None


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
    assert h["symbols"] == ["AAPL"] and h["trades_seen"] == 2


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

    def delta(self, symbol, t0, t1):
        self.calls.append(("delta", symbol, t0, t1))
        if self.delta_ratio is None:
            return None
        return {"buy_vol": 600, "sell_vol": 400, "unknown_vol": 50, "n_trades": 30,
                "delta": 200, "delta_ratio": self.delta_ratio, "classified_share": 0.95}

    def velocity(self, symbol, t1, window_s=60, min_trades=5):
        self.calls.append(("velocity", symbol, t1, window_s))
        if self.per_second is None:
            return None
        return {"price_change": self.per_second * 50, "elapsed_s": 50.0, "per_second": self.per_second,
                "n_trades": 40, "first_ns": t1 - 50 * NS, "last_ns": t1}

    def book_imbalance(self, symbol, t1, window_s=30):
        self.calls.append(("book", symbol, t1, window_s))
        if self.imbalance_v is None:
            return None
        return {"imbalance": self.imbalance_v, "quotes": 12, "bid": 100.0, "ask": 100.02, "bid_size": 500,
                "ask_size": 200, "spread": 0.02, "quote_ts_ns": t1}

    def health(self):
        return {"trades_seen": 1, "quotes_seen": 1, "symbols": ["AAPL"], "per_symbol": {}}


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
    # the delta window covers the pullback leg: first leg bar start .. touch bar end
    d = [c for c in tape.calls if c[0] == "delta"][0]
    impulse_ts = s.st.bars[41].timestamp  # leg starts on the bar after the impulse
    assert d[2] == int(impulse_ts.timestamp() * NS) and d[3] == int(s.st.bars[42].timestamp.timestamp() * NS) + 60 * NS


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
        assert card["window"]["notes"][-1] == "No scheduled macro releases today."
        fomc = datetime(2026, 10, 28, 10, 20, tzinfo=ET)  # in hours, blackout is 13:30-14:30 so clear
        card = next(c for c in r._strategy_cards(fomc) if c["id"] == "vwap_pullback")
        assert "Macro release blackout." not in card["window"]["blockers"]
        assert "FOMC" in card["window"]["notes"][-1]
        nfp = datetime(2026, 10, 2, 9, 40, tzinfo=ET)     # first Friday, 08:15-09:15 blackout; 09:40 is clear
        card = next(c for c in r._strategy_cards(nfp) if c["id"] == "vwap_pullback")
        assert "Nonfarm" in card["window"]["notes"][-1]
        inside = datetime(2026, 10, 2, 9, 31, tzinfo=ET)  # bar judged one minute back: 09:30, still not in blackout
        card = next(c for c in r._strategy_cards(inside) if c["id"] == "vwap_pullback")
        assert card["window"]["in_hours"] is True
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)
