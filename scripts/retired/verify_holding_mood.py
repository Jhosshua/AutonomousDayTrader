#!/usr/bin/env python3
# Retired 2026-10-07: layout replaced by scripts/verify_gut_dashboard.py; retained for reference.
"""scripts/verify_holding_mood.py

Visual and behaviour QA for PLAN_2026_09_29_holding_card_and_market_mood.md ("Holding now" says which
strategy holds a trade; the market mood card says how the robot adapts).

Serves the static export (frontend/out, `npx next build` first) on port 3005 with NO backend. The page's
websocket and REST calls are Playwright route mocks. The frames live in docs/holding_mood/fixtures/
(built from the real backend serializers by scripts/build_holding_mood_fixtures.py). ONE page per
viewport (1440x900 and 390x844) is switched from state to state by pushing websocket frames; it is
never reloaded (the navigation count is asserted). Full-page screenshots go to docs/holding_mood/screenshots/.

Every expected sentence below is written out here from the plan, independently of the page code, and
every detector is proven able to fail by a negative control on a throwaway page at the end.

    python3 scripts/build_holding_mood_fixtures.py     # only when the payload shape changes
    python3 scripts/verify_holding_mood.py
"""
from __future__ import annotations

import copy
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import verify_ui_redesign as base  # noqa: E402  (mock payload helpers + export server)

FIXTURES = ROOT / "docs/holding_mood/fixtures"
SHOTS = ROOT / "docs/holding_mood/screenshots"
BASE_URL = base.BASE_URL

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
# Expected text, written from the plan (not read back from the page code)
# ---------------------------------------------------------------------------------------------------
CLOSED = "Market closed. When it opens, the robot will size and place new trades for the market's mood."
WAITING = "Waiting for market data."
ALL_THREE = "Ride the Trend, Big News and Snap Back"
OWN_CHECKS = "ORB and the Tesla/Coeur plans use their own checks."
MOOD_NOTE = "ORB uses its own 9:38 market check instead. The Tesla/Coeur plans do not change with the mood."
LEVEL_WORD = {"calm": "calm", "normal": "normal", "nervous": "nervous", "panic": "panicky"}
LEVEL_CHIP = {"calm": "Calm", "normal": "Normal", "nervous": "Nervous", "panic": "Panic"}
SIZING = {"calm": 1.2, "normal": 1.0, "nervous": 0.7, "panic": 0.35}
TREND_WORD = {"rising": "rising", "flat": "flat", "falling": "falling"}


def mood_headline(level: str, direction: str, midday: bool = False) -> str:
    if direction == "unknown":
        return f"{ALL_THREE} are waiting until the robot can see SPY and QQQ again. {OWN_CHECKS}"
    m = SIZING[level]
    if m == 1.0:
        body = f"{ALL_THREE} risk the usual amount per trade."
    else:
        body = (f"{ALL_THREE} risk {round(m * 100)}% of the usual amount per trade; "
                f"only trades with a wide safety exit end up {'smaller' if m < 1 else 'bigger'}.")
    if midday:
        body += " Midday halves that."
    return f"The market is {LEVEL_WORD[level]} and {TREND_WORD[direction]}. {body}"


ADAPTIVE_BADGE = "Sized for the market when it bought"
HOLDERS = {
    "AAPL": ("Ride the Trend", ADAPTIVE_BADGE),
    "NVDA": ("Big News", ADAPTIVE_BADGE),
    "MSFT": ("Snap Back", ADAPTIVE_BADGE),
    "GOOGL": ("Snap Back", ADAPTIVE_BADGE),
    "AMZN": ("Big News", ADAPTIVE_BADGE),
    "PLTR": ("Opening Range Breakout (ORBStraddle rules)", "Own market check"),
    "TSLA": ("Tesla Morning Plan", "Fixed plan"),
    "AMD": ("Manual trade", "Not linked to a strategy"),
}

OLD_WORDING = [r"5-minute", r"five.minute", r"RVOL", r"20 bars", r"breakout_fired", r"opening_range", r"range_high"]
# money losses and alarms only (loss chip, Sell button, loss text)
TERRACOTTA = ["rgb(194, 48, 15)", "rgb(255, 239, 234)", "rgb(245, 183, 168)"]  # loss / terracotta, loss background, loss border (2026-10-04 bright palette)
# the alarm-banner family: also forbidden inside the mood card (ORB's strategy band legitimately sits in it)
ALARM_FAMILY = ["rgb(138, 75, 0)", "rgb(255, 244, 219)", "rgb(240, 215, 154)", "rgb(255, 223, 207)"]  # warn text, warn background, warn border, ORB band

