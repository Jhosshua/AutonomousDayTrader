#!/usr/bin/env python3
"""scripts/verify_overnight_row.py

Page QA for the Overnight playbook row and the 3:45 PM handoff (PLAN_2026_10_05_overnight_row_and_handoff.md).

Same harness as scripts/verify_overnight_holds.py: the static export (frontend/out, `npm run build` first) on port
3005, no backend, the websocket and REST calls are Playwright route mocks, the frames are
docs/overnight_holds/fixtures/*.json built from the REAL backend broadcast by build_overnight_holds_fixtures.py,
and the page clock is fixed to each frame's `_fixed_now`. One page per viewport, switched by pushing frames.
A third run uses a browser in Europe/Rome so a local-time bug in the 3:45 PM logic cannot hide.

Every expected sentence is written out here from the plan, not read back from the page code. Negative controls at
the end prove the detectors can fail.

    python3 scripts/verify_overnight_row.py
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, List

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import verify_overnight_holds as voh  # noqa: E402  (Session, frames, export server)

SHOTS = ROOT / "docs/overnight_row/screenshots"
check = voh.check
frame = voh.frame

DAY_ROWS = ["orb", "vwap_pullback", "news_momentum", "mean_reversion", "tsla_asymmetric_dual", "cde_asymmetric_dual"]
NOTE_X6 = ("From 3:45 PM until they sell at 9:30 AM Thu Oct 1, NVDA, IREN and HUT are saved for Overnight. "
           "The day playbooks won't trade them.")
X6_ROW = "NVDA closed at 3:46 PM, 9 minutes early, so Overnight could buy it at the close."


class TZBrowser:
    """Pass-through browser that forces a time zone on every new context (voh.Session hardcodes New York)."""

    def __init__(self, browser, tz: str):
        self.b, self.tz = browser, tz

    def new_context(self, **kw):
        kw["timezone_id"] = self.tz
        return self.b.new_context(**kw)


def chip(s) -> str:
    return s.text("[data-testid=strategy-row-overnight] [data-testid=window-badge]")


def count(s, sel: str) -> int:
    return s.page.locator(sel).count()


def visible(s, sel: str) -> bool:
    loc = s.page.locator(sel)
    return loc.count() > 0 and loc.first.is_visible()


def open_overnight(s) -> None:
    if not visible(s, "[data-testid=overnight-steps], [data-testid=overnight-steps-none]"):
        s.page.locator("[data-testid=strategy-row-overnight] [data-testid=strategy-row-toggle]").click()
        s.page.wait_for_timeout(250)


def close_overnight(s) -> None:
    if visible(s, "#strategy-details-overnight"):
        s.page.locator("[data-testid=strategy-row-overnight] [data-testid=strategy-row-toggle]").click()
        s.page.wait_for_timeout(200)


def steps(s) -> str:
    return ",".join(s.page.locator("[data-testid^=overnight-step-]").evaluate_all("els => els.map(e => e.dataset.status)"))


def bar_span(s, row: str):
    """Left and right edge of a row's first hours segment, in % of its track."""
    return s.page.evaluate(
        """(row) => {
          const t = document.querySelector(`[data-testid=strategy-row-${row}] [data-testid=strategy-window]`);
          const g = t && t.querySelector('.grow');
          if (!t || !g) return null;
          const a = t.getBoundingClientRect(), b = g.getBoundingClientRect();
          return [ (b.left - a.left) / a.width * 100, (b.right - a.left) / a.width * 100 ];
        }""", row)


def labels_overlap(s) -> List[str]:
    return s.page.evaluate(
        """() => {
          const box = document.querySelector('[data-testid=axis-labels]');
          if (!box) return ['no axis'];
          const els = [...box.children].filter(e => e.getBoundingClientRect().width > 0 && getComputedStyle(e).display !== 'none');
          const bad = [];
          for (let i = 0; i < els.length; i++) for (let j = i + 1; j < els.length; j++) {
            const a = els[i].getBoundingClientRect(), b = els[j].getBoundingClientRect();
            if (a.left < b.right - 1 && b.left < a.right - 1) bad.push((els[i].textContent || 'icon') + ' / ' + (els[j].textContent || 'icon'));
          }
          return bad;
        }""")


