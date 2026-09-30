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


# ---------------------------------------------------------------- T1 decision parity
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "overnight_parity.json"
X1_IREN = {date(2023, 10, 6), date(2023, 10, 17), date(2023, 11, 7)}
LIVE_NIGHTS = {"NVDA": 742, "IREN": 739, "HUT": 742}


@pytest.fixture(scope="module")
def parity():
    raw = json.loads(FIXTURE.read_text())
    cols = raw["columns"]
    stocks = {sym: [dict(zip(cols, row)) for row in v["rows"]] for sym, v in raw["stocks"].items()}
    cal = osch.SessionListCalendar([_ymd(d) for d in raw["calendar"]], [_ymd(d) for d in raw["early_closes"]])
    return raw, stocks, cal


def _decisions(rows, sym, cal):
    """Only the 15:46:05 inputs reach the schedule: no research_ok, no next-day data."""
    out = set()
    for r in rows:
        buy = _ymd(r["date"])
        ok, _ = osch.buy_gates(buy, r["count_before_1545"], r["has_0930_bar"], cal)
        if ok:
            out.add((sym, buy, osch.sale_date(buy, cal)))
    return out


def _research(rows, sym):
    return {(sym, _ymd(r["date"]), _ymd(r["next"])) for r in rows if r["outcome"] == "TRADE"}


def test_fixture_is_the_committed_research_run(parity):
    raw, stocks, _ = parity
    assert raw["config"] == {"cond": "none", "exit": "open"}
    assert raw["window"]["first"] == 20231002 and raw["window"]["last"] == 20260925
    assert {s: v["trades"] for s, v in raw["stocks"].items()} == {"NVDA": 742, "IREN": 736, "HUT": 742}
    assert set(raw["sha256"]) == {f"data/trades_{s}_overnight_0.npy" for s in osch.SYMBOLS} | {
        "data/holdout.json"} | {f"data/bars/{s}.npz" for s in ("NVDA", "IREN", "HUT", "SPY")}
    assert all(len(h) == 64 for h in raw["sha256"].values()) and len(raw["generator_commit"]) == 40
    for sym, rows in stocks.items():
        assert len(rows) == 749
        trades = [r for r in rows if r["outcome"] == "TRADE"]
        assert len(trades) == raw["stocks"][sym]["trades"]
        # full precision kept: every trade carries floats, never rounded strings
        assert all(isinstance(r[k], float) for r in trades for k in ("entry", "exit", "ret"))


@pytest.mark.parametrize("sym", osch.SYMBOLS)
def test_t1_live_decisions_equal_research_apart_from_x1(parity, sym):
    _, stocks, cal = parity
    live = _decisions(stocks[sym], sym, cal)
    research = _research(stocks[sym], sym)
    assert research <= live
    extra = {buy for _, buy, _ in live - research}
    assert extra == (X1_IREN if sym == "IREN" else set())
    assert len(live) == LIVE_NIGHTS[sym]
    # the X1 nights are exactly the research's look-ahead skips (sale day short of data)
    x1 = {_ymd(r["date"]) for r in stocks[sym] if r["outcome"] == "NEXT_NOT_OK"}
    assert x1 == extra
    assert not any(r["outcome"] in ("NO_CLOSE_BAR", "NEXT_NO_0930_BAR") for r in stocks[sym])  # F2c, X10: 0


@pytest.mark.parametrize("sym", osch.SYMBOLS)
def test_x2_rule_agrees_with_research_ok_on_every_full_session(parity, sym):
    _, stocks, cal = parity
    full = [r for r in stocks[sym] if not r["early_close"]]
    early = [r for r in stocks[sym] if r["early_close"]]
    assert len(full) == 742 and len(early) == 7
    assert [osch.data_rule_ok(r["count_before_1545"]) for r in full] == [r["research_ok"] for r in full]
    # early closes: research judges 80% of 210 minutes and never trades them; live refuses first
    for r in early:
        assert osch.buy_gates(_ymd(r["date"]), r["count_before_1545"], r["has_0930_bar"], cal) == (False, osch.EARLY_CLOSE)
        assert r["outcome"] == "EARLY_CLOSE"


@pytest.mark.parametrize("sym", osch.SYMBOLS)
def test_t1_production_calendar_gives_identical_2026_decisions(parity, sym):
    _, stocks, cal = parity
    rows = [r for r in stocks[sym] if r["date"] // 10000 == 2026]
    assert len(rows) > 180
    assert _decisions(rows, sym, PROD) == _decisions(rows, sym, cal)


def test_2026_nyse_holidays_equal_the_fixture_calendar_gaps(parity):
    raw, _, _ = parity
    sessions = {_ymd(d) for d in raw["calendar"]}
    last = max(sessions)
    weekdays = {date(2026, 1, 1) + timedelta(days=i) for i in range((last - date(2026, 1, 1)).days + 1)}
    gaps = {d for d in weekdays if d.weekday() < 5 and d not in sessions}
    assert gaps == {d for d in NYSE_HOLIDAYS if d.year == 2026 and d <= last}
    assert not {d for d in NYSE_EARLY_CLOSES if d <= last} and not {
        d for d in map(_ymd, raw["early_closes"]) if d.year == 2026}
