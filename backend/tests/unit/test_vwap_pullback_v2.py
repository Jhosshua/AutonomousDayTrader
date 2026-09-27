"""Ride the Trend v2 (backend/app/strategies/vwap_pullback_v2.py).

Every transition and rejection of the section-2 state machine on constructed bars,
long and short, plus bar-policy edge cases, prefix invariance, restart rebuild,
legacy v1 checkpoint migration, the final-stop contract with the adaptation engine,
the trail-only bracket runner, and the relative-strength admission gate.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pytest

from backend.app.core.bracket import DynamicBracketManager as BracketManager, BracketStatus
from backend.app.core.persistence import decode_runtime_value, encode_runtime_value
from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies.adaptation import DynamicAdaptationEngine
from backend.app.strategies.base import SignalEvent, StrategyStatus
from backend.app.strategies.vwap_pullback import SymbolVWAPState
from backend.app.strategies import vwap_pullback_v2 as v2
from backend.app.strategies.vwap_pullback_v2 import (
    V2Params,
    V2SymbolState,
    VWAPPullbackV2Strategy,
    evaluate_bar,
)

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 9, 28, 9, 30, tzinfo=ET)  # Monday


def _bar(i: int, o: float, h: float, l: float, c: float, v: int = 1000, sym: str = "AAPL",
         ts: Optional[datetime] = None) -> BarEvent:
    return BarEvent(symbol=sym, open=o, high=h, low=l, close=c, volume=v,
                    timestamp=ts or (T0 + timedelta(minutes=i)))


def _base(i: int, v: int = 1000, sym: str = "AAPL") -> BarEvent:
    """Quiet, slowly oscillating background bar: VWAP ~100, std ~0.55, ATR ~0.35."""
    tp = 100 + 0.8 * math.sin(2 * math.pi * i / 40)
    return _bar(i, tp - 0.05, tp + 0.1, tp - 0.1, tp + 0.05, v, sym)


class Scenario:
    """Feeds bars one at a time and builds the next setup bar from the live features,
    so every scenario is a deterministic function of the bar list it produces."""

    def __init__(self, sym: str = "AAPL", p: Optional[V2Params] = None, emission=True, vix=1.0, tape=None,
                 tick_gates: bool = False):
        self.tape = tape
        self.tick_gates = tick_gates
        self.sym = sym
        self.p = p or V2Params()
        self.st = V2SymbolState()
        self.bars: List[BarEvent] = []
        self.events: List[Tuple[int, str, dict]] = []
        self.signals: List[Tuple[int, SignalEvent]] = []
        self.emission = emission
        self.vix = vix

    @property
    def i(self) -> int:
        return len(self.st.bars)

    def feed(self, bar: BarEvent):
        self.bars.append(bar)
        ev, sig = evaluate_bar(self.st, bar, self.p, emission_allowed=self.emission, vix_stop_mult=self.vix,
                               tape=self.tape, tick_gates=self.tick_gates)
        for e in ev:
            self.events.append((e["i"], e["event"], e["detail"]))
        if sig is not None:
            self.signals.append((len(self.st.bars) - 1, sig))
        return ev, sig

    def quiet(self, n: int, v: int = 1000):
        for _ in range(n):
            self.feed(_base(self.i, v, self.sym))

    def names(self, since: int = 0) -> List[str]:
        return [e[1] for e in self.events if e[0] >= since and e[1] != "FEATURE_UNAVAILABLE"]

    # --- building blocks relative to the live features ------------------------------
    def _feat(self):
        st = self.st
        std = st.std if st.std > 0.001 else (st.atr or 0.0)
        return st.vwap, std, (st.atr or 0.3)

    def impulse(self, side: str = "LONG", v: int = 1000):
        i = self.i
        vwap, std, atr = self._feat()
        if side == "LONG":
            h = max(b.high for b in self.st.bars[-30:]) + 1.0
            c = h - 0.1
            return self.feed(_bar(i, c - 0.05, h, c - 0.2, c, v, self.sym))
        l = min(b.low for b in self.st.bars[-30:]) - 1.0
        c = l + 0.1
        return self.feed(_bar(i, c + 0.05, c + 0.2, l, c, v, self.sym))

    def leg_bar(self, side: str = "LONG", v: int = 500):
        """A pullback bar that stays OUTSIDE the zone (no touch yet)."""
        i = self.i
        vwap, std, atr = self._feat()
        if side == "LONG":
            l = vwap + 0.6 * std + 0.05
            return self.feed(_bar(i, l + 0.15, l + 0.2, l, l + 0.1, v, self.sym))
        h = vwap - 0.6 * std - 0.05
        return self.feed(_bar(i, h - 0.15, h, h - 0.2, h - 0.1, v, self.sym))

    def touch(self, side: str = "LONG", v: int = 500, close_off_std: float = -0.2):
        """Candle intersects the zone; the extreme sits at close -/+ 0.1."""
        i = self.i
        vwap, std, atr = self._feat()
        if side == "LONG":
            c = vwap + close_off_std * std
            return self.feed(_bar(i, c + 0.15, c + 0.3, c - 0.1, c, v, self.sym))
        c = vwap - close_off_std * std
        return self.feed(_bar(i, c - 0.15, c + 0.1, c - 0.3, c, v, self.sym))

    def resume(self, side: str = "LONG", slope_atr: float = 0.6, v: int = 900):
        i = self.i
        vwap, std, atr = self._feat()
        ext = self.st.bars[self.st.ext_i] if self.st.ext_i >= 0 else self.st.bars[-1]
        prev = self.st.bars[-1]
        if side == "LONG":
            c = max(ext.close + slope_atr * atr, prev.close + 0.01)  # always an up close
            lo = max(c - 0.15, ext.low + 0.01)  # never a new low
            return self.feed(_bar(i, c - 0.1, c + 0.02, lo, c, v, self.sym))
        c = min(ext.close - slope_atr * atr, prev.close - 0.01)
        hi = min(c + 0.15, ext.high - 0.01)
        return self.feed(_bar(i, c + 0.1, hi, c - 0.02, c, v, self.sym))

    def full_setup(self, side: str = "LONG", leg_v: int = 500):
        self.impulse(side)
        self.leg_bar(side, leg_v)
        self.touch(side, leg_v)
        return self.resume(side)


# =============================================================================
# State machine, long side
# =============================================================================

def test_long_setup_signal_stop_and_target():
    s = Scenario()
    s.quiet(40)
    _, sig = s.full_setup("LONG")
    assert s.names(40) == ["IMPULSE", "PULLBACK", "RESUMING", "SIGNAL"]
    assert sig is not None and sig.side == OrderSide.BUY and sig.order_type == OrderType.MARKET
    assert sig.stop_is_final is True
    f = sig.features
    atr, entry = f["atr"], sig.entry_price
    dist = entry - sig.stop_loss
    expected = max(1.5 * atr, (entry - f["pullback_extreme"]) + 0.5 * atr, 0.004 * entry)
    assert dist == pytest.approx(expected, abs=2e-4)
    assert sig.take_profit_1 == pytest.approx(entry + dist, abs=2e-4)
    assert sig.take_profit_2 == sig.take_profit_1  # no second target
    assert f["pvr"] == pytest.approx(0.5)
    assert 0.004 * entry <= dist <= 0.04 * entry
    assert s.st.state == "IDLE" and s.st.last_emitted_i == s.i - 1
    assert s.st.cooldown_until_i == -1  # the cooldown starts only when main.py admits the order


def test_high_volume_pullback_is_rejected_until_the_next_impulse():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    s.leg_bar("LONG", 1500)
    s.touch("LONG", 1500)
    assert s.names(40) == ["IMPULSE", "HIGH_VOLUME_PULLBACK"]
    assert s.events[-1][2]["pvr"] == pytest.approx(1.5)
    assert s.st.state == "IDLE"
    # a resumption bar now means nothing
    s.resume("LONG")
    assert s.signals == []
    # a new impulse restarts the machine
    s.impulse("LONG")
    assert s.st.state == "IMPULSE"


def test_pullback_volume_between_thresholds_is_not_thin():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    s.leg_bar("LONG", 1000)
    s.touch("LONG", 1000)
    assert s.names(40) == ["IMPULSE", "PVR_NOT_THIN"]
    assert s.st.state == "IDLE"


def test_touch_on_the_bar_after_the_impulse_is_too_early():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    s.touch("LONG")
    assert s.names(40) == ["IMPULSE", "TOUCH_TOO_EARLY"]


def test_no_touch_within_twenty_bars_expires():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    for _ in range(20):
        s.leg_bar("LONG")
    assert s.names(40) == ["IMPULSE", "NO_TOUCH"]
    assert s.events[-1][0] == 40 + 20


def test_pullback_without_resumption_times_out():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    s.leg_bar("LONG")
    s.touch("LONG")
    ext_low = s.st.bars[s.st.ext_i].low
    for k in range(21):  # down closes, never a new low, never an up close
        i = s.i
        prev = s.st.bars[-1].close
        c = prev - 0.001
        s.feed(_bar(i, prev, prev + 0.01, max(ext_low + 0.01, c - 0.01), c, 500))
    assert "PULLBACK_TIMEOUT" in s.names(40)


def test_resumption_too_old_after_three_slow_bars():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    s.leg_bar("LONG")
    s.touch("LONG")
    for _ in range(3):
        s.resume("LONG", slope_atr=0.05)  # up closes, far too slow
    names = s.names(40)
    assert names[:3] == ["IMPULSE", "PULLBACK", "RESUMING"]
    assert names.count("SLOPE_TOO_SLOW") == 3
    s.resume("LONG", slope_atr=0.05)  # age 4
    assert s.names(40)[-1] == "RESUMPTION_TOO_OLD" and s.st.state == "IDLE"


def test_new_low_during_resumption_returns_to_pullback():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    s.leg_bar("LONG")
    s.touch("LONG")
    s.resume("LONG", slope_atr=0.05)
    assert s.st.state == "RESUMING"
    ext = s.st.bars[s.st.ext_i]
    i = s.i
    s.feed(_bar(i, ext.close, ext.close + 0.01, ext.low - 0.2, ext.low - 0.1, 500))
    assert s.names(40)[-1] == "NEW_EXTREME" and s.st.state == "PULLBACK"
    assert s.st.bars[s.st.ext_i].low == pytest.approx(ext.low - 0.2)


def test_chasing_far_above_vwap_is_rejected():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    s.leg_bar("LONG")
    s.touch("LONG")
    vwap, std, atr = s._feat()
    i = s.i
    c = vwap + 2.0 * std
    s.feed(_bar(i, c - 0.2, c + 0.02, c - 0.25, c, 900))
    assert s.names(40)[-1] == "CHASED" and s.st.state == "IDLE"


def test_stop_too_wide_rejects_the_signal(monkeypatch):
    s = Scenario(p=V2Params(stop_atr_mult=1.5))
    s.quiet(40)
    s.impulse("LONG")
    s.leg_bar("LONG")
    s.touch("LONG")
    # Force an ATR so large that 1.5 x ATR exceeds 4% of price, and a wide std so the
    # no-chase cap does not fire first (the stop check is the last gate).
    s.st.atr = 3.0
    s.st.cum_pv2 += 100.0 * s.st.cum_v
    s.resume("LONG", slope_atr=0.6)
    assert s.names(40)[-1] == "STOP_TOO_WIDE" and s.signals == []
    assert s.events[-1][2]["dist"] > 0.04 * s.events[-1][2]["entry"]


def test_vix_multiplier_widens_the_atr_stop_once():
    calm, wild = Scenario(vix=1.0), Scenario(vix=1.3)
    for s in (calm, wild):
        s.quiet(40)
        s.full_setup("LONG")
    d_calm = calm.signals[0][1].entry_price - calm.signals[0][1].stop_loss
    d_wild = wild.signals[0][1].entry_price - wild.signals[0][1].stop_loss
    atr = wild.signals[0][1].features["atr"]
    assert d_wild == pytest.approx(1.5 * atr * 1.3, abs=2e-4)
    assert d_wild > d_calm


# =============================================================================
# Emission: cooldown, daily budget, operator/mode switches, window
# =============================================================================

def test_cooldown_then_second_signal_then_daily_budget():
    s = Scenario()
    s.quiet(40)
    s.full_setup("LONG")
    assert len(s.signals) == 1
    s.st.cooldown_until_i = s.signals[0][0] + 15   # what notify_admitted does on SUBMITTED
    s.quiet(2)
    s.full_setup("LONG")  # inside the 15-bar cooldown
    assert s.names()[-1] == "EMISSION_DISABLED" and len(s.signals) == 1
    s.quiet(12)
    s.full_setup("LONG")
    assert len(s.signals) == 2
    s.st.admitted_today = 2  # main.py reports two admitted orders
    s.quiet(16)
    s.full_setup("LONG")
    assert len(s.signals) == 2 and s.names()[-1] == "EMISSION_DISABLED"
    assert s.events[-1][2]["admitted_today"] == 2


def test_emission_disabled_keeps_tracking_state():
    s = Scenario(emission=False)
    s.quiet(40)
    s.full_setup("LONG")
    assert s.signals == [] and s.names(40) == ["IMPULSE", "PULLBACK", "RESUMING", "EMISSION_DISABLED"]
    s.quiet(16)
    s.impulse("LONG")
    assert s.st.state == "IMPULSE"  # still running


def test_setup_discarded_at_1130_and_nothing_after():
    s = Scenario()
    s.quiet(117)               # continuous bars 09:30 .. 11:26
    s.impulse("LONG")          # i = 117 -> the 11:27 bar
    s.leg_bar("LONG")          # the 11:28 bar (completes 11:29)
    assert s.st.state == "IMPULSE"
    i = s.i
    s.touch("LONG")            # the 11:29 bar completes at 11:30: window closed, setup discarded
    assert s.events[-1] [1] == "WINDOW_CLOSED" and s.st.state == "IDLE"
    s.quiet(40)                # 11:31 .. 12:10: nothing is evaluated
    assert s.names(i + 1) == []


def test_earliest_possible_signal_is_bar_38_at_1008():
    s = Scenario()
    s.quiet(35)
    s.impulse("LONG")          # i = 35 (reference = volumes of bars 5..34)
    s.leg_bar("LONG")          # 36
    s.touch("LONG")            # 37
    s.resume("LONG")           # 38
    assert len(s.signals) == 1
    idx, sig = s.signals[0]
    assert idx == 38
    assert sig.timestamp.astimezone(ET).strftime("%H:%M") == "10:08"
    assert v2.FIRST_POSSIBLE_SIGNAL == "10:09"  # the 10:08 bar completes at 10:09


def test_no_evaluation_before_the_reference_exists():
    s = Scenario()
    s.quiet(34)
    s.impulse("LONG")  # i = 34: reference would start at bar 4 -> unavailable
    assert s.st.state == "IDLE"
    assert "IMPULSE" not in s.names()


# =============================================================================
# Bar policy
# =============================================================================

def test_duplicate_late_and_bad_bars_are_ignored():
    s = Scenario()
    s.quiet(42)
    n = s.i
    dup = s.st.bars[-1]
    s.feed(dup)
    assert s.i == n and s.names()[-1] == "DUP_BAR"
    old = _bar(5, 100, 100.2, 99.8, 100.1)
    s.feed(old)
    assert s.i == n and s.names()[-1] == "LATE_BAR"
    bad = _bar(n, 100, 99.0, 100.5, 100.1)  # high < low
    s.feed(bad)
    assert s.i == n and s.names()[-1] == "BAD_BAR"
    nan = _bar(n, float("nan"), 100.5, 99.5, 100.0)
    s.feed(nan)
    assert s.i == n and s.names()[-1] == "BAD_BAR"


def test_zero_volume_bar_is_kept_but_never_signals():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    s.leg_bar("LONG")
    s.touch("LONG")
    vwap, std, atr = s._feat()
    ext = s.st.bars[s.st.ext_i]
    i = s.i
    c = ext.close + 0.6 * atr
    s.feed(_bar(i, c - 0.1, c + 0.02, c - 0.15, c, 0))  # would have been the signal bar
    assert s.names()[-1] == "ZERO_VOLUME_BAR" and s.signals == [] and s.i == i + 1


def test_feed_gap_resets_an_active_setup():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    s.leg_bar("LONG")
    s.touch("LONG")
    assert s.st.state == "PULLBACK"
    i = s.i
    b = _base(i)
    s.feed(BarEvent(symbol="AAPL", open=b.open, high=b.high, low=b.low, close=b.close, volume=1000,
                    timestamp=s.st.bars[-1].timestamp + timedelta(minutes=7)))
    assert "FEED_GAP" in s.names(40) and s.st.state == "IDLE" and s.i == i + 1


def test_outside_bar_new_high_and_new_low_is_ambiguous():
    s = Scenario()
    s.quiet(40)
    s.impulse("LONG")
    i = s.i
    hi = max(b.high for b in s.st.bars[-30:]) + 1
    lo = min(b.low for b in s.st.bars[-30:]) - 1
    s.feed(_bar(i, 100, hi, lo, 100.05))
    assert s.names(40) == ["IMPULSE", "AMBIGUOUS_BAR"] and s.st.state == "IDLE"


def test_pre_session_and_post_session_bars_are_not_accepted():
    strat = VWAPPullbackV2Strategy()
    pre = BarEvent(symbol="AAPL", open=100, high=101, low=99, close=100, volume=1000,
                   timestamp=datetime(2026, 9, 28, 9, 29, tzinfo=ET))
    post = BarEvent(symbol="AAPL", open=100, high=101, low=99, close=100, volume=1000,
                    timestamp=datetime(2026, 9, 28, 16, 0, tzinfo=ET))
    assert strat.on_bar(pre) == [] and strat.on_bar(post) == []
    assert strat.symbol_states.get("AAPL") is None or strat.symbol_states["AAPL"].bars == []


# =============================================================================
# Short side mirrors
# =============================================================================

def test_short_setup_mirrors_the_long_rules():
    s = Scenario()
    s.quiet(40)
    _, sig = s.full_setup("SHORT")
    assert s.names(40) == ["IMPULSE", "PULLBACK", "RESUMING", "SIGNAL"]
    assert sig.side == OrderSide.SELL
    f = sig.features
    dist = sig.stop_loss - sig.entry_price
    assert dist > 0
    expected = max(1.5 * f["atr"], (f["pullback_extreme"] - sig.entry_price) + 0.5 * f["atr"], 0.004 * sig.entry_price)
    assert dist == pytest.approx(expected, abs=2e-4)
    assert sig.take_profit_1 == pytest.approx(sig.entry_price - dist, abs=2e-4)


def test_short_heavy_pullback_and_chase_are_rejected():
    s = Scenario()
    s.quiet(40)
    s.impulse("SHORT")
    s.leg_bar("SHORT", 1500)
    s.touch("SHORT", 1500)
    assert s.names(40) == ["IMPULSE", "HIGH_VOLUME_PULLBACK"]
    s2 = Scenario()
    s2.quiet(40)
    s2.impulse("SHORT")
    s2.leg_bar("SHORT")
    s2.touch("SHORT")
    vwap, std, atr = s2._feat()
    i = s2.i
    c = vwap - 2.0 * std
    s2.feed(_bar(i, c + 0.2, c + 0.25, c - 0.02, c, 900))
    assert s2.names(40)[-1] == "CHASED"


# =============================================================================
# Determinism: prefix invariance and restart rebuild
# =============================================================================

def _long_day_bars() -> List[BarEvent]:
    s = Scenario()
    s.quiet(40)
    s.full_setup("LONG")
    s.quiet(20)
    s.impulse("LONG")
    s.leg_bar("LONG", 1500)
    s.touch("LONG", 1500)
    s.quiet(10)
    s.full_setup("LONG")
    s.quiet(10)
    return s.bars


def _replay(bars: List[BarEvent], p=V2Params()):
    st = V2SymbolState()
    out: List[Tuple[int, str]] = []
    sigs: List[int] = []
    for b in bars:
        ev, sig = evaluate_bar(st, b, p, emission_allowed=True, vix_stop_mult=1.0, tick_gates=False)
        out.extend((e["i"], e["event"]) for e in ev)
        if sig:
            sigs.append(len(st.bars) - 1)
    return st, out, sigs


def test_prefix_invariance_appending_bars_never_changes_earlier_events():
    bars = _long_day_bars()
    n = 60
    _, short_ev, short_sigs = _replay(bars[:n])
    _, long_ev, long_sigs = _replay(bars)
    assert [e for e in long_ev if e[0] < n] == short_ev
    assert [i for i in long_sigs if i < n] == short_sigs
    assert len(long_sigs) == 2


def test_restart_at_every_bar_reproduces_the_uninterrupted_run():
    bars = _long_day_bars()
    full_st, full_ev, full_sigs = _replay(bars)
    p = V2Params()
    for r in range(1, len(bars)):
        st = V2SymbolState()
        for b in bars[:r]:  # what after_restore does: replay the stored session bars
            evaluate_bar(st, b, p, emission_allowed=True, vix_stop_mult=1.0, tick_gates=False)
        sigs, ev = [], []
        for b in bars[r:]:
            e, sig = evaluate_bar(st, b, p, emission_allowed=True, vix_stop_mult=1.0, tick_gates=False)
            ev.extend((x["i"], x["event"]) for x in e)
            if sig:
                sigs.append(len(st.bars) - 1)
        assert sigs == [i for i in full_sigs if i >= r], r
        assert ev == [e for e in full_ev if e[0] >= r], r
    assert full_st.state == "IDLE"


def test_after_restore_rebuilds_from_legacy_v1_state_and_from_v2_state():
    bars = _long_day_bars()
    k = 55
    strat = VWAPPullbackV2Strategy(require_tick_layers=False)
    strat.symbol_states["AAPL"] = SymbolVWAPState(session_bars=list(bars[:k]))  # what a v1 checkpoint holds
    report = strat.after_restore()
    st = strat.symbol_states["AAPL"]
    assert isinstance(st, V2SymbolState) and len(st.bars) == k
    assert report["rebuilt"]["AAPL"]["bars"] == k
    ref_st, _, _ = _replay(bars[:k])
    assert st.state == ref_st.state and st.cooldown_until_i == ref_st.cooldown_until_i
    # continue the day: the same signals as the uninterrupted run from k onward
    _, _, full_sigs = _replay(bars)
    got = []
    for idx, b in enumerate(bars[k:], start=k):
        if strat.on_bar(b):
            got.append(idx)
    assert got == [i for i in full_sigs if i >= k]
    # v2 -> v2 rebuild keeps the admitted budget
    strat.symbol_states["AAPL"].admitted_today = 2
    strat.after_restore()
    assert strat.symbol_states["AAPL"].admitted_today == 2


def test_checkpoint_roundtrip_of_strategy_state():
    bars = _long_day_bars()
    strat = VWAPPullbackV2Strategy(require_tick_layers=False)
    for b in bars[:50]:
        strat.on_bar(b)
    encoded = encode_runtime_value(strat.__dict__)
    decoded = decode_runtime_value(encoded)
    fresh = VWAPPullbackV2Strategy(require_tick_layers=False)
    fresh.__dict__.update(decoded)
    fresh.after_restore()
    assert isinstance(fresh.symbol_states["AAPL"], V2SymbolState)
    assert len(fresh.symbol_states["AAPL"].bars) == 50
    assert fresh.symbol_states["AAPL"].state == strat.symbol_states["AAPL"].state


# =============================================================================
# Strategy wrapper: mode, exclusions, session reset, card fields, research sink
# =============================================================================

def test_mode_off_and_paused_never_emit_but_keep_evaluating():
    for how in ("off", "paused"):
        strat = VWAPPullbackV2Strategy(mode="off" if how == "off" else "v2_live", require_tick_layers=False)
        if how == "paused":
            strat.pause()
        s = Scenario()
        s.quiet(40)
        s.full_setup("LONG")
        for b in s.bars:
            assert strat.on_bar(b) == []
        assert strat.event_counts_today.get("EMISSION_DISABLED") == 1
        assert strat.event_counts_today.get("IMPULSE") == 1


def test_excluded_symbols_are_never_evaluated():
    strat = VWAPPullbackV2Strategy(excluded_symbols=["tsla", "CDE"], require_tick_layers=False)
    s = Scenario(sym="TSLA")
    s.quiet(40)
    s.full_setup("LONG")
    for b in s.bars:
        assert strat.on_bar(b) == []
    assert "TSLA" not in strat.symbol_states and strat.event_counts_today == {}


def test_live_mode_emits_and_reports_card_fields():
    strat = VWAPPullbackV2Strategy(excluded_symbols=["TSLA", "CDE"], require_tick_layers=False)
    seen: List[dict] = []
    old = v2.EVENT_SINK
    v2.EVENT_SINK = seen.append
    try:
        s = Scenario()
        s.quiet(40)
        s.full_setup("LONG")
        sigs = [sig for b in s.bars for sig in strat.on_bar(b)]
    finally:
        v2.EVENT_SINK = old
    assert len(sigs) == 1 and sigs[0].strategy_id == "vwap_pullback"
    d = strat.to_dict()
    assert d["id"] == "vwap_pullback" and d["version"] == "v2" and d["mode"] == "v2_live"
    assert d["first_possible_signal"] == "10:09" and d["excluded_symbols"] == ["TSLA", "CDE"]
    assert d["today_counts"]["SIGNAL"] == 1 and d["signals_emitted_today"] == 1
    assert any(e["event"] == "SIGNAL" and e["strategy_id"] == "vwap_pullback" for e in seen)
    strat.reset_daily_stats()
    assert strat.symbol_states == {} and strat.to_dict()["today_counts"] == {}


def test_new_session_date_starts_fresh_state():
    strat = VWAPPullbackV2Strategy()
    s = Scenario()
    s.quiet(45)
    for b in s.bars:
        strat.on_bar(b)
    assert len(strat.symbol_states["AAPL"].bars) == 45
    nxt = BarEvent(symbol="AAPL", open=100, high=101, low=99, close=100, volume=1000,
                   timestamp=T0 + timedelta(days=1))
    strat.on_bar(nxt)
    assert len(strat.symbol_states["AAPL"].bars) == 1


# =============================================================================
# Final-stop contract with the adaptation engine
# =============================================================================

def _sig(stop_is_final: bool, entry=100.0, stop=99.0) -> SignalEvent:
    return SignalEvent("AAPL", OrderSide.BUY, OrderType.MARKET, entry, stop, entry + 1, entry + 2,
                       "vwap_pullback", 0.75, "x", stop_is_final=stop_is_final)


def test_final_stop_is_not_rescaled_by_vix_but_v1_style_stops_are():
    eng = DynamicAdaptationEngine()
    for mult in (0.8, 1.3):
        eng.current_stop_multiplier = mult
        assert eng.calculate_adapted_stop(_sig(True)) == 99.0
        scaled = eng.calculate_adapted_stop(_sig(False))
        assert scaled == pytest.approx(100.0 - 1.0 * mult, abs=1e-3)


def test_phase_gate_is_morning_only_for_ride_the_trend():
    eng = DynamicAdaptationEngine()
    assert eng.is_strategy_permitted("vwap_pullback", "OPEN_VOLATILITY_FLUSH")
    assert eng.is_strategy_permitted("vwap_pullback", "TREND_CONTINUATION")
    for phase in ("MIDDAY_CHOP", "AFTERNOON_PUSH", "POWER_HOUR", "EOD_FLATTEN", "PRE_MARKET"):
        assert not eng.is_strategy_permitted("vwap_pullback", phase), phase


# =============================================================================
# Bracket: TRAIL_ONLY runner
# =============================================================================

def test_trail_only_bracket_has_no_second_target_and_still_stops_out():
    bm = BracketManager()
    ts = T0
    b = bm.create_bracket("brk1", "AAPL", "LONG", 10, 100.0, 99.0, strategy_id="vwap_pullback",
                          target_1_r=1.0, runner_policy="TRAIL_ONLY")
    assert b.target_2_order_id is None and b.runner_policy == "TRAIL_ONLY"
    d = bm.activate_bracket_on_fill("brk1", 10, 100.2, ts)
    assert [o["type"] for o in d.orders_to_submit] == ["STOP", "LIMIT"]
    assert b.target_2_order_id is None
    assert b.target_1_price == pytest.approx(round(100.2 + 1.0 * (100.2 - 99.0), 2))
    assert b.target_1_qty == 5 and b.target_2_qty == 5
    # unscaled position stops out in full
    d2 = bm.on_child_order_fill(b.stop_order_id, 99.0, 10, ts)
    assert d2.bracket_status == BracketStatus.COMPLETED_STOP and b.remaining_qty == 0

    # second bracket: T1 fill -> breakeven, trail moves the stop, no T2 ever
    b2 = bm.create_bracket("brk2", "MSFT", "LONG", 10, 100.0, 99.0, strategy_id="vwap_pullback",
                           target_1_r=1.0, runner_policy="TRAIL_ONLY")
    bm.activate_bracket_on_fill("brk2", 10, 100.0, ts)
    d3 = bm.on_child_order_fill(b2.target_1_order_id, 101.0, 5, ts)
    assert d3.bracket_status == BracketStatus.TARGET_1_HIT and b2.remaining_qty == 5
    assert b2.current_stop_price >= 100.0
    assert b2.target_2_order_id is None
    d4 = bm.update_trailing_stop("MSFT", 103.0, 102.5, 0.5, ts)
    assert d4 is not None and b2.current_stop_price == pytest.approx(103.0 - 0.75)
    assert d4.orders_to_modify[0]["new_qty"] == 5
    d5 = bm.on_child_order_fill(b2.stop_order_id, b2.current_stop_price, 5, ts)
    assert d5.bracket_status == BracketStatus.COMPLETED_STOP and b2.remaining_qty == 0


def test_default_bracket_policy_still_creates_a_second_target():
    bm = BracketManager()
    b = bm.create_bracket("brk3", "AAPL", "LONG", 10, 100.0, 99.0, strategy_id="orb")
    assert b.runner_policy == "TARGET" and b.target_2_order_id == "t2_brk3"
    d = bm.activate_bracket_on_fill("brk3", 10, 100.0, T0)
    assert [o["type"] for o in d.orders_to_submit] == ["STOP", "LIMIT", "LIMIT"]


# =============================================================================
# Relative-strength admission gate (main.py)
# =============================================================================

def _feed_index(r, closes: List[float], sym: str, start: datetime):
    for k, c in enumerate(closes):
        r.market_filter.on_bar(BarEvent(symbol=sym, open=c, high=c + 0.1, low=c - 0.1, close=c, volume=1000,
                                        timestamp=start + timedelta(minutes=k)))


def test_relative_strength_gate_pass_fail_and_unavailable():
    from backend.app import main as r
    r.reset_runtime_state()
    r.set_simulation_mode(True)
    try:
        r.market_filter.reset_session()
        strat = r.vwap_strategy
        strat.reset_daily_stats()
        stock = [100 + 0.02 * k for k in range(40)]  # +0.78% over 39 bars
        for k, c in enumerate(stock):
            strat.on_bar(BarEvent(symbol="AAPL", open=c, high=c + 0.1, low=c - 0.1, close=c, volume=1000,
                                  timestamp=T0 + timedelta(minutes=k)))
        _feed_index(r, [500 + 0.01 * k for k in range(40)], "SPY", T0)   # +0.08%
        _feed_index(r, [400 + 0.01 * k for k in range(40)], "QQQ", T0)
        sig = SignalEvent("AAPL", OrderSide.BUY, OrderType.MARKET, stock[-1], stock[-1] - 1, stock[-1] + 1,
                          stock[-1] + 1, "vwap_pullback", 0.75, "x", timestamp=T0 + timedelta(minutes=39),
                          stop_is_final=True)
        stages: Dict = {}
        ok, detail = r._ride_the_trend_rs(sig, stages)
        assert ok and stages["rs_day"] > 0 and stages["rs_30"] > 0 and stages["rs_lag_s"] == 0

        short = SignalEvent("AAPL", OrderSide.SELL, OrderType.MARKET, stock[-1], stock[-1] + 1, stock[-1] - 1,
                            stock[-1] - 1, "vwap_pullback", 0.75, "x", timestamp=T0 + timedelta(minutes=39),
                            stop_is_final=True)
        ok, detail = r._ride_the_trend_rs(short, {})
        assert not ok and "not leading" in detail

        # SPY lagging by one minute is accepted; two minutes is unavailable.
        r.market_filter.reset_session()
        _feed_index(r, [500 + 0.01 * k for k in range(39)], "SPY", T0)
        ok, _ = r._ride_the_trend_rs(sig, {})
        assert ok
        r.market_filter.reset_session()
        _feed_index(r, [500 + 0.01 * k for k in range(38)], "SPY", T0)
        ok, detail = r._ride_the_trend_rs(sig, {})
        assert not ok and detail.startswith("RS_UNAVAILABLE")

        # stock history too short
        strat.reset_daily_stats()
        ok, detail = r._ride_the_trend_rs(sig, {})
        assert not ok and "RS_UNAVAILABLE" in detail
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)
