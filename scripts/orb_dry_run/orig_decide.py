"""Run ORBStraddle's ORIGINAL decision code (/Users/mo/ORBStraddle @71b001f, hash-checked, copied to a temp
dir, no keys, no order code) on the EXACT boards ADT decided in a dry run, at ADT's decision times.

    python scripts/orb_dry_run/orig_decide.py --date 2026-09-28 --result DIR/result.json --out DIR/orig_decisions.json

For each ADT `decide` record: the original adaptive.reply_text + core.validate on the same cards (the primary
board read back like core._load_raw: valid cards only, first per symbol), with its clock pinned to ADT's
decision time, then the candle/flow/macro re-check of each validated pick. Relay answers come from the same
recorded store as the dry run (store.DryRunTransport). Output: one row per ADT decision."""
import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, time as dtime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store  # noqa: E402

pc = store.pc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", default="/Users/mo/ORBStraddle")
    a = ap.parse_args()
    a.result, a.out = os.path.abspath(a.result), os.path.abspath(a.out)
    with open(a.result) as f:
        res = json.load(f)
    decisions = [r for r in res["records"] if r["kind"] == "decide" and r.get("verdict") != "refused"]
    boards = {}
    for r in res["records"]:
        if r["kind"] == "scan" and r.get("board_id"):
            boards[r["board_id"]] = r["cards"]

    with open(pc.SYNC_EDITS) as f:
        pins = json.load(f)["source_sha256"]
    work = tempfile.mkdtemp(prefix="orbs_orig_decide_")
    code_dir = os.path.join(work, "orbs")
    os.makedirs(os.path.join(code_dir, "state"))
    for path in glob.glob(os.path.join(a.src, "*.py")):
        shutil.copy2(path, code_dir)
    for name, want in pins.items():
        with open(os.path.join(code_dir, name), "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != want:
                raise SystemExit(f"ORBStraddle {name} is not the pinned 71b001f content")
    manifest = pc.load_manifest()
    for key in list(os.environ):
        if key.startswith(("ORBS_", "ALPACA_", "APCA_")) or key in ("JSL_KILL", "THESIS_STATE_URL", "RELAY_TOKEN"):
            del os.environ[key]
    os.environ.update(manifest["orbstraddle_railway_env"])
    for key, value in manifest["scanner_env"].items():
        if not key.startswith("_") and value is not None:
            os.environ[key] = value
    os.environ.update({"ORBS_RELAY_BASE": pc.RELAY_ROOT + "/data", "ORBS_RELAY_TOKEN": "dry-run-transport",
                       "ORBS_BOT_NAME": "ORBStraddle", "ORBS_RUNTIME_ENV": os.path.join(work, "runtime.env"),
                       "ORBS_STATE_DIR": os.path.join(code_dir, "state")})
    clock = {"now": datetime.combine(date.fromisoformat(a.date), dtime(9, 15), tzinfo=pc.ET)}

    def now_ns():
        return pc.ts_ns(clock["now"].isoformat())
    transport = store.DryRunTransport(a.date, now_ns)
    import urllib.request
    urllib.request.urlopen = transport
    sys.path.insert(0, code_dir)
    os.chdir(work)
    import config, core, scanner, adaptive, flow, signals  # noqa: E401  (ORBStraddle's own modules)
    assert config.DRY_RUN and not core.TRADING_ARMED, "broker keys must not be present"
    core.now_et = lambda: clock["now"]
    d = date.fromisoformat(a.date)
    scanner.prep(d)
    out = []
    for rec in decisions:
        cards = boards.get(rec["board_id"])
        if cards is None:
            out.append({"board_id": rec["board_id"], "error": "board not recorded"})
            continue
        now = datetime.fromisoformat(rec["now"])
        clock["now"] = now
        if rec["wave"] == "primary":
            seen, dec_cards = set(), []
            for c in json.loads(json.dumps(cards)):
                if isinstance(c, dict) and core._valid_card(c) and c["symbol"] not in seen:
                    seen.add(c["symbol"])
                    dec_cards.append(c)
        else:
            dec_cards = cards
        text, note = adaptive.reply_text(dec_cards, core.today())
        good, validated = core.validate(text, dec_cards)
        by_sym = {c["symbol"]: c for c in dec_cards}
        rechecks = {}
        for p in (validated if good else []):
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
                    why = flow.macro_refusals([p], now=now).get(sym, "no macro verdict")
                    if why:
                        verdict = [False, "macro veto: " + why]
            rechecks[sym] = verdict
        reply = json.loads(text)
        if not good:
            verdict = "rejected"
        elif not validated:
            verdict = "sit_out" if (reply.get("regime") or {}).get("action") == "SIT_OUT_CASH" else "pass"
        else:
            verdict = "trade"
        out.append({"board_id": rec["board_id"], "wave": rec["wave"], "now": rec["now"], "verdict": verdict,
                    "valid": good, "validated": validated, "reply": reply, "note": note, "rechecks": rechecks})
    with open(a.out, "w") as f:
        json.dump({"decisions": out, "misses": transport.misses, "counts": transport.counts}, f, default=str)
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({"decisions": len(out), "misses": len(transport.misses)}))


if __name__ == "__main__":
    main()
