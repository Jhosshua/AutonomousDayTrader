"""Run ORBStraddle's ORIGINAL decision modules (read from /Users/mo/ORBStraddle, never modified) on one past
session through the record/replay transport. Always a subprocess of record.py / compare.py.

Safety: the source .py files are copied into a fresh temp directory (so state/ files land there, never in
ORBStraddle), their sha256 must equal the pinned 71b001f hashes, ORBStraddle's .env is NOT copied, no
broker keys are set (config.DRY_RUN=True, core.TRADING_ARMED=False) and no order code is ever called: only
scanner.prep/run/run_secondary, adaptive.reply_text, core.validate and the candle/flow/macro re-check.

    python orig_runner.py --date 2026-09-22 --mode replay --out /tmp/orig.json [--phase prep|full]
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time as _time
from datetime import date, datetime, time as dtime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import parity_common as common  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--mode", choices=("record", "replay"), required=True)
    ap.add_argument("--phase", choices=("prep", "full"), default="full")
    ap.add_argument("--src", default="/Users/mo/ORBStraddle")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scan-deadline", default=None, help="replay-only override of ORBS_SCAN_DEADLINE_S (both impls)")
    args = ap.parse_args()

    with open(common.SYNC_EDITS) as f:
        pins = json.load(f)["source_sha256"]
    work = tempfile.mkdtemp(prefix="orbs_orig_")
    code_dir = os.path.join(work, "orbs")
    os.makedirs(os.path.join(code_dir, "state"))
    for path in glob.glob(os.path.join(args.src, "*.py")):
        shutil.copy2(path, code_dir)
    for name, want in pins.items():
        with open(os.path.join(code_dir, name), "rb") as f:
            got = hashlib.sha256(f.read()).hexdigest()
        if got != want:
            raise SystemExit(f"ORBStraddle {name} is not the pinned 71b001f content ({got[:12]} != {want[:12]})")

    manifest = common.load_manifest()
    for key in list(os.environ):
        if key.startswith("ORBS_") or key in ("JSL_KILL", "THESIS_STATE_URL"):
            del os.environ[key]
    os.environ.update(manifest["orbstraddle_railway_env"])
    for key, value in manifest["scanner_env"].items():
        if not key.startswith("_") and value is not None:
            os.environ[key] = value
    if args.scan_deadline:
        os.environ["ORBS_SCAN_DEADLINE_S"] = str(args.scan_deadline)
    os.environ["ORBS_RELAY_BASE"] = common.RELAY_ROOT + "/data"
    os.environ["ORBS_RELAY_TOKEN"] = "parity-transport"      # the transport never forwards it
    os.environ["ORBS_BOT_NAME"] = "ORBStraddle"
    os.environ["ORBS_RUNTIME_ENV"] = os.path.join(work, "runtime.env")   # absent -> no dashboard overrides
    os.environ["ORBS_STATE_DIR"] = os.path.join(code_dir, "state")

    synthetic = os.environ.get("ADT_PARITY_SYNTHETIC") == "1"
    token = common.read_token() if args.mode == "record" and not synthetic else ""
    transport = common.Transport(args.date, args.mode, token)
    import urllib.request
    urllib.request.urlopen = transport

    sys.path.insert(0, code_dir)
    os.chdir(work)
    import config, core, scanner, adaptive, flow, signals  # noqa: E401  (ORBStraddle's own modules)
    assert config.DRY_RUN and not core.TRADING_ARMED, "broker keys must not be present"
    assert config.ROOT == code_dir

    clock = {"now": None}
    core.now_et = lambda: clock["now"]
    d = date.fromisoformat(args.date)
    clock["now"] = datetime.combine(d, dtime(9, 15), tzinfo=common.ET)
    t0 = _time.monotonic()
    scanner.prep(d)
    universe = list(scanner._prep["wl"] or [])
    result = {"impl": "original", "day": args.date, "mode": args.mode, "universe": universe,
              "reference_session": scanner.REFERENCE_SESSION_DATE,
              "config": {k: (list(v) if isinstance(v, tuple) else v)
                         for k, v in ((k, getattr(config, k)) for k in manifest["effective"])},
              "scanner_tunables": {"PAGE_LIMIT": scanner.PAGE_LIMIT, "PAGE_ATTEMPTS": scanner.PAGE_ATTEMPTS,
                                   "SCAN_DEADLINE_S": scanner.SCAN_DEADLINE_S,
                                   "ORBS_INCREMENTAL": scanner.ORBS_INCREMENTAL,
                                   "CORRECTION_HORIZON_S": scanner.CORRECTION_HORIZON_S,
                                   "REPAIR_WINDOW_S": scanner.REPAIR_WINDOW_S,
                                   "RECONCILE_INTERVAL_S": scanner.RECONCILE_INTERVAL_S},
              "steps": []}
    if args.phase == "full":
        for step in common.schedule(args.date):
            clock["now"] = step["scan_now"]
            s0 = _time.monotonic()
            if step["wave"] == "secondary":
                cards, health = scanner.run_secondary(d, step["end"])
            else:
                cards, health = scanner.run(d, step["end"])
            rec = {"wave": step["wave"], "end": step["end"], "cards": cards, "health": common.health_subset(health),
                   "scan_s": round(_time.monotonic() - s0, 2)}
            att, okc = health.get("attempted"), health.get("ok")
            cov_ok = isinstance(att, int) and att > 0 and okc / att >= config.MIN_SCAN_COVERAGE
            decide = (step["wave"] == "primary" and cov_ok) or (
                step["wave"] == "secondary" and cards and (not att or cov_ok))
            if decide:
                clock["now"] = step["decide_now"]
                dec_cards = core._load_raw() if step["wave"] == "primary" else cards
                text, note = adaptive.reply_text(dec_cards, core.today())
                good, res = core.validate(text, dec_cards)
                rechecks = {}
                by_sym = {c["symbol"]: c for c in dec_cards}
                for p in (res if good else []):
                    c, sym = by_sym[p["symbol"]], p["symbol"]
                    verdict = [True, ""]
                    why = signals.candle_refusal(c, p) if config.CANDLE_RULE else None
                    if why:
                        verdict = [False, "candle rule: " + why]
                    else:
                        why = flow.flow_refusal(c, p)
                        if why:
                            verdict = [False, "flow rule: " + why]
                        elif config.MACRO_RULE:
                            why = flow.macro_refusals([p], now=core.now_et()).get(sym, "no macro verdict")
                            if why:
                                verdict = [False, "macro veto: " + why]
                    rechecks[sym] = verdict
                rec["decision"] = {"reply": json.loads(text), "note": note, "valid": good, "validated": res,
                                   "cards": dec_cards, "rechecks": rechecks, "now": step["decide_now"].isoformat()}
            result["steps"].append(rec)
            print(f"[original {args.date}] {step['wave']} {step['end']}: {len(cards)} cards, "
                  f"cov {health.get('coverage')}, {rec['scan_s']}s"
                  + (f", decision {'pass' if not (rec['decision']['valid'] and rec['decision']['validated']) else [p['symbol'] for p in rec['decision']['validated']]}"
                     if 'decision' in rec else ""), flush=True)
    result["misses"] = transport.misses
    result["counts"] = transport.counts
    result["elapsed_s"] = round(_time.monotonic() - t0, 1)
    with open(args.out, "w") as f:
        f.write(common.dumps(result))
    shutil.rmtree(work, ignore_errors=True)
    print(f"[original {args.date}] done in {result['elapsed_s']}s, misses {len(transport.misses)}, counts {transport.counts}")


if __name__ == "__main__":
    main()
