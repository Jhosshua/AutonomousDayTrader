#!/usr/bin/env python3
"""scripts/verify_bright_dashboard.py

QA for PLAN_2026_10_04_bright_operator_dashboard.md (section 11 wins): the bright operator dashboard.

Serves the NEW static export (frontend/out, `npx next build` first) on port 3025 and, for the one check that
compares with the old page, the OLD export (`--old-out`) on 3026. No backend: websocket and REST are Playwright
route mocks, the clock is fixed at Tue 2026-09-29 11:56 ET (same helpers as verify_compact_dashboard.py).

Checks
  attention   the status strip's pill and list, one generated frame per attention source (the live frame with
              exactly one condition switched on): count 1 and the label is in the list. Calm frames (idle, live,
              busy, weekend, an overnight hold with no stop) say "No alarms". A stalled stream (frames stop, the
              clock moves 40 s) leaves "No alarms". An orphan that also raises an ORB error counts once. Alarm
              frames with several sources count them all.
  results     By day totals equal the mocked sessions (including a day whose total differs from its trades by 3
              cents), By playbook totals equal the mocked trades plus the recovered aggregates, toggle with click,
              Enter and Space, Show older days, Show all N, open group survives 5 pushed frames and a ledger
              refetch, trade row opens the native dialog with its six fields, Escape closes it and focus returns
              to the row, aggregate-only day text, empty-today text, a ledger 500 keeps the old rows and shows the
              muted line (not red) and recovers, the 2,000 trade note when the drain hits its page cap.
  motion      after the page settles, 5 pushed frames start no animation; no finite animation is running; under
              prefers-reduced-motion nothing runs.
  layout      no horizontal overflow, every button and summary >= 44 px (rows and details open), no console or
              page errors, never reloaded, strip <= 64 px on desktop, nothing red on the idle page except money,
              Bricolage Grotesque in use and no serif, DOM order = phone order, Slow trades mode unchanged (no
              Safety, no Results), the top of "Holding now" (or of the playbook table) is no lower than on the old
              page for the same frame and viewport.
  negative    each new check is run on data that must make it fail.
Screenshots: docs/bright_dashboard/screenshots/.

    (cd frontend && npx next build)
    python3 scripts/verify_bright_dashboard.py --old-out /path/to/old/frontend/out [--only attention,results,...]
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import verify_compact_dashboard as vc  # noqa: E402  (mock helpers, probes, check())
import verify_ui_redesign as base  # noqa: E402

SHOTS = ROOT / "docs/bright_dashboard/screenshots"
check = vc.check

TODAY = vc.TODAY
OVN_FIX = ROOT / "docs/overnight_holds/fixtures"
LOSS_RED = "rgb(194, 48, 15)"


# ---------------------------------------------------------------------------------------------------
# Money and label formatting, as the page does it
# ---------------------------------------------------------------------------------------------------
def money(x: float) -> str:
    return f"{'-' if x < 0 else ''}${abs(x):,.2f}"


def signed(x: float) -> str:
    return f"{'-' if x < 0 else '+'}${abs(x):,.2f}"


def total_text(x: float) -> str:
    return money(0) if x == 0 else signed(x)


# ---------------------------------------------------------------------------------------------------
# Ledger mock with switches (failure, endless pages) and a request counter
# ---------------------------------------------------------------------------------------------------
class Ledger:
    def __init__(self, today_trades: List[Dict[str, Any]], sessions: Optional[List[Dict[str, Any]]] = None):
        self.today_trades = today_trades
        self.sessions = sessions if sessions is not None else vc.history_sessions()
        self.fail_all = False
        self.fail_today = False
        self.endless = False
        self.requests: Counter = Counter()

    def all_body(self) -> Dict[str, Any]:
        return vc.ledger_all(self.today_trades, self.sessions)

    def handle(self, route) -> None:
        url = route.request.url
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        rng = qs.get("range", ["today"])[0]
        self.requests[rng] += 1
        if (rng == "all" and self.fail_all) or (rng == "today" and self.fail_today):
            route.fulfill(status=500, content_type="application/json", body='{"detail":"boom"}')
            return
        if rng == "all":
            body = self.all_body()
            if self.endless:
                body["next_cursor"] = f"c{self.requests['all']}"
        elif rng == "today":
            body = base.trades_response(self.today_trades)
        else:
            body = base.trades_response(self.today_trades)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


class BS:
    """One bright-dashboard page: fresh context, mocked ws and REST, fixed clock, error and navigation counters."""

    def __init__(self, browser, port: int, label: str, viewport: str, frame: Dict[str, Any], ledger: Ledger,
                 reduced: bool = False, record_animations: bool = False,
                 ready: str = "[data-testid=status-strip]", settle_ms: int = 1300, pro: bool = False):
        w, h = vc.VIEWPORTS[viewport]
        self.label = f"{label}/{viewport}"
        self.frame = copy.deepcopy(frame)
        self.ledger = ledger
        self.ctx = browser.new_context(viewport={"width": w, "height": h}, reduced_motion="reduce" if reduced else "no-preference")
        if pro:
            self.ctx.add_init_script("try { localStorage.setItem('daytrader.showPro', '1'); } catch (e) {}")
        if record_animations:
            self.ctx.add_init_script(
                "window.__starts = []; document.addEventListener('animationstart', e => window.__starts.push(e.animationName), true);")
        self.page = self.ctx.new_page()
        self.page.clock.set_fixed_time(vc.FIXED_NOW)
        self.ws: Dict[str, Any] = {}
        self.ws_opens = [0]
        self.navs = [0]
        self.errors: List[str] = []
        p = self.page
        p.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        # a mocked 500 on /api/trades is the point of some checks; the browser logs it as a console error, that is not a page bug
        p.on("console", lambda m: self.errors.append(f"console: {m.text}") if m.type == "error" and "status of 500" not in m.text else None)
        p.on("framenavigated", lambda f: self.navs.__setitem__(0, self.navs[0] + 1) if f == p.main_frame else None)

        def on_ws(ws) -> None:
            self.ws_opens[0] += 1
            self.ws["route"] = ws
            ws.send(json.dumps(self.frame))

        p.route_web_socket(re.compile(r".*/ws/ui$"), on_ws)
        p.route(re.compile(r".*/api/trades.*"), ledger.handle)
        p.route(re.compile(r".*/health$"), lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(base.HEALTH_RESPONSE)))
        p.route(re.compile(r".*/api/account$"), lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(self.frame["account"])))
        p.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=20000)
        p.wait_for_selector(ready, timeout=15000)
        p.evaluate("() => document.fonts.ready")
        p.wait_for_timeout(settle_ms)
        self.loads = self.navs[0]

    def push(self, frame: Optional[Dict[str, Any]] = None, wait: int = 400) -> None:
        if frame is not None:
            self.frame = copy.deepcopy(frame)
        self.ws["route"].send(json.dumps(self.frame))
        self.page.wait_for_timeout(wait)

    def height(self) -> int:
        return int(self.page.evaluate("document.documentElement.scrollHeight"))

    def text(self, sel: str) -> str:
        loc = self.page.locator(sel)
        return " ".join(loc.first.inner_text().split()) if loc.count() else ""

    def close(self) -> None:
        self.ctx.close()


# ---------------------------------------------------------------------------------------------------
# Frames: the live frame with exactly one attention source on
# ---------------------------------------------------------------------------------------------------
def load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


LIVE = load(vc.COMPACT_FIX / "live.json")
LIVE_ONE = load(vc.COMPACT_FIX / "live_one.json")
IDLE = load(vc.MOOD_FIX / "idle.json")
BUSY = load(vc.MOOD_FIX / "busy.json")
WEEKEND = load(vc.MOOD_FIX / "weekend.json")
UNLINKED = load(vc.MOOD_FIX / "pre_release_and_unlinked.json")
ALARMS = load(vc.COMPACT_FIX / "alarms.json")
IREN_POSITION = next(p for p in load(OVN_FIX / "morning_unsold.json")["all_positions"] if p["symbol"] == "IREN")


def with_overnight(frame: Dict[str, Any], needs_look: Optional[List[str]] = None, init_error: Optional[str] = None,
                   unsold: Optional[List[str]] = None) -> Dict[str, Any]:
    """The live frame holding one overnight stock (IREN, no stop on purpose: holds never have one)."""
    f = copy.deepcopy(frame)
    pos = copy.deepcopy(IREN_POSITION)
    pos["exit_due"] = "2026-09-30T09:30:00-04:00"
    f["all_positions"] = list(f["all_positions"]) + [pos]
    f["positions_count"] = len(f["all_positions"])
    f["overnight"] = {
        "state": {"mode": "live", "running": True, "init_error": init_error, "realized_today": 0.0, "unsold_after_0931": unsold or []},
        "settings": {"mode": "live", "enabled": ["NVDA", "IREN", "HUT"], "pct": 0.2, "cap": 25000, "room_multiple": 2},
        "rows": [], "holds": [{"symbol": "IREN", "strategy_id": "overnight_iren", "shares": 238, "buy_avg": 41.73,
                               "buy_date": TODAY, "sale_date": "2026-09-30", "nights": "weeknight", "state": "HELD",
                               "needs_look": needs_look or []}],
        "skips": [], "intents": [], "queued_sales": [], "no_buy_tonight": False, "no_buy_until": "2026-09-29T15:49:30-04:00",
        "unsold_after_0931": unsold or [],
    }
    return f


def mutate(frame: Dict[str, Any], fn: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    f = copy.deepcopy(frame)
    fn(f)
    return f


def strat(f: Dict[str, Any], sid: str) -> Dict[str, Any]:
    return next(s for s in f["strategies"] if s["id"] == sid)


def pos_without_stop(f: Dict[str, Any]) -> None:
    pos = copy.deepcopy(LIVE_ONE["all_positions"][0])
    pos["stop_loss"] = None
    f["all_positions"] = [pos]
    f["positions_count"] = 1


def pos_unlinked(f: Dict[str, Any]) -> None:
    pos = copy.deepcopy(LIVE_ONE["all_positions"][0])
    pos["strategy_id"] = None
    pos["tranches"] = None
    f["all_positions"] = [pos]
    f["positions_count"] = 1


def orb_orphan(f: Dict[str, Any]) -> None:
    orb = strat(f, "orb")["orb"]
    orb["orphans"] = [{"symbol": "RKLB", "text": "ORB holds 40 RKLB shares that no ORB order explains."}]
    orb["alerts"] = ["ORB holds 40 RKLB shares that no ORB order explains."]


def orb_orphan_with_error(f: Dict[str, Any]) -> None:
    orb_orphan(f)
    strat(f, "orb")["orb"]["errors"] = [{"alarm": "orb_alert", "symbol": "RKLB"}, {"alarm": "orphan", "symbol": "RKLB"}]


# key -> (frame, label fragment that must be in the strip's list)
SOURCES: Dict[str, Any] = {
    "feed down": (mutate(LIVE, lambda f: f.update(ingestion={"stock": "disconnected", "news": "disconnected", "vix": "disconnected"})), "price feed down"),
    "feed list empty": (mutate(LIVE, lambda f: f.update(ingestion={})), "price feed down"),
    "broker mismatch": (mutate(LIVE, lambda f: f["broker"].update(mismatch=True)), "do not match Alpaca"),
    "saving problem": (mutate(LIVE, lambda f: f["persistence"].update(status="recovery_halt")), "saving problem"),
    "daily loss limit": (mutate(LIVE, lambda f: f["account"].update(is_circuit_broken=True)), "daily loss limit hit"),
    "ORB init error": (mutate(LIVE, lambda f: strat(f, "orb")["orb"].update(init_error="could not start")), "could not start"),
    "ORB reported a problem": (mutate(LIVE, lambda f: strat(f, "orb")["orb"].update(errors=[{"alarm": "orb_breaker", "symbol": "AAA"}])), "reported a problem"),
    "ORB alert": (mutate(LIVE, lambda f: strat(f, "orb")["orb"].update(alerts=["ORB saw something odd."])), "ORB alert"),
    "ORB orphan": (mutate(LIVE, orb_orphan), "RKLB left open"),
    "tri-engine error": (mutate(LIVE, lambda f: strat(f, "tsla_asymmetric_dual")["tri_engine"].update(last_error="broker said no")), "broker issue on Tesla Morning Plan"),
    "overnight needs a look": (with_overnight(LIVE, needs_look=["BOOK_MORE_THAN_ALPACA"]), "IREN overnight hold needs a look"),
    "overnight init error": (with_overnight(LIVE, init_error="no key"), "overnight holds could not start"),
    "overnight unsold": (with_overnight(LIVE, unsold=["IREN"]), "IREN not sold yet"),
    "no safety exit": (mutate(LIVE_ONE, pos_without_stop), "has no safety exit"),
    "not linked": (mutate(LIVE_ONE, pos_unlinked), "is not linked to a strategy"),
}
CALM = {
    "idle": (IDLE, []), "live": (LIVE, vc.LIVE_TRADES), "busy": (BUSY, vc.BUSY_TRADES), "weekend": (WEEKEND, []),
    "live_one": (LIVE_ONE, vc.LIVE_TRADES), "overnight hold, no stop": (with_overnight(LIVE), vc.LIVE_TRADES),
    # the mood card says "SPY and QQQ can't be read, so new trades wait" here: the robot working as designed, not an alarm
    "market direction unknown": (load(vc.MOOD_FIX / "mood_normal_unknown.json"), []),
}


def pill(s: BS) -> Dict[str, Any]:
    return s.page.evaluate("""() => { const p = document.querySelector('[data-testid=attention-pill]');
      const l = document.querySelector('[data-testid=attention-list]');
      return { text: p ? p.textContent.trim() : null, count: p ? p.getAttribute('data-count') : null,
               list: l ? l.textContent.replace(/\\s+/g, ' ').trim() : null,
               sentence: (document.querySelector('[data-testid=right-now-sentence]') || {}).textContent || null }; }""")


def expect_calm(got: Dict[str, Any]) -> bool:
    return got["text"] == "No alarms" and got["count"] == "0" and got["list"] is None and bool(got["sentence"])


def expect_one(got: Dict[str, Any], fragment: str) -> bool:
    return (got["text"] == "1 needs a look" and got["count"] == "1" and got["list"] is not None
            and fragment in got["list"] and bool(got["sentence"]))


# ---------------------------------------------------------------------------------------------------
# attention
# ---------------------------------------------------------------------------------------------------
def run_attention(browser, port: int) -> None:
    print("\n== attention ==")
    # calm frames
    for name, (frame, trades) in CALM.items():
        s = BS(browser, port, f"calm/{name}", "desktop", frame, Ledger(trades))
        got = pill(s)
        check(expect_calm(got), f"[calm/{name}] pill says 'No alarms', count 0, no list ({got['text']!r}, {got['count']!r}, {got['list']!r})")
        if name == "market direction unknown":
            check("SPY and QQQ can't be read" in s.page.inner_text("main"),
                  "[calm/unknown trend] the \"SPY and QQQ can't be read, so new trades wait\" text is on the page and the pill still says 'No alarms'")
        check(not s.errors, f"[calm/{name}] no console or page errors {s.errors[:2]}")
        s.close()

    # one source at a time
    for name, (frame, fragment) in SOURCES.items():
        s = BS(browser, port, f"one/{name}", "desktop", frame, Ledger(vc.LIVE_TRADES))
        got = pill(s)
        check(expect_one(got, fragment), f"[one/{name}] count 1 and the list says {fragment!r} ({got['text']!r}, {got['list']!r})")
        s.close()

    # a connection that is not live (socket closed and never comes back)
    s = BS(browser, port, "one/connection lost", "desktop", LIVE, Ledger(vc.LIVE_TRADES))
    # every later connection attempt fails at once (a stub WebSocket that errors), then the open socket is closed
    s.page.evaluate("""() => { window.WebSocket = function () { const o = { readyState: 3, close() {}, send() {} };
      setTimeout(() => { o.onerror && o.onerror(new Event('error')); o.onclose && o.onclose(new Event('close')); }, 50); return o; };
      window.WebSocket.OPEN = 1; }""")
    s.ws["route"].close()
    s.page.wait_for_timeout(5600)
    got = pill(s)
    check(expect_one(got, "not connected to the robot"), f"[one/connection lost] socket closed: count 1 and the list says so ({got['text']!r}, {got['list']!r})")
    s.close()

    # stall: frames stop while the clock moves 40 s (the stream hook marks it stale after 30 s)
    s = BS(browser, port, "one/stall", "desktop", LIVE, Ledger(vc.LIVE_TRADES))
    check(expect_calm(pill(s)), "[stall] calm before the stall")
    s.page.clock.set_fixed_time(vc.FIXED_NOW + timedelta(seconds=40))
    s.page.wait_for_timeout(5600)
    got = pill(s)
    check(got["text"] != "No alarms" and got["list"] is not None and "not connected to the robot" in got["list"],
          f"[stall] 40 s without a frame leaves 'No alarms' ({got['text']!r}, {got['list']!r})")
    s.close()

    # today's ledger fails to load: the strip's "Trades today" would be a silent 0
    led = Ledger(vc.LIVE_TRADES)
    led.fail_today = True
    s = BS(browser, port, "one/ledger", "desktop", LIVE, led)
    got = pill(s)
    check(expect_one(got, "today's trades did not load"), f"[one/ledger] today's ledger 500: count 1 and the list says so ({got['text']!r}, {got['list']!r})")
    s.close()

    # de-dup: an orphan that also raises an ORB alert and an ORB error counts once
    s = BS(browser, port, "dedup", "desktop", mutate(LIVE, orb_orphan_with_error), Ledger(vc.LIVE_TRADES))
    got = pill(s)
    check(got["count"] == "1", f"[dedup] an orphan that also raises an ORB alert and error counts once ({got['count']!r}, {got['list']!r})")
    s.close()

    # several at once: the alarms frame (7 sources) counts all of them, none hidden
    s = BS(browser, port, "alarms", "desktop", ALARMS, Ledger(vc.LIVE_TRADES))
    got = pill(s)
    n = int(got["count"] or 0)
    labels = [x for x in (got["list"] or "").replace("Needs a look:", "").rstrip(".").split(", ") if x]
    check(n >= 6 and n == len(labels) and got["text"] == f"{n} need a look",
          f"[alarms] the alarms frame counts every source ({got['text']!r}, {len(labels)} labels listed)")
    s.close()

    # slow trades mode: the words are in the strip itself, no switching back
    s = BS(browser, port, "slow-mode", "desktop", SOURCES["feed down"][0], Ledger(vc.LIVE_TRADES))
    s.page.locator("[data-testid=mode-tab-swing]").click()
    s.page.wait_for_timeout(400)
    got = pill(s)
    check(expect_one(got, "price feed down"), f"[slow-mode] the strip still says what needs a look in Slow trades mode ({got['text']!r})")
    s.close()

    # negative controls: the expectations above fail on the wrong data
    s = BS(browser, port, "neg/attention", "desktop", SOURCES["feed down"][0], Ledger(vc.LIVE_TRADES))
    got = pill(s)
    check(not expect_calm(got), "[negative] 'No alarms' expectation fails on an alarm frame")
    check(not expect_one(got, "saving problem"), "[negative] a wrong label expectation fails")
    s.page.evaluate("() => document.querySelector('[data-testid=attention-pill]').setAttribute('data-count', '0')")
    check(not expect_one(pill(s), "price feed down"), "[negative] a tampered pill count is caught")
    s.close()
    s = BS(browser, port, "neg/attention2", "desktop", LIVE, Ledger(vc.LIVE_TRADES))
    check(not expect_one(pill(s), "price feed down"), "[negative] a one-alarm expectation fails on a calm frame")
    s.close()


# ---------------------------------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------------------------------
JS_GROUPS = """() => [...document.querySelectorAll('[data-testid=results-group]')].map(g => ({
  key: g.getAttribute('data-key'), open: g.querySelector('button[aria-expanded]').getAttribute('aria-expanded') === 'true',
  total: g.querySelector('[data-testid=results-total]').textContent.trim(),
  trades: g.querySelectorAll('[data-testid=results-trade]').length }))"""


def groups(s: BS) -> List[Dict[str, Any]]:
    return s.page.evaluate(JS_GROUPS)


def group_btn(s: BS, key: str):
    return s.page.locator(f"[data-testid=results-group][data-key='{key}'] button[aria-expanded]").first


def run_results(browser, port: int) -> None:
    print("\n== results ==")
    led = Ledger(vc.BUSY_TRADES)
    body = led.all_body()
    sessions = body["sessions"]
    s = BS(browser, port, "results", "desktop", BUSY, led, settle_ms=2200)
    p = s.page

    # By day: today + newest 5 days with trades, totals = the mocked sessions
    g = groups(s)
    keys = [x["key"] for x in g]
    want = [x["session_date"] for x in sessions if x["trades_count"] > 0][:6]
    check(keys == want, f"[by-day] today plus the newest 5 days, newest first ({keys} == {want})")
    by_key = {x["session_date"]: x for x in sessions}
    bad = [(x["key"], x["total"], total_text(by_key[x["key"]]["realized_pnl"])) for x in g if x["total"] != total_text(by_key[x["key"]]["realized_pnl"])]
    check(not bad, f"[by-day] every visible day total equals its mocked session total {bad}")
    check(g[0]["open"] and not any(x["open"] for x in g[1:]), "[by-day] today is the only open group (it has trades)")
    summary = s.text("[data-testid=results-summary]")
    items = body["items"]
    wins, losses = sum(1 for t in items if t["realized_pnl"] > 0), sum(1 for t in items if t["realized_pnl"] < 0)
    check(summary.startswith(f"{len(items)} finished trades · {wins} won, {losses} lost"), f"[summary] counts come from the loaded trades ({summary!r})")
    more = body["summary"]["trades_count"] - len(items)
    check(f"{more} more on daily-total-only days" in summary, f"[summary] trades on daily-total-only days are said, not added ({summary!r})")

    # a day whose total differs from the sum of its trades by a few cents shows the SESSION total
    day1 = sessions[1]["session_date"]  # newest earlier day (index 0 is today)
    btn = group_btn(s, day1)
    btn.click()
    p.wait_for_timeout(300)
    rows = p.locator(f"[data-testid=results-group][data-key='{day1}'] [data-testid=results-trade]")
    row_sum = round(sum(float(re.sub(r"[+$,]", "", t.replace("-$", "-").replace("+$", "+"))) for t in
                        [r.locator("div").last.inner_text().strip() for r in rows.all()]), 2)
    shown = groups(s)[1]["total"]
    check(shown == total_text(by_key[day1]["realized_pnl"]) and abs(row_sum - by_key[day1]["realized_pnl"]) > 0.001,
          f"[by-day] the day total is the ledger's number ({shown}), not the sum of the rows ({row_sum})")

    # toggle: click, Enter, Space
    btn.click(); p.wait_for_timeout(250)
    check(btn.get_attribute("aria-expanded") == "false", "[toggle] click closes an open group")
    btn.focus(); p.keyboard.press("Enter"); p.wait_for_timeout(250)
    check(btn.get_attribute("aria-expanded") == "true", "[toggle] Enter opens it")
    p.keyboard.press("Space"); p.wait_for_timeout(250)
    check(btn.get_attribute("aria-expanded") == "false", "[toggle] Space closes it")
    btn.click(); p.wait_for_timeout(250)

    # open group survives 5 pushed frames and a ledger refetch
    before = led.requests["all"]
    for i in range(5):
        fr = copy.deepcopy(BUSY)
        if i >= 3:
            fr["ledger_revision"] = BUSY.get("ledger_revision", 0) + 1
        s.push(fr, 350)
    p.wait_for_timeout(500)
    g2 = groups(s)
    check(group_btn(s, day1).get_attribute("aria-expanded") == "true" and [x["key"] for x in g2] == keys,
          "[persist] the open group stays open across 5 pushed frames and a ledger refetch")
    check(led.requests["all"] > before, f"[persist] the ledger really refetched ({before} -> {led.requests['all']} range=all requests)")

    # Show all N (today has 13 trades, the group shows 6)
    today_rows = p.locator(f"[data-testid=results-group][data-key='{TODAY}'] [data-testid=results-trade]").count()
    n_today = len(vc.BUSY_TRADES)
    check(today_rows == 6, f"[show-all] an open group shows its newest 6 trades ({today_rows})")
    show_all = p.get_by_role("button", name=f"Show all {n_today}")
    check(show_all.count() == 1 and show_all.first.bounding_box()["height"] >= 43.5, f"[show-all] a 'Show all {n_today}' button (>= 44 px) offers the rest")
    show_all.first.click(); p.wait_for_timeout(250)
    check(p.locator(f"[data-testid=results-group][data-key='{TODAY}'] [data-testid=results-trade]").count() == n_today, "[show-all] it reveals every trade of the day")

    # Show older days
    older = p.get_by_role("button", name="Show older days")
    check(older.count() == 1 and older.first.bounding_box()["height"] >= 43.5, "[older] a 'Show older days' button (>= 44 px) is there")
    older.first.click(); p.wait_for_timeout(300)
    all_keys = [x["key"] for x in groups(s)]
    check(all_keys == [x["session_date"] for x in sessions if x["trades_count"] > 0], f"[older] it reveals every day with trades ({len(all_keys)} groups)")
    check(p.get_by_role("button", name="Show older days").count() == 0, "[older] the button goes away")

    # aggregate-only day: the oldest recovered day
    agg = [x for x in sessions if x["aggregate_only"]][-1]["session_date"]
    group_btn(s, agg).scroll_into_view_if_needed()
    group_btn(s, agg).click(); p.wait_for_timeout(300)
    note = p.get_by_text("The daily total was recovered. Individual trade details are unavailable.")
    check(note.count() == 1 and note.first.is_visible(), "[aggregate] a daily-total-only day says its trades are unavailable")
    sub = s.text(f"[data-testid=results-group][data-key='{agg}'] button[aria-expanded]")
    check("daily total only" in sub, f"[aggregate] its row says 'daily total only' ({sub!r})")
    # a day with a count but no loaded rows (not aggregate)
    mid = next(x["session_date"] for x in sessions[3:] if not x["aggregate_only"] and x["trades_count"] > 0)
    group_btn(s, mid).click(); p.wait_for_timeout(300)
    check(p.get_by_text("Older trades from this day are not loaded.").count() >= 1, "[not-loaded] a day whose rows are not loaded says so")

    # trade row -> dialog with six fields, Escape closes, focus returns to the row
    p.evaluate("window.scrollTo(0, 0)")
    first_trade = p.locator(f"[data-testid=results-group][data-key='{TODAY}'] [data-testid=results-trade]").nth(1)
    tid = first_trade.get_attribute("data-trade-id")
    trade = next(t for t in items if t["trade_id"] == tid)
    first_trade.scroll_into_view_if_needed()
    first_trade.click(); p.wait_for_timeout(300)
    dlg = p.locator("dialog[data-testid=trade-detail]")
    check(dlg.count() == 1 and dlg.first.is_visible() and dlg.first.evaluate("d => d.open"), "[dialog] a trade row opens the native dialog")
    labels = [l.strip() for l in dlg.first.locator("span.text-xs").all_inner_texts()]
    side_labels = ["Sold at", "Bought back at"] if trade["side"] == "SHORT" else ["Bought at", "Sold at"]
    check(labels[-6:] == side_labels + ["Shares", "Result", "Playbook", "Why it exited"], f"[dialog] six fields with the right labels ({labels})")
    vals = [v.strip() for v in dlg.first.locator("span.font-semibold").all_inner_texts()]
    check(money(trade["avg_entry_price"]) in vals and str(trade["quantity"]) in vals and signed(trade["realized_pnl"]) in vals,
          f"[dialog] the values are this trade's ({vals})")
    check(dlg.first.locator("button[aria-label='Close trade details']").bounding_box()["height"] >= 43.5, "[dialog] the close button is >= 44 px")
    p.keyboard.press("Escape"); p.wait_for_timeout(300)
    check(dlg.count() == 0 or not dlg.first.evaluate("d => d.open"), "[dialog] Escape closes it")
    focused = p.evaluate("() => (document.activeElement || {}).getAttribute && document.activeElement.getAttribute('data-trade-id')")
    check(focused == tid, f"[dialog] focus returns to the trade row ({focused!r} == {tid!r})")

    # By playbook: totals = mocked trades + recovered aggregates, best first
    p.get_by_test_id("results-tab-playbook").click(); p.wait_for_timeout(500)
    exp: Dict[str, float] = {}
    for t in items:
        exp[t["strategy_id"]] = round(exp.get(t["strategy_id"], 0) + t["realized_pnl"], 2)
    for sess in body["recovered_sessions"]:
        for sid, a in sess.get("strategies", {}).items():
            exp[sid] = round(exp.get(sid, 0) + a["realized_pnl"], 2)
    g = groups(s)
    got = {x["key"]: x["total"] for x in g}
    bad = {k: (got.get(k), total_text(v)) for k, v in exp.items() if got.get(k) != total_text(v)}
    check(not bad and set(got) == set(exp), f"[by-playbook] every playbook total equals trades plus recovered aggregates {bad}")
    order = [exp[x["key"]] for x in g]
    check(order == sorted(order, reverse=True), "[by-playbook] best to worst")
    check(g[0]["open"] and not any(x["open"] for x in g[1:]), "[by-playbook] switching resets to that grouping's default (first group open)")
    check(p.get_by_test_id("results-tab-playbook").get_attribute("aria-pressed") == "true"
          and p.get_by_test_id("results-tab-day").get_attribute("aria-pressed") == "false", "[by-playbook] aria-pressed follows the choice")
    p.get_by_test_id("results-tab-day").click(); p.wait_for_timeout(400)
    check([x["key"] for x in groups(s)] == keys, "[by-day] switching back resets to today plus the newest 5 days")

    # >= 44 px for every row and button in the panel
    small = p.evaluate("""() => [...document.querySelectorAll('[data-testid=results-panel] button')]
      .filter(b => b.getClientRects().length && b.getBoundingClientRect().height < 43.5).map(b => b.textContent.trim().slice(0, 30))""")
    check(not small, f"[targets] every Results button and row is >= 44 px {small}")

    # ledger 500: old rows stay, a muted line says so (never red), it recovers
    n_groups = len(groups(s))
    led.fail_all = True
    fr = copy.deepcopy(BUSY); fr["ledger_revision"] = BUSY.get("ledger_revision", 0) + 7
    s.push(fr, 900)
    err = p.get_by_test_id("results-error")
    check(err.count() == 1 and err.first.inner_text().strip() == "Couldn't refresh results. Showing the last ones loaded.",
          "[error] a ledger 500 shows the muted line")
    check(len(groups(s)) == n_groups, f"[error] the rows loaded before are still shown ({len(groups(s))} groups)")
    colour = err.first.evaluate("e => getComputedStyle(e).color")
    check(colour != LOSS_RED and "rgb(194" not in colour, f"[error] the line is muted, not red ({colour})")
    led.fail_all = False
    p.wait_for_timeout(5600)
    check(p.get_by_test_id("results-error").count() == 0, "[error] it recovers on the next retry (5 s) and the line goes away")
    s.close()

    # empty today: its group opens with 'No finished trades today.'; first load shows 'Loading results…'
    s = BS(browser, port, "results-empty-today", "desktop", LIVE, Ledger([]), settle_ms=1500)
    g = groups(s)
    check(g and g[0]["key"] == TODAY, "[empty-today] today is listed even with no trades")
    group_btn(s, TODAY).click(); s.page.wait_for_timeout(300)
    check(s.page.get_by_text("No finished trades today.", exact=True).count() == 1, "[empty-today] opened, it says 'No finished trades today.'")
    s.close()
    # nothing at all
    s = BS(browser, port, "results-nothing", "desktop", IDLE, Ledger([], sessions=[]), settle_ms=1500)
    led2 = s.ledger
    check(s.page.get_by_text("No finished trades yet.", exact=True).count() == 1, "[empty] an empty ledger says 'No finished trades yet.'")
    s.close()

    # truncated: the drain hits its 20 page cap
    led = Ledger(vc.LIVE_TRADES)
    led.endless = True
    s = BS(browser, port, "results-truncated", "desktop", LIVE, led, settle_ms=2500)
    # a drain is 20 pages; the first connect also asks for a refetch that supersedes the first drain after 1-2 pages
    check(20 <= led.requests["all"] <= 23, f"[truncated] the drain stops at its 20 page cap, it never loops ({led.requests['all']} range=all requests)")
    t = s.page.get_by_test_id("results-truncated")
    check(t.count() == 1 and t.first.inner_text().strip() == "Playbook totals cover the most recent 2,000 trades. Day totals are complete.",
          "[truncated] the muted note is shown")
    s.close()

    # negative controls
    s = BS(browser, port, "neg/results", "desktop", BUSY, Ledger(vc.BUSY_TRADES), settle_ms=1800)
    g = groups(s)
    s.page.evaluate("() => { const t = document.querySelector('[data-testid=results-total]'); t.textContent = '+$999.99'; }")
    g2 = groups(s)
    mocked = {x["session_date"]: x for x in s.ledger.all_body()["sessions"]}
    check(any(x["total"] != total_text(mocked[x["key"]]["realized_pnl"]) for x in g2), "[negative] a wrong day total is caught by the totals check")
    s.page.evaluate("() => { document.querySelectorAll('[data-testid=results-group]')[1].remove(); }")
    check([x["key"] for x in groups(s)] != [x["session_date"] for x in mocked.values() if x["trades_count"] > 0][:6], "[negative] a missing day is caught by the day-list check")
    s.page.evaluate("() => { const b = document.querySelector('[data-testid=results-group] button'); b.style.height = '30px'; b.style.minHeight = '0'; b.style.padding = '0'; }")
    small = s.page.evaluate("""() => [...document.querySelectorAll('[data-testid=results-panel] button')]
      .filter(b => b.getClientRects().length && b.getBoundingClientRect().height < 43.5).length""")
    check(small >= 1, "[negative] a short row is caught by the 44 px check")
    s.close()


# ---------------------------------------------------------------------------------------------------
# motion
# ---------------------------------------------------------------------------------------------------
JS_FINITE_RUNNING = """() => document.getAnimations().filter(a => a.playState === 'running' &&
  a.effect && a.effect.getComputedTiming().iterations !== Infinity).map(a => a.animationName || a.transitionProperty || '?')"""


def run_motion(browser, port: int) -> None:
    print("\n== motion ==")
    s = BS(browser, port, "motion", "desktop", BUSY, Ledger(vc.BUSY_TRADES), record_animations=True, settle_ms=4500)
    running = s.page.evaluate(JS_FINITE_RUNNING)
    check(not running, f"[motion] after the page settles no finite animation is running {running[:5]}")
    s.page.evaluate("() => { window.__starts = []; }")
    for i in range(5):
        fr = copy.deepcopy(BUSY)
        fr["account"]["equity"] = BUSY["account"]["equity"] + i * 3.17
        fr["timestamp"] = (datetime(2026, 9, 29, 15, 56, i, tzinfo=timezone.utc)).isoformat()
        s.push(fr, 450)
    starts = s.page.evaluate("window.__starts")
    check(starts == [], f"[motion] 5 pushed frames start no animation {starts[:5]}")
    running = s.page.evaluate(JS_FINITE_RUNNING)
    check(not running, f"[motion] and none is running afterwards {running[:5]}")
    # negative control: restarting a class animation is counted
    s.page.evaluate("""() => { const e = document.querySelector('[data-testid=results-panel]'); e.classList.remove('rise'); void e.offsetWidth; e.classList.add('rise'); }""")
    s.page.wait_for_timeout(800)  # the panel's rise has a 300 ms delay before animationstart fires
    check(len(s.page.evaluate("window.__starts")) >= 1, "[negative] a restarted animation is seen by the animationstart counter")
    s.close()

    for vp in vc.VIEWPORTS:
        s = BS(browser, port, "reduced", vp, BUSY, Ledger(vc.BUSY_TRADES), reduced=True, settle_ms=1200)
        running = s.page.evaluate("() => document.getAnimations().filter(a => a.playState === 'running').map(a => a.animationName || a.transitionProperty || '?')")
        check(not running, f"[reduced-motion/{vp}] nothing runs under prefers-reduced-motion {running[:5]}")
        s.close()


# ---------------------------------------------------------------------------------------------------
# layout, look, order
# ---------------------------------------------------------------------------------------------------
JS_REDDISH = """() => {
  const bad = [];
  const red = (c) => { const m = /^rgba?\\((\\d+), (\\d+), (\\d+)/.exec(c); return !!m && +m[1] > 150 && +m[2] < 90 && +m[3] < 90; };
  for (const el of document.querySelectorAll('main *')) {
    if (!el.getClientRects().length) continue;
    if (el.closest('[data-testid=results-panel], [data-testid=balance-card], [data-testid=strategy-pnl]')) continue;
    if (/^[-+]?\\$[\\d,]+\\.\\d\\d$/.test((el.textContent || '').trim())) continue;   // a money number may be loss red
    const cs = getComputedStyle(el);
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if ((own && red(cs.color)) || red(cs.backgroundColor) || (parseFloat(cs.borderTopWidth) > 0 && red(cs.borderTopColor)))
      bad.push(el.tagName + ' ' + (el.textContent || '').trim().slice(0, 40));
  }
  return bad;
}"""

JS_TOP = """() => { const e = document.querySelector('[data-testid^=holding-row-]') || document.querySelector('[data-testid=strategy-table]');
  return Math.round(e.getBoundingClientRect().top + window.scrollY); }"""


def run_layout(browser, port: int, old_port: Optional[int]) -> None:
    print("\n== layout ==")
    SHOTS.mkdir(parents=True, exist_ok=True)
    shot_frames = {"idle": (IDLE, []), "live": (LIVE, vc.LIVE_TRADES), "busy": (BUSY, vc.BUSY_TRADES), "alarms": (ALARMS, vc.LIVE_TRADES),
                   "weekend": (WEEKEND, [])}
    for vp in vc.VIEWPORTS:
        for name, (frame, trades) in shot_frames.items():
            s = BS(browser, port, name, vp, frame, Ledger(trades), settle_ms=2600)
            lab = f"{name}/{vp}"
            s.page.screenshot(path=str(SHOTS / f"{name}_{vp}.png"), full_page=True)
            check(not s.page.evaluate(vc.JS_OVERFLOW), f"[{lab}] no horizontal overflow")
            s.page.evaluate(vc.JS_OPEN_ALL)
            small = s.page.evaluate(vc.JS_SMALL)
            check(not small, f"[{lab}] every button and summary >= 44 px, rows and details open {small}")
            s.page.evaluate(vc.JS_CLOSE_ALL)
            check(s.navs[0] == s.loads, f"[{lab}] never reloaded")
            check(not s.errors, f"[{lab}] no console or page errors {s.errors[:2]}")
            if vp == "desktop":
                h = s.page.evaluate("document.querySelector('[data-testid=status-strip]').getBoundingClientRect().height")
                if name in ("idle", "live", "busy", "weekend"):
                    check(h <= 64.5, f"[{lab}] the status strip is {h:.0f} px (<= 64)")
            if name in ("idle", "weekend", "live", "busy"):
                red = s.page.evaluate(JS_REDDISH)
                check(not red, f"[{lab}] nothing red outside money numbers {red[:3]}")
            fonts = s.page.evaluate("""() => ({ h2: getComputedStyle(document.querySelector('h2')).fontFamily,
              body: getComputedStyle(document.body).fontFamily, bg: getComputedStyle(document.body).backgroundColor,
              loaded: document.fonts.check('700 16px "Bricolage Grotesque"'), serif: [...document.querySelectorAll('main *')].some(e => /serif/i.test(getComputedStyle(e).fontFamily) && !/sans-serif/i.test(getComputedStyle(e).fontFamily)) })""")
            check("Bricolage Grotesque" in fonts["h2"] and fonts["loaded"] and not fonts["serif"] and "Fraunces" not in fonts["h2"],
                  f"[{lab}] Bricolage Grotesque headings, no serif ({fonts['h2'][:40]})")
            check(fonts["bg"] == "rgb(238, 241, 250)", f"[{lab}] the ground is the new #EEF1FA ({fonts['bg']})")
            s.close()

    # DOM order = phone order; desktop columns
    s = BS(browser, port, "order", "phone", LIVE_ONE, Ledger(vc.LIVE_TRADES), settle_ms=1500)
    ys = s.page.evaluate("""() => ['[data-testid=balance-card]', '[data-testid^=holding-row-]', '[data-testid=market-mood]', '[data-testid=strategy-table]',
      '[data-testid=risk-telemetry]', '[data-testid=results-panel]'].map(sel => document.querySelector(sel).getBoundingClientRect().top + scrollY)""")
    check(ys == sorted(ys) and len(set(ys)) == len(ys), f"[order/phone] Balance, Holding now, mood, playbooks, Safety, Results ({[round(y) for y in ys]})")
    s.close()
    s = BS(browser, port, "order", "desktop", LIVE_ONE, Ledger(vc.LIVE_TRADES), settle_ms=1500)
    r = s.page.evaluate("""() => { const b = q => document.querySelector(q).getBoundingClientRect();
      const bal = b('[data-testid=balance-card]'), tab = b('[data-testid=strategy-table]'), saf = b('[data-testid=risk-telemetry]'), res = b('[data-testid=results-panel]');
      return { balRight: bal.left > tab.left, safUnderBal: saf.top >= bal.bottom - 1 && Math.abs(saf.left - bal.left) < 2, resUnderSaf: res.top >= saf.bottom - 1, colW: Math.round(bal.width) }; }""")
    check(r["balRight"] and r["safUnderBal"] and r["resUnderSaf"] and r["colW"] == 420, f"[order/desktop] Balance, Safety, Results stack in the 420 px right column {r}")
    s.close()

    # Slow trades mode: what it showed before (no Safety, no Results), the overnight holds still on top
    s = BS(browser, port, "slow", "desktop", with_overnight(LIVE), Ledger(vc.LIVE_TRADES), settle_ms=1200)
    s.page.locator("[data-testid=mode-tab-swing]").click(); s.page.wait_for_timeout(600)
    sw = s.page.evaluate("""() => ({ safety: document.querySelectorAll('[data-testid=risk-telemetry]').length, results: document.querySelectorAll('[data-testid=results-panel]').length,
      swing: document.querySelectorAll('[data-testid=swing-telemetry]').length, ovn: document.querySelectorAll('[data-testid=overnight-holds]').length,
      bal: document.querySelectorAll('[data-testid=balance-card]').length })""")
    check(sw == {"safety": 0, "results": 0, "swing": 1, "ovn": 1, "bal": 1}, f"[slow-mode] Balance, overnight holds and the slow-trades view; no Safety, no Results {sw}")
    s.page.screenshot(path=str(SHOTS / "slow_desktop.png"), full_page=True)
    check(not s.page.evaluate(vc.JS_OVERFLOW), "[slow-mode] no horizontal overflow")
    s.close()

    # the Close-all button is ink, not red, until it asks for confirmation
    s = BS(browser, port, "buttons", "desktop", LIVE_ONE, Ledger(vc.LIVE_TRADES), settle_ms=1200)
    bg = s.page.eval_on_selector("[data-testid=btn-flatten-all]", "e => getComputedStyle(e).backgroundColor")
    check(bg == "rgb(14, 19, 48)", f"[buttons] 'Close all quick trades now' is ink ({bg})")
    s.page.click("[data-testid=btn-flatten-all]"); s.page.wait_for_timeout(200)
    bg2 = s.page.eval_on_selector("[data-testid=btn-flatten-all]", "e => getComputedStyle(e).backgroundColor")
    check(bg2 == LOSS_RED, f"[buttons] it turns the loss colour while it asks to confirm ({bg2})")
    s.close()

    # the top of "Holding now" (or the playbook table) is no lower than on the old page
    if old_port is not None:
        for name, (path, trades) in vc.FRAMES.items():
            for vp in vc.VIEWPORTS:
                new = vc.Session(browser, port, "new", vp, name)
                old = vc.Session(browser, old_port, "old", vp, name)
                nt, ot = new.page.evaluate(JS_TOP), old.page.evaluate(JS_TOP)
                check(nt <= ot, f"[top/{name}/{vp}] holding (or playbook table) starts at y={nt}, old page y={ot}")
                new.close(); old.close()
        # negative control: the comparison fails when the new page is pushed down
        new = vc.Session(browser, port, "neg", "phone", "live")
        old = vc.Session(browser, old_port, "neg", "phone", "live")
        new.page.evaluate("() => { const d = document.createElement('div'); d.style.height = '4000px'; document.querySelector('main > div').prepend(d); }")
        check(new.page.evaluate(JS_TOP) > old.page.evaluate(JS_TOP), "[negative] the holding-top comparison fails when the new page is pushed down")
        new.close(); old.close()

    # negative controls for overflow and targets
    s = BS(browser, port, "neg/layout", "phone", LIVE, Ledger(vc.LIVE_TRADES), settle_ms=1000)
    s.page.evaluate("() => { const d = document.createElement('div'); d.style.cssText = 'width:900px;height:10px'; document.querySelector('main > div').appendChild(d); }")
    check(bool(s.page.evaluate(vc.JS_OVERFLOW)), "[negative] a wide element is caught by the overflow check")
    s.page.evaluate("() => { const b = document.querySelector('[data-testid=results-tab-day]'); b.style.minHeight = '0'; b.style.height = '30px'; }")
    check(bool(s.page.evaluate(vc.JS_SMALL)), "[negative] a short button is caught by the 44 px check")
    s.close()
    s = BS(browser, port, "neg/red", "desktop", IDLE, Ledger([]), settle_ms=1000)
    s.page.evaluate("() => { const e = document.querySelector('[data-testid=window-badge]'); e.style.background = '#C2300F'; }")
    check(bool(s.page.evaluate(JS_REDDISH)), "[negative] a red status chip on the idle page is caught")
    s.close()


# ---------------------------------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-out", type=Path, default=None, help="export of the old page (for the holding-top check)")
    ap.add_argument("--only", default="attention,results,motion,layout")
    args = ap.parse_args()
    only = set(args.only.split(","))
    if not (vc.NEW_OUT / "index.html").exists():
        print("build the export first: (cd frontend && npx next build)")
        return 2
    servers = [vc.serve(vc.NEW_OUT, vc.NEW_PORT)]
    old_port = None
    if args.old_out is not None:
        if not (args.old_out / "index.html").exists():
            print(f"{args.old_out} has no index.html")
            return 2
        servers.append(vc.serve(args.old_out, vc.OLD_PORT))
        old_port = vc.OLD_PORT
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            if "attention" in only:
                run_attention(browser, vc.NEW_PORT)
            if "results" in only:
                run_results(browser, vc.NEW_PORT)
            if "motion" in only:
                run_motion(browser, vc.NEW_PORT)
            if "layout" in only:
                run_layout(browser, vc.NEW_PORT, old_port)
            browser.close()
    finally:
        for sv in servers:
            sv.terminate()
            sv.wait(timeout=5)
    for port in [vc.NEW_PORT] + ([vc.OLD_PORT] if old_port else []):
        check(vc.port_free(port), f"port {port} released")
    print(f"\n{vc.PASSED[0]} passed, {len(vc.FAILURES)} failed")
    for f in vc.FAILURES:
        print(f"  FAIL {f}")
    return 1 if vc.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
