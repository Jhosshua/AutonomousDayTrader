"""Backtester for the bot's "Ride the Trend" strategy (backend/app/strategies/vwap_pullback.py).

Signal logic is a faithful re-implementation of VWAPPullbackStrategy.on_bar (parity-checked by
parity.py against the real class). Execution replicates main.py + adaptation.py + bracket.py:
phase gate, SPY/QQQ index trend filter, VIX stop/size multipliers, 0.4% stop floor / 4% ceiling,
1% risk sizing capped at $25k, 3 slots, 2 per sector, band-or-fallback targets, 50% scale-out
at T1, breakeven ratchet, 1.5x ATR14 trail on the runner, hard flatten at 15:55.

Every knob is a parameter so hypotheses can be tested as variants.
"""
from __future__ import annotations
import csv, json, math, os, sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
import zoneinfo
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
ET = zoneinfo.ZoneInfo("America/New_York")
SYMS = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]
SECTOR = {"SPY": "Index", "QQQ": "Index", "AAPL": "Technology", "NVDA": "Semiconductors",
          "AMD": "Semiconductors", "MSFT": "Software", "PLTR": "Software", "TSLA": "Consumer Discretionary",
          "AMZN": "Consumer Discretionary", "GOOGL": "Communication Services", "META": "Communication Services",
          "COIN": "Fintech/Crypto"}
MIN_STOP_PCT = 0.0040
MAX_STOP_PCT = 0.0400
VIX_BOUNDS = (15.0, 25.0, 35.0)
VIX_SIZE = (1.20, 1.00, 0.70, 0.35)
VIX_STOP = (0.85, 1.00, 1.40, 2.00)


# ----------------------------------------------------------------------------- data
@dataclass
class Day:
    sym: str
    date: str
    m: np.ndarray      # minute of day in ET (570 = 09:30)
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    ts: List[datetime] # UTC bar start


_cache: Dict[str, Dict[str, Day]] = {}


def load_symbol(sym: str) -> Dict[str, Day]:
    if sym in _cache:
        return _cache[sym]
    npz = os.path.join(DATA, "bars", f"{sym}.npz")
    if os.path.exists(npz):
        z = np.load(npz, allow_pickle=True)
        days = z["days"].item()
        _cache[sym] = days
        return days
    rows = defaultdict(list)
    with open(os.path.join(DATA, "bars", f"{sym}.jsonl")) as f:
        for line in f:
            b = json.loads(line)
            t = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
            et = t.astimezone(ET)
            m = et.hour * 60 + et.minute
            if m < 570 or m >= 960:      # regular session 09:30 <= t < 16:00
                continue
            rows[et.strftime("%Y-%m-%d")].append((m, b["o"], b["h"], b["l"], b["c"], b["v"], t))
    days = {}
    for d, r in rows.items():
        r.sort(key=lambda x: x[0])
        arr = np.array([x[:6] for x in r], dtype=float)
        days[d] = Day(sym, d, arr[:, 0].astype(int), arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4], arr[:, 5],
                      [x[6] for x in r])
    np.savez_compressed(npz, days=np.array(days, dtype=object))
    _cache[sym] = days
    return days


def load_vix() -> Dict[str, Tuple[float, float]]:
    out = {}
    with open(os.path.join(DATA, "VIX_History.csv")) as f:
        rd = csv.DictReader(f)
        for r in rd:
            mm, dd, yy = r["DATE"].split("/")
            out[f"{yy}-{mm}-{dd}"] = (float(r["OPEN"]), float(r["CLOSE"]))
    return out


def vix_regime(v: float) -> Tuple[int, float, float]:
    idx = 0 if v < VIX_BOUNDS[0] else 1 if v < VIX_BOUNDS[1] else 2 if v < VIX_BOUNDS[2] else 3
    return idx, VIX_SIZE[idx], VIX_STOP[idx]


# ----------------------------------------------------------------------------- helpers (exact copies of base.py math)
def ema_seeded(prices: List[float], period: int) -> float:
    if not prices:
        return 0.0
    if len(prices) <= period:
        return sum(prices) / len(prices)
    alpha = 2.0 / (period + 1.0)
    e = sum(prices[:period]) / period
    for p in prices[period:]:
        e = alpha * p + (1.0 - alpha) * e
    return e


