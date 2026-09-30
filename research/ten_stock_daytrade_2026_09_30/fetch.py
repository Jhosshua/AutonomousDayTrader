# @steered SNARE-2 2026-09-30
"""Fetch split adjusted 1 minute SIP bars (04:00 to 15:59 ET) and BTC/USD 1 minute bars from AlpacaRelay.

NOT FOR DEPLOYMENT. Local research tool. The relay token is read from a private file
(~/.cache/edge_hunt/relay_token, mode 600) and is never printed or written anywhere else.

Output per stock: data/bars/<SYM>.npz with arrays day (YYYYMMDD int), minute (ET minutes after
midnight, 240..959), o, h, l, c, v, vw. BTC goes to data/bars/BTC.npz with the same fields, where
day and minute are the ET calendar date and clock minute (24 hours a day).
Resumable, a finished symbol is skipped.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

ET = ZoneInfo("America/New_York")
RELAY = "https://alpacarelay-production.up.railway.app/data"
START = "2023-06-26T00:00:00Z"   # 2024-06 onward is used, earlier months only for a labelled pre window check
END = "2026-09-30T00:00:00Z"     # last complete session is 2026-09-29
STOCKS = "TQQQ QQQ PLTR MSTR COIN META VRT AMD SPY SMH".split()
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bars")
TOKEN_PATH = os.path.expanduser("~/.cache/edge_hunt/relay_token")


def token() -> str:
    with open(TOKEN_PATH) as f:
        tok = f.read().strip()
    if not tok:
        raise SystemExit("empty relay token file")
    return tok


def get(path: str, params: dict, tok: str) -> dict:
    url = RELAY + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Relay-Token": tok})
    for attempt in range(7):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as e:  # network blips and 429s, back off, never log headers
            wait = 2 ** attempt
            print(f"  {params['symbols']} retry {attempt} in {wait}s: {type(e).__name__} {str(e)[:80]}", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"relay failed repeatedly for {params['symbols']}")


def fetch(sym: str, tok: str) -> str:
    name = "BTC" if sym == "BTC/USD" else sym
    path = os.path.join(OUT, f"{name}.npz")
    if os.path.exists(path):
        return f"{name}: already done"
    crypto = sym == "BTC/USD"
    route = "/v1beta3/crypto/us/bars" if crypto else "/v2/stocks/bars"
    rows, page, pages, t0 = [], None, 0, time.time()
    while True:
        p = {"symbols": sym, "timeframe": "1Min", "start": START, "end": END, "limit": 10000}
        if not crypto:
            p.update(feed="sip", adjustment="split")
        if page:
            p["page_token"] = page
        d = get(route, p, tok)
        pages += 1
        for b in (d.get("bars") or {}).get(sym, []) or []:
            ts = datetime.strptime(b["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(ET)
            minute = ts.hour * 60 + ts.minute
            if crypto or 240 <= minute < 960:  # stocks keep 04:00 to 15:59 ET, early closes cut later
                rows.append((ts.year * 10000 + ts.month * 100 + ts.day, minute,
                             b["o"], b["h"], b["l"], b["c"], b["v"], b.get("vw") or b["c"]))
        page = d.get("next_page_token")
        if not page:
            break
        time.sleep(0.15)
    if not rows:
        return f"{name}: NO DATA"
    a = np.array(rows, dtype=np.float64)
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, day=a[:, 0].astype(np.int32), minute=a[:, 1].astype(np.int16),
                        o=a[:, 2], h=a[:, 3], l=a[:, 4], c=a[:, 5], v=a[:, 6], vw=a[:, 7])
    os.replace(tmp, path)
    return f"{name}: {len(rows)} bars, {pages} pages, {time.time() - t0:.0f}s"


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    tok = token()
    syms = sys.argv[1].split(",") if len(sys.argv) > 1 else ["SPY"] + [s for s in STOCKS if s != "SPY"] + ["BTC/USD"]
    with ThreadPoolExecutor(max_workers=3) as ex:  # gentle on the shared relay
        for msg in ex.map(lambda s: fetch(s, tok), syms):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
