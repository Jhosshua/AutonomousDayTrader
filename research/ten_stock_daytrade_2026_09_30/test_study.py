# @steered SNARE-2 2026-09-30
"""Tests for the ten stock study. They guard the things that would silently fake an edge:
premarket bars leaking into the session, lookahead in fills or bitcoin prices, early close bars,
the cost model, and protocol thresholds drifting from PLAN.md."""
import math
import os

import numpy as np
import pytest

import core
from core import BARS, DAYS, EARLY_CLOSES, fill_index, load, trade_row
import fam


@pytest.fixture(scope="module")
def spy():
    return load("SPY")


def test_calendar_early_closes_match_relay():
    # a wrong list would leave 13:00 to 16:00 extended hours bars inside the session
    inside = {d for d in EARLY_CLOSES if core.WIN_START <= d <= core.HOLD_END}
    assert inside == {20241129, 20241224, 20250703, 20251128, 20251224}


def test_regular_matrices_hold_exactly_the_regular_bars(spy):
    # if premarket minutes wrapped into the session arrays the cell count or values would differ
    raw = np.load(os.path.join(BARS, "SPY.npz"))
    di = np.searchsorted(DAYS, raw["day"])
    inday = (di < len(DAYS)) & (DAYS[np.minimum(di, len(DAYS) - 1)] == raw["day"])
    m = raw["minute"].astype(int)
    idx = m - 570
    end = spy.end[np.minimum(di, len(DAYS) - 1)]
    keep = inday & (m >= 570) & (m < 960) & (idx < end)
    assert np.isfinite(spy.C).sum() == keep.sum()
    assert np.allclose(spy.C[di[keep], idx[keep]], raw["c"][keep])
    pre = inday & (m < 570)
    assert pre.sum() > 0 and np.nansum(spy.pre_n) == pre.sum()


def test_early_close_days_have_no_bars_after_13(spy):
    for d in np.flatnonzero(np.isin(DAYS, list(EARLY_CLOSES))):
        assert not np.isfinite(spy.C[d, 210:]).any()
        assert spy.V[d, 210:].sum() == 0


def test_fill_is_two_bars_after_signal():
    O = np.full((1, 390), 1.0)
    assert fill_index(O, 0, 10, 390) == 12  # decided at 09:41 close, filled at 09:42 open
    O[0, 12] = np.nan
    assert fill_index(O, 0, 10, 390) == 13


def test_btc_price_uses_only_bars_closed_by_T():
    B = fam.btc()
    day, T = 20250602, 9 * 60 + 35
    p = B.price(day, T)
    k = B.key_of(day, T) - 1
    j = int(np.searchsorted(B.key, k, side="right")) - 1
    assert B.key[j] <= k and p == B.c[j]
    # a bar starting at T itself (closes after the decision) must not be used
    assert not (B.key[j] == B.key_of(day, T))


def test_cost_doubles_for_early_continuous_fills():
    early = trade_row(0, 1, 100.0, 100.0, 5, 200, "time")
    mid = trade_row(0, 1, 100.0, 100.0, 100, 200, "time")
    assert math.isclose(mid[4], -6e-4) and math.isclose(early[4], -9e-4)
    auction_exit = trade_row(0, 1, 100.0, 100.0, 100, 390, "close")
    assert math.isclose(auction_exit[4], -6e-4)
    assert math.isclose(mid[6], 2 * mid[4])  # 2x stress column


def test_stop_exit_pays_slippage():
    s = trade_row(0, 1, 100.0, 99.0, 100, 200, "stop", 99.0)
    assert s[4] < -0.01 - 6e-4


def test_protocol_thresholds_match_plan():
    assert core.MIN_PER_WEEK == 1.0 and core.DESIGN_PF == 1.2 and core.DESIGN_T == 2.0
    assert core.HOLD_PF == 1.15 and core.HOLD_P == 0.05 and core.BH_Q == 0.10
    assert core.HALVES[2][0] == core.HOLD_START and core.HALVES[3][1] == core.HOLD_END
    assert core.DESIGN_END < core.HOLD_START


