#!/usr/bin/env python3
"""
tests/e2e/test_challenger_mobile.py

Adversarial Visual Layout & Mobile Responsiveness Challenger Test Suite
Role: challenger_m3_1 (critic, specialist)

Stress tests:
1. CSS styling and responsive classes across mobile viewports (320px, 360px, 375px, 390px, 414px)
   ensuring NO horizontal page overflow (scrollWidth <= innerWidth) and no unintended text clipping.
2. Framer Motion drawer spring configuration (stiffness: 350, damping: 32) and presence of expandable modal elements.
3. LiveChart SVG bracket levels (Stop Loss, Entry, TP1, TP2, Laser price pulse).
4. Interactive modal flows: ActivePositionTray expansion, Flatten confirmation prompt, Strategy Inspector sheet.
5. Safe UI port 3005 configuration in package.json & host isolation.
6. Process hygiene and clean port release (zero lingering processes).
"""

from __future__ import annotations

import json
import os
import re
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
    # Never terminate an unrelated process that happens to own the test port.
    if is_port_listening(PORT):
        raise RuntimeError(f"Port {PORT} is already occupied; stop the owning process before visual QA.")

    # The production artifact is a static Next export in frontend/out, served
    # by the same export server used by the single-service deployment.
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

    # Wait for server to be responsive
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

    # Teardown: terminate cleanly and ensure port liberation
    pgid = None
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=3)
    except Exception:
        pass

    # Wait up to 5 seconds for port 3005 to be completely liberated
    deadline = time.time() + 5.0
    while time.time() < deadline and is_port_listening(PORT):
        time.sleep(0.1)

    # If still listening, escalate to SIGKILL
    if is_port_listening(PORT) and pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.2)

    # Enforce process hygiene post-teardown
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
# Test 2: Framer Motion Drawer Spring Physics & Configuration Code Audit
# ============================================================================
def test_framer_motion_drawer_spring_configuration():
    """Verify spring physics (stiffness: 350, damping: 32) and drawer elements in code."""
    drawer_file = FRONTEND_DIR / "components" / "ActivePositionTray.tsx"
    assert drawer_file.exists(), f"ActivePositionTray.tsx missing at {drawer_file}"
    code = drawer_file.read_text(encoding="utf-8")

    # Verify spring transition parameters
    assert "stiffness: 350" in code, "ActivePositionTray must specify stiffness: 350"
    assert "damping: 32" in code, "ActivePositionTray must specify damping: 32"
    assert 'type: "spring"' in code or "type: 'spring'" in code, "ActivePositionTray must use spring physics"

    # Verify gesture dismissal and drag constraints
    assert 'drag="y"' in code or "drag='y'" in code, "ActivePositionTray must support drag='y' gesture"
    assert "onDragEnd={handleDragEnd}" in code, "ActivePositionTray must handle drag end dismissal"

    # Verify expandable modal sheet elements
    assert "LiveChart" in code, "ActivePositionTray must embed LiveChart component"
    assert "ManualControls" in code, "ActivePositionTray must embed ManualControls component"
    assert "ExecutionLog" in code, "ActivePositionTray must embed ExecutionLog component"
    assert "ChevronDown" in code, "ActivePositionTray must have dismiss ChevronDown button"