# ---------------------------------------------------------------------------------------------------
# Page probes
# ---------------------------------------------------------------------------------------------------
JS_COLORS = """([sel, forbidden]) => {
  const roots = [...document.querySelectorAll(sel)];
  const bad = [];
  const reddish = (c) => { const m = /^rgba?\\((\\d+), (\\d+), (\\d+)/.exec(c); return !!m && +m[1] > 150 && +m[2] < 90 && +m[3] < 90; };
  for (const root of roots) for (const el of [root, ...root.querySelectorAll('*')]) {
    const cs = getComputedStyle(el);
    if (!el.offsetParent && cs.position !== 'fixed') continue;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const seen = [];
    if (own) seen.push(['text', cs.color]);
    if (cs.backgroundColor !== 'rgba(0, 0, 0, 0)') seen.push(['background', cs.backgroundColor]);
    if (parseFloat(cs.borderTopWidth) > 0) seen.push(['border', cs.borderTopColor]);
    for (const [kind, c] of seen) if (forbidden.includes(c) || reddish(c)) bad.push(kind + ' ' + c + ' :: ' + el.textContent.trim().slice(0, 50));
  }
  return bad;
}"""

JS_OVERFLOW = """() => {
  const w = window.innerWidth, bad = [];
  const de = document.documentElement;
  if (de.scrollWidth > w + 1) bad.push('document scrollWidth ' + de.scrollWidth + ' > ' + w);
  if (document.body.scrollWidth > w + 1) bad.push('body scrollWidth ' + document.body.scrollWidth);
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || !el.getClientRects().length) continue;
    if (el.closest('.drift, .drift2') || cs.position === 'fixed') continue;     // decorative blurred blobs
    const r = el.getBoundingClientRect();
    if (r.width && (r.right > w + 1 || r.left < -1)) bad.push(el.tagName + '.' + String(el.className).slice(0, 40) + ' [' + Math.round(r.left) + ',' + Math.round(r.right) + ']');
  }
  return bad.slice(0, 8);
}"""

# every button on the page, and the summaries of the two new components (an older strategy-card summary,
# "Latest refused setup by stock", is 23px tall and is not part of this change)
JS_SMALL = """() => [...document.querySelectorAll('button, [data-testid=market-mood] summary, [data-testid^=holding-row-] summary')].filter(b => b.getClientRects().length && b.getBoundingClientRect().height < 43)
   .map(b => (b.textContent || '').trim().slice(0, 30) + ' h=' + Math.round(b.getBoundingClientRect().height))"""

JS_ORDER = """() => {
  const hold = document.querySelector('[data-testid^=holding-row-]');
  const mood = document.querySelector('[data-testid=market-mood]');
  const strat = document.querySelector('[data-testid=strategy-window]');
  const before = (a, b) => !!(a && b && (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING));
  return {hold: !!hold, mood: !!mood, holdBeforeMood: before(hold, mood), moodBeforeStrat: before(mood, strat)};
}"""


def overflow(page: Page) -> List[str]:
    return page.evaluate(JS_OVERFLOW)


def text(page: Page, sel: str) -> str:
    loc = page.locator(sel)
    return loc.first.inner_text().strip() if loc.count() else ""


class Session:
    """One page, one viewport: frames are pushed over the mocked websocket, never a reload."""

    def __init__(self, browser, label: str, width: int, height: int, first_frame: str = "idle"):
        self.label = label
        self.ctx = browser.new_context(viewport={"width": width, "height": height})
        self.page = self.ctx.new_page()
        self.ws: Dict[str, Any] = {}
        self.navs = [0]
        self.errors: List[str] = []
        self.actions: List[dict] = []
        p = self.page
        p.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        p.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        p.on("framenavigated", lambda f: self.navs.__setitem__(0, self.navs[0] + 1) if f == p.main_frame else None)
        first = frame(first_frame)

        def on_ws(ws) -> None:
            self.ws["route"] = ws
            ws.on_message(lambda m: self.actions.append(json.loads(m)))
            ws.send(json.dumps(first))

        p.route_web_socket(re.compile(r".*/ws/ui$"), on_ws)
        p.route(re.compile(r".*/api/trades.*"), lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(base.trades_response([]))))
        p.route(re.compile(r".*/health$"), lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(base.HEALTH_RESPONSE)))
        p.route(re.compile(r".*/api/account$"), lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(first["account"])))
        p.goto(BASE_URL, wait_until="networkidle", timeout=20000)
        p.wait_for_selector("[data-testid=market-mood]", timeout=15000)
        self.loads = self.navs[0]
        p.wait_for_timeout(1300)

    def push(self, data: Dict[str, Any], settle_ms: int = 350) -> None:
        self.ws["route"].send(json.dumps(data))
        self.page.wait_for_timeout(settle_ms)

    def wait_headline(self, expected: str) -> bool:
        try:
            self.page.wait_for_function(
                "(t) => { const e = document.querySelector('[data-testid=mood-headline]'); return !!e && e.innerText.trim() === t; }",
                arg=expected, timeout=6000)
            return True
        except Exception:
            return False

    def close(self) -> None:
        self.ctx.close()


