# @steered SNARE-2 2026-09-29
"""Why these tests exist. An edge found by a backtest is only as honest as its fill model and its
causality. Each test below would fail if the simulator quietly gave itself a better price than a
live bot could get, or used a bar it could not have seen yet."""
import math
import os

import numpy as np
import pytest

import lib
from families import ALL_FAMILIES as FAMILIES, RUNNERS
from lib import Tape, exit_trade, fill_index, trade_row


def synthetic(price=100.0, days=3, n=390):
    D = days
    O = np.full((D, n), price)
    H = O + 0.05
    L = O - 0.05
    C = O.copy()
    shape = (D, n)
    return Tape("TEST", np.arange(20240102, 20240102 + D), O, H, L, C, np.ones(shape) * 1000, O.copy(),
                np.full(D, n), O[:, 0].copy(), C[:, -1].copy(), np.r_[np.nan, C[:-1, -1]],
                np.r_[np.nan, H[:-1].max(1)], np.r_[np.nan, L[:-1].min(1)], np.ones(D, bool))


def test_signal_fills_two_bars_later_not_on_the_signal_bar():
    tp = synthetic()
    tp.O[0, 12] = 101.0
    assert fill_index(tp.O, 0, 10, 390) == 12  # decided on bar 10, a bot can only act by bar 12


def test_missing_fill_bar_moves_to_next_bar_then_gives_up():
    tp = synthetic()
    tp.O[0, 12] = np.nan
    assert fill_index(tp.O, 0, 10, 390) == 13
    tp.O[0, 13:15] = np.nan
    assert fill_index(tp.O, 0, 10, 390) == -1


def test_stop_wins_when_one_bar_touches_stop_and_target():
    tp = synthetic()
    tp.H[0, 20], tp.L[0, 20] = 102.0, 98.0  # both levels inside one bar
    px, _, why = exit_trade(tp, 0, 15, 1, 99.0, 101.0, 100)
    assert why == "stop" and px == 99.0


def test_gap_through_stop_fills_at_the_worse_open():
    tp = synthetic()
    tp.O[0, 20], tp.H[0, 20], tp.L[0, 20] = 98.0, 98.2, 97.8
    px, _, why = exit_trade(tp, 0, 15, 1, 99.0, 105.0, 100)
    assert why == "stop" and px == 98.0


def test_target_needs_trade_through_not_a_touch():
    tp = synthetic()
    tp.H[0, 20] = 101.0  # touching the limit does not prove a fill
    px, _, why = exit_trade(tp, 0, 15, 1, 90.0, 101.0, 100)
    assert why == "time"
    tp.H[0, 21] = 101.01
    px, _, why = exit_trade(tp, 0, 15, 1, 90.0, 101.0, 100)
    assert why == "target" and px == 101.0


def test_costs_make_a_flat_trade_a_loss():
    row = trade_row(0, 1, 100.0, 100.0, 99.0)
    assert row[4] < 0 and row[5] < row[4]  # a scratch is a loss, and stress cost is worse


def test_cheap_stocks_pay_more_than_six_bps():
    assert lib.cost_frac(100.0) == pytest.approx(6e-4)
    assert lib.cost_frac(8.0) == pytest.approx(0.01 / 8 + 2e-4)


HAS_DATA = os.path.exists(os.path.join(lib.BARS, "AAPL.npz")) and os.path.exists(os.path.join(lib.BARS, "QQQ.npz"))


def _decisions(tr):
    return {(int(r[0]), int(r[1]), round(float(r[2]), 6)) for r in tr}


