"""Record one or more PAST sessions for the ORBStraddle parity harness (read-only relay calls only).

    python scripts/orbs_parity/record.py 2026-09-22 2026-09-23 [--workers 10]

Per date, into research/orbs_parity_cache/<date>/ (gitignored):
  1. ORIGINAL ORBStraddle prep in record mode: calendar, assets and daily bars go live once and are saved.
  2. The universe's SIP trades + quotes 09:30-10:15 are fetched once into the tape store.
  3. The ORIGINAL full timeline (preview, primary, secondary 09:45..10:15 + decisions) in record mode:
     scans read the tape, and every other relay read (SPY/QQQ bars, sector bars, news) goes live once
     and is saved. Its output is kept as record_original.json.
  4. ORBStraddle's public, read-only /api/research/day for the date (live boards and decisions).
The relay token comes from $RELAY_TOKEN or `railway variables --kv` in /Users/mo/AutonomousDayTrader; it
is never printed. No broker keys, no order code.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import parity_common as common  # noqa: E402

LIVE_RESEARCH = "https://orbstraddle-production.up.railway.app/api/research/day?day={day}"   # `day`, not `date`


def run_orig(day, phase, out, extra=()):
    cmd = [sys.executable, os.path.join(HERE, "orig_runner.py"), "--date", day, "--mode", "record",
           "--phase", phase, "--out", out, *extra]
    env = dict(os.environ)
    proc = subprocess.run(cmd, env=env)
    if proc.returncode:
        raise SystemExit(f"orig_runner {phase} failed for {day}")


def record_day(day, token, workers):
    ddir = common.day_dir(day)
    os.makedirs(ddir, exist_ok=True)
    t0 = time.time()
    prep_out = os.path.join(ddir, "record_prep.json")
    run_orig(day, "prep", prep_out)
    with open(prep_out) as f:
        universe = json.load(f)["universe"]
    print(f"[{day}] universe {len(universe)} symbols ({time.time() - t0:.0f}s)", flush=True)
    if not universe:
        raise SystemExit(f"[{day}] empty universe")

    tape = common.TapeStore(day)
    jobs = [(s, k) for s in universe for k in ("trades", "quotes") if not tape.has(s, k)]
    stats = {"rows": 0, "failed": []}

    def one(job):
        sym, kind = job
        rows = common.fetch_tape(token, day, sym, kind)
        return sym, kind, tape.write(sym, kind, rows)

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(one, j): j for j in jobs}
        for fut in as_completed(futs):
            try:
                sym, kind, n = fut.result()
                stats["rows"] += n
            except Exception as exc:
                stats["failed"].append(f"{futs[fut]}: {exc}")
            done += 1
            if done % 50 == 0:
                print(f"[{day}] tape {done}/{len(jobs)} files, {stats['rows']:,} rows, "
                      f"{time.time() - t0:.0f}s", flush=True)
    if stats["failed"]:
        print(f"[{day}] TAPE FAILURES: {stats['failed'][:10]}", flush=True)
    size = sum(os.path.getsize(os.path.join(tape.dir, f)) for f in os.listdir(tape.dir))
    print(f"[{day}] tape complete: {stats['rows']:,} new rows, {size / 1e9:.2f} GB on disk "
          f"({time.time() - t0:.0f}s)", flush=True)

    run_orig(day, "full", os.path.join(ddir, "record_original.json"))

    try:
        req = urllib.request.Request(LIVE_RESEARCH.format(day=day), headers={"User-Agent": "ADT-ORBS-parity/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
        if json.loads(body).get("day") != day:
            raise ValueError("endpoint answered for a different day")
        with open(os.path.join(ddir, "live_research.json"), "wb") as f:
            f.write(body)
    except Exception as exc:
        print(f"[{day}] live research fetch failed (cross-check skipped): {exc}", flush=True)
    meta = {"day": day, "universe": len(universe), "tape_bytes": size, "tape_failures": stats["failed"],
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "elapsed_s": round(time.time() - t0, 1)}
    with open(os.path.join(ddir, "record_meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    print(f"[{day}] recorded in {meta['elapsed_s']}s", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="+")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    token = common.read_token()
    os.environ["RELAY_TOKEN"] = token          # inherited by the orig_runner subprocesses, never printed
    for day in args.dates:
        record_day(day, token, args.workers)


if __name__ == "__main__":
    main()
