"""backend/tests/unit/test_adaptation.py
Unit tests for the Dynamic Self-Adaptation Engine:
- VIX volatility regime classification & invariant dollar risk sizing
- Time-of-Day execution phase transitions & strategy permission gating
- Concurrency cap and multi-strategy signal arbitration priority
"""
from __future__ import annotations
from datetime import datetime, time as dtime, timezone
import pytest

from backend.app.models.events import OrderSide, OrderType, VixPrint, VixRegime
from backend.app.strategies.adaptation import (
    DynamicAdaptationEngine,
    TimeOfDayPhase,
    calculate_position_size,
    get_time_of_day_phase,
    get_vix_regime,
)
from backend.app.strategies.base import SignalEvent


def test_vix_regime_classification_and_multipliers():
    # 1. Low Regime (<15)
    r_low, s_low, sm_low = get_vix_regime(13.2)
    assert r_low == "LOW"
    assert s_low == 1.20
    assert sm_low == 0.85

    # 2. Normal Regime (15-25)
    r_norm, s_norm, sm_norm = get_vix_regime(18.5)
    assert r_norm == "NORMAL"
    assert s_norm == 1.00
    assert sm_norm == 1.00

    # 3. Elevated Regime (25-35)
    r_elev, s_elev, sm_elev = get_vix_regime(29.0)
    assert r_elev == "ELEVATED"
    assert s_elev == 0.70
    assert sm_elev == 1.40

    # 4. Crisis Regime (>=35)
    r_cris, s_cris, sm_cris = get_vix_regime(45.0)
    assert r_cris == "CRISIS"
    assert s_cris == 0.35
    assert sm_cris == 2.00


def test_vix_invariant_dollar_risk_scaling():
    equity = 50000.00
    # Use entry price $50.0 and stop $45.0 ($5.00 distance) so capital allocation cap (250 shares) is not binding
    shares_low = calculate_position_size(equity, 50.0, 45.0, vix_multiplier=1.20)
    shares_norm = calculate_position_size(equity, 50.0, 45.0, vix_multiplier=1.00)
    shares_cris = calculate_position_size(equity, 50.0, 45.0, vix_multiplier=0.35)

    assert shares_low > shares_norm > shares_cris > 0


def test_time_of_day_phase_transitions():
    assert get_time_of_day_phase(dtime(9, 15)) == "PRE_MARKET"
    assert get_time_of_day_phase(dtime(9, 35)) == "OPEN_VOLATILITY_FLUSH"
    assert get_time_of_day_phase(dtime(10, 30)) == "TREND_CONTINUATION"
    assert get_time_of_day_phase(dtime(12, 15)) == "MIDDAY_CHOP"
    assert get_time_of_day_phase(dtime(14, 30)) == "AFTERNOON_PUSH"
    assert get_time_of_day_phase(dtime(15, 15)) == "POWER_HOUR"
    assert get_time_of_day_phase(dtime(15, 50)) == "EOD_FLATTEN"
    assert get_time_of_day_phase(dtime(16, 5)) == "POST_MARKET"


def test_strategy_permissions_by_phase():
    engine = DynamicAdaptationEngine()

    # Pre-market: all blocked
    assert not engine.is_strategy_permitted("orb", "PRE_MARKET")
    assert not engine.is_strategy_permitted("vwap_pullback", "PRE_MARKET")
    assert not engine.is_strategy_permitted("news_momentum", "PRE_MARKET")
    assert not engine.is_strategy_permitted("mean_reversion", "PRE_MARKET")

    # Open flush: ORB permitted, Mean Reversion blocked
    assert engine.is_strategy_permitted("orb", "OPEN_VOLATILITY_FLUSH")
    assert not engine.is_strategy_permitted("mean_reversion", "OPEN_VOLATILITY_FLUSH")

    # Midday chop: ORB blocked, Mean Reversion permitted, VWAP Pullback blocked
    assert not engine.is_strategy_permitted("orb", "MIDDAY_CHOP")
    assert engine.is_strategy_permitted("mean_reversion", "MIDDAY_CHOP")
    assert not engine.is_strategy_permitted("vwap_pullback", "MIDDAY_CHOP")

    # Power hour: ORB blocked
    assert not engine.is_strategy_permitted("orb", "POWER_HOUR")
    assert engine.is_strategy_permitted("news_momentum", "POWER_HOUR")

    # EOD flatten: all blocked
    assert not engine.is_strategy_permitted("orb", "EOD_FLATTEN")
    assert not engine.is_strategy_permitted("news_momentum", "EOD_FLATTEN")


def test_concurrency_cap_and_arbitration_priority():
    engine = DynamicAdaptationEngine(max_concurrent_positions=3)
    engine.current_time_phase = "TREND_CONTINUATION"

    sig_news = SignalEvent("AAPL", OrderSide.BUY, OrderType.MARKET, 150.0, 148.0, 153.0, 155.0, "news_momentum", 0.9, "News")
    sig_orb = SignalEvent("NVDA", OrderSide.BUY, OrderType.MARKET, 120.0, 118.0, 123.0, 125.0, "orb", 0.85, "ORB")
    sig_vwap = SignalEvent("MSFT", OrderSide.BUY, OrderType.MARKET, 400.0, 396.0, 406.0, 410.0, "vwap_pullback", 0.75, "VWAP")
    sig_mr = SignalEvent("TSLA", OrderSide.SELL, OrderType.MARKET, 220.0, 224.0, 215.0, 212.0, "mean_reversion", 0.70, "MR")

    # Arbitrate collision: News > ORB > VWAP > MR
    sorted_sigs = engine.arbitrate_signals([sig_mr, sig_vwap, sig_news, sig_orb])
    assert [s.strategy_id for s in sorted_sigs] == ["news_momentum", "orb", "vwap_pullback", "mean_reversion"]

    # Concurrency limit enforcement:
    # 0 open positions -> approved
    approved, _, qty = engine.evaluate_signal_admission(sig_news, equity=50000.0, current_positions_count=0, is_symbol_active=False)
    assert approved is True
    assert qty > 0

    # 3 open positions, new symbol -> rejected by concurrency gate
    appr_4th, reason, _ = engine.evaluate_signal_admission(sig_news, equity=50000.0, current_positions_count=3, is_symbol_active=False)
    assert appr_4th is False
    assert "CONCURRENCY_GATE_DENIED" in reason

    # 3 open positions, but existing symbol -> permitted (modifying/adding to existing)
    appr_exist, _, _ = engine.evaluate_signal_admission(sig_news, equity=50000.0, current_positions_count=3, is_symbol_active=True)
    assert appr_exist is True


def test_market_context_telemetry():
    engine = DynamicAdaptationEngine(default_vix=18.5)
    engine.current_time_phase = "TREND_CONTINUATION"
    ctx = engine.get_market_context()

    assert ctx["vix"] == 18.5
    assert ctx["vix_regime"] == "NORMAL"
    assert ctx["time_phase"] == "TREND_CONTINUATION"
    assert ctx["market_status"] == "OPEN"
    assert ctx["sizing_multiplier"] == 1.00
    assert ctx["stop_multiplier"] == 1.00
