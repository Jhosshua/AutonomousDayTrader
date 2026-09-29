# @steered SNARE-2 2026-09-29
"""The six strategy families of PROTOCOL.md. Each run_* returns a trades array (lib.TRADE_COLS)
for sessions with index in [d0, d1]. Every input to a decision is known at the decision bar."""
from __future__ import annotations

import itertools
import math

import numpy as np

from lib import Tape, exit_trade, fill_index, trade_row, trailing_std

EMPTY = np.zeros((0, 9))


def _grid(**axes):
    keys = list(axes)
    return [dict(zip(keys, vals)) for vals in itertools.product(*axes.values())]


FAMILIES = {
    "late_momo": {"use_r": False, "grid": _grid(entry=[1500, 1530], pred=["pc_1000", "pc_now", "open_now"], k=[0.0, 0.5, 1.0])},
    "overnight": {"use_r": False, "grid": _grid(cond=["none", "down", "down_big", "up_big"], exit=["open", "1000"])},
    "gap": {"use_r": True, "grid": _grid(g=[0.003, 0.0075, 0.015], mode=["fade", "go"], m=[0.5, 1.0], x=[1130, 1555])},
    "vwap_rev": {"use_r": True, "grid": _grid(z=[2.0, 2.5, 3.0], rev=[False, True], stop=["extreme", "sym"], hold=[60, 180])},
    "failed_level": {"use_r": True, "grid": _grid(w=[5, 15], tgt=[1.5, 2.0], hold=[90, 180])},
    "rel_value": {"use_r": False, "grid": _grid(at=[1100, 1300], z=[0.5, 0.75, 1.0], exit=["close", "120"])},
}


