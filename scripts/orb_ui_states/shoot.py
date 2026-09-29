#!/usr/bin/env python3
"""DEV ONLY: visual QA of the ORB card and the whole dashboard in each ORB state.

Starts scripts/orb_ui_states/serve.py (the real backend + frontend/out export, ORB on fakes) on a
free port, opens ONE page per viewport (desktop 1440x900, phone 390x844) and never reloads it:
each state is switched through POST /__dev/orb_state/<name>, and the card must change through the
websocket alone. Saves full-page and ORB-card screenshots to docs/orb_replacement/ui_screenshots/
and prints every check. Needs `npx next build` in frontend/ first (frontend/out).

    python3 scripts/orb_ui_states/shoot.py
"""
from __future__ import annotations

import json
import re
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
OUT = ROOT / "docs/orb_replacement/ui_screenshots"
HOURS = "Decides 9:38 AM, may add trades until 10:15 AM, closes by 11:00 AM"
OLD_WORDING = [r"5-minute", r"five.minute", r"RVOL", r"20 bars", r"breakout_fired", r"opening_range", r"range_high"]
TERRACOTTA = {"rgb(143, 68, 36)", "rgb(169, 85, 58)"}          # the dashboard's alarm colors
RED_RE = re.compile(r"rgb\((\d+), (\d+), (\d+)\)")