def shot(s: Session, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    out = SHOTS / f"{name}_{s.label}.png"
    s.page.screenshot(path=str(out), full_page=True)
    check(out.exists() and out.stat().st_size > 5000, f"[{s.label}] screenshot {out.name}")


def common(s: Session, state: str, holders: Optional[List[str]] = None) -> None:
    """Checks every state must pass (whole page, not just the diff)."""
    lab = f"{s.label}/{state}"
    p = s.page
    ov = overflow(p)
    check(not ov, f"[{lab}] no horizontal overflow anywhere on the page {ov[:3]}")
    body = p.inner_text("body")
    olds = [w for w in OLD_WORDING if re.search(w, body, re.I)]
    check(not olds, f"[{lab}] no RVOL / old ORB wording visible {olds}")
    bad = p.evaluate(JS_COLORS, ["[data-testid=market-mood]", TERRACOTTA + ALARM_FAMILY])
    check(not bad, f"[{lab}] no terracotta or alarm colors inside the mood card {bad[:3]}")
    bad = p.evaluate(JS_COLORS, ["[data-testid=holding-banner]", TERRACOTTA])
    check(not bad, f"[{lab}] no terracotta inside any holding banner or badge {bad[:3]}")
    small = p.evaluate(JS_SMALL)
    check(not small, f"[{lab}] every button and summary is at least 44px tall {small[:3]}")
    order = p.evaluate(JS_ORDER)
    if holders:
        check(order["holdBeforeMood"] and order["moodBeforeStrat"], f"[{lab}] order: Holding now, mood card, strategy cards {order}")
    else:
        check(order["mood"] and not order["hold"] and order["moodBeforeStrat"], f"[{lab}] order: mood card, then strategy cards (no holdings) {order}")
    if holders is not None:
        rows = p.locator("[data-testid^=holding-row-]")
        check(rows.count() == len(holders), f"[{lab}] {rows.count()} holding cards shown, expected {len(holders)}")
        for sym in holders:
            name, badge = HOLDERS[sym]
            row = p.locator(f"[data-testid=holding-row-{sym}]")
            got_name = row.locator("[data-testid=holding-strategy]").inner_text().strip() if row.count() else ""
            got_badge = row.locator("[data-testid=holding-badge]").inner_text().strip() if row.count() else ""
            check(got_name == name and got_badge == badge, f"[{lab}] {sym}: '{name}' + badge '{badge}' (got '{got_name}' / '{got_badge}')")


def contains(s: Session, sel: str, *needles: str, absent: Optional[List[str]] = None, lab: str = "") -> None:
    got = " ".join(s.page.locator(sel).first.inner_text().split()) if s.page.locator(sel).count() else ""
    for n in needles:
        check(n in got, f"[{s.label}] {lab or sel}: contains '{n}'")
    for n in absent or []:
        check(n not in got, f"[{s.label}] {lab or sel}: does NOT contain '{n}'")


# ---------------------------------------------------------------------------------------------------
# The state matrix
# ---------------------------------------------------------------------------------------------------
def run_matrix(s: Session) -> None:
    lab = s.label
    page = s.page
    print(f"\n=== {lab}")

    # idle: market closed, no holdings
    s.push(frame("idle"), 700)
    check(s.wait_headline(CLOSED), f"[{lab}/idle] headline is the closed sentence")
    common(s, "idle", holders=None)
    check(page.locator("[data-testid=mood-tiles]").count() == 0, f"[{lab}/idle] no tiles while the market is closed")
    shot(s, "idle")

    s.push(frame("weekend"), 500)
    check(s.wait_headline(CLOSED), f"[{lab}/weekend] headline is the closed sentence")
    common(s, "weekend", holders=None)

    s.push(frame("waiting"), 500)
    check(s.wait_headline(WAITING), f"[{lab}/waiting] headline is 'Waiting for market data.'")
    common(s, "waiting", holders=None)

    # 4 fear levels x 4 directions (all 16), morning
    for level in ("calm", "normal", "nervous", "panic"):
        for direction in ("rising", "flat", "falling", "unknown"):
            st = f"mood_{level}_{direction}"
            s.push(frame(st), 300)
            check(s.wait_headline(mood_headline(level, direction)), f"[{lab}/{st}] exact headline")
            chip = text(page, "[data-testid=mood-level-chip]")
            check(chip == LEVEL_CHIP[level], f"[{lab}/{st}] level chip '{LEVEL_CHIP[level]}' (got '{chip}')")
            tile = text(page, "[data-testid=mood-tile-trend]")
            want = {"rising": "Rising", "flat": "Flat", "falling": "Falling", "unknown": "Unclear"}[direction]
            check(tile.split("\n")[1].strip() == want if "\n" in tile else False, f"[{lab}/{st}] market direction tile says {want}")
            note = text(page, "[data-testid=mood-note]")
            check((note == MOOD_NOTE) == (direction != "unknown"), f"[{lab}/{st}] second line only when trading by the mood")
            common(s, st, holders=None)

    s.push(frame("mood_nervous_rising_midday"), 400)
    check(s.wait_headline(mood_headline("nervous", "rising", midday=True)), f"[{lab}/midday] headline adds 'Midday halves that.'")
    check("Midday 11:30 AM to 2:00 PM: new trades are half size" in text(page, "[data-testid=mood-tile-time]").replace("\n", " "),
          f"[{lab}/midday] time tile reads the payload's midday window")
    common(s, "midday", holders=None)

    s.push(frame("stale_vix"), 400)
    check(s.wait_headline(mood_headline("normal", "rising")), f"[{lab}/stale] headline uses the level the robot is really sizing by")
    check("Not updating, sizes held at normal" in text(page, "[data-testid=mood-tile-fear]").replace("\n", " "),
          f"[{lab}/stale] fear gauge says it is not updating and sizes are held at normal")
    common(s, "stale", holders=None)

    # busy: five holdings of five kinds
    s.push(frame("busy"), 1500)
    check(s.wait_headline(mood_headline("normal", "rising")), f"[{lab}/busy] headline")
    holders = ["AAPL", "NVDA", "MSFT", "PLTR", "TSLA"]
    common(s, "busy", holders=holders)
    contains(s, "[data-testid=holding-row-AAPL]", "Ride the Trend", "bought at $236.10", "decided at 10:13 AM",
             "started at $234.80", "First part sold. The rest follows the price.", "Sells by 3:55 PM at the latest",
             "Normal size. The per-trade money cap was the limit, so the market mood did not change the share count.",
             "Safety exit placed farther away because the market is jumpy.", "It bought because the market was rising too.",
             "Market is normal now. This trade keeps the size it got; its safety exit never moves farther away.",
             lab="AAPL card")
    contains(s, "[data-testid=holding-row-NVDA]", "Bought 54 shares, fewer than the usual 67: jumpy market (70%).",
             "against the market's direction because the news was extreme and trading was heavy.",
             "Moves to break-even after the first target, then follows the price.", lab="NVDA card")
    contains(s, "[data-testid=holding-row-MSFT]", "sold short at $512.00", "Move safety exit to my entry price",
             "Normal size. The per-trade money cap was the limit",
             "The market was flat, which this playbook allows.", "Safety exit placed a normal distance away.", lab="MSFT card")
    contains(s, "[data-testid=holding-row-PLTR]",
             "Traded after its 9:38 market check: SPY and QQQ data complete and the breakout list balanced "
             "(40% bets down, needs 25% to 75%). Risk on this trade: $1,000.00. Its stop moves to its entry price at "
             "+0.75x its risk, and it can close early if the trade fails fast.",
             "Sells by 11:00 AM at the latest", absent=["Tesla", "held at Alpaca", "Target"], lab="ORB card")
    contains(s, "[data-testid=holding-row-TSLA]", "Does the market change this trade? No, on purpose. It always risks 0.75% of the account",
             "First half", "Second half", "Fixed plan", lab="Tesla card")
    check("Sized for the market" not in page.locator("[data-testid=holding-row-TSLA]").inner_text(),
          f"[{lab}/busy] the Tesla card never claims it was sized for the market")
    check(page.locator("[data-testid=holding-row-PLTR] [data-testid=holding-banner]").get_attribute("data-kind") == "orb",
          f"[{lab}/busy] the ORB banner is its own kind (distinct icon and dashed border)")
    for sym in holders:
        for tid in (f"btn-sell-now-{sym}", f"btn-break-even-{sym}"):
            check(page.locator(f"[data-testid={tid}]").count() == 1, f"[{lab}/busy] {tid} still present")
    shot(s, "busy")

    # sized-down trades: the sentence names the real reasons
    s.push(frame("sized_down"), 1500)
    check(s.wait_headline(mood_headline("nervous", "rising")), f"[{lab}/sized_down] headline")
    common(s, "sized_down", holders=["NVDA", "GOOGL", "AMZN"])
    contains(s, "[data-testid=holding-row-NVDA]", "Bought 54 shares, fewer than the usual 67: jumpy market (70%).", lab="NVDA sized down")
    contains(s, "[data-testid=holding-row-GOOGL]", "Sold short 35 shares, fewer than the usual 50: jumpy market (70%) and midday (half).", lab="GOOGL midday")
    contains(s, "[data-testid=holding-row-AMZN]", "Bought 17 shares, fewer than the usual 49: panicky market (35%), and the account's limits trimmed it.", lab="AMZN trimmed")
    shot(s, "sized_down")

    # a trade from before the record was kept, and one the robot did not place
    s.push(frame("pre_release_and_unlinked"), 1500)
    common(s, "pre_release_and_unlinked", holders=["AAPL", "AMD"])
    contains(s, "[data-testid=holding-row-AAPL]", "This trade started before the robot kept this record.",
             absent=["fewer than", "Normal size"], lab="pre-release card")
    check(page.locator("[data-testid=holding-row-AMD] [data-testid=holding-why]").count() == 0,
          f"[{lab}/unlinked] an unlinked position gets no 'why this size' box")
    shot(s, "pre_release_and_unlinked")

    # details: open both, re-check overflow, and keep them open across 3 frames
    s.push(frame("busy"), 1500)
    page.locator("[data-testid=mood-details] summary").click()
    opened = 0
    for det in page.locator("[data-testid=holding-why-details]").all():
        det.locator("summary").click()
        opened += 1
    page.wait_for_timeout(300)
    check(opened >= 3, f"[{lab}/details] opened the mood details and {opened} 'Why this size?' details")
    ov = overflow(page)
    check(not ov, f"[{lab}/details] no horizontal overflow with every details open {ov[:3]}")
    table = page.locator("[data-testid=mood-tier-table] tr[data-current=true]")
    check(table.count() == 1 and "Normal" in table.first.inner_text() and "(now)" in table.first.inner_text(),
          f"[{lab}/details] the level table marks the current level (Normal) by regime, once")
    check("At most 3 quick trades at once (Tesla/Coeur count, ORB does not)." in text(page, "[data-testid=mood-details]").replace("\n", " "),
          f"[{lab}/details] max trades sentence")
    check("Each trade can use at most 25% of the account." in text(page, "[data-testid=mood-details]").replace("\n", " "),
          f"[{lab}/details] cap sentence")
    shot(s, "busy_details_open")
    for i in range(3):
        f = copy.deepcopy(frame("busy"))
        f["timestamp"] = f"2026-09-29T15:2{i + 1}:00Z"
        for pos in f["all_positions"]:
            pos["market_price"] = round(pos["market_price"] + 0.01 * (i + 1), 2)
        s.push(f, 400)
        still = page.evaluate("() => [...document.querySelectorAll('[data-testid=mood-details], [data-testid=holding-why-details]')].filter(d => !d.open).length")
        check(still == 0, f"[{lab}/details] all details still open after frame {i + 1} ({still} closed)")
    ov = overflow(page)
    check(not ov, f"[{lab}/details] still no overflow after the frames {ov[:3]}")

    # back to a quiet state, then check the page never reloaded and never logged an error
    s.push(frame("idle"), 600)
    check(s.wait_headline(CLOSED), f"[{lab}/end] back to the closed headline through the websocket")
    check(s.navs[0] == s.loads, f"[{lab}] the page never reloaded ({s.navs[0] - s.loads} extra navigations)")
    errs = [e for e in s.errors if "favicon" not in e]
    check(not errs, f"[{lab}] no console errors {errs[:3]}")


def run_old_backend(browser, label: str, width: int, height: int) -> None:
    """An older backend during a deploy sends none of the new keys. It must be a FRESH page: a page that
    already saw new frames keeps their values (frames merge), which is not what a deploy looks like."""
    print(f"\n=== {label} old backend (fresh page, first frame has no new keys)")
    s = Session(browser, f"{label}-oldbackend", width, height, first_frame="old_backend")
    try:
        page = s.page
        old_head = "The market is normal. The mood-sized playbooks risk the usual amount per trade."
        check(s.wait_headline(old_head), f"[{label}/old_backend] headline renders without any new key")
        common(s, "old_backend", holders=["AAPL"])
        check(page.locator("[data-testid=mood-tier-table]").count() == 0, f"[{label}/old_backend] no level table without vix_tiers")
        check(text(page, "[data-testid=mood-tile-trend]").split("\n")[1].strip() == "Not reported",
              f"[{label}/old_backend] market direction says 'Not reported', not a guess")
        contains(s, "[data-testid=holding-row-AAPL]", "This trade started before the robot kept this record.", lab="old-backend card")
        page.locator("[data-testid=mood-details] summary").click()
        ov = overflow(page)
        check(not ov, f"[{label}/old_backend] no overflow with the details open {ov[:3]}")
        errs = [e for e in s.errors if "favicon" not in e]
        check(not errs, f"[{label}/old_backend] no console errors {errs[:3]}")
    finally:
        s.close()


# ---------------------------------------------------------------------------------------------------
# Negative controls: each detector must FAIL on a deliberately broken page
# ---------------------------------------------------------------------------------------------------
def negative_controls(browser, width: int, height: int) -> None:
    print("\n=== negative controls (throwaway page: every detector must catch an injected fault)")
    s = Session(browser, "negative", width, height)
    p = s.page
    try:
        s.push(frame("busy"), 1500)
        clean_overflow = overflow(p)
        p.add_style_tag(content="[data-testid=mood-tile-fear]{background:#C2300F !important;color:#FFEFEA !important}")
        bad = p.evaluate(JS_COLORS, ["[data-testid=market-mood]", TERRACOTTA + ALARM_FAMILY])
        check(len(bad) >= 1, f"[negative] terracotta inside the mood card is detected ({len(bad)} hits)")
        p.add_style_tag(content="[data-testid=holding-row-AAPL] [data-testid=holding-badge]{background:#FFEFEA !important}")
        bad = p.evaluate(JS_COLORS, ["[data-testid=holding-banner]", TERRACOTTA])
        check(len(bad) >= 1, f"[negative] terracotta inside a holding badge is detected ({len(bad)} hits)")
        p.evaluate("() => { const d = document.createElement('div'); d.style.cssText = 'width:2400px;height:10px;background:#ccc'; "
                   "document.querySelector('[data-testid=holding-row-AAPL]').appendChild(d); }")
        check(not clean_overflow and False or len(overflow(p)) >= 1, "[negative] a too-wide element is detected as horizontal overflow")
        p.evaluate("() => { const b = document.querySelector('[data-testid=btn-sell-now-AAPL]'); b.style.minHeight = '0'; b.style.height = '20px'; }")
        check(len(p.evaluate(JS_SMALL)) >= 1, "[negative] a 20px button is detected")
        p.evaluate("() => { const m = document.querySelector('[data-testid=market-mood]'); const h = document.querySelector('[data-testid=holding-row-AAPL]').closest('section'); h.parentNode.insertBefore(m, h); }")
        order = p.evaluate(JS_ORDER)
        check(not order["holdBeforeMood"], "[negative] the mood card moved above Holding now is detected")
        check(not s.wait_headline("This is not the headline"), "[negative] a wrong headline is detected")
        check(re.search(OLD_WORDING[2], "the RVOL was high", re.I) is not None, "[negative] the RVOL wording detector matches")
    finally:
        s.close()


def main() -> int:
    if not (ROOT / "frontend/out/index.html").exists():
        print("frontend/out is missing: run `npx next build` in frontend/ first")
        return 2
    proc = base.start_export_server()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for label, w, h in (("desktop", 1440, 900), ("phone", 390, 844)):
                s = Session(browser, label, w, h)
                try:
                    run_matrix(s)
                finally:
                    s.close()
                run_old_backend(browser, label, w, h)
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
