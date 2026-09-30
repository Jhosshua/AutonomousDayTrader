# @steered SNARE-2 2026-09-30
"""Data loading, fill and cost model, and statistics for the ten stock day trade study (PLAN.md).

Forked from research/edge_hunt_2026_09_29/lib.py with this protocol's constants. Bars live in
dense [session, minute] matrices, index 0 is the bar starting 09:30 ET. Bar i starts at 09:30 + i
and closes at 09:31 + i. A signal decided on bar i fills at the open of bar i + 2 (60 seconds after
the signal bar closes), or i + 3, i + 4 if the earlier bar is missing.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

import numpy as np
from scipy import stats as sstats

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BARS = os.path.join(DATA, "bars")
N = 390

# ---------------------------------------------------------------- protocol constants (PLAN.md)
WARM_START = 20240603
WIN_START, DESIGN_END = 20240930, 20250930
HOLD_START, HOLD_END = 20251001, 20260929
HALVES = ((20240930, 20250331), (20250401, 20250930), (20251001, 20260331), (20260401, 20260929))
QUARTERS = ((20250401, 20250630), (20250701, 20250930), (20251001, 20251231),
            (20260101, 20260331), (20260401, 20260630), (20260701, 20260929))
MIN_PER_WEEK = 1.0
DESIGN_PF, DESIGN_T, NEIGH_T, NEIGH_T_NONE = 1.2, 2.0, 1.0, 2.5
HOLD_PF, HOLD_P, BH_Q = 1.15, 0.05, 0.10
COST_LEVELS = (1.0, 1.5, 2.0, 3.0)
NAMES = "TQQQ QQQ PLTR MSTR COIN META VRT AMD SPY SMH".split()
CLUSTER = {"SPY": "index", "QQQ": "index", "TQQQ": "index", "SMH": "semis", "AMD": "semis",
           "VRT": "semis", "MSTR": "crypto", "COIN": "crypto", "PLTR": "PLTR", "META": "META"}


def _calendar():
    cal = json.load(open(os.path.join(DATA, "calendar.json")))
    days, early = [], set()
    for c in cal:
        d = int(c["date"].replace("-", ""))
        if WARM_START <= d <= HOLD_END:
            days.append(d)
            if c["close"] != "16:00":
                early.add(d)
    return np.array(sorted(days), dtype=np.int64), early


DAYS, EARLY_CLOSES = _calendar()
_DAILY = None


def daily_official(sym: str) -> dict:
    """Official open, close, high, low per session from split adjusted daily bars."""
    global _DAILY
    if _DAILY is None:
        _DAILY = json.load(open(os.path.join(DATA, "daily.json")))
    out = {}
    for b in _DAILY[sym]["split"]:
        d = int(b["t"][:10].replace("-", ""))
        out[d] = (b["o"], b["h"], b["l"], b["c"], b["v"])
    return out


@dataclass
class Tape:
    sym: str
    days: np.ndarray
    O: np.ndarray
    H: np.ndarray
    L: np.ndarray
    C: np.ndarray
    V: np.ndarray            # 0 where no bar
    VW: np.ndarray
    end: np.ndarray          # first index after the session (390, or 210 on early closes)
    open_: np.ndarray        # official open
    close: np.ndarray        # official close
    prev_close: np.ndarray
    high: np.ndarray         # regular session high from minute bars
    low: np.ndarray
    prev_high: np.ndarray
    prev_low: np.ndarray
    ok: np.ndarray
    pre_hi: np.ndarray       # premarket 04:00 to 09:29
    pre_lo: np.ndarray
    pre_n: np.ndarray        # premarket bar count
    pre_v: np.ndarray        # premarket volume
    cache: dict = field(default_factory=dict)


def _shift(a: np.ndarray, k: int = 1) -> np.ndarray:
    return np.concatenate([np.full(k, np.nan), a[:-k]])


def load(sym: str, days: np.ndarray = DAYS) -> Tape:
    d = np.load(os.path.join(BARS, f"{sym}.npz"))
    di = np.searchsorted(days, d["day"])
    inday = (di < len(days)) & (days[np.minimum(di, len(days) - 1)] == d["day"])
    minute = d["minute"].astype(np.int64)
    reg = inday & (minute >= 570) & (minute < 960)
    pre = inday & (minute >= 240) & (minute < 570)
    D = len(days)
    mats = {}
    ri, rm = di[reg], minute[reg] - 570
    for k in ("o", "h", "l", "c", "vw"):
        m = np.full((D, N), np.nan)
        m[ri, rm] = d[k][reg]
        mats[k] = m
    V = np.zeros((D, N))
    V[ri, rm] = d["v"][reg]
    end = np.where(np.isin(days, list(EARLY_CLOSES)), 210, 390)
    cut = np.arange(N)[None, :] >= end[:, None]
    for m in mats.values():
        m[cut] = np.nan
    V[cut] = 0.0
    C = mats["c"]
    ok = np.isfinite(C).sum(axis=1) >= 0.8 * end
    with np.errstate(all="ignore"):
        high = np.where(ok, np.nanmax(np.where(cut, np.nan, mats["h"]), axis=1), np.nan)
        low = np.where(ok, np.nanmin(np.where(cut, np.nan, mats["l"]), axis=1), np.nan)
    off = daily_official(sym)
    open_ = np.array([off[x][0] if x in off else np.nan for x in days])
    close = np.array([off[x][3] if x in off else np.nan for x in days])
    open_ = np.where(ok, open_, np.nan)
    close = np.where(ok, close, np.nan)
    pi, ph, pl, pv = di[pre], d["h"][pre], d["l"][pre], d["v"][pre]
    pre_hi = np.full(D, np.nan)
    pre_lo = np.full(D, np.nan)
    np.fmax.at(pre_hi, pi, ph)
    np.fmin.at(pre_lo, pi, pl)
    pre_n = np.bincount(pi, minlength=D).astype(float)
    pre_v = np.bincount(pi, weights=pv, minlength=D)
    return Tape(sym, days, mats["o"], mats["h"], mats["l"], C, V, mats["vw"], end, open_, close,
                _shift(close), high, low, _shift(high), _shift(low), ok, pre_hi, pre_lo, pre_n, pre_v)


def day_index(days: np.ndarray, lo: int, hi: int) -> tuple[int, int]:
    return int(np.searchsorted(days, lo)), int(np.searchsorted(days, hi, side="right")) - 1


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


def trailing_mean(x: np.ndarray, n: int = 20, min_n: int = 15) -> np.ndarray:
    out = np.full(len(x), np.nan)
    hist: list[float] = []
    for i in range(len(x)):
        if len(hist) >= min_n:
            out[i] = float(np.mean(hist[-n:]))
        if np.isfinite(x[i]):
            hist.append(float(x[i]))
    return out


def rolling_beta(rs: np.ndarray, rb: np.ndarray, n: int = 60, min_n: int = 40) -> np.ndarray:
    out = np.full(len(rs), np.nan)
    for i in range(len(rs)):
        a, b = rs[max(0, i - n):i], rb[max(0, i - n):i]
        m = np.isfinite(a) & np.isfinite(b)
        if m.sum() >= min_n and np.var(b[m]) > 0:
            out[i] = np.cov(a[m], b[m], ddof=1)[0, 1] / np.var(b[m], ddof=1)
    return out


def ffill_close(tp: Tape) -> np.ndarray:
    if "Cff" not in tp.cache:
        C = tp.C
        idx = np.where(np.isfinite(C), np.arange(C.shape[1])[None, :], 0)
        np.maximum.accumulate(idx, axis=1, out=idx)
        tp.cache["Cff"] = C[np.arange(C.shape[0])[:, None], idx]
    return tp.cache["Cff"]


def running_low(tp: Tape) -> np.ndarray:
    if "runlow" not in tp.cache:
        tp.cache["runlow"] = np.fmin.accumulate(tp.L, axis=1)
    return tp.cache["runlow"]


# ---------------------------------------------------------------- fills and costs


def fill_index(O: np.ndarray, d: int, i: int, end: int) -> int:
    """Bar whose open fills a signal decided on bar i, or -1."""
    if i < 0:
        raise ValueError("signal bar before the open")
    for e in (i + 2, i + 3, i + 4):
        if e < end and math.isfinite(O[d, e]):
            return e
    return -1


def fixed_entry(O: np.ndarray, d: int, b: int, end: int) -> int:
    """Entry at a fixed bar open for rules known before the bar (no lag), or the next two bars."""
    for e in (b, b + 1, b + 2):
        if e < end and math.isfinite(O[d, e]):
            return e
    return -1


def ssr_blocks_short(tp: Tape, d: int, i: int) -> bool:
    """Rule 201 active for a short decided on bar i of session d."""
    if d >= 2 and math.isfinite(tp.prev_low[d]) and math.isfinite(tp.close[d - 2]) \
            and tp.prev_low[d] <= 0.9 * tp.close[d - 2]:
        return True
    pc = tp.prev_close[d]
    if not math.isfinite(pc):
        return False
    lo = running_low(tp)[d, min(i, N - 1)]
    return bool(math.isfinite(lo) and lo <= 0.9 * pc)


def price_at_open_or_before(tp: Tape, d: int, x: int) -> float:
    if x < tp.end[d] and math.isfinite(tp.O[d, x]):
        return float(tp.O[d, x])
    row = tp.C[d, :x]
    f = np.flatnonzero(np.isfinite(row))
    return float(row[f[-1]]) if len(f) else float("nan")


def exit_trade(tp: Tape, d: int, e: int, side: int, stop: float, target: float, x: int):
    """Scan bars e..x-1 for stop and target (stop first on a shared bar), else leave at the open of
    bar x. x >= end means the official close. Returns (exit_price, exit_bar, reason)."""
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
        return float(tp.close[d]), int(tp.end[d]), "close"
    return price_at_open_or_before(tp, d, x), x, "time"


def round_trip(price: float) -> float:
    return max(6e-4, 0.01 / price + 2e-4)


def leg_mult(bar: int, auction: bool) -> float:
    """Continuous fills before 09:45 or from 15:55 cost double. Auctions cost normal."""
    if auction:
        return 1.0
    return 2.0 if (bar < 15 or bar >= 385) else 1.0


REASONS = {"stop": 0, "target": 1, "close": 2, "time": 3}
TRADE_COLS = ("d", "side", "entry", "exit", "r1", "r15", "r2", "r3", "e", "x", "reason", "risk")


def trade_row(d: int, side: int, entry: float, exit_: float, e: int, x: int, reason: str,
              stop: float = math.nan, entry_auction: bool = False) -> tuple:
    """Net percent return at 1x, 1.5x, 2x and 3x cost. Stop exits pay max(1 cent, 3 bps) slippage."""
    gross = side * (exit_ - entry) / entry
    c = round_trip(entry)
    legs = c / 2 * leg_mult(e, entry_auction) + c / 2 * leg_mult(x, reason == "close")
    slip = max(0.01, 3e-4 * exit_) / entry if reason == "stop" else 0.0
    rets = [gross - m * (legs + slip) for m in COST_LEVELS]
    risk = abs(entry - stop) / entry if math.isfinite(stop) else math.nan
    return (d, side, entry, exit_, *rets, e, x, REASONS[reason], risk)


EMPTY = np.zeros((0, len(TRADE_COLS)))


def as_array(rows: list) -> np.ndarray:
    """Trades array. Rows with a missing price (for example no official close) are dropped."""
    if not rows:
        return EMPTY
    a = np.array(rows, dtype=float)
    return a[np.isfinite(a[:, 4])]


# ---------------------------------------------------------------- statistics

COL = {1.0: 4, 1.5: 5, 2.0: 6, 3.0: 7}


def slice_trades(trades: np.ndarray, days: np.ndarray, lo: int, hi: int) -> np.ndarray:
    if trades.size == 0:
        return trades
    td = days[trades[:, 0].astype(int)]
    return trades[(td >= lo) & (td <= hi)]


def daily_series(trades: np.ndarray, days: np.ndarray, lo: int, hi: int, mult: float = 1.0) -> np.ndarray:
    """Daily summed net return over every session in [lo, hi], zero on no trade days."""
    d0, d1 = day_index(days, lo, hi)
    out = np.zeros(d1 - d0 + 1)
    t = slice_trades(trades, days, lo, hi)
    if t.size:
        np.add.at(out, t[:, 0].astype(int) - d0, t[:, COL[mult]])
    return out


def ttest(x: np.ndarray) -> tuple[float, float]:
    if len(x) < 3:
        return 0.0, 1.0
    sd = x.std(ddof=1)
    if sd <= 0:
        return 0.0, 1.0
    t = x.mean() / sd * math.sqrt(len(x))
    return float(t), float(sstats.t.sf(t, len(x) - 1))


def summarize(trades: np.ndarray, days: np.ndarray, lo: int, hi: int, mult: float = 1.0) -> dict:
    """Stats for trades in [lo, hi]. t uses daily sums over trade days (edge hunt convention)."""
    d0, d1 = day_index(days, lo, hi)
    weeks = (d1 - d0 + 1) / 5.0
    t = slice_trades(trades, days, lo, hi)
    n = len(t)
    base = {"n": n, "per_week": n / weeks, "win": 0.0, "mean": 0.0, "pf": 0.0, "t": 0.0, "p": 1.0,
            "total": 0.0, "mean2": 0.0, "total2": 0.0}
    if n < 3:
        return base
    x = t[:, COL[mult]]
    wins, losses = x[x > 0].sum(), -x[x < 0].sum()
    di = t[:, 0].astype(int)
    dsum = np.bincount(di, weights=x)[np.bincount(di) > 0]
    tt, p = ttest(dsum)
    x2 = t[:, COL[2.0]]
    base.update(win=float((t[:, 4] > 0).mean()), mean=float(x.mean()),
                pf=float(wins / losses) if losses > 0 else float("inf"), t=tt, p=p,
                total=float(x.sum()), mean2=float(x2.mean()), total2=float(x2.sum()))
    return base


def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, float)
    n = len(p)
    if n == 0:
        return p
    order = np.argsort(p)
    q = p[order] * n / np.arange(1, n + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(q, 1.0)
    return out


def stationary_bootstrap(x: np.ndarray, mean_block: float = 5.0, draws: int = 10000,
                         seed: int = 20260930) -> tuple[float, float, float]:
    """Politis and Romano stationary bootstrap of the mean. Returns (lo5, hi95, one sided p of mean <= 0)."""
    rng = np.random.default_rng(seed)
    n = len(x)
    if n < 5:
        return math.nan, math.nan, 1.0
    p_new = 1.0 / mean_block
    means = np.empty(draws)
    xc = x - x.mean()  # null centred copy for the p value
    means_null = np.empty(draws)
    for k in range(draws):
        idx = np.empty(n, dtype=np.int64)
        idx[0] = rng.integers(n)
        starts = rng.random(n) < p_new
        jumps = rng.integers(n, size=n)
        for i in range(1, n):
            idx[i] = jumps[i] if starts[i] else (idx[i - 1] + 1) % n
        means[k] = x[idx].mean()
        means_null[k] = xc[idx].mean()
    p = float((means_null >= x.mean()).mean())
    return float(np.percentile(means, 5)), float(np.percentile(means, 95)), p


def deflated_sharpe(sr: float, T: int, n_trials: float, var_sr: float, skew: float, kurt: float) -> float:
    """Bailey and Lopez de Prado (2014). sr and var_sr are per period (daily). kurt is not excess."""
    if n_trials < 2 or var_sr <= 0 or T < 3:
        return math.nan
    g = 0.5772156649
    z = sstats.norm.ppf
    sr0 = math.sqrt(var_sr) * ((1 - g) * z(1 - 1 / n_trials) + g * z(1 - 1 / (n_trials * math.e)))
    den = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if den <= 0:
        return math.nan
    return float(sstats.norm.cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(den)))


def max_drawdown(daily: np.ndarray) -> tuple[float, float]:
    """Max drawdown and the open drawdown at the end, on compounded equity from daily returns."""
    eq = np.cumprod(1 + daily)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    dd = eq / peak - 1
    return float(dd.min()) if len(dd) else 0.0, float(dd[-1]) if len(dd) else 0.0
