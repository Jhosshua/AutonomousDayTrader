# @steered SNARE-2 2026-09-30
"""T8: every reader of ADT's positions and working orders has an explicit overnight decision, and
holds survive a full main loop pass (bars, quotes, trades, news, VIX, the clock through the close,
midnight and the pre-market, and the broker compare).

The reader list is found by grep (every function in backend/app whose code reads
account.positions, acct.positions, working_orders or open_positions). A new reader fails this
test until someone writes down what it does with an overnight hold."""
import ast
import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from backend.app.core import overnight_schedule as osch
from backend.app.models.events import NewsEvent, TradeEvent, VixPrint, VixRegime
from backend.tests.unit.overnight_integration.fakes import PRICES, MainOvernight, at, buy_night, queue_sales

APP = Path(__file__).resolve().parents[3] / "app"
PATTERN = re.compile(r"\b(?:account|acct|target_acct|self\.account|r\.account|self\.r\.account)\.positions\b"
                     r"|\bworking_orders\b|\bopen_positions\b")

NO_OVN = "no overnight order is ever a working order (R4), so nothing overnight is read here"
SWING = "swing arm only (filters on the swing arm or its symbols)"
OWN = "the controller's own symbols only (TSLA/CDE tri plan, TSLA OR15, ORB)"
DECISIONS = {
    # engine
    "core/engine.py::__init__": "declares working_orders",
    "core/engine.py::_apply_fill_to_ledger": "the booking call; OVERNIGHT_POLICY orders are filled at once and never enter working_orders",
    "core/engine.py::_execute_fill": NO_OVN,
    "core/engine.py::_handle_broker_error": NO_OVN,
    "core/engine.py::_reduces_position": "S2: overnight orders never reach _broker_execute (refused by policy); others are checked by order_guard first",
    "core/engine.py::_reject_after_broker": NO_OVN,
    "core/engine.py::cancel_all_orders": NO_OVN + " (S5)",
    "core/engine.py::cancel_order": NO_OVN,
    "core/engine.py::process_bar": "S15: account.update_market_price skips holds; overnight orders are not matched",
    "core/engine.py::process_quote": "S15: account.update_market_price skips holds; overnight orders are not matched",
    "core/engine.py::prune_session_state": "booking orders have no broker ids and are prunable history; the trade row is built from the integration ledger",
    "core/engine.py::settle_broker_orders": "R2-11: OVERNIGHT_POLICY joins the ORB and tri skips",
    "core/engine.py::submit_order": "booking orders are never submitted; every other order meets S1 in the validator",
    "core/flattening.py::execute_phase_4_audit": "S4: holds exempt",
    # fixed plans and ORB
    "core/or15_execution.py::_enter": OWN, "core/or15_execution.py::_poll_protection": OWN,
    "core/or15_execution.py::_run_exit": OWN, "core/or15_execution.py::owns": OWN, "core/or15_execution.py::tick": OWN,
    "core/tri_execution.py::_book": OWN, "core/tri_execution.py::_prepare_entry": OWN, "core/tri_execution.py::open_risk": OWN,
    "core/tri_execution.py::owns": OWN, "core/tri_execution.py::validate_tri_state": OWN,
    "core/orb_integration.py::adt_occupied": "S11: a hold is 'ADT holds'; a reserved stock is refused through overnight.occupied_reason",
    "core/orb_integration.py::apply_resolution": "ORB positions only",
    "core/orb_integration.py::card": "ORB positions only", "core/orb_integration.py::check_orphans": "ORB positions only",
    "core/orb_integration.py::is_orphan": "strategy_id orb only", "core/orb_integration.py::mark_positions": "ORB positions only (no hold is marked)",
    "core/orb_integration.py::note_price": "ORB positions only", "core/orb_integration.py::orphan_targets": "ORB positions only",
    "core/orb_integration.py::symbols": "ORB positions only",
    # the overnight glue itself
    "core/overnight_integration.py::<module>": "module docstring",
    "core/overnight_integration.py::_book": "books holds; never merges into a non overnight position (R2-13)",
    "core/overnight_integration.py::_book_split": "adjusts a hold after a split",
    "core/overnight_integration.py::_book_symbol_change": "moves a hold to its new symbol after a confirmed symbol change; never merges",
    "core/overnight_integration.py::_cancel_day_entries": "15:46:05 cancels other working entries in the stock",
    "core/overnight_integration.py::_close_day_trade": "X6: closes the day trade, never a hold",
    "core/overnight_integration.py::_day_shares": "non overnight shares only",
    "core/overnight_integration.py::_held_by_other": "the stock held by ORB, a fixed plan or a Slow trade is skipped",
    "core/overnight_integration.py::_overnight_shares": "overnight shares only; a night is released only when ADT's book holds none",
    "core/overnight_integration.py::_swing_value": "Slow trades held tonight count against the room (D3)",
    "core/overnight_integration.py::_x6_step": "X6 booking of the day trade close",
    "core/overnight_integration.py::holds_symbol": "S1: a hold in the book",
    "core/overnight_integration.py::order_refusal": "S1/S2",
    # research (observation only)
    "core/research_tracker.py::_swing_fill": SWING, "core/research_tracker.py::context": "observation only (a count)",
    "core/research_tracker.py::on_bar": "observation only, bracketed trades",
    # checkpoint
    "core/runtime_state.py::capture_runtime_state": "holds are saved like any position; the controller in the 'overnight' key",
    "core/runtime_state.py::restore_runtime_state": "restored like any position",
    "core/runtime_state.py::validate_runtime_state": "brackets and stops only; a hold has neither",
    # main
    "main.py::_apply_bracket_directive": "bracket children only",
    "main.py::_check_session_boundary": "S6: holds exempt from the boundary liquidation",
    "main.py::_checkpoint_runtime": NO_OVN + " (the recovery halt cancels only working orders)",
    "main.py::_execute_manual_flatten": "S8 / D9: holds skipped with the sale time",
    "main.py::_expire_stale_staged_swing_orders": SWING,
    "main.py::_flatten_symbol": "never called for a hold (every caller skips holds first)",
    "main.py::_get_effective_committed_portfolio": "S12: holds excluded from the cap, sector and exposure maps",
    "main.py::_local_signed_positions": "S13: holds are compared with Alpaca like any position",
    "main.py::_quote_requires_write_ahead": "S15: a hold is never projected from a quote; S14 offset in the projection",
    "main.py::_release_resolved_swing_reservations": SWING,
    "main.py::_restore_checkpoint": "a log line count",
    "main.py::_serialize_position": "S17: exit_due is the sale time",
    "main.py::_startup_overnight": "ORB positions check before the startup boundary",
    "main.py::_strategy_cards": "fallback count only",
    "main.py::_trip_circuit_breaker": "S7: holds never liquidated",
    "main.py::broadcast_ui_state": "display (holds listed, sale time shown)",
    "main.py::execute_strategy_signal": "S9: the contradiction exit skips holds; an entry meets S1",
    "main.py::get_health": "display count; the watchdog subtracts holds (S19)",
    "main.py::handle_bar_event": "display history only",
    "main.py::handle_flattening_directive": "S3/S4/S5: 15:50 purge (working orders only), 15:55 and 15:58 skip holds",
    "main.py::handle_news_event": "monitoring only; its exit signal is refused for holds (S9)",
    "main.py::is_symbol_reserved_for_swing": SWING,
    "main.py::manual_flatten": "S8 target list; holds reported as skipped",
    "main.py::pre_trade_risk_validator": "S1 first, then S12 counts exclude holds",
    "main.py::reset_runtime_state": "clears (tests and replays)",
    "main.py::submit_order": "API: a hold has no bracket so a reduce is refused (409); S1 in the validator",
    "main.py::ui_websocket_endpoint": "S10: tighten stop refused for holds",
    # swing
    "strategies/swing_panic_dip.py::<module>": SWING, "strategies/swing_panic_dip.py::_unresolved_exit_order": SWING,
    "strategies/swing_panic_dip.py::check_intraday_emergency_stops": SWING,
    "strategies/swing_panic_dip.py::evaluate_market_close": SWING, "strategies/swing_panic_dip.py::execute_immediate_exit": SWING,
    "strategies/swing_panic_dip.py::execute_market_open": "S12: holds not yet sold at 09:30 never count against the swing slots (found by T10)", "strategies/swing_panic_dip.py::get_active_swing_positions": SWING,
    "strategies/swing_panic_dip.py::on_entry_fill": SWING, "strategies/swing_panic_dip.py::on_exit_fill": SWING,
    "strategies/swing_panic_dip.py::to_ui_dict": SWING,
}


