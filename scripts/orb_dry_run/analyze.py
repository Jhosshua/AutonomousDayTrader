"""Compare ORB dry-run results with ORBStraddle (replay of the ORIGINAL code, and what it did live).

    python scripts/orb_dry_run/analyze.py OUT_ROOT            (reads OUT_ROOT/<run>/result.json)

Per run it checks: boards vs the parity replay of ORBStraddle's original code (same end minute, minus the
symbols ADT had to leave out: already executed / supervised), each decision vs the ORIGINAL code on the
same board at the same time (orig_decide.py output), the entry math (fresh price, stop, target, size),
fills and exits, broker writes (none in shadow, none after 11:01), the broker mismatch flag, the other-arm
refusal, the breaker's view of ORB P&L and the event-loop timings. Writes OUT_ROOT/summary.json."""
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store  # noqa: E402

pc = store.pc
START_EQUITY = 49702.10


def dumps(x):
    return json.dumps(x, sort_keys=True, default=str)


def load(path):
    with open(path) as f:
        return json.load(f)


def boards_vs_replay(res, day):
    path = os.path.join(store.base_dir(day), "replay_original.json")
    orig = load(path)
    by = {(s["wave"], s["end"]): s for s in orig["steps"]}
    out = {"compared": 0, "equal": 0, "diffs": []}
    for r in res["records"]:
        if r["kind"] != "scan" or not r.get("ok") and r.get("wave") != "secondary":
            continue
        s = by.get((r["wave"], r["end"]))
        if s is None:
            out["diffs"].append({"board": f"{r['wave']}@{r['end']}", "why": "no replay step"})
            continue
        leave_out = set(r.get("skip") or []) | set(r.get("executed") or [])
        want = [c for c in s["cards"] if c.get("symbol") not in leave_out]
        out["compared"] += 1
        if dumps(want) == dumps(r["cards"]):
            out["equal"] += 1
        else:
            ws = {c["symbol"]: c for c in want}
            gs = {c["symbol"]: c for c in r["cards"]}
            changed = sorted(k for k in set(ws) & set(gs) if dumps(ws[k]) != dumps(gs[k]))
            out["diffs"].append({"board": f"{r['wave']}@{r['end']}", "left_out": sorted(leave_out),
                                 "only_replay": sorted(set(ws) - set(gs)), "only_adt": sorted(set(gs) - set(ws)),
                                 "changed": changed[:10], "order_only": not changed and set(ws) == set(gs)})
    return out


def decisions_vs_original(res, orig_dec):
    dec = [r for r in res["records"] if r["kind"] == "decide" and r.get("verdict") != "refused"]
    out = {"compared": 0, "equal": 0, "diffs": []}
    for a, o in zip(dec, orig_dec["decisions"]):
        out["compared"] += 1
        a_picks = [p["symbol"] for p in (a.get("validated") or [])] if a.get("valid") else []
        o_picks = [p["symbol"] for p in (o.get("validated") or [])] if o.get("valid") else []
        a_verdict = a["verdict"] if a["verdict"] not in ("blocked",) else "trade"
        same = (dumps(a.get("reply")) == dumps(o.get("reply")) and a_picks == o_picks
                and a_verdict == o["verdict"] and a.get("board_id") == o.get("board_id"))
        if same:
            out["equal"] += 1
        else:
            out["diffs"].append({"at": a["now"], "adt": [a["verdict"], a_picks], "orig": [o["verdict"], o_picks]})
    if len(dec) != len(orig_dec["decisions"]):
        out["diffs"].append({"why": f"{len(dec)} ADT decisions vs {len(orig_dec['decisions'])} original"})
    out["orig_misses"] = orig_dec.get("misses")
    return out


def decisions_vs_replay_steps(res, day):
    """Informational: ADT's decisions vs the parity replay's decisions on the same board end (replay decides
    30 s after the scan and never excludes executed symbols)."""
    orig = load(os.path.join(store.base_dir(day), "replay_original.json"))
    by = {(s["wave"], s["end"]): s for s in orig["steps"]}
    rows = []
    for r in res["records"]:
        if r["kind"] != "decide":
            continue
        s = by.get((r["wave"], r.get("board_end")))
        d = (s or {}).get("decision")
        rp = [p["symbol"] for p in d["validated"]] if d and d["valid"] and d["validated"] else []
        rows.append({"board": f"{r['wave']}@{r.get('board_end')}", "adt_at": r["now"][11:19], "adt": r["verdict"],
                     "adt_picks": [p["symbol"] + ":" + p["direction"] for p in r["picks"]],
                     "adt_validated": [p["symbol"] for p in (r.get("validated") or [])] if r.get("valid") else [],
                     "replay_validated": rp, "same_validated": rp == ([p["symbol"] for p in (r.get("validated") or [])]
                                                                      if r.get("valid") else [])})
    return rows