def atr_wilder(h, l, c, period=14) -> float:
    n = len(h)
    if n == 0:
        return 0.01
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    if len(tr) <= period:
        return max(0.01, sum(tr) / len(tr))
    a = sum(tr[:period]) / period
    for x in tr[period:]:
        a = (a * (period - 1) + x) / period
    return max(0.01, a)


def resolve_stop(entry: float, raw_dist: float, is_long: bool) -> Tuple[float, float]:
    risk = max(entry * MIN_STOP_PCT, raw_dist)
    if is_long:
        stop = math.floor((entry - risk) * 10000) / 10000
    else:
        stop = math.ceil((entry + risk) * 10000) / 10000
    return stop, abs(entry - stop)


# ----------------------------------------------------------------------------- signal generator
@dataclass
class SigParams:
    ema_fast: int = 20
    ema_slow: int = 50
    min_bars: int = 50            # production: ema_slow
    cooldown_min: int = 15
    zone_lo: float = 0.2          # long zone [vwap - zone_lo*std, vwap + zone_hi*std]
    zone_hi: float = 0.3
    wick_min: float = 0.30
    vol_min_ratio: float = 1.20
    require_both: bool = False    # wick AND volume instead of OR
    stop_band: float = 0.50
    tp1_band: float = 1.0
    tp2_band: float = 2.0
    t1_r: float = 0.80
    t2_r: float = 1.80
    allow_prior_zone: bool = True
    long_only: bool = False
    short_only: bool = False
    min_std_pct: float = 0.0      # require std/entry >= this
    max_std_pct: float = 9.0
    ema_gap_min_pct: float = 0.0  # require |ema_fast-ema_slow|/price >= this
    start_min: int = 570          # first bar accepted (09:30)
    first_signal_only: bool = False
    min_day_move_pct: float = -9.0   # sgn*(close/open0930-1) >= this
    min_vwap_slope_pct: float = -9.0 # sgn*(vwap - vwap 30 bars ago)/c >= this
    min_bars_since_cross: int = 0    # bars since last close on the wrong side of VWAP
    max_nth_signal: int = 99
    end_min: int = 945            # last bar accepted exclusive (15:45)


@dataclass
class Signal:
    sym: str
    i: int                 # bar index in day
    m: int
    side: str              # LONG/SHORT
    entry: float
    stop: float
    risk: float
    tp1: float
    tp2: float
    fb1: bool
    fb2: bool
    feats: dict