# ============================================================================
# Test 3: CSS Responsive Classes & Horizontal Overflow Stress Across Viewports
# ============================================================================
@pytest.mark.parametrize("vp", MOBILE_VIEWPORTS, ids=[v["name"] for v in MOBILE_VIEWPORTS])
def test_responsive_viewport_no_horizontal_overflow(nextjs_server, vp):
    """Stress test responsive viewports (320px, 360px, 375px, 390px, 414px) for horizontal overflow."""
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

        # 1. Document-level overflow check
        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        client_width = page.evaluate("() => document.documentElement.clientWidth")
        inner_width = page.evaluate("() => window.innerWidth")

        assert scroll_width <= inner_width, (
            f"Horizontal overflow detected on viewport {vp['name']} ({vp['width']}px): "
            f"scrollWidth={scroll_width}px > innerWidth={inner_width}px"
        )
        assert client_width <= inner_width, (
            f"Client width exceeds inner width: clientWidth={client_width}px > innerWidth={inner_width}px"
        )

        # 2. Check for child elements overflowing horizontally (excluding horizontal scroll containers)
        overflow_elements = page.evaluate("""
            () => {
                const overflowing = [];
                const innerW = window.innerWidth;
                const all = document.querySelectorAll('header, section, div, main, nav, p, span, h1, h2, h3');
                for (const el of all) {
                    // Skip elements inside intentionally horizontal scrollable containers or clipped ambient background
                    if (el.closest('.overflow-x-auto') || el.closest('.overflow-x-scroll') || el.closest('.overflow-hidden') || el.closest('[aria-hidden="true"]')) {
                        continue;
                    }
                    const rect = el.getBoundingClientRect();
                    // Allow 1px tolerance for subpixel antialiasing/rounding
                    if (rect.right > innerW + 1 && rect.width > 0) {
                        overflowing.push({
                            tag: el.tagName,
                            className: el.className,
                            right: rect.right,
                            width: rect.width,
                            innerW: innerW
                        });
                    }
                }
                return overflowing;
            }
        """)

        assert len(overflow_elements) == 0, (
            f"Found {len(overflow_elements)} elements overflowing on {vp['name']}: {overflow_elements[:3]}"
        )

        # 3. Check body and main overflow-x styles
        body_overflow_x = page.evaluate("() => window.getComputedStyle(document.body).overflowX")
        assert body_overflow_x in ("hidden", "clip"), (
            f"Body overflow-x should be hidden or clip, got: {body_overflow_x}"
        )

        browser.close()


# ============================================================================
# Test 4: Mobile Text Clipping and Truncation Integrity
# ============================================================================
@pytest.mark.parametrize("vp", MOBILE_VIEWPORTS, ids=[v["name"] for v in MOBILE_VIEWPORTS])
def test_mobile_text_clipping_and_wrapping(nextjs_server, vp):
    """Verify that text elements do not overflow or clip destructively on mobile viewports."""
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

        # Wait for the server-rendered dashboard shell and then select the actual
        # currency value, avoiding a race with the initial client hydration.
        page.get_by_text("Portfolio Equity", exact=True).wait_for(state="visible", timeout=5000)
        equity_text = page.locator("main").get_by_text(re.compile(r"^\$[0-9,]+\.\d{2}$")).first
        equity_text.wait_for(state="visible", timeout=5000)

        # Check telemetry boxes fit inside screen
        telemetry_boxes = page.locator("section.px-4 .grid > div")
        count = telemetry_boxes.count()
        assert count == 4, f"Expected 4 risk telemetry cards, found {count}"

        for i in range(count):
            box = telemetry_boxes.nth(i)
            box_rect = box.bounding_box()
            assert box_rect is not None, f"Telemetry box {i} bounding box missing"
            assert box_rect["x"] >= 0, f"Box {i} x position negative: {box_rect['x']}"
            assert box_rect["x"] + box_rect["width"] <= vp["width"] + 1, (
                f"Box {i} overflows viewport width {vp['width']}: right={box_rect['x'] + box_rect['width']}"
            )

        browser.close()