def run_matrix(s, phone: bool) -> None:
    L = s.label
    # --- 14:00, nothing held, tonight's buy planned ---------------------------------------------
    s.show(frame("day"))
    heading = s.text("[data-testid=strategy-table] h2")
    check(heading.startswith("The 7 ways it trades"), f"[{L}] day: heading counts 7 ({heading!r})")
    check("6 by day, 1 overnight" in heading, f"[{L}] day: 6 by day, 1 overnight")
    check(count(s, "[data-testid^=strategy-row-]:not([data-testid=strategy-row-toggle])") == 7, f"[{L}] day: 7 playbook rows")
    check(count(s, "[data-testid=strategy-row-overnight]") == 1, f"[{L}] day: one Overnight row")
    last = s.page.locator("[data-testid=strategy-table] article").last.get_attribute("data-testid")
    check(last == "strategy-row-overnight", f"[{L}] day: Overnight is the last row")
    check(chip(s) == "Buys at the 4 PM close", f"[{L}] day: chip {chip(s)!r}")
    check(s.text("[data-testid=overnight-stocks]") == "NVDA, IREN and HUT", f"[{L}] day: stocks read live")
    check(count(s, "[data-testid=handoff-note]") == 0, f"[{L}] day: no handoff note before 3:45 PM")
    check(count(s, "[data-testid=handoff-band]") == 7, f"[{L}] day: handoff stripes on all 7 rows")
    check(count(s, "[data-testid=night-part]") == 7, f"[{L}] day: night part on all 7 rows")
    check("Stripes = 3:45 PM handoff. Night part not to scale." in s.text("[data-testid=night-legend]"), f"[{L}] day: legend explains stripes and night")
    # expected from the frame's own ORB hours, mapped by hand onto 9:30-16:00 = 0-88%
    rng = next(x for x in frame("day")["strategies"] if x["id"] == "orb")["window"]["ranges"][0]
    mins = [int(t[:2]) * 60 + int(t[3:]) - 570 for t in rng]
    want = [m / 390 * 88 for m in mins]
    span = bar_span(s, "orb")
    check(span is not None and abs(span[0] - want[0]) < 0.6 and abs(span[1] - want[1]) < 0.6,
          f"[{L}] day: ORB {rng[0]} to {rng[1]} bar at {want[0]:.1f}% to {want[1]:.1f}% ({span})")
    check(labels_overlap(s) == [], f"[{L}] day: axis labels do not overlap {labels_overlap(s)}")
    if phone:
        check(not visible(s, "[data-testid=axis-4pm]") and visible(s, "[data-testid=axis-night-icon]"), f"[{L}] day: phone shows the moon, not the 4 PM and Night words")
    else:
        check(visible(s, "[data-testid=axis-4pm]") and visible(s, "[data-testid=axis-night]"), f"[{L}] day: desktop shows 4 PM and Night")
    open_overnight(s)
    check(steps(s) == "next,next,next,next,next,next,next", f"[{L}] day: steps all next ({steps(s)})")
    check(visible(s, "[data-testid=overnight-holds-link]"), f"[{L}] day: details link to the holds box")
    href = s.page.locator("[data-testid=overnight-holds-link]").get_attribute("href")
    check(href == "#overnight-holds" and count(s, "#overnight-holds") == 1, f"[{L}] day: link target exists")
    close_overnight(s)

    # --- 14:05 after "No overnight buy tonight" ---------------------------------------------------------
    s.show(frame("no_buy"))
    check(chip(s) == "No buy tonight", f"[{L}] no buy: chip {chip(s)!r}")
    color = s.page.locator("[data-testid=strategy-row-overnight] [data-testid=window-badge]").evaluate("e => getComputedStyle(e).color")
    check(color == "rgb(91, 98, 131)", f"[{L}] no buy: chip is grey, not amber ({color})")

    # --- 15:47:10, Ride the Trend's NVDA closed early, the buy accepted --------------------------------
    s.show(frame("x6_closing"))
    check(chip(s) == "Buying at the close", f"[{L}] x6: chip {chip(s)!r}")
    check(s.text("[data-testid=handoff-note]") == NOTE_X6, f"[{L}] x6: handoff note {s.text('[data-testid=handoff-note]')!r}")
    check(s.text("[data-testid=strategy-row-vwap_pullback] [data-testid=x6-note]") == X6_ROW, f"[{L}] x6: Ride the Trend row says it was closed early")
    check(count(s, "[data-testid=x6-note]") == 1, f"[{L}] x6: only that one row has the note")
    open_overnight(s)
    check(steps(s) == "done,done,done,now,next,next,next", f"[{L}] x6: steps ({steps(s)})")
    check(s.text("[data-testid=overnight-step-x6]").endswith("Ride the Trend's NVDA trade was closed early at 3:46 PM."), f"[{L}] x6: step names the playbook")
    shot(s, "x6_closing_details")
    close_overnight(s)

    # --- 15:47:10, HUT short of data: retried until 3:49:30 PM ---------------------------------------------
    s.show(frame("closing"))
    check(chip(s) == "Not bought yet", f"[{L}] closing: chip {chip(s)!r}")
    # HUT is still retried until 3:49:30 PM and stays reserved, so it is saved for Overnight too
    check("NVDA, IREN and HUT are saved for Overnight" in s.text("[data-testid=handoff-note]"), f"[{L}] closing: note lists the reserved stocks")

    # --- 20:00, three holds, sales queued ---------------------------------------------------------------
    s.show(frame("evening"))
    check(chip(s) == "Holding 3 until 9:30 AM Thu Oct 1", f"[{L}] evening: chip {chip(s)!r}")
    check(count(s, "[data-testid=handoff-note]") == 0, f"[{L}] evening: no handoff note once bought")
    open_overnight(s)
    check(steps(s) == "done,done,done,done,done,done,next", f"[{L}] evening: steps ({steps(s)})")
    shot(s, "evening_details")
    close_overnight(s)

    # --- Friday 20:00, weekend holds ---------------------------------------------------------------------
    s.show(frame("weekend"))
    check(chip(s) == "Holding 3 until 9:30 AM Mon Oct 5", f"[{L}] weekend: chip {chip(s)!r}")

    # --- Thursday 09:32, IREN unsold -------------------------------------------------------------------
    s.show(frame("morning_unsold"))
    check(chip(s) == "Selling", f"[{L}] morning unsold: chip {chip(s)!r}")

    # --- Friday Nov 27, early close -------------------------------------------------------------------
    s.show(frame("early_close"))
    check(chip(s) == "No buy today (early close)", f"[{L}] early close: chip {chip(s)!r}")
    check(count(s, "[data-testid=handoff-band]") == 0, f"[{L}] early close: no handoff stripes")
    check("Stripes" not in s.text("[data-testid=night-legend]"), f"[{L}] early close: legend has no stripes")
    open_overnight(s)
    check(visible(s, "[data-testid=overnight-steps-none]"), f"[{L}] early close: no steps")
    close_overnight(s)

    # --- whole page hygiene --------------------------------------------------------------------------------
    s.show(frame("day"))
    over = s.page.evaluate(voh.JS_OVERFLOW)
    check(over == [], f"[{L}] no horizontal overflow {over[:3]}")
    check(s.errors == [], f"[{L}] no console errors {s.errors[:3]}")
    check(s.navs[0] == s.loads, f"[{L}] no reload while switching frames")
    shot(s, "day")