# state -> (text the ORB card must show once the websocket delivers it, alarm color allowed?)
STATES = [
    ("waiting", "Preparing: sizing is frozen", False),
    ("shadow_pick", "would have placed buy APP", False),
    ("live_trade", "Bought APP", False),
    ("sat_out", "sat out (board is one-sided: 84% shorts)", False),
    ("orphan_alert", "no ORB order at Alpaca explains", True),
    ("off", "ORB is switched off", False),
    ("relay_error", "The 9:38 scan did not complete", False),
]

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
    req = urllib.request.Request(base + path, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def is_reddish(color: str) -> bool:
    m = RED_RE.match(color)
    if not m:
        return False
    rr, gg, bb = map(int, m.groups())
    return color in TERRACOTTA or (rr > 150 and gg < 90 and bb < 90)


PROBE = """(card) => {
  const all = card ? [card, ...card.querySelectorAll('*')] : [...document.querySelectorAll('body *')];
  const reds = [];
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (!el.offsetParent && cs.position !== 'fixed') continue;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (own) reds.push({c: cs.color, t: el.textContent.trim().slice(0, 80)});
    if (cs.backgroundColor !== 'rgba(0, 0, 0, 0)') reds.push({c: cs.backgroundColor, t: 'bg: ' + el.textContent.trim().slice(0, 60)});
    if (cs.borderTopColor && cs.borderTopWidth !== '0px') reds.push({c: cs.borderTopColor, t: 'border: ' + el.textContent.trim().slice(0, 60)});
  }
  const over = [];
  if (card) {
    const cr = card.getBoundingClientRect();
    for (const el of card.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.width && (r.right > cr.right + 1 || r.left < cr.left - 1)) over.push(el.tagName + ': ' + el.textContent.trim().slice(0, 60));
      if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX !== 'visible' && el.clientWidth) over.push('clipped ' + el.tagName + ': ' + el.textContent.trim().slice(0, 60));
    }
  }
  return {colors: reds, over};
}"""


def orb_card(page):
    return page.locator("article", has=page.locator("[data-testid=orb-details]")).first


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = tempfile.NamedTemporaryFile("w", prefix="orb_ui_serve_", suffix=".log", delete=False)
    print("server log:", log.name)
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
                print(f"\n=== {vp_name} {vp['width']}px")
                ctx = browser.new_context(viewport=vp, device_scale_factor=2 if vp_name == "phone" else 1)
                page = ctx.new_page()
                console_errors: list = []
                page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))
                ws_frames = [0]
                page.on("websocket", lambda ws: ws.on("framereceived", lambda _f: ws_frames.__setitem__(0, ws_frames[0] + 1)))
                navs = [0]
                page.on("framenavigated", lambda f: navs.__setitem__(0, navs[0] + 1) if f == page.main_frame else None)
                first = post(base, "/__dev/orb_state/waiting")
                page.clock.set_fixed_time(datetime.fromisoformat(first["fake_now"]))
                page.goto(f"http://adt.test:{port}/", wait_until="networkidle")
                page.wait_for_selector("[data-testid=orb-details]", timeout=20000)
                loads = navs[0]
                for name, expect, alarm_ok in STATES:
                    print(f"-- {name}")
                    out = post(base, f"/__dev/orb_state/{name}")
                    page.clock.set_fixed_time(datetime.fromisoformat(out["fake_now"]))
                    card = orb_card(page)
                    try:
                        page.wait_for_function(
                            "(t) => [...document.querySelectorAll('[data-testid=orb-details]')].some(e => e.closest('article').innerText.includes(t))",
                            arg=expect, timeout=15000)
                        live = True
                    except Exception:
                        live = False
                    check(live, f"{vp_name}/{name}: ORB card shows '{expect}' via the websocket, no reload")
                    page.wait_for_timeout(900)                          # entrance animations settle
                    text = card.inner_text()
                    body = page.inner_text("body")
                    check(HOURS in text, f"{vp_name}/{name}: hours read '{HOURS}'")
                    olds = [w for w in OLD_WORDING if re.search(w, body, re.I)]
                    check(not olds, f"{vp_name}/{name}: no old ORB wording on the page {olds}")
                    check(not re.search(r"\b\d+(\.\d+)?R\b", text), f"{vp_name}/{name}: no bare 'R' jargon in the ORB card")
                    probe = card.evaluate(PROBE)
                    reds = sorted({f"{c['c']} {c['t']}" for c in probe["colors"] if is_reddish(c["c"])})
                    if alarm_ok:
                        check(bool(reds), f"{vp_name}/{name}: the alert is shown in the alarm color")
                    else:
                        check(not reds, f"{vp_name}/{name}: no red/terracotta in the ORB card {reds[:4]}")
                    check(not probe["over"], f"{vp_name}/{name}: nothing overflows the ORB card {probe['over'][:3]}")
                    sw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                    check(sw[0] <= sw[1], f"{vp_name}/{name}: no horizontal page scroll {sw}")
                    n_cards = page.locator("article").count()
                    titles = page.locator("article h3").all_inner_texts()
                    check(n_cards >= 4 and all(t.strip() for t in titles), f"{vp_name}/{name}: {n_cards} strategy cards render {titles}")
                    page.screenshot(path=str(OUT / f"{vp_name}_{name}_page.png"), full_page=True)
                    card.screenshot(path=str(OUT / f"{vp_name}_{name}_orb_card.png"))
                    if name == "off":
                        check("Switched off" in text, f"{vp_name}/off: short 'Switched off' status chip")
                    if name == "shadow_pick":
                        check("Watching only (shadow)" in text, f"{vp_name}/shadow_pick: chip says watching only")
                    check("skipped all" not in text, f"{vp_name}/{name}: ORB note does not call a decision a skipped chance")
                    if name == "live_trade":
                        hold = page.locator("[data-testid=holding-row-APP]").inner_text()
                        check("Tesla" not in hold and "Opening Range Breakout" in hold,
                              f"{vp_name}/live_trade: the holding row names ORB, not the Tesla plan")
                    if name == "orphan_alert":
                        btn = page.locator("[data-testid=orb-resolve-orphan]")
                        check(btn.count() == 1, f"{vp_name}: resolve-orphan button is shown")
                        btn.click()
                        page.wait_for_timeout(300)
                        armed = card.inner_text()
                        check("Tap again to confirm" in armed and "Confirm: clear APP" in armed,
                              f"{vp_name}: first tap only arms the button (asks to confirm)")
                        card.screenshot(path=str(OUT / f"{vp_name}_orphan_armed_orb_card.png"))
                        btn.click()
                        page.wait_for_function("() => document.body.innerText.includes('Alpaca still holds')", timeout=15000)
                        check(True, f"{vp_name}: second tap while Alpaca still holds the shares is refused with the reason")
                        card.screenshot(path=str(OUT / f"{vp_name}_orphan_refused_orb_card.png"))
                        post(base, "/__dev/orb_state/orphan_closed_at_alpaca")
                        page.wait_for_timeout(500)
                        btn.click()
                        btn.click()
                        page.wait_for_function("() => !document.querySelector('[data-testid=orb-alert]')", timeout=15000)
                        check(True, f"{vp_name}: after closing at Alpaca, confirm clears the alert live")
                        card.screenshot(path=str(OUT / f"{vp_name}_orphan_resolved_orb_card.png"))
                    if name == "live_trade" and vp_name == "desktop":
                        page.locator("[data-testid=pro-words-toggle]").click()
                        page.wait_for_timeout(400)
                        card.screenshot(path=str(OUT / f"{vp_name}_{name}_pro_words_orb_card.png"))
                        page.locator("[data-testid=pro-words-toggle]").click()
                check(navs[0] == loads, f"{vp_name}: page never reloaded ({navs[0] - loads} navigations)")
                check(ws_frames[0] > len(STATES), f"{vp_name}: websocket delivered {ws_frames[0]} frames")
                # the refused resolve (409, Alpaca still held the shares) is logged by the browser on purpose
                errs = [e for e in console_errors if "favicon" not in e and "status of 409" not in e]
                check(not errs, f"{vp_name}: no console errors {errs[:5]}")
                ctx.close()
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
