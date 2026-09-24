"""Rebuild backend/app/data/daily_bars_seed.json from real Alpaca daily bars.

Fetches split/dividend-adjusted SIP daily bars through the AlpacaRelay /data proxy.
Usage: RELAY_TOKEN=... python3 scripts/build_daily_bars_seed.py [END_DATE]
END_DATE (YYYY-MM-DD, inclusive) defaults to yesterday; never include today's
unfinished session.
"""
import json
import os
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

RELAY = os.environ.get("RELAY_HTTP_URL", "https://alpacarelay-production.up.railway.app").rstrip("/")
SYMBOLS = ["QQQ", "LRCX", "KLAC", "MU", "AMD", "GS"]
BARS_PER_SYMBOL = 265
OUT = Path(__file__).resolve().parents[1] / "backend" / "app" / "data" / "daily_bars_seed.json"


def fetch(end: date) -> dict:
    start = end - timedelta(days=420)
    out = {s: [] for s in SYMBOLS}
    token = None
    while True:
        url = (
            f"{RELAY}/data/v2/stocks/bars?symbols={','.join(SYMBOLS)}&timeframe=1Day"
            f"&start={start.isoformat()}&end={end.isoformat()}&adjustment=all&feed=sip&limit=10000"
        )
        if token:
            url += f"&page_token={token}"
        req = urllib.request.Request(url, headers={"X-Relay-Token": os.environ["RELAY_TOKEN"]})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        for sym, bars in (data.get("bars") or {}).items():
            for b in bars:
                out[sym].append({
                    "symbol": sym, "date": b["t"][:10], "open": b["o"], "high": b["h"],
                    "low": b["l"], "close": b["c"], "volume": int(b["v"]),
                })
        token = data.get("next_page_token")
        if not token:
            break
    for sym in SYMBOLS:
        bars = sorted(out[sym], key=lambda x: x["date"])
        if len(bars) < 201:
            raise SystemExit(f"{sym}: only {len(bars)} bars, need >= 201 for the 200-SMA")
        out[sym] = bars[-BARS_PER_SYMBOL:]
    return out


if __name__ == "__main__":
    end = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today() - timedelta(days=1)
    seed = fetch(end)
    OUT.write_text(json.dumps(seed, indent=1))
    for sym, bars in seed.items():
        print(sym, len(bars), bars[0]["date"], "->", bars[-1]["date"], "close", bars[-1]["close"])
