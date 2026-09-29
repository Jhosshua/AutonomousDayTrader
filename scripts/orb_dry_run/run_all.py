"""Run the whole ORB dry-run matrix, then the ORIGINAL-code decision check and the analysis.

    python scripts/orb_dry_run/run_all.py OUT_ROOT [--jobs 6] [--only NAME,...]

Scenarios (per recorded day 2026-09-22/23/24/25/28):
  <day>_live       ORB_MODE=live exactly as built (BUG 1: no un-vetoed order can be sent)
  <day>_adapt      ORB_MODE=live with the harness macro-contract adapter (exercises orders, fills, exits)
  <day>_shadow     ORB_MODE=shadow
  <day>_adapt_eod  after <day>_adapt: a new process restores the end-of-day checkpoint (16:06)
Restarts on 2026-09-28 (adapter): hard kill at 09:38:45, hard kill at 10:30:03 and graceful stop at 10:30:03
(mid-trade, on the supervisor's 5 s phase), and a kill in the middle of the APP bracket POST; each resumes in
a NEW process on the same state and the same fake account. Plus 2026-09-28 adapt with 80 ms relay latency."""
import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
DAYS = ["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]


def run(cmd, log):
    with open(log, "a") as f:
        f.write("\n$ " + " ".join(cmd) + "\n")
        f.flush()
        return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode


def day_run(root, name, day, *extra):
    out = os.path.join(root, name)
    rc = run([PY, os.path.join(HERE, "run_day.py"), "--date", day, "--out", out, *extra], out + ".log")
    return rc


def orig(root, name, day):
    out = os.path.join(root, name)
    if os.path.exists(os.path.join(out, "result.json")):
        run([PY, os.path.join(HERE, "orig_decide.py"), "--date", day, "--result", os.path.join(out, "result.json"),
             "--out", os.path.join(out, "orig_decisions.json")], out + ".log")


def base(root, name, day, mode, *extra):
    rc = day_run(root, name, day, "--mode", mode, *extra)
    orig(root, name, day)
    if name.endswith("_adapt"):
        out = os.path.join(root, name)
        day_run(root, name + "_eod", day, "--mode", mode, "--macro-adapter", "--state", os.path.join(out, "state"),
                "--resume", out, "--start", "16:06:00", "--end", "16:06:30")
    return name, rc


def restart(root, name, day, stop_at, kind, kill_on_post=None):
    seg1, seg2 = os.path.join(root, name + "_s1"), os.path.join(root, name + "_s2")
    state = os.path.join(root, name + "_state")
    extra = ["--kill-on-post", kill_on_post] if kill_on_post else ["--stop-at", stop_at, "--stop-kind", kind]
    rc1 = day_run(root, name + "_s1", day, "--mode", "live", "--macro-adapter", "--state", state, *extra)
    with open(os.path.join(seg1, "result.json")) as f:
        r1 = json.load(f)
    at = (r1.get("killed_at") or "")[11:19] if kill_on_post else stop_at
    if not at:
        return name, f"segment 1 never stopped (rc {rc1})"
    rc2 = day_run(root, name + "_s2", day, "--mode", "live", "--macro-adapter", "--state", state,
                  "--resume", seg1, "--start", at)
    orig(root, name + "_s2", day)
    return name, (rc1, rc2, at)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    os.makedirs(a.root, exist_ok=True)
    jobs = []
    for d in DAYS:
        jobs += [("base", f"{d}_live", d, "live"), ("base", f"{d}_adapt", d, "live", "--macro-adapter"),
                 ("base", f"{d}_shadow", d, "shadow")]
    jobs += [("base", "2026-09-28_adapt_latency80", "2026-09-28", "live", "--macro-adapter", "--relay-latency-ms", "80"),
             ("restart", "r28_hard_0938", "2026-09-28", "09:38:45", "hard"),
             ("restart", "r28_hard_1030", "2026-09-28", "10:30:03", "hard"),
             ("restart", "r28_graceful_1030", "2026-09-28", "10:30:03", "graceful"),
             ("restart", "r28_kill_on_post", "2026-09-28", None, None, "APP")]
    only = {x for x in a.only.split(",") if x}
    if only:
        jobs = [j for j in jobs if j[1] in only]

    def go(j):
        if j[0] == "base":
            return base(a.root, *j[1:])
        return restart(a.root, *j[1:])
    with ThreadPoolExecutor(a.jobs) as ex:
        for res in ex.map(go, jobs):
            print(res, flush=True)


if __name__ == "__main__":
    main()
