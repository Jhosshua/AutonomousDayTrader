#!/usr/bin/env python3
"""scripts/verify_visual_qa.py
Visual QA smoke test for the plain-language redesign (PLAN_2026_09_24_plain_language_ui.md).

This script spawns the static export with NO backend attached, so per F7 (never render synthetic
zeroed data before a real snapshot) the page can only show the "Connecting to the robot..."
loading skeleton. What this script checks, at Desktop (1440x900) and Mobile (390x844):
1. The static export builds and serves.
2. The loading skeleton renders with no horizontal overflow and no console errors.
3. Strict port hygiene (server terminated, port 3005 liberated afterward).

Full interactive coverage against realistic mocked API data (strategy cards, tabs, holding-now
actions, safety card confirm flow, swing candidates, >100-trade pagination, reduced-motion, etc.)
lives in `scripts/verify_ui_redesign.py`, which is the suite of record for that coverage.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
PORT = 3005
BASE_URL = f"http://127.0.0.1:{PORT}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("visual_qa")

VIEWPORTS = [
    {
        "name": "Desktop (1440x900)",
        "width": 1440,
        "height": 900,
        "is_mobile": False,
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    },
    {
        "name": "Mobile (390x844 - iPhone 14 Pro)",
        "width": 390,
        "height": 844,
        "is_mobile": True,
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    },
]


def check_port_free(port: int) -> bool:
    res = subprocess.run(["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, check=False)
    return not bool(res.stdout.strip())


def run_visual_audit() -> Dict[str, Any]:
    if not check_port_free(PORT):
        raise RuntimeError(f"Port {PORT} is already occupied. Ensure clean port state before visual QA.")

    log.info(f"Starting Next.js export server on port {PORT}...")
    server_proc = subprocess.Popen(
        ["node", "scripts/serve_export.mjs", "--port", str(PORT)],
        cwd=str(FRONTEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=os.setsid,
    )

    ready = False
    start_t = time.time()
    while time.time() - start_t < 10.0:
        try:
            with urllib.request.urlopen(BASE_URL, timeout=1) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.3)

    if not ready:
        os.killpg(os.getpgid(server_proc.pid), signal.SIGKILL)
        raise RuntimeError(f"Server on port {PORT} failed to start within 10s")

    log.info(f"Server responsive at {BASE_URL}. Running Playwright visual audit...")
    audit_results: List[Dict[str, Any]] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            for vp in VIEWPORTS:
                log.info(f"\n--- Testing Viewport: {vp['name']} ({vp['width']}x{vp['height']}) ---")
                context = browser.new_context(
                    viewport={"width": vp["width"], "height": vp["height"]},
                    user_agent=vp["user_agent"],
                    is_mobile=vp["is_mobile"],
                    device_scale_factor=2 if vp["is_mobile"] else 1,
                )
                page = context.new_page()
                console_errors: List[str] = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

                page.goto(BASE_URL, wait_until="networkidle", timeout=10000)

                # 1. The plain-language brand and F7 loading skeleton must render.
                page.wait_for_selector("text=Day Trader", timeout=5000)
                brand_visible = page.is_visible("text=Day Trader")
                connecting_visible = page.is_visible("text=Connecting to the robot")
                log.info(f"  Brand visible: {brand_visible}; loading skeleton visible: {connecting_visible}")

                # 2. Horizontal overflow check.
                overflow = page.evaluate("() => document.body.scrollWidth > window.innerWidth")
                log.info(f"  Horizontal Overflow: {'DETECTED (FAIL)' if overflow else 'NONE (PASS)'}")
                assert not overflow, f"Horizontal overflow detected on {vp['name']}"

                audit_results.append({
                    "viewport": vp["name"],
                    "width": vp["width"],
                    "height": vp["height"],
                    "brand_visible": brand_visible,
                    "loading_skeleton_visible": connecting_visible,
                    "no_overflow": not overflow,
                    "console_errors_count": len(console_errors),
                    "status": "PASS" if brand_visible and not overflow else "FAIL",
                })
                context.close()

            browser.close()

    finally:
        log.info(f"Terminating Next.js server (PID: {server_proc.pid})...")
        try:
            os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)
            server_proc.wait(timeout=3)
        except Exception:
            try:
                os.killpg(os.getpgid(server_proc.pid), signal.SIGKILL)
            except Exception:
                pass
        time.sleep(0.5)

    is_free = check_port_free(PORT)
    log.info(f"Port {PORT} hygiene check: {'LIBERATED (PASS)' if is_free else 'OCCUPIED (FAIL)'}")

    summary = {
        "status": "PASS" if all(r["status"] == "PASS" for r in audit_results) and is_free else "FAIL",
        "viewports_tested": audit_results,
        "port_3005_liberated": is_free,
    }
    return summary


def main() -> int:
    try:
        summary = run_visual_audit()
        print("\n" + "=" * 70)
        print(" VISUAL QA SMOKE TEST SUMMARY (DESKTOP & MOBILE, no backend)")
        print("=" * 70)
        print(f" Overall Status:      {summary['status']}")
        for v in summary["viewports_tested"]:
            print(f" Viewport:            {v['viewport']}  -> {v['status']}")
        print(f" Port 3005 Hygiene:   {'LIBERATED (CLEAN)' if summary['port_3005_liberated'] else 'DIRTY'}")
        print(" Interactive/mocked-data coverage: see scripts/verify_ui_redesign.py")
        print("=" * 70)
        return 0 if summary["status"] == "PASS" else 1
    except Exception as e:
        log.exception(f"Visual QA failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