def executions(res):
    rows = []
    latest = [r for r in res["records"] if r["kind"] == "latest_trade"]
    for r in res["records"]:
        if r["kind"] != "orb_event" or r["row"].get("kind") != "execution_intent":
            continue
        for p in r["row"]["plan"]:
            lt = [x for x in latest if x["symbol"] == p["symbol"] and x["sim"] <= r["sim"]]
            px = lt[-1]["price"] if lt else None
            chk = {}
            if px:
                stop = p["stop"]
                rd = abs(px - stop)
                risk = START_EQUITY * 0.02
                shares = int(risk // rd)
                shares = min(shares, int(START_EQUITY * 1.5 // px))
                tgt = round(px + 0.75 * rd * (1 if p["direction"] == "long" else -1), 2)
                chk = {"fresh_px": px, "rd": round(rd, 4), "target": tgt, "shares_first_trade": shares,
                       "rd_ok": abs(rd - p["rd"]) < 1e-3, "target_ok": abs(tgt - p["target"]) < 0.005,
                       "entry_ref_ok": round(px, 2) == p["entry_ref"]}
            rows.append({"sim": r["sim"], **p, "check": chk})
    return rows


def trades(res):
    t = res.get("trades_api") or {}
    items = t.get("items") or t.get("trades") or [] if isinstance(t, dict) else []
    out = []
    for x in items or res.get("pending_trades") or []:
        if x.get("strategy_id") != "orb":
            continue
        out.append({k: x.get(k) for k in ("symbol", "side", "quantity", "avg_entry_price", "avg_exit_price",
                                          "realized_pnl", "exit_reason", "opened_at", "closed_at", "r_multiple")})
    return out


def checks(res):
    writes = res.get("broker_writes") or []
    after_1101 = [w for w in writes if datetime.fromtimestamp(w["sim_ns"] / 1e9, pc.ET).strftime("%H:%M:%S") > "11:01:00"]
    arm = [r for r in res["records"] if r["kind"] == "arm_refusal_check"]
    rw = res.get("risk_while_holding") or {}
    breaker_ok = None
    if rw:
        breaker_ok = abs(rw["drawdown"] - max(0.0, round(rw["starting_equity"] - rw["equity"], 2))) < 0.02 \
            and abs(rw["equity"] - (rw["cash"] + sum(p["qty"] * p["mark"] * (1 if "LONG" in p["side"] else -1)
                                                  for p in rw["positions"].values()))) < 0.05
    return {"broker_writes": len(writes), "writes_after_1101": len(after_1101), "refused_403": res.get("refused_403"),
            "mismatch_seen": res.get("mismatch_seen"), "arm_refusals": [(a["strategy"], a["allowed"], (a["why"] or "")[:60]) for a in arm],
            "breaker_math_ok": breaker_ok, "risk_while_holding": rw, "loop": res.get("loop"),
            "budget": res.get("budget"), "orb_errors": res.get("orb_errors"), "orb_alerts": res.get("orb_alerts"),
            "misses": res.get("transport", {}).get("misses"), "run_errors": res.get("errors")}


def summarize(run_dir):
    res = load(os.path.join(run_dir, "result.json"))
    day = res["date"]
    out = {"run": os.path.basename(run_dir), "date": day, "mode": res["mode"], "adapter": res.get("macro_adapter"),
           "boards": boards_vs_replay(res, day), "replay_decisions": decisions_vs_replay_steps(res, day),
           "executions": executions(res), "fills": res.get("fills"), "trades": trades(res), "checks": checks(res),
           "snapshots": res.get("snapshots")}
    od = os.path.join(run_dir, "orig_decisions.json")
    if os.path.exists(od):
        out["decisions_vs_original"] = decisions_vs_original(res, load(od))
    return out


if __name__ == "__main__":
    root = sys.argv[1]
    runs = sys.argv[2:] or sorted(d for d in os.listdir(root) if os.path.exists(os.path.join(root, d, "result.json")))
    allout = {}
    for d in runs:
        s = summarize(os.path.join(root, d))
        allout[d] = s
        b = s["boards"]
        dv = s.get("decisions_vs_original", {})
        print(f"== {d}: boards {b['equal']}/{b['compared']} equal; decisions vs original "
              f"{dv.get('equal')}/{dv.get('compared')}; trades {[(t['symbol'], t['side'], t['realized_pnl'], t['exit_reason']) for t in s['trades']]}; "
              f"writes {s['checks']['broker_writes']} (after 11:01: {s['checks']['writes_after_1101']}); "
              f"mismatch {len(s['checks']['mismatch_seen'] or [])}")
    with open(os.path.join(root, "summary.json"), "w") as f:
        json.dump(allout, f, indent=1, default=str)
