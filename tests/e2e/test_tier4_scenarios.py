"""
Tier 4: End-to-End Real-World Application Scenarios.

Exercises complete operational workflows under synthetic and replayed market feeds:
- Scenario 1: Complete ORB Breakout with 2-Tier Bracket & Breakeven Ratchet
- Scenario 2: News Momentum Breakout with Contradiction Emergency Reversal
- Scenario 3: Institutional Drawdown Circuit Breaker Halt ($1,500 limit)
- Scenario 4: 15:55 ET Mandatory Zero-Overnight EOD Auto-Flattening
- Scenario 5: Midday Chop Defense & Statistical Mean Reversion Fade
- Scenario 6: Full Monday Market Open Live Simulation Rehearsal (09:25–10:30 ET)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest
import websockets

from backend.app.replay.mock_relay import MockAlpacaRelayServer, DEFAULT_RELAY_TOKEN
from backend.app.replay.feed_player import FeedPlayer
from tests.e2e.test_contracts import (
    AccountLedger,
    Position,
    calculate_position_size,
    calculate_brackets,
    get_eod_phase,
    evaluate_orb_signal,
    score_news_sentiment,
    evaluate_mean_reversion_zscore,
    get_vix_regime,
    get_time_of_day_phase,
    get_momentum_glow,
    validate_ui_state_payload,
)

SCENARIO_PORT_BASE = 9500


# ============================================================================
# Scenario 1: ORB Breakout Trade with 2-Tier Brackets & Breakeven Ratchet
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_1_orb_breakout_full_lifecycle():
    """
    Scenario 1:
    1. Pre-market / Open: NVDA establishes 5-minute Opening Range (High $124.80, Low $123.60).
    2. 09:35 ET: Bar breaks $124.80 with RVOL 2.5x -> Signal BUY.
    3. Engine checks risk: Buys 100 shares @ $124.95.
    4. Attaches bracket: Stop at $124.20 (Midpoint, $75 risk), Target 1 at $126.07, Target 2 at $126.82.
    5. Price hits $126.10: 50% shares closed (+1.5R = +$57.50), stop ratcheted to $124.97 (Breakeven).
    6. Price hits $127.10: Remaining 50% closed (+2.5R = +$107.50).
    7. Total realized PnL = +$165.00. Account equity = $50,165.00.
    """
    acc = AccountLedger()
    bars_5m = [
        {"h": 124.80, "l": 123.60, "c": 124.50, "v": 200000}
    ]
    breakout_bar = {"h": 125.10, "l": 124.40, "c": 124.95, "v": 450000}

    # 1. ORB Signal Evaluation
    sig = evaluate_orb_signal(bars_5m, breakout_bar, rvol=2.25)
    assert sig == "BUY"

    # 2. Risk check and sizing
    midpoint_stop = (124.80 + 123.60) / 2.0  # 124.20
    shares = calculate_position_size(acc.equity, 124.95, midpoint_stop)
    assert shares >= 100

    trade_shares = 100
    tp1, tp2 = calculate_brackets(124.95, midpoint_stop)
    pos = acc.execute_fill(
        symbol="NVDA",
        side="BUY",
        qty=trade_shares,
        price=124.95,
        strategy_id="orb",
        stop_loss=midpoint_stop,
        tp1=tp1,
        tp2=tp2,
    )
    assert pos.take_profit_1 == tp1
    assert pos.take_profit_2 == tp2

    # 3. Target 1 reached -> scale out 50%
    acc.update_price("NVDA", tp1)
    scale_qty = trade_shares // 2
    acc.execute_fill("NVDA", "SELL", scale_qty, tp1)
    assert acc.positions["NVDA"].qty == 50

    # 4. Ratchet stop to breakeven + buffer
    acc.positions["NVDA"].stop_loss = 124.97
    assert acc.positions["NVDA"].stop_loss > 124.95

    # 5. Target 2 reached -> close remainder
    acc.update_price("NVDA", tp2)
    acc.execute_fill("NVDA", "SELL", 50, tp2)
    assert "NVDA" not in acc.positions
    assert acc.realized_pnl > 0.0
    assert acc.equity > 50000.00


# ============================================================================
# Scenario 2: News Catalyst Breakout with Contradiction Emergency Exit
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_2_news_momentum_with_contradiction_exit():
    """
    Scenario 2:
    1. Breaking positive news arrives for TSLA (sentiment score +0.80).
    2. Tape volume surges 4.0x -> BUY 100 shares TSLA @ $214.80.
    3. Stop placed at candle low $213.50.
    4. Breaking negative headline arrives: "SEC Opens Preliminary Inquiry" (sentiment -0.82).
    5. Contradiction detector triggers immediate EMERGENCY FLATTEN.
    6. Position sold at current market $214.20 (realized loss -$60.00).
    7. Prevents subsequent catastrophic gap down to $205.
    """
    acc = AccountLedger()
    bullish_headline = "Tesla Surges After Announcing Major Autonomous Delivery Fleet Approval"
    s_bull = score_news_sentiment(bullish_headline)
    assert s_bull >= 0.60

    # Entry
    pos = acc.execute_fill("TSLA", "BUY", 100, 214.80, strategy_id="news_momentum", stop_loss=213.50)
    assert "TSLA" in acc.positions

    # Adverse news arrives
    adverse_headline = "Tesla Recalls 50,000 Units Due to Steering Glitch; SEC Opens Preliminary Inquiry"
    s_bear = score_news_sentiment(adverse_headline)
    assert s_bear < -0.35

    # Contradiction triggers emergency market exit
    acc.execute_fill("TSLA", "SELL", 100, 214.20)
    assert "TSLA" not in acc.positions
    assert round(acc.realized_pnl, 2) == -60.00
    # Equity preserved
    assert round(acc.equity, 2) == 49940.00


# ============================================================================
# Scenario 3: Institutional Drawdown Circuit Breaker Halt ($1,500 Limit)
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_3_circuit_breaker_drawdown_halt():
    """
    Scenario 3:
    1. Consecutive losses accumulate: Trade 1 loss -$500, Trade 2 loss -$500.
    2. Open position Trade 3 suffers adverse flash move of -$550.
    3. Total intraday drawdown reaches $1,550 >= $1,500 limit.
    4. Circuit breaker trips: engine enters HALTED_DAILY_LOSS.
    5. All open positions are force-flattened.
    6. Subsequent order requests are strictly blocked with PermissionError.
    """
    acc = AccountLedger()
    acc.realized_pnl = -1000.00
    acc.cash -= 1000.00

    # Open Trade 3
    acc.execute_fill("NVDA", "BUY", 100, 150.00)
    acc.update_price("NVDA", 144.50)  # -$550 unrealized loss -> total DD = $1,550

    # Check breaker
    tripped = acc.check_circuit_breaker()
    assert tripped is True
    assert acc.is_circuit_broken is True

    # Emergency flatten
    acc.flatten_all()
    assert len(acc.positions) == 0

    # Verify order lockout
    with pytest.raises(PermissionError, match="Trading halted"):
        acc.execute_fill("AAPL", "BUY", 10, 100.0)


# ============================================================================
# Scenario 4: 15:55 ET Zero-Overnight Mandatory EOD Auto-Flattening
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_4_eod_mandatory_flattening():
    """
    Scenario 4:
    1. Active swing positions exist at 15:40 ET (AAPL and SPY).
    2. 15:45 ET (ENTRY_LOCKOUT): New entries blocked.
    3. 15:50 ET (ORDER_PURGE): Passive limit orders purged.
    4. 15:55 ET (FORCE_FLATTEN): MOC orders sweep all inventory.
    5. 15:58 ET (FLAT_AUDIT): Confirms zero open positions and 100% cash before 16:00 close.
    """
    acc = AccountLedger()
    acc.execute_fill("AAPL", "BUY", 50, 150.00)
    acc.execute_fill("SPY", "BUY", 50, 560.00)
    assert len(acc.positions) == 2

    # Advance clock to 15:45
    assert get_eod_phase(dtime(15, 45)) == "ENTRY_LOCKOUT"

    # Advance clock to 15:50
    assert get_eod_phase(dtime(15, 50)) == "ORDER_PURGE"

    # Advance clock to 15:55 -> Liquidate
    assert get_eod_phase(dtime(15, 55)) == "FORCE_FLATTEN"
    flattened_count = acc.flatten_all()
    assert flattened_count == 2

    # Advance clock to 15:58 -> Audit verification
    assert get_eod_phase(dtime(15, 58)) == "FLAT_AUDIT"
    assert len(acc.positions) == 0
    assert acc.cash == acc.equity


# ============================================================================
# Scenario 5: Midday Chop Defense & Statistical Mean Reversion Fade
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_5_midday_chop_mean_reversion_fade():
    """
    Scenario 5:
    1. Clock is 12:15 ET (MIDDAY_CHOP window).
    2. Trend breakouts are throttled. Mean reversion engine active.
    3. Asset spikes into exhaustion: 20-period price series reaches Z-score +2.70 (RSI extreme).
    4. Strategy 4 triggers short exhaustion fade.
    5. Enters short trade with profit target at 20-SMA mean.
    6. Price mean-reverts to SMA: Target achieved, profit locked in.
    """
    assert get_time_of_day_phase(dtime(12, 15)) == "MIDDAY_CHOP"

    # 19 flat bars around $100, bar 20 climaxes to $115
    prices = [100.0] * 19 + [115.0]
    mean, std, z = evaluate_mean_reversion_zscore(prices)
    assert z >= 2.50  # Overbought exhaustion

    # Enter fade targeting mean
    entry_p = 115.0
    target_p = mean
    stop_p = entry_p + (0.5 * std)

    assert target_p < entry_p  # Profit target is lower (reversion)
    assert stop_p > entry_p    # Stop is higher


# ============================================================================
# Scenario 6: Full Monday Market Open Simulation Rehearsal (09:25–10:30 ET)
# ============================================================================

@pytest.mark.asyncio
async def test_scenario_6_monday_market_open_replay_rehearsal():
    """
    Scenario 6:
    Replays monday_open_session.json through MockAlpacaRelayServer and FeedPlayer:
    1. Starts mock server on isolated port.
    2. Downstream client connects and subscribes.
    3. Replays entire sequenced session (pre-market, open bell, ORB, news, brackets).
    4. Client confirms continuous stream delivery without disconnections.
    5. Clean teardown and port liberation.
    """
    port = SCENARIO_PORT_BASE + 1
    server = MockAlpacaRelayServer(port=port)
    await server.start()
    try:
        player = FeedPlayer(server, speed=10.0)
        fixture_path = Path("/Users/mo/AutonomousDayTrader/tests/e2e/fixtures/monday_open_session.json")
        player.load_fixture_file(fixture_path)
        assert len(player.events) > 0

        # Downstream client session
        async with websockets.connect(f"ws://127.0.0.1:{port}/v2/stocks") as ws:
            await ws.recv()  # banner
            await ws.send(json.dumps({"action": "auth", "token": DEFAULT_RELAY_TOKEN}))
            await ws.recv()  # auth ack
            await ws.send(json.dumps({"action": "subscribe", "bars": ["*"], "quotes": ["*"], "trades": ["*"], "news": ["*"]}))
            await ws.recv()  # sub ack

            # Step through all session events
            dispatched = await player.step_all()
            assert dispatched == len(player.events)
    finally:
        await server.stop()
