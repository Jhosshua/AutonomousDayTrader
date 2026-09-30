# @steered SNARE-2 2026-09-30
"""Overnight holds: the frozen buy rule and the calendar (plan T1 decision parity, T2 calendar).

T1 feeds every research session to overnight_schedule with only what is visible at 15:46:05
(bars starting before 15:45, the 09:30 bar) and no next-day data, and compares the
(stock, buy date, sale date) sets with the research trade files through a committed fixture
written by scripts/overnight_research_fixture.py.
"""
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path

import pytest

from backend.app.core import overnight_schedule as osch
from backend.app.core.trading_windows import ET, NYSE_EARLY_CLOSES, NYSE_HOLIDAYS

PROD = osch.TradingWindowsCalendar()


def _ymd(n: int) -> date:
    return date(n // 10000, n // 100 % 100, n % 100)


# ---------------------------------------------------------------- T2 calendar
def test_early_close_2026_11_27_has_no_buy_even_with_full_extended_hours_bars():
    # 2026-11-27 closes at 13:00. Raw SIP bars (extended hours included) would pass the count.
    day = date(2026, 11, 27)
    starts = [datetime.combine(day, time(4, 0), ET) + timedelta(minutes=i) for i in range(16 * 60)]
    count, has_open = osch.count_bars_before_cutoff(starts, day)
    assert (count, has_open) == (375, True)       # only 09:30 to 15:44 counted, once each
    assert osch.buy_gates(day, count, has_open, PROD) == (False, osch.EARLY_CLOSE)
    # the sale on the early close morning is normal: Wed 11-25 buy sells Fri 11-27 (Thanksgiving 11-26)
    assert osch.sale_date(date(2026, 11, 25), PROD) == day
    assert osch.buy_gates(date(2026, 11, 25), 375, True, PROD) == (True, "OK")


def test_early_close_with_raw_extended_hours_bars_2025_11_28_research_calendar():
    sessions = [date(2025, 11, 26), date(2025, 11, 28), date(2025, 12, 1)]
    cal = osch.SessionListCalendar(sessions, early_closes=[date(2025, 11, 28)])
    day = date(2025, 11, 28)
    starts = [datetime.combine(day, time(9, 30), ET) + timedelta(minutes=i) for i in range(7 * 60)]
    count, has_open = osch.count_bars_before_cutoff(starts, day)
    assert osch.data_rule_ok(count)
    assert osch.buy_gates(day, count, has_open, cal) == (False, osch.EARLY_CLOSE)
    assert osch.sale_date(date(2025, 11, 26), cal) == day


def test_friday_sells_monday_and_holiday_monday_sells_tuesday():
    assert osch.sale_date(date(2026, 10, 2), PROD) == date(2026, 10, 5)
    assert osch.holding_nights(date(2026, 10, 2), date(2026, 10, 5)) == "weekend"
    # MLK day Monday 2026-01-19
    assert osch.sale_date(date(2026, 1, 16), PROD) == date(2026, 1, 20)
    assert osch.holding_nights(date(2026, 1, 16), date(2026, 1, 20)) == "holiday"
    assert osch.buy_gates(date(2026, 1, 16), 375, True, PROD) == (True, "OK")
    assert osch.buy_gates(date(2026, 1, 19), 375, True, PROD) == (False, osch.NOT_TRADING_DAY)
    # Good Friday 2026-04-03: Thursday buy sells Monday
    assert osch.sale_date(date(2026, 4, 2), PROD) == date(2026, 4, 6)
    assert osch.holding_nights(date(2026, 9, 29), date(2026, 9, 30)) == "weeknight"


def test_unscheduled_closure_2025_01_09_is_skipped_by_the_session_list():
    cal = osch.SessionListCalendar([date(2025, 1, 8), date(2025, 1, 10), date(2025, 1, 13)])
    assert osch.sale_date(date(2025, 1, 8), cal) == date(2025, 1, 10)
    assert osch.buy_gates(date(2025, 1, 9), 375, True, cal) == (False, osch.NOT_TRADING_DAY)


def test_2028_is_refused_and_the_last_2027_session_cannot_buy():
    assert osch.buy_gates(date(2028, 1, 3), 375, True, PROD) == (False, osch.CALENDAR_NOT_COVERED)
    assert osch.buy_gates(date(2025, 9, 30), 375, True, PROD) == (False, osch.CALENDAR_NOT_COVERED)
    # 2027-12-31 is a Friday; its sale would be in 2028, which the calendar cannot vouch for
    assert osch.buy_gates(date(2027, 12, 31), 375, True, PROD) == (False, osch.CALENDAR_NOT_COVERED)
    assert osch.sale_date(date(2027, 12, 31), PROD) is None
    assert osch.sale_date(date(2027, 12, 30), PROD) == date(2027, 12, 31)


@pytest.mark.parametrize("day,utc_hour", [
    (date(2026, 3, 6), 20), (date(2026, 3, 9), 19),     # EST -> EDT on Sun 2026-03-08
    (date(2026, 10, 30), 19), (date(2026, 11, 2), 20),  # EDT -> EST on Sun 2026-11-01
])
def test_dst_weeks_keep_the_et_clock(day, utc_hour):
    at = osch.et(day, osch.BUY_WINDOW_START).astimezone(timezone.utc)
    assert (at.hour, at.minute, at.second) == (utc_hour, 46, 5)
    assert osch.et_time(at) == time(15, 46, 5) and osch.et_date(at) == day
    # a Friday buy before the fall change sells Monday at 09:30 EST (14:30 UTC)
    sale = osch.sale_date(date(2026, 10, 30), PROD)
    assert sale == date(2026, 11, 2)
    assert osch.et(sale, osch.SALE_POLL_FROM).astimezone(timezone.utc).hour == 14


def test_alpaca_calendar_disagreement_is_refused():
    day = date(2026, 9, 30)
    good = [{"date": "2026-09-30", "open": "09:30", "close": "16:00"}]
    assert osch.buy_gates(day, 375, True, PROD, good, check_alpaca=True) == (True, "OK")
    assert osch.buy_gates(day, 375, True, PROD, [], check_alpaca=True) == (False, osch.CALENDAR_DISAGREES)
    early = [{"date": "2026-09-30", "open": "09:30", "close": "13:00"}]
    assert osch.buy_gates(day, 375, True, PROD, early, check_alpaca=True) == (False, osch.EARLY_CLOSE)
    odd = [{"date": "2026-09-30", "open": "09:30", "close": "15:00"}]
    assert osch.buy_gates(day, 375, True, PROD, odd, check_alpaca=True) == (False, osch.CALENDAR_DISAGREES)
    assert osch.buy_gates(day, 375, True, PROD, None, check_alpaca=True) == (False, osch.ALPACA_CALENDAR_UNAVAILABLE)


def test_data_rule_boundary_and_0930_bar():
    day = date(2026, 9, 30)
    assert osch.buy_gates(day, 297, True, PROD) == (True, "OK")        # 297 + 15 = 312
    assert osch.buy_gates(day, 296, True, PROD) == (False, osch.DATA_SHORT)
    assert osch.buy_gates(day, 375, False, PROD) == (False, osch.NO_0930_BAR)
    # a bar starting at 15:45 is not visible at 15:46:05 and is never counted
    starts = [datetime.combine(day, time(15, 45), ET), datetime.combine(day, time(9, 29), ET),
              datetime.combine(day, time(9, 30, 0), ET), datetime.combine(day, time(9, 30, 0), ET)]
    assert osch.count_bars_before_cutoff(starts, day) == (1, True)


def test_sizing_and_client_ids():
    assert osch.shares_for(49_700.0, 180.0) == 55                      # floor(9940 / 180)
    assert osch.shares_for(200_000.0, 100.0) == 250                    # $25,000 cap binds
    assert osch.shares_for(0.0, 100.0) == 0 and osch.shares_for(50_000.0, 0.0) == 0
    assert osch.client_id("nvda", date(2026, 10, 2), "buy", 1) == "adt-ovn-NVDA-20261002-buy-1"
    assert osch.client_id("HUT", date(2026, 10, 2), "sell", 3) == "adt-ovn-HUT-20261002-sell-3"
    with pytest.raises(ValueError):
        osch.client_id("HUT", date(2026, 10, 2), "sell", 0)


def test_2026_holidays_and_early_closes_are_covered_and_distinct():
    assert all(d.year in PROD.COVERED_YEARS for d in NYSE_HOLIDAYS | NYSE_EARLY_CLOSES)
    assert not NYSE_HOLIDAYS & NYSE_EARLY_CLOSES
