"""Run ADT's COPY (backend/app/strategies/orbs, through OrbsFacade) on one past session in REPLAY mode,
with the same schedule and pinned clock as orig_runner.py. A subprocess of compare.py, or run_copy()
in-process (the always-on synthetic parity test).

    python copy_runner.py --date 2026-09-22 --out /tmp/copy.json [--exclude TSLA,CDE]
"""
import argparse
import os
import shutil
import sys
import tempfile
import time as _time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import parity_common as common  # noqa: E402

if common.REPO not in sys.path:
    sys.path.insert(0, common.REPO)


def _make_transport(cache_root, day):
    """Built inside the ORB decision process (FacadeProxy): the replay transport for one day."""
    common.CACHE_ROOT = cache_root
    return common.Transport(day, "replay")


def run_copy(day, scan_deadline=None, exclude=None, log=print, max_steps=None, use_proxy=False):
    """use_proxy: run the facade in ADT's decision child process (orb_facade_proc.FacadeProxy), as
    production does, instead of in this process. Its outputs must be byte-identical."""
    manifest = common.load_manifest()
    if scan_deadline:
        manifest["scanner_env"]["ORBS_SCAN_DEADLINE_S"] = str(scan_deadline)
    if exclude:
        manifest["adt"]["exclude_symbols"] = list(exclude)
    work = tempfile.mkdtemp(prefix="orbs_copy_")
    if use_proxy:
        import functools
        from backend.app.core.orb_facade_proc import FacadeProxy
        transport = None
        fac = FacadeProxy(os.path.join(work, "state"), common.RELAY_ROOT, "parity-transport", manifest,
                          exclude=list(exclude) if exclude else None,
                          http_factory=functools.partial(_make_transport, common.CACHE_ROOT, day))
        fac.start()
    else:
        transport = common.Transport(day, "replay")
        from backend.app.strategies.orbs import config as ocfg
        ocfg.apply_manifest(manifest)          # before scanner is imported: it reads its tunables at import time
        from backend.app.strategies.orbs.facade import OrbsFacade
        fac = OrbsFacade(state_dir=os.path.join(work, "state"), relay_base=common.RELAY_ROOT,
                         relay_token="parity-transport", manifest=manifest, http=transport)
    d = date.fromisoformat(day)
    t0 = _time.monotonic()
    try:
        prep = fac.prep(d)
        eff = fac.effective_config()
        result = {"impl": "copy", "day": day, "mode": "replay", "universe": prep.get("universe") or [],
                  "reference_session": prep.get("reference_session"),
                  "config": {k: eff[k] for k in manifest["effective"]},
                  "scanner_tunables": eff["scanner"], "exclude_symbols": eff["EXCLUDE_SYMBOLS"], "steps": []}
        for step in common.schedule(day)[:max_steps]:
            end_dt = step["scan_now"].replace(second=0)
            s0 = _time.monotonic()
            board = fac.scan(d, end_dt, step["wave"], set(),
                             executed_today=set() if step["wave"] == "secondary" else None)
            if board["health"] is None:
                raise RuntimeError(f"scan failed: {board['error']}")
            cards, health = board["cards"], board["health"]
            rec = {"wave": step["wave"], "end": step["end"], "cards": cards,
                   "health": common.health_subset(health), "scan_s": round(_time.monotonic() - s0, 2)}
            out = None
            if step["wave"] != "preview":
                # the facade applies the board gates itself (scan ok, coverage, non-empty, cutoff, board_id)
                out = fac.decide(d, board, step["wave"], step["decide_now"], set(), executed_today=set())
                if out["verdict"] == "refused":
                    rec["refused"] = out["reason"]
                    out = None
            if out is not None:
                rechecks = {}
                by_sym = {c["symbol"]: c for c in out["cards"]}
                for p in (out["validated"] if out["valid"] else []):
                    ok, why = fac.recheck(by_sym[p["symbol"]], step["decide_now"])
                    rechecks[p["symbol"]] = [ok, why]
                rec["decision"] = {"reply": out["reply"], "note": out["note"], "valid": out["valid"],
                                   "validated": out["validated"], "cards": out["cards"], "rechecks": rechecks,
                                   "now": step["decide_now"].isoformat(), "verdict": out["verdict"],
                                   "picks": [p["symbol"] for p in out["picks"]]}
            result["steps"].append(rec)
            log(f"[copy {day}] {step['wave']} {step['end']}: {len(cards)} cards, cov {health.get('coverage')}, "
                f"{rec['scan_s']}s" + (f", decision {rec['decision']['verdict']} {rec['decision']['picks']}"
                                       if 'decision' in rec else ""))
        if use_proxy:
            result["misses"] = fac.http_attr("misses")
            result["counts"] = fac.http_attr("counts")
            result["tape_splits"] = fac.http_attr("tape.splits_done")
    finally:
        if use_proxy:
            fac.close()
        shutil.rmtree(work, ignore_errors=True)
    if not use_proxy:
        result["misses"] = transport.misses
        result["counts"] = transport.counts
        result["tape_splits"] = transport.tape.splits_done
    result["elapsed_s"] = round(_time.monotonic() - t0, 1)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scan-deadline", default=None, help="replay-only override of ORBS_SCAN_DEADLINE_S (both impls)")
    ap.add_argument("--exclude", default="", help="exclude_symbols for an ADT-behaviour run (parity runs leave it empty)")
    args = ap.parse_args()
    for key in list(os.environ):
        if key.startswith("ORBS_"):
            del os.environ[key]
    result = run_copy(args.date, args.scan_deadline, [s for s in args.exclude.split(",") if s],
                      log=lambda m: print(m, flush=True))
    with open(args.out, "w") as f:
        f.write(common.dumps(result))
    print(f"[copy {args.date}] done in {result['elapsed_s']}s, misses {len(result['misses'])}, "
          f"counts {result['counts']}")


if __name__ == "__main__":
    main()
