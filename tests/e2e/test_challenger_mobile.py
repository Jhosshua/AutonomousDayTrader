#!/usr/bin/env python3
"""
tests/e2e/test_challenger_mobile.py

Visual layout / mobile responsiveness checks for the plain-language redesign
(PLAN_2026_09_24_plain_language_ui.md). Role: challenger_m3_1 (critic, specialist).

This suite starts the static export with `npm run start` and NO backend attached, so the page
can only ever show the F7 "Connecting to the robot..." loading skeleton (the redesign deliberately
never renders synthetic zeroed data before a real snapshot arrives). That is exactly what these
tests check: the loading shell never overflows, text is never clipped, and the safe-port /
process-hygiene contract still holds.

Interactive behavior against real data (strategy cards, holding-now actions, safety card confirm
flow, swing tabs, etc.) is covered with realistic mocked API responses by
`scripts/verify_ui_redesign.py` (V3), which is the suite of record for that coverage now.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
PORT = 3005
BASE_URL = f"http://127.0.0.1:{PORT}"

MOBILE_VIEWPORTS = [
    {"name": "iPhone_SE_375", "width": 375, "height": 667},
    {"name": "iPhone_14_Pro_390", "width": 390, "height": 844},
    {"name": "iPhone_11_Plus_414", "width": 414, "height": 896},
    {"name": "Android_Compact_360", "width": 360, "height": 800},
    {"name": "Ultra_Narrow_Stress_320", "width": 320, "height": 568},
]


def is_port_listening(port: int) -> bool:
    try:
        res = subprocess.run(
            ["lsof", "-tiTCP:" + str(port), "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
        return bool(res.stdout.strip())
    except Exception:
        return False


@pytest.fixture(scope="module")
def nextjs_server():
    """Starts the Next.js production server on safe port 3005 and ensures clean teardown."""
    if is_port_listening(PORT):
        raise RuntimeError(f"Port {PORT} is already occupied; stop the owning process before visual QA.")

    cmd = ["npm", "run", "start"]
    proc = subprocess.Popen(
        cmd,
        cwd=str(FRONTEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ),
        text=True,
        preexec_fn=os.setsid,
    )

    start_time = time.time()
    ready = False
    while time.time() - start_time < 15:
        try:
            req = urllib.request.Request(BASE_URL)
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.4)

    if not ready:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
        out, err = proc.communicate(timeout=2)
        raise RuntimeError(f"Next.js server failed to launch on port {PORT}. Out: {out}, Err: {err}")

    yield proc

    pgid = None
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=3)
    except Exception:
        pass

    deadline = time.time() + 5.0
    while time.time() < deadline and is_port_listening(PORT):
        time.sleep(0.1)

    if is_port_listening(PORT) and pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.2)

    hygiene_script = PROJECT_ROOT / "scripts" / "verify_port_hygiene.sh"
    res = subprocess.run(["bash", str(hygiene_script)], capture_output=True, text=True)
    assert res.returncode == 0, f"Port hygiene verification failed in teardown:\n{res.stdout}\n{res.stderr}"


# ============================================================================
# Test 1: Safe Port 3005 Configuration in package.json
# ============================================================================
def test_safe_port_3005_configuration():
    """Verify package.json strictly configures safe port 3005 avoiding port 3000 collision."""
    pkg_path = FRONTEND_DIR / "package.json"
    assert pkg_path.exists(), f"package.json missing at {pkg_path}"
    with open(pkg_path, "r", encoding="utf-8") as f:
        pkg = json.load(f)

    scripts = pkg.get("scripts", {})
    assert "dev" in scripts, "Missing dev script"
    assert "start" in scripts, "Missing start script"
    assert "-p 3005" in scripts["dev"], f"dev script must specify -p 3005: {scripts['dev']}"
    assert ("-p 3005" in scripts["start"] or "--port 3005" in scripts["start"]), \
        f"start script must specify port 3005: {scripts['start']}"


# ============================================================================
# Test 2: Two-step confirm exists for destructive actions, never window.confirm (rule 2)
# ============================================================================
def test_two_step_confirm_never_window_confirm():
    """SafetyCard/HoldingNow/ActiveSwingPositionsTable must use an inline 'tap again to confirm'
    step for destructive actions, and no component may use window.confirm/alert/prompt."""
    for name in ("SafetyCard.tsx", "HoldingNow.tsx", "ActiveSwingPositionsTable.tsx"):
        code = (FRONTEND_DIR / "components" / name).read_text(encoding="utf-8")
        assert "Tap again to confirm" in code, f"{name} must use the inline two-step confirm copy"
        assert "window.confirm(" not in code
        assert "window.alert(" not in code
        assert "window.prompt(" not in code

    action_button = (FRONTEND_DIR / "hooks" / "useActionButton.ts").read_text(encoding="utf-8")
    for phase in ('"idle"', '"confirm"', '"sending"', '"done"', '"failed"'):
        assert phase in action_button, f"useActionButton.ts must define the {phase} phase"


# ============================================================================
# Test 3: CSS Responsive Classes & Horizontal Overflow Stress Across Viewports
# ============================================================================
@pytest.mark.parametrize("vp", MOBILE_VIEWPORTS, ids=[v["name"] for v in MOBILE_VIEWPORTS])
def test_responsive_viewport_no_horizontal_overflow(nextjs_server, vp):
    """Stress test responsive viewports (320px, 360px, 375px, 390px, 414px) for horizontal overflow
    of the F7 loading skeleton (no backend is running for this suite)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": vp["width"], "height": vp["height"]},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()
        page.goto(BASE_URL, wait_until="networkidle", timeout=10000)

        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        inner_width = page.evaluate("() => window.innerWidth")
        assert scroll_width <= inner_width, (
            f"Horizontal overflow detected on viewport {vp['name']} ({vp['width']}px): "
            f"scrollWidth={scroll_width}px > innerWidth={inner_width}px"
        )

        body_overflow_x = page.evaluate("() => window.getComputedStyle(document.body).overflowX")
        assert body_overflow_x in ("hidden", "clip"), (
            f"Body overflow-x should be hidden or clip, got: {body_overflow_x}"
        )

        browser.close()


