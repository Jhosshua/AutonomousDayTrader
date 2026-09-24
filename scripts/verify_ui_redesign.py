#!/usr/bin/env python3
"""scripts/verify_ui_redesign.py

V3 verification for PLAN_2026_09_24_plain_language_ui.md ("Revision 1 after Codex attack").

Serves the static export (frontend/out) on port 3005 with NO real backend. Every network call
the page makes - the /ws/ui WebSocket and the /api/trades, /health REST calls on the resolved
:8005 origin - is intercepted with Playwright route mocks built from the real API shapes captured
in /private/tmp/.../scratchpad/fixtures/*.json (captured from production on 2026-09-24), not a
live backend (ERRORS.md: `/api/trades` 503s locally without persistence).

Variants: idle (market closed, empty ledger), busy (2 intraday positions incl. a short + 1 swing
holding + 13 trades today), plus lighter real-assertion checks for >100-trade pagination, a
recovered aggregate session, reconnecting, circuit breaker, and feed-down.

Every assertion below can genuinely fail the run (see `check()` / FAILURES) - it is not a script
that always prints PASS. Screenshots (desktop 1440x900 full page, phone 390x844 full page, idle
and busy, both tabs) are saved for review.
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
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright, Page

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
PORT = 3005
BASE_URL = f"http://127.0.0.1:{PORT}"
API_ORIGIN = "http://127.0.0.1:8005"
SHOTS_DIR = Path(
    "/private/tmp/claude-502/-Users-mo/94c38963-6e3d-425a-90b6-007195a4a8a9/scratchpad/shots"
)

FAILURES: List[str] = []
PASS_COUNT = 0


def check(condition: bool, message: str) -> None:
    global PASS_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  ✅ {message}")
    else:
        FAILURES.append(message)
        print(f"  ❌ FAIL: {message}")


def check_port_free(port: int) -> bool:
    res = subprocess.run(["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, check=False)
    return not bool(res.stdout.strip())


# ---------------------------------------------------------------------------
# Mock payload builders (shapes match backend/app/main.py broadcast_ui_state
# and the real captured fixtures)
# ---------------------------------------------------------------------------

def strategy_window(
    state: str,
    headline: str,
    ranges: List[List[str]],
    trading_day: bool = True,
    blockers: Optional[List[str]] = None,
    market_text: str = "Market is rising: buys only.",
    notes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "state": state,
        "headline": headline,
        "can_open_now": state in ("CAN_TRADE", "LIMITED"),
        "in_hours": state in ("CAN_TRADE", "LIMITED", "BLOCKED"),
        "hours": ", ".join(f"{a} - {b}" for a, b in ranges) or "never",
        "ranges": ranges,
        "trading_day": trading_day,
        "schedule_text": headline,
        "next_change_at": None,
        "blockers": blockers or [],
        "limits": [],
        "market_text": market_text,
        "notes": notes or [],
        "evaluated_at": "2026-09-24T13:10:57-04:00",
    }


def decisions(signals: int, orders: int) -> Dict[str, Any]:
    return {
        "signals_today": signals,
        "orders_today": orders,
        "blocked_today": max(0, signals - orders),
        "top_block_reason": "MARKET_FILTER" if signals > orders else None,
        "top_block_text": "Blocked by the market-direction check" if signals > orders else None,
        "blocked_by_reason": {},
    }


def swing_candidate(symbol: str, price: float, sma_pass: bool, rs_pass: bool, rsi_pass: bool,
                     earnings_date: Optional[str], blackout: bool, is_staged: bool = False,
                     is_held: bool = False) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "date": "2026-09-23",
        "price": price,
        "close": price,
        "sma_200": price * 0.9,
        "sma_200_pass": sma_pass,
        "above_200_sma": sma_pass,
        "rs_stock_60d": 12.5 if rs_pass else -8.2,
        "rs_qqq_60d": 2.5,
        "rs_60d_stock": 12.5 if rs_pass else -8.2,
        "rs_60d_qqq": 2.5,
        "relative_strength_ok": rs_pass,
        "rs_pass": rs_pass,
        "rsi_2": 8.5 if rsi_pass else 65.0,
        "rsi_pass": rsi_pass,
        "panic_trigger": rsi_pass,
        "earnings_blackout": blackout,
        "earnings_date": earnings_date,
        "next_earnings_date": earnings_date,
        "daily_atr_14": 12.4,
        "atr_14": 12.4,
        "qualified": sma_pass and rs_pass and rsi_pass and not blackout,
        "is_held": is_held,
        "is_staged": is_staged,
        "status": "QUALIFIED" if (sma_pass and rs_pass and rsi_pass) else "WATCHING",
        "rejection_reasons": [],
    }


DEFAULT_CANDIDATES = [
    swing_candidate("LRCX", 307.28, True, False, False, "2026-10-21", False),
    swing_candidate("KLAC", 187.86, True, False, False, "2026-10-29", False),
    swing_candidate("MU", 1071.88, True, False, False, "2026-09-30", False),
    swing_candidate("AMD", 614.61, True, True, False, "2026-10-27", False),
    swing_candidate("GS", 936.36, False, False, False, "2026-10-13", False),
]


def position(symbol: str, side: str, shares: int, entry: float, market: float, stop: Optional[float],
             strategy_id: str = "vwap_pullback") -> Dict[str, Any]:
    direction = 1 if side == "LONG" else -1
    unrealized = round(direction * (market - entry) * shares, 2)
    return {
        "symbol": symbol,
        "side": side,
        "shares": shares,
        "entry_price": entry,
        "market_price": market,
        "market_value": round(market * shares, 2),
        "unrealized_pnl": unrealized,
        "unrealized_pnl_pct": round(unrealized / (entry * shares), 4) if entry and shares else 0.0,
        "stop_loss": stop,
        "take_profit_1": None,
        "take_profit_2": None,
        "strategy_id": strategy_id,
        "cost_basis": round(entry * shares, 2),
        "realized_pnl": 0.0,
        "entry_atr": None,
        "entry_date": "2026-09-24",
    }


def base_payload(market_status: str, trading_day: bool) -> Dict[str, Any]:
    return {
        "type": "STATE_UPDATE",
        "timestamp": "2026-09-24T17:10:00Z",
        "account": {
            "equity": 50000.0,
            "cash": 50000.0,
            "buying_power": 200000.0,
            "daily_pnl": 0.0,
            "daily_pnl_pct": 0.0,
            "daily_drawdown": 0.0,
            "daily_drawdown_pct": 0.0,
            "is_circuit_broken": False,
            "risk_level": "NORMAL",
            "status": "ACTIVE",
            "daily_starting_equity": 50000.0,
        },
        "market_context": {
            "vix": 15.48,
            "vix_regime": "NORMAL",
            "time_phase": "MIDDAY_CHOP",
            "market_status": market_status,
            "sizing_multiplier": 1.0,
        },
        "strategies": [
            {
                "id": "orb", "name": "Opening Range Breakout", "status": "ACTIVE",
                "daily_pnl": 0.0, "win_rate": 0.0, "trades_count": 0, "sharpe": 0.0,
                "window": strategy_window(
                    "DONE_FOR_DAY" if trading_day else "MARKET_CLOSED",
                    "Done for today" if trading_day else "Market closed",
                    [["09:30", "11:30"]], trading_day=trading_day,
                ),
                "decisions": decisions(1, 1) if trading_day else decisions(0, 0),
            },
            {
                "id": "vwap_pullback", "name": "VWAP Trend Pullback & Continuation", "status": "ACTIVE",
                "daily_pnl": 0.0, "win_rate": 0.0, "trades_count": 0, "sharpe": 0.0,
                "window": strategy_window(
                    "WAITING", "Opens 2:00 PM", [["09:30", "11:30"], ["14:00", "15:45"]], trading_day=trading_day,
                ),
                "decisions": decisions(17, 0) if trading_day else decisions(0, 0),
            },
            {
                "id": "news_momentum", "name": "Catalyst News Momentum Breakout", "status": "ACTIVE",
                "daily_pnl": 0.0, "win_rate": 0.0, "trades_count": 0, "sharpe": 0.0,
                "window": strategy_window(
                    "CAN_TRADE" if trading_day else "MARKET_CLOSED",
                    "Can open trades now" if trading_day else "Market closed",
                    [["09:30", "15:45"]], trading_day=trading_day,
                ),
                "decisions": decisions(0, 0),
            },
            {
                "id": "mean_reversion", "name": "Statistical Mean Reversion / Exhaustion Fades", "status": "ACTIVE",
                "daily_pnl": 0.0, "win_rate": 0.0, "trades_count": 0, "sharpe": 0.0,
                "window": strategy_window(
                    "LIMITED" if trading_day else "MARKET_CLOSED",
                    "Can trade, with limits" if trading_day else "Market closed",
                    [["10:00", "15:45"]], trading_day=trading_day,
                ),
                "decisions": decisions(9, 0) if trading_day else decisions(0, 0),
            },
        ],
        "primary_position": None,
        "all_positions": [],
        "positions_count": 0,
        "working_orders_count": 0,
        "recent_activity": [],
        "ingestion": {"stock": "connected", "news": "connected", "vix": "connected"},
        "recent_news": [],
        "ledger_revision": 1,
        "persistence": {"status": "durable", "last_checkpoint_at": "2026-09-24T17:09:00Z", "restored_at": "2026-09-24T15:36:00Z"},
        "swing": {
            "status": "STANDBY",
            "strategy_name": "2-Day Panic Dip (Connors RSI-2)",
            "allocated_capital": 50000.0,
            "slot_notional": 25000.0,
            "max_slots": 2,
            "active_slots_used": 0,
            "available_slots": 2,
            "flattening_exempt": True,
            "candidates": [dict(c) for c in DEFAULT_CANDIDATES],
            "positions": [],
            "last_scan_time": "2026-09-23T16:00:00-04:00",
            "schedule_text": "Checks the 4:00 PM close for sharp dips; any buy or sell happens at the next 9:30 AM open.",
            "last_close_data_note": None,
            "last_close_entries_withheld": False,
        },
    }


def idle_payload() -> Dict[str, Any]:
    return base_payload(market_status="CLOSED", trading_day=False)


def busy_payload() -> Dict[str, Any]:
    payload = base_payload(market_status="OPEN", trading_day=True)
    payload["account"].update({
        "equity": 50245.10,
        "daily_pnl": 245.10,
        "daily_pnl_pct": 0.49,
        "daily_drawdown": 0.0,
    })
    long_pos = position("AAPL", "LONG", 37, 336.14, 340.20, 330.00, strategy_id="vwap_pullback")
    short_pos = position("TSLA", "SHORT", 20, 420.00, 410.50, 428.00, strategy_id="orb")
    swing_pos = position("MU", "LONG", 23, 1071.88, 1090.00, None, strategy_id="swing_panic_dip")
    payload["all_positions"] = [long_pos, short_pos, swing_pos]
    payload["positions_count"] = 3
    payload["primary_position"] = long_pos
    payload["swing"]["positions"] = [{
        "symbol": "MU", "side": "LONG", "shares": 23, "entry_price": 1071.88, "market_price": 1090.0,
        "market_value": 25070.0, "unrealized_pnl": round((1090.0 - 1071.88) * 23, 2), "unrealized_pnl_pct": 0.017,
        "stop_loss": None, "stop_loss_price": None, "atr_14": 49.84, "entry_atr": 49.84,
        "atr_stop_distance": 124.6, "atr_stop_pct": 11.4, "entry_date": "2026-09-23",
        "holding_days": 2, "max_holding_days": 5, "holding_progress": "Day 2 of 5",
        "sma_5": 1050.0, "rsi_2": 45.0,
        "exit_triggers": {"sma_5_cross": False, "rsi_70_cross": False, "time_stop_day_5": False, "earnings_tomorrow": False},
        "staged_exit_at_open": False,
    }]
    payload["swing"]["active_slots_used"] = 1
    payload["swing"]["available_slots"] = 1
    payload["swing"]["status"] = "ACTIVE"
    payload["swing"]["candidates"][3]["is_staged"] = True  # AMD staged to buy at next open
    return payload


def make_trade(idx: int, symbol: str, side: str, strategy_id: str, pnl: float, closed_hour: int, closed_minute: int,
               session_date: str = "2026-09-24") -> Dict[str, Any]:
    closed_at = f"{session_date}T{closed_hour:02d}:{closed_minute:02d}:00Z"
    return {
        "aggregate_only": False,
        "avg_entry_price": 200.0,
        "avg_exit_price": 200.0 + (1 if pnl >= 0 else -1),
        "closed_at": closed_at,
        "exit_reason": "COMPLETED_STOP",
        "fees": 0.36,
        "fill_legs": [],
        "opened_at": closed_at,
        "quantity": 10,
        "realized_pnl": pnl,
        "session_date": session_date,
        "side": side,
        "status": "CLOSED",
        "strategy_id": strategy_id,
        "symbol": symbol,
        "trade_id": f"trd_{session_date}_{idx:04d}",
    }


# Hours are UTC; ET (EDT, UTC-4) in parens so the balance-chart step line lands inside the
# 9:30-16:00 ET session instead of clamping to the open (all pre-9:30-ET timestamps would
# otherwise collapse to the same x position).
BUSY_TODAY_TRADES = [
    make_trade(1, "AAPL", "SHORT", "vwap_pullback", -57.37, 18, 13),   # 2:13 PM ET
    make_trade(2, "AMD", "SHORT", "vwap_pullback", 89.35, 18, 2),      # 2:02 PM ET
    make_trade(3, "PLTR", "SHORT", "vwap_pullback", -59.92, 17, 31),   # 1:31 PM ET
    make_trade(4, "NVDA", "SHORT", "vwap_pullback", 29.55, 17, 0),     # 1:00 PM ET
    make_trade(5, "AAPL", "SHORT", "orb", -112.04, 16, 50),            # 12:50 PM ET
    make_trade(6, "PLTR", "LONG", "vwap_pullback", 44.30, 16, 33),     # 12:33 PM ET
    make_trade(7, "META", "LONG", "vwap_pullback", 37.08, 16, 6),      # 12:06 PM ET
    make_trade(8, "MSFT", "LONG", "news_momentum", 21.10, 15, 55),     # 11:55 AM ET
    make_trade(9, "GOOGL", "SHORT", "mean_reversion", -18.40, 15, 45), # 11:45 AM ET
    make_trade(10, "AMZN", "LONG", "vwap_pullback", 12.75, 15, 40),    # 11:40 AM ET
    make_trade(11, "NVDA", "LONG", "orb", 65.20, 15, 35),              # 11:35 AM ET
    make_trade(12, "MSFT", "SHORT", "vwap_pullback", -9.10, 14, 33),   # 10:33 AM ET
    make_trade(13, "META", "SHORT", "news_momentum", 8.60, 14, 31),    # 10:31 AM ET
]


def trades_summary(items: List[Dict[str, Any]], opening_equity: float = 50000.0, current_equity: float = 50245.10) -> Dict[str, Any]:
    wins = sum(1 for t in items if t["realized_pnl"] > 0)
    losses = sum(1 for t in items if t["realized_pnl"] < 0)
    realized = round(sum(t["realized_pnl"] for t in items), 2)
    return {
        "opening_equity": opening_equity,
        "current_equity": current_equity,
        "realized_pnl": realized,
        "fees": round(sum(t["fees"] for t in items), 2),
        "fees_known": True,
        "trades_count": len(items),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(1, wins + losses), 4),
    }


def trades_response(items: List[Dict[str, Any]], recovered: Optional[List[Dict[str, Any]]] = None,
                     next_cursor: Optional[str] = None) -> Dict[str, Any]:
    return {
        "as_of": "2026-09-24T17:10:58Z",
        "timezone": "America/New_York",
        "persistence": {"status": "durable", "last_checkpoint_at": "2026-09-24T17:10:48Z", "restored_at": "2026-09-24T15:36:34Z", "schema_version": 2},
        "summary": trades_summary(items),
        "items": items,
        "recovered_sessions": recovered or [],
        "next_cursor": next_cursor,
    }


HEALTH_RESPONSE = {
    "status": "healthy",
    "mode": "development",
    "limits": {
        "max_daily_loss_dollars": 1500.0,
        "max_position_notional": 12500.0,
        "max_position_equity_pct": 0.25,
        "max_concurrent_positions": 3,
        "base_trade_risk_pct": 0.01,
        "stop_distance_pct": [0.004, 0.04],
    },
    "persistence": {"status": "durable"},
    "relay": {"stock": "connected", "news": "connected", "vix": "connected"},
}


# ---------------------------------------------------------------------------
# Playwright wiring
# ---------------------------------------------------------------------------

def install_mocks(page: Page, ws_payload: Dict[str, Any], trades_items: List[Dict[str, Any]],
                   dispatched_actions: List[Dict[str, Any]], recovered: Optional[List[Dict[str, Any]]] = None,
                   trades_pages: Optional[List[List[Dict[str, Any]]]] = None) -> None:
    def ws_handler(ws) -> None:
        def on_message(message: str) -> None:
            try:
                dispatched_actions.append(json.loads(message))
            except Exception:
                pass
        ws.on_message(on_message)
        ws.send(json.dumps(ws_payload))

    page.route_web_socket(re.compile(r".*/ws/ui$"), ws_handler)

    def trades_handler(route) -> None:
        url = route.request.url
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        rng = qs.get("range", ["today"])[0]
        cursor = qs.get("cursor", [None])[0]
        if trades_pages is not None:
            page_idx = int(cursor) if cursor else 0
            page_items = trades_pages[page_idx] if page_idx < len(trades_pages) else []
            next_cursor = str(page_idx + 1) if page_idx + 1 < len(trades_pages) else None
            body = trades_response(page_items, recovered=recovered if page_idx == 0 else None, next_cursor=next_cursor)
        else:
            items = trades_items if rng in ("today", "7d", "all") else []
            body = trades_response(items, recovered=recovered)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    page.route(re.compile(r".*/api/trades.*"), trades_handler)
    page.route(
        re.compile(r".*/health$"),
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(HEALTH_RESPONSE)),
    )
    page.route(
        re.compile(r".*/api/account$"),
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(ws_payload["account"])),
    )


def start_export_server() -> subprocess.Popen:
    if not check_port_free(PORT):
        raise RuntimeError(f"Port {PORT} already occupied; free it before running this suite.")
    proc = subprocess.Popen(
        ["node", "scripts/serve_export.mjs", "--port", str(PORT)],
        cwd=str(FRONTEND_DIR), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=os.setsid,
    )
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE_URL, timeout=1) as resp:
                if resp.status == 200:
                    return proc
        except Exception:
            time.sleep(0.3)
    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    raise RuntimeError("Static export server failed to start")


def stop_export_server(proc: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=3)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
    deadline = time.time() + 5.0
    while time.time() < deadline and not check_port_free(PORT):
        time.sleep(0.1)


def no_overflow(page: Page, label: str) -> None:
    scroll_w = page.evaluate("() => document.documentElement.scrollWidth")
    inner_w = page.evaluate("() => window.innerWidth")
    check(scroll_w <= inner_w + 1, f"[{label}] no horizontal overflow (scrollWidth={scroll_w} innerWidth={inner_w})")


def no_clipped_text(page: Page, label: str) -> None:
    clipped = page.evaluate(
        """
        () => {
            const bad = [];
            const els = document.querySelectorAll('h1,h2,h3,p,span,div,button,a');
            for (const el of els) {
                const style = window.getComputedStyle(el);
                if (style.overflow === 'hidden' && style.textOverflow !== 'ellipsis' && el.scrollWidth > el.clientWidth + 2 && el.children.length === 0) {
                    bad.push(el.className || el.tagName);
                }
            }
            return bad;
        }
        """
    )
    check(len(clipped) == 0, f"[{label}] no clipped text nodes (found {len(clipped)}: {clipped[:5]})")


def controls_min_44px(page: Page, label: str) -> None:
    small = page.evaluate(
        """
        () => {
            const bad = [];
            document.querySelectorAll('button').forEach((btn) => {
                const rect = btn.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) return; // not rendered/hidden
                if (rect.height < 43) bad.push({ text: (btn.textContent || '').slice(0, 30), height: rect.height });
            });
            return bad;
        }
        """
    )
    check(len(small) == 0, f"[{label}] all visible buttons >= 44px tall (found {len(small)} short: {small[:5]})")


def reduced_motion_no_running_animations(page: Page, label: str) -> None:
    running = page.evaluate("() => document.getAnimations ? document.getAnimations().length : 0")
    check(running == 0, f"[{label}] reduced-motion leaves no running Web Animations (found {running})")


# ---------------------------------------------------------------------------
# Variant runs
# ---------------------------------------------------------------------------

def run_variant(browser, variant_name: str, ws_payload: Dict[str, Any], trades_items: List[Dict[str, Any]],
                 screenshot: bool, recovered: Optional[List[Dict[str, Any]]] = None) -> None:
    for viewport_name, width, height in (("desktop", 1440, 900), ("phone", 390, 844)):
        dispatched: List[Dict[str, Any]] = []
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        install_mocks(page, ws_payload, trades_items, dispatched, recovered=recovered)
        page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
        page.get_by_text("Day Trader", exact=False).first.wait_for(state="visible", timeout=10000)

        for tab_name, tab_testid in (("intraday", "mode-tab-intraday"), ("swing", "mode-tab-swing")):
            page.locator(f'[data-testid="{tab_testid}"]').click()
            # Let every staggered .rise entrance animation finish (last delay + duration is
            # under ~1.2s) before screenshotting or measuring layout, so nothing is caught
            # mid-animation at opacity: 0.
            page.wait_for_timeout(1300)
            label = f"{variant_name}/{viewport_name}/{tab_name}"
            no_overflow(page, label)
            no_clipped_text(page, label)
            controls_min_44px(page, label)
            if screenshot:
                SHOTS_DIR.mkdir(parents=True, exist_ok=True)
                out = SHOTS_DIR / f"{variant_name}_{viewport_name}_{tab_name}.png"
                page.screenshot(path=str(out), full_page=True)
                check(out.exists() and out.stat().st_size > 0, f"[{label}] screenshot saved to {out}")

        context.close()


def run_action_dispatch_checks(browser) -> None:
    """Each of the 6 actions must send the exact payload; confirm expires; no duplicate sends."""
    payload = busy_payload()
    dispatched: List[Dict[str, Any]] = []
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    install_mocks(page, payload, BUSY_TODAY_TRADES, dispatched)
    page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
    page.get_by_text("Day Trader", exact=False).first.wait_for(state="visible", timeout=10000)

    # --- TIGHTEN_STOP via HoldingNow "Move safety exit to my buy price" (AAPL: stop 330 < entry 336.14 < market 340.20) ---
    dispatched.clear()
    btn = page.locator('[data-testid="btn-break-even-AAPL"]')
    check(btn.is_visible(), "[actions] break-even button visible for AAPL")
    btn.click()
    page.wait_for_timeout(300)
    check(
        any(a.get("action") == "TIGHTEN_STOP" and a.get("symbol") == "AAPL" and abs(float(a.get("new_stop", 0)) - 336.14) < 0.01 for a in dispatched),
        f"[actions] TIGHTEN_STOP sent with exact symbol/new_stop payload, got: {dispatched}",
    )

    # --- FLATTEN_POSITION via HoldingNow "Sell now" (long AAPL), with confirm step ---
    dispatched.clear()
    sell_btn = page.locator('[data-testid="btn-sell-now-AAPL"]')
    sell_btn.click()  # -> confirm
    page.wait_for_timeout(150)
    check(len(dispatched) == 0, "[actions] first click only arms confirm, does not send yet")
    sell_btn.click()  # -> sends
    page.wait_for_timeout(300)
    check(
        any(a.get("action") == "FLATTEN_POSITION" and a.get("symbol") == "AAPL" for a in dispatched),
        f"[actions] FLATTEN_POSITION sent with exact symbol after confirm, got: {dispatched}",
    )
    sent_after_confirm = len(dispatched)
    sell_btn.click()
    page.wait_for_timeout(200)
    check(len(dispatched) == sent_after_confirm, "[actions] no duplicate FLATTEN_POSITION send on rapid re-click while sending/disabled")

    # --- Confirm expiry: TSLA "Close trade" (short) confirm auto-reverts after ~4s without sending ---
    dispatched.clear()
    tsla_btn = page.locator('[data-testid="btn-sell-now-TSLA"]')
    tsla_btn.click()
    page.wait_for_timeout(200)
    check(tsla_btn.inner_text().strip().lower().startswith("tap again"), "[actions] TSLA button shows 'Tap again to confirm' after first click")
    page.wait_for_timeout(4300)
    check(not tsla_btn.inner_text().strip().lower().startswith("tap again"), "[actions] confirm state expires (~4s) back to idle without sending")
    check(len(dispatched) == 0, "[actions] no action was sent when confirm expired")

    # --- FLATTEN_ALL via SafetyCard "Close all quick trades now" ---
    page.locator('[data-testid="mode-tab-intraday"]').click()
    dispatched.clear()
    flatten_all_btn = page.locator('[data-testid="btn-flatten-all"]')
    flatten_all_btn.scroll_into_view_if_needed()
    flatten_all_btn.click()
    page.wait_for_timeout(150)
    flatten_all_btn.click()
    page.wait_for_timeout(300)
    check(any(a.get("action") == "FLATTEN_ALL" for a in dispatched), f"[actions] FLATTEN_ALL sent with exact payload, got: {dispatched}")

    # --- Swing actions: SWING_EXIT_NEXT_OPEN, SWING_EXIT_IMMEDIATE, SWING_TIGHTEN_STOP ---
    page.locator('[data-testid="mode-tab-swing"]').click()
    page.wait_for_timeout(200)

    dispatched.clear()
    next_open_btn = page.locator('[data-testid="btn-exit-open-MU"]')
    next_open_btn.click()
    page.wait_for_timeout(300)
    check(
        any(a.get("action") == "SWING_EXIT_NEXT_OPEN" and a.get("symbol") == "MU" for a in dispatched),
        f"[actions] SWING_EXIT_NEXT_OPEN sent with exact symbol, got: {dispatched}",
    )

    dispatched.clear()
    raise_btn = page.locator('[data-testid="btn-tighten-stop-MU"]')
    raise_btn.click()
    page.wait_for_timeout(150)
    page.locator('[data-testid="input-raise-stop-MU"]').fill("1080.00")
    page.locator('[data-testid="btn-confirm-raise-MU"]').click()
    page.wait_for_timeout(300)
    check(
        any(a.get("action") == "SWING_TIGHTEN_STOP" and a.get("symbol") == "MU" and abs(float(a.get("new_stop", 0)) - 1080.0) < 0.01 for a in dispatched),
        f"[actions] SWING_TIGHTEN_STOP sent with exact symbol/new_stop payload, got: {dispatched}",
    )

    dispatched.clear()
    emergency_btn = page.locator('[data-testid="btn-emergency-exit-MU"]')
    emergency_btn.click()
    page.wait_for_timeout(150)
    emergency_btn.click()
    page.wait_for_timeout(300)
    check(
        any(a.get("action") == "SWING_EXIT_IMMEDIATE" and a.get("symbol") == "MU" for a in dispatched),
        f"[actions] SWING_EXIT_IMMEDIATE sent with exact symbol after confirm, got: {dispatched}",
    )

    context.close()


def run_reduced_motion_check(browser) -> None:
    payload = busy_payload()
    dispatched: List[Dict[str, Any]] = []
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.emulate_media(reduced_motion="reduce")
    install_mocks(page, payload, BUSY_TODAY_TRADES, dispatched)
    page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
    page.get_by_text("Day Trader", exact=False).first.wait_for(state="visible", timeout=10000)
    page.wait_for_timeout(500)
    reduced_motion_no_running_animations(page, "reduced-motion")
    context.close()


def run_pagination_check(browser) -> None:
    """>100 trades: /api/trades drains next_cursor and 'Load older trades' grows the list."""
    payload = idle_payload()
    dispatched: List[Dict[str, Any]] = []
    page1 = [make_trade(i, "AAPL", "LONG", "vwap_pullback", 1.0, 9, i % 60, session_date="2026-09-18") for i in range(100)]
    page2 = [make_trade(100 + i, "AAPL", "LONG", "vwap_pullback", -1.0, 9, i % 60, session_date="2026-09-17") for i in range(30)]
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    install_mocks(page, payload, [], dispatched, trades_pages=[page1, page2])
    page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
    page.get_by_text("Day Trader", exact=False).first.wait_for(state="visible", timeout=10000)
    page.get_by_text("See all trades", exact=False).click()
    page.wait_for_timeout(400)
    initial_count = page.locator('button:has(time)').count()
    check(initial_count > 0, f"[pagination] first page of trades rendered ({initial_count} rows)")
    load_more = page.get_by_text("Load older trades", exact=False)
    if load_more.count() > 0:
        load_more.click()
        page.wait_for_timeout(500)
        grown_count = page.locator('button:has(time)').count()
        check(grown_count > initial_count, f"[pagination] 'Load older trades' grew the list ({initial_count} -> {grown_count})")
    else:
        check(False, "[pagination] expected a 'Load older trades' button for a >100-trade history")
    context.close()


def run_recovered_session_check(browser) -> None:
    payload = idle_payload()
    dispatched: List[Dict[str, Any]] = []
    recovered = [{
        "session_date": "2026-09-21", "opening_equity": 50000.0, "closing_equity": 49978.66,
        "realized_pnl": -21.34, "trades_count": 5, "fees": 0.0, "source": "LEGACY_SUMMARY_IMPORT",
        "aggregate_only": True, "note": "recovered from summary; individual executions were not persisted",
    }]
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    install_mocks(page, payload, [], dispatched, recovered=recovered)
    page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
    page.get_by_text("Day Trader", exact=False).first.wait_for(state="visible", timeout=10000)
    page.get_by_text("See all trades", exact=False).click()
    page.wait_for_timeout(400)
    check(page.get_by_text("older, recovered day", exact=False).count() > 0, "[recovered] recovered aggregate session note is shown")
    check(page.get_by_text("Some older details unavailable", exact=False).count() > 0, "[recovered] 'Some older details unavailable' note is shown")
    context.close()


def run_circuit_breaker_and_feed_down_check(browser) -> None:
    payload = busy_payload()
    payload["account"]["is_circuit_broken"] = True
    payload["ingestion"] = {"stock": "disconnected", "news": "disconnected", "vix": "disconnected"}
    dispatched: List[Dict[str, Any]] = []
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    install_mocks(page, payload, BUSY_TODAY_TRADES, dispatched)
    page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
    page.get_by_text("Day Trader", exact=False).first.wait_for(state="visible", timeout=10000)
    check(page.get_by_text("hit the daily loss limit", exact=False).count() > 0, "[circuit-breaker] plain-language stopped-for-today banner shown, always visible (not pro-only)")
    check(page.get_by_text("Price feed is down", exact=False).count() > 0, "[feed-down] plain-language feed-down banner shown, always visible (not pro-only)")
    context.close()


def main() -> int:
    proc = start_export_server()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            print("\n=== idle variant (market closed, empty ledger): screenshots + overflow/clip/44px ===")
            run_variant(browser, "idle", idle_payload(), [], screenshot=True)

            print("\n=== busy variant (2 intraday incl. short + 1 swing holding + 13 trades) ===")
            run_variant(browser, "busy", busy_payload(), BUSY_TODAY_TRADES, screenshot=True)

            print("\n=== action dispatch: exact payloads, confirm expiry, no duplicate sends ===")
            run_action_dispatch_checks(browser)

            print("\n=== prefers-reduced-motion: no running Web Animations ===")
            run_reduced_motion_check(browser)

            print("\n=== >100 trades: pagination drains next_cursor ===")
            run_pagination_check(browser)

            print("\n=== recovered aggregate session note ===")
            run_recovered_session_check(browser)

            print("\n=== circuit breaker + feed-down banners (always visible) ===")
            run_circuit_breaker_and_feed_down_check(browser)

            # Reconnecting banner: no WS mock installed, so the socket can never open.
            print("\n=== reconnecting banner (no WS route installed) ===")
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.goto(BASE_URL, wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(1500)
            check(
                page.get_by_text("Connecting to the robot", exact=False).count() > 0
                or page.get_by_text("Lost connection to the robot", exact=False).count() > 0,
                "[reconnecting] plain-language connecting/reconnecting state shown when the socket never opens",
            )
            context.close()

            browser.close()
    finally:
        stop_export_server(proc)

    print("\n" + "=" * 72)
    print(f" verify_ui_redesign.py: {PASS_COUNT} checks passed, {len(FAILURES)} failed")
    if FAILURES:
        print(" FAILURES:")
        for f in FAILURES:
            print(f"  - {f}")
    print("=" * 72)
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
