#!/usr/bin/env python3
# @steered SNARE-2 2026-09-30
"""Websocket frames for scripts/verify_overnight_holds.py (PLAN_2026_09_30_overnight_holds.md section 5).

Each frame is what the REAL backend.app.main broadcasts (broadcast_ui_state, captured from a stand in
websocket client) while the real overnight controller runs against the fake Alpaca of
backend/tests/unit/overnight_integration/fakes.py on a fake clock. Nothing is typed in by hand except
the loss day frame, which is the day frame with the loss stop flag set.

Prices. The closing auction fills at the real 2026-09-28 closes from the edge hunt research bars
(NVDA 228.87, IREN 41.73, HUT 92.75). At 20% of $49,700 that is 43, 238 and 107 shares.

No network. Every httpx transport that is not the fake's MockTransport raises. No keys are read.

    .venv/bin/python scripts/build_overnight_holds_fixtures.py
Writes docs/overnight_holds/fixtures/*.json.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PERSISTENCE_ENABLED", "false")
for key in list(os.environ):
    if key.startswith(("ALPACA_", "APCA_")) or key == "RELAY_TOKEN":
        del os.environ[key]

import httpx  # noqa: E402

BLOCKED: List[str] = []


def _refuse_sync(self, request):
    BLOCKED.append(f"{request.method} {request.url}")
    raise AssertionError(f"real network request attempted: {request.method} {request.url}")


async def _refuse_async(self, request):
    BLOCKED.append(f"{request.method} {request.url}")
    raise AssertionError(f"real network request attempted: {request.method} {request.url}")


httpx.HTTPTransport.handle_request = _refuse_sync
httpx.AsyncHTTPTransport.handle_async_request = _refuse_async

OUT = ROOT / "docs/overnight_holds/fixtures"
EQUITY = 49_700.0
CLOSE = {"NVDA": 228.87, "IREN": 41.73, "HUT": 92.75}      # real 2026-09-28 closes (research bars)
OPEN = {"NVDA": 230.10, "IREN": 42.05, "HUT": 93.40}
LOW = {"NVDA": 221.00, "IREN": 38.90, "HUT": 86.10}         # a bad night, -$1,723.50 in all
WED, THU, FRI = date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)


class Capture:
    """Stands in for one browser websocket in main.ui_clients."""

    def __init__(self) -> None:
        self.frames: List[str] = []

    async def send_text(self, raw: str) -> None:
        self.frames.append(raw)


def frame(r) -> Dict[str, Any]:
    cap = Capture()
    r.ui_clients.add(cap)
    try:
        asyncio.run(r.broadcast_ui_state(force=True))
    finally:
        r.ui_clients.discard(cap)
    if not cap.frames:
        raise SystemExit("broadcast_ui_state sent nothing")
    return json.loads(cap.frames[-1])


def stamp(fr: Dict[str, Any], when) -> Dict[str, Any]:
    """The page clock in the verifier is set from this (the frame's own timestamp is the wall clock)."""
    fr["_fixed_now"] = when.isoformat()
    return fr


def main() -> int:
    logging.disable(logging.CRITICAL)
    from backend.app import main as r
    from backend.tests.unit.overnight_integration.fakes import MainOvernight, at

    OUT.mkdir(parents=True, exist_ok=True)
    frames: Dict[str, Dict[str, Any]] = {}

    def prices(h) -> None:
        for sym, px in CLOSE.items():
            h.price(sym, px)

    # 1. Wednesday afternoon, nothing held, the buy planned for tonight
    h = MainOvernight(r, at(WED, 14, 0), equity=EQUITY)
    prices(h)
    h.step(at(WED, 14, 0))
    frames["day"] = stamp(frame(r), at(WED, 14, 0))

    # 2. the same afternoon after "No overnight buy tonight"
    ok, _msg = r.overnight.set_no_buy_tonight(True, at(WED, 14, 5))
    assert ok, _msg
    h.step(at(WED, 14, 5))
    frames["no_buy"] = stamp(frame(r), at(WED, 14, 5))

    # 3. 15:47:10 with the control used: every stock says why there is no buy tonight
    h.run(at(WED, 15, 47, 10), every=30)
    frames["no_buy_closing"] = stamp(frame(r), at(WED, 15, 47, 10))

    # 4. 15:47:10, HUT short of data (retried until 15:49:30), NVDA and IREN accepted at Alpaca
    h = MainOvernight(r, at(WED, 15, 40), equity=EQUITY)
    h.bar_counts["HUT"] = (100, True)
    prices(h)
    h.run(at(WED, 15, 47, 10), every=5)
    frames["closing"] = stamp(frame(r), at(WED, 15, 47, 10))
    # 5. 15:52, HUT given up (skip reason), too late for the control
    h.run(at(WED, 15, 52), every=5)
    frames["closing_late"] = stamp(frame(r), at(WED, 15, 52))

    # 6. Wednesday evening, three holds, sales queued at Alpaca for Thu Oct 1
    h = MainOvernight(r, at(WED, 15, 40), equity=EQUITY)
    prices(h)
    h.run(at(WED, 15, 59, 59), every=5)
    h.set(at(WED, 16, 0))
    h.alpaca.close_auction(CLOSE)
    h.run(at(WED, 16, 0, 30))
    h.run(at(WED, 19, 1), every=10)
    h.step(at(WED, 20, 0))
    frames["evening"] = stamp(frame(r), at(WED, 20, 0))

    # 7. Thursday 09:32, IREN's open halted: its sale is not filled after 09:31
    h.alpaca.halted = {"IREN"}
    h.run(at(THU, 9, 29, 50), every=60)
    h.set(at(THU, 9, 30))
    h.alpaca.open_auction(OPEN)
    h.run(at(THU, 9, 32), every=1)
    frames["morning_unsold"] = stamp(frame(r), at(THU, 9, 32))

    # 7b. Thursday 09:35 after a bad night: all three sold lower. The account is down, the loss stop is not.
    h = MainOvernight(r, at(WED, 15, 40), equity=EQUITY)
    prices(h)
    h.run(at(WED, 15, 59, 59), every=5)
    h.set(at(WED, 16, 0))
    h.alpaca.close_auction(CLOSE)
    h.run(at(WED, 16, 0, 30))
    h.run(at(WED, 19, 1), every=10)
    h.run(at(THU, 9, 29, 50), every=60)
    h.set(at(THU, 9, 30))
    h.alpaca.open_auction(LOW)
    h.run(at(THU, 9, 35), every=1)
    frames["morning_loss"] = stamp(frame(r), at(THU, 9, 35))

    # 8. Friday evening, three holds over the weekend (sell Mon Oct 5)
    h = MainOvernight(r, at(FRI, 15, 40), equity=EQUITY)
    prices(h)
    h.run(at(FRI, 15, 59, 59), every=5)
    h.set(at(FRI, 16, 0))
    h.alpaca.close_auction(CLOSE)
    h.run(at(FRI, 16, 0, 30))
    h.run(at(FRI, 19, 1), every=10)
    h.step(at(FRI, 20, 0))
    frames["weekend"] = stamp(frame(r), at(FRI, 20, 0))

    # 9. a loss limit day (derived): the day frame with the loss stop flag set
    loss = copy.deepcopy(frames["day"])
    loss["account"]["is_circuit_broken"] = True
    frames["loss_day"] = loss

    r.reset_runtime_state()
    for name, fr in frames.items():
        (OUT / f"{name}.json").write_text(json.dumps(fr, indent=1, sort_keys=True))
    summary = {name: {"now": fr.get("_fixed_now"),
                      "holds": [p["symbol"] for p in fr["all_positions"] if p.get("overnight")],
                      "rows": [(row["symbol"], row["state"], row["reason"], row["block"]) for row in
                               (fr.get("overnight") or {}).get("rows", [])],
                      "no_buy_tonight": (fr.get("overnight") or {}).get("no_buy_tonight"),
                      "unsold": (fr.get("overnight") or {}).get("unsold_after_0931")}
               for name, fr in frames.items()}
    (OUT / "index.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    if BLOCKED:
        print(f"network attempts refused: {BLOCKED}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