def test_trial_count_matches_plan():
    n = sum(len(f["grid"]) for f in fam.FAMILIES.values())
    assert n == 158
    per_name = {s: sum(len(f["grid"]) for k, f in fam.FAMILIES.items() if fam.eligible(k, s)) for s in core.NAMES}
    assert sum(per_name.values()) == 1440


def test_mirrors_and_categories_are_never_neighbours():
    g = fam.FAMILIES["F4_shock"]["grid"]
    for i in range(len(g)):
        for j in fam.neighbours("F4_shock", i):
            assert g[i]["dir"] == g[j]["dir"]
    g = fam.FAMILIES["F5_candle"]["grid"]
    for i in range(len(g)):
        for j in fam.neighbours("F5_candle", i):
            assert g[i]["state"] == g[j]["state"] and g[i]["dir"] == g[j]["dir"]


def test_ssr_blocks_shorts_after_a_ten_percent_drop():
    tp = load("MSTR")
    hit = [d for d in range(2, len(DAYS) - 1)
           if tp.ok[d] and math.isfinite(tp.low[d]) and tp.low[d] <= 0.9 * tp.prev_close[d]]
    assert hit, "MSTR should have at least one 10% down day"
    d = hit[0]
    assert core.ssr_blocks_short(tp, d, 389)
    assert core.ssr_blocks_short(tp, d + 1, 0)


def test_hand_parity_F5_strong_close_follow_spy(spy):
    # rebuild F5 strong/follow/close from raw daily and minute files, independent of the Tape
    import json
    daily = {int(b["t"][:10].replace("-", "")): b for b in json.load(open("data/daily.json"))["SPY"]["split"]}
    raw = np.load(os.path.join(BARS, "SPY.npz"))
    d0, d1 = core.day_index(DAYS, core.WIN_START, core.DESIGN_END)
    trades = fam.run_F5_candle(spy, {"state": "strong", "dir": "follow", "exit": "close"}, d0, d1)
    expect = []
    for d in range(d0, d1 + 1):
        p = int(DAYS[d - 1])
        if p in EARLY_CLOSES:
            continue
        k = (raw["day"] == p) & (raw["minute"] >= 570) & (raw["minute"] < 960)
        hi, lo = raw["h"][k].max(), raw["l"][k].min()
        clv = (daily[p]["c"] - lo) / (hi - lo)
        if clv >= 0.8:
            today = (raw["day"] == DAYS[d]) & (raw["minute"] == 575)
            entry = raw["o"][today][0]
            ex = daily[int(DAYS[d])]["c"]
            c = max(6e-4, 0.01 / entry + 2e-4)
            expect.append((ex - entry) / entry - c * 1.5)
    assert len(trades) == len(expect)
    assert np.allclose(trades[:, 4], expect)


def test_hand_parity_F1_15min_far_close_amd():
    tp = load("AMD")
    raw = np.load(os.path.join(BARS, "AMD.npz"))
    d0, d1 = core.day_index(DAYS, 20250102, 20250331)
    trades = fam.run_F1_orb(tp, {"rng": 15, "stop": "far", "tgt": "close"}, d0, d1)
    n = 0
    for d in range(d0, d1 + 1):
        k = (raw["day"] == DAYS[d]) & (raw["minute"] >= 570) & (raw["minute"] < 960)
        mins, h, l, c, o = raw["minute"][k], raw["h"][k], raw["l"][k], raw["c"][k], raw["o"][k]
        r = mins < 585
        hi, lo = h[r].max(), l[r].min()
        for q in np.flatnonzero((mins >= 585) & (mins < 690)):
            if c[q] > hi or c[q] < lo:
                side = 1 if c[q] > hi else -1
                if side < 0 and core.ssr_blocks_short(tp, d, int(mins[q]) - 570):
                    break  # Rule 201 day, the engine must not short
                fillm = mins[q] + 2
                f = np.flatnonzero(mins == fillm)
                if len(f):
                    entry = o[f[0]]
                    stop = lo if side > 0 else hi
                    risk = side * (entry - stop)
                    if risk > 0 and risk / entry >= 3 * max(6e-4, 0.01 / entry + 2e-4):
                        row = trades[trades[:, 0] == d]
                        assert len(row) == 1 and row[0, 1] == side and math.isclose(row[0, 2], entry)
                        n += 1
                break
    assert n == len(trades) and n > 20
