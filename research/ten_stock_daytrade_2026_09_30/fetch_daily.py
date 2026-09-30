# @steered SNARE-2 2026-09-30
"""Fetch daily bars (split adjusted and raw) for the ten names plus XLF, and the exchange calendar.

NOT FOR DEPLOYMENT. Local research tool. Token handling is the same as fetch.py.
Outputs data/daily.json ({sym: {"split": [...], "raw": [...]}}) and data/calendar.json.
"""
import json
import os
import time
import urllib.parse
import urllib.request

from fetch import END, TOKEN_PATH, get, token

ROOT = "https://alpacarelay-production.up.railway.app"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SYMS = "TQQQ QQQ PLTR MSTR COIN META VRT AMD SPY SMH XLF".split()


def daily(sym: str, adj: str, tok: str) -> list:
    rows, page = [], None
    while True:
        p = {"symbols": sym, "timeframe": "1Day", "start": "2024-01-01T00:00:00Z", "end": END,
             "limit": 10000, "feed": "sip", "adjustment": adj}
        if page:
            p["page_token"] = page
        d = get("/v2/stocks/bars", p, tok)
        rows += (d.get("bars") or {}).get(sym, []) or []
        page = d.get("next_page_token")
        if not page:
            return rows
        time.sleep(0.2)


def calendar(tok: str) -> list:
    out = []
    for a, b in (("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31"), ("2026-01-01", "2026-09-30")):
        url = ROOT + "/metadata/calendar?" + urllib.parse.urlencode({"start": a, "end": b})
        req = urllib.request.Request(url, headers={"X-Relay-Token": tok})
        with urllib.request.urlopen(req, timeout=60) as r:
            out += json.load(r)
        time.sleep(0.3)
    return out


def main() -> None:
    tok = token()
    res = {}
    for s in SYMS:
        res[s] = {"split": daily(s, "split", tok), "raw": daily(s, "raw", tok)}
        print(s, len(res[s]["split"]), flush=True)
    with open(os.path.join(OUT, "daily.json"), "w") as f:
        json.dump(res, f)
    cal = calendar(tok)
    with open(os.path.join(OUT, "calendar.json"), "w") as f:
        json.dump(cal, f)
    print("calendar", len(cal), cal[0] if cal else None)


if __name__ == "__main__":
    main()