def gen_signals(day: Day, p: SigParams) -> List[Signal]:
    """Bar-by-bar replica of VWAPPullbackStrategy.on_bar."""
    out: List[Signal] = []
    n = len(day.m)
    sess_pv = 0.0; sess_v = 0.0; sess_pv2 = 0.0
    recent_c: List[float] = []
    recent_v: List[float] = []
    in_zone = False
    last_sig_m: Optional[int] = None
    max_recent = p.ema_slow * 2
    sh, sl, sc = [], [], []
    vwap_hist: List[float] = []
    open0 = None; day_hi = -1e9; day_lo = 1e9; last_below = -1; last_above = -1; nsig = 0
    for i in range(n):
        m = int(day.m[i])
        if m < p.start_min or m >= p.end_min:
            continue
        o, h, l, c, v = day.o[i], day.h[i], day.l[i], day.c[i], day.v[i]
        recent_c.append(c); recent_v.append(v)
        if len(recent_c) > max_recent:
            del recent_c[:-max_recent]; del recent_v[:-max_recent]
        tp = (h + l + c) / 3.0
        sess_pv += tp * v; sess_v += v; sess_pv2 += tp * tp * v
        sh.append(h); sl.append(l); sc.append(c)
        if open0 is None: open0 = o
        day_hi = max(day_hi, h); day_lo = min(day_lo, l)
        cur_vwap = sess_pv / sess_v if sess_v > 0 else c
        vwap_hist.append(cur_vwap)
        if c < cur_vwap: last_below = len(sc) - 1
        if c > cur_vwap: last_above = len(sc) - 1
        if len(recent_c) < p.min_bars:
            continue
        if last_sig_m is not None and (m - last_sig_m) * 60 < p.cooldown_min * 60:
            continue
        if sess_v <= 0:
            vwap, std = 0.0, 0.0
        else:
            vwap = sess_pv / sess_v
            std = math.sqrt(max(0.0, sess_pv2 / sess_v - vwap * vwap))
        if std <= 0.001:
            std = atr_wilder(sh, sl, sc, 14)
        ef = ema_seeded(recent_c, p.ema_fast)
        es = ema_seeded(recent_c, p.ema_slow)
        bull = ef > es; bear = ef < es
        prior = recent_v[:-1][-10:]
        sma10 = (sum(prior) / len(prior)) if prior else float(v)  # calculate_sma over <10 items = mean
        if len(prior) < 10 and prior:
            sma10 = sum(prior) / len(prior)
        cr = max(0.01, h - l)
        lw = min(o, c) - l
        uw = h - max(o, c)
        if v <= 0 or sma10 <= 0:
            continue
        gap_ok = abs(ef - es) / c >= p.ema_gap_min_pct
        std_ok = p.min_std_pct <= std / c <= p.max_std_pct
        k = len(sc) - 1
        vs30 = (vwap - vwap_hist[max(0, k - 30)]) / c
        feats_common = dict(vwap=vwap, std=std, std_pct=std / c, ema_fast=ef, ema_slow=es,
                            ema_gap_pct=(ef - es) / c, volume_ratio=v / sma10, sma10=sma10,
                            bars_seen=len(recent_c), day_move_pct=(c / open0 - 1), vwap_slope30=vs30,
                            range_pos=((c - day_lo) / (day_hi - day_lo)) if day_hi > day_lo else 0.5,
                            bars_since_cross_long=k - last_below, bars_since_cross_short=k - last_above,
                            nth_signal=nsig + 1, day_range_pct=(day_hi - day_lo) / c)
        if bull:
            zl, zh = vwap - p.zone_lo * std, vwap + p.zone_hi * std
            tested = (zl <= l <= zh) or (zl <= c <= zh)
            bounce = c > o and c >= vwap
            wick = lw >= p.wick_min * cr
            volc = v >= p.vol_min_ratio * sma10
            conf = (wick and volc) if p.require_both else (wick or volc)
            zone_ok = tested or (in_zone and p.allow_prior_zone)
            if zone_ok and bounce and conf:
                nsig += 1
                extra_ok = ((c / open0 - 1) >= p.min_day_move_pct and vs30 >= p.min_vwap_slope_pct
                            and (k - last_below) >= p.min_bars_since_cross and nsig <= p.max_nth_signal
                            and (not p.first_signal_only or nsig == 1))
                if not p.short_only and gap_ok and std_ok and extra_ok:
                    entry = c
                    stop = round(vwap - p.stop_band * std, 4)
                    mind = entry * MIN_STOP_PCT
                    raw = max(mind, std * 0.8) if entry - stop < mind else (entry - stop)
                    stop, risk = resolve_stop(entry, raw, True)
                    tp1 = round(vwap + p.tp1_band * std, 4)
                    fb1 = tp1 < round(entry + 0.5 * risk, 4)
                    if fb1:
                        tp1 = round(entry + p.t1_r * risk, 4)
                    tp2 = round(vwap + p.tp2_band * std, 4)
                    fb2 = tp2 <= tp1
                    if fb2:
                        tp2 = round(entry + p.t2_r * risk, 4)
                    f = dict(feats_common, tested=tested, prior_zone=in_zone, wick_ratio=lw / cr,
                             wick=wick, volc=volc, raw_risk=raw, floored=(risk > raw + 1e-9),
                             close_vs_vwap_pct=(c - vwap) / c)
                    out.append(Signal(day.sym, i, m, "LONG", entry, stop, risk, tp1, tp2, fb1, fb2, f))
                in_zone = False
                last_sig_m = m
            elif tested:
                in_zone = True
            else:
                in_zone = False
        elif bear:
            zl, zh = vwap - p.zone_hi * std, vwap + p.zone_lo * std
            tested = (zl <= h <= zh) or (zl <= c <= zh)
            rej = c < o and c <= vwap
            wick = uw >= p.wick_min * cr
            volc = v >= p.vol_min_ratio * sma10
            conf = (wick and volc) if p.require_both else (wick or volc)
            zone_ok = tested or (in_zone and p.allow_prior_zone)
            if zone_ok and rej and conf:
                nsig += 1
                extra_ok = (-(c / open0 - 1) >= p.min_day_move_pct and -vs30 >= p.min_vwap_slope_pct
                            and (k - last_above) >= p.min_bars_since_cross and nsig <= p.max_nth_signal
                            and (not p.first_signal_only or nsig == 1))
                if not p.long_only and gap_ok and std_ok and extra_ok:
                    entry = c
                    stop = round(vwap + p.stop_band * std, 4)
                    mind = entry * MIN_STOP_PCT
                    raw = max(mind, std * 0.8) if stop - entry < mind else (stop - entry)
                    stop, risk = resolve_stop(entry, raw, False)
                    tp1 = round(vwap - p.tp1_band * std, 4)
                    fb1 = tp1 > round(entry - 0.5 * risk, 4)
                    if fb1:
                        tp1 = round(entry - p.t1_r * risk, 4)
                    tp2 = round(vwap - p.tp2_band * std, 4)
                    fb2 = tp2 >= tp1
                    if fb2:
                        tp2 = round(entry - p.t2_r * risk, 4)
                    f = dict(feats_common, tested=tested, prior_zone=in_zone, wick_ratio=uw / cr,
                             wick=wick, volc=volc, raw_risk=raw, floored=(risk > raw + 1e-9),
                             close_vs_vwap_pct=(c - vwap) / c)
                    out.append(Signal(day.sym, i, m, "SHORT", entry, stop, risk, tp1, tp2, fb1, fb2, f))
                in_zone = False
                last_sig_m = m
            elif tested:
                in_zone = True
            else:
                in_zone = False
    return out