def _readers():
    found = set()
    for p in sorted(APP.rglob("*.py")):
        src = p.read_text()
        funcs = [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for i, line in enumerate(src.splitlines(), 1):
            if PATTERN.search(line.split("#")[0]):
                best = None
                for f in funcs:
                    if f.lineno <= i <= f.end_lineno and (best is None or f.lineno > best.lineno):
                        best = f
                found.add(f"{p.relative_to(APP)}::{best.name if best else '<module>'}")
    return found


def test_every_reader_of_positions_and_working_orders_has_a_decision():
    readers = _readers()
    missing = sorted(readers - set(DECISIONS))
    stale = sorted(set(DECISIONS) - readers)
    assert not missing, f"new readers without an overnight decision: {missing}"
    assert not stale, f"decisions for readers that no longer exist: {stale}"
    assert all(DECISIONS[k].strip() for k in readers)


def test_booking_orders_carry_the_policy_and_never_rest_in_the_engine(main_runtime):
    h = MainOvernight(main_runtime, at(date(2026, 10, 1), 15, 40))
    buy_night(h, date(2026, 10, 1))
    booked = [o for o in h.r.engine.orders.values() if o.strategy_id in osch.OVERNIGHT_IDS]
    assert len(booked) == 3
    assert all(o.execution_policy == osch.OVERNIGHT_POLICY and o.broker_order_id is None
               and o.broker_client_id is None and o.status.value == "FILLED" for o in booked)
    assert not any(o.id in h.r.engine.working_orders for o in booked)
    # the 5 s settle loop never reads them, even if an id were attached (R2-11)
    booked[0].broker_client_id = "adt-ovn-NVDA-20261001-buy-1"
    calls_before = len(h.alpaca.requests)
    assert h.r.engine.settle_broker_orders() == []
    assert len(h.alpaca.requests) == calls_before
    booked[0].broker_client_id = None


def test_holds_survive_a_full_main_loop_pass(main_runtime):
    r = main_runtime
    thu, fri = date(2026, 10, 1), date(2026, 10, 2)
    h = MainOvernight(r, at(thu, 15, 40))
    buy_night(h, thu)
    queue_sales(h, thu)
    held = {s: (p.shares, p.avg_entry_price) for s, p in r.account.positions.items()}
    equity = r.account.equity
    sales = sorted(o["id"] for o in h.alpaca.live(side="sell"))
    # after hours and pre-market inputs through every handler
    for when, px in ((at(thu, 19, 30), 1.10), (at(thu, 23, 30), 0.90), (at(fri, 4, 0), 0.80), (at(fri, 9, 5), 1.20)):
        h.run(when, every=300)
        for sym in PRICES:
            h.bar(sym, PRICES[sym] * px)
            h.quote(sym, PRICES[sym] * px)
            asyncio.run(r.handle_trade_event(TradeEvent(sym, 1, PRICES[sym] * px, 100, "V", h.clock.now)))
        news = NewsEvent(int(when.timestamp()), "NVDA misses badly", "", ["NVDA", "IREN", "HUT"], "test", h.clock.now,
                         sentiment_score=-0.95, sentiment_confidence=0.95)
        asyncio.run(r.handle_news_event(news))
        asyncio.run(r.handle_vix_print(VixPrint(30.0, h.clock.now, h.clock.now, 0.0, "ready", "connected",
                                                VixRegime.ELEVATED, 0.5)))
        h.compare()
        assert {s: (p.shares, p.avg_entry_price) for s, p in r.account.positions.items()} == held
        assert r.account.equity == equity                         # S15: never re-marked before the sale
        assert sorted(o["id"] for o in h.alpaca.live(side="sell")) == sales
        assert r.broker_state["mismatch"] is False
        assert all(r.account.positions[s].market_price == PRICES[s] for s in PRICES)
    assert r.last_session_date == fri
    writes = [(m, p, b) for m, p, b, _q in h.alpaca.requests if m in ("POST", "DELETE", "PATCH")]
    assert all(m == "POST" and b["client_order_id"].startswith("adt-ovn-") for m, p, b in writes), writes


def test_a_partial_opening_sale_leaves_the_rest_at_its_buy_price(main_runtime):
    """S15 during the sale: the unsold shares of a partly filled opening sale stay at their buy
    price, so equity moves only by the realized part (the offset covers exactly that)."""
    r = main_runtime
    thu, fri = date(2026, 10, 1), date(2026, 10, 2)
    h = MainOvernight(r, at(thu, 15, 40))
    buy_night(h, thu)
    queue_sales(h, thu)
    h.run(at(fri, 9, 29, 50), every=120)
    h.set(at(fri, 9, 30))
    sale = next(o for o in h.alpaca.live("NVDA", "sell"))
    h.alpaca.fill(sale["id"], 20, 190.0)                     # 20 of 55 at the open
    h.run(at(fri, 9, 30, 20))
    pos = r.account.positions["NVDA"]
    assert pos.shares == 35 and pos.market_price == pos.avg_entry_price == 180.0
    assert r.account.realized_pnl == 200.0
    assert r.account.equity == 49_700.0 + 200.0
    assert r.overnight.realized_today() == 200.0


def test_a_buy_fill_never_merges_into_a_day_position(main_runtime):
    """R2-13: if a non overnight position is in ADT's book when the buy fills, the fill is not
    merged into it (the controller raises needs look and still sells its own shares)."""
    r = main_runtime
    thu = date(2026, 10, 1)
    h = MainOvernight(r, at(thu, 16, 0, 5))
    r.account.apply_fill("x", "NVDA", "BUY", 10, 180.0, 0.0, h.clock.now, strategy_id="vwap_pullback")
    ok = r.overnight._book({"kind": "fill", "role": "buy", "strategy_id": "overnight_nvda", "symbol": "NVDA",
                            "hold_symbol": "NVDA", "buy_date": thu.isoformat(), "qty": 55, "price": 181.0,
                            "at": h.clock.now.isoformat(), "alpaca_order_id": "o1",
                            "client_order_id": "adt-ovn-NVDA-20261001-buy-1", "realized": None})
    assert ok is False
    pos = r.account.positions["NVDA"]
    assert (pos.shares, pos.strategy_id, pos.avg_entry_price) == (10, "vwap_pullback", 180.0)
