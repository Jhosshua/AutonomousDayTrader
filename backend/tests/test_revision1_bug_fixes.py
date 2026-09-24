"""backend/tests/test_revision1_bug_fixes.py

Regression tests for the 6 backend bugs found by the Codex attack on
PLAN_2026_09_24_plain_language_ui.md ("Revision 1"): B1-B6. Each test fails against the
pre-fix code and passes after the fix (verified manually via `git stash`).
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone

import pytest

from backend.app.core.account import AccountStatus, Position, PositionSide, TradingArm
from backend.app.core.flattening import ET_TZ
from backend.app.core.trading_windows import strategy_window
from backend.app import main


@pytest.fixture(autouse=True)
def reset_test_state():
    """Mirrors backend/tests/test_swing_flattening_exemption.py's reset fixture."""
    def _reset():
        main.flattening_engine.reset_for_new_session()
        main.last_session_date = None
        main.account.cash = main.account.initial_balance
        main.account.equity = main.account.initial_balance
        main.account.daily_starting_equity = main.account.initial_balance
        main.account.realized_pnl = 0.0
        main.account.unrealized_pnl = 0.0
        main.account.fees_paid = 0.0
        main.account.daily_drawdown_dollars = 0.0
        main.account.daily_drawdown_pct = 0.0
        main.account.positions.clear()
        main.account.status = AccountStatus.ACTIVE
        main.risk_engine.reset_daily_metrics(main.account.initial_balance)
        main.engine.orders.clear()
        main.engine.working_orders.clear()
        main.bracket_manager.brackets.clear()
        main.bracket_manager.symbol_to_bracket.clear()
        main.swing_reserved_symbols.clear()
        main.swing_staged_order_manager.clear()
        main.ui_clients.clear()
        # broadcast_ui_state throttles on a module-global timestamp; force=True in this file's
        # own tests still advances it, which would otherwise starve the very next unrelated
        # non-forced broadcast test in the suite of a real send.
        main._last_broadcast_time = 0.0

    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# B1: FLATTEN_ALL must skip swing holdings and report per-symbol truth
# ---------------------------------------------------------------------------
def test_b1_flatten_all_skips_swing_and_reports_truthfully():
    now_dt = datetime.now(timezone.utc)
    main.account.apply_fill("f1", "AAPL", "BUY", 10, 150.0, 0.0, now_dt, arm=TradingArm.INTRADAY, strategy_id="orb")
    main.account.apply_fill(
        "f2", "MU", "BUY", 20, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip"
    )
    assert "AAPL" in main.account.positions
    assert "MU" in main.account.positions

    result = asyncio.run(main.manual_flatten())

    assert result["flattened"] == ["AAPL"]
    assert "AAPL" not in main.account.positions

    # The swing position must be completely untouched: still open, same size.
    assert "MU" in main.account.positions
    assert main.account.positions["MU"].shares == 20
    skipped_symbols = {s["symbol"] for s in result["skipped"]}
    assert "MU" in skipped_symbols


def test_b1_flatten_position_on_swing_symbol_is_skipped_not_flattened():
    """Even a targeted FLATTEN_POSITION for a swing symbol must never close it via this path."""
    now_dt = datetime.now(timezone.utc)
    main.account.apply_fill(
        "f1", "MU", "BUY", 20, 100.0, 0.0, now_dt, arm=TradingArm.SWING, strategy_id="swing_panic_dip"
    )
    result = asyncio.run(main.manual_flatten(main.FlattenRequest(symbol="MU")))
    assert result["flattened"] == []
    assert "MU" in main.account.positions
    assert any(s["symbol"] == "MU" for s in result["skipped"])


# ---------------------------------------------------------------------------
# B2: swing SET stop must reject unless current_stop < proposed < market_price, and finite
# ---------------------------------------------------------------------------
def test_b2_swing_tighten_stop_rejects_loosening_and_invalid_values():
    sym = "MU"
    pos = Position(
        symbol=sym, side=PositionSide.LONG, shares=100, avg_entry_price=100.0, market_price=110.0,
        arm=TradingArm.SWING, strategy_id="swing_panic_dip", stop_loss_price=95.0,
    )
    main.swing_strategy_engine.account.positions[sym] = pos

    # Loosening (below current stop) rejected.
    assert main.swing_strategy_engine.tighten_stop(sym, 90.0) is False
    assert pos.stop_loss_price == 95.0

    # Not strictly tighter (equal to current stop) rejected.
    assert main.swing_strategy_engine.tighten_stop(sym, 95.0) is False

    # At/above market price rejected.
    assert main.swing_strategy_engine.tighten_stop(sym, 110.0) is False
    assert main.swing_strategy_engine.tighten_stop(sym, 115.0) is False

    # Non-finite rejected.
    assert main.swing_strategy_engine.tighten_stop(sym, float("nan")) is False
    assert main.swing_strategy_engine.tighten_stop(sym, float("inf")) is False

    # A valid tightening (current_stop < proposed < market_price) is accepted.
    assert main.swing_strategy_engine.tighten_stop(sym, 99.0) is True
    assert pos.stop_loss_price == 99.0


