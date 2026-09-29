#!/usr/bin/env python3
"""scripts/build_compact_dashboard_fixtures.py

Frames for scripts/verify_compact_dashboard.py (PLAN_2026_09_29_compact_dashboard.md).

Source: one real websocket frame captured read-only from production on 2026-09-29 13:27 ET
(docs/compact_dashboard/fixtures/live_source_2026_09_29.json: 6 playbooks, flat account, ORB live).
Derived frames (written next to it):
  live.json            the source unchanged
  live_one.json        the source plus one open Coeur Morning Plan short shaped like the real 09-29 trade
                       (position fields copied from the holding_mood busy fixture's fixed-plan TSLA row)
  alarms.json          the source with every alarm switched on: an ORB orphan alert + its resolve button, an ORB
                       init error, a tri-engine broker issue, and the page banners (feed down, broker mismatch,
                       saving problem, daily loss stop, slow-trade entries withheld)
  branches.json        the source with the rare branches the plan attack listed: an ORB open trade (R, break-even,
                       exit requested), Ride the Trend switched off with add-ons off and refused setups, an OR15
                       plan holding with protection not yet confirmed, an early-close note
  branches_confirmed.json  branches.json with the OR15 plan's broker protection confirmed

    python3 scripts/build_compact_dashboard_fixtures.py
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs/compact_dashboard/fixtures"
SOURCE = OUT / "live_source_2026_09_29.json"
BUSY = ROOT / "docs/holding_mood/fixtures/busy.json"


def strategy(frame: dict, sid: str) -> dict:
    return next(s for s in frame["strategies"] if s["id"] == sid)


def main() -> None:
    live = json.loads(SOURCE.read_text())
    (OUT / "live.json").write_text(json.dumps(live, indent=1))

    one = copy.deepcopy(live)
    tsla = next(p for p in json.loads(BUSY.read_text())["all_positions"] if p["symbol"] == "TSLA")
    cde = copy.deepcopy(tsla)
    cde.update({
        "symbol": "CDE", "strategy_id": "cde_asymmetric_dual", "side": "SHORT", "shares": 1006, "qty": -1006,
        "entry_price": 17.47, "avg_entry_price": 17.47, "market_price": 17.235, "current_price": 17.235,
        "stop_loss": 17.695, "take_profit_1": 17.02, "take_profit_2": None, "exit_due": "2026-09-29T12:49:01-04:00",
        "unrealized_pnl": 236.41, "unrealized_pnl_pct": 0.0135, "cost_basis": 17574.82, "market_value": -17338.41,
        "plan_risk_pct": 0.75,
        "tranches": [{"id": 1, "qty": 1006, "closed_qty": 0, "stop": 17.695, "target": 17.02, "target_r": 2.0,
                      "exit_due": "2026-09-29T12:49:01-04:00", "exit_reason": None,
                      "protection_confirmed": True, "protection_terminal": False}],
    })
    one["all_positions"] = [cde]
    one["positions_count"] = 1
    (OUT / "live_one.json").write_text(json.dumps(one, indent=1))

    alarms = copy.deepcopy(live)
    orb = strategy(alarms, "orb")["orb"]
    text = "ORB holds 40 RKLB shares that no ORB order explains. They are never sold automatically: close them at Alpaca, then clear them here."
    orb["alerts"] = [text]
    orb["orphans"] = [{"symbol": "RKLB", "text": text}]
    orb["init_error"] = "ORB could not start its decision process; it will not trade today."
    orb["errors"] = [{"kind": "exit", "alarm": "orb_exit_retry", "symbol": "APP"}]
    strategy(alarms, "tsla_asymmetric_dual")["tri_engine"]["last_error"] = "HTTP 403 insufficient buying power"
    alarms["ingestion"] = {k: "disconnected" for k in (alarms.get("ingestion") or {"stock": 1, "news": 1, "vix": 1})}
    alarms.setdefault("broker", {})["mismatch"] = True
    alarms["persistence"]["status"] = "degraded"
    alarms["account"]["is_circuit_broken"] = True
    if alarms.get("swing") is not None:
        alarms["swing"]["last_close_entries_withheld"] = True
    (OUT / "alarms.json").write_text(json.dumps(alarms, indent=1))

    br = copy.deepcopy(live)
    orb = strategy(br, "orb")["orb"]
    orb["step"] = "Bought APP (long), 40 shares, now +0.30x its risk; stop 98.00, target 101.85 (held at Alpaca); closes by 11:00 AM."
    orb["open_trades"] = [{"symbol": "APP", "direction": "long", "qty": 40, "avg_price": 100.2, "stop": 98.0, "target": 101.85,
                           "r": 0.3, "last_price": 100.86, "breakeven_locked": True, "exit_requested": "time flatten at 11:00 AM",
                           "status": "OPEN"}]
    orb["realized_pnl"] = -12.5
    orb["unrealized_pnl"] = 26.4
    trend = strategy(br, "vwap_pullback")
    trend["mode"] = "off"
    trend["addons_enforced"] = False
    trend["last_block_by_symbol"] = {
        "AMD": {"bar": "2026-09-29T10:21:00-04:00", "event": "SESSION_DELTA_AGAINST"},
        "AAPL": {"bar": "2026-09-29T10:44:00-04:00", "event": "SPREAD_WIDE"},
    }
    news = strategy(br, "news_momentum")
    news["window"]["notes"] = ["Early close today: quick trades close by 12:55 PM."] + list(news["window"].get("notes") or [])
    or15 = copy.deepcopy(strategy(br, "tsla_asymmetric_dual"))
    or15.update({"id": "tsla_or15_retest", "name": "TSLA OR15 Retest", "tri_engine": None})
    or15.pop("tri_engine", None)
    or15["or15"] = {"phase": "HOLDING", "reason": "ENTRY_FILLED", "quantity": 1, "mode": "alpaca_paper", "or_high": 441.2,
                    "or_low": 436.8, "target_price": 449.9, "exit_due": "2026-09-29T12:10:00-04:00",
                    "protection_confirmed": False, "version": "TSLA_OR15_RETEST_2R_BROKER_PAPER_V1", "incomplete": False}
    br["strategies"].append(or15)
    (OUT / "branches.json").write_text(json.dumps(br, indent=1))

    confirmed = copy.deepcopy(br)
    strategy(confirmed, "tsla_or15_retest")["or15"]["protection_confirmed"] = True
    (OUT / "branches_confirmed.json").write_text(json.dumps(confirmed, indent=1))
    print("wrote live.json, live_one.json, alarms.json, branches.json, branches_confirmed.json")


if __name__ == "__main__":
    main()