def bar_closing_at(hhmm: int) -> int:
    """Index of the bar that closes at hh:mm ET (hhmm clock time, e.g. 1530)."""
    i = (hhmm // 100) * 60 + hhmm % 100 - 570 - 1
    if not 0 <= i < 390:
        raise ValueError(f"{hhmm} is not a regular session clock time")
    return i


def prep(tp: Tape) -> dict:
    if getattr(tp, "_x", None) is not None:
        return tp._x
    C = tp.C
    idx = np.where(np.isfinite(C), np.arange(C.shape[1])[None, :], 0)
    np.maximum.accumulate(idx, axis=1, out=idx)
    Cff = C[np.arange(C.shape[0])[:, None], idx]  # last known close at or before each minute
    v = tp.V
    pv = np.nan_to_num(tp.VW) * v
    cv = np.cumsum(v, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        vwap = np.cumsum(pv, axis=1) / cv
        var = np.cumsum(np.nan_to_num(tp.VW) ** 2 * v, axis=1) / cv - vwap ** 2
    sd = np.sqrt(np.clip(var, 0, None))
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (C - vwap) / sd
    tp._x = {"Cff": Cff, "vwap": vwap, "vsd": sd, "z": z}
    return tp._x


def _time_exit_index(tp: Tape, d: int, e: int, hold: int) -> int:
    return min(e + hold, int(tp.end[d]) - 5)


def run_late_momo(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    x = prep(tp)
    i_d = bar_closing_at(cfg["entry"])
    i10 = bar_closing_at(1000)
    Cff = x["Cff"]
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
        e = fill_index(tp.O, d, i_d, tp.end[d])
        if e < 0:
            continue
        entry = float(tp.O[d, e])
        px, _, _ = exit_trade(tp, d, e, side, math.nan, math.nan, int(tp.end[d]))
        rows.append(trade_row(d, side, entry, px, math.nan, e))
    return np.array(rows) if rows else EMPTY


def run_overnight(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    x = prep(tp)
    r = x["Cff"][:, bar_closing_at(1548)] / tp.open_ - 1
    r = np.where(tp.ok & (tp.end == 390), r, np.nan)
    sig = trailing_std(r)
    rows = []
    for d in range(d0, min(d1, len(tp.days) - 2) + 1):
        if not (math.isfinite(r[d]) and math.isfinite(tp.close[d]) and tp.ok[d + 1]):
            continue
        c = cfg["cond"]
        if c != "none" and not math.isfinite(sig[d]):
            continue
        if (c == "down" and not r[d] < 0) or (c == "down_big" and not r[d] < -0.5 * sig[d]) \
                or (c == "up_big" and not r[d] > 0.5 * sig[d]):
            continue
        entry = float(tp.close[d])
        if cfg["exit"] == "open":
            px = tp.open_[d + 1]
        else:
            px = tp.O[d + 1, 30] if math.isfinite(tp.O[d + 1, 30]) else x["Cff"][d + 1, 29]
        if not math.isfinite(px):
            continue
        rows.append(trade_row(d, 1, entry, float(px), math.nan, int(tp.end[d]) - 1))
    return np.array(rows) if rows else EMPTY


def run_gap(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
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
                continue  # the gap already closed before the fill
        else:
            side = 1 if g > 0 else -1
            stop = entry - side * cfg["m"] * gap_px
            target = entry + side * 1.5 * cfg["m"] * gap_px
        xi = bar_closing_at(1130) + 1 if cfg["x"] == 1130 else bar_closing_at(1555) + 1
        xi = min(xi, int(tp.end[d]) - 5)
        px, _, _ = exit_trade(tp, d, e, side, stop, target, xi)
        rows.append(trade_row(d, side, entry, px, stop, e))
    return np.array(rows) if rows else EMPTY


def run_vwap_rev(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    x = prep(tp)
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
            if e < 0:
                pos = i + 1
                continue
            entry = float(tp.O[d, e])
            if side * (target - entry) <= 0 or side * (entry - stop) <= 0:
                pos = i + 1
                continue
            px, xb, _ = exit_trade(tp, d, e, side, stop, target, _time_exit_index(tp, d, e, cfg["hold"]))
            rows.append(trade_row(d, side, entry, px, stop, e))
            taken += 1
            pos = xb + 1
    return np.array(rows) if rows else EMPTY


def run_failed_level(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    first, last_sig = bar_closing_at(945) + 1, bar_closing_at(1500)
    rows = []
    for d in range(d0, d1 + 1):
        if not (tp.ok[d] and math.isfinite(tp.prev_high[d]) and math.isfinite(tp.prev_low[d])):
            continue
        last = min(last_sig, int(tp.end[d]) - 61)
        C = tp.C[d]
        for level, side in ((tp.prev_high[d], -1), (tp.prev_low[d], 1)):
            # outside means closed beyond the level: above the high (side -1) or below the low (side +1)
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
            if e < 0:
                continue
            entry = float(tp.O[d, e])
            if side * (entry - ext) <= 0:
                continue
            target = entry + side * cfg["tgt"] * abs(entry - ext)
            px, _, _ = exit_trade(tp, d, e, side, ext, target, _time_exit_index(tp, d, e, cfg["hold"]))
            rows.append(trade_row(d, side, entry, px, ext, e))
    return np.array(rows) if rows else EMPTY


def rolling_beta(rs: np.ndarray, rb: np.ndarray, n: int = 60, min_n: int = 40) -> np.ndarray:
    out = np.full(len(rs), np.nan)
    for i in range(len(rs)):
        a, b = rs[max(0, i - n):i], rb[max(0, i - n):i]
        m = np.isfinite(a) & np.isfinite(b)
        if m.sum() >= min_n and np.var(b[m]) > 0:
            out[i] = np.cov(a[m], b[m], ddof=1)[0, 1] / np.var(b[m], ddof=1)
    return out


def run_rel_value(tp: Tape, cfg: dict, d0: int, d1: int, bench: Tape = None, **_) -> np.ndarray:
    x, xb = prep(tp), prep(bench)
    i_d = bar_closing_at(cfg["at"])
    cache = getattr(tp, "_beta", None)
    if cache is None:
        rs = tp.close / tp.prev_close - 1
        rb = bench.close / bench.prev_close - 1
        cache = tp._beta = rolling_beta(rs, rb)
    beta = cache
    res = (x["Cff"][:, i_d] / tp.open_ - 1) - beta * (xb["Cff"][:, i_d] / bench.open_ - 1)
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
        e = fill_index(tp.O, d, i_d, tp.end[d])
        if e < 0:
            continue
        entry = float(tp.O[d, e])
        xi = int(tp.end[d]) if cfg["exit"] == "close" else e + 120
        if xi >= tp.end[d] - 5:
            xi = int(tp.end[d])
        px, _, _ = exit_trade(tp, d, e, side, math.nan, math.nan, xi)
        rows.append(trade_row(d, side, entry, px, math.nan, e))
    return np.array(rows) if rows else EMPTY


RUNNERS = {"late_momo": run_late_momo, "overnight": run_overnight, "gap": run_gap,
           "vwap_rev": run_vwap_rev, "failed_level": run_failed_level, "rel_value": run_rel_value}


# ---------------------------------------------------------------- round 2 (PROTOCOL.md, round 2)

ROUND2 = {
    "trend_hold": {"use_r": False, "grid": _grid(at=[1000, 1030, 1100], k=[0.0, 0.5], confirm=["none", "market", "market_vwap"])},
    "overnight_persist": {"use_r": False, "grid": _grid(look=[20, 60], mode=["long_short", "long_only"], exit=["open", "1000"])},
    "daily_swing": {"use_r": False, "grid": _grid(look=[1, 3, 5], dir=["follow", "fade"])},
}


def run_trend_hold(tp: Tape, cfg: dict, d0: int, d1: int, bench: Tape = None, **_) -> np.ndarray:
    x, xb = prep(tp), prep(bench)
    i_d = bar_closing_at(cfg["at"])
    r = x["Cff"][:, i_d] / tp.open_ - 1
    rb = xb["Cff"][:, i_d] / bench.open_ - 1
    r = np.where(tp.ok & (tp.end > i_d + 1), r, np.nan)
    sig = trailing_std(r)
    rows = []
    for d in range(d0, d1 + 1):
        p = r[d]
        if not (math.isfinite(p) and math.isfinite(sig[d])) or p == 0 or abs(p) <= cfg["k"] * sig[d]:
            continue
        side = 1 if p > 0 else -1
        if cfg["confirm"] != "none":
            if not (math.isfinite(rb[d]) and np.sign(rb[d]) == side):
                continue
            if cfg["confirm"] == "market_vwap":
                vw = x["vwap"][d, i_d]
                if not (math.isfinite(vw) and side * (x["Cff"][d, i_d] - vw) > 0):
                    continue
        e = fill_index(tp.O, d, i_d, tp.end[d])
        if e < 0:
            continue
        entry = float(tp.O[d, e])
        px, _, _ = exit_trade(tp, d, e, side, math.nan, math.nan, int(tp.end[d]))
        rows.append(trade_row(d, side, entry, px, math.nan, e))
    return np.array(rows) if rows else EMPTY


def run_overnight_persist(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    x = prep(tp)
    D = len(tp.days)
    # overnight return earned by holding session d's close into session d+1's open, known at d+1's open
    on = np.full(D, np.nan)
    on[:-1] = tp.open_[1:] / tp.close[:-1] - 1
    rows = []
    for d in range(d0, min(d1, D - 2) + 1):
        if not (tp.ok[d] and tp.end[d] == 390 and tp.ok[d + 1] and math.isfinite(tp.close[d])):
            continue
        past = on[max(0, d - cfg["look"]):d]  # nights ending at or before today's open
        past = past[np.isfinite(past)]
        if len(past) < 0.75 * cfg["look"]:
            continue
        m = past.mean()
        if m == 0 or (cfg["mode"] == "long_only" and m < 0):
            continue
        side = 1 if m > 0 else -1
        entry = float(tp.close[d])
        if cfg["exit"] == "open":
            px = tp.open_[d + 1]
        else:
            px = tp.O[d + 1, 30] if math.isfinite(tp.O[d + 1, 30]) else x["Cff"][d + 1, 29]
        if not math.isfinite(px):
            continue
        rows.append(trade_row(d, side, entry, float(px), math.nan, int(tp.end[d]) - 1))
    return np.array(rows) if rows else EMPTY


def run_daily_swing(tp: Tape, cfg: dict, d0: int, d1: int, **_) -> np.ndarray:
    x = prep(tp)
    i_d = bar_closing_at(1548)
    D = len(tp.days)
    rows = []
    for d in range(d0, min(d1, D - 2) + 1):
        L = cfg["look"]
        if d - L < 0 or not (tp.ok[d] and tp.end[d] == 390 and tp.ok[d + 1]):
            continue
        ref, now = tp.close[d - L], x["Cff"][d, i_d]
        if not (math.isfinite(ref) and math.isfinite(now) and math.isfinite(tp.close[d])
                and math.isfinite(tp.close[d + 1])) or now == ref:
            continue
        side = 1 if now > ref else -1
        if cfg["dir"] == "fade":
            side = -side
        rows.append(trade_row(d, side, float(tp.close[d]), float(tp.close[d + 1]), math.nan, int(tp.end[d]) - 1))
    return np.array(rows) if rows else EMPTY


RUNNERS.update({"trend_hold": run_trend_hold, "overnight_persist": run_overnight_persist,
                "daily_swing": run_daily_swing})
ALL_FAMILIES = {**FAMILIES, **ROUND2}


def neighbours(grid: list[dict], i: int) -> list[int]:
    a = grid[i]
    return [j for j, b in enumerate(grid) if j != i and sum(a[k] != b[k] for k in a) == 1]
