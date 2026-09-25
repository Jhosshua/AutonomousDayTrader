"""Focused regressions for the 2026-09-25 strategy audit."""
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from backend.app.core.bracket import BracketOrder, DynamicBracketManager
from backend.app.core.account import PaperTradingAccount, TradingArm
from backend.app.core.engine import ExecutionEngine
from backend.app.core.persistence import decode_runtime_value, encode_runtime_value
from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies.adaptation import DynamicAdaptationEngine
from backend.app.strategies.base import SignalEvent
from backend.app.strategies.news_momentum import NewsMomentumStrategy, PendingCatalyst
from backend.app.strategies.swing_panic_dip import StagedSwingOrder, SwingStagedOrderManager, SwingStrategyEngine


def signal(strategy: str, side=OrderSide.BUY, entry=100.0, stop=99.0, tp1=100.8, tp2=101.8):
    return SignalEvent(
        symbol="AAPL", side=side, order_type=OrderType.MARKET,
        entry_price=entry, stop_loss=stop, take_profit_1=tp1,
        take_profit_2=tp2, strategy_id=strategy, confidence=0.9,
        reason="AUDIT_TEST", timestamp=datetime(2026, 9, 25, 14, 30, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("strategy", ["orb", "news_momentum"])
@pytest.mark.parametrize("side,entry,stop,adapted,fill", [
    (OrderSide.BUY, 100.0, 99.0, 98.6, 100.1),
    (OrderSide.SELL, 100.0, 101.0, 101.4, 99.9),
])
def test_r_based_targets_use_adapted_stop_and_actual_fill(strategy, side, entry, stop, adapted, fill):
    import backend.app.main as runtime

    direction = 1 if side == OrderSide.BUY else -1
    sig = signal(strategy, side=side, entry=entry, stop=stop,
                 tp1=entry + direction * 0.8, tp2=entry + direction * 1.8)
    t1, t2, error = runtime._intraday_target_overrides(sig, adapted_stop=adapted)
    assert (t1, t2, error) == (None, None, None)
    brackets = DynamicBracketManager()
    bracket = brackets.create_bracket("brk_test", "AAPL", "LONG" if direction == 1 else "SHORT",
                                      10, entry, adapted, strategy_id=strategy,
                                      target_1_override=t1, target_2_override=t2)
    brackets.activate_bracket_on_fill(bracket.bracket_id, 10, fill, sig.timestamp)
    actual_risk = abs(fill - adapted)
    assert bracket.target_1_price == round(fill + direction * 0.8 * actual_risk, 2)
    assert bracket.target_2_price == round(fill + direction * 1.8 * actual_risk, 2)


def test_admission_sizes_using_adapted_stop():
    adaptation = DynamicAdaptationEngine()
    adaptation.is_strategy_permitted = lambda *_args: True
    seen = []
    adaptation.calculate_adapted_size = lambda **kw: seen.append(kw["stop_loss_price"]) or 5
    approved, _, shares = adaptation.evaluate_signal_admission(
        signal("orb"), equity=50000.0, current_positions_count=0,
        is_symbol_active=False, stop_loss_price=98.6,
    )
    assert approved and shares == 5 and seen == [98.6]


def test_mean_reversion_rejects_structural_target_after_vix_widens_stop():
    import backend.app.main as runtime

    sig = signal("mean_reversion", tp1=101.1, tp2=101.5)
    assert runtime._intraday_target_overrides(sig, adapted_stop=99.0)[2] is None
    assert "20-SMA target" in runtime._intraday_target_overrides(sig, adapted_stop=98.6)[2]


def test_vwap_uses_r_fallback_when_adapted_risk_erodes_band_target():
    import backend.app.main as runtime

    sig = signal("vwap_pullback", tp1=100.6, tp2=101.1)
    assert runtime._intraday_target_overrides(sig, adapted_stop=99.0)[:2] == (100.6, 101.1)
    assert runtime._intraday_target_overrides(sig, adapted_stop=98.6) == (None, None, None)
    sig.target_1_is_r_fallback = True
    sig.take_profit_1 = 100.8  # still above 0.50R, but was the strategy's raw-stop fallback
    assert runtime._intraday_target_overrides(sig, adapted_stop=98.6) == (None, None, None)
    sig.target_1_is_r_fallback = False
    sig.target_2_is_r_fallback = True
    assert runtime._intraday_target_overrides(sig, adapted_stop=99.0) == (100.8, None, None)


def test_vwap_actual_fill_falls_back_but_old_bracket_keeps_its_target():
    brackets = DynamicBracketManager()
    ts = datetime(2026, 9, 25, 14, 30, tzinfo=timezone.utc)
    new = brackets.create_bracket("new", "AAPL", "LONG", 10, 100.0, 98.6,
                                  strategy_id="vwap_pullback", target_1_override=100.8,
                                  target_2_override=101.8, min_target_1_r=0.50)
    old = brackets.create_bracket("old", "MSFT", "LONG", 10, 100.0, 98.6,
                                  strategy_id="vwap_pullback", target_1_override=100.8,
                                  target_2_override=101.8)
    brackets.activate_bracket_on_fill("new", 10, 100.2, ts)
    brackets.activate_bracket_on_fill("old", 10, 100.2, ts)
    assert new.target_1_price == round(100.2 + 0.8 * 1.6, 2)
    assert new.target_2_price == round(100.2 + 1.8 * 1.6, 2)
    assert old.target_1_price == 100.8 and old.target_2_price == 101.8


@pytest.mark.parametrize("stop_multiplier", [0.85, 1.0, 1.4, 2.0])
@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
def test_vwap_r_fallback_stays_fill_anchored_across_volatility_regimes(stop_multiplier, side):
    import backend.app.main as runtime

    direction = 1 if side == OrderSide.BUY else -1
    sig = signal("vwap_pullback", side=side, stop=100.0 - direction,
                 tp1=100.0 + direction * 0.8, tp2=100.0 + direction * 1.8)
    sig.target_1_is_r_fallback = True
    adaptation = DynamicAdaptationEngine()
    adaptation.current_stop_multiplier = stop_multiplier
    adapted_stop = adaptation.calculate_adapted_stop(sig)
    t1, t2, error = runtime._intraday_target_overrides(sig, adapted_stop)
    assert (t1, t2, error) == (None, None, None)
    fill = 100.0 + direction * 0.1
    manager = DynamicBracketManager()
    bracket = manager.create_bracket("fill", "AAPL", "LONG" if direction == 1 else "SHORT", 10,
                                     100.0, adapted_stop, target_1_override=t1, target_2_override=t2)
    manager.activate_bracket_on_fill("fill", 10, fill, sig.timestamp)
    risk = abs(fill - adapted_stop)
    assert direction * (bracket.target_1_price - fill) == pytest.approx(round(0.8 * risk, 2), abs=0.01)
    assert direction * (bracket.target_2_price - fill) == pytest.approx(round(1.8 * risk, 2), abs=0.01)


def _bar(ts: str, volume: int) -> BarEvent:
    return BarEvent("AAPL", 100.0, 101.0, 99.8, 100.8, volume, datetime.fromisoformat(ts))


def test_news_premarket_bar_does_not_consume_open_catalyst_or_volume_baseline():
    strat = NewsMomentumStrategy()
    catalyst = PendingCatalyst("Strong approval and raised guidance", 0.85, ["AAPL"],
                               datetime.fromisoformat("2026-09-25T09:28:00-04:00"))
    strat.pending_catalysts["AAPL"] = [catalyst]
    assert strat.on_bar(_bar("2026-09-25T09:29:00-04:00", 1200000)) == []
    assert not catalyst.processed and "AAPL" not in strat.recent_bars
    sigs = strat.on_bar(_bar("2026-09-25T09:30:00-04:00", 1200000))
    assert len(sigs) == 1 and catalyst.processed
    assert sigs[0].volume_surge == pytest.approx(2.4)


def test_news_requires_strictly_more_than_two_times_volume():
    strat = NewsMomentumStrategy()
    catalyst = PendingCatalyst("Strong approval and raised guidance", 0.85, ["AAPL"],
                               datetime.fromisoformat("2026-09-25T09:29:00-04:00"))
    strat.pending_catalysts["AAPL"] = [catalyst]
    assert strat.on_bar(_bar("2026-09-25T09:30:00-04:00", 1000000)) == []
    assert not catalyst.processed


@pytest.mark.asyncio
async def test_news_contradiction_does_not_liquidate_swing_arm():
    import backend.app.main as runtime

    runtime.reset_runtime_state()
    try:
        ts = datetime(2026, 9, 25, 14, 30, tzinfo=timezone.utc)
        runtime.account.apply_fill(
            order_id="test_swing", symbol="AMD", side="BUY", qty=10,
            price=100.0, fee=0.0, timestamp=ts, arm=TradingArm.SWING,
            strategy_id="swing_panic_dip", stop_loss_price=90.0,
        )
        before = len(runtime.engine.orders)
        contradiction = signal("news_momentum", side=OrderSide.SELL, entry=100.0,
                               stop=101.0, tp1=99.2, tp2=98.2)
        contradiction.symbol = "AMD"
        contradiction.reason = "NEWS_CONTRADICTION_CIRCUIT_BREAKER"
        await runtime.execute_strategy_signal(contradiction)
        assert runtime.account.positions["AMD"].shares == 10
        assert len(runtime.engine.orders) == before
    finally:
        runtime.reset_runtime_state()


def test_expired_swing_entry_keeps_amd_reserved_until_alpaca_order_resolves():
    import backend.app.main as runtime

    runtime.reset_runtime_state()
    try:
        staged = runtime.swing_staged_order_manager.stage_buy(
            "AMD", 25000.0, 4.0, datetime(2026, 9, 24, tzinfo=timezone.utc).date(), "PANIC_DIP"
        )
        staged.created_at = datetime(2026, 9, 25, 13, 25, tzinfo=timezone.utc)
        order = runtime.engine.create_order("AMD", OrderSide.BUY, OrderType.MARKET, 10,
                                            estimated_price=100.0, strategy_id="swing_panic_dip",
                                            arm=TradingArm.SWING)
        runtime.engine.submit_order(order.id)
        order.broker_order_id = "alpaca_pending"
        order.broker_client_id = "adt_pending"
        staged.execution_order_id = order.id
        runtime.reserve_symbol_for_swing("AMD")

        runtime._expire_stale_staged_swing_orders(datetime(2026, 9, 25, 13, 46, tzinfo=timezone.utc))
        assert not runtime.swing_staged_order_manager.is_staged_for_entry("AMD")
        assert "AMD" in runtime.swing_reserved_symbols
        order.broker_order_id = None
        order.broker_client_id = None
        runtime._release_resolved_swing_reservations()
        assert "AMD" not in runtime.swing_reserved_symbols
    finally:
        runtime.reset_runtime_state()


def test_close_scan_counts_expired_but_unsettled_swing_buys_as_slots(monkeypatch):
    account = PaperTradingAccount()
    engine = ExecutionEngine(account)
    swing = SwingStrategyEngine(account, engine, symbols=["MU", "LRCX", "KLAC"])
    for symbol in ("MU", "LRCX"):
        order = engine.create_order(symbol, OrderSide.BUY, OrderType.MARKET, 10,
                                    strategy_id="swing_panic_dip", arm=TradingArm.SWING)
        order.broker_order_id = f"pending_{symbol}"

    calls = []
    def qualify(**kwargs):
        calls.append(kwargs["symbol"])
        return SimpleNamespace(qualified=True, daily_atr_14=4.0, rsi_2=2.0, sma_200=90.0)
    monkeypatch.setattr("backend.app.strategies.swing_panic_dip.evaluate_swing_qualification", qualify)

    swing.evaluate_market_close(date(2026, 9, 25))
    assert calls == [] and swing.staged_manager.get_staged_entries() == []

    engine.orders[next(oid for oid, order in engine.orders.items() if order.symbol == "LRCX")].broker_order_id = None
    swing.evaluate_market_close(date(2026, 9, 25))
    assert len(swing.staged_manager.get_staged_entries()) == 1
    assert swing.staged_manager.get_staged_entries()[0].symbol == "LRCX"


def test_old_checkpoint_objects_restore_without_new_optional_fields():
    engine = ExecutionEngine(PaperTradingAccount())
    order = engine.create_order("MU", OrderSide.BUY, OrderType.MARKET, 10,
                                strategy_id="swing_panic_dip", arm=TradingArm.SWING)
    encoded = encode_runtime_value(order)
    encoded["fields"].pop("swing_entry_atr")
    assert decode_runtime_value(encoded).swing_entry_atr is None

    manager = SwingStagedOrderManager()
    staged = manager.stage_buy("MU", 25000.0, 4.0, datetime(2026, 9, 24).date(), "PANIC_DIP")
    old_staged = staged.to_dict()
    old_staged.pop("execution_order_id")
    assert StagedSwingOrder.from_dict(old_staged).execution_order_id is None

    bracket = DynamicBracketManager().create_bracket("legacy", "AAPL", "LONG", 10, 100.0, 99.0)
    old_bracket = bracket.model_dump()
    for new_field in ("target_1_r", "target_2_r", "min_target_1_r"):
        old_bracket.pop(new_field)
    restored = BracketOrder.model_validate(old_bracket)
    assert restored.target_1_r is None and restored.min_target_1_r is None
    assert restored.target_1_price == bracket.target_1_price