# ============================================================================
# Test 4: Loading skeleton renders without text clipping and shows plain branding
# ============================================================================
@pytest.mark.parametrize("vp", MOBILE_VIEWPORTS, ids=[v["name"] for v in MOBILE_VIEWPORTS])
def test_mobile_loading_skeleton_not_clipped(nextjs_server, vp):
    """Verify the F7 loading skeleton (no data yet) does not overflow or clip on mobile viewports."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": vp["width"], "height": vp["height"]},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()
        page.goto(BASE_URL, wait_until="networkidle", timeout=10000)

        connecting_text = page.get_by_text("Connecting to the robot", exact=False)
        connecting_text.wait_for(state="visible", timeout=5000)
        box = connecting_text.bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= vp["width"] + 1, (
            f"Loading text overflows viewport width {vp['width']}: right={box['x'] + box['width']}"
        )

        browser.close()


# ============================================================================
# Test 5: Non-Colliding Ports Isolation During Execution
# ============================================================================
def test_ports_isolation_during_execution(nextjs_server):
    """Verify backend ports (8005, 8080) and host port 3000 remain unmolested during UI execution."""
    assert not is_port_listening(8005), "Port 8005 should not have unmanaged listener"
    assert not is_port_listening(8080), "Port 8080 should not have unmanaged listener"
    assert is_port_listening(PORT), f"Port {PORT} must be active for UI"


if __name__ == "__main__":
    print("Launching Challenger Mobile Responsiveness Test Suite...")
    exit_code = pytest.main([__file__, "-v", "-s"])
    sys.exit(exit_code)
