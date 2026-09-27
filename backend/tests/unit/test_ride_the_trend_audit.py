"""Release regressions for the independent Ride the Trend audit (2026-09-27)."""
import asyncio
from datetime import date, datetime, timedelta
import threading

import pytest

from backend.app import main as r
from backend.app.core.macro_calendar import MacroCalendar
from backend.app.core.tick_tape import NS, TickTape
from backend.app.core.volume_profile import ProfileBuildError, ProfileStore, VolumeProfile, build_profile
from backend.app.models.events import BarEvent, QuoteEvent, TradeEvent
from backend.app.strategies import vwap_pullback_v2 as v2
from backend.tests.unit.test_ride_the_trend_data_layers import ET, T0_NS, _bars_for_profile
from backend.tests.unit.test_ride_the_trend_pipeline import TrendScenario, _index_bar, _session


def test_latest_cross_blocks_even_with_ample_prior_coverage_and_multiple_crosses():
    tape = TickTape()
    for k in range(1810):
        tape.on_quote('AAPL', 100, 100.02, 900, 100, T0_NS + k * NS)
    end = T0_NS + 1810 * NS
    assert tape.spread_stats('AAPL', end) is not None
    assert tape.book_imbalance('AAPL', end) is not None
    tape.on_quote('AAPL', 100.05, 100.02, 900, 100, end - NS // 2)
    assert tape.spread_stats('AAPL', end) is None
    assert tape.book_imbalance('AAPL', end) is None
    # A later cross must not erase the earlier invalid interval from history.
    for k in range(1810, 1841):
        tape.on_quote('AAPL', 100, 100.02, 900, 100, T0_NS + k * NS)
    tape.on_quote('AAPL', 100.05, 100.02, 900, 100, T0_NS + 1840 * NS + NS // 2)
    assert tape.book_imbalance('AAPL', end) is None
    assert tape.spread_stats('AAPL', end) is None
    recovered = T0_NS + 1840 * NS
    assert tape.book_imbalance('AAPL', recovered) is not None
    assert tape.spread_stats('AAPL', recovered) is not None


def test_late_print_in_crossed_interval_stays_unknown_after_recovery():
    tape = TickTape()
    tape.on_quote('AAPL', 100, 100.02, 900, 100, T0_NS)
    tape.on_trade('AAPL', 100.02, 10, T0_NS + 1)
    tape.on_quote('AAPL', 100.05, 100.02, 900, 100, T0_NS + NS)
    tape.on_quote('AAPL', 100, 100.02, 900, 100, T0_NS + 3 * NS)
    tape.on_trade('AAPL', 100.03, 10, T0_NS + 3 * NS + 1)
    assert tape.on_trade('AAPL', 100.04, 10, T0_NS + 2 * NS) == 0


def test_session_delta_includes_opening_partial_minute_and_exact_reconnect_boundary():
    tape = TickTape()
    tape.on_quote('AAPL', 100, 100.02, 900, 100, T0_NS + 1)
    tape.on_trade('AAPL', 100.02, 10000, T0_NS + 2)
    for k in range(1, 32):
        tape.on_quote('AAPL', 100, 100.02, 900, 100, T0_NS + k * 60 * NS)
        tape.on_trade('AAPL', 100, 1, T0_NS + k * 60 * NS + 1)
    result = tape.session_delta('AAPL', T0_NS + 31 * 60 * NS)
    assert result['delta'] == 9970 and result['n_trades'] == 31
    assert not result['partial']
    reconnect = T0_NS + (32 * 60 + 20) * NS
    tape.note_disconnect(reconnect - NS)
    tape.note_reconnect(reconnect)
    tape.on_quote('AAPL', 100, 100.02, 900, 100, reconnect)
    tape.on_trade('AAPL', 100.02, 100, reconnect + 1)
    tape.on_trade('AAPL', 100, 10000, reconnect - NS // 2)  # delayed pre-gap print
    end = reconnect + 31 * 60 * NS
    tape.on_quote('AAPL', 100, 100.02, 900, 100, end - NS)
    tape.on_trade('AAPL', 100.02, 10, end - NS + 1)
    result = tape.session_delta('AAPL', (end // (60 * NS)) * 60 * NS)
    assert result['delta'] == 100 and result['partial']
    assert result['since_ns'] == reconnect
    assert tape.session_delta('AAPL', reconnect + 29 * 60 * NS) is None


def test_midday_start_is_labeled_partial():
    tape = TickTape()
    start = T0_NS + 3600 * NS
    for k in range(32):
        tape.on_quote('AAPL', 100, 100.02, 900, 100, start + k * 60 * NS)
        tape.on_trade('AAPL', 100.02, 10, start + k * 60 * NS + 1)
    assert tape.session_delta('AAPL', start + 31 * 60 * NS)['partial']


def test_discarded_print_ids_do_not_bypass_storage_cap():
    tape = TickTape(raw_cap=10)
    for k in range(1000):
        tape.on_trade('AAPL', 100, 1, T0_NS + k, trade_id=k + 1, conditions=['Z'])
    assert tape.ineligible == 1000
    assert sum(len(b.ids) for b in tape._syms['AAPL'].buckets.values()) == 0


def test_relative_strength_requires_spy_opening_bar():
    _session(r)
    try:
        scenario = TrendScenario(tick_gates=False)
        scenario.quiet(40)
        scenario.full_setup('LONG')
        r.vwap_strategy.symbol_states['AAPL'] = scenario.st
        for i in range(1, len(scenario.bars)):
            r.market_filter.on_bar(_index_bar('SPY', i, 500))
        ok, reason = r._ride_the_trend_rs(scenario.signals[0][1], {})
        assert not ok and "SPY's first session bar is not the 09:30 bar" in reason
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)


def test_profile_rejects_missing_recent_sessions_and_handles_short_session():
    stale = _bars_for_profile([14, 15, 16, 17, 18], lambda d, m: 100 + m / 100, lambda d, m: 1000)
    with pytest.raises(ProfileBuildError):
        build_profile('AAPL', stale, '2026-09-28')
    bars = []
    for day in (20, 23, 24, 25, 27):
        n = 210 if day == 27 else 390
        start = datetime(2026, 11, day, 9, 30, tzinfo=ET)
        bars.extend(BarEvent(symbol='AAPL', open=100, high=100.1, low=99.9, close=100,
                             volume=1000, timestamp=start + timedelta(minutes=k)) for k in range(n))
    prof = build_profile('AAPL', bars, '2026-11-30')
    assert prof.sessions == [f'2026-11-{d}' for d in (20, 23, 24, 25, 27)]
    assert prof.total_volume == (4 * 390 + 210) * 1000


def test_cancelled_profile_worker_cannot_overwrite_new_generation(monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    store = ProfileStore()
    monkeypatch.setattr(r, 'profile_store', store)
    monkeypatch.setattr(r, 'PROFILE_SYMBOLS', ['AAPL'])
    monkeypatch.setattr(r, 'profile_generation', 1)

    async def fetch(*args):
        return []

    def build(sym, bars, session, *args):
        if session == '2026-09-27':
            started.set()
            assert release.wait(5)
            finished.set()
        return VolumeProfile(sym, session, [], 0.1, 100, built_at=session)

    monkeypatch.setattr(r, '_fetch_relay_bars', fetch)
    monkeypatch.setattr(r, 'build_profile', build)

    async def run():
        old = asyncio.create_task(r.rebuild_volume_profiles(date(2026, 9, 27), 1))
        assert await asyncio.to_thread(started.wait, 5)
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
        r.profile_generation = 2
        await r.rebuild_volume_profiles(date(2026, 9, 28), 2)
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        assert store.profiles['AAPL'].for_session == '2026-09-28'
        assert store.last_build == '2026-09-28' and store.errors == {}

    try:
        asyncio.run(run())
    finally:
        release.set()


def test_stale_fetch_failure_cannot_delete_new_profile(monkeypatch):
    store = ProfileStore()
    monkeypatch.setattr(r, 'profile_store', store)
    monkeypatch.setattr(r, 'PROFILE_SYMBOLS', ['AAPL'])
    monkeypatch.setattr(r, 'profile_generation', 1)

    async def fetch(*args):
        r.profile_generation = 2
        store.profiles['AAPL'] = VolumeProfile('AAPL', '2026-09-28', [], 0.1, 100)
        raise RuntimeError('old request failed')

    monkeypatch.setattr(r, '_fetch_relay_bars', fetch)
    asyncio.run(r.rebuild_volume_profiles(date(2026, 9, 27), 1))
    assert store.profiles['AAPL'].for_session == '2026-09-28' and not store.errors


def test_verified_macro_dates_and_coverage():
    cal = MacroCalendar()
    assert not cal.check(datetime(2026, 11, 10, 8, 30, tzinfo=ET))[0]
    assert cal.check(datetime(2026, 11, 12, 8, 30, tzinfo=ET))[0]
    assert not cal.check(datetime(2026, 8, 1, 10, 0, tzinfo=ET))[0]
    assert not cal.check(datetime(2027, 1, 1, 10, 0, tzinfo=ET))[0]
    for month, day in ((10, 2), (11, 6), (12, 4)):
        assert not cal.check(datetime(2026, month, day, 8, 30, tzinfo=ET))[0]


def test_restore_failure_expires_at_new_session():
    strategy = v2.VWAPPullbackV2Strategy()
    strategy.emission_blocked_reason = 'restore_failed'
    assert not strategy.emission_allowed()
    strategy.reset_daily_stats()
    assert strategy.emission_allowed()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_real_tape_and_real_profile_pass_entire_entry_pipeline(monkeypatch, side):
    _session(r)
    monkeypatch.setattr(v2, 'TAPE', r.tick_tape)
    monkeypatch.setattr(v2, 'PROFILE', r.profile_store)
    try:
        scenario = TrendScenario(tick_gates=False)
        scenario.quiet(40)
        scenario.full_setup('LONG')
        extreme = scenario.signals[0][1].features['pullback_extreme']
        def mirror(bar, anchor):
            return BarEvent(symbol=bar.symbol, open=anchor-bar.open, high=anchor-bar.low,
                            low=anchor-bar.high, close=anchor-bar.close, volume=bar.volume, timestamp=bar.timestamp)
        bars = scenario.bars
        if side == 'SHORT':
            bars = [mirror(b, 200) for b in bars]
            extreme = 200 - extreme
        prior = _bars_for_profile([21, 22, 23, 24, 25],
            lambda d, m: extreme - 0.1 if m < 300 else extreme - 1 - (m % 90) / 100,
            lambda d, m: 10000 if m < 300 else 100)
        assert r.profile_store.build('AAPL', prior, '2026-09-28') is not None

        async def run():
            for i, bar in enumerate(bars):
                for second in range(60):
                    start = bar.low if side == 'LONG' else bar.high
                    price = start + (bar.close - start) * second / 59
                    ts = bar.timestamp + timedelta(seconds=second, microseconds=100000)
                    await r.handle_quote_event(QuoteEvent(symbol='AAPL', bid_price=price - 0.02 if side == 'LONG' else price,
                        ask_price=price if side == 'LONG' else price + 0.02, bid_size=900 if side == 'LONG' else 100,
                        ask_size=100 if side == 'LONG' else 900, bid_exchange='Q', ask_exchange='Q', timestamp=ts, timestamp_ns=int(ts.timestamp() * NS)))
                    await r.handle_trade_event(TradeEvent(symbol='AAPL', price=price, size=10,
                        exchange='Q', timestamp=ts, timestamp_ns=int(ts.timestamp() * NS) + 1, trade_id=i * 60 + second + 1))
                for sym, base in [('SPY', 500), ('QQQ', 400)]:
                    index = _index_bar(sym, i, base)
                    await r.handle_bar_event(index if side == 'LONG' else mirror(index, base * 2))
                await r.handle_bar_event(bar)
        asyncio.run(run())
        decisions = r.decision_log.recent(10, 'vwap_pullback')
        assert decisions and decisions[0]['outcome'] == 'SUBMITTED', (decisions, r.vwap_strategy.last_block_by_symbol)
        bracket = next(b for b in r.bracket_manager.brackets.values() if b.strategy_id == 'vwap_pullback')
        assert bracket.runner_policy == 'TRAIL_ONLY' and bracket.target_2_order_id is None
        assert 'AAPL' in r.account.positions
        rows = [row for row in r.research_recorder.recent['signals'] if row['strategy_id'] == 'vwap_pullback']
        features = rows[-1]['signal']['features']
        assert features['addon_session_delta']['delta'] * (1 if side == 'LONG' else -1) > 0
        assert features['addon_hvn_support'] and features['addon_hvn_overhead'] is None
        assert features['addon_spread']['ratio'] <= 1.5
    finally:
        r.reset_runtime_state()
        r.set_simulation_mode(False)
