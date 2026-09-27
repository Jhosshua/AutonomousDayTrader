"""Fetch full-day 1-minute SIP bars for the bot's 12 watchlist symbols from AlpacaRelay.
Writes one JSON-lines file per symbol under data/bars/<SYM>.jsonl (t,o,h,l,c,v,vw).
Resumable: skips a symbol whose file already ends at or after END. Token never printed."""
import json, os, sys, time, urllib.request, urllib.parse
SYMS = ["SPY","QQQ","AAPL","NVDA","TSLA","AMD","MSFT","AMZN","META","GOOGL","PLTR","COIN"]
START = sys.argv[1] if len(sys.argv) > 1 else "2024-01-02T00:00:00Z"
END = sys.argv[2] if len(sys.argv) > 2 else "2026-09-27T00:00:00Z"
ONLY = sys.argv[3].split(",") if len(sys.argv) > 3 else SYMS
BASE = "https://alpacarelay-production.up.railway.app/data/v2/stocks/bars"
tok = ""
for line in open(os.path.expanduser("~/LargeAccountAccess/.env")):
    if line.startswith("RELAY_TOKEN="):
        tok = line.split("=",1)[1].strip().strip('"')
assert tok, "no relay token"
out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bars")
os.makedirs(out_dir, exist_ok=True)

def get(params):
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Relay-Token": tok})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            wait = 2 ** attempt
            print(f"  retry {attempt} after {wait}s: {str(e)[:120]}", flush=True)
            time.sleep(wait)
    raise SystemExit("relay failed repeatedly")

for sym in ONLY:
    path = os.path.join(out_dir, f"{sym}.jsonl")
    last_t = None
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                pass
            if line.strip():
                last_t = json.loads(line)["t"]
    start = START
    if last_t:
        if last_t >= END[:19]:
            print(f"{sym}: complete to {last_t}", flush=True); continue
        start = last_t  # relay returns bars >= start; drop the duplicate below
    n = 0; page = None; t0 = time.time()
    with open(path, "a") as f:
        while True:
            p = {"symbols": sym, "timeframe": "1Min", "start": start, "end": END,
                 "limit": 10000, "feed": "sip", "adjustment": "raw"}
            if page: p["page_token"] = page
            d = get(p)
            bars = d.get("bars", {}).get(sym, []) or []
            for b in bars:
                if last_t and b["t"] <= last_t:
                    continue
                f.write(json.dumps({"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"], "vw": b.get("vw")}) + "\n")
                n += 1
            page = d.get("next_page_token")
            if bars: last_t = bars[-1]["t"]
            if not page: break
            time.sleep(0.15)
    print(f"{sym}: +{n} bars in {time.time()-t0:.0f}s, last {last_t}", flush=True)