def run_old_backend(browser, label: str, w: int, h: int) -> None:
    fr = copy.deepcopy(frame("evening"))
    fr.pop("overnight", None)
    s = voh.Session(browser, f"{label}-old", w, h, fr)
    try:
        heading = s.text("[data-testid=strategy-table] h2")
        check(heading == "The 6 ways it trades", f"[{label}] old backend: heading unchanged ({heading!r})")
        check(count(s, "[data-testid=strategy-row-overnight]") == 0, f"[{label}] old backend: no Overnight row")
        check(count(s, "[data-testid=night-part]") == 0 and count(s, "[data-testid=handoff-band]") == 0, f"[{label}] old backend: old hours bars")
    finally:
        s.close()


def shot(s, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    out = SHOTS / f"{name}_{s.label}.png"
    s.page.screenshot(path=str(out), full_page=True)
    check(out.exists() and out.stat().st_size > 5000, f"[{s.label}] screenshot {out.relative_to(ROOT)}")


def negative_controls(browser) -> None:
    s = voh.Session(browser, "neg", 1440, 900, frame("day"))
    try:
        before = (len(voh.FAILURES), voh.PASSED[0])
        check(chip(s) == "Buys at 3:46 PM", "(expected to fail) negative: a wrong chip is caught")
        check(count(s, "[data-testid=strategy-row-overnight]") == 0, "(expected to fail) negative: a missing row is caught")
        s.page.evaluate("() => { const b = document.querySelector('[data-testid=axis-labels]'); const x = document.createElement('span'); x.textContent = 'XX'; x.style.position='absolute'; x.style.left='0'; b.appendChild(x); }")
        check(labels_overlap(s) == [], "(expected to fail) negative: an overlapping label is caught")
        caught = len(voh.FAILURES) - before[0]
        del voh.FAILURES[before[0]:]
        check(caught == 3, f"negative controls: {caught} of 3 detectors failed as they should")
    finally:
        s.close()


def main() -> int:
    if not (ROOT / "frontend/out/index.html").exists():
        print("frontend/out is missing: run `npm run build` in frontend/ first")
        return 2
    proc = voh.base.start_export_server()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for label, w, h, tz in (("desktop", 1440, 900, None), ("phone", 390, 844, None), ("rome", 1280, 900, "Europe/Rome")):
                b = TZBrowser(browser, tz) if tz else browser
                s = voh.Session(b, label, w, h, frame("day"))
                try:
                    run_matrix(s, phone=(w < 1024))
                finally:
                    s.close()
                if tz is None:
                    run_old_backend(browser, label, w, h)
            negative_controls(browser)
            browser.close()
    finally:
        voh.base.stop_export_server(proc)
    print(f"\n{voh.PASSED[0]} passed, {len(voh.FAILURES)} failed")
    for f in voh.FAILURES:
        print("FAIL:", f)
    return 1 if voh.FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