# ----------------------------------------------------------------------------- index trend filter (market_filter.py replica)
class IndexState:
    def __init__(self):
        self.pv = 0.0; self.vol = 0.0; self.closes: List[float] = []; self.first_open = None
        self.last = 0.0; self.vwap = 0.0; self.ema9 = 0.0; self.ema21 = 0.0; self.n = 0

    def update(self, o, h, l, c, v):
        if self.first_open is None:
            self.first_open = o
        tp = (h + l + c) / 3.0
        self.pv += tp * v; self.vol += v
        self.vwap = self.pv / self.vol if self.vol > 0 else c
        self.closes.append(c); self.last = c; self.n += 1
        self.ema9 = _ema_stream(self.ema9, c, 9, self.n)
        self.ema21 = _ema_stream(self.ema21, c, 21, self.n)

    def bullish(self, db=0.0003):
        return self.vwap > 0 and self.n > 0 and self.last > self.vwap * (1 + db) and (self.ema9 >= self.ema21 or self.n < 5)

    def bearish(self, db=0.0003):
        return self.vwap > 0 and self.n > 0 and self.last < self.vwap * (1 - db) and (self.ema9 <= self.ema21 or self.n < 5)


def _ema_stream(prev, c, period, n):
    if n == 1:
        return c
    a = 2.0 / (period + 1.0)
    return a * c + (1 - a) * prev


def index_trend(spy: IndexState, qqq: IndexState) -> str:
    if spy.n == 0 or qqq.n == 0:
        return "UNKNOWN"
    if spy.n < 3 or qqq.n < 3:
        su = spy.last >= spy.first_open; qu = qqq.last >= qqq.first_open
        return "BULLISH" if (su and qu) else "BEARISH" if (not su and not qu) else "NEUTRAL"
    if spy.bullish() and qqq.bullish():
        return "BULLISH"
    if spy.bearish() and qqq.bearish():
        return "BEARISH"
    return "NEUTRAL"


# ----------------------------------------------------------------------------- execution simulator
@dataclass
class ExecParams:
    equity: float = 50000.0
    risk_pct: float = 0.01
    max_alloc_pct: float = 0.50
    max_notional: float = 25000.0
    max_positions: int = 3
    max_per_sector: int = 2
    use_index_filter: bool = True
    use_vix: bool = True
    vix_source: str = "open"        # open|close|none
    phase_block_midday: bool = True # 11:30-14:00 blocked
    allow_morning: bool = True      # 09:30-11:30
    allow_afternoon: bool = True    # 14:00-15:45
    scale_out: bool = True          # 50% at T1
    breakeven_after_t1: bool = True
    be_buffer_mult: float = 1.0
    trail_after_t1: bool = True
    trail_atr_mult: float = 1.5
    trail_from_start: bool = False
    time_stop_min: int = 0          # 0 = none
    time_stop_only_if_underwater: bool = False
    flatten_min: int = 955          # 15:55
    slip_bps: float = 1.5           # half-spread + slippage on market fills
    stop_slip_bps: float = 1.5
    target_needs_through: bool = False  # limit fills need price beyond target (not just touch)
    t1_r_override: float = 0.0      # if >0, force T1 = entry +/- r*risk (ignores band)
    t2_r_override: float = 0.0
    stop_atr_mult: float = 0.0      # if >0, stop = entry -/+ mult*ATR14 (floored)
    same_bar_stop_first: bool = True
    symbols: Tuple[str, ...] = tuple(SYMS)
    entry_mode: str = "market"      # market | limit_close | limit_vwap  (limit: rest for limit_wait_bars, no slippage)
    limit_wait_bars: int = 3


