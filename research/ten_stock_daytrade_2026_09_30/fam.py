# @steered SNARE-2 2026-09-30
"""Strategy families of PLAN.md. Each runner returns a trades array (core.TRADE_COLS) for sessions
with index in [d0, d1]. Every input to a decision is known at the decision bar.

Families F1 to F6 are this study's grids. E1 to E5 are the edge hunt grids, logic copied unchanged
from research/edge_hunt_2026_09_29/families.py, with this study's cost model, official close and
short sale restriction. TSLA_OR15 is the operator's live plan, a benchmark outside the trial count.
"""
from __future__ import annotations

import itertools
import math
import os
from datetime import date

import numpy as np

from core import (BARS, EMPTY, Tape, as_array, exit_trade, ffill_close, fill_index, fixed_entry,
                  price_at_open_or_before, rolling_beta, round_trip, ssr_blocks_short, trade_row,
                  trailing_mean, trailing_std)


def _grid(**axes):
    keys = list(axes)
    return [dict(zip(keys, vals)) for vals in itertools.product(*axes.values())]


def bar_closing_at(hhmm: int) -> int:
    """Index of the bar that closes at hh:mm ET."""
    i = (hhmm // 100) * 60 + hhmm % 100 - 570 - 1
    if not 0 <= i < 390:
        raise ValueError(f"{hhmm} is not a regular session clock time")
    return i


def bar_starting_at(hhmm: int) -> int:
    return bar_closing_at(hhmm) + 1


# ordinal axes per family define neighbours (PLAN.md, design selection)
FAMILIES = {
    "F1_orb": {"grid": _grid(rng=[5, 15, 30, "pre"], stop=["far", "mid"], tgt=["2R", "close"]), "ord": ("stop", "tgt")},
    "F2_btc": {"grid": _grid(mode=["catch", "follow"], at=[935, 1000], k=[0.5, 1.0], exit=[60, "close"]), "ord": ("k", "exit"),
               "names": ("MSTR", "COIN")},
    "F3_period": {"grid": _grid(look=[20, 40], thr=[1.0, 2.0]), "ord": ("look", "thr")},
    "F4_shock": {"grid": _grid(vm=[5, 10], dir=["follow", "fade"], exit=[15, 60, "close"]), "ord": ("vm", "exit")},
    "F5_candle": {"grid": _grid(state=["strong", "weak", "expand", "inside"], dir=["follow", "fade"], exit=[1200, "close"]), "ord": ("exit",)},
    "F6_midday": {"grid": _grid(start=[1130, 1200], stop=["far", "mid"], tgt=["2R", "close"]), "ord": ("start", "stop", "tgt")},
    "E1_gap": {"grid": _grid(g=[0.003, 0.0075, 0.015], mode=["fade", "go"], m=[0.5, 1.0], x=[1130, 1555]), "ord": ("g", "m", "x")},
    "E2_vwap_rev": {"grid": _grid(z=[2.0, 2.5, 3.0], rev=[False, True], stop=["extreme", "sym"], hold=[60, 180]), "ord": ("z", "hold")},
    "E3_failed_level": {"grid": _grid(w=[5, 15], tgt=[1.5, 2.0], hold=[90, 180]), "ord": ("w", "tgt", "hold")},
    "E4_late_momo": {"grid": _grid(entry=[1500, 1530], pred=["pc_1000", "pc_now", "open_now"], k=[0.0, 0.5, 1.0]), "ord": ("entry", "k")},
    "E5_rel_value": {"grid": _grid(at=[1100, 1300], z=[0.5, 0.75, 1.0], exit=["close", "120"]), "ord": ("at", "z", "exit"),
                     "skip": ("SPY",)},
}
FAMILY_ORDER = list(FAMILIES)


def eligible(fam: str, sym: str) -> bool:
    f = FAMILIES[fam]
    if "names" in f and sym not in f["names"]:
        return False
    return sym not in f.get("skip", ())


def neighbours(fam: str, i: int) -> list[int]:
    grid, ords = FAMILIES[fam]["grid"], FAMILIES[fam]["ord"]
    a = grid[i]
    out = []
    for j, b in enumerate(grid):
        diff = [k for k in a if a[k] != b[k]]
        if j != i and len(diff) == 1 and diff[0] in ords:
            out.append(j)
    return out


# edge hunt benchmark map, unchanged
TECH = ("AAPL MSFT AMZN META GOOGL NFLX PLTR CRWD SNOW SHOP UBER ORCL ADBE").split()
SEMIS = "NVDA AVGO AMD MU SMCI INTC ARM QCOM MRVL".split()
FIN = "HOOD SOFI PYPL AFRM JPM BAC GS COIN".split()


def benchmark(sym: str) -> str:
    if sym in TECH:
        return "QQQ"
    if sym in SEMIS:
        return "SMH"
    if sym in FIN:
        return "XLF"
    return "SPY"


def _short_ok(tp: Tape, d: int, side: int, i: int) -> bool:
    return side > 0 or not ssr_blocks_short(tp, d, i)


# ---------------------------------------------------------------- F1 and F6 range breakouts


def _breakout(tp: Tape, d: int, hi: float, lo: float, first: int, last: int, cfg: dict):
    if not (math.isfinite(hi) and math.isfinite(lo)) or hi <= lo:
        return None
    C = tp.C[d]
    for i in range(first, min(last, int(tp.end[d]) - 1) + 1):
        c = C[i]
        if not math.isfinite(c):
            continue
        if c > hi or c < lo:
            side = 1 if c > hi else -1
            if not _short_ok(tp, d, side, i):
                return None
            e = fill_index(tp.O, d, i, tp.end[d])
            if e < 0:
                return None
            entry = float(tp.O[d, e])
            far = lo if side > 0 else hi
            stop = far if cfg["stop"] == "far" else (hi + lo) / 2
            risk = side * (entry - stop)
            if risk <= 0 or risk / entry < 3 * round_trip(entry):
                return None
            target = entry + side * 2 * risk if cfg["tgt"] == "2R" else math.nan
            px, xb, why = exit_trade(tp, d, e, side, stop, target, int(tp.end[d]))
            return trade_row(d, side, entry, px, e, xb, why, stop)
    return None


def run_F1_orb(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    rows = []
    last = bar_starting_at(1130) - 1
    for d in range(d0, d1 + 1):
        if not tp.ok[d]:
            continue
        if cfg["rng"] == "pre":
            if tp.pre_n[d] < 60:
                continue
            hi, lo, first = tp.pre_hi[d], tp.pre_lo[d], 0
        else:
            R = cfg["rng"]
            hi, lo, first = np.nanmax(tp.H[d, :R]), np.nanmin(tp.L[d, :R]), R
        r = _breakout(tp, d, float(hi), float(lo), first, last, cfg)
        if r:
            rows.append(r)
    return as_array(rows)


def run_F6_midday(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    rows = []
    s, e_rng = bar_starting_at(cfg["start"]), bar_starting_at(1330)
    for d in range(d0, d1 + 1):
        if not tp.ok[d] or tp.end[d] < 390:
            continue
        with np.errstate(all="ignore"):
            hi, lo = np.nanmax(tp.H[d, s:e_rng]), np.nanmin(tp.L[d, s:e_rng])
        r = _breakout(tp, d, float(hi), float(lo), e_rng, bar_starting_at(1500) - 1, cfg)
        if r:
            rows.append(r)
    return as_array(rows)


# ---------------------------------------------------------------- F2 bitcoin lead


class BTC:
    """BTC/USD one minute bars keyed by ET clock minutes since an epoch (bar starts)."""

    def __init__(self):
        b = np.load(os.path.join(BARS, "BTC.npz"))
        day = b["day"].astype(np.int64)
        ords = np.array([date(x // 10000, x // 100 % 100, x % 100).toordinal() for x in np.unique(day)])
        omap = dict(zip(np.unique(day), ords))
        key = np.array([omap[x] for x in day]) * 1440 + b["minute"].astype(np.int64)
        o = np.argsort(key, kind="stable")
        self.key, self.c = key[o], b["c"][o]

    @staticmethod
    def key_of(day: int, minute: int) -> int:
        return date(day // 10000, day // 100 % 100, day % 100).toordinal() * 1440 + minute

    def price(self, day: int, minute_T: int) -> float:
        """Close of the last BTC bar starting at or before T minus 1, if it started within 5 minutes of T."""
        k = self.key_of(day, minute_T) - 1
        j = int(np.searchsorted(self.key, k, side="right")) - 1
        if j < 0 or self.key[j] < k - 4:
            return math.nan
        return float(self.c[j])


_BTC = None


def btc() -> BTC:
    global _BTC
    if _BTC is None:
        _BTC = BTC()
    return _BTC


def btc_features(tp: Tape, at: int) -> dict:
    """Per session BTC anchor, decision price, hours, beta. Cached per tape and decision time."""
    key = ("btc", at)
    if key in tp.cache:
        return tp.cache[key]
    B = btc()
    D = len(tp.days)
    anchor_min = np.where(tp.end == 210, 13 * 60 - 1, 16 * 60 - 1)  # start of the last regular bar
    anchor = np.array([B.price(int(tp.days[d]), int(anchor_min[d]) + 1) for d in range(D)])
    # "price at T" uses bars starting <= T - 1, so pass T = last bar start + 1 to anchor at that bar start
    i_d = bar_closing_at(at)
    T = 570 + i_d + 1
    now = np.array([B.price(int(tp.days[d]), T) for d in range(D)])
    prev_anchor = np.concatenate([[np.nan], anchor[:-1]])
    hours = np.full(D, np.nan)
    for d in range(1, D):
        k0 = BTC.key_of(int(tp.days[d - 1]), int(anchor_min[d - 1]))
        k1 = BTC.key_of(int(tp.days[d]), T - 1)
        hours[d] = (k1 - k0) / 60.0
    m = now / prev_anchor - 1
    daily_b = anchor / prev_anchor - 1
    rs = tp.close / tp.prev_close - 1
    beta = rolling_beta(rs, daily_b)
    Cff = ffill_close(tp)
    s = Cff[:, i_d] / tp.prev_close - 1
    e = s - beta * m
    root = np.sqrt(hours)
    out = {"m": m, "mz": m / root, "ez": e / root, "skip": ~(np.isfinite(now) & np.isfinite(prev_anchor)),
           "daily_b": daily_b, "rs": rs}
    out["sig_mz"] = trailing_std(out["mz"])
    out["sig_ez"] = trailing_std(out["ez"])
    tp.cache[key] = out
    return out


def run_F2_btc(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    f = btc_features(tp, cfg["at"])
    i_d = bar_closing_at(cfg["at"])
    rows = []
    for d in range(d0, d1 + 1):
        if not tp.ok[d] or f["skip"][d]:
            continue
        if cfg["mode"] == "catch":
            z, sg = f["ez"][d], f["sig_ez"][d]
            side_of = lambda v: -1 if v > 0 else 1
        else:
            z, sg = f["mz"][d], f["sig_mz"][d]
            side_of = lambda v: 1 if v > 0 else -1
        if not (math.isfinite(z) and math.isfinite(sg) and sg > 0) or abs(z) <= cfg["k"] * sg:
            continue
        side = side_of(z)
        if not _short_ok(tp, d, side, i_d):
            continue
        e = fill_index(tp.O, d, i_d, tp.end[d])
        if e < 0:
            continue
        entry = float(tp.O[d, e])
        x = int(tp.end[d]) if cfg["exit"] == "close" else e + 60
        if x >= tp.end[d] - 5:
            x = int(tp.end[d])
        px, xb, why = exit_trade(tp, d, e, side, math.nan, math.nan, x)
        rows.append(trade_row(d, side, entry, px, e, xb, why))
    return as_array(rows)


# ---------------------------------------------------------------- F3 intraday periodicity

SLOTS = 12  # 09:30 to 15:00 starts, the 15:30 slot is dropped


def slot_returns(tp: Tape) -> np.ndarray:
    """[D, 12] return from the open of the slot's second minute to the open of the next slot's second
    minute, nan where missing. Early close days keep slots 0 to 5 only in the history."""
    if "slots" in tp.cache:
        return tp.cache["slots"]
    D = len(tp.days)
    r = np.full((D, SLOTS), np.nan)
    for k in range(SLOTS):
        a, b = 30 * k + 1, 30 * (k + 1) + 1
        ok = tp.end > b
        with np.errstate(invalid="ignore"):
            r[:, k] = np.where(ok & tp.ok, tp.O[:, b] / tp.O[:, a] - 1, np.nan)
    tp.cache["slots"] = r
    return r


def slot_stats(tp: Tape, look: int) -> tuple[np.ndarray, np.ndarray]:
    key = ("slot_t", look)
    if key in tp.cache:
        return tp.cache[key]
    r = slot_returns(tp)
    D = len(tp.days)
    mean = np.full((D, SLOTS), np.nan)
    tst = np.full((D, SLOTS), np.nan)
    for k in range(SLOTS):
        hist: list[float] = []
        for d in range(D):
            if len(hist) >= 0.75 * look:
                h = np.array(hist[-look:])
                sd = h.std(ddof=1)
                mean[d, k] = h.mean()
                tst[d, k] = h.mean() / sd * math.sqrt(len(h)) if sd > 0 else 0.0
            if math.isfinite(r[d, k]):
                hist.append(float(r[d, k]))
    tp.cache[key] = (mean, tst)
    return mean, tst


def run_F3_period(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    mean, tst = slot_stats(tp, cfg["look"])
    rows = []
    for d in range(d0, d1 + 1):
        if not tp.ok[d]:
            continue
        end = int(tp.end[d])
        sides = []
        for k in range(SLOTS):
            if 30 * k + 1 >= end:
                sides.append(0)
                continue
            t = tst[d, k]
            sides.append(int(np.sign(mean[d, k])) if math.isfinite(t) and abs(t) > cfg["thr"] else 0)
        k = 0
        while k < SLOTS:
            s = sides[k]
            if s == 0:
                k += 1
                continue
            j = k
            while j + 1 < SLOTS and sides[j + 1] == s:
                j += 1
            a = 30 * k + 1
            e = fixed_entry(tp.O, d, a, end)
            b = 30 * (j + 1) + 1
            if e >= 0 and e < b and _short_ok(tp, d, s, max(e - 1, 0)):
                entry = float(tp.O[d, e])
                if b >= end - 1:
                    px, xb, why = float(tp.close[d]), end, "close"
                else:
                    px, xb, why = price_at_open_or_before(tp, d, b), b, "time"
                if math.isfinite(px):
                    rows.append(trade_row(d, s, entry, px, e, xb, why))
            k = j + 1
    return as_array(rows)


# ---------------------------------------------------------------- F4 volume shock bars


def shock_inputs(tp: Tape) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if "shock" in tp.cache:
        return tp.cache["shock"]
    D = len(tp.days)
    Vn = np.where(np.isfinite(tp.C), tp.V, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        ar = np.abs(tp.C / tp.O - 1)
    med_v = np.full((D, 390), np.nan)
    med_r = np.full(D, np.nan)
    valid = np.flatnonzero(tp.ok)
    for n, d in enumerate(valid):
        if n < 15:
            continue
        prev = valid[max(0, n - 20):n]
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                med_v[d] = np.nanmedian(Vn[prev], axis=0)
                med_r[d] = np.nanmedian(ar[prev])
    tp.cache["shock"] = (med_v, med_r, ar)
    return tp.cache["shock"]


def run_F4_shock(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    med_v, med_r, ar = shock_inputs(tp)
    lo_i, hi_i = bar_starting_at(945), bar_starting_at(1500)
    rows = []
    for d in range(d0, d1 + 1):
        if not tp.ok[d] or not math.isfinite(med_r[d]) or med_r[d] <= 0:
            continue
        end = int(tp.end[d])
        with np.errstate(invalid="ignore"):
            shock = (tp.V[d] >= cfg["vm"] * med_v[d]) & (ar[d] >= 3 * med_r[d]) & (tp.C[d] != tp.O[d])
        shock[:lo_i] = False
        shock[min(hi_i, end - 1) + 1:] = False
        pos, taken = lo_i, 0
        while taken < 3:
            nxt = np.flatnonzero(shock[pos:])
            if not len(nxt):
                break
            i = pos + int(nxt[0])
            side = 1 if tp.C[d, i] > tp.O[d, i] else -1
            if cfg["dir"] == "fade":
                side = -side
            e = fill_index(tp.O, d, i, end)
            if e < 0 or not _short_ok(tp, d, side, i):
                pos = i + 1
                continue
            entry = float(tp.O[d, e])
            x = end if cfg["exit"] == "close" else e + cfg["exit"]
            if x >= end - 5:
                x = end
            px, xb, why = exit_trade(tp, d, e, side, math.nan, math.nan, x)
            rows.append(trade_row(d, side, entry, px, e, xb, why))
            taken += 1
            pos = xb + 1
            if pos >= end:
                break
    return as_array(rows)


# ---------------------------------------------------------------- F5 prior day candle


def candle_state(tp: Tape) -> dict:
    if "candle" in tp.cache:
        return tp.cache["candle"]
    rng = tp.high - tp.low
    mean_rng = trailing_mean(rng)
    D = len(tp.days)
    dirn = np.sign(tp.close - tp.open_)
    with np.errstate(invalid="ignore", divide="ignore"):
        clv = (tp.close - tp.low) / rng
    out = {s: np.zeros(D) for s in ("strong", "weak", "expand", "inside")}
    for d in range(2, D):
        p = d - 1  # yesterday
        if not (tp.ok[p] and tp.end[p] == 390 and math.isfinite(clv[p]) and rng[p] > 0):
            continue
        if clv[p] >= 0.8:
            out["strong"][d] = 1
        if clv[p] <= 0.2:
            out["weak"][d] = -1
        if math.isfinite(mean_rng[p]) and rng[p] >= 1.5 * mean_rng[p]:
            out["expand"][d] = dirn[p]
        if tp.ok[p - 1] and tp.high[p] < tp.high[p - 1] and tp.low[p] > tp.low[p - 1]:
            out["inside"][d] = dirn[p]
    tp.cache["candle"] = out
    return out


def run_F5_candle(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    st = candle_state(tp)[cfg["state"]]
    b = bar_starting_at(935)
    rows = []
    for d in range(d0, d1 + 1):
        if not tp.ok[d] or st[d] == 0:
            continue
        side = int(st[d]) if cfg["dir"] == "follow" else -int(st[d])
        end = int(tp.end[d])
        e = fixed_entry(tp.O, d, b, end)
        if e < 0 or not _short_ok(tp, d, side, e - 1):
            continue
        entry = float(tp.O[d, e])
        x = end if cfg["exit"] == "close" else bar_starting_at(1200)
        px, xb, why = exit_trade(tp, d, e, side, math.nan, math.nan, x)
        rows.append(trade_row(d, side, entry, px, e, xb, why))
    return as_array(rows)


# ---------------------------------------------------------------- E1 to E5 edge hunt grids (logic unchanged)


def vwap_prep(tp: Tape) -> dict:
    if "vwap" in tp.cache:
        return tp.cache["vwap"]
    v = tp.V
    pv = np.nan_to_num(tp.VW) * v
    cv = np.cumsum(v, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        vwap = np.cumsum(pv, axis=1) / cv
        var = np.cumsum(np.nan_to_num(tp.VW) ** 2 * v, axis=1) / cv - vwap ** 2
        sd = np.sqrt(np.clip(var, 0, None))
        z = (tp.C - vwap) / sd
    tp.cache["vwap"] = {"vwap": vwap, "z": z}
    return tp.cache["vwap"]


def _time_exit_index(tp: Tape, d: int, e: int, hold: int) -> int:
    return min(e + hold, int(tp.end[d]) - 5)


def run_E1_gap(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    rows = []
    for d in range(d0, d1 + 1):
        o, pc = tp.open_[d], tp.prev_close[d]
        if not (tp.ok[d] and math.isfinite(o) and math.isfinite(pc)):
            continue
        g = o / pc - 1
        if abs(g) < cfg["g"]:
            continue
        e = fill_index(tp.O, d, 0, tp.end[d])
        if e < 0:
            continue
        entry = float(tp.O[d, e])
        gap_px = abs(o - pc)
        if cfg["mode"] == "fade":
            side = -1 if g > 0 else 1
            target = pc
            stop = entry - side * cfg["m"] * gap_px
            if side * (target - entry) <= 0:
                continue
        else:
            side = 1 if g > 0 else -1
            stop = entry - side * cfg["m"] * gap_px
            target = entry + side * 1.5 * cfg["m"] * gap_px
        if not _short_ok(tp, d, side, 0):
            continue
        xi = bar_closing_at(1130) + 1 if cfg["x"] == 1130 else bar_closing_at(1555) + 1
        xi = min(xi, int(tp.end[d]) - 5)
        px, xb, why = exit_trade(tp, d, e, side, stop, target, xi)
        rows.append(trade_row(d, side, entry, px, e, xb, why, stop))
    return as_array(rows)


def run_E2_vwap_rev(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    x = vwap_prep(tp)
    z, vwap = x["z"], x["vwap"]
    lo_i, hi_i = bar_closing_at(1000), bar_closing_at(1500)
    rows = []
    for d in range(d0, d1 + 1):
        if not tp.ok[d]:
            continue
        last = min(hi_i, int(tp.end[d]) - 61)
        zr = z[d]
        with np.errstate(invalid="ignore"):
            cand = np.abs(zr) >= cfg["z"]
            if cfg["rev"]:
                green = tp.C[d] > tp.O[d]
                red = tp.C[d] < tp.O[d]
                cand &= ((zr < 0) & green) | ((zr > 0) & red)
        cand[:lo_i] = False
        cand[last + 1:] = False
        pos, taken = lo_i, 0
        while taken < 2:
            nxt = np.flatnonzero(cand[pos:])
            if not len(nxt):
                break
            i = pos + int(nxt[0])
            side = -1 if zr[i] > 0 else 1
            c = float(tp.C[d, i])
            target = float(vwap[d, i])
            if cfg["stop"] == "extreme":
                w = slice(max(0, i - 14), i + 1)
                stop = float(np.nanmax(tp.H[d, w])) if side < 0 else float(np.nanmin(tp.L[d, w]))
            else:
                stop = c - side * abs(c - target)
            e = fill_index(tp.O, d, i, tp.end[d])
            if e < 0 or not _short_ok(tp, d, side, i):
                pos = i + 1
                continue
            entry = float(tp.O[d, e])
            if side * (target - entry) <= 0 or side * (entry - stop) <= 0:
                pos = i + 1
                continue
            px, xb, why = exit_trade(tp, d, e, side, stop, target, _time_exit_index(tp, d, e, cfg["hold"]))
            rows.append(trade_row(d, side, entry, px, e, xb, why, stop))
            taken += 1
            pos = xb + 1
    return as_array(rows)


def run_E3_failed_level(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    first, last_sig = bar_closing_at(945) + 1, bar_closing_at(1500)
    rows = []
    for d in range(d0, d1 + 1):
        if not (tp.ok[d] and math.isfinite(tp.prev_high[d]) and math.isfinite(tp.prev_low[d])):
            continue
        last = min(last_sig, int(tp.end[d]) - 61)
        C = tp.C[d]
        for level, side in ((tp.prev_high[d], -1), (tp.prev_low[d], 1)):
            with np.errstate(invalid="ignore"):
                outside = (C > level) if side < 0 else (C < level)
                inside = (C <= level) if side < 0 else (C >= level)
            b = -1
            for i in range(max(first, 1), last + 1):
                if outside[i] and inside[i - 1]:
                    b = i
                    break
            if b < 0:
                continue
            j = -1
            for k in range(b + 1, min(b + cfg["w"], last) + 1):
                if math.isfinite(C[k]) and ((side < 0 and C[k] < level) or (side > 0 and C[k] > level)):
                    j = k
                    break
            if j < 0:
                continue
            ext = float(np.nanmax(tp.H[d, b:j + 1])) if side < 0 else float(np.nanmin(tp.L[d, b:j + 1]))
            e = fill_index(tp.O, d, j, tp.end[d])
            if e < 0 or not _short_ok(tp, d, side, j):
                continue
            entry = float(tp.O[d, e])
            if side * (entry - ext) <= 0:
                continue
            target = entry + side * cfg["tgt"] * abs(entry - ext)
            px, xb, why = exit_trade(tp, d, e, side, ext, target, _time_exit_index(tp, d, e, cfg["hold"]))
            rows.append(trade_row(d, side, entry, px, e, xb, why, ext))
    return as_array(rows)


def run_E4_late_momo(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    Cff = ffill_close(tp)
    i_d = bar_closing_at(cfg["entry"])
    i10 = bar_closing_at(1000)
    if cfg["pred"] == "pc_1000":
        pred = Cff[:, i10] / tp.prev_close - 1
    elif cfg["pred"] == "pc_now":
        pred = Cff[:, i_d] / tp.prev_close - 1
    else:
        pred = Cff[:, i_d] / tp.open_ - 1
    pred = np.where(tp.ok & (tp.end == 390), pred, np.nan)
    sig = trailing_std(pred)
    rows = []
    for d in range(d0, d1 + 1):
        p = pred[d]
        if not (math.isfinite(p) and math.isfinite(sig[d])) or p == 0 or abs(p) <= cfg["k"] * sig[d]:
            continue
        side = 1 if p > 0 else -1
        if not _short_ok(tp, d, side, i_d):
            continue
        e = fill_index(tp.O, d, i_d, tp.end[d])
        if e < 0:
            continue
        entry = float(tp.O[d, e])
        px, xb, why = exit_trade(tp, d, e, side, math.nan, math.nan, int(tp.end[d]))
        rows.append(trade_row(d, side, entry, px, e, xb, why))
    return as_array(rows)


def run_E5_rel_value(tp: Tape, cfg: dict, d0: int, d1: int, bench: Tape = None, **_) -> np.ndarray:
    Cff, Cb = ffill_close(tp), ffill_close(bench)
    i_d = bar_closing_at(cfg["at"])
    if "beta" not in tp.cache:
        tp.cache["beta"] = rolling_beta(tp.close / tp.prev_close - 1, bench.close / bench.prev_close - 1)
    beta = tp.cache["beta"]
    res = (Cff[:, i_d] / tp.open_ - 1) - beta * (Cb[:, i_d] / bench.open_ - 1)
    res = np.where(tp.ok & bench.ok & (tp.end > i_d + 1), res, np.nan)
    sig = trailing_std(res)
    rows = []
    for d in range(d0, d1 + 1):
        if not (math.isfinite(res[d]) and math.isfinite(sig[d]) and sig[d] > 0):
            continue
        zz = res[d] / sig[d]
        if abs(zz) < cfg["z"]:
            continue
        side = -1 if zz > 0 else 1
        if not _short_ok(tp, d, side, i_d):
            continue
        e = fill_index(tp.O, d, i_d, tp.end[d])
        if e < 0:
            continue
        entry = float(tp.O[d, e])
        xi = int(tp.end[d]) if cfg["exit"] == "close" else e + 120
        if xi >= tp.end[d] - 5:
            xi = int(tp.end[d])
        px, xb, why = exit_trade(tp, d, e, side, math.nan, math.nan, xi)
        rows.append(trade_row(d, side, entry, px, e, xb, why))
    return as_array(rows)


# ---------------------------------------------------------------- TSLA OR15 retest benchmark


def frozen_atr(H: np.ndarray, L: np.ndarray, C: np.ndarray, upto: int) -> float:
    """The live plan's TR formula: first bar high minus low, later max(high minus low, |high minus prior close|)."""
    h, l, c = H[:upto + 1], L[:upto + 1], C[:upto + 1]
    if len(h) < 3:
        return float(c[-1]) * 0.005
    tr = np.empty(len(h))
    tr[0] = h[0] - l[0]
    tr[1:] = np.maximum(h[1:] - l[1:], np.abs(h[1:] - c[:-1]))
    return float(tr[-14:].mean())


def run_tsla_or15(tp: Tape, cfg: dict, d0: int, d1: int, qqq: Tape = None, **_) -> np.ndarray:
    rows = []
    last = bar_starting_at(1130)
    for d in range(d0, d1 + 1):
        if not (tp.ok[d] and qqq.ok[d]):
            continue
        H, L, C, O = tp.H[d], tp.L[d], tp.C[d], tp.O[d]
        if not np.isfinite(C[:15]).all():
            continue
        hi, lo = float(H[:15].max()), float(L[:15].min())
        mid = (hi + lo) / 2
        qh, ql, qc, qv = qqq.H[d], qqq.L[d], qqq.C[d], qqq.V[d]
        with np.errstate(invalid="ignore", divide="ignore"):
            tpx = np.nan_to_num((qh + ql + qc) / 3)
            qvwap = np.cumsum(tpx * qv) / np.cumsum(qv)
        broke = -1
        for i in range(15, min(last, int(tp.end[d]) - 1) + 1):
            if not math.isfinite(C[i]):
                break  # the live plan skips a session with a missing bar
            if broke < 0:
                if C[i] > hi:
                    broke = i
                continue
            atr = frozen_atr(H, L, C, i)
            if not (L[i] <= hi + 0.2 * atr and L[i] >= mid and C[i] > O[i] and C[i] >= hi
                    and math.isfinite(qvwap[i]) and qc[i] >= qvwap[i]):
                continue
            e = i + 2
            if e >= tp.end[d] or not math.isfinite(O[e]):
                break
            entry = float(O[e])
            risk = float(C[i]) - lo
            target = float(C[i]) + 2 * risk
            x = min(e + 120, int(tp.end[d]) - 5)
            px, xb, why = exit_trade(tp, d, e, 1, lo, target, x)
            rows.append(trade_row(d, 1, entry, px, e, xb, why, lo))
            break
    return as_array(rows)


RUNNERS = {"F1_orb": run_F1_orb, "F2_btc": run_F2_btc, "F3_period": run_F3_period, "F4_shock": run_F4_shock,
           "F5_candle": run_F5_candle, "F6_midday": run_F6_midday, "E1_gap": run_E1_gap,
           "E2_vwap_rev": run_E2_vwap_rev, "E3_failed_level": run_E3_failed_level,
           "E4_late_momo": run_E4_late_momo, "E5_rel_value": run_E5_rel_value}
