#!/usr/bin/env python3
# @steered SNARE-2 2026-09-30
"""scripts/verify_overnight_holds.py

Page QA for the minimal overnight holds page (PLAN_2026_09_30_overnight_holds.md section 5, the operator's
minimal scope: no redesign before mockups).

Serves the static export (frontend/out, `npm run build` in frontend/ first) on port 3005 with NO backend.
The websocket, /health, /api/trades, /api/overnight and POST /api/overnight/no-buy-tonight are Playwright
route mocks. The frames are docs/overnight_holds/fixtures/*.json, built from the REAL backend broadcast
by scripts/build_overnight_holds_fixtures.py. The page clock is fixed to each frame's `_fixed_now`.
ONE page per viewport (1440x900 and 390x844) is switched from state to state by pushing frames, never
reloaded. Screenshots of the evening state with three holds go to docs/overnight_holds/screenshots/.

Every expected sentence is written out here from the plan and the operator's brief, not read back from
the page code. Negative controls at the end prove the detectors can fail.

    python3 scripts/build_overnight_holds_fixtures.py     # (with the repo .venv) only when the payload changes
    python3 scripts/verify_overnight_holds.py
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import verify_ui_redesign as base  # noqa: E402  (export server + mock payload helpers)

FIXTURES = ROOT / "docs/overnight_holds/fixtures"
SHOTS = ROOT / "docs/overnight_holds/screenshots"
BASE_URL = base.BASE_URL
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS"}

FAILURES: List[str] = []
PASSED = [0]


def check(ok: bool, msg: str) -> None:
    if ok:
        PASSED[0] += 1
        print(f"  ok   {msg}")
    else:
        FAILURES.append(msg)
        print(f"  FAIL {msg}")


def frame(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


# ---------------------------------------------------------------------------------------------------
# Expected text, from the brief and the plan
# ---------------------------------------------------------------------------------------------------
MOVE_TOGETHER = "IREN and HUT are both bitcoin miners and move together."
HOLD_THU = {
    "NVDA": "43 shares of NVDA bought at $228.87 at the close. No stop. Sells at the 9:30 AM open on Thu Oct 1.",
    "IREN": f"238 shares of IREN bought at $41.73 at the close. No stop. Sells at the 9:30 AM open on Thu Oct 1. {MOVE_TOGETHER}",
    "HUT": f"107 shares of HUT bought at $92.75 at the close. No stop. Sells at the 9:30 AM open on Thu Oct 1. {MOVE_TOGETHER}",
}
HOLD_WEEKEND_NVDA = "43 shares of NVDA bought at $228.87 at the close. No stop. Held over the weekend, sells at the 9:30 AM open on Mon Oct 5."
HOLD_HOLIDAY_NVDA = "43 shares of NVDA bought at $228.87 at the close. No stop. Held over the holiday, sells at the 9:30 AM open on Fri Nov 27."
BALANCE_NOTE_3 = "Includes $29,697.40 in overnight holds at their buy price. Their real value is known at 9:30 AM."  # 43x228.87 + 238x41.73 + 107x92.75
SUMMARY_ALL = "Tonight it buys NVDA, IREN and HUT at the 4:00 PM close and sells at the next 9:30 AM open."
SUMMARY_NVDA_HUT = "Tonight it buys NVDA and HUT at the 4:00 PM close and sells at the next 9:30 AM open."
BTN_OFF = "No overnight buy tonight"
BTN_UNDO = "Turn tonight's buy back on"
BTN_CONFIRM = "Tap again to confirm"
REPLY_ON = "No overnight buy tonight. Holds already bought still sell at the next open."   # backend overnight_execution.py
REPLY_OFF = "The overnight buy is on for tonight."
NO_BUY_ON_LINE = "Tonight's buy is off. Holds already bought still sell at the next open."
TOO_LATE = "Too late to change tonight. It can only be changed until 3:49:30 PM."
ALREADY_STOPPED = "Tonight's buy was already stopped and cannot be restarted."
NOTHING_PLANNED = "No overnight buy is planned tonight."
LOSS_LINE_NEW = ("If day trades ever lose $1,500.00 in a day, it stops day trading for the day on its own. "
                 "The daily loss limit covers day trades only. The overnight buy still goes in at the close.")
LOSS_LINE_OLD = "If it ever loses $1,500.00 in a day, it stops for the day on its own."
QUICK_OLD = "Quick trades start closing at 3:55 PM. Slow trades can stay open for days."
# on a buy night the lead itself carries the 3:46 PM exception (X6), it never says all quick trades close at 3:55 PM
QUICK_NEW = ("Most quick trades close at 3:55 PM, but a quick trade in NVDA, IREN or HUT closes at 3:46 PM on a night "
             "the robot buys that stock. Slow trades can stay open for days. Overnight holds buy at the 4:00 PM close "
             "and sell at the next 9:30 AM open.")
SMALL_OLD = ("Small bets. Keeps each bet small (about 1% of the account at risk). Exception: Opening Range Breakout "
             "follows ORBStraddle's sizing, 2% on its first trade of the day and 2.5% in total.")
SMALL_NEW = SMALL_OLD + " Overnight holds are not small bets. Each puts 20% of the account in one stock with no stop."
EXIT_OLD = "Every trade has an exit plan. A price where it gives up, set before it buys."
EXIT_NEW = "Every day trade has an exit plan. A price where it gives up, set before it buys. Overnight holds have no stop and sell at the next open."
HOLDS_NOT_CLOSED = "Overnight holds are not included. They sell at the next 9:30 AM open."
UNSOLD_IREN = "IREN overnight is not sold yet after 9:31 AM, or the robot's book and Alpaca disagree on it. Check the Alpaca app."
STOPPED_NEW = "Day trading stopped for today. It hit the daily loss limit."
STOPPED_OLD = "Stopped for today. It hit the daily loss limit."
TONIGHT = {
    "no_buy_closing": {s: "No buy tonight. You turned off tonight's buy." for s in ("NVDA", "IREN", "HUT")},
    "closing": {
        "NVDA": "Buy for 43 shares is waiting at Alpaca. It fills at the 4:00 PM close.",
        "IREN": "Buy for 238 shares is waiting at Alpaca. It fills at the 4:00 PM close.",
        "HUT": "Not bought yet. Too much of today's price data is missing. It keeps trying until 3:49:30 PM.",
    },
    "closing_late": {
        "NVDA": "Buy for 43 shares is waiting at Alpaca. It fills at the 4:00 PM close.",
        "IREN": "Buy for 238 shares is waiting at Alpaca. It fills at the 4:00 PM close.",
        "HUT": "No buy tonight. Too much of today's price data is missing.",
    },
}
# new page words must not use dashes as punctuation, colons or semicolons in sentences (times like 3:49 PM are fine)
PUNCT = re.compile(r"(\s[-\u2013\u2014]\s|[\u2013\u2014]|;|:\s)")


def norm(s: str) -> str:
    return " ".join(s.split())


class Session:
    """One page, one viewport. Frames are pushed over the mocked websocket, the clock is fixed per frame."""

    def __init__(self, browser, label: str, width: int, height: int, first: Dict[str, Any]):
        self.label = label
        self.ctx = browser.new_context(viewport={"width": width, "height": height}, timezone_id="America/New_York")
        self.page = self.ctx.new_page()
        self.ws: Dict[str, Any] = {}
        self.navs = [0]
        self.errors: List[str] = []
        self.posts: List[Dict[str, Any]] = []
        self.reply: Dict[str, Any] = {"status": 200, "body": {"ok": True, "message": REPLY_ON}}
        self.current = first
        p = self.page
        p.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        p.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        p.on("framenavigated", lambda f: self.navs.__setitem__(0, self.navs[0] + 1) if f == p.main_frame else None)

        def on_ws(ws) -> None:
            self.ws["route"] = ws
            ws.send(json.dumps(self.current))

        def json_route(body_fn):
            def handler(route) -> None:
                if route.request.method == "OPTIONS":
                    route.fulfill(status=204, headers=CORS)
                    return
                route.fulfill(status=200, content_type="application/json", headers=CORS, body=json.dumps(body_fn()))
            return handler

        def on_post(route) -> None:
            if route.request.method == "OPTIONS":
                route.fulfill(status=204, headers=CORS)
                return
            self.posts.append(route.request.post_data_json)
            route.fulfill(status=self.reply["status"], content_type="application/json", headers=CORS,
                          body=json.dumps(self.reply["body"]))

        p.route_web_socket(re.compile(r".*/ws/ui$"), on_ws)
        p.route(re.compile(r".*/api/trades.*"), json_route(lambda: base.trades_response([])))
        p.route(re.compile(r".*/health$"), json_route(lambda: base.HEALTH_RESPONSE))
        p.route(re.compile(r".*/api/account$"), json_route(lambda: self.current["account"]))
        p.route(re.compile(r".*/api/overnight$"), json_route(lambda: self.current.get("overnight")))
        p.route(re.compile(r".*/api/overnight/no-buy-tonight$"), on_post)
        p.clock.set_fixed_time(first["_fixed_now"])
        p.goto(BASE_URL, wait_until="networkidle", timeout=20000)
        p.wait_for_selector("[data-testid=risk-telemetry]", timeout=15000)
        self.loads = self.navs[0]
        p.wait_for_timeout(1200)

    def show(self, fr: Dict[str, Any], settle_ms: int = 450) -> None:
        self.current = fr
        if fr.get("_fixed_now"):
            self.page.clock.set_fixed_time(fr["_fixed_now"])
        self.ws["route"].send(json.dumps(fr))
        self.page.wait_for_timeout(settle_ms)

    def text(self, sel: str) -> str:
        loc = self.page.locator(sel)
        return norm(loc.first.inner_text()) if loc.count() else ""

    def close(self) -> None:
        self.ctx.close()


def shot(s: Session, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    out = SHOTS / f"{name}_{s.label}.png"
    s.page.screenshot(path=str(out), full_page=True)
    check(out.exists() and out.stat().st_size > 5000, f"[{s.label}] screenshot {out.relative_to(ROOT)}")


JS_OVERFLOW = """() => {
  const w = window.innerWidth, bad = [];
  const de = document.documentElement;
  if (de.scrollWidth > w + 1) bad.push('document scrollWidth ' + de.scrollWidth + ' > ' + w);
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || !el.getClientRects().length) continue;
    if (el.closest('.drift, .drift2') || cs.position === 'fixed') continue;     // decorative blurred blobs
    const r = el.getBoundingClientRect();
    if (r.width && (r.right > w + 1 || r.left < -1)) bad.push(el.tagName + '.' + (el.getAttribute('data-testid') || '') + ' [' + Math.round(r.left) + ',' + Math.round(r.right) + ']');
  }
  return bad.slice(0, 8);
}"""


def hold_symbols(s: Session) -> List[str]:
    return s.page.eval_on_selector_all("[data-testid^=overnight-hold-]:not([data-testid=overnight-hold-line])",
                                       "els => els.map(e => e.getAttribute('data-testid').replace('overnight-hold-', ''))")


def common(s: Session, state: str, holds: List[str], quick: List[str]) -> None:
    """Checks every state must pass."""
    lab = f"{s.label}/{state}"
    p = s.page
    ov = p.evaluate(JS_OVERFLOW)
    check(not ov, f"[{lab}] no horizontal overflow {ov[:3]}")
    got = hold_symbols(s)
    check(sorted(got) == sorted(holds), f"[{lab}] overnight hold rows {sorted(holds)} (got {sorted(got)})")
    rows = p.eval_on_selector_all("[data-testid^=holding-row-]", "els => els.map(e => e.getAttribute('data-testid').replace('holding-row-', ''))")
    check(sorted(rows) == sorted(quick), f"[{lab}] Quick trades rows {sorted(quick)} (got {sorted(rows)})")
    for sym in holds:
        check(p.locator(f"[data-testid=holding-row-{sym}]").count() == 0, f"[{lab}] {sym} hold is not in the Quick trades list")
        row = p.locator(f"[data-testid=overnight-hold-{sym}]")
        label = norm(row.locator("[data-testid=overnight-strategy]").inner_text()) if row.count() else ""
        check(label == f"{sym} overnight", f"[{lab}] {sym} row label '{sym} overnight' (got '{label}')")
    body = norm(p.inner_text("body"))
    if holds:
        check("Manual trade" not in body, f"[{lab}] 'Manual trade' appears nowhere on the page")
    sec = p.locator("[data-testid=overnight-holds]")
    if sec.count():
        sec_text = sec.inner_text()
        for bad in ("Sell now", "Move safety exit", "Close trade"):
            check(bad not in sec_text, f"[{lab}] no '{bad}' button on the holds")
        check(sec.locator("button").count() == 1, f"[{lab}] the holds section has exactly one button (the no buy control)")
        new_text = sec_text + "\n" + s.text("[data-testid=overnight-unsold-banner]") + "\n" + s.text("[data-testid=safety-overnight-result]") \
            + "\n" + s.text("[data-testid=balance-overnight-note]") + "\n" + s.text("[data-testid=safety-holds-not-closed]")
        bad_p = PUNCT.findall(new_text)
        check(not bad_p, f"[{lab}] new words have no dashes, colons or semicolons {bad_p[:3]}")
        btn = p.locator("[data-testid=btn-no-buy-tonight]")
        h = btn.bounding_box()["height"] if btn.count() else 0
        check(h >= 44, f"[{lab}] no buy control is at least 44 px tall ({h})")
    quick_count = len(quick)
    sentence = s.text("[data-testid=right-now-sentence]")
    if quick_count == 0:
        check(not re.search(r"Holding \d+ trades? right now", sentence), f"[{lab}] Right now does not count holds as quick trades ('{sentence}')")
    else:
        plural = "trade" if quick_count == 1 else "trades"
        check(sentence.startswith(f"Holding {quick_count} {plural} right now."), f"[{lab}] Right now counts {quick_count} quick {plural} ('{sentence}')")
    flat = p.locator("[data-testid=btn-flatten-all]")
    check(flat.is_disabled() == (quick_count == 0), f"[{lab}] 'Close all quick trades now' enabled only with quick trades")
    check(not s.errors, f"[{lab}] no console errors {s.errors[:2]}")


def contains(s: Session, sel: str, needle: str, lab: str) -> None:
    got = s.text(sel)
    check(needle in got, f"[{s.label}] {lab}: '{needle}' (got '{got[:160]}')")


def equals(s: Session, sel: str, want: str, lab: str) -> None:
    got = s.text(sel)
    check(got == want, f"[{s.label}] {lab}: '{want}' (got '{got[:160]}')")


def absent(s: Session, sel: str, lab: str) -> None:
    check(s.page.locator(sel).count() == 0, f"[{s.label}] {lab} not shown")


def btn_state(s: Session, label: str, enabled: bool, lab: str) -> None:
    b = s.page.locator("[data-testid=btn-no-buy-tonight]")
    got = norm(b.inner_text()) if b.count() else ""
    check(got == label and b.is_enabled() == enabled,
          f"[{s.label}] {lab}: button '{label}' {'enabled' if enabled else 'disabled'} (got '{got}', enabled={b.is_enabled() if b.count() else None})")


def with_quick(fr: Dict[str, Any], sym: str = "NVDA") -> Dict[str, Any]:
    out = copy.deepcopy(fr)
    out["all_positions"] = out["all_positions"] + [base.position(sym, "LONG", 10, 100.0, 101.0, 99.0, strategy_id="vwap_pullback")]
    out["positions_count"] = len(out["all_positions"])
    return out


# ---------------------------------------------------------------------------------------------------
# The state matrix
# ---------------------------------------------------------------------------------------------------
def run_matrix(s: Session) -> None:
    print(f"\n=== {s.label}")
    p = s.page

    # day, Wed 2:00 PM, nothing held, the buy planned for tonight
    s.show(frame("day"))
    common(s, "day", [], [])
    equals(s, "[data-testid=overnight-summary]", SUMMARY_ALL, "day summary")
    btn_state(s, BTN_OFF, True, "day")
    absent(s, "[data-testid=overnight-no-buy-reason]", "day disabled reason")
    absent(s, "[data-testid=overnight-tonight]", "day (before 3:45 PM) tonight lines")
    absent(s, "[data-testid=overnight-unsold-banner]", "day unsold banner")
    absent(s, "[data-testid=balance-overnight-note]", "day balance note (no holds)")
    contains(s, "[data-testid=risk-telemetry]", LOSS_LINE_NEW, "day Safety loss line")
    contains(s, "[data-testid=risk-telemetry]", QUICK_NEW, "day Safety closing line")
    body = s.text("[data-testid=risk-telemetry]")
    check("Quick trades start closing at 3:55 PM" not in body,
          f"[{s.label}] day Safety lead never says every quick trade closes at 3:55 PM on a buy night")
    contains(s, "[data-testid=risk-telemetry]", SMALL_NEW, "day Safety small bets line")
    contains(s, "[data-testid=risk-telemetry]", EXIT_NEW, "day Safety exit plan line")
    contains(s, "[data-testid=risk-telemetry]", "Daily loss limit used $0.00 of $1,500.00", "day Safety meter")
    equals(s, "[data-testid=safety-overnight-result]", "No overnight hold result today.", "day overnight result line")

    # the control: stop tonight's buy (two taps), POST {on: true}, plain confirmation line
    n0 = len(s.posts)
    s.reply = {"status": 200, "body": {"ok": True, "message": REPLY_ON, "no_buy_tonight": True}}
    p.click("[data-testid=btn-no-buy-tonight]")
    p.wait_for_timeout(150)
    btn_state(s, BTN_CONFIRM, True, "first tap asks to confirm")
    check(len(s.posts) == n0, f"[{s.label}] the first tap sends nothing")
    p.click("[data-testid=btn-no-buy-tonight]")
    p.wait_for_timeout(500)
    check(s.posts[n0:] == [{"on": True}], f"[{s.label}] second tap POSTs {{'on': true}} (got {s.posts[n0:]})")
    equals(s, "[data-testid=overnight-no-buy-reply]", REPLY_ON, "confirmation line")
    s.show(frame("no_buy"))
    common(s, "no_buy", [], [])
    btn_state(s, BTN_UNDO, True, "no_buy undo")
    equals(s, "[data-testid=overnight-no-buy-on]", NO_BUY_ON_LINE, "no_buy line")
    absent(s, "[data-testid=overnight-summary]", "no_buy summary")
    # undo: one tap, POST {on: false}
    s.reply = {"status": 200, "body": {"ok": True, "message": REPLY_OFF, "no_buy_tonight": False}}
    p.click("[data-testid=btn-no-buy-tonight]")
    p.wait_for_timeout(500)
    check(s.posts[n0 + 1:] == [{"on": False}], f"[{s.label}] undo POSTs {{'on': false}} in one tap (got {s.posts[n0 + 1:]})")
    equals(s, "[data-testid=overnight-no-buy-reply]", REPLY_OFF, "undo confirmation line")
    # a refusal shows the backend's plain reason
    s.reply = {"status": 409, "body": {"detail": "Too late for tonight. The control can only be changed until 3:49:30 PM."}}
    p.click("[data-testid=btn-no-buy-tonight]")
    p.wait_for_timeout(500)
    equals(s, "[data-testid=overnight-no-buy-reply]", "Too late for tonight. The control can only be changed until 3:49:30 PM.", "refusal line")
    s.reply = {"status": 200, "body": {"ok": True, "message": REPLY_ON}}
    # the browser logs the intended 409 as a failed resource; only that exact message is cleared
    s.errors[:] = [e for e in s.errors if "status of 409" not in e]
    p.wait_for_timeout(8500)
    absent(s, "[data-testid=overnight-no-buy-reply]", "the reply line after 8 s (it does not linger into later states)")

    # 3:47 PM with the control used: each stock says why, the undo is refused with a plain reason
    s.show(frame("no_buy_closing"))
    common(s, "no_buy_closing", [], [])
    for sym, want in TONIGHT["no_buy_closing"].items():
        equals(s, f"[data-testid=overnight-tonight-{sym}]", f"{sym} overnight. {want}", f"no_buy_closing {sym}")
    btn_state(s, BTN_UNDO, False, "no_buy_closing")
    equals(s, "[data-testid=overnight-no-buy-reason]", ALREADY_STOPPED, "no_buy_closing reason")

    # 3:47 PM normal night: NVDA and IREN waiting at Alpaca, HUT short of data and still trying
    s.show(frame("closing"))
    common(s, "closing", [], [])
    for sym, want in TONIGHT["closing"].items():
        equals(s, f"[data-testid=overnight-tonight-{sym}]", f"{sym} overnight. {want}", f"closing {sym}")
    btn_state(s, BTN_OFF, True, "closing (before 3:49:30 PM)")

    # 3:52 PM: too late, HUT given up with its reason
    s.show(frame("closing_late"))
    common(s, "closing_late", [], [])
    for sym, want in TONIGHT["closing_late"].items():
        equals(s, f"[data-testid=overnight-tonight-{sym}]", f"{sym} overnight. {want}", f"closing_late {sym}")
    btn_state(s, BTN_OFF, False, "closing_late")
    equals(s, "[data-testid=overnight-no-buy-reason]", TOO_LATE, "closing_late reason")

    # Wed 8:00 PM, three holds (the screenshot state)
    ev = frame("evening")
    s.show(ev)
    common(s, "evening", ["NVDA", "IREN", "HUT"], [])
    for sym, want in HOLD_THU.items():
        equals(s, f"[data-testid=overnight-hold-{sym}] [data-testid=overnight-hold-line]", want, f"evening {sym} line")
    equals(s, "[data-testid=balance-overnight-note]", BALANCE_NOTE_3, "evening balance note")
    contains(s, "[data-testid=right-now-sentence]", "Holding 3 overnight stocks until the 9:30 AM open on Thu Oct 1.", "evening Right now")
    equals(s, "[data-testid=safety-holds-not-closed]", HOLDS_NOT_CLOSED, "evening Close all note")
    btn_state(s, BTN_OFF, False, "evening")
    equals(s, "[data-testid=overnight-no-buy-reason]", TOO_LATE, "evening reason")
    absent(s, "[data-testid=overnight-unsold-banner]", "evening unsold banner")

    # the evening with a quick trade still open elsewhere: the count is quick trades only
    s.show(with_quick(ev, "TSLA"))
    common(s, "evening+quick", ["NVDA", "IREN", "HUT"], ["TSLA"])

    # Friday 8:00 PM, over the weekend
    s.show(frame("weekend"))
    common(s, "weekend", ["NVDA", "IREN", "HUT"], [])
    equals(s, "[data-testid=overnight-hold-NVDA] [data-testid=overnight-hold-line]", HOLD_WEEKEND_NVDA, "weekend NVDA line")
    contains(s, "[data-testid=overnight-hold-HUT] [data-testid=overnight-hold-line]", MOVE_TOGETHER, "weekend HUT moves together")

    # over a holiday (derived from the evening frame: Wed Nov 25 buy, Fri Nov 27 sale)
    hol = copy.deepcopy(ev)
    for h in hol["overnight"]["holds"]:
        h["sale_date"], h["nights"] = "2026-11-27", "holiday"
    s.show(hol)
    equals(s, "[data-testid=overnight-hold-NVDA] [data-testid=overnight-hold-line]", HOLD_HOLIDAY_NVDA, "holiday NVDA line")

    # Thu 9:32 AM, IREN's sale not filled: red banner, NVDA and HUT sold
    s.show(frame("morning_unsold"))
    common(s, "morning_unsold", ["IREN"], [])
    equals(s, "[data-testid=overnight-unsold-banner]", UNSOLD_IREN, "morning_unsold banner")
    banner_color = p.eval_on_selector("[data-testid=overnight-unsold-banner]", "e => getComputedStyle(e).color")
    check(banner_color == "rgb(194, 48, 15)", f"[{s.label}] unsold banner uses the alarm red #C2300F ({banner_color})")
    equals(s, "[data-testid=safety-overnight-result]", "Overnight holds made $122.44 today. This is not counted in the limit above.", "morning_unsold result")
    equals(s, "[data-testid=overnight-summary]", SUMMARY_NVDA_HUT, "morning_unsold summary (IREN still held)")

    # Thu 9:35 AM after a bad night: the meter reads the loss stop's own drawdown, the result on its own line
    s.show(frame("morning_loss"))
    common(s, "morning_loss", [], [])
    absent(s, "[data-testid=overnight-unsold-banner]", "morning_loss unsold banner")
    contains(s, "[data-testid=risk-telemetry]", "Daily loss limit used $0.00 of $1,500.00", "morning_loss meter ignores the overnight loss")
    equals(s, "[data-testid=safety-overnight-result]", "Overnight holds lost $1,723.50 today. This is not counted in the limit above.", "morning_loss result")
    equals(s, "[data-testid=overnight-summary]", SUMMARY_ALL, "morning_loss summary")

    # the loss limit hit: day trading stops, tonight's buy still planned (D5)
    s.show(frame("loss_day"))
    common(s, "loss_day", [], [])
    contains(s, "main", STOPPED_NEW, "loss_day banner")
    contains(s, "[data-testid=right-now-sentence]", "Day trading stopped for today. It hit the $1,500 loss limit.", "loss_day Right now")
    equals(s, "[data-testid=overnight-summary]", SUMMARY_ALL, "loss_day summary (D5 still buys)")

    # a quick NVDA trade on a buy night: it closes at 3:46 PM (X6), the count still quick only
    s.show(with_quick(frame("day"), "NVDA"))
    common(s, "day+NVDA quick", [], ["NVDA"])
    contains(s, "main", "NVDA quick trade closes in", "X6 countdown label")
    contains(s, "main", "1h 46m", "X6 countdown to 3:46 PM")

    # nothing planned: every stock skipped before the close with the control off (derived)
    nothing = copy.deepcopy(frame("closing_late"))
    for r in nothing["overnight"]["rows"]:
        r["state"], r["reason"] = "SKIPPED", "DATA_SHORT"
    nothing["_fixed_now"] = "2026-09-30T15:48:00-04:00"
    s.show(nothing)
    btn_state(s, BTN_OFF, False, "nothing planned")
    equals(s, "[data-testid=overnight-no-buy-reason]", NOTHING_PLANNED, "nothing planned reason")
    check(s.navs[0] == s.loads, f"[{s.label}] the page was never reloaded ({s.navs[0]} navigations)")


def run_old_backend(browser, label: str, width: int, height: int) -> None:
    """An older backend (no overnight key, no risk_drawdown): every changed string reads exactly as before."""
    print(f"\n=== {label}/old backend")
    old = frame("day")
    old.pop("overnight", None)
    for k in ("risk_drawdown", "overnight_realized_today"):
        old["account"].pop(k, None)
    old["account"]["daily_drawdown"] = 250.0
    s = Session(browser, f"{label}-old", width, height, old)
    try:
        s.show(old)
        absent(s, "[data-testid=overnight-holds]", "overnight section")
        for want, lab in ((LOSS_LINE_OLD, "loss line"), (QUICK_OLD, "closing line"), (SMALL_OLD, "small bets"), (EXIT_OLD, "exit plan")):
            contains(s, "[data-testid=risk-telemetry]", want, f"old {lab} byte identical")
        body = s.text("[data-testid=risk-telemetry]")
        check("overnight" not in body.lower(), f"[{s.label}] Safety card never says overnight on an old backend")
        contains(s, "[data-testid=risk-telemetry]", "Daily loss limit used $250.00 of $1,500.00", "old meter reads daily_drawdown")
        absent(s, "[data-testid=safety-overnight-result]", "overnight result line")
        contains(s, "main", "Quick trades close in", "old countdown label")
        broken = copy.deepcopy(old)
        broken["account"]["is_circuit_broken"] = True
        s.show(broken)
        contains(s, "main", STOPPED_OLD, "old stopped banner")
        check(STOPPED_NEW not in s.text("main"), f"[{s.label}] old backend never says 'Day trading stopped'")
        equals(s, "[data-testid=right-now-sentence]", "Stopped for today. It hit the $1,500 loss limit.", "old Right now")
        # a hold on an old payload (strategy id only, no overnight flag) is still kept out of Quick trades
        ev = frame("evening")
        ev.pop("overnight", None)
        for pos in ev["all_positions"]:
            pos.pop("overnight", None)
        s.show(ev)
        common(s, "old+holds", ["NVDA", "IREN", "HUT"], [])
        equals(s, "[data-testid=overnight-hold-NVDA] [data-testid=overnight-hold-line]", HOLD_THU["NVDA"], "old payload NVDA line from exit_due")
    finally:
        s.close()


def negative_controls(browser, width: int, height: int) -> None:
    print("\n=== negative controls (every detector must catch an injected fault)")
    ev = frame("evening")
    s = Session(browser, "negative", width, height, ev)
    try:
        s.show(ev)
        p = s.page
        # a hold without its id or flag lands in Quick trades as "Manual trade": the row and label checks see it
        bad = copy.deepcopy(ev)
        bad.pop("overnight", None)
        for pos in bad["all_positions"]:
            pos["overnight"], pos["strategy_id"] = False, "manual"
        s.show(bad)
        rows = p.locator("[data-testid^=holding-row-]").count()
        check(rows == 3 and hold_symbols(s) == [], f"[negative] an unflagged hold shows in Quick trades and is detected ({rows} rows)")
        check("Manual trade" in s.text("main"), "[negative] the 'Manual trade' detector matches")
        check("Sell now" in s.text("main"), "[negative] the 'Sell now' detector matches")
        s.show(ev)
        p.evaluate("() => { const b = document.querySelector('[data-testid=btn-no-buy-tonight]'); b.style.minHeight = '0'; b.style.height = '20px'; }")
        check(p.locator("[data-testid=btn-no-buy-tonight]").bounding_box()["height"] < 44, "[negative] a 20 px control is detected")
        p.evaluate("() => { const d = document.createElement('div'); d.style.cssText = 'width:2400px;height:10px'; document.querySelector('[data-testid=overnight-holds]').appendChild(d); }")
        check(bool(p.evaluate(JS_OVERFLOW)), "[negative] a too wide element is detected")
        check(bool(PUNCT.findall("Sells at the open; no stop")) and bool(PUNCT.findall("Note: sells")) and bool(PUNCT.findall("sells \u2014 open")),
              "[negative] semicolon, colon and dash detectors match")
        check(not PUNCT.findall("Sells at the 9:30 AM open on Thu Oct 1."), "[negative] a clock time is not a colon")
        s.show(frame("morning_loss"))
        check("$1,723.50 of" not in s.text("[data-testid=risk-telemetry]"), "[negative] the meter check would see the account drawdown")
    finally:
        s.close()


def run_screenshots(browser, label: str, width: int, height: int) -> None:
    """A fresh page opened in the evening, so the header clock and every line belong to that state. The fixture
    was built with no price feeds attached; for the picture only, the feeds read connected (else a 'price feed
    is down' banner that has nothing to do with the holds sits on top)."""
    print(f"\n=== {label}/screenshot")
    ev = frame("evening")
    ev["ingestion"] = {k: "connected" for k in (ev.get("ingestion") or {})}
    s = Session(browser, label, width, height, ev)
    try:
        s.page.wait_for_timeout(800)
        common(s, "screenshot", ["NVDA", "IREN", "HUT"], [])
        if label == "desktop":   # the phone header hides the clock
            contains(s, "header", "Wed, Sep 30", "screenshot header date")
            contains(s, "header", "8:00 PM", "screenshot header time")
        absent(s, "[data-testid=overnight-no-buy-reply]", "screenshot reply line")
        shot(s, "evening_three_holds")
    finally:
        s.close()


def main() -> int:
    if not (ROOT / "frontend/out/index.html").exists():
        print("frontend/out is missing: run `npm run build` in frontend/ first")
        return 2
    proc = base.start_export_server()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for label, w, h in (("desktop", 1440, 900), ("phone", 390, 844)):
                s = Session(browser, label, w, h, frame("day"))
                try:
                    run_matrix(s)
                finally:
                    s.close()
                run_old_backend(browser, label, w, h)
                run_screenshots(browser, label, w, h)
            negative_controls(browser, 390, 844)
            browser.close()
    finally:
        base.stop_export_server(proc)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print("FAIL:", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
