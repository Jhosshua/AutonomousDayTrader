# @steered SNARE-2 2026-09-30
"""Pattern atlas (PLAN.md), description only, run after the holdout. Year 1 is the design year,
year 2 the holdout year. A pattern "holds" when its sign is the same in both years.
Writes data/atlas.json."""
import json
import math
from datetime import date

import numpy as np

import core
from core import DAYS, DESIGN_END, HOLD_END, HOLD_START, WIN_START, load, ttest

YEARS = {"y1": (WIN_START, DESIGN_END), "y2": (HOLD_START, HOLD_END)}
# FOMC statement days, from memory (2026 dates not verified against the Fed calendar)
FOMC = {20241107, 20241218, 20250129, 20250319, 20250507, 20250618, 20250730, 20250917, 20251029,
        20251210, 20260128, 20260318, 20260429, 20260617, 20260729, 20260916}


def opex_days(days):
    out = set()
    by_month = {}
    for d in days:
        by_month.setdefault(d // 100, []).append(d)
    for ym, ds in by_month.items():
        y, m = ym // 100, ym % 100
        fridays = [x for x in range(1, 32) if _valid(y, m, x) and date(y, m, x).weekday() == 4]
        third = y * 10000 + m * 100 + fridays[2]
        before = [x for x in ds if x <= third]
        if before:  # partial first month may start after its expiry
            out.add(max(before))
    return out


def _valid(y, m, x):
    try:
        date(y, m, x)
        return True
    except ValueError:
        return False


def tom_days(days):
    out = set()
    ds = list(days)
    for i, d in enumerate(ds):
        if i + 1 < len(ds) and ds[i + 1] // 100 != d // 100:
            out.add(d)
            out.update(ds[i + 1:i + 3])
    return out


def stat(x):
    x = np.asarray([v for v in x if math.isfinite(v)])
    if len(x) < 3:
        return {"n": len(x), "mean_bps": math.nan, "t": math.nan}
    t, _ = ttest(x)
    return {"n": len(x), "mean_bps": float(x.mean() * 1e4), "t": t}


def autocorr(r):
    """Within day lag 1 autocorrelation of non overlapping returns, pooled over days."""
    a, b = r[:, :-1].ravel(), r[:, 1:].ravel()
    m = np.isfinite(a) & np.isfinite(b)
    return float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 30 else math.nan


def one(sym):
    tp = load(sym)
    ox = {}
    opx, tom = opex_days(DAYS[(DAYS >= WIN_START)]), tom_days(DAYS[(DAYS >= WIN_START)])
    for yk, (lo, hi) in YEARS.items():
        d0, d1 = core.day_index(DAYS, lo, hi)
        idx = [d for d in range(d0, d1 + 1) if tp.ok[d] and math.isfinite(tp.prev_close[d])]
        full = [d for d in idx if tp.end[d] == 390]
        oc = np.array([tp.close[d] / tp.open_[d] - 1 for d in idx])
        on = np.array([tp.open_[d] / tp.prev_close[d] - 1 for d in idx])
        cc = np.array([tp.close[d] / tp.prev_close[d] - 1 for d in idx])
        y = {"days": len(idx), "intraday_log": float(np.log1p(oc).sum()), "overnight_log": float(np.log1p(on).sum()),
             "buy_hold": float(np.prod(1 + cc) - 1), "open_to_close_every_day": float(np.prod(1 + oc) - 1)}
        # half hour slots, open to open
        O = tp.O
        slots = {}
        for k in range(13):
            a, b = 30 * k, 30 * (k + 1)
            r = [(O[d, b] / O[d, a] - 1) if b < 390 else (tp.close[d] / O[d, a] - 1) for d in full]
            slots[f"{9 + (30 + 30 * k) // 60:02d}:{(30 * k + 30) % 60:02d}"] = {**stat(r), "abs_bps": float(np.nanmean(np.abs(r)) * 1e4)}
        y["slots"] = slots
        # gaps
        g = np.array([tp.open_[d] / tp.prev_close[d] - 1 for d in idx])
        fill1030, fillc = [], []
        for d in idx:
            pc, o = tp.prev_close[d], tp.open_[d]
            if abs(o / pc - 1) < 0.003:
                continue
            up = o > pc
            lows, highs = tp.L[d], tp.H[d]
            hit = (lows <= pc) if up else (highs >= pc)
            fill1030.append(bool(np.nanmax(hit[:60]) if np.isfinite(lows[:60]).any() else False))
            fillc.append(bool(np.nanmax(hit[:tp.end[d]])))
        y["gap"] = {"mean_abs_bps": float(np.abs(g).mean() * 1e4), "n_gaps_over_30bps": len(fill1030),
                    "fill_by_1030": float(np.mean(fill1030)) if fill1030 else math.nan,
                    "fill_by_close": float(np.mean(fillc)) if fillc else math.nan}
        # first 30 minutes versus the rest of the day
        f30 = np.array([O[d, 30] / tp.open_[d] - 1 for d in full])
        rest = np.array([tp.close[d] / O[d, 30] - 1 for d in full])
        m = np.isfinite(f30) & np.isfinite(rest)
        y["first30_vs_rest_corr"] = float(np.corrcoef(f30[m], rest[m])[0, 1])
        ac = {}
        for h in (5, 15, 30, 60):
            n = 390 // h
            r = np.full((len(full), n - 1), np.nan)
            for i, d in enumerate(full):
                p = O[d, np.arange(0, 390, h)[:n]]
                r[i] = p[1:] / p[:-1] - 1
            ac[str(h)] = autocorr(r)
        y["autocorr"] = ac
        dow = {}
        for wd, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri"]):
            dd = [d for d in idx if date(DAYS[d] // 10000, DAYS[d] // 100 % 100, DAYS[d] % 100).weekday() == wd]
            dow[name] = stat([tp.close[d] / tp.open_[d] - 1 for d in dd])
        y["day_of_week"] = dow
        y["calendar"] = {lab: stat([tp.close[d] / tp.open_[d] - 1 for d in idx if int(DAYS[d]) in s])
                         for lab, s in (("FOMC", FOMC), ("OPEX", opx), ("turn_of_month", tom))}
        ox[yk] = y
    # event days (earnings proxy) over the whole window
    d0, d1 = core.day_index(DAYS, WIN_START, HOLD_END)
    gap = np.where(tp.ok, tp.open_ / tp.prev_close - 1, np.nan)
    gsig = core.trailing_std(gap)
    pmed = np.array([np.median(tp.pre_v[max(0, d - 20):d]) if d >= 15 else np.nan for d in range(len(DAYS))])
    ev = [d for d in range(d0, d1 + 1) if math.isfinite(gap[d]) and math.isfinite(gsig[d])
          and abs(gap[d]) >= 3 * gsig[d] and tp.pre_v[d] >= 5 * pmed[d]]
    ox["event_days"] = {"n": len(ev), "dates": [int(DAYS[d]) for d in ev],
                        "open_to_close": stat([tp.close[d] / tp.open_[d] - 1 for d in ev]),
                        "gap_abs_bps": float(np.nanmean(np.abs(gap[ev])) * 1e4) if ev else math.nan}
    return sym, ox


def main():
    res = dict(one(s) for s in core.NAMES)
    json.dump(res, open("data/atlas.json", "w"), indent=1, default=float)
    for s, a in res.items():
        y1, y2 = a["y1"], a["y2"]
        print(f"{s:5s} buy&hold y1 {y1['buy_hold'] * 100:+6.1f}% y2 {y2['buy_hold'] * 100:+6.1f}% | "
              f"overnight log y1 {y1['overnight_log'] * 100:+6.1f} y2 {y2['overnight_log'] * 100:+6.1f} | "
              f"intraday log y1 {y1['intraday_log'] * 100:+6.1f} y2 {y2['intraday_log'] * 100:+6.1f} | "
              f"gapfill1030 {y1['gap']['fill_by_1030']:.2f}/{y2['gap']['fill_by_1030']:.2f} "
              f"f30corr {y1['first30_vs_rest_corr']:+.2f}/{y2['first30_vs_rest_corr']:+.2f} "
              f"ac5 {y1['autocorr']['5']:+.3f}/{y2['autocorr']['5']:+.3f} events {a['event_days']['n']}")
        held = []
        for k in y1["slots"]:
            a1, a2 = y1["slots"][k], y2["slots"][k]
            if np.sign(a1["mean_bps"]) == np.sign(a2["mean_bps"]):
                held.append((min(abs(a1["t"]), abs(a2["t"])), k, a1["mean_bps"], a2["mean_bps"], a1["t"], a2["t"]))
        held.sort(reverse=True)
        print("      slots same sign (top 3 by weaker t):",
              "; ".join(f"{k} {m1:+.1f}/{m2:+.1f}bps t {t1:+.1f}/{t2:+.1f}" for _, k, m1, m2, t1, t2 in held[:3]))
        dw = [(k, y1["day_of_week"][k]["mean_bps"], y2["day_of_week"][k]["mean_bps"]) for k in y1["day_of_week"]
              if np.sign(y1["day_of_week"][k]["mean_bps"]) == np.sign(y2["day_of_week"][k]["mean_bps"])]
        print("      weekday same sign:", "; ".join(f"{k} {a:+.0f}/{b:+.0f}" for k, a, b in dw),
              "| calendar:", "; ".join(f"{k} {y1['calendar'][k]['mean_bps']:+.0f}/{y2['calendar'][k]['mean_bps']:+.0f} (n {y1['calendar'][k]['n']}/{y2['calendar'][k]['n']})" for k in y1["calendar"]))


if __name__ == "__main__":
    main()