@pytest.mark.skipif(not HAS_DATA, reason="needs fetched bars")
@pytest.mark.parametrize("family,key,allowed", [
    ("late_momo", "entry", {1500: {331, 332, 333}, 1530: {361, 362, 363}}),
    ("rel_value", "at", {1100: {91, 92, 93}, 1300: {211, 212, 213}}),
    ("gap", None, {None: {2, 3, 4}}),
    ("trend_hold", "at", {1000: {31, 32, 33}, 1030: {61, 62, 63}, 1100: {91, 92, 93}}),
])
def test_entries_happen_at_the_protocol_clock_times(family, key, allowed):
    """A decision at hh:mm fills on the bar starting one minute later (09:30 bar = index 0).
    This caught a real bug where 15:00 was written as 900 and traded at the open."""
    days = lib.calendar()
    tp, b = lib.load("AAPL", days), lib.load("QQQ", days)
    for cfg in FAMILIES[family]["grid"]:
        tr = RUNNERS[family](tp, cfg, 100, 200, bench=b)
        want = allowed[cfg[key] if key else None]
        assert len(tr) and set(tr[:, 8].astype(int)) <= want, cfg


@pytest.mark.skipif(not HAS_DATA, reason="needs fetched bars")
def test_intraday_families_only_signal_inside_their_windows():
    days = lib.calendar()
    tp, b = lib.load("AAPL", days), lib.load("QQQ", days)
    for fam, lo, hi in (("vwap_rev", 31, 331 + 2), ("failed_level", 17, 331 + 2)):
        for cfg in FAMILIES[fam]["grid"]:
            e = RUNNERS[fam](tp, cfg, 100, 200, bench=b)[:, 8]
            assert len(e) and e.min() >= lo and e.max() <= hi, (fam, cfg)


@pytest.mark.skipif(not HAS_DATA, reason="needs fetched bars")
@pytest.mark.parametrize("family", list(FAMILIES))
def test_later_sessions_never_change_earlier_trades(family):
    """Blank every session after a cut. Trades on earlier sessions must be identical, so nothing
    (sigma, beta, prior levels) looks forward in time."""
    days = lib.calendar()
    d0, d1 = 80, 300
    cut = 250
    for cfg in FAMILIES[family]["grid"][::5]:
        full = RUNNERS[family](lib.load("AAPL", days), cfg, d0, d1, bench=lib.load("QQQ", days))
        tp, b = lib.load("AAPL", days), lib.load("QQQ", days)
        for t in (tp, b):
            for m in (t.O, t.H, t.L, t.C, t.VW):
                m[cut:] = np.nan
            t.V[cut:] = 0
            t.ok[cut:] = False
            t.open_[cut:] = t.close[cut:] = np.nan
        part = RUNNERS[family](tp, cfg, d0, cut - 2, bench=b)
        keep = full[full[:, 0] <= cut - 2] if len(full) else full
        assert np.allclose(np.sort(keep, axis=0), np.sort(part, axis=0), equal_nan=True), cfg


@pytest.mark.skipif(not HAS_DATA, reason="needs fetched bars")
@pytest.mark.parametrize("family", [f for f in FAMILIES if f not in ("overnight", "overnight_persist", "daily_swing")])
def test_bars_after_the_fill_never_change_the_entry(family):
    """Scramble everything after each trade's fill bar. The trade must still be taken on the same
    day, side and fill price. Only the exit may change."""
    days = lib.calendar()
    rng = np.random.default_rng(7)
    for cfg in FAMILIES[family]["grid"][::7]:
        tp, b = lib.load("AAPL", days), lib.load("QQQ", days)
        tr = RUNNERS[family](tp, cfg, 100, 160, bench=b)
        for r in tr[:15]:
            d, entry, e = int(r[0]), float(r[2]), int(r[8])
            tp2, b2 = lib.load("AAPL", days), lib.load("QQQ", days)
            for t in (tp2, b2):
                for m in (t.O, t.H, t.L, t.C, t.VW):
                    m[d, e + 1:] = m[d, e + 1:] * rng.uniform(0.9, 1.1, m.shape[1] - e - 1)
                t.close[d] = np.nan  # the day's close is future information too
            again = RUNNERS[family](tp2, cfg, d, d, bench=b2)
            assert (d, int(r[1]), round(entry, 6)) in _decisions(again), (cfg, d)
