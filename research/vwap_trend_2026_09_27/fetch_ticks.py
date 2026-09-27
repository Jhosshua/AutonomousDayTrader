"""Fetch real SIP trade prints and NBBO quotes for one session window from AlpacaRelay.
Writes data/ticks/<DATE>/<SYM>.trades.jsonl and .quotes.jsonl (raw relay rows, nanosecond t).
Usage: python3 fetch_ticks.py 2026-09-25 13:30:00 15:40:00 [SYM,SYM]"""
import json, os, sys, time, urllib.request, urllib.parse
DATE = sys.argv[1]; START = f"{DATE}T{sys.argv[2]}Z"; END = f"{DATE}T{sys.argv[3]}Z"
SYMS = sys.argv[4].split(",") if len(sys.argv) > 4 else ["AAPL","NVDA","AMD","MSFT","AMZN","META","GOOGL","PLTR","COIN","SPY","QQQ"]
BASE = "https://alpacarelay-production.up.railway.app/data/v2/stocks/"
tok = ""
for line in open(os.path.expanduser("~/LargeAccountAccess/.env")):
    if line.startswith("RELAY_TOKEN="):
        tok = line.split("=",1)[1].strip().strip('"')
assert tok
out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ticks", DATE)
os.makedirs(out_dir, exist_ok=True)
def get(kind, params):
    url = BASE + kind + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Relay-Token": tok})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as e:
            time.sleep(2 ** attempt); print("retry", kind, attempt, str(e)[:100], flush=True)
    raise SystemExit("relay failed")
for sym in SYMS:
    for kind in ("trades", "quotes"):
        path = os.path.join(out_dir, f"{sym}.{kind}.jsonl")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            print("skip", path, flush=True); continue
        n = 0; token = None
        with open(path + ".tmp", "w") as f:
            while True:
                params = {"symbols": sym, "start": START, "end": END, "limit": 10000}
                if token: params["page_token"] = token
                d = get(kind, params)
                rows = d.get(kind, {}).get(sym, [])
                for row in rows:
                    f.write(json.dumps(row, separators=(",", ":")) + "\n"); n += 1
                token = d.get("next_page_token")
                if not token or not rows: break
        os.replace(path + ".tmp", path)
        print(f"{sym} {kind}: {n} rows", flush=True)
print("done")
