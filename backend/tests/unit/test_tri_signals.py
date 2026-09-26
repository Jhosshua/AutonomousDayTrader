"""Independent rule and boundary review of the frozen TSLA/CDE signal states.

All bars are synthetic and all clocks are explicit. These tests never construct
a broker, access the network, or submit an account order.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.core.trading_windows import ET
from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies import tri_engine
from backend.app.strategies.tri_engine import AsymmetricDualStrategy


START = datetime(2026, 9, 28, 9, 30, tzinfo=ET)
MINUTE = timedelta(minutes=1)


def pair(symbol, minute, *, start=START, o=100, h=101, l=99, c=100,
         q=200, qh=201, ql=199, qv=1000):
    at = start + minute * MINUTE
    return (BarEvent(symbol, o, h, l, c, 1000, at),
            BarEvent("QQQ", q, qh, ql, q, qv, at))


def feed(strategy, values, *, reverse=False, delay=0):
    result = []
    for bar in values[::-1] if reverse else values:
        result.extend(strategy.on_completed_bar(
            bar, bar.timestamp + MINUTE + timedelta(seconds=delay)))
    return result


def prepare(strategy, *, until=15, reverse=False, start=START):
    for minute in range(until):
        assert not feed(strategy, pair(strategy.symbol, minute, start=start), reverse=reverse)


def breakout(strategy, minute=15, *, reverse=False, **kwargs):
    return feed(strategy, pair(strategy.symbol, minute, o=100, h=102.5,
                               l=100, c=102, **kwargs), reverse=reverse)


def retest(strategy, minute=16, *, reverse=False, **kwargs):
    params = dict(o=101.1, h=102, l=101, c=101.5)
    params.update(kwargs)
    return feed(strategy, pair(strategy.symbol, minute, **params), reverse=reverse)


@pytest.mark.parametrize("symbol", ["TSLA", "CDE"])
@pytest.mark.parametrize("reverse", [False, True])
def test_long_uses_prior_breakout_and_emits_only_after_matching_completed_pair(symbol, reverse):
    s = AsymmetricDualStrategy(symbol)
    prepare(s, reverse=reverse)
    assert (s.or_low, s.or_mid, s.or_high) == (99, 100, 101)
    # This green breakout also meets every numerical retest predicate. It must
    # still wait for a later candle instead of using its own breakout.
    assert not breakout(s, reverse=reverse)
    values = pair(symbol, 16, o=101.1, h=102, l=101, c=101.5)
    first, second = values[::-1] if reverse else values
    assert not s.on_completed_bar(first, first.timestamp + MINUTE)
    [signal] = s.on_completed_bar(second, second.timestamp + MINUTE)
    assert signal.side == OrderSide.BUY
    assert signal.order_type == OrderType.MARKET  # market at T+2 (operator decision 2026-09-25)
    assert signal.stop_loss == 99
    assert signal.timestamp == START + 16 * MINUTE
    assert s.decision_at == START + 17 * MINUTE
    assert s.entry_due == START + 18 * MINUTE
    assert s.signal_consumed and s.side == "LONG" and s.stop == 99
    assert not retest(s, 17)


@pytest.mark.parametrize("symbol", ["TSLA", "CDE"])
def test_breakout_requires_strict_close_above_range_high(symbol):
    s = AsymmetricDualStrategy(symbol)
    prepare(s)
    assert not feed(s, pair(symbol, 15, o=100, h=102, l=100, c=101))
    assert s.breakout_at is None
    assert not retest(s)
    assert s.breakout_at == START + 16 * MINUTE
    assert not s.signal_consumed


@pytest.mark.parametrize("changes,qualifies", [
    ({"l": 100}, True),                # Retest can touch the midpoint.
    ({"l": 99.999999}, False),          # It cannot cross below it.
    ({"l": 101.4, "o": 101.5, "c": 101.8}, True),
    ({"l": 101.400001, "o": 101.5, "c": 101.8}, False),
    ({"o": 101.5, "c": 101.5}, False), # A doji is not a green candle.
    ({"o": 101.6, "c": 101.5}, False),
    ({"o": 100.5, "l": 100, "c": 101}, True),
    ({"o": 100.5, "l": 100, "c": 100.999999}, False),
    ({"q": 199, "qh": 200, "ql": 198}, False),
])
@pytest.mark.parametrize("symbol", ["TSLA", "CDE"])
def test_each_long_rule_boundary_is_enforced(symbol, changes, qualifies, monkeypatch):
    s = AsymmetricDualStrategy(symbol)
    prepare(s)
    breakout(s)
    # Isolate the literal <= OR high + 0.20 * ATR boundary from ATR changes
    # caused by changing the candidate candle itself.
    monkeypatch.setattr(tri_engine, "frozen_atr", lambda _: 2.0)
    assert bool(retest(s, **changes)) is qualifies


@pytest.mark.parametrize("symbol,cutoff", [("TSLA", 90), ("CDE", 120)])
@pytest.mark.parametrize("offset,qualifies", [(-1, True), (0, False), (1, False)])
def test_short_cutoff_is_strict_and_uses_the_signal_candle_timestamp(symbol, cutoff, offset, qualifies):
    minute = cutoff + offset
    s = AsymmetricDualStrategy(symbol)
    prepare(s, until=minute)
    result = feed(s, pair(symbol, minute, o=99.5, h=100, l=98, c=98.5,
                          q=199, qh=200, ql=198))
    assert bool(result) is qualifies
    if qualifies:
        assert result[0].side == OrderSide.SELL
        assert result[0].order_type == OrderType.MARKET
        assert result[0].stop_loss == 100
        assert s.entry_due == START + (minute + 2) * MINUTE
        assert s.side == "SHORT" and s.stop == 100


@pytest.mark.parametrize("close,qqq_close,qualifies", [
    (98.999999, 199, True),
    (99, 199, False),
    (98.5, 200, False),
    (98.5, 201, False),
])
def test_short_breakdown_and_context_are_both_strict(close, qqq_close, qualifies):
    s = AsymmetricDualStrategy("TSLA")
    prepare(s)
    result = feed(s, pair("TSLA", 15, o=99.5, h=100, l=98, c=close,
                          q=qqq_close, qh=qqq_close + 1, ql=qqq_close - 1))
    assert bool(result) is qualifies


@pytest.mark.parametrize("symbol", ["TSLA", "CDE"])
def test_failed_context_does_not_consume_setup_and_short_can_follow_prior_breakout(symbol):
    s = AsymmetricDualStrategy(symbol)
    prepare(s)
    breakout(s)
    assert not retest(s, q=199, qh=200, ql=198)
    assert not s.signal_consumed
    [signal] = feed(s, pair(symbol, 17, o=99.5, h=100, l=98, c=98.5,
                            q=198, qh=199, ql=197))
    assert signal.side == OrderSide.SELL
    assert s.signal_consumed


@pytest.mark.parametrize("symbol,last", [("TSLA", 120), ("CDE", 147)])
@pytest.mark.parametrize("offset,qualifies", [(0, True), (1, False)])
def test_last_long_signal_bar_is_inclusive_and_cde_t_plus_two_stays_before_noon(symbol, last, offset, qualifies):
    s = AsymmetricDualStrategy(symbol)
    minute = last + offset
    prepare(s, until=minute - 1)
    breakout(s, minute - 1)
    result = retest(s, minute)
    assert bool(result) is qualifies
    if qualifies:
        assert s.entry_due == START + (minute + 2) * MINUTE
        assert s.entry_due < START.replace(hour=12, minute=0)


def test_qqq_gate_uses_close_volume_weighted_vwap_from_session_open():
    # Same formula as the audited research script: cumsum(close*volume)/cumsum(volume).
    s = AsymmetricDualStrategy("TSLA")
    for minute in range(15):
        assert not feed(s, pair("TSLA", minute, q=202, qh=210, ql=190, qv=4000))
    breakout(s, q=202, qh=210, ql=190, qv=4000)
    assert not retest(s, q=201, qh=201, ql=200, qv=1000)
    expected = (16 * 4000 * 202 + 1000 * 201) / 65000
    assert s.qqq_vwap == pytest.approx(expected)
    assert s.qqq_close < s.qqq_vwap
    # Typical-price VWAP would be about 200.7 here and would wrongly approve this long.
    assert 201 > (16 * 4000 * ((210 + 190 + 202) / 3) + 1000 * ((201 + 200 + 201) / 3)) / 65000


def test_frozen_atr_keeps_the_referenced_high_to_previous_close_formula():
    values = [pair("TSLA", 0)[0],
              pair("TSLA", 1, o=90, h=91, l=89, c=90)[0],
              pair("TSLA", 2, o=91, h=92, l=90, c=91)[0]]
    assert tri_engine.frozen_atr(values) == pytest.approx((2 + 9 + 2) / 3)
    assert tri_engine.frozen_atr(values[:2]) == pytest.approx(.45)


@pytest.mark.parametrize("delay,valid", [(-.5, True), (-.500001, False), (10, True), (60, True)])
def test_bar_completion_clock_skew_boundary_and_late_bars_still_count(delay, valid):
    # A bar used before its minute closes is lookahead. A late bar is still real
    # data; lateness is enforced where it matters, at the T+2 entry clock.
    s = AsymmetricDualStrategy("TSLA")
    feed(s, pair("TSLA", 0), delay=delay)
    assert (s.phase != "SKIPPED") is valid
    if not valid:
        assert s.reason == "INCOMPLETE_BAR"
    else:
        assert len(s.bars["TSLA"]) == 1


@pytest.mark.parametrize("fault,reason", [
    ("gap", "MISSING_OPENING_RANGE_BAR"),
    ("naive", "NAIVE_BAR_TIMESTAMP"),
    ("seconds", "INCOMPLETE_BAR"),
    ("wrong_day", "WRONG_SESSION_BAR"),
    ("nan", "INVALID_OHLCV"),
    ("negative_volume", "INVALID_OHLCV"),
    ("bad_high", "INVALID_OHLCV"),
    ("bad_low", "INVALID_OHLCV"),
])
def test_bad_required_data_latches_the_session_closed(fault, reason):
    s = AsymmetricDualStrategy("TSLA")
    feed(s, pair("TSLA", 0))
    bar = pair("TSLA", 1)[0]
    at = bar.timestamp + MINUTE
    if fault == "gap":
        bar = pair("TSLA", 2)[0]
        at = bar.timestamp + MINUTE
    elif fault == "naive":
        bar = replace(bar, timestamp=bar.timestamp.replace(tzinfo=None))
    elif fault == "seconds":
        bar = replace(bar, timestamp=bar.timestamp + timedelta(seconds=1))
        at = bar.timestamp + MINUTE
    elif fault == "wrong_day":
        bar = replace(bar, timestamp=bar.timestamp - timedelta(days=3))
    elif fault == "nan":
        bar = replace(bar, close=float("nan"))
    elif fault == "negative_volume":
        bar = replace(bar, volume=-1)
    elif fault == "bad_high":
        bar = replace(bar, high=99)
    elif fault == "bad_low":
        bar = replace(bar, low=101)
    assert not s.on_completed_bar(bar, at)
    assert s.phase == "SKIPPED" and s.reason == reason
    assert not feed(s, pair("TSLA", 1))
    assert s.phase == "SKIPPED" and s.reason == reason


def test_repeated_bar_is_ignored_not_fatal():
    s = AsymmetricDualStrategy("TSLA")
    feed(s, pair("TSLA", 0))
    feed(s, pair("TSLA", 0))
    assert s.phase == "BUILDING_RANGE" and len(s.bars["TSLA"]) == 1 and len(s.bars["QQQ"]) == 1


@pytest.mark.parametrize("symbol", ["TSLA", "CDE"])
def test_quiet_minute_after_the_range_is_skipped_like_the_research(symbol):
    # 31 of 314 CDE sessions (2024-06..2025-09) miss a minute before noon. The
    # research loop iterates existing rows, so a gap only removes that minute.
    s = AsymmetricDualStrategy(symbol)
    prepare(s)
    breakout(s)
    qqq_only = pair(symbol, 16)[1]
    assert not s.on_completed_bar(qqq_only, qqq_only.timestamp + MINUTE)
    [signal] = retest(s, 17)
    assert signal.timestamp == START + 17 * MINUTE and s.phase == "WAITING_ENTRY"


def test_missing_context_minute_fails_only_that_minutes_qqq_check():
    s = AsymmetricDualStrategy("TSLA")
    prepare(s)
    breakout(s)
    stock, _ = pair("TSLA", 16, o=101.1, h=102, l=101, c=101.5)
    assert not s.on_completed_bar(stock, stock.timestamp + MINUTE)  # waits for QQQ to reach 09:46
    later_qqq = pair("TSLA", 17)[1]
    assert not s.on_completed_bar(later_qqq, later_qqq.timestamp + MINUTE)
    evaluated = [e for e in s.audit if e["kind"] == "EVALUATE"][-1]
    assert evaluated["long_checks"]["qqq_above_vwap"] is False and s.phase == "WAITING_RETEST"
    [signal] = retest(s, 18)
    assert signal.timestamp == START + 18 * MINUTE


def test_zero_context_volume_prevents_a_signal():
    s = AsymmetricDualStrategy("CDE")
    for minute in range(15):
        feed(s, pair("CDE", minute, qv=0))
    assert not breakout(s, qv=0)
    assert not retest(s, qv=0)
    assert s.qqq_vwap is None and not s.signal_consumed


def test_missing_opening_range_bar_is_detected_by_the_clock():
    s = AsymmetricDualStrategy("TSLA")
    prepare(s, until=14)
    deadline = START + 16 * MINUTE + timedelta(seconds=10)
    s.on_time_tick(deadline)
    assert s.phase == "BUILDING_RANGE"
    s.on_time_tick(deadline + timedelta(microseconds=1))
    assert s.phase == "SKIPPED" and s.reason == "MISSING_OPENING_RANGE_BAR"


def test_bar_problems_after_the_signal_never_close_the_trade():
    s = AsymmetricDualStrategy("CDE")
    prepare(s)
    breakout(s)
    retest(s)
    s.phase = "HOLDING"
    bad = replace(pair("CDE", 30)[0], close=float("nan"))
    assert not s.on_completed_bar(bad, bad.timestamp + MINUTE)
    assert s.phase == "HOLDING" and not s.incomplete and s.exit_reason is None


@pytest.mark.parametrize("phase", ["ENTERING", "HOLDING", "EXITING"])
def test_fault_and_session_rollover_preserve_active_trade_ownership(phase):
    s = AsymmetricDualStrategy("TSLA")
    prepare(s)
    breakout(s)
    retest(s)
    s.phase = phase
    s.tranches = [{"id": "T1", "remaining": 2}]
    s.risk_reserved = 10
    s.entry_order_id = "stable-order"
    s.skip("MISSING_BAR", START + 20 * MINUTE)
    assert s.phase == phase and s.incomplete and s.exit_reason == "MISSING_BAR"
    s.start_session((START + timedelta(days=1)).date())
    assert s.session_day == START.date()
    assert s.tranches == [{"id": "T1", "remaining": 2}]
    assert s.risk_reserved == 10 and s.entry_order_id == "stable-order"


def test_rejected_qualified_setup_stays_consumed_until_the_next_session():
    s = AsymmetricDualStrategy("CDE")
    prepare(s)
    breakout(s)
    retest(s)
    s.skip("INSUFFICIENT_BUYING_POWER", s.entry_due)
    assert s.signal_consumed
    assert not retest(s, 17)
    s.start_session((START + timedelta(days=1)).date())
    assert not s.signal_consumed and s.signal is None
    assert s.phase == "BUILDING_RANGE"


@pytest.mark.parametrize("symbol,last", [("TSLA", 120), ("CDE", 147)])
def test_no_signal_day_and_entry_deadline_respect_inclusive_grace(symbol, last):
    s = AsymmetricDualStrategy(symbol)
    prepare(s, until=last + 1)
    final = START + (last + 1) * MINUTE + timedelta(seconds=10)
    s.on_time_tick(final)
    assert s.phase == "WAITING_BREAKOUT"
    s.on_time_tick(final + timedelta(microseconds=1))
    assert s.phase == "NO_SIGNAL"
    s = AsymmetricDualStrategy(symbol)
    prepare(s)
    breakout(s)
    retest(s)
    s.on_time_tick(s.entry_due + timedelta(seconds=5))
    assert s.phase == "WAITING_ENTRY"
    s.on_time_tick(s.entry_due + timedelta(seconds=5, microseconds=1))
    assert s.phase == "SKIPPED" and s.reason == "MISSED_ENTRY_WINDOW"


def test_utc_bars_and_et_bars_pair_by_actual_instant():
    s = AsymmetricDualStrategy("TSLA")
    for minute in range(15):
        stock, qqq = pair("TSLA", minute)
        qqq = replace(qqq, timestamp=qqq.timestamp.astimezone(timezone.utc))
        feed(s, (stock, qqq))
    assert s.phase == "WAITING_BREAKOUT"
    assert s.or_high == 101 and s.paired_count == 15


@pytest.mark.parametrize("symbol,last", [("TSLA", 120), ("CDE", 147)])
def test_ui_keeps_the_final_eligible_signal_candle_open_until_it_completes(symbol, last):
    s = AsymmetricDualStrategy(symbol)
    prepare(s, until=last)
    # The last signal candle is still forming. The UI must not imply that
    # this arm's signal window has already ended while it can still qualify.
    at = START + last * MINUTE + timedelta(seconds=30)
    window = s.window(at, [])
    assert window["in_hours"]
    assert window["state"] == "CAN_TRADE"


@pytest.mark.parametrize("symbol", ["TSLA", "CDE"])
def test_ui_state_and_paper_contract_for_blocked_active_and_early_close_states(symbol):
    s = AsymmetricDualStrategy(symbol)
    prepare(s)
    now = START.replace(hour=10)
    assert s.window(now, ["Account loss halt."])["state"] == "BLOCKED"
    s.phase = "HOLDING"
    assert s.window(now, ["Account loss halt."])["state"] == "MANAGING"
    s.session_equity = 100000
    s.quantity = 10
    s.risk_reserved = 750
    data = s.to_dict()["tri_engine"]
    assert data["risk_budget"] == 750 and data["risk_reserved"] == 750
    assert data["mode"] == "alpaca_paper" and data["quantity"] == 10
    early = s.window(datetime(2026, 11, 27, 10, tzinfo=ET), [])
    assert any("12:55 PM" in text for text in early["notes"])


def test_weekend_card_says_market_closed_not_failed_check():
    s = AsymmetricDualStrategy("TSLA")
    saturday = datetime(2026, 9, 26, 10, 0, tzinfo=ET)
    s.on_time_tick(saturday)
    w = s.window(saturday, [])
    assert w["state"] == "MARKET_CLOSED" and w["headline"] == "Market closed today."
    assert not w["blockers"]
