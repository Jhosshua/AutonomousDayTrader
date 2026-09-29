# @steered SNARE-2 2026-09-29
"""Shared data loading, fill model and statistics for the edge hunt. See PROTOCOL.md.

Bars live in dense [session, minute] matrices, minute 0 = 09:30 ET bar. A bar at index i
closes at 09:31 + i. A signal decided on bar i fills at the open of bar i + 2 (or i + 3, i + 4
if the earlier bar is missing), which is the 60 second latency rule of the Tesla research.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

import numpy as np
from scipy import stats as sstats

HERE = os.path.dirname(os.path.abspath(__file__))
BARS = os.path.join(HERE, "data", "bars")
N = 390
EARLY_CLOSES = {20230703, 20231124, 20240703, 20241129, 20241224, 20250703, 20251128, 20251224}
IS_START, IS_END = 20231002, 20250630
OOS_START, OOS_END = 20250701, 20260928

TECH = "AAPL MSFT AMZN META GOOGL NFLX PLTR CRWD SNOW SHOP UBER ORCL ADBE".split()
SEMIS = "NVDA AVGO AMD MU SMCI INTC ARM QCOM MRVL".split()
FIN = "HOOD SOFI PYPL AFRM JPM BAC GS".split()
ENERGY = "XOM OXY".split()
# round 3 (PROTOCOL.md): internet and software to QQQ, energy to XLE, banks to XLF, rest SPY
TECH += "BABA PDD JD BIDU RBLX PINS SNAP LYFT ROKU U PATH AI UPST TTD CHWY ETSY DKNG IONQ".split()
ENERGY += "DVN APA HAL SLB EQT".split()
FIN += "KEY WAL".split()


def benchmark(sym: str) -> str:
    if sym in TECH:
        return "QQQ"
    if sym in SEMIS:
        return "SMH"
    if sym in FIN:
        return "XLF"
    if sym in ENERGY:
        return "XLE"
    return "SPY"


def calendar() -> np.ndarray:
    """Trading sessions, taken from SPY (the most complete tape)."""
    return np.unique(np.load(os.path.join(BARS, "SPY.npz"))["day"]).astype(np.int64)


@dataclass
class Tape:
    sym: str
    days: np.ndarray        # [D] YYYYMMDD
    O: np.ndarray           # [D, N]
    H: np.ndarray
    L: np.ndarray
    C: np.ndarray
    V: np.ndarray           # 0 where no bar
    VW: np.ndarray
    end: np.ndarray         # [D] first index after the session (390, or 210 on early closes)
    open_: np.ndarray       # [D] 09:30 bar open, nan if that bar is missing
    close: np.ndarray       # [D] last regular bar close
    prev_close: np.ndarray  # [D] close of the previous session
    prev_high: np.ndarray
    prev_low: np.ndarray
    ok: np.ndarray          # [D] session has data


def load(sym: str, days: np.ndarray) -> Tape:
    d = np.load(os.path.join(BARS, f"{sym}.npz"))
    di = np.searchsorted(days, d["day"])
    good = (di < len(days)) & (days[np.minimum(di, len(days) - 1)] == d["day"])
    di, mi = di[good], d["minute"][good].astype(np.int64) - 570
    D = len(days)
    mats = {}
    for k in ("o", "h", "l", "c", "vw"):
        m = np.full((D, N), np.nan)
        m[di, mi] = d[k][good]
        mats[k] = m
    V = np.zeros((D, N))
    V[di, mi] = d["v"][good]
    end = np.where(np.isin(days, list(EARLY_CLOSES)), 210, 390)
    cut = np.arange(N)[None, :] >= end[:, None]
    for m in mats.values():
        m[cut] = np.nan
    V[cut] = 0.0
    C = mats["c"]
    ok = np.isfinite(C).sum(axis=1) >= 0.8 * end
    close = np.full(D, np.nan)
    high = np.full(D, np.nan)
    low = np.full(D, np.nan)
    for i in range(D):
        if not ok[i]:
            continue
        row = C[i, :end[i]]
        f = np.flatnonzero(np.isfinite(row))
        if len(f) and f[-1] >= end[i] - 5:
            close[i] = row[f[-1]]
        high[i] = np.nanmax(mats["h"][i, :end[i]])
        low[i] = np.nanmin(mats["l"][i, :end[i]])
    shift = lambda a: np.concatenate([[np.nan], a[:-1]])
    open_ = np.where(ok, mats["o"][:, 0], np.nan)
    return Tape(sym, days, mats["o"], mats["h"], mats["l"], C, V, mats["vw"], end, open_, close,
                shift(close), shift(high), shift(low), ok)


def trailing_std(x: np.ndarray, n: int = 20, min_n: int = 15) -> np.ndarray:
    """Std of the prior n finite values, excluding today."""
    out = np.full(len(x), np.nan)
    hist: list[float] = []
    for i in range(len(x)):
        if len(hist) >= min_n:
            out[i] = float(np.std(hist[-n:], ddof=1))
        if np.isfinite(x[i]):
            hist.append(float(x[i]))
    return out


def cost_frac(price: float, stress: bool = False) -> float:
    c = max(6e-4, 0.01 / price + 2e-4)
    return 2 * c if stress else c


def fill_index(O: np.ndarray, d: int, i: int, end: int) -> int:
    """Bar whose open fills a signal decided on bar i, or -1."""
    if i < 0:
        raise ValueError("signal bar before the open")
    for e in (i + 2, i + 3, i + 4):
        if e < end and math.isfinite(O[d, e]):
            return e
    return -1


def price_at_open_or_before(tp: Tape, d: int, x: int) -> float:
    if x < tp.end[d] and math.isfinite(tp.O[d, x]):
        return float(tp.O[d, x])
    row = tp.C[d, :x]
    f = np.flatnonzero(np.isfinite(row))
    return float(row[f[-1]]) if len(f) else float("nan")


def exit_trade(tp: Tape, d: int, e: int, side: int, stop: float, target: float, x: int):
    """Scan bars e..x-1 for stop and target (stop first on a shared bar), else leave at the open
    of bar x. x == tp.end[d] means market on close (last regular bar close).
    Returns (exit_price, exit_bar, reason)."""
    x = min(x, int(tp.end[d]))
    h, l, o = tp.H[d, e:x], tp.L[d, e:x], tp.O[d, e:x]
    with np.errstate(invalid="ignore"):
        if side > 0:
            s_hit = l <= stop if math.isfinite(stop) else np.zeros(len(h), bool)
            t_hit = h > target if math.isfinite(target) else np.zeros(len(h), bool)
        else:
            s_hit = h >= stop if math.isfinite(stop) else np.zeros(len(h), bool)
            t_hit = l < target if math.isfinite(target) else np.zeros(len(h), bool)
    ks = int(np.argmax(s_hit)) if s_hit.any() else 10**9
    kt = int(np.argmax(t_hit)) if t_hit.any() else 10**9
    if ks < 10**9 and ks <= kt:
        op = o[ks]
        through = math.isfinite(op) and ((side > 0 and op < stop) or (side < 0 and op > stop))
        return (float(op) if through else stop), e + ks, "stop"
    if kt < 10**9:
        return target, e + kt, "target"
    if x >= tp.end[d]:
        row = tp.C[d, :x]
        f = np.flatnonzero(np.isfinite(row))
        return float(row[f[-1]]), x - 1, "close"
    return price_at_open_or_before(tp, d, x), x, "time"


def trade_row(d: int, side: int, entry: float, exit_: float, stop: float, e: int = -1) -> tuple:
    """Net return (fraction) and net R at normal and stress cost. e is the entry bar."""
    gross = side * (exit_ - entry) / entry
    c, cs = cost_frac(entry), cost_frac(entry, True)
    risk = abs(entry - stop) / entry if math.isfinite(stop) else float("nan")
    return (d, side, entry, exit_, gross - c, gross - cs,
            (gross - c) / risk if risk == risk else float("nan"),
            (gross - cs) / risk if risk == risk else float("nan"), e)


TRADE_COLS = ("d", "side", "entry", "exit", "ret", "ret_s", "r", "r_s", "e")


def summarize(days: np.ndarray, trades: np.ndarray, use_r: bool, lo: int, hi: int) -> dict:
    """Stats for trades whose session is in [lo, hi]. t uses daily sums."""
    sessions = int(((days >= lo) & (days <= hi)).sum())
    weeks = sessions / 5.0
    if trades.size == 0:
        return {"n": 0, "per_week": 0.0, "win": 0.0, "mean": 0.0, "pf": 0.0, "t": 0.0, "p": 1.0,
                "mean_s": 0.0, "total": 0.0, "total_s": 0.0}
    tday = days[trades[:, 0].astype(int)]
    m = (tday >= lo) & (tday <= hi)
    t = trades[m]
    n = len(t)
    if n < 3:
        return {"n": n, "per_week": n / weeks, "win": 0.0, "mean": 0.0, "pf": 0.0, "t": 0.0, "p": 1.0,
                "mean_s": 0.0, "total": 0.0, "total_s": 0.0}
    col, col_s = (6, 7) if use_r else (4, 5)
    x, xs = t[:, col], t[:, col_s]
    wins, losses = x[x > 0].sum(), -x[x < 0].sum()
    dsum = np.bincount(t[:, 0].astype(int), weights=x)
    dsum = dsum[np.bincount(t[:, 0].astype(int)) > 0]
    sd = dsum.std(ddof=1)
    tstat = dsum.mean() / sd * math.sqrt(len(dsum)) if sd > 0 else 0.0
    p = float(sstats.t.sf(tstat, len(dsum) - 1))
    return {"n": n, "per_week": n / weeks, "win": float((t[:, 4] > 0).mean()), "mean": float(x.mean()),
            "pf": float(wins / losses) if losses > 0 else float("inf"), "t": float(tstat), "p": p,
            "mean_s": float(xs.mean()), "total": float(x.sum()), "total_s": float(xs.sum())}