@dataclass
class Trade:
    sym: str; date: str; side: str; entry_m: int; exit_m: int; entry: float; stop0: float; risk: float
    qty: int; q1: int; q2: int; tp1: float; tp2: float; fb1: bool; fb2: bool
    pnl: float; r: float; exit_reason: str; t1_hit: bool; t2_hit: bool
    mfe_r: float; mae_r: float; hold_min: int; vix: float; vix_regime: int; trend: str
    stop_pct: float; raw_stop_pct: float; floored: bool; notional: float; cap_hit: str
    feats: dict = field(default_factory=dict)


class Position:
    def __init__(self, sig: Signal, qty, q1, q2, entry, stop, risk, tp1, tp2, trend, vix, vreg, cap_hit):
        self.sig = sig; self.sym = sig.sym; self.side = sig.side; self.qty = qty; self.rem = qty
        self.q1 = q1; self.q2 = q2; self.entry = entry; self.stop = stop; self.stop0 = stop; self.risk = risk
        self.tp1 = tp1; self.tp2 = tp2; self.t1_hit = False; self.t2_hit = False
        self.peak = entry; self.pnl = 0.0; self.mfe = 0.0; self.mae = 0.0
        self.trend = trend; self.vix = vix; self.vreg = vreg; self.cap_hit = cap_hit
        self.entry_i = sig.i; self.entry_m = sig.m


