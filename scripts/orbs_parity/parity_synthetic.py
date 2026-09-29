"""A tiny, fully synthetic session (2031-03-04) for the always-on parity test.

Seven names are built so the board exercises every decision branch: fast flow-confirmed breaks both ways
(AMD, JPM long; XOM short with an earnings-miss headline), a wrong-way break under a green opening candle
(CAT: candle-blocked), a slow crossing with no trade-speed burst (NKE: flow-blocked), a break that only
the secondary wave sees (PLTR long 09:47, BA short 09:50) and a fund (SPY) that the universe filter must drop. Odd-lot prints
far outside the range test that volume-only prints never set a price. Index and sector ETFs are flat, so
the macro veto passes and the SPY slope is 0.

`write_tape()` writes the tape store; `respond(path, params)` answers every other relay read (calendar,
assets, daily bars, SPY/QQQ/sector minute bars, news, latest trade) and is used by the record transport
when ORBS_PARITY_SYNTHETIC=1.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DAY = "2031-03-04"
D = date.fromisoformat(DAY)

SPECS = {
    # symbol: side, base price, break time (None = never), fast burst?, candle override, size, prev close gap %
    "AMD": dict(side="long", base=150.0, brk=(9, 35, 30), fast=True, size=400, gap=-1.6, rvol=6.0),
    "JPM": dict(side="long", base=250.0, brk=(9, 36, 30), fast=True, size=200, gap=-1.2, rvol=3.5),
    "XOM": dict(side="short", base=110.0, brk=(9, 36, 10), fast=True, size=300, gap=1.5, rvol=4.5),
    "CAT": dict(side="short", base=380.0, brk=(9, 36, 20), fast=True, size=150, gap=1.1, rvol=3.0, candle="green"),
    "NKE": dict(side="long", base=80.0, brk=(9, 36, 40), fast=False, size=500, gap=-1.0, rvol=3.2),
    "PLTR": dict(side="long", base=30.0, brk=(9, 47, 0), fast=True, size=2000, gap=-2.0, rvol=4.0),
    "BA": dict(side="short", base=200.0, brk=(9, 50, 0), fast=True, size=300, gap=1.3, rvol=3.8),
}
ETFS = ("SPY", "QQQ", "XLK", "XLF", "XLE", "XLI", "XLY", "XLC", "XLV", "XLB")
NEWS = [{"id": 1, "headline": "Exxon Mobil Q4 2030 EPS $1.20 Misses $1.35 Estimate", "symbols": ["XOM"],
         "created_at": "2031-03-03T19:05:00Z", "source": "synthetic"}]


def _stamp(et_dt: datetime, extra_ns: int = 0) -> str:
    utc = et_dt.astimezone(timezone.utc)
    ns = utc.microsecond * 1000 + extra_ns
    return utc.strftime("%Y-%m-%dT%H:%M:%S") + ".%09dZ" % ns


def _at(h, m, s=0, ms=0):
    return datetime.combine(D, dtime(h, m, s), tzinfo=ET) + timedelta(milliseconds=ms)


def _opening_path(k: int) -> float:
    """Offsets (in % of base) of the 60 opening prints: dips to -0.20, peaks +0.20, closes +0.10."""
    if k == 0:
        return -0.10
    if k <= 10:
        return -0.10 - 0.01 * k
    if k <= 30:
        return -0.20 + 0.02 * (k - 10)
    if k <= 45:
        return 0.20 - 0.01 * (k - 30)
    return 0.05 + (0.05 if k == 59 else 0.0)


def build(sym):
    spec = SPECS[sym]
    u = spec["base"] / 100.0
    flip = spec["side"] == "short"

    def px(off):              # off in "units" around 100; short = mirror image around the base
        return round(u * (100.0 - off if flip else 100.0 + off), 4)

    trades, quotes = [], []

    qs = max(500, int(round(60000 / spec["base"] / 10.0)) * 10)     # both sides >= $25k: a "deep" quote

    def quote(t, mid_off, bs, as_, extra_ns=0):
        bs, as_ = bs - 500 + qs, as_ - 500 + qs
        bid, ask = px(mid_off - 0.01), px(mid_off + 0.01)
        if flip:
            bid, ask, bs, as_ = ask, bid, as_, bs
        quotes.append({"t": _stamp(t, extra_ns), "bp": bid, "ap": ask, "bs": bs, "as": as_})

    def trade(t, off, size, cond=None, extra_ns=0):
        trades.append({"t": _stamp(t, extra_ns), "p": px(off), "s": size, "c": cond or ["@"]})

    brk = _at(*spec["brk"]) if spec["brk"] else None
    end = _at(10, 15)
    # quotes every 2 s; alternating sizes keep the SOFI window's variance small, the jump makes the z-score
    t, k = _at(9, 30), 0
    while t < end:
        if brk is not None and t >= brk - timedelta(milliseconds=300):
            break
        quote(t, 0.0, 500 if k % 2 else 520, 520 if k % 2 else 500, extra_ns=17)
        t += timedelta(seconds=2)
        k += 1
    # opening range prints (09:30:00.050 + 5 s), with odd lots far outside the range
    for k in range(60):
        off = _opening_path(k)
        if spec.get("candle") == "green" and flip and k in (0, 59):
            off = 0.15 if k == 0 else -0.15      # mirrored: first print below last = green candle
        size = int(spec["size"] * (1.5 if off > 0 else 1.0))
        trade(_at(9, 30, 0, 50) + timedelta(seconds=5 * k), off, size, extra_ns=5)
        if k % 7 == 3:
            trade(_at(9, 30, 1, 0) + timedelta(seconds=5 * k), 0.5, 7, cond=["@", "I"], extra_ns=9)
    # a print and a quote stamped EXACTLY 09:35:00.000000000 (first post-range instant) and 09:35:05.000000000
    # (the delay gate), so page splits at those boundaries (ADT_PARITY_SPLIT_AT) land on real rows
    for boundary in (_at(9, 35, 0), _at(9, 35, 5)):
        quote(boundary, 0.0, 510, 510)
        trade(boundary, 0.05, 100)
    # inside the range until the break
    t = _at(9, 35, 2)
    stop_inside = brk if brk is not None else end
    while t < stop_inside and t < end:
        trade(t, 0.05, 100, extra_ns=3)
        t += timedelta(seconds=5)
    if brk is not None and brk < end:
        quote(brk - timedelta(milliseconds=300), 0.25, 3000, 3000, extra_ns=11)
        if spec["fast"]:
            for j in range(10):
                trade(brk + timedelta(milliseconds=100 * j), 0.26, 100, extra_ns=21)
        t, k = brk + timedelta(seconds=1), 0
        while t < end:
            quote(t, 0.27, 500 if k % 2 else 520, 520 if k % 2 else 500, extra_ns=13)
            t += timedelta(seconds=2)
            k += 1
        t = brk + timedelta(seconds=2)
        while t < end:
            trade(t, 0.27 if spec["fast"] else 0.26, 100, extra_ns=7)
            t += timedelta(seconds=5)
    trades.sort(key=lambda r: r["t"])
    quotes.sort(key=lambda r: r["t"])
    return trades, quotes


def sessions():
    """Weekday sessions in the 35 calendar days before DAY (the synthetic calendar has no holidays)."""
    out, d = [], D - timedelta(days=35)
    while d < D:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _opening_dollar_volume(sym):
    trades, _ = build(sym)
    total = 0.0
    for r in trades:
        stamp = datetime.fromisoformat(r["t"][:26] + "+00:00").astimezone(ET)
        if stamp.time() < dtime(9, 35):
            total += r["p"] * r["s"]
    return total


def daily_bars(sym, start: date, end: date):
    spec = SPECS[sym]
    u = spec["base"] / 100.0
    open_px = round(u * (100.0 - _opening_path(0) if spec["side"] == "short" else 100.0 + _opening_path(0)), 4)
    prev_close = round(open_px / (1 + spec["gap"] / 100.0), 2)
    volume = int(_opening_dollar_volume(sym) / (prev_close * spec["rvol"] * 5 / 390))
    rows = []
    for s in sessions():
        if start <= s < end:
            rows.append({"t": f"{s.isoformat()}T05:00:00Z", "o": prev_close, "h": round(prev_close + 1.5 * u, 2),
                         "l": round(prev_close - 1.5 * u, 2), "c": prev_close, "v": volume, "n": 1000,
                         "vw": prev_close})
    return rows


def minute_bars(etf, start: datetime, end: datetime):
    rows, t = [], start.astimezone(ET).replace(second=0, microsecond=0)
    while t < end:
        rows.append({"t": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "o": 500.0, "c": 500.0,
                     "v": 10000, "vw": 500.0})
        t += timedelta(minutes=1)
    return rows


def _parse(raw):
    raw = raw.replace("Z", "+00:00")
    if "T" not in raw:
        return date.fromisoformat(raw)
    return datetime.fromisoformat(raw)


def respond(path, params):
    if path == "/metadata/calendar":
        start, end = date.fromisoformat(params["start"]), date.fromisoformat(params["end"])
        body = [{"date": s.isoformat(), "open": "09:30", "close": "16:00"} for s in sessions() if start <= s <= end]
    elif path == "/metadata/assets":
        body = [{"symbol": s, "tradable": True, "exchange": "NYSE" if s in ("JPM", "XOM", "CAT", "NKE") else "NASDAQ",
                 "name": f"{s} Synthetic Inc.", "status": "active"} for s in SPECS]
        body.append({"symbol": "SPY", "tradable": True, "exchange": "ARCA", "name": "SPDR S&P 500 ETF Trust",
                     "status": "active"})
    elif path == "/data/v2/stocks/bars":
        syms = params["symbols"].split(",")
        if params["timeframe"] == "1Day":
            start, end = _parse(params["start"]), _parse(params["end"])
            body = {"bars": {s: daily_bars(s, start, end) for s in syms if s in SPECS}, "next_page_token": None}
        else:
            start, end = _parse(params["start"]), _parse(params["end"])
            body = {"bars": {s: minute_bars(s, start, end) for s in syms if s in ETFS}, "next_page_token": None}
    elif path == "/data/v1beta1/news":
        want = set(params["symbols"].split(","))
        body = {"news": [n for n in NEWS if want & set(n["symbols"])], "next_page_token": None}
    elif path.startswith("/data/v2/stocks/") and path.endswith("/trades/latest"):
        sym = path.split("/")[4]
        trades, _ = build(sym)
        body = {"symbol": sym, "trade": trades[-1]}
    else:
        return 404, json.dumps({"message": f"synthetic relay has no {path}"}).encode()
    return 200, json.dumps(body, separators=(",", ":")).encode()


def write_tape(store):
    for sym in SPECS:
        trades, quotes = build(sym)
        store.write(sym, "trades", trades)
        store.write(sym, "quotes", quotes)
