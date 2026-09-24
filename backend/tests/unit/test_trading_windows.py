"""Operator card windows. Expected hours are written out independently of the gate code."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.app.core.decisions import DecisionLog
from backend.app.core.trading_windows import strategy_window
from backend.app.strategies.adaptation import DynamicAdaptationEngine

ET = ZoneInfo("America/New_York")
GATE = DynamicAdaptationEngine().is_strategy_permitted

# Documented schedule (README / plan): new entries allowed [start, end).
EXPECTED = {
    "orb": [(time(9, 30), time(11, 30))],
    "vwap_pullback": [(time(9, 30), time(11, 30)), (time(14, 0), time(15, 45))],
    "mean_reversion": [(time(10, 0), time(15, 45))],
    "news_momentum": [(time(9, 30), time(15, 45))],
}


def _at(h, m, day=24):
    return datetime(2026, 9, day, h, m, tzinfo=ET)  # 2026-09-24 is a Thursday


@pytest.mark.parametrize("sid", list(EXPECTED))
def test_every_minute_matches_documented_hours(sid):
    for minute in range(8 * 60, 17 * 60):
        now = _at(minute // 60, minute % 60)
        want = any(a <= now.time() < b for a, b in EXPECTED[sid])
        w = strategy_window(sid, now, GATE, market_trend="BULLISH")
        assert w["in_hours"] is want, (sid, now.time())
        assert w["can_open_now"] is want, (sid, now.time())


def test_labels_and_next_change():
    w = strategy_window("vwap_pullback", _at(12, 15), GATE, market_trend="BULLISH")
    assert w["state"] == "WAITING" and w["headline"] == "Opens 2:00 PM"
    assert w["hours"] == "9:30 AM - 11:30 AM, 2:00 PM - 3:45 PM"
    assert w["next_change_at"] == "2026-09-24T14:00:00-04:00"
    w = strategy_window("orb", _at(12, 0), GATE, market_trend="BULLISH")
    assert w["state"] == "DONE_FOR_DAY"
    assert "Next: tomorrow 9:30 AM" in w["schedule_text"]
    w = strategy_window("orb", _at(10, 0), GATE, market_trend="BULLISH")
    assert w["state"] == "CAN_TRADE" and w["next_change_at"] == "2026-09-24T11:30:00-04:00"


def test_weekend_holiday_and_friday_rollover():
    sat = strategy_window("news_momentum", _at(10, 30, day=26), GATE, market_trend="BULLISH")
    assert sat["state"] == "MARKET_CLOSED" and not sat["can_open_now"]
    assert sat["next_change_at"] == "2026-09-28T09:30:00-04:00"
    fri = strategy_window("orb", _at(16, 30, day=25), GATE)
    assert fri["next_change_at"] == "2026-09-28T09:30:00-04:00"
    thanksgiving = strategy_window("orb", datetime(2026, 11, 26, 10, 0, tzinfo=ET), GATE)
    assert thanksgiving["state"] == "MARKET_CLOSED"
    half = strategy_window("orb", datetime(2026, 11, 27, 10, 0, tzinfo=ET), GATE, market_trend="BULLISH")
    assert any("Early market close" in n for n in half["notes"])


def test_utc_input_and_dst():
    # 14:00 UTC on a summer day is 10:00 ET; in January it is 09:00 ET (pre-open).
    summer = strategy_window("mean_reversion", datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc), GATE, market_trend="NEUTRAL")
    winter = strategy_window("mean_reversion", datetime(2027, 1, 12, 14, 0, tzinfo=timezone.utc), GATE, market_trend="NEUTRAL")
    assert summer["can_open_now"] is True
    assert winter["in_hours"] is False


@pytest.mark.parametrize("kw,text", [
    ({"operator_status": "PAUSED"}, "Paused by operator."),
    ({"breaker_halted": True}, "Daily loss limit hit: no new trades today."),
    ({"entry_lockout": True}, "End-of-day close-out has started: no new trades."),
    ({"persistence_halted": True}, "Saving is failing: new trades blocked until fixed."),
    ({"positions_full": True}, "Maximum open positions reached."),
    ({"market_trend": "UNKNOWN"}, "Market direction unknown."),
])
def test_live_blockers_turn_card_off_inside_hours(kw, text):
    args = {"market_trend": "BULLISH", **kw}
    w = strategy_window("orb", _at(10, 15), GATE, **args)
    assert w["in_hours"] is True
    assert w["can_open_now"] is False
    assert text in w["blockers"]


def test_market_direction_text_follows_filter_policy():
    assert "blocked" in strategy_window("vwap_pullback", _at(10, 15), GATE, market_trend="NEUTRAL")["market_text"]
    assert strategy_window("vwap_pullback", _at(10, 15), GATE, market_trend="NEUTRAL")["can_open_now"] is False
    assert strategy_window("mean_reversion", _at(10, 15), GATE, market_trend="NEUTRAL")["can_open_now"] is True
    assert "buys only" in strategy_window("orb", _at(10, 15), GATE, market_trend="BULLISH")["market_text"]
    assert "shorts only" in strategy_window("orb", _at(10, 15), GATE, market_trend="BEARISH")["market_text"]


def test_decision_log_counts_and_state_roundtrip():
    log = DecisionLog()
    t = datetime(2026, 9, 24, 14, 5, tzinfo=timezone.utc)
    log.record("orb", "aapl", "BUY", 100.0, "MARKET_FILTER", "INDEX_FILTER_DENIED", t)
    log.record("orb", "MSFT", "BUY", 200.0, "MARKET_FILTER", "INDEX_FILTER_DENIED", t)
    log.record("orb", "NVDA", "BUY", 50.0, "SUBMITTED", "ok", t)
    s = log.summary("orb")
    assert (s["signals_today"], s["orders_today"], s["blocked_today"], s["top_block_reason"]) == (3, 1, 2, "MARKET_FILTER")
    assert log.recent(1)[0]["symbol"] == "NVDA" and log.recent(1)[0]["time"] == "2026-09-24T10:05:00-04:00"
    other = DecisionLog()
    other.load_state(log.to_state())
    assert other.summary("orb") == s
    finished = other.reset_for_session("2026-09-25")
    assert finished["orb"]["SUBMITTED"] == 1 and other.summary("orb")["signals_today"] == 0


def test_unknown_market_blocks_news_too_but_names_the_exception():
    w = strategy_window("news_momentum", _at(10, 15), GATE, market_trend="UNKNOWN")
    assert w["can_open_now"] is False and w["state"] == "BLOCKED"
    assert "Market direction unknown: only extreme news can trade." in w["blockers"]
