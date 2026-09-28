"""Parity gate: ORBStraddle's ORIGINAL modules vs ADT's COPY on the same frozen inputs (replay only).

    python scripts/orbs_parity/compare.py 2026-09-22 [2026-09-23 ...] [--reuse]

Per recorded date: runs orig_runner.py and copy_runner.py in replay mode (no network; a request the cache
cannot answer is a miss and fails the run), then diffs, EXACTLY (floats compared by their repr through
sorted-key JSON): the effective config, universe, every board (cards + scan health) of the preview, primary
and each secondary scan 09:45..10:15, every decision (adaptive reply: picks, audit rows, regime; note;
validator result; the board it judged) and every pick's candle/flow/macro re-check.
Then an INFORMATIONAL cross-check of the replayed original against what ORBStraddle actually decided live
that day (its public /api/research/day, recorded by record.py): live timing differs, so differences there
are reported, not failed.
Writes research/orbs_parity_cache/<date>/parity_report.json and exits 1 on any parity difference.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import parity_common as common  # noqa: E402

MAX_DIFFS = 40


def jnorm(x):
    return json.loads(common.dumps(x))


def walk(a, b, path, out):
    if len(out) >= MAX_DIFFS:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append({"path": f"{path}.{k}", "original": a.get(k, "<missing>"), "copy": b.get(k, "<missing>")})
            else:
                walk(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append({"path": f"{path}[len]", "original": len(a), "copy": len(b)})
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]", out)
    elif common.dumps(a) != common.dumps(b):
        out.append({"path": path, "original": a, "copy": b})


def run(script, day, out, extra):
    cmd = [sys.executable, os.path.join(HERE, script), "--date", day, "--out", out, *extra]
    if script == "orig_runner.py":
        cmd += ["--mode", "replay"]
    proc = subprocess.run(cmd, env=dict(os.environ))
    if proc.returncode:
        raise SystemExit(f"{script} failed for {day}")


def parity(orig, copy):
    report = {"sections": {}, "equal": True}
    for key in ("config", "scanner_tunables", "universe", "reference_session"):
        diffs = []
        walk(jnorm(orig[key]), jnorm(copy[key]), key, diffs)
        report["sections"][key] = diffs
    steps = []
    if len(orig["steps"]) != len(copy["steps"]):
        report["sections"]["steps_len"] = [{"original": len(orig["steps"]), "copy": len(copy["steps"])}]
    for so, sc in zip(orig["steps"], copy["steps"]):
        diffs = []
        for part in ("wave", "end", "cards", "health"):
            walk(jnorm(so[part]), jnorm(sc[part]), part, diffs)
        do, dc = so.get("decision"), sc.get("decision")
        if (do is None) != (dc is None):
            diffs.append({"path": "decision", "original": do is not None, "copy": dc is not None})
        elif do is not None:
            for part in ("reply", "note", "valid", "validated", "cards", "rechecks", "now"):
                walk(jnorm(do[part]), jnorm(dc[part]), "decision." + part, diffs)
        picks = None
        if do is not None and do["valid"] and do["validated"]:
            picks = [p["symbol"] for p in do["validated"]]
        steps.append({"step": f"{so['wave']}@{so['end']}", "cards": len(so["cards"]),
                      "coverage": so["health"].get("coverage"),
                      "decision": None if do is None else (picks or do["reply"]["regime"]["classification"]),
                      "equal": not diffs, "diffs": diffs})
    report["steps"] = steps
    report["misses"] = {"original": orig["misses"][:10], "copy": copy["misses"][:10],
                        "n_original": len(orig["misses"]), "n_copy": len(copy["misses"])}
    report["equal"] = (not any(report["sections"].values()) and all(s["equal"] for s in steps)
                       and not orig["misses"] and not copy["misses"])
    return report


def cross_check(day, orig):
    """Replayed ORIGINAL vs what ORBStraddle did live (informational)."""
    path = os.path.join(common.day_dir(day), "live_research.json")
    if not os.path.exists(path):
        return {"available": False}
    with open(path) as f:
        live = json.load(f)
    if live.get("day") != day:
        return {"available": False, "why": f"live research file is for {live.get('day')}, not {day}"}
    by_end = {s["end"].replace(":", ""): s for s in orig["steps"]}
    boards = []
    for b in live.get("boards") or []:
        wave, _, end = b["name"].partition("_")
        s = by_end.get(end)
        if s is None or s["wave"] != wave:
            boards.append({"board": b["name"], "replayed": False})
            continue
        lv = {c["symbol"]: c for c in b["cards"]}
        rp = {c["symbol"]: c for c in s["cards"]}
        moved = []
        for sym in sorted(set(lv) & set(rp)):
            for k in ("direction", "entry", "stop", "trig", "flow_blocked", "candle_blocked"):
                if lv[sym].get(k) != rp[sym].get(k):
                    moved.append(f"{sym}.{k}: live {lv[sym].get(k)} replay {rp[sym].get(k)}")
        boards.append({"board": b["name"], "live_cards": len(lv), "replay_cards": len(rp),
                       "only_live": sorted(set(lv) - set(rp)), "only_replay": sorted(set(rp) - set(lv)),
                       "field_diffs": moved[:25], "n_field_diffs": len(moved)})
    # live decisions carry no board name: the first is the primary; each later one is matched, in order, to the
    # first replayed secondary step whose board has the same number of cards as the live decision judged
    # (its audit has one row per card unless the wave sat out). Unmatched = the live board differed.
    steps = orig["steps"]
    decisions, si = [], 0
    for r in live.get("ledger") or []:
        if r.get("kind") != "adaptive_decision":
            continue
        dec = r["decision"]
        sat_out = dec["regime"].get("action") == "SIT_OUT_CASH"
        n_cards = None if sat_out else len(dec.get("audit") or [])
        match = None
        if not decisions:
            match = next((s for s in steps if s["wave"] == "primary"), None)
        elif n_cards is not None:
            for j in range(si, len(steps)):
                if steps[j]["wave"] == "secondary" and len(steps[j]["cards"]) == n_cards:
                    match, si = steps[j], j + 1
                    break
        rd = (match or {}).get("decision")
        rpicks = [p["symbol"] for p in rd["validated"]] if rd and rd["valid"] and rd["validated"] else []
        decisions.append({"live_at": r.get("ts"), "live_cards": n_cards,
                          "replay_step": None if match is None else f"{match['wave']}@{match['end']}",
                          "live": {"picks": [p["symbol"] for p in dec.get("picks") or []],
                                   "class": dec["regime"].get("classification"),
                                   "short_frac": dec["regime"].get("short_frac")},
                          "replay": None if rd is None else {
                              "picks": rpicks, "class": rd["reply"]["regime"].get("classification"),
                              "short_frac": rd["reply"]["regime"].get("short_frac"), "at": rd["now"]}})
    executions = [{"symbols": [p.get("symbol") for p in r.get("picks") or []], "ts": r.get("ts")}
                  for r in live.get("ledger") or [] if r.get("kind") == "execution"]
    return {"available": True, "boards": boards, "decisions": decisions, "live_executions": executions}


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="+")
    ap.add_argument("--reuse", action="store_true", help="reuse existing replay outputs")
    ap.add_argument("--scan-deadline", default=None)
    args = ap.parse_args()
    extra = ["--scan-deadline", args.scan_deadline] if args.scan_deadline else []
    failed = []
    for day in args.dates:
        ddir = common.day_dir(day)
        o_path, c_path = os.path.join(ddir, "replay_original.json"), os.path.join(ddir, "replay_copy.json")
        if not (args.reuse and os.path.exists(o_path)):
            run("orig_runner.py", day, o_path, extra)
        if not (args.reuse and os.path.exists(c_path)):
            run("copy_runner.py", day, c_path, extra)
        with open(o_path) as f:
            orig = json.load(f)
        with open(c_path) as f:
            copy = json.load(f)
        report = parity(orig, copy)
        report["day"] = day
        report["elapsed_s"] = {"original": orig.get("elapsed_s"), "copy": copy.get("elapsed_s")}
        report["cross_check_live"] = cross_check(day, orig)
        with open(os.path.join(ddir, "parity_report.json"), "w") as f:
            json.dump(report, f, indent=1, default=str)
        n_steps = len(report["steps"])
        n_eq = sum(1 for s in report["steps"] if s["equal"])
        print(f"== {day}: {'EXACT MATCH' if report['equal'] else 'DIFFERENT'} "
              f"({n_eq}/{n_steps} steps equal, misses {report['misses']['n_original']}/{report['misses']['n_copy']})")
        for s in report["steps"]:
            flag = "ok  " if s["equal"] else "DIFF"
            print(f"   {flag} {s['step']:<18} cards {s['cards']:>3}  decision {s['decision']}")
            for d in s["diffs"][:5]:
                print("        ", json.dumps(d, default=str)[:300])
        for k, v in report["sections"].items():
            if v:
                print("   DIFF", k, json.dumps(v[:5], default=str)[:600])
        if not report["equal"]:
            failed.append(day)
    if failed:
        print("PARITY FAILED:", failed)
        sys.exit(1)
    print("PARITY OK:", args.dates)


if __name__ == "__main__":
    main()