def build_position(g: Signal, entry_px: float, sp: SigParams, ep: ExecParams, vstop: float, vsize: float,
                   atr_now: float, trend: str, vix: float, vreg: int) -> Optional[Position]:
    """Adapted stop, risk-engine ceiling, sizing, band-or-fallback targets, scale-out split (main.py + bracket.py)."""
    is_long = g.side == "LONG"
    sgn = 1.0 if is_long else -1.0
    # structural stop distance from the signal's band stop, measured from the actual entry
    raw = sgn * (entry_px - g.stop)
    if raw < entry_px * MIN_STOP_PCT:
        raw = max(entry_px * MIN_STOP_PCT, g.feats["std"] * 0.8) if raw < entry_px * MIN_STOP_PCT else raw
    if ep.stop_atr_mult > 0:
        raw = ep.stop_atr_mult * atr_now
    adist = raw * vstop
    if raw > entry_px * (MAX_STOP_PCT + 1e-6):
        adist = max(raw, adist)
    stop, risk = resolve_stop(entry_px, adist, is_long)
    if risk / entry_px > MAX_STOP_PCT + 1e-6:
        return None
    rd = ep.equity * ep.risk_pct * vsize
    q_risk = math.floor(rd / risk)
    q_cap = math.floor(min(ep.equity * ep.max_alloc_pct, ep.max_notional) / entry_px)
    qty = max(0, min(q_risk, q_cap))
    cap_hit = "notional" if q_cap < q_risk else "risk"
    if qty <= 0:
        return None
    fb = max(0.01, entry_px * 0.0005)
    buffered = risk + fb
    reward = sgn * (g.tp1 - entry_px)
    if g.fb1 or reward + 1e-9 < 0.5 * buffered:
        t1 = round(entry_px + sgn * sp.t1_r * risk, 2); t2 = round(entry_px + sgn * sp.t2_r * risk, 2)
    else:
        t1 = round(g.tp1, 2)
        second = sgn * (g.tp2 - entry_px)
        t2 = round(entry_px + sgn * sp.t2_r * risk, 2) if (g.fb2 or second <= reward) else round(g.tp2, 2)
    if ep.t1_r_override > 0:
        t1 = round(entry_px + sgn * ep.t1_r_override * risk, 2)
    if ep.t2_r_override > 0:
        t2 = round(entry_px + sgn * ep.t2_r_override * risk, 2)
    q1 = max(1, qty // 2) if qty > 1 else 1
    q2 = qty - q1 if qty > 1 else 0
    if not ep.scale_out:
        q1, q2 = qty, 0
    return Position(g, qty, q1, q2, entry_px, stop, risk, t1, t2, trend, vix, vreg, cap_hit)


def run_day(date: str, days: Dict[str, Day], sp: SigParams, ep: ExecParams, vix_map) -> List[Trade]:
    trades: List[Trade] = []
    syms = [s for s in ep.symbols if s in days]
    if not syms or "SPY" not in days or "QQQ" not in days:
        return trades
    sigs: Dict[str, Dict[int, Signal]] = {}
    for s in syms:
        sigs[s] = {g.i: g for g in gen_signals(days[s], sp)}
    # index filter state, driven by SPY/QQQ regardless of whether they are traded
    spy, qqq = IndexState(), IndexState()
    idx_by_m = {}
    for s, st in (("SPY", spy), ("QQQ", qqq)):
        d = days[s]
        idx_by_m[s] = {int(d.m[i]): i for i in range(len(d.m))}
    vix_o, vix_c = vix_map.get(date, (20.0, 20.0))
    vix = vix_o if ep.vix_source == "open" else vix_c
    vreg, vsize, vstop = vix_regime(vix) if ep.use_vix else (1, 1.0, 1.0)
    pos: Dict[str, Position] = {}
    pending: Dict[str, tuple] = {}
    ptr = {s: 0 for s in syms}
    # precompute ATR14 (true range, last 14 bars) per symbol per bar for the trail
    atr = {}
    for s in syms:
        d = days[s]
        tr = np.empty(len(d.c)); tr[0] = d.h[0] - d.l[0]
        if len(d.c) > 1:
            tr[1:] = np.maximum(d.h[1:] - d.l[1:], np.maximum(abs(d.h[1:] - d.c[:-1]), abs(d.l[1:] - d.c[:-1])))
        cs = np.cumsum(np.insert(tr, 0, 0.0))
        a = np.empty(len(d.c))
        for i in range(len(d.c)):
            k = min(14, i + 1)
            a[i] = max(0.01, (cs[i + 1] - cs[i + 1 - k]) / k)
        atr[s] = a
    for m in range(570, 960):
        # 1) feed index filter
        for s, st in (("SPY", spy), ("QQQ", qqq)):
            i = idx_by_m[s].get(m)
            if i is not None:
                d = days[s]; st.update(d.o[i], d.h[i], d.l[i], d.c[i], d.v[i])
        trend = index_trend(spy, qqq)
        # 2a) pending limit entries
        for s in list(pending.keys()):
            g, limit_px, expire_j = pending[s]
            d = days[s]
            if s not in idx_by_m:
                idx_by_m[s] = {int(d.m[jj]): jj for jj in range(len(d.m))}
            j = idx_by_m[s].get(m)
            if j is None or j <= g.i:
                continue
            if j > expire_j or m >= ep.flatten_min - 5:
                del pending[s]; continue
            o, h, l = d.o[j], d.h[j], d.l[j]
            hit = (l <= limit_px) if g.side == "LONG" else (h >= limit_px)
            if hit:
                px = min(o, limit_px) if g.side == "LONG" else max(o, limit_px)
                built = build_position(g, px, sp, ep, vstop, vsize, atr[s][j], trend, vix, vreg)
                del pending[s]
                if built is not None:
                    built.entry_i = j; built.entry_m = m
                    pos[s] = built
        # 2) manage open positions on this bar
        for s in list(pos.keys()):
            d = days[s]; i = idx_by_m.get(s)
            p = pos[s]
            if s not in idx_by_m:
                idx_by_m[s] = {int(d.m[j]): j for j in range(len(d.m))}
            j = idx_by_m[s].get(m)
            if j is None or j <= p.entry_i:
                continue
            o, h, l, c = d.o[j], d.h[j], d.l[j], d.c[j]
            sgn = 1.0 if p.side == "LONG" else -1.0
            # excursions
            p.mfe = max(p.mfe, sgn * ((h if sgn > 0 else l) - p.entry))
            p.mae = max(p.mae, -sgn * ((l if sgn > 0 else h) - p.entry))
            reason = None
            # flatten
            if m >= ep.flatten_min:
                px = c * (1 - sgn * ep.slip_bps / 1e4)
                p.pnl += sgn * (px - p.entry) * p.rem; p.rem = 0; reason = "FLATTEN"
            else:
                # time stop
                if ep.time_stop_min and (m - p.entry_m) >= ep.time_stop_min and not p.t1_hit:
                    if not ep.time_stop_only_if_underwater or sgn * (c - p.entry) <= 0:
                        px = c * (1 - sgn * ep.slip_bps / 1e4)
                        p.pnl += sgn * (px - p.entry) * p.rem; p.rem = 0; reason = "TIME"
                if p.rem > 0:
                    stop_hit = (l <= p.stop) if sgn > 0 else (h >= p.stop)
                    t1_hit = (not p.t1_hit) and ((h >= p.tp1) if sgn > 0 else (l <= p.tp1))
                    t2_hit = (h >= p.tp2) if sgn > 0 else (l <= p.tp2)
                    if ep.target_needs_through:
                        t1_hit = (not p.t1_hit) and ((h > p.tp1) if sgn > 0 else (l < p.tp1))
                        t2_hit = (h > p.tp2) if sgn > 0 else (l < p.tp2)
                    gap_through = (o <= p.stop) if sgn > 0 else (o >= p.stop)
                    if stop_hit and (ep.same_bar_stop_first or not (t1_hit or t2_hit)):
                        base = o if gap_through else p.stop
                        px = base * (1 - sgn * ep.stop_slip_bps / 1e4)
                        p.pnl += sgn * (px - p.entry) * p.rem; p.rem = 0
                        reason = "STOP" if not p.t1_hit else "STOP_AFTER_T1"
                    else:
                        if t1_hit and p.q1 > 0 and not p.t1_hit:
                            p.t1_hit = True
                            fq = p.q1 if ep.scale_out else p.rem
                            p.pnl += sgn * (p.tp1 - p.entry) * fq; p.rem -= fq
                            if p.rem <= 0:
                                reason = "T1_FULL"
                            elif ep.breakeven_after_t1:
                                buf = max(0.04, round(p.entry * 0.0005, 2)) * ep.be_buffer_mult
                                ns = round(p.entry + sgn * buf, 4)
                                p.stop = max(p.stop, ns) if sgn > 0 else min(p.stop, ns)
                        if p.rem > 0 and t2_hit and (p.t1_hit or not ep.scale_out):
                            p.pnl += sgn * (p.tp2 - p.entry) * p.rem; p.rem = 0; p.t2_hit = True
                            reason = "T2"
                        elif p.rem > 0 and stop_hit and not ep.same_bar_stop_first:
                            px = p.stop * (1 - sgn * ep.stop_slip_bps / 1e4)
                            p.pnl += sgn * (px - p.entry) * p.rem; p.rem = 0
                            reason = "STOP" if not p.t1_hit else "STOP_AFTER_T1"
                    # trailing update (after fills, like production)
                    if p.rem > 0 and ((p.t1_hit and ep.trail_after_t1) or ep.trail_from_start):
                        p.peak = max(p.peak, h) if sgn > 0 else min(p.peak, l)
                        td = ep.trail_atr_mult * max(0.01, atr[s][j])
                        cand = round(p.peak - sgn * td, 4)
                        if (sgn > 0 and cand > p.stop) or (sgn < 0 and cand < p.stop):
                            p.stop = cand
            if p.rem <= 0:
                rr = p.risk * p.qty
                trades.append(Trade(
                    sym=s, date=date, side=p.side, entry_m=p.entry_m, exit_m=m, entry=p.entry, stop0=p.stop0,
                    risk=p.risk, qty=p.qty, q1=p.q1, q2=p.q2, tp1=p.tp1, tp2=p.tp2, fb1=p.sig.fb1, fb2=p.sig.fb2,
                    pnl=round(p.pnl, 2), r=p.pnl / rr if rr > 0 else 0.0, exit_reason=reason or "?",
                    t1_hit=p.t1_hit, t2_hit=p.t2_hit, mfe_r=p.mfe / p.risk, mae_r=p.mae / p.risk,
                    hold_min=m - p.entry_m, vix=vix, vix_regime=p.vreg, trend=p.trend,
                    stop_pct=p.risk / p.entry, raw_stop_pct=p.sig.feats["raw_risk"] / p.entry,
                    floored=p.sig.feats["floored"], notional=p.entry * p.qty, cap_hit=p.cap_hit,
                    feats=p.sig.feats))
                del pos[s]
        # 3) new signals at this minute
        for s in syms:
            d = days[s]
            if s not in idx_by_m:
                idx_by_m[s] = {int(d.m[j]): j for j in range(len(d.m))}
            j = idx_by_m[s].get(m)
            if j is None or j not in sigs[s]:
                continue
            g = sigs[s][j]
            if s in pos or s in pending:
                continue
            # phase gate
            if m >= 945 or m < 570:
                continue
            if ep.phase_block_midday and 690 <= m < 840:
                continue
            if not ep.allow_morning and m < 690:
                continue
            if not ep.allow_afternoon and m >= 840:
                continue
            # index filter
            if ep.use_index_filter:
                if trend == "UNKNOWN" or trend == "NEUTRAL":
                    continue
                if (g.side == "LONG" and trend != "BULLISH") or (g.side == "SHORT" and trend != "BEARISH"):
                    continue
            # concurrency + sector
            if len(pos) + len(pending) >= ep.max_positions:
                continue
            sec = SECTOR[s]
            if sec not in ("Index", "Index/ETF") and sum(1 for q in pos if SECTOR[q] == sec) >= ep.max_per_sector:
                continue
            built = build_position(g, g.entry, sp, ep, vstop, vsize, atr[s][j], trend, vix, vreg)
            if built is None:
                continue
            if ep.entry_mode == "market":
                built.entry = g.entry * (1 + (1.0 if g.side == "LONG" else -1.0) * ep.slip_bps / 1e4)
                pos[s] = built
            else:
                limit_px = g.entry if ep.entry_mode == "limit_close" else (g.feats["vwap"] if g.side == "LONG" and g.feats["vwap"] <= g.entry or g.side == "SHORT" and g.feats["vwap"] >= g.entry else g.entry)
                pending[s] = (g, limit_px, j + ep.limit_wait_bars)
    return trades


def run(sp: SigParams, ep: ExecParams, start="2024-01-01", end="2026-12-31", syms=None) -> List[Trade]:
    syms = list(syms or ep.symbols)
    need = set(syms) | {"SPY", "QQQ"}
    data = {s: load_symbol(s) for s in need}
    dates = sorted(set(data["SPY"].keys()) & set(data["QQQ"].keys()))
    dates = [d for d in dates if start <= d <= end]
    vix_map = load_vix()
    out = []
    ep2 = ExecParams(**{**asdict(ep), "symbols": tuple(syms)})
    for d in dates:
        days = {s: data[s][d] for s in need if d in data[s]}
        out.extend(run_day(d, days, sp, ep2, vix_map))
    return out


def summarize(trades: List[Trade]) -> dict:
    if not trades:
        return dict(n=0)
    r = np.array([t.r for t in trades]); pnl = np.array([t.pnl for t in trades])
    wins = (pnl > 0).sum()
    return dict(n=len(trades), pnl=round(float(pnl.sum()), 0), mean_r=round(float(r.mean()), 3),
                se_r=round(float(r.std(ddof=1) / math.sqrt(len(r))), 3) if len(r) > 1 else 0,
                win=round(float(wins / len(trades)), 3), avg_win=round(float(pnl[pnl > 0].mean()), 1) if wins else 0,
                avg_loss=round(float(pnl[pnl <= 0].mean()), 1) if (pnl <= 0).any() else 0,
                pf=round(float(pnl[pnl > 0].sum() / max(1e-9, -pnl[pnl <= 0].sum())), 2),
                t1=round(float(np.mean([t.t1_hit for t in trades])), 3),
                t2=round(float(np.mean([t.t2_hit for t in trades])), 3))


def write_csv(trades: List[Trade], path: str):
    if not trades:
        open(path, "w").write("")
        return
    keys = [k for k in asdict(trades[0]).keys() if k != "feats"]
    fkeys = sorted(trades[0].feats.keys())
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(keys + fkeys)
        for t in trades:
            d = asdict(t); w.writerow([d[k] for k in keys] + [t.feats.get(k) for k in fkeys])


if __name__ == "__main__":
    import argparse, time
    ap = argparse.ArgumentParser()
    ap.add_argument("--sig", default="{}"); ap.add_argument("--exec", default="{}")
    ap.add_argument("--start", default="2024-01-01"); ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    sp = SigParams(**json.loads(a.sig)); ep = ExecParams(**json.loads(a.exec))
    t0 = time.time()
    tr = run(sp, ep, a.start, a.end)
    print(json.dumps(summarize(tr)), f"{time.time()-t0:.0f}s")
    if a.out:
        write_csv(tr, a.out)