# ============================================================================
# Test 5: Interactive ActivePositionTray Expansion & Modal Sheet Verification
# ============================================================================
def test_active_position_tray_expansion_and_modal_elements(nextjs_server):
    """Test interactive ActivePositionTray expansion into full modal sheet on 375px mobile viewport."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 375, "height": 667},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()
        page.goto(BASE_URL, wait_until="networkidle", timeout=10000)

        # 1. Docked Active Position Bar verification
        docked_tray = page.locator("div.fixed.bottom-4")
        assert docked_tray.is_visible(), "Docked ActivePositionTray must be visible at bottom of page"

        # Check docked bar position info
        assert docked_tray.locator("text=Active").count() > 0 or docked_tray.locator("text=NVDA").count() > 0 or docked_tray.locator("text=No Active Position").count() > 0

        # Check action buttons on docked tray
        breakeven_btn = docked_tray.locator("button[title='Lock Breakeven Stop']")
        flatten_btn = docked_tray.locator("button[title='Emergency Flatten']")
        if breakeven_btn.count() > 0:
            assert breakeven_btn.is_visible()
            assert flatten_btn.is_visible()

        # 2. Expand ActivePositionTray on click (click left ticker badge or trade ticker)
        expand_trigger = docked_tray.locator("span:has-text('NVDA'), span:has-text('Active'), span:has-text('No Active Position')").first
        assert expand_trigger.is_visible(), "Docked tray trade trigger must be visible"
        expand_trigger.click()

        # 3. Verify Full-Screen Modal Sheet is now visible
        modal_header = page.locator("text=Active Primary Trade")
        modal_header.wait_for(state="visible", timeout=3000)
        assert modal_header.is_visible(), "Expanded modal sheet header 'Active Primary Trade' must be visible"

        # An unconnected/flat backend must remain honest: the export should
        # show the empty-state panel rather than inventing a position or
        # bracket levels just to make the visual test look populated.
        if page.locator("text=No Open Position").is_visible():
            assert page.locator("text=Awaiting signal breakout or position entry...").is_visible()
            assert page.locator("svg text:has-text('TP2')").count() == 0
            page.locator("button:has(svg.lucide-chevron-down)").click()
            browser.close()
            return

        # Verify Drag handle bar
        drag_handle = page.locator(".cursor-grab")
        assert drag_handle.is_visible(), "Modal sheet drag handle must be present"

        # 4. Verify LiveChart SVG and Bracket Levels in Modal
        live_chart_svg = page.locator("svg")
        assert live_chart_svg.count() >= 1, "LiveChart SVG must be rendered"

        # Verify bracket lines in LiveChart SVG
        tp2_text = page.locator("svg text:has-text('TP2')")
        tp1_text = page.locator("svg text:has-text('TP1')")
        ent_text = page.locator("svg text:has-text('ENT')")
        stp_text = page.locator("svg text:has-text('STP')")

        assert tp2_text.is_visible(), "Take Profit 2 (TP2) bracket line must be visible"
        assert tp1_text.is_visible(), "Take Profit 1 (TP1) bracket line must be visible"
        assert ent_text.is_visible(), "Entry (ENT) bracket line must be visible"
        assert stp_text.is_visible(), "Stop Loss (STP) bracket line must be visible"

        # 5. Verify Manual Intervention Controls
        lock_be_modal_btn = page.locator("button:has-text('Lock Breakeven')")
        trail_modal_btn = page.locator("button:has-text('Trail +50% Gain')")
        flatten_modal_btn = page.locator("button:has-text('Flatten NVDA')")

        assert lock_be_modal_btn.is_visible(), "Lock Breakeven button must be visible in modal"
        assert trail_modal_btn.is_visible(), "Trail +50% Gain button must be visible in modal"
        assert flatten_modal_btn.is_visible(), "Flatten button must be visible in modal"

        # Test Flatten confirmation dialog interaction
        flatten_modal_btn.click()
        page.wait_for_timeout(150)
        confirm_dialog = page.locator("text=Confirm immediate market exit")
        assert confirm_dialog.is_visible(), "Safety confirmation prompt must appear when Flatten is clicked"

        cancel_btn = page.locator("button:has-text('Cancel')").first
        assert cancel_btn.is_visible(), "Cancel button must be visible in confirmation prompt"
        cancel_btn.click()
        page.wait_for_timeout(150)
        assert not confirm_dialog.is_visible(), "Confirmation prompt must disappear on Cancel"

        # 6. Verify ExecutionLog inside modal
        exec_log_title = page.locator("text=Execution Audit Log")
        assert exec_log_title.count() >= 1, "Execution Audit Log must be present"

        # 7. Dismiss Modal Sheet via ChevronDown button
        close_btn = page.locator("button:has(svg.lucide-chevron-down)")
        assert close_btn.is_visible(), "ChevronDown close button must be visible"
        close_btn.click()
        modal_header.wait_for(state="detached", timeout=3000)
        assert page.locator("text=Active Primary Trade").count() == 0, "Modal sheet should be dismissed after clicking close"

        browser.close()


# ============================================================================
# Test 6: Strategy Carousel Snap-Scroll & Strategy Inspector Modal
# ============================================================================
def test_strategy_carousel_and_inspector_modal(nextjs_server):
    """Test StrategyCarousel cards and Strategy Inspector modal sheet on mobile."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 375, "height": 667},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()
        page.goto(BASE_URL, wait_until="networkidle", timeout=10000)

        # 1. Verify Strategy Carousel section
        carousel_heading = page.locator("text=Trading Strategies")
        assert carousel_heading.is_visible(), "Trading Strategies header must be visible"

        # 2. Verify all 4 Strategy Cards are present in carousel
        strategies_expected = [
            "Opening Range Breakout",
            "VWAP Trend Pullback",
            "Catalyst News Momentum",
            "Statistical Mean Reversion",
        ]
        for name in strategies_expected:
            card_title = page.locator(f"h3:has-text('{name}')")
            assert card_title.count() > 0, f"Strategy card for '{name}' must exist"

        # 3. Click first strategy card to open Inspector
        first_card = page.locator("div.snap-center").first
        first_card.click()
        page.wait_for_timeout(300)

        # 4. Verify Strategy Inspector Sheet
        inspector_sheet = page.locator("div.fixed.inset-0")
        inspector_title = inspector_sheet.locator("text=Strategy Inspector")
        assert inspector_title.is_visible(), "Strategy Inspector modal must open on card click"

        # Check inspector content scoped to modal sheet
        assert inspector_sheet.locator("text=Win Rate").is_visible(), "Win Rate stat must be displayed"
        assert inspector_sheet.locator("text=Sharpe Ratio").is_visible(), "Sharpe Ratio stat must be displayed"
        assert inspector_sheet.locator("text=Risk Allocation").is_visible(), "Risk Allocation rule must be displayed"
        assert inspector_sheet.locator("text=Exit Protocols").is_visible(), "Exit Protocols rule must be displayed"

        # 5. Close Inspector Sheet
        close_inspector_btn = inspector_sheet.locator("button:has-text('Close Inspector')")
        assert close_inspector_btn.is_visible()
        close_inspector_btn.click()
        page.locator("text=Strategy Inspector").wait_for(state="detached", timeout=3000)
        assert page.locator("text=Strategy Inspector").count() == 0, "Strategy Inspector must close cleanly"

        browser.close()


