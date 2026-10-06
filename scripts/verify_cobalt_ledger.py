#!/usr/bin/env python3
"""Responsive visual verification for the Cobalt Ledger dashboard."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import verify_compact_dashboard as compact  # noqa: E402
import verify_ui_redesign as base  # noqa: E402

OUT = ROOT / "frontend" / "out"
SHOTS = ROOT / ".local_qa_screenshots" / "cobalt_ledger"
PORT = 3025
FRAME = json.loads((ROOT / "docs/compact_dashboard/fixtures/live_one.json").read_text())
TODAY_TRADES = compact.LIVE_TRADES


def main() -> int:
    if not (OUT / "index.html").exists():
        print("Run the frontend production build first.")
        return 2

    SHOTS.mkdir(parents=True, exist_ok=True)
    server = compact.serve(OUT, PORT)
    failures: list[str] = []
    checks = 0

    def check(condition: bool, message: str) -> None:
        nonlocal checks
        if condition:
            checks += 1
            print(f"  ok   {message}")
        else:
            failures.append(message)
            print(f"  FAIL {message}")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for name, width, height in (
                ("desktop", 1440, 900),
                ("mobile", 390, 844),
                ("narrow", 320, 760),
            ):
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    reduced_motion="reduce",
                )
                page = context.new_page()
                page.clock.set_fixed_time(compact.FIXED_NOW)
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(f"pageerror {error}"))
                page.on(
                    "console",
                    lambda message: errors.append(f"console {message.text}")
                    if message.type == "error"
                    else None,
                )

                def on_socket(socket) -> None:
                    socket.send(json.dumps(FRAME))

                def trades(route) -> None:
                    body = (
                        compact.ledger_all(TODAY_TRADES)
                        if "range=all" in route.request.url
                        else base.trades_response(TODAY_TRADES)
                    )
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(body),
                    )

                page.route_web_socket(re.compile(r".*/ws/ui$"), on_socket)
                page.route(re.compile(r".*/api/trades.*"), trades)
                page.route(
                    re.compile(r".*/api/account$"),
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(FRAME["account"]),
                    ),
                )
                page.route(
                    re.compile(r".*/health$"),
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(base.HEALTH_RESPONSE),
                    ),
                )

                page.goto(
                    f"http://127.0.0.1:{PORT}/",
                    wait_until="networkidle",
                    timeout=20_000,
                )
                page.locator("[data-testid=performance-panel]").wait_for(timeout=15_000)
                page.evaluate("() => document.fonts.ready")

                check(
                    page.get_by_text("Cobalt Ledger", exact=True).first.is_visible(),
                    f"{name} shows the Cobalt Ledger product name",
                )
                check(
                    page.locator("[data-testid=performance-panel]").is_visible(),
                    f"{name} shows account performance",
                )
                check(
                    page.get_by_text("+1.35%", exact=True).is_visible(),
                    f"{name} shows the open holding percentage",
                )
                check(
                    page.evaluate(
                        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
                    ),
                    f"{name} has no horizontal overflow",
                )
                short_buttons = page.locator("button:visible").evaluate_all(
                    "(buttons) => buttons.filter((button) => button.getBoundingClientRect().height < 43.5).map((button) => button.textContent.trim())"
                )
                check(
                    short_buttons == [],
                    f"{name} keeps every visible button at least 44 pixels tall",
                )
                page.screenshot(
                    path=str(SHOTS / f"{name}_today.png"),
                    full_page=True,
                )

                page.get_by_role("button", name="Week", exact=True).first.click()
                page.get_by_role("button", name="History", exact=True).click()
                page.locator("[data-testid=history-explorer]").wait_for()
                check(
                    page.get_by_text("Detailed account history", exact=True).is_visible(),
                    f"{name} opens detailed history",
                )
                explorer = page.locator("[data-testid=history-explorer]")
                explorer.get_by_role("button", name="Plans", exact=True).click()
                check(
                    explorer.locator("li").count() > 0,
                    f"{name} shows the weekly plan breakdown",
                )
                explorer.get_by_role("button", name="Trades", exact=True).click()
                trade_button = explorer.get_by_role(
                    "button",
                    name=re.compile(r"Open details for"),
                ).first
                check(trade_button.is_visible(), f"{name} shows detailed trade rows")
                trade_button.click()
                check(
                    page.get_by_role("dialog").is_visible(),
                    f"{name} opens a trade detail dialog",
                )
                page.keyboard.press("Escape")
                page.screenshot(
                    path=str(SHOTS / f"{name}_history.png"),
                    full_page=True,
                )
                check(errors == [], f"{name} has no browser errors")
                context.close()
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)

    check(compact.port_free(PORT), f"port {PORT} is released")
    print(f"\n{checks} passed, {len(failures)} failed")
    for failure in failures:
        print(f"  FAIL {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
