"""Small READ-ONLY top-up from the AlpacaRelay for the ORB dry run (never the broker).

    python scripts/orb_dry_run/topup.py 2026-09-28 --symbols APP [--bars]

  --symbols  SIP trades + quotes 10:15:00-11:05:00 ET for the few symbols ADT trades (exits run to 11:00;
             the parity tape stops at 10:15). Same row format as the parity tape.
  --bars     1-minute bars 09:30-11:05 ET for SPY, QQQ and the 11 sector ETFs (macro veto at any time).

Writes under research/orbs_parity_cache/dry_run_topup/<date>/ (gitignored). The relay token comes from
$RELAY_TOKEN or `railway variables --kv` in /Users/mo/AutonomousDayTrader and is never printed."""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store  # noqa: E402

pc = store.pc


def fetch_ext_tape(token, day, sym, kind):
    d = date.fromisoformat(day)
    params = {"start": datetime.combine(d, store.EXT_START, tzinfo=store.ET).isoformat(),
              "end": datetime.combine(d, store.EXT_END, tzinfo=store.ET).isoformat(),
              "limit": 10000, "feed": "sip", "sort": "asc"}
    headers = {"X-Relay-Token": token, "Accept": "application/json", "User-Agent": "ADT-ORB-dryrun/1.0"}
    rows, page_token = [], None
    for _ in range(2000):
        q = dict(params)
        if page_token:
            q["page_token"] = page_token
        url = f"{pc.RELAY_ROOT}/data/v2/stocks/{urllib.parse.quote(sym)}/{kind}?" + urllib.parse.urlencode(q)
        for attempt in range(5):
            try:
                with pc._REAL_URLOPEN(urllib.request.Request(url, headers=headers), timeout=90) as r:
                    page = json.loads(r.read())
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 * (attempt + 1))
        rows.extend(page.get(kind) or [])
        page_token = page.get("next_page_token")
        if not page_token:
            return rows
    raise RuntimeError("too many pages")


def fetch_bars(token, day):
    d = date.fromisoformat(day)
    params = {"symbols": ",".join(store.ETFS), "timeframe": "1Min",
              "start": datetime.combine(d, pc.TAPE_START, tzinfo=store.ET).isoformat(),
              "end": datetime.combine(d, store.EXT_END, tzinfo=store.ET).isoformat(),
              "feed": "sip", "limit": 10000}
    headers = {"X-Relay-Token": token, "Accept": "application/json", "User-Agent": "ADT-ORB-dryrun/1.0"}
    out, page_token = {}, None
    for _ in range(50):
        q = dict(params)
        if page_token:
            q["page_token"] = page_token
        url = f"{pc.RELAY_ROOT}/data/v2/stocks/bars?" + urllib.parse.urlencode(q)
        with pc._REAL_URLOPEN(urllib.request.Request(url, headers=headers), timeout=90) as r:
            page = json.loads(r.read())
        for s, rows in (page.get("bars") or {}).items():
            out.setdefault(s, []).extend(rows)
        page_token = page.get("next_page_token")
        if not page_token:
            return out
    raise RuntimeError("too many bar pages")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("day")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--bars", action="store_true")
    a = ap.parse_args()
    token = pc.read_token()
    tdir = store.topup_dir(a.day)
    os.makedirs(os.path.join(tdir, "tape"), exist_ok=True)
    if a.bars:
        bars = fetch_bars(token, a.day)
        with open(os.path.join(tdir, "bars_1min.json"), "w") as f:
            json.dump({"day": a.day, "symbols": store.ETFS, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "bars": bars}, f)
        print(a.day, "bars", {s: len(v) for s, v in bars.items()})
    ext = store._Tape(os.path.join(tdir, "tape"), 0, 0)
    for sym in [s.strip().upper() for s in a.symbols.split(",") if s.strip()]:
        for kind in ("trades", "quotes"):
            if ext.has(sym, kind):
                continue
            rows = fetch_ext_tape(token, a.day, sym, kind)
            w = pc.TapeStore.__new__(pc.TapeStore)
            w.dir = os.path.join(tdir, "tape")
            n = pc.TapeStore.write(w, sym, kind, rows)
            print(a.day, sym, kind, n, "rows")


if __name__ == "__main__":
    main()