# ============================================================================
# Test 7: Desktop Viewport (1440x900) Layout, Component Inspection & No Overflow
# ============================================================================
def test_desktop_viewport_1440x900_layout_and_no_overflow(nextjs_server):
    """Verify visual UI layout and absence of horizontal overflow on desktop (1440x900)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
        )
        page = context.new_page()
        page.goto(BASE_URL, wait_until="networkidle", timeout=10000)

        # 1. Document-level overflow check on 1440x900
        scroll_width = page.evaluate("() => document.documentElement.scrollWidth")
        client_width = page.evaluate("() => document.documentElement.clientWidth")
        inner_width = page.evaluate("() => window.innerWidth")

        assert scroll_width <= inner_width, (
            f"Desktop horizontal overflow detected: scrollWidth={scroll_width}px > innerWidth={inner_width}px"
        )
        assert client_width <= inner_width, (
            f"Desktop clientWidth exceeds innerWidth: clientWidth={client_width}px > innerWidth={inner_width}px"
        )

        # 2. Check for child element overflow on desktop
        overflow_elements = page.evaluate("""
            () => {
                const overflowing = [];
                const innerW = window.innerWidth;
                const all = document.querySelectorAll('header, section, div, main, nav, p, span, h1, h2, h3');
                for (const el of all) {
                    if (el.closest('.overflow-x-auto') || el.closest('.overflow-x-scroll') || el.closest('.overflow-hidden') || el.closest('[aria-hidden="true"]')) {
                        continue;
                    }
                    const rect = el.getBoundingClientRect();
                    if (rect.right > innerW + 1 && rect.width > 0) {
                        overflowing.push({
                            tag: el.tagName,
                            className: el.className,
                            right: rect.right,
                            width: rect.width,
                            innerW: innerW
                        });
                    }
                }
                return overflowing;
            }
        """)
        assert len(overflow_elements) == 0, f"Found overflowing elements on desktop: {overflow_elements[:3]}"

        # 3. Verify Telemetry grid in 4-column layout on desktop
        page.get_by_text("Portfolio Equity", exact=True).wait_for(state="visible", timeout=5000)
        telemetry_boxes = page.locator("section.px-4 .grid > div")
        count = telemetry_boxes.count()
        assert count == 4, f"Expected 4 telemetry cards, found {count}"

        # In sm:grid-cols-4, each box width should be properly sized
        container_rect = page.locator("section.px-4 .grid").bounding_box()
        assert container_rect is not None
        for i in range(count):
            box = telemetry_boxes.nth(i)
            box_rect = box.bounding_box()
            assert box_rect is not None
            assert box_rect["width"] > 180, f"Box {i} unexpectedly narrow on desktop: {box_rect['width']}px"

        # 4. Verify Strategy Carousel header & cards
        assert page.locator("text=Trading Strategies").is_visible()
        strategies_expected = [
            "Opening Range Breakout",
            "VWAP Trend Pullback",
            "Catalyst News Momentum",
            "Statistical Mean Reversion",
        ]
        for name in strategies_expected:
            assert page.locator(f"h3:has-text('{name}')").count() > 0, f"Missing strategy card '{name}'"

        # 5. Verify Docked ActivePositionTray is centered and within max-w-xl
        docked_tray = page.locator("div.fixed.bottom-4")
        assert docked_tray.is_visible(), "Docked ActivePositionTray must be visible on desktop"
        tray_rect = docked_tray.bounding_box()
        assert tray_rect is not None
        assert tray_rect["width"] <= 576 + 32, f"Tray wider than max-w-xl on desktop: {tray_rect['width']}px"

        browser.close()


# ============================================================================
# Test 8: Desktop Viewport Interactive Modal, LiveChart & Strategy Inspector
# ============================================================================
def test_desktop_viewport_interactive_modal_and_inspector(nextjs_server):
    """Verify desktop interactive modals: ActivePositionTray modal sheet and Strategy Inspector."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
        )
        page = context.new_page()
        page.goto(BASE_URL, wait_until="networkidle", timeout=10000)

        # 1. Expand ActivePositionTray on desktop
        docked_tray = page.locator("div.fixed.bottom-4")
        expand_trigger = docked_tray.locator("span:has-text('NVDA'), span:has-text('Active'), span:has-text('No Active Position')").first
        expand_trigger.click()

        # 2. Verify modal sheet is centered on desktop (sm:items-center)
        modal_header = page.locator("text=Active Primary Trade")
        modal_header.wait_for(state="visible", timeout=3000)
        assert modal_header.is_visible()

        # Check modal sheet bounding box
        modal_sheet = page.locator("div.fixed.inset-0 .relative.max-w-2xl")
        sheet_rect = modal_sheet.bounding_box()
        assert sheet_rect is not None
        # Centered horizontally within 1440px
        center_x = sheet_rect["x"] + sheet_rect["width"] / 2.0
        assert abs(center_x - 720.0) < 50.0, f"Modal sheet not centered on desktop: center_x={center_x}"

        # 3. Dismiss modal
        close_btn = page.locator("button:has(svg.lucide-chevron-down)")
        close_btn.click()
        modal_header.wait_for(state="detached", timeout=3000)

        # 4. Open Strategy Inspector on desktop
        first_card = page.locator("div.snap-center").first
        first_card.click()
        page.wait_for_timeout(300)

        inspector_title = page.locator("text=Strategy Inspector")
        assert inspector_title.is_visible(), "Strategy Inspector modal must open on desktop"
        close_inspector_btn = page.locator("button:has-text('Close Inspector')")
        close_inspector_btn.click()
        page.locator("text=Strategy Inspector").wait_for(state="detached", timeout=3000)

        browser.close()


# ============================================================================
# Test 9: Non-Colliding Ports Isolation During Execution
# ============================================================================
def test_ports_isolation_during_execution(nextjs_server):
    """Verify backend ports (8005, 8080) and host port 3000 remain unmolested during UI execution."""
    # Port 8005 should be free (backend not running during this test)
    assert not is_port_listening(8005), "Port 8005 should not have unmanaged listener"
    # Port 8080 should be free
    assert not is_port_listening(8080), "Port 8080 should not have unmanaged listener"
    # Port 3005 is safely bound by our server
    assert is_port_listening(PORT), f"Port {PORT} must be active for UI"


if __name__ == "__main__":
    # If run directly as a script
    print("🚀 Launching Challenger Mobile Responsiveness Test Suite...")
    exit_code = pytest.main([__file__, "-v", "-s"])
    sys.exit(exit_code)

