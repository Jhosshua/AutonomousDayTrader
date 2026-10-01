# @steered SNARE-2 2026-09-30
"""Pure rule and research parity tests for the live SPY and COIN day strategies."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path

import pytest

from backend.app.core import day_one_schedule as d1

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "day_one_research_parity.json").read_text())


def point(raw, target_day, target_minute):
    return d1.BtcPoint(
        date.fromisoformat(target_day),
        target_minute,
        datetime.fromisoformat(f"{raw['selected_day']}T00:00:00-04:00"),
        raw["close"],
    )


def test_spy_fixture_is_the_frozen_post_hoc_atlas_slice():
    spy = FIXTURE["spy"]
    assert spy["evidence"] == d1.EVIDENCE[d1.SPY_ID]
    assert (spy["year1_count"], spy["year2_count"], len(spy["rows"])) == (37, 35, 72)
    early = [r["date"] for r in spy["rows"] if r["close_time"] == "13:00"]
    assert early == ["2024-11-29", "2025-11-28"]
    for row in spy["rows"]:
        assert math.isclose(row["gross"] - row["model_cost"], row["net"], abs_tol=1e-9)


def test_turn_month_eligibility_includes_first_two_last_and_early_close():
    sessions = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30),
                date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 30),
                date(2026, 11, 2)]
    got = {x for x in sessions if d1.turn_month_eligible(x, sessions)}
    assert got == {date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30),
                   date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 30), date(2026, 11, 2)}
    submit, fallback = d1.close_deadlines(date(2026, 11, 27), d1.EARLY_CLOSE)
    assert submit.timetz().replace(tzinfo=None) == dtime(12, 50)
    assert fallback.timetz().replace(tzinfo=None) == dtime(12, 59, 30)


def test_calendar_parser_rejects_duplicates_and_unknown_closes():
    assert d1.parse_calendar([{"date": "2026-10-01", "close": "16:00"}]) == {date(2026, 10, 1): dtime(16)}
    with pytest.raises(ValueError):
        d1.parse_calendar([{"date": "2026-10-01", "close": "16:00"}, {"date": "2026-10-01", "close": "16:00"}])
    with pytest.raises(ValueError):
        d1.parse_calendar([{"date": "2026-10-01", "close": "14:00"}])


def test_btc_selector_uses_rightmost_recent_close_and_rejects_bad_values():
    rows = [
        {"t": "2026-10-01T13:31:00Z", "c": 100.0},
        {"t": "2026-10-01T13:34:00Z", "c": 101.0},
        {"t": "2026-10-01T13:34:00Z", "c": 102.0},
    ]
    p = d1.select_btc_close(rows, date(2026, 10, 1), 574)
    assert p is not None and p.close == 102.0 and p.selected_at.minute == 34
    assert d1.select_btc_close(rows[:1], date(2026, 10, 1), 574) is not None
    assert d1.select_btc_close([{"t": "2026-10-01T13:29:00Z", "c": 99}], date(2026, 10, 1), 574) is None
    with pytest.raises(ValueError):
        d1.select_btc_close([{"t": "2026-10-01T13:34:00Z", "c": float("nan")}], date(2026, 10, 1), 574)


def test_coin_fixture_matches_exact_normalized_move_sigma_and_signal():
    coin = FIXTURE["coin"]
    assert coin["config_index"] == 11
    assert coin["params"] == {"mode": "follow", "at": 935, "k": 1.0, "exit": "close"}
    assert coin["evidence"] == d1.EVIDENCE[d1.COIN_ID]
    history = list(coin["initial_history"])
    raw_signals = trades = ssr = 0
    for row in coin["rows"]:
        if row["anchor"] and row["decision"]:
            anchor = point(row["anchor"], row["prior_session"], row["anchor"]["target_minute"])
            decision = point(row["decision"], row["date"], row["decision"]["target_minute"])
            move = d1.normalized_btc_move(anchor, decision)
            assert math.isclose(move, row["zmove"], abs_tol=2e-12)
            decided = d1.coin_decision(history, move)
            if row["sigma"] is None:
                assert math.isnan(decided.sigma)
            else:
                assert math.isclose(decided.sigma, row["sigma"], abs_tol=2e-12)
            assert decided.history_count == row["history_count"]
            assert decided.side == row["threshold_side"]
            history.append(move)
        raw_signals += row["threshold_side"] != 0
        ssr += row["ssr_blocked"]
        trades += row["trade_side"] != 0
        assert row["trade_side"] == (0 if row["ssr_blocked"] else row["threshold_side"])
    assert (raw_signals, ssr, trades) == (146, 1, 145)


def test_elapsed_hours_use_eastern_civil_time_across_dst():
    # Friday 15:59 to Monday 09:34 is 65h35 by the frozen civil formula on both DST weekends.
    for a, b in ((date(2025, 3, 7), date(2025, 3, 10)), (date(2025, 10, 31), date(2025, 11, 3))):
        anchor = d1.BtcPoint(a, 959, datetime.combine(a, dtime(15, 59), timezone.utc), 100.0)
        decision = d1.BtcPoint(b, 574, datetime.combine(b, dtime(9, 34), timezone.utc), 101.0)
        expected = 0.01 / math.sqrt(65 + 35 / 60)
        assert math.isclose(d1.normalized_btc_move(anchor, decision), expected)


def test_sigma_uses_last_twenty_finite_prior_values_and_excludes_today():
    history = [float("nan"), 999.0] + [i / 1000 for i in range(1, 22)]
    sigma, n = d1.trailing_sigma(history)
    expected = [i / 1000 for i in range(2, 22)]
    mean = sum(expected) / 20
    expected_sigma = math.sqrt(sum((x - mean) ** 2 for x in expected) / 19)
    assert n == 20 and math.isclose(sigma, expected_sigma)
    assert d1.coin_decision(history, 0.0).side == 0


def test_rule_201_is_exact_and_fails_closed_on_bad_input():
    assert d1.ssr_blocks_short(89.0, 100.0, 100.0, 100.0)
    assert d1.ssr_blocks_short(100.0, 100.0, 90.0, 100.0)
    assert not d1.ssr_blocks_short(90.01, 100.0, 90.01, 100.0)
    assert d1.ssr_blocks_short(float("nan"), 100.0, 100.0, 100.0)


def test_sizing_and_client_identity_are_bounded():
    assert d1.target_shares(50_000, 100_000, 500, d1.SPY_PCT) == 20
    assert d1.target_shares(500_000, 100_000, 100, d1.COIN_PCT) == 250
    assert d1.target_shares(50_000, 2_000, 100, d1.COIN_PCT) == 20
    assert d1.client_id(d1.SPY_ID, date(2026, 10, 1), "entry") == "adt-tom-spy-20261001-entry-1"


def test_lifecycle_validation_rejects_wrong_identity_and_multiple_live_attempts():
    base = {"phase": "HELD", "symbol": "SPY", "strategy_id": d1.SPY_ID,
            "target_qty": 10, "entry_qty": 10, "exit_qty": 0, "attempts": []}
    d1.validate_lifecycle(base)
    with pytest.raises(ValueError):
        d1.validate_lifecycle({**base, "strategy_id": d1.COIN_ID})
    attempts = [{"role": "entry", "client_id": "adt-a", "status": "accepted"},
                {"role": "entry", "client_id": "adt-b", "status": "ambiguous"}]
    with pytest.raises(ValueError):
        d1.validate_lifecycle({**base, "attempts": attempts})
