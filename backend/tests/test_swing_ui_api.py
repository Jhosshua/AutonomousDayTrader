"""backend/tests/test_swing_ui_api.py
Unit and API integration tests for Swing Engine UI serialization and WebSocket/REST action handling.
"""
from datetime import date
import pytest
from starlette.testclient import TestClient

from backend.app.core.account import Position, PositionSide, TradingArm
from backend.app.main import app, broadcast_ui_state, swing_strategy_engine


@pytest.fixture
def client():
    return TestClient(app)


def test_swing_engine_to_ui_dict_structure():
    """Verify that swing_strategy_engine.to_ui_dict() contains all required fields."""
    ui_dict = swing_strategy_engine.to_ui_dict()
    assert "status" in ui_dict
    assert "strategy_name" in ui_dict
    assert "allocated_capital" in ui_dict
    assert "slot_notional" in ui_dict
    assert "max_slots" in ui_dict
    assert "active_slots_used" in ui_dict
    assert "available_slots" in ui_dict
    assert "flattening_exempt" in ui_dict
    assert ui_dict["flattening_exempt"] is True
    assert "candidates" in ui_dict
    assert "positions" in ui_dict
    assert len(ui_dict["candidates"]) == 5

    # Check candidate structure
    for cand in ui_dict["candidates"]:
        assert "symbol" in cand
        assert "price" in cand
        assert "sma_200" in cand
        assert "sma_200_pass" in cand
        assert "rs_60d_stock" in cand
        assert "rs_60d_qqq" in cand
        assert "rs_pass" in cand
        assert "rsi_2" in cand
        assert "rsi_pass" in cand
        assert "earnings_blackout" in cand
        assert "status" in cand
        assert "daily_atr_14" in cand


def test_get_swing_state_endpoint(client):
    """Verify GET /api/swing/state returns 200 with full UI state."""
    response = client.get("/api/swing/state")
    assert response.status_code == 200
    data = response.json()
    assert data["strategy_name"] == "2-Day Panic Dip (Connors RSI-2)"
    assert data["max_slots"] == 2
    assert len(data["candidates"]) == 5


def test_swing_actions_and_manual_overrides(client):
    """Verify operator manual overrides: EXIT_NEXT_OPEN, EXIT_IMMEDIATE, TIGHTEN_STOP."""
    # Setup mock active swing position in account
    sym = "MU"
    pos = Position(
        symbol=sym,
        side=PositionSide.LONG,
        shares=100,
        avg_entry_price=105.0,
        market_price=110.0,
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
        stop_loss_price=98.0,
        entry_date=date.today(),
        holding_days=2,
    )
    swing_strategy_engine.account.positions[sym] = pos

    # 1. Test TIGHTEN_STOP
    resp = client.post("/api/swing/action", json={
        "action": "SWING_TIGHTEN_STOP",
        "symbol": sym,
        "new_stop": 102.50,
    })
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert pos.stop_loss_price == 102.50

    # 2. Test EXIT_NEXT_OPEN
    resp = client.post("/api/swing/action", json={
        "action": "SWING_EXIT_NEXT_OPEN",
        "symbol": sym,
    })
    assert resp.status_code == 200
    assert resp.json()["staged"] is True
    assert swing_strategy_engine.staged_manager.is_staged_for_exit(sym)

    # 3. Test EXIT_IMMEDIATE
    resp = client.post("/api/swing/action", json={
        "action": "SWING_EXIT_IMMEDIATE",
        "symbol": sym,
    })
    assert resp.status_code == 200
    assert resp.json()["result"]["shares"] == 100
    assert not swing_strategy_engine.staged_manager.is_staged_for_exit(sym)
    assert sym not in swing_strategy_engine.account.positions


@pytest.mark.asyncio
async def test_broadcast_ui_state_includes_swing():
    """Verify broadcast_ui_state includes 'swing' field."""
    # Force broadcast execution
    await broadcast_ui_state(force=True)
    ui_dict = swing_strategy_engine.to_ui_dict()
    assert ui_dict["max_slots"] == 2


@pytest.mark.asyncio
async def test_swing_engine_to_ui_dict_with_active_positions(client):
    """Verify to_ui_dict and broadcast_ui_state serialize cleanly when active swing positions exist."""
    pos = Position(
        symbol="MU",
        side=PositionSide.LONG,
        shares=100,
        avg_entry_price=105.0,
        market_price=110.0,
        arm=TradingArm.SWING,
        strategy_id="swing_panic_dip",
        stop_loss_price=98.0,
        entry_date=date.today(),
        holding_days=2,
    )
    swing_strategy_engine.account.positions["MU"] = pos
    try:
        ui_dict = swing_strategy_engine.to_ui_dict()
        assert len(ui_dict["positions"]) == 1
        pos_ui = ui_dict["positions"][0]
        assert pos_ui["symbol"] == "MU"
        assert "exit_triggers" in pos_ui
        assert "sma_5_cross" in pos_ui["exit_triggers"]
        assert "rsi_70_cross" in pos_ui["exit_triggers"]
        assert "time_stop_day_5" in pos_ui["exit_triggers"]
        assert "earnings_tomorrow" in pos_ui["exit_triggers"]
        await broadcast_ui_state(force=True)
    finally:
        swing_strategy_engine.account.positions.pop("MU", None)

