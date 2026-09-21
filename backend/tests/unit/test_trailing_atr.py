"""The trailing stop's volatility estimate must not collapse on one quiet bar.

Observed live on 2026-09-21: an NVDA opening-range long entered at $223.9502 with its
stop at $222.7303 (0.55% of entry, a sane distance). The trailing ratchet used a single
one-minute bar's high-low as "ATR", so during quiet minutes the trail distance fell to a
few cents. Because the ratchet never loosens, the stop walked to $223.6655 (0.127% of
entry) within three minutes and the position was scratched for -$9.37 at 09:43:57, six
minutes after entry, with its 1.5R target at $225.78 left unreachable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app import main
from backend.app.models.events import BarEvent

TS = datetime(2026, 9, 21, 13, 40, tzinfo=timezone.utc)


def _bar(i: int, high: float, low: float, close: float) -> BarEvent:
    return BarEvent(
        symbol="NVDA", timestamp=TS + timedelta(minutes=i),
        open=low, high=high, low=low, close=close, volume=1000,
    )


@pytest.fixture(autouse=True)
def _clean():
    main.reset_runtime_state()
    yield
    main.reset_runtime_state()


def _seed(bars):
    hist = main.market_history.setdefault("NVDA", [])
    for b in bars:
        hist.append({"time": b.timestamp.isoformat(), "open": b.open, "high": b.high,
                     "low": b.low, "close": b.close, "volume": b.volume})


def test_one_quiet_bar_does_not_collapse_the_volatility_estimate():
    # Twelve bars with a ~$0.50 range, then one near-flat minute.
    wide = [_bar(i, 224.25, 223.75, 224.00) for i in range(12)]
    quiet = _bar(12, 224.01, 223.99, 224.00)
    _seed(wide + [quiet])

    atr = main._atr_estimate("NVDA", quiet)
    # The old code returned the quiet bar's own range, $0.02.
    assert atr > 0.30, f"one calm minute collapsed ATR to {atr}"
    # 1.5x ATR must still clear ordinary noise on a $224 stock.
    assert 1.5 * atr > 0.45


def test_atr_averages_true_range_over_the_window():
    bars = [_bar(i, 101.0, 99.0, 100.0) for i in range(15)]
    _seed(bars)
    # Every bar: high-low = 2.0, prev close 100 sits inside, so TR = 2.0 throughout.
    assert main._atr_estimate("NVDA", bars[-1]) == pytest.approx(2.0)


def test_true_range_counts_a_gap_between_bars():
    _seed([_bar(0, 100.5, 99.5, 100.0), _bar(1, 110.2, 110.0, 110.1)])
    # Second bar's own range is 0.2, but it gapped 10 points from the prior close.
    assert main._atr_estimate("NVDA", _bar(1, 110.2, 110.0, 110.1)) == pytest.approx(10.2)


def test_falls_back_to_bar_range_before_there_is_history():
    lone = _bar(0, 100.5, 99.5, 100.0)
    assert main._atr_estimate("NVDA", lone) == pytest.approx(1.0)


def test_active_bracket_keeps_the_structural_stop():
    """Before Target 1, the strategy's stop must survive untouched.

    This is the live NVDA geometry: entry $223.9502, structural stop $222.7303, and a
    peak of $224.13 that is only 18c above entry. The old code trailed from entry, so
    peak minus 1.5*ATR pinned the stop BELOW entry and inside the noise.
    """
    from backend.app.core.bracket import BracketStatus, DynamicBracketManager

    mgr = DynamicBracketManager()
    b = mgr.create_bracket(
        bracket_id="brk_t", symbol="NVDA", side="LONG", total_qty=55,
        entry_price=223.9502, stop_price=222.7303, strategy_id="orb", timestamp=TS,
    )
    b.status = BracketStatus.ACTIVE
    b.stop_order_id = "stop_1"

    directive = mgr.update_trailing_stop(
        symbol="NVDA", current_bar_high=224.13, current_bar_low=223.90,
        current_atr=0.34, timestamp=TS,
    )
    assert directive is None, "trail must not touch an ACTIVE bracket's structural stop"
    assert b.current_stop_price == pytest.approx(222.7303)
    stop_pct = (b.entry_price - b.current_stop_price) / b.entry_price
    assert stop_pct > 0.0035, "structural stop must stay outside ordinary noise"


def test_trail_engages_once_target_1_has_scaled_out():
    """After Target 1 the runner does trail, which is the documented design."""
    from backend.app.core.bracket import BracketStatus, DynamicBracketManager

    mgr = DynamicBracketManager()
    b = mgr.create_bracket(
        bracket_id="brk_t2", symbol="NVDA", side="LONG", total_qty=55,
        entry_price=223.9502, stop_price=222.7303, strategy_id="orb", timestamp=TS,
    )
    b.status = BracketStatus.TARGET_1_HIT
    b.stop_order_id = "stop_1"
    b.current_stop_price = 223.9702  # breakeven + buffer, set when Target 1 hit

    directive = mgr.update_trailing_stop(
        symbol="NVDA", current_bar_high=228.00, current_bar_low=227.00,
        current_atr=0.40, timestamp=TS,
    )
    assert directive is not None
    assert b.current_stop_price == pytest.approx(228.00 - 1.5 * 0.40)
    assert b.current_stop_price > b.entry_price, "runner's stop should be locked in profit"
