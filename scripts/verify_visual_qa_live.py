#!/usr/bin/env python3
"""scripts/verify_visual_qa_live.py

Live-network-path visual QA for the plain-language redesign (PLAN_2026_09_24_plain_language_ui.md).

Unlike scripts/verify_ui_redesign.py (V3, which intercepts the WebSocket/HTTP calls at the
Playwright network layer with page.route / page.route_web_socket), this script spins up a real
aiohttp WebSocket + REST server on port 8005 and points the static export at it, exercising the
actual browser WebSocket client -> real server -> actual UI action-dispatch path end to end. It
is deliberately a smaller, complementary check, not a duplicate of V3's exhaustive coverage.

Never contacts the deployed Railway URL: this is a local mock backend only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from aiohttp import web
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
# Never write under .agents/ (off-limits scratch space owned by other tooling); use this repo's
# own gitignored local screenshot output directory instead.
SCREENSHOTS_DIR = PROJECT_ROOT / ".local_qa_screenshots" / "verify_visual_qa_live"
WEB_PORT = 3005
WS_PORT = 8005
BASE_URL = f"http://127.0.0.1:{WEB_PORT}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("visual_qa_live")

DISPATCHED_ACTIONS: List[Dict[str, Any]] = []

# Payload shape matches backend/app/main.py broadcast_ui_state and includes the B5 ranges/
# trading_day fields and B6 daily_pnl fields the redesign reads.
MOCK_PAYLOAD: Dict[str, Any] = {
    "type": "STATE_UPDATE",
    "timestamp": "2026-09-24T17:30:00Z",
    "account": {
        "equity": 50516.00, "cash": 24488.00, "buying_power": 97952.00,
        "daily_pnl": 516.00, "daily_pnl_pct": 1.03,
        "daily_drawdown": 0.0, "daily_drawdown_pct": 0.0,
        "is_circuit_broken": False, "risk_level": "NORMAL", "status": "ACTIVE",
        "daily_starting_equity": 50000.0,
    },
    "market_context": {"vix": 17.85, "vix_regime": "NORMAL", "time_phase": "TREND_CONTINUATION", "market_status": "OPEN", "sizing_multiplier": 1.0},
    "strategies": [
        {"id": "orb", "name": "Opening Range Breakout", "status": "ACTIVE", "daily_pnl": 280.0, "win_rate": 0.68, "trades_count": 3, "sharpe": 2.41,
         "window": {"state": "DONE_FOR_DAY", "headline": "Done for today", "can_open_now": False, "in_hours": False,
                    "hours": "9:30 AM - 11:30 AM", "ranges": [["09:30", "11:30"]], "trading_day": True,
                    "schedule_text": "Done for today.", "next_change_at": None, "blockers": [], "limits": [],
                    "market_text": "Market is rising: buys only.", "notes": [], "evaluated_at": "2026-09-24T13:10:00-04:00"},
         "decisions": {"signals_today": 3, "orders_today": 3, "blocked_today": 0, "top_block_reason": None, "top_block_text": None, "blocked_by_reason": {}}},
        {"id": "vwap_pullback", "name": "VWAP Trend Pullback", "status": "ACTIVE", "daily_pnl": 340.0, "win_rate": 0.62, "trades_count": 2, "sharpe": 1.88,
         "window": {"state": "CAN_TRADE", "headline": "Can open trades now", "can_open_now": True, "in_hours": True,
                    "hours": "9:30 AM - 3:45 PM", "ranges": [["09:30", "15:45"]], "trading_day": True,
                    "schedule_text": "In its trading hours.", "next_change_at": None, "blockers": [], "limits": [],
                    "market_text": "Market is rising: buys only.", "notes": [], "evaluated_at": "2026-09-24T13:10:00-04:00"},
         "decisions": {"signals_today": 2, "orders_today": 2, "blocked_today": 0, "top_block_reason": None, "top_block_text": None, "blocked_by_reason": {}}},
        {"id": "news_momentum", "name": "Catalyst News Momentum", "status": "STANDBY", "daily_pnl": 180.0, "win_rate": 0.71, "trades_count": 1, "sharpe": 2.15,
         "window": {"state": "CAN_TRADE", "headline": "Can open trades now", "can_open_now": True, "in_hours": True,
                    "hours": "9:30 AM - 3:45 PM", "ranges": [["09:30", "15:45"]], "trading_day": True,
                    "schedule_text": "In its trading hours.", "next_change_at": None, "blockers": [], "limits": [],
                    "market_text": "Market is rising: buys only.", "notes": [], "evaluated_at": "2026-09-24T13:10:00-04:00"},
         "decisions": {"signals_today": 1, "orders_today": 1, "blocked_today": 0, "top_block_reason": None, "top_block_text": None, "blocked_by_reason": {}}},
        {"id": "mean_reversion", "name": "Statistical Mean Reversion", "status": "COOLDOWN", "daily_pnl": 0.0, "win_rate": 0.55, "trades_count": 0, "sharpe": 1.65,
         "window": {"state": "LIMITED", "headline": "Can trade, with limits", "can_open_now": True, "in_hours": True,
                    "hours": "10:00 AM - 3:45 PM", "ranges": [["10:00", "15:45"]], "trading_day": True,
                    "schedule_text": "In its trading hours.", "next_change_at": None, "blockers": [], "limits": ["Market is flat."],
                    "market_text": "Market is flat: allowed, both directions.", "notes": [], "evaluated_at": "2026-09-24T13:10:00-04:00"},
         "decisions": {"signals_today": 0, "orders_today": 0, "blocked_today": 0, "top_block_reason": None, "top_block_text": None, "blocked_by_reason": {}}},
    ],
    "primary_position": None,
    "all_positions": [{
        "symbol": "MU", "side": "LONG", "shares": 240, "entry_price": 104.15, "market_price": 106.30,
        "market_value": 25512.0, "unrealized_pnl": 516.0, "unrealized_pnl_pct": 0.0206,
        "stop_loss": 95.80, "strategy_id": "swing_panic_dip", "entry_date": "2026-09-23",
    }],
    "positions_count": 1,
    "working_orders_count": 0,
    "recent_activity": [{
        "id": "act_101", "timestamp": "09:30:15", "type": "FILL", "symbol": "MU",
        "message": "SWING ENTRY FILL: 240 shares @ $104.15 (2-Day Panic Dip)", "price": 104.15, "qty": 240,
    }],
    "ingestion": {"stock": "connected", "news": "connected", "vix": "connected"},
    "recent_news": [],
    "ledger_revision": 42,
    "persistence": {"status": "durable", "last_checkpoint_at": "2026-09-24T01:25:00Z", "restored_at": None},
    "swing": {
        "status": "ACTIVE", "strategy_name": "2-Day Panic Dip (Connors RSI-2)",
        "allocated_capital": 50000.0, "slot_notional": 25000.0, "max_slots": 2,
        "active_slots_used": 1, "available_slots": 1, "flattening_exempt": True,
        "candidates": [
            {"symbol": "LRCX", "date": "2026-09-23", "price": 860.50, "close": 860.50, "sma_200": 810.0, "sma_200_pass": True,
             "rs_stock_60d": 15.2, "rs_qqq_60d": 12.1, "rs_pass": True, "rsi_2": 8.4, "rsi_pass": True,
             "earnings_blackout": False, "earnings_date": "2026-11-10", "next_earnings_date": "2026-11-10",
             "daily_atr_14": 24.50, "atr_14": 24.50, "is_staged": False, "is_held": False, "status": "QUALIFIED"},
            {"symbol": "KLAC", "date": "2026-09-23", "price": 695.00, "close": 695.00, "sma_200": 660.0, "sma_200_pass": True,
             "rs_stock_60d": 14.1, "rs_qqq_60d": 12.1, "rs_pass": True, "rsi_2": 14.2, "rsi_pass": False,
             "earnings_blackout": False, "earnings_date": None, "next_earnings_date": None,
             "daily_atr_14": 18.20, "atr_14": 18.20, "is_staged": False, "is_held": False, "status": "WATCHING"},
            {"symbol": "MU", "date": "2026-09-23", "price": 106.30, "close": 106.30, "sma_200": 95.0, "sma_200_pass": True,
             "rs_stock_60d": 18.5, "rs_qqq_60d": 12.1, "rs_pass": True, "rsi_2": 45.2, "rsi_pass": False,
             "earnings_blackout": False, "earnings_date": "2026-10-30", "next_earnings_date": "2026-10-30",
             "daily_atr_14": 3.34, "atr_14": 3.34, "is_staged": False, "is_held": True, "status": "ACTIVE"},
            {"symbol": "AMD", "date": "2026-09-23", "price": 156.40, "close": 156.40, "sma_200": 148.0, "sma_200_pass": True,
             "rs_stock_60d": 11.0, "rs_qqq_60d": 12.1, "rs_pass": False, "rsi_2": 22.0, "rsi_pass": False,
             "earnings_blackout": False, "earnings_date": "2026-10-28", "next_earnings_date": "2026-10-28",
             "daily_atr_14": 4.80, "atr_14": 4.80, "is_staged": False, "is_held": False, "status": "WATCHING"},
            {"symbol": "GS", "date": "2026-09-23", "price": 492.10, "close": 492.10, "sma_200": 440.0, "sma_200_pass": True,
             "rs_stock_60d": 9.5, "rs_qqq_60d": 12.1, "rs_pass": False, "rsi_2": 35.0, "rsi_pass": False,
             "earnings_blackout": True, "earnings_date": "2026-09-25", "next_earnings_date": "2026-09-25",
             "daily_atr_14": 7.10, "atr_14": 7.10, "is_staged": False, "is_held": False, "status": "BLOCKED"},
        ],
        "positions": [{
            "symbol": "MU", "side": "LONG", "shares": 240, "entry_price": 104.15, "market_price": 106.30,
            "market_value": 25512.0, "unrealized_pnl": 516.0, "unrealized_pnl_pct": 0.0206,
            "stop_loss": 95.80, "stop_loss_price": 95.80, "atr_14": 3.34, "entry_atr": 3.34,
            "atr_stop_distance": 10.50, "atr_stop_pct": 9.88, "entry_date": "2026-09-23",
            "holding_days": 2, "max_holding_days": 5, "holding_progress": "Day 2 of 5",
            "sma_5": 103.50, "rsi_2": 45.2,
            "exit_triggers": {"sma_5_cross": False, "rsi_70_cross": False, "time_stop_day_5": False, "earnings_tomorrow": False},
            "staged_exit_at_open": False,
        }],
        "last_scan_time": "2026-09-23T16:00:00Z",
        "schedule_text": "Checks the 4:00 PM close for sharp dips; any buy or sell happens at the next 9:30 AM open.",
        "last_close_data_note": None, "last_close_entries_withheld": False,
    },
}

TRADES_RESPONSE = {
    "as_of": "2026-09-24T17:30:00Z", "timezone": "America/New_York",
    "persistence": {"status": "durable", "last_checkpoint_at": "2026-09-24T17:25:00Z", "restored_at": None, "schema_version": 2},
    "summary": {"opening_equity": 50000.0, "current_equity": 50516.0, "realized_pnl": 516.0, "fees": 1.8,
                "fees_known": True, "trades_count": 3, "wins": 2, "losses": 1, "win_rate": 0.667},
    "items": [],
    "recovered_sessions": [],
    "next_cursor": None,
}

HEALTH_RESPONSE = {
    "status": "healthy", "mode": "development",
    "limits": {"max_daily_loss_dollars": 1500.0, "base_trade_risk_pct": 0.01,
               "max_position_notional": 12500.0, "max_position_equity_pct": 0.25,
               "max_concurrent_positions": 3, "stop_distance_pct": [0.004, 0.04]},
    "persistence": {"status": "durable"}, "relay": {"stock": "connected", "news": "connected", "vix": "connected"},
}


def check_port_free(port: int) -> bool:
    res = subprocess.run(["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, check=False)
    return not bool(res.stdout.strip())


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_str(json.dumps(MOCK_PAYLOAD))
    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
                DISPATCHED_ACTIONS.append(data)
            except Exception as e:
                log.error(f"Error handling WS message: {e}")
        elif msg.type == web.WSMsgType.ERROR:
            log.error(f"WS connection closed with error: {ws.exception()}")
    return ws


async def start_mock_backend() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/ws/ui", websocket_handler)
    app.router.add_get("/api/account", lambda req: web.json_response(MOCK_PAYLOAD["account"]))
    app.router.add_get("/api/trades", lambda req: web.json_response(TRADES_RESPONSE))
    app.router.add_get("/health", lambda req: web.json_response(HEALTH_RESPONSE))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", WS_PORT)
    await site.start()
    log.info(f"Mock backend server started at http://127.0.0.1:{WS_PORT}")
    return runner


def run_full_visual_qa() -> Dict[str, Any]:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    if not check_port_free(WEB_PORT):
        raise RuntimeError(f"Web Port {WEB_PORT} already occupied.")
    if not check_port_free(WS_PORT):
        raise RuntimeError(f"WS Port {WS_PORT} already occupied.")

    loop = asyncio.new_event_loop()
    backend_runner = loop.run_until_complete(start_mock_backend())
    backend_thread = threading.Thread(target=loop.run_forever, daemon=True)
    backend_thread.start()

    log.info(f"Starting Next.js export server on port {WEB_PORT}...")
    server_proc = subprocess.Popen(
        ["node", "scripts/serve_export.mjs", "--port", str(WEB_PORT)],
        cwd=str(FRONTEND_DIR), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=os.setsid,
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
        loop.run_until_complete(backend_runner.cleanup())
        raise RuntimeError(f"Static web server failed to start on port {WEB_PORT}")

    test_records: List[Dict[str, Any]] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            for vp_name, width, height, file_prefix in (
                ("Desktop (1440x900)", 1440, 900, "desktop_1440"),
                ("Mobile (390x844 - iPhone 14 Pro)", 390, 844, "mobile_390"),
            ):
                log.info(f"\n=== {vp_name} ===")
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                console_errors: List[str] = []
                page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

                page.goto(BASE_URL, wait_until="networkidle", timeout=10000)
                page.get_by_text("Day Trader", exact=False).first.wait_for(state="visible", timeout=5000)
                page.wait_for_timeout(1300)  # let staggered .rise entrance animations finish

                overflow_intra = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                assert overflow_intra <= 0, f"{vp_name}: intraday horizontal overflow {overflow_intra}px"
                page.screenshot(path=str(SCREENSHOTS_DIR / f"{file_prefix}_intraday.png"), full_page=True)

                page.locator("[data-testid='mode-tab-swing']").click()
                page.wait_for_timeout(1300)

                page.wait_for_selector("[data-testid='swing-telemetry']")
                page.wait_for_selector("[data-testid='active-swing-row-MU']")
                page.wait_for_selector("[data-testid='swing-candidate-watchlist']")
                for sym in ["LRCX", "KLAC", "MU", "AMD", "GS"]:
                    assert page.locator(f"[data-testid='candidate-card-{sym}']").count() > 0, f"{vp_name}: candidate card for {sym} missing"

                overflow_swing = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                assert overflow_swing <= 0, f"{vp_name}: swing horizontal overflow {overflow_swing}px"
                page.screenshot(path=str(SCREENSHOTS_DIR / f"{file_prefix}_swing.png"), full_page=True)

                # Real network-path action dispatch: SWING_EXIT_NEXT_OPEN (no confirm step).
                DISPATCHED_ACTIONS.clear()
                page.locator("[data-testid='btn-exit-open-MU']").click()
                page.wait_for_timeout(400)
                assert any(a.get("action") == "SWING_EXIT_NEXT_OPEN" and a.get("symbol") == "MU" for a in DISPATCHED_ACTIONS), (
                    f"{vp_name}: SWING_EXIT_NEXT_OPEN not received by the real mock server"
                )

                test_records.append({
                    "viewport": vp_name, "width": width, "height": height,
                    "intraday_overflow_px": overflow_intra, "swing_overflow_px": overflow_swing,
                    "console_errors": len(console_errors), "status": "PASS",
                })
                context.close()

            browser.close()
    finally:
        log.info("Terminating servers...")
        try:
            os.killpg(os.getpgid(server_proc.pid), signal.SIGTERM)
            server_proc.wait(timeout=3)
        except Exception:
            try:
                os.killpg(os.getpgid(server_proc.pid), signal.SIGKILL)
            except Exception:
                pass
        try:
            future = asyncio.run_coroutine_threadsafe(backend_runner.cleanup(), loop)
            future.result(timeout=3.0)
        except Exception as e:
            log.warning(f"Error cleaning up backend runner: {e}")
        loop.call_soon_threadsafe(loop.stop)
        backend_thread.join(timeout=2.0)
        time.sleep(0.5)

    web_free = check_port_free(WEB_PORT)
    ws_free = check_port_free(WS_PORT)
    log.info(f"Port {WEB_PORT} free: {web_free}, Port {WS_PORT} free: {ws_free}")

    return {
        "status": "PASS" if all(r["status"] == "PASS" for r in test_records) and web_free and ws_free else "FAIL",
        "records": test_records,
        "actions_dispatched": len(DISPATCHED_ACTIONS),
        "ports_clean": web_free and ws_free,
    }


def main() -> int:
    try:
        summary = run_full_visual_qa()
        print("\n" + "=" * 70)
        print(" LIVE-NETWORK-PATH VISUAL QA SUMMARY (real mock WS server on 8005)")
        print("=" * 70)
        print(f" Overall Status:  {summary['status']}")
        print(f" Ports Clean:     {summary['ports_clean']}")
        for r in summary["records"]:
            print(f" Viewport:        {r['viewport']} -> {r['status']} (intraday {r['intraday_overflow_px']}px, swing {r['swing_overflow_px']}px overflow)")
        print(" Deep interactive/mocked-data coverage: see scripts/verify_ui_redesign.py")
        print("=" * 70)
        return 0 if summary["status"] == "PASS" else 1
    except Exception as e:
        log.exception(f"Live visual QA failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
