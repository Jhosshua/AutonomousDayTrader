#!/usr/bin/env python3
# Retired 2026-10-07: layout replaced by scripts/verify_gut_dashboard.py; retained for reference.
"""scripts/verify_compact_dashboard.py

QA for PLAN_2026_09_29_compact_dashboard.md: the "Quick trades" view keeps every piece of information but
needs far less scrolling.

Serves the NEW static export (frontend/out, `npx next build` first) and the OLD export of the commit before
the change (`--old-out DIR`, required) on two local ports with NO backend. Websocket and REST calls are
Playwright route mocks; the clock is fixed at Tue 2026-09-29 11:56 ET so the header clock, countdown and
"now" line match on both pages. Every frame gets a fresh page per build and viewport (nothing left open from
an earlier frame), fonts are awaited before measuring.

Checks
  1. Height: new page under a hard cap AND a set fraction of the old page on the same frame (plan section 8).
  2. Text parity: every visible DOM text node of the old page (all <details> opened) must be on the new page
     (every playbook row and <details> opened), compared with exact case as a MULTISET PER REGION (holding row,
     playbook, mood card, safety card, rest). A node counts only if it has a >= 2 px box inside every clipping
     ancestor and passes checkVisibility (so sr-only, truncated, clipped or collapsed text counts as missing).
     Pro words are checked too (desktop, live + branches).
  3. Alarms: with every row and <details> closed, each alarm and page banner is outside the playbook details
     panel and is the element actually hit at its centre and four inner points.
  4. Playbook rows: aria-expanded flips, the panel appears, Enter/Space toggle it, an open row stays open
     across pushed frames.
  5. No horizontal overflow; every visible button and summary >= 44 px; no console or page errors; the page
     is never reloaded.
  6. Negative controls prove checks 1-3 can fail (removed node, sr-only, truncation, line moved into a hidden
     panel, tall page, alarm moved into a hidden panel).
Screenshots: docs/compact_dashboard/screenshots/.

    python3 scripts/build_compact_dashboard_fixtures.py        # only when the fixtures change
    (cd frontend && npx next build)
    python3 scripts/verify_compact_dashboard.py --old-out /path/to/old/frontend/out
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import verify_ui_redesign as base  # noqa: E402  (mock payload helpers)

NEW_OUT = ROOT / "frontend/out"
MOOD_FIX = ROOT / "docs/holding_mood/fixtures"
COMPACT_FIX = ROOT / "docs/compact_dashboard/fixtures"
SHOTS = ROOT / "docs/compact_dashboard/screenshots"
NEW_PORT, OLD_PORT = 3025, 3026
FIXED_NOW = datetime(2026, 9, 29, 15, 56, 0, tzinfo=timezone.utc)  # 11:56 AM ET

VIEWPORTS = {"desktop": (1440, 900), "phone": (390, 844)}

# Today's two real finished trades (2026-09-29, Snap Back shorts in MSFT), times in UTC.
LIVE_TRADES = [
    base.make_trade(1, "MSFT", "SHORT", "mean_reversion", 97.63, 15, 7, session_date="2026-09-29"),
    base.make_trade(2, "MSFT", "SHORT", "mean_reversion", -50.04, 14, 33, session_date="2026-09-29"),
]

def redate(items: List[Dict[str, Any]], day: str = "2026-09-29") -> List[Dict[str, Any]]:
    """The busy fixtures' trades are dated 2026-09-24; this harness's clock says 09-29, so they are 'today' only once
    their dates say so (Results groups by session_date). Times of day are unchanged."""
    out = []
    for t in items:
        t = dict(t)
        t["session_date"] = day
        for k in ("opened_at", "closed_at"):
            t[k] = day + t[k][10:]
        out.append(t)
    return out


BUSY_TRADES = redate(base.BUSY_TODAY_TRADES)

# frame name -> (fixture path, finished trades the /api/trades mock returns)
FRAMES: Dict[str, Tuple[Path, List[Dict[str, Any]]]] = {
    "idle": (MOOD_FIX / "idle.json", []),
    "live": (COMPACT_FIX / "live.json", LIVE_TRADES),
    "live_one": (COMPACT_FIX / "live_one.json", LIVE_TRADES),
    "busy": (MOOD_FIX / "busy.json", BUSY_TRADES),
    "alarms": (COMPACT_FIX / "alarms.json", LIVE_TRADES),
    "branches": (COMPACT_FIX / "branches.json", LIVE_TRADES),
    "branches_confirmed": (COMPACT_FIX / "branches_confirmed.json", LIVE_TRADES),
    "waiting": (MOOD_FIX / "waiting.json", []),
    "weekend": (MOOD_FIX / "weekend.json", []),
    "stale_vix": (MOOD_FIX / "stale_vix.json", []),
    "sized_down": (MOOD_FIX / "sized_down.json", BUSY_TRADES),
    "pre_release_and_unlinked": (MOOD_FIX / "pre_release_and_unlinked.json", BUSY_TRADES),
}
PRO_FRAMES = ("live", "branches")

# Plan section 8: hard cap (px) and max fraction of the old page's height on the same frame (--no-ratio skips the
# fraction: on 2026-09-30 old and new are both compact pages, see ERRORS.md 2026-09-30).
# 2026-10-04 bright dashboard (PLAN_2026_10_04 section 11.6), pre-registered, not to be raised without a MEMORY.md
# entry. The compact page of 2026-09-29 had 1100/1250/1300/2050 and 2600/3200/3400/5000; Results with one open day
# and a "Show older days" button is taller than the six-row "today" list it replaces.
CAP = {
    ("idle", "desktop"): 1150, ("live", "desktop"): 1300, ("live_one", "desktop"): 1350, ("busy", "desktop"): 2100,
    ("idle", "phone"): 2950, ("live", "phone"): 3550, ("live_one", "phone"): 3750, ("busy", "phone"): 5350,
}
RATIO = {"desktop": 0.60, "phone": 0.75}

# Old-page text that may appear FEWER times per region on the new page, as long as it is on the page at least
# once. Each entry has its reason.
SHARED_ONCE: Dict[str, str] = {
    "9:30": "one shared hours axis above the playbook rows replaces one axis per card",
    "noon": "one shared hours axis above the playbook rows replaces one axis per card",
    "4 PM": "one shared hours axis above the playbook rows replaces one axis per card",
}
# Old-page text allowed to be missing entirely, each with its reason (empty on purpose).
ALLOWED_MISSING: Dict[str, str] = {
    "What it did today": "the section is now 'Results'; today is its first group (PLAN_2026_10_04 section 6)",
    "Trade history": "the history drawer button is gone: history is the always-visible Results panel",
    "No finished trades today. Your previous days are saved in Trade history.": "now 'No finished trades today.' inside Results, nothing is saved 'in' a drawer any more",
    "Right now": "the small label above the sentence is gone: the status strip is the status line (PLAN_2026_10_04 section 6)",
    "Loading today's trades…": "now 'Loading results…'",
    "Couldn't load today's trades": "now the muted line 'Couldn't refresh results. Showing the last ones loaded.' (never red)",
    # The drawer-only texts of PLAN section 6 / 11.4 (range buttons, Balance/Result/Trades/Fees tiles, "Load older trades",
    # "No finished trades on this day.", "More trades from this day ...") are NOT listed: the old drawer was closed in every
    # parity frame, so none of them is in the old node list, and listing common words like "Result" would only blind the check.
}

MIN_OLD_NODES = 40

NO_RATIO = [False]
FAILURES: List[str] = []
PASSED = [0]


TODAY = "2026-09-29"  # the fixed clock's ET date


def history_sessions(n: int = 30, before: str = TODAY) -> List[Dict[str, Any]]:
    """n earlier trading days, newest first, deterministic: a mix of wins and losses, the oldest four recovered
    daily totals with no trade rows (aggregate_only). The newest day's total differs from the sum of its trades
    by 3 cents (a day total is the ledger's number, not a sum the page computes)."""
    d = date.fromisoformat(before)
    out: List[Dict[str, Any]] = []
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() >= 5:
            continue
        i = len(out)
        pnl = round(((i * 37) % 200) - 90 + (i % 3) * 1.37, 2)
        out.append({
            "session_date": d.isoformat(), "opening_equity": 50000.0, "closing_equity": round(50000.0 + pnl, 2),
            "realized_pnl": pnl, "trades_count": 3 + i % 5, "fees": 0.0, "source": "ledger",
            "aggregate_only": i >= n - 4,
            "strategies": {"vwap_pullback": {"trades_count": 2, "realized_pnl": round(pnl / 2, 2)},
                           "mean_reversion": {"trades_count": 1 + i % 5, "realized_pnl": round(pnl - round(pnl / 2, 2), 2)}},
        })
    return out


def history_trades(sessions: List[Dict[str, Any]], days: int = 2) -> List[Dict[str, Any]]:
    """Trade rows for the newest `days` earlier days; the first day's total is the trade sum plus 0.03."""
    items: List[Dict[str, Any]] = []
    for di, sess in enumerate(sessions[:days]):
        n = sess["trades_count"]
        total = sess["realized_pnl"] - (0.03 if di == 0 else 0)
        each = round(total / n, 2)
        pnls = [each] * (n - 1) + [round(total - each * (n - 1), 2)]
        for k, pnl in enumerate(pnls):
            sym = ["MSFT", "AMD", "NVDA", "META", "GOOGL", "AMZN", "PLTR", "AAPL"][(k + di) % 8]
            items.append(base.make_trade(100 * (di + 1) + k, sym, "LONG" if k % 2 == 0 else "SHORT",
                                         ["vwap_pullback", "mean_reversion", "news_momentum", "orb"][k % 4], pnl,
                                         15 + k % 3, 5 * k, session_date=sess["session_date"]))
    return items


def ledger_all(today_trades: List[Dict[str, Any]], sessions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The /api/trades?range=all answer: today's trades and the newest earlier days' trades as rows, every day as a
    session summary (sessions is not paginated)."""
    hist = sessions if sessions is not None else history_sessions()
    items = list(today_trades) + history_trades(hist)
    resp = base.trades_response(items)
    today_sum = round(sum(t["realized_pnl"] for t in today_trades), 2)
    today_sess = []
    if today_trades:
        today_sess = [{"session_date": TODAY, "opening_equity": 50000.0, "closing_equity": 50000.0 + today_sum,
                       "realized_pnl": today_sum, "trades_count": len(today_trades), "fees": 0.0, "source": "ledger",
                       "aggregate_only": False}]
    resp["sessions"] = today_sess + hist
    resp["recovered_sessions"] = [s for s in hist if s["aggregate_only"]]
    total = sum(s["trades_count"] for s in resp["sessions"])
    resp["summary"] = dict(resp["summary"], trades_count=total,
                           realized_pnl=round(sum(s["realized_pnl"] for s in resp["sessions"]), 2))
    return resp


def trades_mock(url: str, today_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """range=all answers sessions and items; every other range answers as before (today's rows, no sessions), so
    the OLD page, which never asks for range=all, sees exactly what it always saw."""
    if "range=all" in url:
        return ledger_all(today_trades)
    return base.trades_response(today_trades)


def check(ok: bool, msg: str) -> None:
    if ok:
        PASSED[0] += 1
        print(f"  ok   {msg}")
    else:
        FAILURES.append(msg)
        print(f"  FAIL {msg}")


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def serve(directory: Path, port: int) -> subprocess.Popen:
    if not port_free(port):
        raise SystemExit(f"port {port} is busy; stop whatever holds it first")
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "-d", str(directory)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if not port_free(port):
            return proc
        time.sleep(0.1)
    proc.terminate()
    raise SystemExit(f"static server on {port} did not start")


# ---------------------------------------------------------------------------------------------------
# Page probes
# ---------------------------------------------------------------------------------------------------
JS_OPEN_ALL = """async () => {
  const openDetails = () => document.querySelectorAll('details').forEach(d => { d.open = true; });
  openDetails();
  document.querySelectorAll('[data-testid=strategy-row-toggle][aria-expanded=false]').forEach(b => b.click());
  await new Promise(r => setTimeout(r, 200));
  openDetails();
  await new Promise(r => setTimeout(r, 150));
}"""

JS_CLOSE_ALL = """async () => {
  document.querySelectorAll('details').forEach(d => { d.open = false; });
  document.querySelectorAll('[data-testid=strategy-row-toggle][aria-expanded=true]').forEach(b => b.click());
  await new Promise(r => setTimeout(r, 200));
}"""

# Visible text nodes -> [region, text] pairs. Region = holding row / playbook (by name) / mood / safety / page.
JS_NODES = """() => {
  const clips = (el) => { const cs = getComputedStyle(el); return /(hidden|clip|auto|scroll)/.test(cs.overflowX + cs.overflowY); };
  const inside = (r, b) => r.left >= b.left - 1 && r.right <= b.right + 1 && r.top >= b.top - 1 && r.bottom <= b.bottom + 1;
  const region = (el) => {
    const hold = el.closest('[data-testid^=holding-row-]');
    if (hold) return hold.getAttribute('data-testid');
    const art = el.closest('article');
    if (art && art.querySelector('[data-testid=strategy-window]')) {
      const n = art.querySelector('[data-testid=strategy-name]') || art.querySelector('h3');
      return 'strategy:' + (n ? n.textContent.replace(/\\s+/g, ' ').trim().toLowerCase() : '?');
    }
    if (el.closest('[data-testid=market-mood]')) return 'mood';
    if (el.closest('[data-testid=risk-telemetry]')) return 'safety';
    return 'page';
  };
  const out = [];
  const root = document.querySelector('main') || document.body;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = n.textContent.replace(/\\s+/g, ' ').trim();  // DOM text, exact case (CSS text-transform does not apply)
    if (!text || /^[·|•,.:;()\\-–—]+$/.test(text)) continue;
    const el = n.parentElement;
    if (!el || !el.checkVisibility({ opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true })) continue;
    const range = document.createRange(); range.selectNodeContents(n);
    const rects = [...range.getClientRects()].filter(r => r.width >= 2 && r.height >= 2);
    if (!rects.length) continue;
    let ok = true;
    for (let a = el; a && a !== document.documentElement && ok; a = a.parentElement) {
      if (!clips(a)) continue;
      const b = a.getBoundingClientRect();
      if (!rects.every(r => inside(r, b))) ok = false;
    }
    if (ok) out.push([region(el), text]);
  }
  return out;
}"""

JS_OVERFLOW = """() => {
  const w = window.innerWidth, bad = [];
  const de = document.documentElement;
  if (de.scrollWidth > w + 1) bad.push('document scrollWidth ' + de.scrollWidth + ' > ' + w);
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || !el.getClientRects().length) continue;
    if (el.closest('.drift, .drift2') || cs.position === 'fixed') continue;
    const r = el.getBoundingClientRect();
    if (r.width && (r.right > w + 1 || r.left < -1)) bad.push(el.tagName + '.' + String(el.className).slice(0, 40) + ' [' + Math.round(r.left) + ',' + Math.round(r.right) + ']');
  }
  return bad.slice(0, 8);
}"""

JS_SMALL = """() => [...document.querySelectorAll('main button, main summary')]
  .filter(b => b.getClientRects().length && b.getBoundingClientRect().height < 43.5)
  .map(b => (b.textContent || '').trim().slice(0, 30) + ' h=' + Math.round(b.getBoundingClientRect().height))"""

JS_TRUNCATE_CLASSES = """() => [...document.querySelectorAll('main .truncate, main [class*=line-clamp]')].map(e => e.className.slice(0, 60))"""

# An alarm is "shown" when it is outside the playbook details panel and is what the pointer hits at its centre.
JS_HIT = """async (sel) => {
  const res = [];
  for (const e of document.querySelectorAll(sel)) {
    if (e.closest('[data-testid=strategy-details]')) { res.push('inside details panel'); continue; }
    e.scrollIntoView({ block: 'center' });
    await new Promise(r => setTimeout(r, 60));
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) { res.push('no box'); continue; }
    // centre plus four inner points: every one must land on the alarm itself
    const pts = [[0.5, 0.5], [0.15, 0.25], [0.85, 0.25], [0.15, 0.75], [0.85, 0.75]];
    const ok = pts.every(([fx, fy]) => { const hit = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy); return hit && (hit === e || e.contains(hit)); });
    res.push(ok ? 'ok' : 'covered or hidden');
  }
  window.scrollTo(0, 0);
  return res;
}"""

BANNERS = [
    "Price feed is down, it can't trade right now.",
    "The robot's positions don't match the Alpaca account. New trades are paused until they match.",
    "Saving problems: new trades are paused until this is fixed.",
    "Stopped for today. It hit the daily loss limit.",
]


class Session:
    """One build, one viewport, one frame (fresh page), frames may be re-pushed; never reloaded."""

    def __init__(self, browser, port: int, label: str, viewport: str, frame_name: str, pro: bool = False):
        w, h = VIEWPORTS[viewport]
        self.label = f"{label}/{frame_name}/{viewport}{'/pro' if pro else ''}"
        self.frame_name = frame_name
        path, trades = FRAMES[frame_name]
        self.frame = json.loads(path.read_text())
        self.ctx = browser.new_context(viewport={"width": w, "height": h})
        if pro:
            self.ctx.add_init_script("try { localStorage.setItem('daytrader.showPro', '1'); } catch (e) {}")
        self.page = self.ctx.new_page()
        self.page.clock.set_fixed_time(FIXED_NOW)
        self.ws: Dict[str, Any] = {}
        self.navs = [0]
        self.errors: List[str] = []
        p = self.page
        p.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        p.on("console", lambda m: self.errors.append(f"console: {m.text}") if m.type == "error" else None)
        p.on("framenavigated", lambda f: self.navs.__setitem__(0, self.navs[0] + 1) if f == p.main_frame else None)

        def on_ws(ws) -> None:
            self.ws["route"] = ws
            ws.send(json.dumps(self.frame))

        p.route_web_socket(re.compile(r".*/ws/ui$"), on_ws)
        p.route(re.compile(r".*/api/trades.*"), lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(trades_mock(route.request.url, trades))))
        p.route(re.compile(r".*/health$"), lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(base.HEALTH_RESPONSE)))
        p.route(re.compile(r".*/api/account$"), lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(self.frame["account"])))
        p.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=20000)
        p.wait_for_selector("[data-testid=market-mood]", timeout=15000)
        p.evaluate("() => document.fonts.ready")
        p.wait_for_timeout(1300)
        self.loads = self.navs[0]

    def push(self) -> None:
        self.ws["route"].send(json.dumps(self.frame))
        self.page.wait_for_timeout(400)

    def height(self) -> int:
        return int(self.page.evaluate("document.documentElement.scrollHeight"))

    def nodes(self) -> Counter:
        self.page.evaluate(JS_OPEN_ALL)
        got = Counter((r, t) for r, t in self.page.evaluate(JS_NODES))
        self.page.evaluate(JS_CLOSE_ALL)
        return got

    def close(self) -> None:
        self.ctx.close()


def missing(old: Counter, new: Counter) -> List[str]:
    on_page = {t for (_, t) in new}
    out = []
    for (region, text), n in old.items():
        if text in ALLOWED_MISSING:
            continue
        if text in SHARED_ONCE:
            if text not in on_page:
                out.append(f"{region}: {text!r} (not on the page at all)")
            continue
        have = new.get((region, text), 0)
        if have < n:
            out.append(f"{region}: {text!r} x{n - have}")
    return out


# ---------------------------------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------------------------------
def frame_checks(browser, name: str, vp: str, report: List[str]) -> None:
    new = Session(browser, NEW_PORT, "new", vp, name)
    old = Session(browser, OLD_PORT, "old", vp, name)
    lab = f"{name}/{vp}"
    new_h, old_h = new.height(), old.height()
    report.append(f"| {name} | {vp} | {old_h} | {new_h} | {round(100 * new_h / old_h)}% |")
    if (name, vp) in CAP:
        check(new_h <= CAP[(name, vp)], f"[{lab}] height {new_h} <= cap {CAP[(name, vp)]}")
    if not NO_RATIO[0]:
        check(new_h <= RATIO[vp] * old_h, f"[{lab}] height {new_h} <= {int(RATIO[vp] * 100)}% of old {old_h}")
    if name in ("idle", "live", "live_one", "busy", "alarms", "branches"):
        new.page.screenshot(path=str(SHOTS / f"{name}_{vp}.png"), full_page=True)

    bad = new.page.evaluate(JS_OVERFLOW)
    check(not bad, f"[{lab}] no horizontal overflow {bad}")
    new.page.evaluate(JS_OPEN_ALL)
    small = new.page.evaluate(JS_SMALL)
    check(not small, f"[{lab}] every visible button and summary >= 44 px, rows and details open {small}")
    new.page.evaluate(JS_CLOSE_ALL)
    check(not new.page.evaluate(JS_TRUNCATE_CLASSES), f"[{lab}] no truncate / line-clamp classes")

    old_nodes, new_nodes = old.nodes(), new.nodes()
    check(sum(old_nodes.values()) >= MIN_OLD_NODES, f"[{lab}] old page produced {sum(old_nodes.values())} text nodes (>= {MIN_OLD_NODES})")
    gone = missing(old_nodes, new_nodes)
    check(not gone, f"[{lab}] every old text node is on the new page, same region ({len(gone)} missing: {gone[:8]})")

    if name == "alarms":
        for sel, what in (("[data-testid=orb-alert]", "ORB orphan alert"),
                          ("[data-testid=orb-resolve-orphan]", "orphan resolve button"),
                          ("[data-testid=orb-init-error]", "ORB init error"),
                          ("[data-testid=tri-engine-broker-issue]", "tri-engine broker issue")):
            res = new.page.evaluate(JS_HIT, sel)
            check(bool(res) and all(r == "ok" for r in res), f"[{lab}] {what} shown with every row closed {res}")
        for sentence in BANNERS:
            loc = new.page.locator("[role=status]", has_text=sentence)
            check(loc.count() == 1 and loc.first.is_visible(), f"[{lab}] banner visible: {sentence[:40]}")

    if name == "branches":
        for text in ("Closing: time flatten at 11:00 AM", "stop moved to the entry", "New entries switched off",
                     "Extra flow, spread and prior-volume checks switched off.", "Confirming protection with the broker.",
                     "Bought APP (long), 40 shares"):
            loc = new.page.locator("[data-testid=strategy-status]", has_text=text)
            check(loc.count() >= 1 and loc.first.is_visible(), f"[{lab}] shown with rows closed: {text}")

    if name == "branches_confirmed":
        loc = new.page.locator("[data-testid=strategy-status]", has_text="Safety exit and target held at the broker.")
        check(loc.count() == 1 and loc.first.is_visible(), f"[{lab}] OR15 confirmed protection shown with rows closed")

    if name == "live":
        rows = new.page.locator("[data-testid=strategy-row-toggle]")
        check(rows.count() == 6, f"[{lab}] 6 playbook rows ({rows.count()})")
        first = rows.first
        check(first.get_attribute("aria-expanded") == "false", f"[{lab}] rows start closed")
        first.click()
        new.page.wait_for_timeout(200)
        check(first.get_attribute("aria-expanded") == "true", f"[{lab}] click opens a row")
        panel_id = first.get_attribute("aria-controls") or ""
        check(new.page.locator(f"[id='{panel_id}'][data-testid=strategy-details]").is_visible(), f"[{lab}] its details panel is visible")
        for _ in range(3):
            new.push()
        check(first.get_attribute("aria-expanded") == "true", f"[{lab}] an open row stays open across 3 pushed frames")
        first.focus()
        new.page.keyboard.press("Enter")
        new.page.wait_for_timeout(200)
        check(first.get_attribute("aria-expanded") == "false", f"[{lab}] Enter closes it")
        new.page.keyboard.press("Space")
        new.page.wait_for_timeout(200)
        check(first.get_attribute("aria-expanded") == "true", f"[{lab}] Space opens it")
        new.page.evaluate(JS_CLOSE_ALL)

        # negative controls (DOM), each must make a check fail
        base_nodes = new.nodes()
        check(not missing(old_nodes, base_nodes), f"[negative/{vp}] baseline before the negative controls is clean")
        mutations = {
            "remove a text node": "() => { const e = document.querySelector('[data-testid=right-now-sentence]'); e.textContent = ''; }",
            "sr-only": "() => { document.querySelector('[data-testid=strategy-decisions]').classList.add('sr-only'); }",
            "truncation": "() => { const e = document.querySelector('[data-testid=account-label]'); Object.assign(e.style, {width: '30px', overflow: 'hidden', whiteSpace: 'nowrap', display: 'block'}); }",
            "moved into a hidden panel": "() => { const d = document.createElement('div'); d.style.display = 'none'; document.querySelector('main').appendChild(d); d.appendChild(document.querySelector('[data-testid=window-badge]')); }",
        }
        for what, js in mutations.items():
            s = Session(browser, NEW_PORT, "neg", vp, "live")
            s.page.evaluate(js)
            got = s.page.evaluate(JS_NODES)
            check(bool(missing(old_nodes, Counter((r, t) for r, t in got))), f"[negative/{vp}] parity catches: {what}")
            s.close()
        new.page.evaluate("() => { const d = document.createElement('div'); d.id = 'neg-tall'; d.style.height = '6000px'; document.querySelector('main').appendChild(d); }")
        check(new.height() > CAP[("live", vp)], f"[negative/{vp}] height check catches a tall page")
        new.page.evaluate("() => document.getElementById('neg-tall').remove()")

    if name == "alarms":
        new.page.evaluate("() => { const d = document.createElement('div'); d.style.display = 'none'; document.querySelector('main').appendChild(d); d.appendChild(document.querySelector('[data-testid=orb-alert]')); }")
        res = new.page.evaluate(JS_HIT, "[data-testid=orb-alert]")
        check(res != ["ok"], f"[negative/{vp}] alarm check catches an alarm moved into a hidden panel {res}")

    for s in (new, old):
        check(s.navs[0] == s.loads, f"[{s.label}] never reloaded ({s.navs[0]} navigations)")
    check(not new.errors, f"[{new.label}] no console or page errors {new.errors[:3]}")
    new.close()
    old.close()


def pro_checks(browser, name: str) -> None:
    new = Session(browser, NEW_PORT, "new", "desktop", name, pro=True)
    old = Session(browser, OLD_PORT, "old", "desktop", name, pro=True)
    gone = missing(old.nodes(), new.nodes())
    check(not gone, f"[{name}/desktop/pro] pro words: every old text node is on the new page ({len(gone)} missing: {gone[:8]})")
    check(not new.errors, f"[{new.label}] no console or page errors {new.errors[:3]}")
    new.close()
    old.close()


def run(old_out: Path) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    servers = [serve(NEW_OUT, NEW_PORT), serve(old_out, OLD_PORT)]
    report: List[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for vp in VIEWPORTS:
                for name in FRAMES:
                    frame_checks(browser, name, vp, report)
            for name in PRO_FRAMES:
                pro_checks(browser, name)
            browser.close()
    finally:
        for s in servers:
            s.terminate()
            s.wait(timeout=5)
    print("\n| frame | viewport | old height | new height | new/old |\n|---|---|---|---|---|")
    print("\n".join(report))
    for port in (NEW_PORT, OLD_PORT):
        check(port_free(port), f"port {port} released")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-out", type=Path, required=True, help="static export of the commit before the change")
    ap.add_argument("--no-ratio", action="store_true",
                    help="skip only the '<= 60% / 75% of old' ratio checks (old and new are both compact pages)")
    args = ap.parse_args()
    NO_RATIO[0] = args.no_ratio
    for d in (NEW_OUT, args.old_out):
        if not (d / "index.html").exists():
            print(f"{d} has no index.html: build the export first")
            return 2
    run(args.old_out)
    print(f"\n{PASSED[0]} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print(f"  FAIL {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