# ---------------------------------------------------------------------------
# B3: a staged EXIT must survive the 09:45 purge and fire at the NEXT session's open
# ---------------------------------------------------------------------------
def test_b3_staged_exit_survives_purge_stale_entry_does_not():
    main.account.positions["MU"] = Position(
        symbol="MU", side=PositionSide.LONG, shares=50, avg_entry_price=100.0, market_price=100.0,
        arm=TradingArm.SWING, strategy_id="swing_panic_dip", stop_loss_price=90.0,
    )

    staged_at = datetime(2026, 9, 24, 11, 0, 0, tzinfo=ET_TZ)
    exit_order = main.swing_staged_order_manager.stage_sell(
        "MU", 50, staged_at.date(), "OPERATOR_MANUAL_EXIT_AT_OPEN"
    )
    exit_order.created_at = staged_at.astimezone(timezone.utc)

    # A staged ENTRY from the same moment should still be purged as stale once the open
    # execution window (09:45 ET) has passed without it firing.
    entry_order = main.swing_staged_order_manager.stage_buy("AMD", 25000.0, 10.0, staged_at.date(), "QUALIFIED")
    entry_order.created_at = staged_at.astimezone(timezone.utc)

    # Advance past 11:01 ET the same day.
    main._expire_stale_staged_swing_orders(datetime(2026, 9, 24, 11, 5, 0, tzinfo=ET_TZ))
    assert main.swing_staged_order_manager.is_staged_for_exit("MU"), "staged exit purged too early"
    assert not main.swing_staged_order_manager.is_staged_for_entry("AMD"), "stale staged entry should be purged"

    # Advance past 16:00 ET the same day: the exit must still be staged.
    main._expire_stale_staged_swing_orders(datetime(2026, 9, 24, 16, 30, 0, tzinfo=ET_TZ))
    assert main.swing_staged_order_manager.is_staged_for_exit("MU")

    # And it must survive all the way to, and fire at, the NEXT session's 09:30 open.
    result = main.swing_strategy_engine.execute_market_open(
        open_prices={"MU": 101.0},
        open_time=datetime(2026, 9, 25, 9, 30, 0, tzinfo=ET_TZ),
        apply_slippage=False,
    )
    assert any(e["symbol"] == "MU" for e in result["exits"])
    assert "MU" not in main.account.positions


# ---------------------------------------------------------------------------
# B4: REST /api/swing/action must checkpoint exactly like the WS path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_b4_rest_swing_action_checkpoints_like_ws(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_checkpoint_runtime", lambda reason, *a, **k: calls.append(reason) or True)

    sym = "MU"
    pos = Position(
        symbol=sym, side=PositionSide.LONG, shares=10, avg_entry_price=100.0, market_price=105.0,
        arm=TradingArm.SWING, strategy_id="swing_panic_dip", stop_loss_price=95.0,
    )
    main.swing_strategy_engine.account.positions[sym] = pos

    req = main.SwingActionRequest(action="SWING_TIGHTEN_STOP", symbol=sym, new_stop=99.0)
    resp = await main.post_swing_action(req)

    assert resp["success"] is True
    assert "SWING_MANUAL_TIGHTEN_STOP" in calls, "REST swing action path never checkpointed"


# ---------------------------------------------------------------------------
# B5: trading_windows.strategy_window adds `ranges` and `trading_day`
# ---------------------------------------------------------------------------
def test_b5_strategy_window_ranges_and_trading_day():
    def vwap_permitted(_strategy_id: str, phase: str) -> bool:
        return phase in ("OPEN_VOLATILITY_FLUSH", "AFTERNOON_PUSH", "POWER_HOUR")

    # Thursday (weekday): two ranges, trading_day True.
    thursday = datetime(2026, 9, 24, 12, 0, 0, tzinfo=ET_TZ)
    w = strategy_window("vwap_pullback", thursday, vwap_permitted)
    assert w["ranges"] == [["09:30", "10:00"], ["14:00", "15:45"]]
    assert w["trading_day"] is True

    # Saturday: same schedule shape, trading_day False.
    saturday = datetime(2026, 9, 26, 12, 0, 0, tzinfo=ET_TZ)
    w_sat = strategy_window("vwap_pullback", saturday, vwap_permitted)
    assert w_sat["ranges"] == [["09:30", "10:00"], ["14:00", "15:45"]]
    assert w_sat["trading_day"] is False

    # Thanksgiving (NYSE holiday): trading_day False.
    holiday = datetime(2026, 11, 26, 12, 0, 0, tzinfo=ET_TZ)
    w_hol = strategy_window("vwap_pullback", holiday, vwap_permitted)
    assert w_hol["trading_day"] is False


# ---------------------------------------------------------------------------
# B6: REST /api/account and the WS broadcast must agree on daily_pnl fields
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_b6_rest_account_and_ws_broadcast_daily_pnl_agree():
    main.account.equity = main.account.initial_balance - 118.69
    main.account.daily_starting_equity = main.account.initial_balance
    main.account.realized_pnl = -118.69
    # Simulate the historically observed divergence directly on the stored field: REST and WS
    # both read `account.daily_drawdown_dollars`, so setting it once here proves either transport
    # would report the same number were it non-zero.
    main.account.daily_drawdown_dollars = 118.69
    main.account.daily_drawdown_pct = 0.0024

    rest = await main.get_account_state()

    captured: dict = {}

    class _FakeWS:
        async def send_text(self, raw: str) -> None:
            captured["raw"] = raw

    main.ui_clients.add(_FakeWS())
    await main.broadcast_ui_state(force=True)
    ws_account = json.loads(captured["raw"])["account"]

    assert rest["daily_pnl"] == ws_account["daily_pnl"]
    assert rest["daily_pnl_pct"] == ws_account["daily_pnl_pct"]
    assert rest["daily_drawdown_dollars"] == ws_account["daily_drawdown"]
    # Sanity: not trivially both-zero (that would pass even if both sides were still broken).
    assert rest["daily_pnl"] != 0.0
    assert rest["daily_drawdown_dollars"] != 0.0
