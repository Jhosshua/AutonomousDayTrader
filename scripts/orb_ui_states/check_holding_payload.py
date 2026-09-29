#!/usr/bin/env python3
"""DEV ONLY: real-backend check for the Holding card / market mood (PLAN_2026_09_29).

Starts scripts/orb_ui_states/serve.py (the real backend + frontend/out, ORB on fakes, no broker, no network),
switches it to the `live_trade_adaptive` state and reads the frames the REAL websocket sends to a real page:
all_positions must carry entry_context on the adaptive holding and orb_context on the ORB holding, and
market_context must carry every new key. The page must render both banners. Nothing is mocked here.
Screenshots go to docs/holding_mood/screenshots/real_*.png. Needs `npx next build` first.

    python3 scripts/orb_ui_states/check_holding_payload.py
"""
from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs/holding_mood/screenshots"
CONTEXT_KEYS = {"vix", "vix_regime", "vix_stale", "vix_age_seconds", "sizing_multiplier", "stop_multiplier", "time_phase",
                "time_multiplier", "market_status", "market_trend", "market_trend_reason", "max_concurrent_positions",
                "notional_cap_pct", "base_risk_pct", "midday", "vix_tiers", "adaptive_strategies"}
ENTRY_KEYS = {"decided_at", "time_phase", "time_multiplier", "vix", "vix_regime", "sizing_multiplier", "stop_multiplier",
              "qty_adaptation", "qty_final", "qty_if_neutral", "size_limited_by", "market_trend", "trend_reason",
              "stop_raw", "stop_adapted"}
ORB_KEYS = {"classification", "short_frac", "wave", "decided_at", "short_bounds", "flow_rules_on", "breakeven_r",
            "risk_usd", "flatten_at"}
FAIL: list = []
PASS = [0]


def check(ok: bool, msg: str) -> None:
    if ok:
        PASS[0] += 1
        print(f"  ok   {msg}")
    else:
        FAIL.append(msg)
        print(f"  FAIL {msg}")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def post(base: str, path: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(base + path, method="POST", data=b""), timeout=60) as resp:
        return json.loads(resp.read())


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = tempfile.NamedTemporaryFile("w", prefix="holding_payload_serve_", suffix=".log", delete=False)
    srv = subprocess.Popen([sys.executable, str(ROOT / "scripts/orb_ui_states/serve.py"), "--port", str(port)],
                           cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/health", timeout=2)
                break
            except Exception:
                time.sleep(1)
        else:
            raise SystemExit("backend did not start")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--host-resolver-rules=MAP adt.test 127.0.0.1"])
            for vp_name, vp in (("desktop", {"width": 1440, "height": 900}), ("phone", {"width": 390, "height": 844})):
                print(f"\n=== {vp_name}")
                page = browser.new_context(viewport=vp).new_page()
                frames: list = []
                errors: list = []
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("websocket", lambda ws: ws.on("framereceived", lambda f: frames.append(f if isinstance(f, str) else f.get("payload", ""))))
                first = post(base, "/__dev/orb_state/waiting")
                page.clock.set_fixed_time(datetime.fromisoformat(first["fake_now"]))
                page.goto(f"http://adt.test:{port}/", wait_until="networkidle")
                page.wait_for_selector("[data-testid=market-mood]", timeout=20000)
                frames.clear()
                out = post(base, "/__dev/orb_state/live_trade_adaptive")
                page.clock.set_fixed_time(datetime.fromisoformat(out["fake_now"]))
                page.wait_for_selector("[data-testid=holding-row-NVDA]", timeout=20000)
                page.wait_for_timeout(1500)
                state = None
                for raw in reversed(frames):
                    try:
                        d = json.loads(raw)
                    except Exception:
                        continue
                    if d.get("type") == "STATE_UPDATE" and any(p["symbol"] == "NVDA" for p in d.get("all_positions", [])):
                        state = d
                        break
                check(state is not None, f"{vp_name}: a real STATE_UPDATE frame carries both holdings")
                if state is None:
                    continue
                pos = {p["symbol"]: p for p in state["all_positions"]}
                ec = pos["NVDA"].get("entry_context")
                check(isinstance(ec, dict) and ENTRY_KEYS <= set(ec), f"{vp_name}: NVDA all_positions entry_context has its keys {sorted(ENTRY_KEYS - set(ec or {}))}")
                check(ec and ec["vix_regime"] == "ELEVATED" and ec["sizing_multiplier"] == 0.7 and ec["qty_final"] <= ec["qty_if_neutral"],
                      f"{vp_name}: entry_context holds the nervous-market values the engine used")
                check(pos["NVDA"]["strategy_id"] == "news_momentum" and pos["NVDA"]["orb_context"] is None,
                      f"{vp_name}: adaptive holding is news_momentum with orb_context null")
                oc = pos["APP"].get("orb_context")
                check(isinstance(oc, dict) and set(oc) == ORB_KEYS, f"{vp_name}: ORB holding carries orb_context with every key")
                check(oc and oc["classification"] == "CALM_TREND" and oc["short_frac"] == 0.4 and oc["breakeven_r"] == 0.75
                      and oc["flatten_at"] == "11:00" and oc["risk_usd"] and oc["short_bounds"] == {"min": 0.25, "max": 0.75},
                      f"{vp_name}: orb_context values come from the real ORB ledger and config {oc}")
                check(pos["APP"]["entry_context"] is None, f"{vp_name}: ORB holding has no adaptive entry_context")
                mc = state["market_context"]
                check(CONTEXT_KEYS <= set(mc), f"{vp_name}: market_context has every new key {sorted(CONTEXT_KEYS - set(mc))}")
                check(mc["vix_regime"] == "ELEVATED" and len(mc["vix_tiers"]) == 4 and mc["midday"] == {"start": "11:30", "end": "14:00"},
                      f"{vp_name}: market_context tiers and midday come from live config")
                banners = {s: page.locator(f"[data-testid=holding-row-{s}] [data-testid=holding-strategy]").inner_text().strip() for s in ("NVDA", "APP")}
                check(banners["NVDA"] == "Big News" and banners["APP"] == "Opening Range Breakout (ORBStraddle rules)",
                      f"{vp_name}: the page renders both banners {banners}")
                check("Traded after its 9:38 market check: SPY and QQQ data complete" in page.locator("[data-testid=holding-orb-note]").inner_text(),
                      f"{vp_name}: the ORB box is built from the real orb_context")
                check("Bought" in page.locator("[data-testid=holding-row-NVDA] [data-testid=holding-why]").inner_text() or
                      "Normal size" in page.locator("[data-testid=holding-row-NVDA] [data-testid=holding-why]").inner_text(),
                      f"{vp_name}: the adaptive box says how it was sized")
                sw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                check(sw[0] <= sw[1], f"{vp_name}: no horizontal page scroll {sw}")
                page.screenshot(path=str(SHOTS / f"real_backend_{vp_name}.png"), full_page=True)
                # a state reset must not leak the injected holding
                post(base, "/__dev/orb_state/waiting")
                page.wait_for_function("() => !document.querySelector('[data-testid=holding-row-NVDA]')", timeout=15000)
                check(True, f"{vp_name}: reset to the next state removed the injected holding through the websocket")
                check(not [e for e in errors if "favicon" not in e], f"{vp_name}: no console errors {errors[:3]}")
            browser.close()
    finally:
        srv.send_signal(signal.SIGINT)
        try:
            srv.wait(timeout=30)
        except subprocess.TimeoutExpired:
            srv.kill()
            srv.wait()
        log.close()
    print(f"\n{PASS[0]} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
